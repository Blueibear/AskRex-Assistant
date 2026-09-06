"""Behavior tests for the persistent Rex background supervisor."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from rex.background.lock import AlreadyRunningError, SingleInstanceLock
from rex.background.paths import BackgroundPaths
from rex.background.supervisor import (
    ComponentSpec,
    RuntimeSupervisor,
    _pid_is_alive,
    _request_authenticated_core_shutdown,
)
from rex.background.types import HealthState


@dataclass
class _FakeChild:
    name: str
    pid: int
    events: list[str]
    returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.events.append(f"terminate:{self.name}")
        self.returncode = 0

    def wait(self, timeout: float | None = None) -> int:
        self.events.append(f"wait:{self.name}")
        if self.returncode is None and timeout is not None:
            raise subprocess.TimeoutExpired(self.name, timeout)
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def kill(self) -> None:
        self.events.append(f"kill:{self.name}")
        self.returncode = -9


class _FakeProcessFactory:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.children: dict[str, list[_FakeChild]] = {"core": [], "voice_agent": []}
        self._next_pid = 1000

    def __call__(self, spec: ComponentSpec) -> _FakeChild:
        self.events.append(f"start:{spec.name}")
        child = _FakeChild(spec.name, self._next_pid, self.events)
        self._next_pid += 1
        self.children[spec.name].append(child)
        return child


def _spec(name: str, *, max_restarts: int = 3) -> ComponentSpec:
    return ComponentSpec(
        name=name,
        argv=("python", "-m", f"fake.{name}"),
        required=True,
        max_restarts=max_restarts,
        restart_window_seconds=60.0,
    )


def _supervisor(
    tmp_path: Path,
    factory: _FakeProcessFactory,
    *,
    core_restarts: int = 3,
    voice_restarts: int = 3,
    now: list[float] | None = None,
) -> RuntimeSupervisor:
    return RuntimeSupervisor(
        BackgroundPaths.from_runtime_root(tmp_path),
        _spec("core", max_restarts=core_restarts),
        _spec("voice_agent", max_restarts=voice_restarts),
        poll_interval=0.01,
        process_factory=factory,
        clock=(lambda: now[0]) if now is not None else (lambda: 100.0),
    )


def _write_core_endpoint(paths: BackgroundPaths, pid: int) -> None:
    paths.state_dir.mkdir(parents=True, exist_ok=True)
    paths.core_endpoint_file.write_text(
        json.dumps(
            {
                "host": "127.0.0.1",
                "port": 49152,
                "token": "t" * 32,
                "pid": pid,
            }
        )
        + "\n",
        encoding="utf-8",
    )


def test_duplicate_supervisor_start_is_rejected(tmp_path: Path) -> None:
    paths = BackgroundPaths.from_runtime_root(tmp_path)
    factory = _FakeProcessFactory()
    supervisor = RuntimeSupervisor(
        paths,
        _spec("core"),
        _spec("voice_agent"),
        process_factory=factory,
        clock=lambda: 100.0,
    )

    with SingleInstanceLock(paths.supervisor_lock):
        with pytest.raises(AlreadyRunningError):
            supervisor.start()


def test_core_starts_before_voice_and_waits_for_endpoint(tmp_path: Path) -> None:
    factory = _FakeProcessFactory()
    supervisor = _supervisor(tmp_path, factory)

    supervisor.start()
    assert factory.events == ["start:core"]

    _write_core_endpoint(supervisor.paths, factory.children["core"][0].pid)
    supervisor.tick()

    assert factory.events[:2] == ["start:core", "start:voice_agent"]
    assert supervisor.health().core.state is HealthState.READY
    assert supervisor.health().voice_agent.state is HealthState.STARTING

    voice = factory.children["voice_agent"][0]
    _write_voice_health(supervisor.paths, voice.pid, HealthState.READY)
    supervisor.tick()
    assert supervisor.health().voice_agent.state is HealthState.READY
    supervisor.stop()


def test_voice_crash_restarts_without_restarting_core(tmp_path: Path) -> None:
    factory = _FakeProcessFactory()
    now = [100.0]
    supervisor = _supervisor(tmp_path, factory, now=now)
    supervisor.start()
    _write_core_endpoint(supervisor.paths, factory.children["core"][0].pid)
    supervisor.tick()

    factory.children["voice_agent"][0].returncode = 7
    supervisor.tick()
    assert len(factory.children["voice_agent"]) == 1

    now[0] = 101.0
    supervisor.tick()
    assert len(factory.children["core"]) == 1
    assert len(factory.children["voice_agent"]) == 2
    assert supervisor.health().core.state is HealthState.READY
    supervisor.stop()


def test_repeated_voice_crash_becomes_failed_after_bounded_restarts(tmp_path: Path) -> None:
    factory = _FakeProcessFactory()
    now = [100.0]
    supervisor = _supervisor(tmp_path, factory, voice_restarts=2, now=now)
    supervisor.start()
    _write_core_endpoint(supervisor.paths, factory.children["core"][0].pid)
    supervisor.tick()

    for index in range(3):
        factory.children["voice_agent"][index].returncode = 1
        supervisor.tick()
        if index < 2:
            now[0] += 1.0
            supervisor.tick()

    assert len(factory.children["core"]) == 1
    assert len(factory.children["voice_agent"]) == 3
    health = supervisor.health()
    assert health.core.state is HealthState.READY
    assert health.voice_agent.state is HealthState.FAILED
    assert health.voice_agent.detail_code == "restart_limit_exceeded"
    supervisor.stop()


def test_core_crash_degrades_voice_and_restarts_core_before_voice(tmp_path: Path) -> None:
    factory = _FakeProcessFactory()
    now = [100.0]
    supervisor = _supervisor(tmp_path, factory, now=now)
    supervisor.start()
    _write_core_endpoint(supervisor.paths, factory.children["core"][0].pid)
    supervisor.tick()

    first_voice = factory.children["voice_agent"][0]
    factory.children["core"][0].returncode = 9
    supervisor.tick()

    health = supervisor.health()
    assert len(factory.children["core"]) == 1
    assert len(factory.children["voice_agent"]) == 1
    assert first_voice.returncode == 0
    assert health.core.state is HealthState.STARTING
    assert health.core.detail_code == "restart_backoff"
    assert health.voice_agent.state is HealthState.DEGRADED
    assert health.voice_agent.detail_code == "core_unavailable"

    now[0] = 101.0
    supervisor.tick()
    assert len(factory.children["core"]) == 2
    _write_core_endpoint(supervisor.paths, factory.children["core"][1].pid)
    supervisor.tick()
    assert len(factory.children["voice_agent"]) == 2
    assert factory.events.index("start:core", 2) < factory.events.index("start:voice_agent", 3)
    supervisor.stop()


def test_stop_terminates_voice_before_core(tmp_path: Path) -> None:
    factory = _FakeProcessFactory()
    supervisor = _supervisor(tmp_path, factory)
    supervisor.start()
    _write_core_endpoint(supervisor.paths, factory.children["core"][0].pid)
    supervisor.tick()

    supervisor.stop()
    voice_index = factory.events.index("terminate:voice_agent")
    core_index = factory.events.index("terminate:core")
    assert voice_index < core_index
    assert supervisor.health().voice_agent.state is HealthState.STOPPED
    assert supervisor.health().core.state is HealthState.STOPPED


def test_aggregate_health_file_is_content_free(tmp_path: Path) -> None:
    factory = _FakeProcessFactory()
    supervisor = _supervisor(tmp_path, factory)
    supervisor.start()
    _write_core_endpoint(supervisor.paths, factory.children["core"][0].pid)
    supervisor.tick()

    payload = json.loads(supervisor.paths.health_file.read_text(encoding="utf-8"))
    assert set(payload) == {"core", "voice_agent", "supervisor_pid", "observed_at"}
    assert set(payload["core"]) == {
        "component",
        "state",
        "detail_code",
        "observed_at",
        "pid",
    }
    assert set(payload["voice_agent"]) == set(payload["core"])
    serialized = json.dumps(payload).lower()
    for forbidden in (
        "transcript",
        "prompt",
        "user_id",
        "credential",
        "tool_result",
        "microphone_audio",
    ):
        assert forbidden not in serialized
    supervisor.stop()


def test_core_startup_timeout_restarts_before_voice_starts(tmp_path: Path) -> None:
    factory = _FakeProcessFactory()
    now = [100.0]
    supervisor = RuntimeSupervisor(
        BackgroundPaths.from_runtime_root(tmp_path),
        _spec("core", max_restarts=1),
        _spec("voice_agent"),
        poll_interval=0.01,
        core_start_timeout_seconds=5.0,
        process_factory=factory,
        clock=lambda: now[0],
    )

    supervisor.start()
    supervisor.tick()
    assert len(factory.children["core"]) == 1
    assert factory.children["voice_agent"] == []

    now[0] = 106.0
    supervisor.tick()
    assert len(factory.children["core"]) == 1
    assert factory.children["voice_agent"] == []
    assert "terminate:core" in factory.events
    assert supervisor.health().core.detail_code == "restart_backoff"
    assert supervisor.health().voice_agent.detail_code == "core_unavailable"

    now[0] = 107.0
    supervisor.tick()
    assert len(factory.children["core"]) == 2
    assert supervisor.health().core.state is HealthState.STARTING
    supervisor.stop()


def test_failed_voice_stays_failed_across_core_restart(tmp_path: Path) -> None:
    factory = _FakeProcessFactory()
    now = [100.0]
    supervisor = _supervisor(tmp_path, factory, voice_restarts=0, now=now)
    supervisor.start()
    _write_core_endpoint(supervisor.paths, factory.children["core"][0].pid)
    supervisor.tick()

    factory.children["voice_agent"][0].returncode = 1
    supervisor.tick()
    assert supervisor.health().voice_agent.state is HealthState.FAILED

    factory.children["core"][0].returncode = 1
    supervisor.tick()
    now[0] = 101.0
    supervisor.tick()
    _write_core_endpoint(supervisor.paths, factory.children["core"][1].pid)
    supervisor.tick()

    assert len(factory.children["voice_agent"]) == 1
    assert supervisor.health().voice_agent.state is HealthState.FAILED
    supervisor.stop()


def test_stop_removes_owned_core_endpoint(tmp_path: Path) -> None:
    factory = _FakeProcessFactory()
    supervisor = _supervisor(tmp_path, factory)
    supervisor.start()
    _write_core_endpoint(supervisor.paths, factory.children["core"][0].pid)
    supervisor.tick()
    assert supervisor.paths.core_endpoint_file.exists()

    supervisor.stop()
    assert not supervisor.paths.core_endpoint_file.exists()


def test_pid_liveness_probe_does_not_terminate_live_process() -> None:
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert _pid_is_alive(child.pid) is True
        time.sleep(0.2)
        assert child.poll() is None
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5.0)


def test_default_core_startup_window_tolerates_slow_assistant_init(tmp_path: Path) -> None:
    factory = _FakeProcessFactory()
    now = [100.0]
    supervisor = RuntimeSupervisor(
        BackgroundPaths.from_runtime_root(tmp_path),
        _spec("core", max_restarts=1),
        _spec("voice_agent"),
        poll_interval=0.01,
        process_factory=factory,
        clock=lambda: now[0],
    )

    supervisor.start()
    now[0] = 115.0
    supervisor.tick()

    assert len(factory.children["core"]) == 1
    assert factory.children["voice_agent"] == []
    assert supervisor.health().core.state is HealthState.STARTING
    supervisor.stop()


def _write_voice_health(
    paths: BackgroundPaths,
    pid: int,
    state: HealthState,
    detail_code: str | None = None,
) -> None:
    paths.state_dir.mkdir(parents=True, exist_ok=True)
    paths.voice_agent_health_file.write_text(
        json.dumps(
            {
                "component": "voice_agent",
                "state": state.value,
                "detail_code": detail_code,
                "observed_at": 100.0,
                "pid": pid,
            }
        )
        + "\n",
        encoding="utf-8",
    )


def test_voice_waits_for_child_readiness_signal(tmp_path: Path) -> None:
    factory = _FakeProcessFactory()
    supervisor = _supervisor(tmp_path, factory)
    supervisor.start()
    _write_core_endpoint(supervisor.paths, factory.children["core"][0].pid)
    supervisor.tick()

    voice = factory.children["voice_agent"][0]
    assert supervisor.health().voice_agent.state is HealthState.STARTING
    supervisor.tick()
    assert supervisor.health().voice_agent.state is HealthState.STARTING

    _write_voice_health(supervisor.paths, voice.pid, HealthState.READY)
    supervisor.tick()
    assert supervisor.health().voice_agent.state is HealthState.READY
    supervisor.stop()


def test_voice_startup_failure_detail_is_preserved_during_backoff(tmp_path: Path) -> None:
    factory = _FakeProcessFactory()
    now = [100.0]
    supervisor = RuntimeSupervisor(
        BackgroundPaths.from_runtime_root(tmp_path),
        _spec("core"),
        _spec("voice_agent", max_restarts=1),
        poll_interval=0.01,
        process_factory=factory,
        clock=lambda: now[0],
    )
    supervisor.start()
    _write_core_endpoint(supervisor.paths, factory.children["core"][0].pid)
    supervisor.tick()
    voice = factory.children["voice_agent"][0]
    _write_voice_health(
        supervisor.paths,
        voice.pid,
        HealthState.UNAVAILABLE,
        "microphone_unavailable",
    )
    voice.returncode = 1

    supervisor.tick()
    health = supervisor.health().voice_agent
    assert health.state is HealthState.UNAVAILABLE
    assert health.detail_code == "microphone_unavailable"
    assert len(factory.children["voice_agent"]) == 1

    now[0] = 100.5
    supervisor.tick()
    assert len(factory.children["voice_agent"]) == 1
    supervisor.stop()


def test_restart_backoff_delays_relaunch_until_due(tmp_path: Path) -> None:
    factory = _FakeProcessFactory()
    now = [100.0]
    supervisor = RuntimeSupervisor(
        BackgroundPaths.from_runtime_root(tmp_path),
        _spec("core"),
        _spec("voice_agent", max_restarts=1),
        poll_interval=0.01,
        restart_backoff_seconds=1.0,
        process_factory=factory,
        clock=lambda: now[0],
    )
    supervisor.start()
    _write_core_endpoint(supervisor.paths, factory.children["core"][0].pid)
    supervisor.tick()
    voice = factory.children["voice_agent"][0]
    _write_voice_health(supervisor.paths, voice.pid, HealthState.READY)
    supervisor.tick()
    voice.returncode = 1

    supervisor.tick()
    assert len(factory.children["voice_agent"]) == 1
    now[0] = 100.9
    supervisor.tick()
    assert len(factory.children["voice_agent"]) == 1
    now[0] = 101.0
    supervisor.tick()
    assert len(factory.children["voice_agent"]) == 2
    supervisor.stop()


class _FakeContainment:
    def __init__(self) -> None:
        self.added: list[int] = []
        self.closed = False

    def add(self, process) -> None:
        self.added.append(process.pid)

    def close(self) -> None:
        self.closed = True


def test_supervisor_assigns_children_to_owned_containment(tmp_path: Path) -> None:
    factory = _FakeProcessFactory()
    containment = _FakeContainment()
    supervisor = RuntimeSupervisor(
        BackgroundPaths.from_runtime_root(tmp_path),
        _spec("core"),
        _spec("voice_agent"),
        process_factory=factory,
        containment_factory=lambda: containment,
        clock=lambda: 100.0,
    )
    supervisor.start()
    core = factory.children["core"][0]
    assert containment.added == [core.pid]

    _write_core_endpoint(supervisor.paths, core.pid)
    supervisor.tick()
    voice = factory.children["voice_agent"][0]
    assert containment.added == [core.pid, voice.pid]

    supervisor.stop()
    assert containment.closed is True


def test_start_failure_terminates_launched_core_and_releases_containment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    factory = _FakeProcessFactory()
    containment = _FakeContainment()
    supervisor = RuntimeSupervisor(
        BackgroundPaths.from_runtime_root(tmp_path),
        _spec("core"),
        _spec("voice_agent"),
        process_factory=factory,
        containment_factory=lambda: containment,
        clock=lambda: 100.0,
    )
    monkeypatch.setattr(
        supervisor,
        "_write_health",
        lambda: (_ for _ in ()).throw(OSError("disk full")),
    )

    with pytest.raises(OSError, match="disk full"):
        supervisor.start()

    core = factory.children["core"][0]
    assert core.returncode == 0
    assert containment.closed is True
    with SingleInstanceLock(supervisor.paths.supervisor_lock):
        pass


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object contract")
def test_windows_containment_kills_child_when_owner_closes() -> None:
    from rex.background.supervisor import _WindowsJobContainment

    containment = _WindowsJobContainment()
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        containment.add(child)
        containment.close()
        child.wait(timeout=5.0)
        assert child.returncode is not None
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5.0)


class _HealthPublishingFactory(_FakeProcessFactory):
    def __init__(self, paths: BackgroundPaths) -> None:
        super().__init__()
        self.paths = paths

    def __call__(self, spec: ComponentSpec) -> _FakeChild:
        child = super().__call__(spec)
        if spec.name == "voice_agent":
            _write_voice_health(self.paths, child.pid, HealthState.READY)
        return child


def test_voice_health_published_during_spawn_is_not_deleted(tmp_path: Path) -> None:
    paths = BackgroundPaths.from_runtime_root(tmp_path)
    factory = _HealthPublishingFactory(paths)
    supervisor = RuntimeSupervisor(
        paths,
        _spec("core"),
        _spec("voice_agent"),
        process_factory=factory,
        clock=lambda: 100.0,
    )
    supervisor.start()
    _write_core_endpoint(paths, factory.children["core"][0].pid)
    supervisor.tick()
    supervisor.tick()

    assert supervisor.health().voice_agent.state is HealthState.READY
    supervisor.stop()


def test_ready_core_with_lost_endpoint_reenters_starting_and_times_out(tmp_path: Path) -> None:
    factory = _FakeProcessFactory()
    now = [100.0]
    supervisor = RuntimeSupervisor(
        BackgroundPaths.from_runtime_root(tmp_path),
        _spec("core", max_restarts=1),
        _spec("voice_agent"),
        core_start_timeout_seconds=5.0,
        process_factory=factory,
        clock=lambda: now[0],
    )
    supervisor.start()
    core = factory.children["core"][0]
    _write_core_endpoint(supervisor.paths, core.pid)
    supervisor.tick()
    assert supervisor.health().core.state is HealthState.READY

    now[0] = 110.0
    supervisor.paths.core_endpoint_file.unlink()
    supervisor.tick()
    assert supervisor.health().core.state is HealthState.STARTING
    assert supervisor.health().core.detail_code == "core_starting"
    assert supervisor.health().voice_agent.state is HealthState.DEGRADED

    now[0] = 114.9
    supervisor.tick()
    assert core.returncode is None
    now[0] = 115.1
    supervisor.tick()
    assert core.returncode == 0
    assert supervisor.health().core.detail_code == "restart_backoff"
    supervisor.stop()


class _TerminateFailChild(_FakeChild):
    def terminate(self) -> None:
        self.events.append(f"terminate:{self.name}")
        raise RuntimeError("terminate failed")


class _TerminateFailFactory(_FakeProcessFactory):
    def __call__(self, spec: ComponentSpec) -> _FakeChild:
        self.events.append(f"start:{spec.name}")
        child = _TerminateFailChild(spec.name, self._next_pid, self.events)
        self._next_pid += 1
        self.children[spec.name].append(child)
        return child


class _CloseFailContainment(_FakeContainment):
    def close(self) -> None:
        self.closed = True
        raise RuntimeError("containment close failed")


def test_start_failure_releases_lock_even_when_cleanup_steps_fail(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    factory = _TerminateFailFactory()
    containment = _CloseFailContainment()
    supervisor = RuntimeSupervisor(
        BackgroundPaths.from_runtime_root(tmp_path),
        _spec("core"),
        _spec("voice_agent"),
        process_factory=factory,
        containment_factory=lambda: containment,
        clock=lambda: 100.0,
    )
    monkeypatch.setattr(
        supervisor,
        "_write_health",
        lambda: (_ for _ in ()).throw(OSError("disk full")),
    )

    with pytest.raises(OSError, match="disk full"):
        supervisor.start()

    assert containment.closed is True
    with SingleInstanceLock(supervisor.paths.supervisor_lock):
        pass


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object contract")
def test_windows_containment_spawns_child_in_job_at_creation() -> None:
    from rex.background.supervisor import _WindowsJobContainment

    containment = _WindowsJobContainment()
    spec = ComponentSpec(
        name="probe",
        argv=(sys.executable, "-c", "import time; time.sleep(30)"),
        required=True,
    )
    child = containment.spawn(spec)
    try:
        containment.close()
        child.wait(timeout=5.0)
        assert child.returncode is not None
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5.0)


class _LaunchFailProcessFactory(_FakeProcessFactory):
    """Fake factory that raises on selected spawn attempts of a component."""

    def __init__(self, *, fail: dict[str, set[int]]) -> None:
        super().__init__()
        self._fail = {name: set(indexes) for name, indexes in fail.items()}
        self._attempts: dict[str, int] = {"core": 0, "voice_agent": 0}

    def __call__(self, spec: ComponentSpec) -> _FakeChild:
        attempt = self._attempts.get(spec.name, 0)
        self._attempts[spec.name] = attempt + 1
        if attempt in self._fail.get(spec.name, set()):
            self.events.append(f"start-fail:{spec.name}")
            raise OSError("spawn failed")
        return super().__call__(spec)


def test_voice_launch_exception_leaves_core_running_and_schedules_restart(
    tmp_path: Path,
) -> None:
    now = [100.0]
    factory = _LaunchFailProcessFactory(fail={"voice_agent": {0}})
    supervisor = RuntimeSupervisor(
        BackgroundPaths.from_runtime_root(tmp_path),
        _spec("core"),
        _spec("voice_agent", max_restarts=3),
        poll_interval=0.01,
        restart_backoff_seconds=1.0,
        process_factory=factory,
        clock=lambda: now[0],
    )
    supervisor.start()
    _write_core_endpoint(supervisor.paths, factory.children["core"][0].pid)

    # First Voice launch attempt raises: it must not escape tick().
    supervisor.tick()

    assert len(factory.children["voice_agent"]) == 0
    health = supervisor.health()
    assert health.core.state is HealthState.READY
    assert health.voice_agent.state is HealthState.STARTING
    assert health.voice_agent.detail_code == "restart_backoff"
    assert len(factory.children["core"]) == 1

    # After backoff the Voice Agent launches; the healthy Core is untouched.
    now[0] = 101.0
    supervisor.tick()
    assert len(factory.children["voice_agent"]) == 1
    assert len(factory.children["core"]) == 1
    assert supervisor.health().core.state is HealthState.READY
    supervisor.stop()


def test_repeated_voice_launch_exception_marks_voice_failed(tmp_path: Path) -> None:
    now = [100.0]
    factory = _LaunchFailProcessFactory(fail={"voice_agent": {0, 1, 2, 3}})
    supervisor = RuntimeSupervisor(
        BackgroundPaths.from_runtime_root(tmp_path),
        _spec("core"),
        _spec("voice_agent", max_restarts=2),
        poll_interval=0.01,
        restart_backoff_seconds=1.0,
        process_factory=factory,
        clock=lambda: now[0],
    )
    supervisor.start()
    _write_core_endpoint(supervisor.paths, factory.children["core"][0].pid)

    for _ in range(4):
        supervisor.tick()
        now[0] += 1.0

    health = supervisor.health()
    assert health.voice_agent.state is HealthState.FAILED
    assert health.voice_agent.detail_code == "restart_limit_exceeded"
    assert health.core.state is HealthState.READY
    assert len(factory.children["core"]) == 1
    assert len(factory.children["voice_agent"]) == 0
    supervisor.stop()


def test_core_relaunch_exception_consumes_budget_and_fails_without_crashing_loop(
    tmp_path: Path,
) -> None:
    now = [100.0]
    factory = _LaunchFailProcessFactory(fail={"core": {1, 2}})
    supervisor = RuntimeSupervisor(
        BackgroundPaths.from_runtime_root(tmp_path),
        _spec("core", max_restarts=2),
        _spec("voice_agent"),
        poll_interval=0.01,
        restart_backoff_seconds=1.0,
        process_factory=factory,
        clock=lambda: now[0],
    )
    supervisor.start()
    _write_core_endpoint(supervisor.paths, factory.children["core"][0].pid)
    supervisor.tick()
    assert supervisor.health().core.state is HealthState.READY

    # Core crashes and enters bounded restart backoff.
    factory.children["core"][0].returncode = 9
    supervisor.tick()
    assert supervisor.health().core.state is HealthState.STARTING
    assert supervisor.health().core.detail_code == "restart_backoff"

    # Relaunch attempt #1 raises: absorbed, budget consumed, backoff rescheduled.
    now[0] = 101.0
    supervisor.tick()
    assert len(factory.children["core"]) == 1
    assert supervisor.health().core.state is HealthState.STARTING
    assert supervisor.health().core.detail_code == "restart_backoff"

    # Relaunch attempt #2 raises: budget exhausted, Core marked FAILED, loop alive.
    now[0] = 102.0
    supervisor.tick()
    assert supervisor.health().core.state is HealthState.FAILED
    assert supervisor.health().core.detail_code == "restart_limit_exceeded"
    assert supervisor.health().voice_agent.detail_code == "core_unavailable"

    # The supervisor loop keeps advancing without raising.
    now[0] = 103.0
    supervisor.tick()
    assert supervisor.health().core.state is HealthState.FAILED
    supervisor.stop()


def _fail_unlink_of(monkeypatch: pytest.MonkeyPatch, target: Path, error: OSError) -> None:
    """Make ``Path.unlink`` raise ``error`` only for ``target`` (sharing violation)."""

    real_unlink = Path.unlink

    def _unlink(self: Path, *args: object, **kwargs: object) -> None:
        if self == target:
            raise error
        return real_unlink(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "unlink", _unlink)


def test_voice_health_cleanup_failure_during_failed_launch_keeps_core_running(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    now = [100.0]
    factory = _LaunchFailProcessFactory(fail={"voice_agent": {0}})
    supervisor = RuntimeSupervisor(
        BackgroundPaths.from_runtime_root(tmp_path),
        _spec("core"),
        _spec("voice_agent", max_restarts=3),
        poll_interval=0.01,
        restart_backoff_seconds=1.0,
        process_factory=factory,
        clock=lambda: now[0],
    )
    supervisor.start()
    _write_core_endpoint(supervisor.paths, factory.children["core"][0].pid)
    _write_voice_health(supervisor.paths, factory.children["core"][0].pid + 1, HealthState.STARTING)

    # A Windows sharing-style violation while clearing the stale Voice health
    # file during a failed Voice launch must not escape tick() and take Core down.
    _fail_unlink_of(
        monkeypatch,
        supervisor.paths.voice_agent_health_file,
        PermissionError("sharing violation"),
    )

    supervisor.tick()

    health = supervisor.health()
    assert health.core.state is HealthState.READY
    assert len(factory.children["core"]) == 1
    assert health.voice_agent.state is HealthState.STARTING
    assert health.voice_agent.detail_code == "restart_backoff"

    monkeypatch.undo()
    supervisor.stop()


def test_voice_health_cleanup_failure_on_core_exit_does_not_escape_tick(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    now = [100.0]
    factory = _FakeProcessFactory()
    supervisor = RuntimeSupervisor(
        BackgroundPaths.from_runtime_root(tmp_path),
        _spec("core", max_restarts=3),
        _spec("voice_agent"),
        poll_interval=0.01,
        restart_backoff_seconds=1.0,
        process_factory=factory,
        clock=lambda: now[0],
    )
    supervisor.start()
    _write_core_endpoint(supervisor.paths, factory.children["core"][0].pid)
    supervisor.tick()
    voice = factory.children["voice_agent"][0]
    _write_voice_health(supervisor.paths, voice.pid, HealthState.READY)

    # Core crashes; clearing the Voice health file during lifecycle cleanup
    # hits a sharing-style violation. It must not escape tick(); Core still
    # enters its own bounded restart and Voice degrades to core_unavailable.
    _fail_unlink_of(
        monkeypatch,
        supervisor.paths.voice_agent_health_file,
        PermissionError("sharing violation"),
    )
    factory.children["core"][0].returncode = 9

    supervisor.tick()

    health = supervisor.health()
    assert health.core.state is HealthState.STARTING
    assert health.core.detail_code == "restart_backoff"
    assert health.voice_agent.state is HealthState.DEGRADED
    assert health.voice_agent.detail_code == "core_unavailable"

    monkeypatch.undo()
    supervisor.stop()


def _started_supervisor_with_live_voice(
    tmp_path: Path,
    factory: _FakeProcessFactory,
    containment: _FakeContainment,
) -> RuntimeSupervisor:
    supervisor = RuntimeSupervisor(
        BackgroundPaths.from_runtime_root(tmp_path),
        _spec("core"),
        _spec("voice_agent"),
        process_factory=factory,
        containment_factory=lambda: containment,
        clock=lambda: 100.0,
    )
    supervisor.start()
    _write_core_endpoint(supervisor.paths, factory.children["core"][0].pid)
    supervisor.tick()  # Core becomes READY, so the Voice Agent is launched.
    assert len(factory.children["voice_agent"]) == 1
    return supervisor


def test_stop_attempts_core_after_voice_terminate_fails_and_releases_ownership(
    tmp_path: Path,
) -> None:
    factory = _TerminateFailFactory()
    containment = _FakeContainment()
    supervisor = _started_supervisor_with_live_voice(tmp_path, factory, containment)

    with pytest.raises(RuntimeError, match="terminate failed"):
        supervisor.stop()

    # Voice stop is attempted before Core stop, and Core stop still runs even
    # though the Voice terminate raised.
    assert "terminate:voice_agent" in factory.events
    assert "terminate:core" in factory.events
    assert factory.events.index("terminate:voice_agent") < factory.events.index("terminate:core")

    # Ownership resources are released despite the mid-cleanup failure.
    assert containment.closed is True
    with SingleInstanceLock(supervisor.paths.supervisor_lock):
        pass

    # An orderly stop whose child termination failed stays observable as
    # failed rather than silently reporting "stopped".
    health = supervisor.health()
    assert health.voice_agent.state is HealthState.FAILED
    assert health.core.state is HealthState.FAILED

    # A second stop after a failed cleanup is a safe no-op.
    supervisor.stop()


def test_stop_releases_lock_even_when_containment_close_fails(tmp_path: Path) -> None:
    factory = _FakeProcessFactory()
    containment = _CloseFailContainment()
    supervisor = _started_supervisor_with_live_voice(tmp_path, factory, containment)

    with pytest.raises(RuntimeError, match="containment close failed"):
        supervisor.stop()

    assert containment.closed is True
    # The supervisor lock is released so the runtime can be restarted.
    with SingleInstanceLock(supervisor.paths.supervisor_lock):
        pass

    # Both children terminated cleanly; only ownership teardown failed.
    assert factory.children["voice_agent"][0].returncode == 0
    assert factory.children["core"][0].returncode == 0
    health = supervisor.health()
    assert health.voice_agent.state is HealthState.STOPPED
    assert health.core.state is HealthState.STOPPED

    supervisor.stop()


def test_stop_reraises_first_failure_with_notes_and_still_releases_lock(tmp_path: Path) -> None:
    factory = _TerminateFailFactory()
    containment = _CloseFailContainment()
    supervisor = _started_supervisor_with_live_voice(tmp_path, factory, containment)

    with pytest.raises(RuntimeError, match="terminate failed") as excinfo:
        supervisor.stop()

    # The first meaningful failure (Voice stop) is raised; later cleanup
    # failures (Core stop, containment close) are attached as notes, not
    # swallowed.
    notes = "\n".join(getattr(excinfo.value, "__notes__", []))
    assert "containment close failed" in notes

    assert containment.closed is True
    with SingleInstanceLock(supervisor.paths.supervisor_lock):
        pass

    supervisor.stop()


def test_failed_stop_cleanup_leaves_runtime_restartable(tmp_path: Path) -> None:
    factory = _TerminateFailFactory()
    containment = _CloseFailContainment()
    supervisor = _started_supervisor_with_live_voice(tmp_path, factory, containment)

    with pytest.raises(RuntimeError):
        supervisor.stop()

    # Every cleanup step failed, yet a brand-new supervisor over the same paths
    # must still acquire the lock and launch Core -- a failed stop can never
    # leave the background runtime permanently unstartable.
    restart_factory = _FakeProcessFactory()
    restart_containment = _FakeContainment()
    restarted = RuntimeSupervisor(
        supervisor.paths,
        _spec("core"),
        _spec("voice_agent"),
        process_factory=restart_factory,
        containment_factory=lambda: restart_containment,
        clock=lambda: 200.0,
    )
    restarted.start()
    assert restart_factory.events == ["start:core"]
    restarted.stop()
    assert restart_containment.closed is True


def test_stop_releases_ownership_when_health_write_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    factory = _FakeProcessFactory()
    containment = _FakeContainment()
    supervisor = _started_supervisor_with_live_voice(tmp_path, factory, containment)
    monkeypatch.setattr(
        supervisor,
        "_write_health",
        lambda: (_ for _ in ()).throw(OSError("health write failed")),
    )

    with pytest.raises(OSError, match="health write failed"):
        supervisor.stop()

    assert containment.closed is True
    with SingleInstanceLock(supervisor.paths.supervisor_lock):
        pass
    assert supervisor.health().voice_agent.state is HealthState.STOPPED
    assert supervisor.health().core.state is HealthState.STOPPED


def test_core_unavailable_voice_stop_failure_does_not_escape_or_duplicate(
    tmp_path: Path,
) -> None:
    factory = _TerminateFailFactory()
    containment = _FakeContainment()
    supervisor = _started_supervisor_with_live_voice(tmp_path, factory, containment)
    core = factory.children["core"][0]
    voice = factory.children["voice_agent"][0]
    supervisor.paths.core_endpoint_file.unlink()

    supervisor.tick()

    assert core.poll() is None
    assert supervisor._voice.process is voice
    assert len(factory.children["voice_agent"]) == 1
    assert supervisor.health().voice_agent.state is HealthState.DEGRADED
    assert supervisor.health().voice_agent.detail_code == "core_unavailable"


def test_stale_voice_child_health_is_not_treated_as_ready(tmp_path: Path) -> None:
    factory = _FakeProcessFactory()
    now = [100.0]
    supervisor = _supervisor(tmp_path, factory, now=now)
    supervisor.start()
    _write_core_endpoint(supervisor.paths, factory.children["core"][0].pid)
    supervisor.tick()
    voice = factory.children["voice_agent"][0]
    _write_voice_health(supervisor.paths, voice.pid, HealthState.READY)
    payload = json.loads(supervisor.paths.voice_agent_health_file.read_text(encoding="utf-8"))
    payload["observed_at"] = 90.0
    supervisor.paths.voice_agent_health_file.write_text(
        json.dumps(payload) + "\n", encoding="utf-8"
    )

    supervisor.tick()

    assert supervisor.health().voice_agent.state is HealthState.STARTING


def test_core_endpoint_cleanup_sharing_violation_does_not_abort_restart(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    factory = _FakeProcessFactory()
    now = [100.0]
    supervisor = _supervisor(tmp_path, factory, now=now)
    supervisor.start()
    core = factory.children["core"][0]
    _write_core_endpoint(supervisor.paths, core.pid)
    supervisor.tick()
    _fail_unlink_of(
        monkeypatch,
        supervisor.paths.core_endpoint_file,
        PermissionError("sharing violation"),
    )
    core.returncode = 9

    supervisor.tick()

    assert supervisor.health().core.state is HealthState.STARTING
    assert supervisor.health().core.detail_code == "restart_backoff"


def test_core_exit_voice_stop_failure_still_schedules_core_restart(tmp_path: Path) -> None:
    now = [100.0]
    factory = _TerminateFailFactory()
    supervisor = _supervisor(tmp_path, factory, now=now)
    supervisor.start()
    core = factory.children["core"][0]
    _write_core_endpoint(supervisor.paths, core.pid)
    supervisor.tick()
    assert len(factory.children["voice_agent"]) == 1

    core.returncode = 9
    supervisor.tick()

    assert supervisor.health().core.state is HealthState.STARTING
    assert supervisor.health().core.detail_code == "restart_backoff"
    assert supervisor.health().voice_agent.state is HealthState.DEGRADED
    assert supervisor.health().voice_agent.detail_code == "core_unavailable"
    assert supervisor._voice.process is factory.children["voice_agent"][0]


def test_core_startup_timeout_terminate_failure_is_contained_without_duplicate(
    tmp_path: Path,
) -> None:
    now = [100.0]
    factory = _TerminateFailFactory()
    supervisor = RuntimeSupervisor(
        BackgroundPaths.from_runtime_root(tmp_path),
        _spec("core", max_restarts=1),
        _spec("voice_agent"),
        core_start_timeout_seconds=5.0,
        process_factory=factory,
        clock=lambda: now[0],
    )
    supervisor.start()
    core = factory.children["core"][0]
    now[0] = 106.0

    supervisor.tick()

    assert supervisor._core.process is core
    assert len(factory.children["core"]) == 1
    assert supervisor.health().core.state is HealthState.FAILED
    assert supervisor.health().core.detail_code == "stop_failed"
    assert supervisor.health().voice_agent.detail_code == "core_unavailable"


def test_nan_voice_child_health_is_not_treated_as_ready(tmp_path: Path) -> None:
    factory = _FakeProcessFactory()
    now = [100.0]
    supervisor = _supervisor(tmp_path, factory, now=now)
    supervisor.start()
    _write_core_endpoint(supervisor.paths, factory.children["core"][0].pid)
    supervisor.tick()
    voice = factory.children["voice_agent"][0]
    _write_voice_health(supervisor.paths, voice.pid, HealthState.READY)
    payload = json.loads(supervisor.paths.voice_agent_health_file.read_text(encoding="utf-8"))
    payload["observed_at"] = float("nan")
    supervisor.paths.voice_agent_health_file.write_text(
        json.dumps(payload) + "\n", encoding="utf-8"
    )

    supervisor.tick()

    assert supervisor.health().voice_agent.state is HealthState.STARTING


def test_tick_keeps_runtime_alive_when_aggregate_health_write_temporarily_fails(
    tmp_path: Path, monkeypatch
) -> None:
    factory = _FakeProcessFactory()
    supervisor = _supervisor(tmp_path, factory)
    supervisor.start()
    _write_core_endpoint(supervisor.paths, factory.children["core"][0].pid)

    monkeypatch.setattr(
        supervisor,
        "_write_health",
        lambda: (_ for _ in ()).throw(OSError("sharing violation")),
    )

    supervisor.tick()

    assert factory.children["core"][0].poll() is None
    assert supervisor.health().core.state is HealthState.READY
    monkeypatch.undo()
    supervisor.stop()


def test_supervisor_accepts_listening_paused_voice_health(tmp_path: Path) -> None:
    factory = _FakeProcessFactory()
    supervisor = _supervisor(tmp_path, factory)
    supervisor.start()
    _write_core_endpoint(supervisor.paths, factory.children["core"][0].pid)
    supervisor.tick()
    voice = factory.children["voice_agent"][0]
    _write_voice_health(
        supervisor.paths,
        voice.pid,
        HealthState.DEGRADED,
        detail_code="listening_paused",
    )

    supervisor.tick()

    health = supervisor.health().voice_agent
    assert health.state is HealthState.DEGRADED
    assert health.detail_code == "listening_paused"
    supervisor.stop()


def test_orderly_stop_allows_voice_cleanup_and_requests_authenticated_core_shutdown(
    tmp_path: Path, monkeypatch
) -> None:
    paths = BackgroundPaths.from_runtime_root(tmp_path)
    events: list[str] = []

    class _OrderlyChild(_FakeChild):
        def wait(self, timeout: float | None = None) -> int:
            del timeout
            if self.name == "voice_agent" and paths.stop_file.exists():
                events.append("graceful:voice_agent")
                self.returncode = 0
                return 0
            if self.returncode is not None:
                return self.returncode
            raise subprocess.TimeoutExpired(self.name, 1.0)

    class _OrderlyFactory(_FakeProcessFactory):
        def __call__(self, spec: ComponentSpec) -> _OrderlyChild:
            events.append(f"start:{spec.name}")
            child = _OrderlyChild(spec.name, self._next_pid, events)
            self._next_pid += 1
            self.children[spec.name].append(child)
            return child

    factory = _OrderlyFactory()

    def _request_core_shutdown(_path: Path) -> bool:
        events.append("shutdown:core")
        factory.children["core"][0].returncode = 0
        return True

    supervisor = RuntimeSupervisor(
        paths,
        _spec("core"),
        _spec("voice_agent"),
        poll_interval=0.01,
        process_factory=factory,
        core_shutdown_requester=_request_core_shutdown,
        clock=lambda: 100.0,
    )
    supervisor.start()
    core = factory.children["core"][0]
    _write_core_endpoint(paths, core.pid)
    supervisor.tick()

    supervisor.stop()

    assert "graceful:voice_agent" in events
    assert "shutdown:core" in events
    assert "terminate:voice_agent" not in events
    assert "terminate:core" not in events
    assert events.index("graceful:voice_agent") < events.index("shutdown:core")


def test_authenticated_core_shutdown_request_uses_bounded_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, float] = {}

    class _Client:
        async def shutdown(self) -> dict[str, bool]:
            return {"ok": True}

    def _load(_path: Path, *, timeout: float):
        captured["timeout"] = timeout
        return _Client()

    monkeypatch.setattr(
        "rex.background.supervisor.CoreClient.from_endpoint_file",
        _load,
    )

    assert _request_authenticated_core_shutdown(tmp_path / "core-endpoint.json") is True
    assert 0 < captured["timeout"] <= 2.0


def test_supervisor_codefactor_regressions_stay_closed() -> None:
    import ast

    source_path = Path(__file__).parents[2] / "rex" / "background" / "supervisor.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    functions = {node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}

    for name in (
        "_handle_core_exit",
        "_relaunch_within_policy",
        "_discard_voice_health_file",
        "_remove_endpoint_for_pid",
    ):
        handlers = [
            node for node in ast.walk(functions[name]) if isinstance(node, ast.ExceptHandler)
        ]
        assert not any(
            len(handler.body) == 1
            and (
                isinstance(handler.body[0], (ast.Pass, ast.Return))
                or (
                    isinstance(handler.body[0], ast.Expr)
                    and isinstance(handler.body[0].value, ast.Call)
                    and isinstance(handler.body[0].value.func, ast.Attribute)
                    and isinstance(handler.body[0].value.func.value, ast.Name)
                    and handler.body[0].value.func.value.id == "logger"
                )
            )
            for handler in handlers
        ), name

    voice_handler = functions["_handle_voice"]
    complexity = 1
    complexity += sum(
        isinstance(node, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.IfExp, ast.comprehension))
        for node in ast.walk(voice_handler)
    )
    complexity += sum(isinstance(node, ast.ExceptHandler) for node in ast.walk(voice_handler))
    complexity += sum(
        len(node.values) - 1 for node in ast.walk(voice_handler) if isinstance(node, ast.BoolOp)
    )
    assert complexity <= 8
