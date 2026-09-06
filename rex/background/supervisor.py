"""Lifecycle supervisor for persistent Rex Core and Voice Agent processes."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import os
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from rex.background.core_client import CoreClient
from rex.background.core_server import CoreEndpoint
from rex.background.lock import SingleInstanceLock
from rex.background.paths import BackgroundPaths
from rex.background.types import ComponentHealth, HealthState, RuntimeHealth

_VOICE_DETAIL_CODES = frozenset(
    {
        None,
        "core_unavailable",
        "microphone_unavailable",
        "speaker_unavailable",
        "wakeword_unavailable",
        "restart_backoff",
        "restart_limit_exceeded",
        "listening_paused",
    }
)

_VOICE_HEALTH_MAX_AGE_SECONDS = 5.0

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ComponentSpec:
    """Static launch and bounded-restart policy for one child process."""

    name: str
    argv: tuple[str, ...]
    required: bool
    max_restarts: int = 3
    restart_window_seconds: float = 60.0

    def __post_init__(self) -> None:
        if not self.name or not self.argv:
            raise ValueError("Background component name and argv are required")
        if self.max_restarts < 0 or self.restart_window_seconds <= 0:
            raise ValueError("Background restart policy is invalid")


@dataclass(slots=True)
class _ComponentRuntime:
    spec: ComponentSpec
    process: Any | None = None
    health: ComponentHealth | None = None
    restart_times: list[float] = field(default_factory=list)
    next_restart_at: float | None = None


ProcessFactory = Callable[[ComponentSpec], Any]
Clock = Callable[[], float]


class _ProcessContainment(Protocol):
    def add(self, process: Any) -> None: ...

    def close(self) -> None: ...


class _NoopContainment:
    def add(self, process: Any) -> None:
        del process

    def close(self) -> None:
        return


ContainmentFactory = Callable[[], _ProcessContainment]
CoreShutdownRequester = Callable[[Path], bool]


class RuntimeSupervisor:
    """Own Core and Voice Agent lifecycles independently of Electron."""

    def __init__(
        self,
        paths: BackgroundPaths,
        core_spec: ComponentSpec,
        voice_spec: ComponentSpec,
        *,
        poll_interval: float = 0.25,
        core_start_timeout_seconds: float = 30.0,
        restart_backoff_seconds: float = 1.0,
        process_factory: ProcessFactory | None = None,
        containment_factory: ContainmentFactory | None = None,
        core_shutdown_requester: CoreShutdownRequester | None = None,
        clock: Clock = time.time,
    ) -> None:
        if poll_interval <= 0:
            raise ValueError("Supervisor poll interval must be positive")
        if core_start_timeout_seconds <= 0:
            raise ValueError("Core startup timeout must be positive")
        if restart_backoff_seconds <= 0:
            raise ValueError("Supervisor restart backoff must be positive")
        self.paths = paths
        self.poll_interval = float(poll_interval)
        self.core_start_timeout_seconds = float(core_start_timeout_seconds)
        self.restart_backoff_seconds = float(restart_backoff_seconds)
        self._clock = clock
        self._process_factory = process_factory or _spawn_process
        if containment_factory is not None:
            self._containment_factory = containment_factory
        elif process_factory is None:
            self._containment_factory = _default_containment
        else:
            self._containment_factory = _NoopContainment
        if core_shutdown_requester is not None:
            self._core_shutdown_requester = core_shutdown_requester
        elif process_factory is None:
            self._core_shutdown_requester = _request_authenticated_core_shutdown
        else:
            self._core_shutdown_requester = lambda _path: False
        self._core = _ComponentRuntime(core_spec)
        self._voice = _ComponentRuntime(voice_spec)
        self._lock: SingleInstanceLock | None = None
        self._containment: _ProcessContainment | None = None
        self._running = False
        self._core_started_at: float | None = None

    def start(self) -> None:
        """Acquire ownership and launch Core; Voice waits for Core readiness."""

        if self._running:
            return
        lock = SingleInstanceLock(self.paths.supervisor_lock)
        lock.acquire()
        containment: _ProcessContainment | None = None
        try:
            containment = self._containment_factory()
            self._containment = containment
            self.paths.state_dir.mkdir(parents=True, exist_ok=True)
            self.paths.stop_file.unlink(missing_ok=True)
            self.paths.voice_agent_health_file.unlink(missing_ok=True)
            self._remove_stale_endpoint()
            self._lock = lock
            self._running = True
            self._launch(self._core, HealthState.STARTING)
            self._set_voice_health(HealthState.STARTING, "core_starting", pid=None)
            self._write_health()
        except BaseException as exc:
            core_pid = getattr(self._core.process, "pid", None)
            cleanup_errors: list[tuple[str, BaseException]] = []

            def attempt(label: str, action: Callable[[], None]) -> None:
                try:
                    action()
                except BaseException as cleanup_exc:
                    cleanup_errors.append((label, cleanup_exc))

            attempt("stop voice", lambda: self._stop_component(self._voice))
            attempt("stop core", lambda: self._stop_component(self._core))
            if isinstance(core_pid, int):
                attempt("remove core endpoint", lambda: self._remove_endpoint_for_pid(core_pid))
            attempt(
                "remove voice health",
                lambda: self.paths.voice_agent_health_file.unlink(missing_ok=True),
            )
            if containment is not None:
                attempt("close containment", containment.close)
            self._containment = None
            attempt("release supervisor lock", lock.close)
            self._lock = None
            self._running = False
            self._core_started_at = None
            for label, cleanup_exc in cleanup_errors:
                exc.add_note(f"Startup cleanup failed while attempting to {label}: {cleanup_exc!r}")
            raise

    def tick(self) -> None:
        """Advance one deterministic lifecycle iteration."""

        if not self._running:
            return
        if self.paths.stop_file.exists():
            self.stop()
            return

        self._handle_core_exit()
        if not self._running:
            return
        core_ready = self._core_endpoint_matches_live_child()
        if core_ready:
            self._set_core_health(HealthState.READY, None)
            self._handle_voice(core_ready=True)
        else:
            self._mark_core_waiting_for_endpoint()
            self._handle_core_startup_timeout()
            self._handle_voice(core_ready=False)
        self._write_health_best_effort()

    def run(self) -> None:
        """Run until a stop request or signal-driven caller stops the supervisor."""

        self.start()
        try:
            while self._running:
                self.tick()
                if self._running:
                    time.sleep(self.poll_interval)
        finally:
            if self._running:
                self.stop()

    def stop(self) -> None:
        """Stop Voice Agent first, then Core, and release supervisor ownership.

        Every teardown step is attempted even if an earlier one raises: Voice
        stop, Core stop, stop-/health-file removal, owned-endpoint removal,
        process-containment close, aggregate-health write, and supervisor-lock
        release all run in order. Ownership resources (the Windows job handle and
        the single-instance lock) are always released so a failed stop cannot
        block a restart. A component whose ``terminate``/``wait`` raised is left
        ``FAILED`` -- not silently ``STOPPED`` -- because the supervisor cannot
        prove that child is gone. The first meaningful failure is re-raised once
        every step has been attempted, with any later cleanup failures attached
        as notes, so an incomplete orderly stop stays observably failed.
        """

        if not self._running and self._lock is None:
            return
        self._running = False

        first_error: list[BaseException] = []
        cleanup_notes: list[tuple[str, BaseException]] = []

        def attempt(label: str, action: Callable[[], None]) -> bool:
            try:
                action()
                return True
            except BaseException as exc:  # orderly stop must run every teardown step
                if first_error:
                    cleanup_notes.append((label, exc))
                else:
                    first_error.append(exc)
                return False

        attempt("request child stop", lambda: self.paths.stop_file.touch(exist_ok=True))
        voice_stopped = attempt("stop voice agent", self._stop_voice_orderly)
        self._set_voice_health(
            HealthState.STOPPED if voice_stopped else HealthState.FAILED,
            None if voice_stopped else "stop_failed",
            pid=None,
        )
        core_pid = getattr(self._core.process, "pid", None)
        core_stopped = attempt("stop core", self._stop_core_orderly)
        self._set_core_health(
            HealthState.STOPPED if core_stopped else HealthState.FAILED,
            None if core_stopped else "stop_failed",
            pid=None,
        )
        attempt("remove stop file", lambda: self.paths.stop_file.unlink(missing_ok=True))
        attempt(
            "remove voice health file",
            lambda: self.paths.voice_agent_health_file.unlink(missing_ok=True),
        )
        if isinstance(core_pid, int):
            attempt("remove core endpoint", lambda: self._remove_endpoint_for_pid(core_pid))
        attempt("write aggregate health", self._write_health)

        containment, self._containment = self._containment, None
        if containment is not None:
            attempt("close process containment", containment.close)
        lock, self._lock = self._lock, None
        if lock is not None:
            attempt("release supervisor lock", lock.close)
        self._core_started_at = None

        if first_error:
            error = first_error[0]
            for label, cleanup_exc in cleanup_notes:
                error.add_note(f"Stop cleanup failed while attempting to {label}: {cleanup_exc!r}")
            raise error

    def health(self) -> RuntimeHealth:
        """Return the current content-free aggregate health snapshot."""

        observed_at = self._clock()
        core = self._core.health or self._component_health(
            self._core, HealthState.STOPPED, None, observed_at=observed_at
        )
        voice = self._voice.health or self._component_health(
            self._voice, HealthState.STOPPED, None, observed_at=observed_at
        )
        return RuntimeHealth(core, voice, os.getpid(), observed_at)

    def _handle_core_exit(self) -> None:
        process = self._core.process
        if process is None:
            self._maybe_restart_core()
            return
        if process.poll() is None:
            return

        dead_pid = int(process.pid)
        self._core.process = None
        self._remove_endpoint_for_pid(dead_pid)
        if self._voice.process is not None:
            self._try_stop_voice_after_core_exit()
        self._discard_voice_health_file()
        if self._voice.health is None or self._voice.health.state is not HealthState.FAILED:
            self._set_voice_health(HealthState.DEGRADED, "core_unavailable", pid=None)

        if self._schedule_restart(self._core):
            self._set_core_health(HealthState.STARTING, "restart_backoff", pid=None)
        else:
            self._set_core_health(HealthState.FAILED, "restart_limit_exceeded", pid=None)

    def _try_stop_voice_after_core_exit(self) -> bool:
        """Best-effort stop of the obsolete Voice child during Core recovery."""

        try:
            self._stop_component(self._voice)
        except Exception:
            # Core recovery must continue while ownership of a child that could
            # not be stopped is retained, preventing duplicate Voice launch.
            return False
        return True

    def _mark_core_waiting_for_endpoint(self) -> None:
        process = self._core.process
        health = self._core.health
        if (
            process is None
            or process.poll() is not None
            or health is None
            or health.state is not HealthState.READY
        ):
            return
        self._core_started_at = self._clock()
        self._set_core_health(HealthState.STARTING, "core_starting")

    def _handle_core_startup_timeout(self) -> None:
        process = self._core.process
        health = self._core.health
        started_at = self._core_started_at
        if (
            process is None
            or process.poll() is not None
            or health is None
            or health.state is not HealthState.STARTING
            or started_at is None
            or self._clock() - started_at < self.core_start_timeout_seconds
        ):
            return

        dead_pid = int(process.pid)
        try:
            self._stop_component(self._core)
        except Exception:
            # Never launch a replacement while the timed-out Core may still be
            # alive. Keep ownership, fail closed, and leave the supervisor up.
            self._set_core_health(HealthState.FAILED, "stop_failed")
            if self._voice.health is None or self._voice.health.state is not HealthState.FAILED:
                self._set_voice_health(HealthState.DEGRADED, "core_unavailable", pid=None)
            return
        self._remove_endpoint_for_pid(dead_pid)
        if self._voice.health is None or self._voice.health.state is not HealthState.FAILED:
            self._set_voice_health(HealthState.DEGRADED, "core_unavailable", pid=None)
        if self._schedule_restart(self._core):
            self._set_core_health(HealthState.STARTING, "restart_backoff", pid=None)
        else:
            self._set_core_health(HealthState.FAILED, "restart_limit_exceeded", pid=None)

    def _maybe_restart_core(self) -> None:
        if self._core.process is not None or self._core.next_restart_at is None:
            return
        if self._clock() < self._core.next_restart_at:
            return
        if self._core.health is not None and self._core.health.state is HealthState.FAILED:
            return
        self._relaunch_within_policy(self._core)

    def _discard_voice_health_file(self) -> None:
        """Best-effort removal of the Voice Agent health file during lifecycle cleanup.

        A transient filesystem error here -- for example a Windows sharing
        violation while an exiting Voice child still holds the handle -- must
        never escape ``tick()``. If it did, ``run()`` would fall through to
        ``stop()`` and take down a healthy Core over a Voice-only cleanup
        failure. ``start()`` startup validation deliberately does not use this
        path so an initial-launch failure still surfaces with cleanup.
        """

        with contextlib.suppress(OSError):
            self.paths.voice_agent_health_file.unlink(missing_ok=True)

    def _relaunch_within_policy(self, runtime: _ComponentRuntime) -> None:
        """Relaunch a child inside the lifecycle loop, absorbing launch failures.

        A spawn or containment-assignment failure must never escape ``tick()``:
        if it did, ``run()`` would fall through to ``stop()`` and take down a
        healthy Core when only the Voice Agent failed. Instead a failed launch
        consumes restart budget/backoff exactly like any other restart, and
        exhausting that budget marks the component ``failed`` without pretending
        healthy. The initial launch in ``start()`` deliberately does not use this
        path: startup has not yet established a running supervisor, so its
        failure stays an exception with cleanup.
        """

        try:
            self._launch(runtime, HealthState.STARTING)
            return
        except Exception:
            self._cleanup_failed_launch(runtime)
        if runtime is self._voice:
            self._discard_voice_health_file()
        if self._schedule_restart(runtime):
            state, detail_code = HealthState.STARTING, "restart_backoff"
        else:
            state, detail_code = HealthState.FAILED, "restart_limit_exceeded"
        if runtime is self._core:
            self._set_core_health(state, detail_code, pid=None)
        else:
            self._set_voice_health(state, detail_code, pid=None)

    def _cleanup_failed_launch(self, runtime: _ComponentRuntime) -> None:
        try:
            self._stop_component(runtime)
        except Exception:
            runtime.process = None

    def _handle_voice_without_core(self, process: Any | None) -> None:
        if process is not None:
            try:
                self._stop_component(self._voice)
            except Exception:
                self._discard_voice_health_file()
                self._set_voice_health(HealthState.DEGRADED, "core_unavailable")
                return
            self._discard_voice_health_file()
        if self._voice.health is None or self._voice.health.state is not HealthState.FAILED:
            self._set_voice_health(HealthState.DEGRADED, "core_unavailable", pid=None)

    def _handle_voice(self, *, core_ready: bool) -> None:
        process = self._voice.process
        if not core_ready:
            self._handle_voice_without_core(process)
            return
        if process is None:
            self._handle_missing_voice_process()
            return
        if self._voice_requires_core_rebind():
            self._replace_voice_after_core_restart()
            return
        child_health = self._read_voice_child_health(int(process.pid))
        if process.poll() is None:
            self._refresh_running_voice_health(child_health)
            return
        self._handle_exited_voice(child_health)

    def _handle_missing_voice_process(self) -> None:
        if self._voice.health is not None and self._voice.health.state is HealthState.FAILED:
            return
        restart_at = self._voice.next_restart_at
        if restart_at is not None and self._clock() < restart_at:
            return
        self._relaunch_within_policy(self._voice)

    def _voice_requires_core_rebind(self) -> bool:
        health = self._voice.health
        return health is not None and health.detail_code == "core_unavailable"

    def _replace_voice_after_core_restart(self) -> None:
        # A Voice child constructed against the previous Core endpoint must
        # be replaced before it can become READY against the restarted Core.
        try:
            self._stop_component(self._voice)
        except Exception:
            return
        self._discard_voice_health_file()
        self._relaunch_within_policy(self._voice)

    def _refresh_running_voice_health(self, child_health: ComponentHealth | None) -> None:
        if child_health is not None:
            self._voice.health = child_health
            return
        if self._voice.health is None or self._voice.health.state is not HealthState.STARTING:
            self._set_voice_health(HealthState.STARTING, None)

    def _handle_exited_voice(self, child_health: ComponentHealth | None) -> None:
        self._voice.process = None
        if child_health is not None and child_health.state in {
            HealthState.DEGRADED,
            HealthState.UNAVAILABLE,
            HealthState.FAILED,
        }:
            self._voice.health = ComponentHealth(
                component="voice_agent",
                state=child_health.state,
                detail_code=child_health.detail_code,
                observed_at=self._clock(),
                pid=None,
            )
        else:
            self._set_voice_health(HealthState.STARTING, "restart_backoff", pid=None)
        self._discard_voice_health_file()
        if self._schedule_restart(self._voice):
            return
        self._set_voice_health(HealthState.FAILED, "restart_limit_exceeded", pid=None)

    def _schedule_restart(self, runtime: _ComponentRuntime) -> bool:
        now = self._clock()
        cutoff = now - runtime.spec.restart_window_seconds
        runtime.restart_times[:] = [stamp for stamp in runtime.restart_times if stamp >= cutoff]
        if len(runtime.restart_times) >= runtime.spec.max_restarts:
            runtime.next_restart_at = None
            return False
        runtime.restart_times.append(now)
        runtime.next_restart_at = now + self.restart_backoff_seconds
        return True

    def _read_voice_child_health(self, expected_pid: int) -> ComponentHealth | None:
        path = self.paths.voice_agent_health_file
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        allowed = {"component", "state", "detail_code", "observed_at", "pid"}
        if set(payload) != allowed or payload.get("component") != "voice_agent":
            return None
        pid = payload.get("pid")
        observed_at = payload.get("observed_at")
        detail_code = payload.get("detail_code")
        state_value = payload.get("state")
        if isinstance(pid, bool) or not isinstance(pid, int) or pid != expected_pid:
            return None
        if isinstance(observed_at, bool) or not isinstance(observed_at, (int, float)):
            return None
        observed_at_float = float(observed_at)
        if not math.isfinite(observed_at_float):
            return None
        age = self._clock() - observed_at_float
        if age < -_VOICE_HEALTH_MAX_AGE_SECONDS or age > _VOICE_HEALTH_MAX_AGE_SECONDS:
            return None
        if detail_code not in _VOICE_DETAIL_CODES:
            return None
        if not isinstance(state_value, str):
            return None
        try:
            state = HealthState(state_value)
        except ValueError:
            return None
        return ComponentHealth(
            component="voice_agent",
            state=state,
            detail_code=detail_code,
            observed_at=observed_at_float,
            pid=pid,
        )

    def _launch(self, runtime: _ComponentRuntime, state: HealthState) -> None:
        containment = self._containment
        if containment is None:
            raise RuntimeError("Supervisor process containment is not initialized")
        if runtime is self._voice:
            self.paths.voice_agent_health_file.unlink(missing_ok=True)

        process: Any
        if self._process_factory is _spawn_process and isinstance(
            containment, _WindowsJobContainment
        ):
            process = containment.spawn(runtime.spec)
        else:
            process = self._process_factory(runtime.spec)
            runtime.process = process
            containment.add(process)
        runtime.process = process
        runtime.next_restart_at = None
        runtime.health = self._component_health(runtime, state, None)
        if runtime is self._core:
            self._core_started_at = self._clock()

    def _stop_voice_orderly(self) -> None:
        process = self._voice.process
        if process is None:
            return
        if process.poll() is not None:
            self._voice.process = None
            return
        try:
            process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            self._stop_component(self._voice)
            return
        self._voice.process = None

    def _stop_core_orderly(self) -> None:
        process = self._core.process
        if process is None:
            return
        if process.poll() is not None:
            self._core.process = None
            return
        shutdown_requested = False
        try:
            shutdown_requested = self._core_shutdown_requester(self.paths.core_endpoint_file)
        except (OSError, ValueError, json.JSONDecodeError, RuntimeError):
            shutdown_requested = False
        if shutdown_requested:
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=2.0)
                self._core.process = None
                return
        self._stop_component(self._core)

    def _stop_component(self, runtime: _ComponentRuntime) -> None:
        process = runtime.process
        if process is None:
            return
        if process.poll() is not None:
            runtime.process = None
            return
        process.terminate()
        try:
            process.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5.0)
        runtime.process = None

    def _core_endpoint_matches_live_child(self) -> bool:
        process = self._core.process
        if process is None or process.poll() is not None:
            return False
        try:
            payload = json.loads(self.paths.core_endpoint_file.read_text(encoding="utf-8"))
            endpoint = CoreEndpoint.from_dict(payload)
        except (FileNotFoundError, OSError, json.JSONDecodeError, ValueError):
            return False
        return endpoint.pid == int(process.pid)

    def _set_core_health(
        self, state: HealthState, detail_code: str | None, *, pid: int | None = None
    ) -> None:
        self._core.health = self._component_health(self._core, state, detail_code, pid=pid)

    def _set_voice_health(
        self, state: HealthState, detail_code: str | None, *, pid: int | None = None
    ) -> None:
        self._voice.health = self._component_health(self._voice, state, detail_code, pid=pid)

    def _component_health(
        self,
        runtime: _ComponentRuntime,
        state: HealthState,
        detail_code: str | None,
        *,
        pid: int | None = None,
        observed_at: float | None = None,
    ) -> ComponentHealth:
        process = runtime.process
        resolved_pid = pid if pid is not None else getattr(process, "pid", None)
        return ComponentHealth(
            component=runtime.spec.name,
            state=state,
            detail_code=detail_code,
            observed_at=self._clock() if observed_at is None else observed_at,
            pid=resolved_pid,
        )

    def _write_health(self) -> None:
        _atomic_write_json(self.paths.health_file, self.health().to_dict())

    def _write_health_best_effort(self) -> None:
        with contextlib.suppress(OSError):
            self._write_health()

    def _remove_stale_endpoint(self) -> None:
        path = self.paths.core_endpoint_file
        if not path.exists():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            endpoint = CoreEndpoint.from_dict(payload)
        except (OSError, json.JSONDecodeError, ValueError):
            path.unlink(missing_ok=True)
            return
        if not _pid_is_alive(endpoint.pid):
            path.unlink(missing_ok=True)

    def _remove_owned_endpoint(self) -> None:
        process = self._core.process
        if process is not None:
            self._remove_endpoint_for_pid(process.pid)

    def _remove_endpoint_for_pid(self, pid: int) -> None:
        path = self.paths.core_endpoint_file
        if not path.exists():
            return
        endpoint: CoreEndpoint | None = None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            endpoint = CoreEndpoint.from_dict(payload)
        except (OSError, json.JSONDecodeError, ValueError):
            endpoint = None
        if endpoint is None:
            return
        if endpoint.pid == pid:
            with contextlib.suppress(OSError):
                path.unlink(missing_ok=True)


class _WindowsJobChild:
    """Minimal Popen-compatible wrapper around a native Windows process handle."""

    def __init__(
        self,
        kernel32: Any,
        process_handle: Any,
        pid: int,
        argv: tuple[str, ...],
        get_last_error: Callable[[], int],
        win_error: Callable[[int], OSError],
    ) -> None:
        self._kernel32 = kernel32
        self._process_handle = process_handle
        self.pid = pid
        self.args = argv
        self.returncode: int | None = None
        self._get_last_error = get_last_error
        self._win_error = win_error

    def poll(self) -> int | None:
        if self.returncode is not None:
            return self.returncode
        handle = self._process_handle
        if handle is None:
            return self.returncode
        import ctypes

        code = ctypes.c_ulong()
        if not self._kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            raise self._win_error(int(self._get_last_error()))
        if int(code.value) == 259:  # STILL_ACTIVE
            return None
        self.returncode = int(code.value)
        self._close_handle()
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is not None:
            return self.returncode
        handle = self._process_handle
        if handle is None:
            if self.returncode is None:
                raise RuntimeError("Windows child process handle is closed")
            return self.returncode
        milliseconds = 0xFFFFFFFF if timeout is None else max(0, int(timeout * 1000))
        result = int(self._kernel32.WaitForSingleObject(handle, milliseconds))
        if result == 0x00000102:  # WAIT_TIMEOUT
            if timeout is None:
                raise RuntimeError("Infinite Windows wait unexpectedly timed out")
            raise subprocess.TimeoutExpired(self.args, timeout)
        if result != 0:  # WAIT_OBJECT_0
            raise self._win_error(int(self._get_last_error()))
        returncode = self.poll()
        if returncode is None:
            raise RuntimeError("Windows child remained active after wait completed")
        return returncode

    def terminate(self) -> None:
        if self.poll() is not None:
            return
        handle = self._process_handle
        if handle is None:
            return
        if not self._kernel32.TerminateProcess(handle, 1):
            raise self._win_error(int(self._get_last_error()))

    def kill(self) -> None:
        self.terminate()

    def _close_handle(self) -> None:
        handle, self._process_handle = self._process_handle, None
        if handle is not None:
            self._kernel32.CloseHandle(handle)

    def __del__(self) -> None:
        with contextlib.suppress(Exception):
            self._close_handle()


class _WindowsJobContainment:
    """Keep background children in a kill-on-supervisor-exit Windows Job Object."""

    def __init__(self) -> None:
        if os.name != "nt":
            raise OSError("Windows process containment is only available on Windows")

        import ctypes
        from ctypes import wintypes

        class _BasicLimitInformation(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class _IoCounters(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class _ExtendedLimitInformation(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", _BasicLimitInformation),
                ("IoInfo", _IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        class _StartupInfoW(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("lpReserved", wintypes.LPWSTR),
                ("lpDesktop", wintypes.LPWSTR),
                ("lpTitle", wintypes.LPWSTR),
                ("dwX", wintypes.DWORD),
                ("dwY", wintypes.DWORD),
                ("dwXSize", wintypes.DWORD),
                ("dwYSize", wintypes.DWORD),
                ("dwXCountChars", wintypes.DWORD),
                ("dwYCountChars", wintypes.DWORD),
                ("dwFillAttribute", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD),
                ("wShowWindow", wintypes.WORD),
                ("cbReserved2", wintypes.WORD),
                ("lpReserved2", ctypes.POINTER(ctypes.c_ubyte)),
                ("hStdInput", wintypes.HANDLE),
                ("hStdOutput", wintypes.HANDLE),
                ("hStdError", wintypes.HANDLE),
            ]

        class _StartupInfoExW(ctypes.Structure):
            _fields_ = [
                ("StartupInfo", _StartupInfoW),
                ("lpAttributeList", ctypes.c_void_p),
            ]

        class _ProcessInformation(ctypes.Structure):
            _fields_ = [
                ("hProcess", wintypes.HANDLE),
                ("hThread", wintypes.HANDLE),
                ("dwProcessId", wintypes.DWORD),
                ("dwThreadId", wintypes.DWORD),
            ]

        win_dll = ctypes.__dict__["WinDLL"]
        get_last_error = ctypes.__dict__["get_last_error"]
        win_error = ctypes.__dict__["WinError"]
        kernel32 = win_dll("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.InitializeProcThreadAttributeList.argtypes = [
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        kernel32.InitializeProcThreadAttributeList.restype = wintypes.BOOL
        kernel32.UpdateProcThreadAttribute.argtypes = [
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.c_size_t,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        kernel32.UpdateProcThreadAttribute.restype = wintypes.BOOL
        kernel32.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
        kernel32.DeleteProcThreadAttributeList.restype = None
        kernel32.CreateProcessW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPWSTR,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.BOOL,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.LPCWSTR,
            ctypes.POINTER(_StartupInfoW),
            ctypes.POINTER(_ProcessInformation),
        ]
        kernel32.CreateProcessW.restype = wintypes.BOOL
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.GetExitCodeProcess.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.TerminateProcess.restype = wintypes.BOOL

        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            raise win_error(int(get_last_error()))

        info = _ExtendedLimitInformation()
        info.BasicLimitInformation.LimitFlags = 0x00002000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            error = int(get_last_error())
            kernel32.CloseHandle(job)
            raise win_error(error)

        self._kernel32 = kernel32
        self._ctypes = ctypes
        self._wintypes = wintypes
        self._startup_info_ex_type = _StartupInfoExW
        self._process_information_type = _ProcessInformation
        self._get_last_error = get_last_error
        self._win_error = win_error
        self._job = job

    def spawn(self, spec: ComponentSpec) -> _WindowsJobChild:
        """Create a child atomically assigned to this Job Object."""

        job = self._job
        if job is None:
            raise RuntimeError("Windows process containment is closed")
        ctypes = self._ctypes
        wintypes = self._wintypes
        kernel32 = self._kernel32
        attribute_size = ctypes.c_size_t()
        kernel32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(attribute_size))
        if attribute_size.value <= 0:
            raise self._win_error(int(self._get_last_error()))
        attribute_buffer = ctypes.create_string_buffer(attribute_size.value)
        if not kernel32.InitializeProcThreadAttributeList(
            attribute_buffer, 1, 0, ctypes.byref(attribute_size)
        ):
            raise self._win_error(int(self._get_last_error()))

        process_info = self._process_information_type()
        thread_handle: Any | None = None
        process_handle: Any | None = None
        try:
            job_list = (wintypes.HANDLE * 1)(job)
            if not kernel32.UpdateProcThreadAttribute(
                attribute_buffer,
                0,
                0x0002000D,  # PROC_THREAD_ATTRIBUTE_JOB_LIST
                ctypes.cast(job_list, ctypes.c_void_p),
                ctypes.sizeof(job_list),
                None,
                None,
            ):
                raise self._win_error(int(self._get_last_error()))
            startup = self._startup_info_ex_type()
            startup.StartupInfo.cb = ctypes.sizeof(startup)
            startup.lpAttributeList = ctypes.cast(attribute_buffer, ctypes.c_void_p)
            command_line = ctypes.create_unicode_buffer(subprocess.list2cmdline(spec.argv))
            if not kernel32.CreateProcessW(
                None,
                command_line,
                None,
                None,
                False,
                0x00080000,  # EXTENDED_STARTUPINFO_PRESENT
                None,
                None,
                ctypes.byref(startup.StartupInfo),
                ctypes.byref(process_info),
            ):
                raise self._win_error(int(self._get_last_error()))
            process_handle = process_info.hProcess
            thread_handle = process_info.hThread
            child = _WindowsJobChild(
                kernel32,
                process_handle,
                int(process_info.dwProcessId),
                spec.argv,
                self._get_last_error,
                self._win_error,
            )
            process_handle = None
            return child
        finally:
            kernel32.DeleteProcThreadAttributeList(attribute_buffer)
            if thread_handle is not None:
                kernel32.CloseHandle(thread_handle)
            if process_handle is not None:
                kernel32.CloseHandle(process_handle)

    def add(self, process: Any) -> None:
        job = self._job
        if job is None:
            raise RuntimeError("Windows process containment is closed")
        pid = int(process.pid)
        process_handle = self._kernel32.OpenProcess(0x0101, False, pid)
        if not process_handle:
            raise self._win_error(int(self._get_last_error()))
        try:
            if not self._kernel32.AssignProcessToJobObject(job, process_handle):
                raise self._win_error(int(self._get_last_error()))
        finally:
            self._kernel32.CloseHandle(process_handle)

    def close(self) -> None:
        job, self._job = self._job, None
        if job is not None:
            self._kernel32.CloseHandle(job)


def _default_containment() -> _ProcessContainment:
    if os.name == "nt":
        return _WindowsJobContainment()
    return _NoopContainment()


def _spawn_process(spec: ComponentSpec) -> subprocess.Popen[bytes]:
    return subprocess.Popen(spec.argv, close_fds=True)


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0 or pid > 0xFFFFFFFF:
        return False
    if os.name == "nt":
        return _windows_pid_is_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, OverflowError, ValueError):
        return False
    return True


def _windows_pid_is_alive(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    still_active = 259
    error_access_denied = 5
    win_dll = ctypes.__dict__["WinDLL"]
    get_last_error = ctypes.__dict__["get_last_error"]
    kernel32 = win_dll("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return int(get_last_error()) == error_access_denied
    try:
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return int(exit_code.value) == still_active
    finally:
        kernel32.CloseHandle(handle)


def _request_authenticated_core_shutdown(endpoint_file: Path) -> bool:
    try:
        client = CoreClient.from_endpoint_file(endpoint_file, timeout=2.0)
        return asyncio.run(client.shutdown()).get("ok") is True
    except (OSError, ValueError, json.JSONDecodeError, RuntimeError):
        return False


def _atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, separators=(",", ":"), ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
