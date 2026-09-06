"""Internal process entrypoints for the persistent Rex background runtime."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import signal
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rex.background.supervisor import RuntimeSupervisor

from rex.background.lock import AlreadyRunningError, SingleInstanceLock
from rex.background.paths import BackgroundPaths
from rex.background.types import ComponentHealth, HealthState, RuntimeHealth
from rex.background.windows_startup import (
    DEFAULT_TASK_NAME,
    StartupTaskError,
    install_startup,
    remove_startup,
)

_CONTENT_FREE_DETAIL_CODES = frozenset(
    {
        None,
        "health_unavailable",
        "core_starting",
        "core_unavailable",
        "restart_backoff",
        "restart_limit_exceeded",
        "stop_failed",
        "microphone_unavailable",
        "speaker_unavailable",
        "wakeword_unavailable",
        "listening_paused",
    }
)

_STATUS_MAX_AGE_SECONDS = 5.0
_STOP_WAIT_MAX_SECONDS = 30.0
_STOP_WAIT_POLL_SECONDS = 0.1


_RUNTIME_ENV_NAMES = (
    "ASKREX_RUNTIME_DIR",
    "ASKREX_CONFIG_PATH",
    "ASKREX_ENV_PATH",
    "ASKREX_PROFILES_DIR",
    "REX_DATA_DIR",
    "ASKREX_HOUSEHOLD_DATA_DIR",
    "ASKREX_USERS_DATA_DIR",
    "ASKREX_MEMORY_DIR",
    "ASKREX_PACKAGED",
    "REX_ALLOW_PLAINTEXT_CREDENTIAL_FALLBACK",
)


def _configure_runtime_environment(
    paths: BackgroundPaths, *, packaged: bool = False
) -> dict[str, str | None]:
    """Make the explicit runtime root authoritative until the caller restores it."""

    previous = {name: os.environ.get(name) for name in _RUNTIME_ENV_NAMES}
    root = paths.runtime_root
    values = {
        "ASKREX_RUNTIME_DIR": root,
        "ASKREX_CONFIG_PATH": root / "config" / "rex_config.json",
        "ASKREX_ENV_PATH": root / ".env",
        "ASKREX_PROFILES_DIR": root / "profiles",
        "REX_DATA_DIR": root / "data",
        "ASKREX_HOUSEHOLD_DATA_DIR": root / "data" / "household",
        "ASKREX_USERS_DATA_DIR": root / "data" / "users",
        "ASKREX_MEMORY_DIR": root / "Memory",
    }
    for name, value in values.items():
        os.environ[name] = str(value)
    if packaged:
        os.environ["ASKREX_PACKAGED"] = "1"
        os.environ.pop("REX_ALLOW_PLAINTEXT_CREDENTIAL_FALLBACK", None)
    return previous


def _restore_runtime_environment(previous: dict[str, str | None]) -> None:
    for name, value in previous.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


def build_supervisor(
    paths: BackgroundPaths,
    *,
    user_id: str,
    activation_mode: str = "wake-word",
    origin_device_id: str | None = None,
) -> RuntimeSupervisor:
    """Build child specs using the exact absolute interpreter running us."""

    from rex.background.supervisor import ComponentSpec, RuntimeSupervisor
    from rex.identity import validate_user_id

    user_id = validate_user_id(user_id)
    python = str(Path(sys.executable).resolve())
    root = str(paths.runtime_root)
    core = ComponentSpec(
        name="core",
        argv=(python, "-m", "rex.background.cli", "core", "--runtime-root", root),
        required=True,
    )
    voice_argv = [
        python,
        "-m",
        "rex.background.cli",
        "voice-agent",
        "--runtime-root",
        root,
        "--user",
        user_id,
        "--activation-mode",
        activation_mode,
    ]
    if origin_device_id:
        voice_argv.extend(("--origin-device-id", origin_device_id))
    voice = ComponentSpec(
        name="voice_agent",
        argv=tuple(voice_argv),
        required=True,
    )
    return RuntimeSupervisor(paths, core, voice)


async def _run_core(paths: BackgroundPaths) -> None:
    from rex.assistant import Assistant
    from rex.background.core_server import CoreServer

    server = CoreServer(assistant_factory=Assistant, paths=paths)
    await server.start()
    await server.wait_closed()


def _unavailable_health() -> RuntimeHealth:
    observed_at = time.time()

    def unavailable(component: str) -> ComponentHealth:
        return ComponentHealth(
            component=component,
            state=HealthState.UNAVAILABLE,
            detail_code="health_unavailable",
            observed_at=observed_at,
            pid=None,
        )

    return RuntimeHealth(
        core=unavailable("core"),
        voice_agent=unavailable("voice_agent"),
        supervisor_pid=0,
        observed_at=observed_at,
    )


def _parse_component_health(payload: object, expected_component: str) -> ComponentHealth | None:
    if not isinstance(payload, dict):
        return None
    allowed = {"component", "state", "detail_code", "observed_at", "pid"}
    if set(payload) != allowed or payload.get("component") != expected_component:
        return None
    detail_code = payload.get("detail_code")
    observed_at = payload.get("observed_at")
    pid = payload.get("pid")
    if detail_code not in _CONTENT_FREE_DETAIL_CODES:
        return None
    if isinstance(observed_at, bool) or not isinstance(observed_at, (int, float)):
        return None
    observed_at_float = float(observed_at)
    if not math.isfinite(observed_at_float):
        return None
    if pid is not None and (
        isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0 or pid > 0xFFFFFFFF
    ):
        return None
    state_value = payload.get("state")
    if not isinstance(state_value, str):
        return None
    try:
        state = HealthState(state_value)
    except (TypeError, ValueError):
        return None
    return ComponentHealth(expected_component, state, detail_code, observed_at_float, pid)


def _read_status(paths: BackgroundPaths) -> tuple[dict[str, object], int]:
    from rex.background.supervisor import _pid_is_alive

    try:
        payload = json.loads(paths.health_file.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return _unavailable_health().to_dict(), 1
    allowed = {"core", "voice_agent", "supervisor_pid", "observed_at"}
    if not isinstance(payload, dict) or set(payload) != allowed:
        return _unavailable_health().to_dict(), 1
    core = _parse_component_health(payload.get("core"), "core")
    voice = _parse_component_health(payload.get("voice_agent"), "voice_agent")
    supervisor_pid = payload.get("supervisor_pid")
    observed_at = payload.get("observed_at")
    if (
        core is None
        or voice is None
        or isinstance(supervisor_pid, bool)
        or not isinstance(supervisor_pid, int)
        or supervisor_pid <= 0
        or supervisor_pid > 0xFFFFFFFF
        or not _pid_is_alive(supervisor_pid)
        or isinstance(observed_at, bool)
        or not isinstance(observed_at, (int, float))
    ):
        return _unavailable_health().to_dict(), 1
    observed_at_float = float(observed_at)
    if not math.isfinite(observed_at_float):
        return _unavailable_health().to_dict(), 1
    age = time.time() - observed_at_float
    if age < -_STATUS_MAX_AGE_SECONDS or age > _STATUS_MAX_AGE_SECONDS:
        return _unavailable_health().to_dict(), 1
    health = RuntimeHealth(core, voice, supervisor_pid, observed_at_float)
    return health.to_dict(), 0


def _request_stop(paths: BackgroundPaths) -> None:
    paths.state_dir.mkdir(parents=True, exist_ok=True)
    paths.stop_file.touch(exist_ok=True)


def _supervisor_is_running(paths: BackgroundPaths) -> bool:
    lock = SingleInstanceLock(paths.supervisor_lock)
    try:
        lock.acquire()
    except AlreadyRunningError:
        return True
    else:
        lock.close()
        return False


def _wait_for_supervisor_stop(paths: BackgroundPaths, timeout_seconds: float) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while _supervisor_is_running(paths):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(_STOP_WAIT_POLL_SECONDS, remaining))
    return True


def _install_stop_signal_handlers(paths: BackgroundPaths) -> None:
    def _handle_signal(_signum: int, _frame: object) -> None:
        _request_stop(paths)

    for name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, name, None)
        if sig is not None:
            signal.signal(sig, _handle_signal)


def _add_runtime_root(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-r",
        "--runtime-root",
        required=True,
        help="Absolute AskRex runtime root containing background state",
    )


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m rex.background.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)

    supervisor = subparsers.add_parser("supervisor")
    _add_runtime_root(supervisor)
    supervisor.add_argument("-u", "--user", required=True)
    supervisor.add_argument(
        "--activation-mode",
        choices=("hold-to-talk", "wake-word"),
        default="wake-word",
    )
    supervisor.add_argument("--origin-device-id")
    supervisor.add_argument("-p", "--packaged", action="store_true")

    core = subparsers.add_parser("core")
    _add_runtime_root(core)

    voice = subparsers.add_parser("voice-agent")
    _add_runtime_root(voice)
    voice.add_argument("--user", required=True)
    voice.add_argument(
        "--activation-mode",
        choices=("hold-to-talk", "wake-word"),
        default="wake-word",
    )
    voice.add_argument("--origin-device-id")

    status = subparsers.add_parser("status")
    _add_runtime_root(status)

    stop = subparsers.add_parser("stop")
    _add_runtime_root(stop)
    stop.add_argument("--wait-seconds", type=float, default=0.0)

    install = subparsers.add_parser("install-startup")
    _add_runtime_root(install)
    install.add_argument("--pythonw-path", required=True)
    install.add_argument("--user", required=True)
    install.add_argument("--run-as-user")
    install.add_argument("--task-name", default=DEFAULT_TASK_NAME)
    install.add_argument("--packaged", action="store_true")

    remove = subparsers.add_parser("remove-startup")
    _add_runtime_root(remove)
    remove.add_argument("--task-name", default=DEFAULT_TASK_NAME)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = create_parser().parse_args(argv)
    paths = BackgroundPaths.from_runtime_root(Path(args.runtime_root))
    previous = _configure_runtime_environment(
        paths, packaged=bool(getattr(args, "packaged", False))
    )
    try:
        return _dispatch(args, paths)
    finally:
        _restore_runtime_environment(previous)


def _dispatch(args: argparse.Namespace, paths: BackgroundPaths) -> int:
    if args.command == "install-startup":
        try:
            install_startup(
                args.task_name,
                Path(args.pythonw_path),
                paths.runtime_root,
                args.user,
                run_as_user=args.run_as_user,
            )
        except (StartupTaskError, ValueError):
            print(json.dumps({"ok": False, "detail_code": "startup_registration_failed"}))
            return 1
        print(json.dumps({"ok": True, "installed": True}, separators=(",", ":")))
        return 0
    if args.command == "remove-startup":
        try:
            remove_startup(args.task_name)
        except StartupTaskError:
            print(json.dumps({"ok": False, "detail_code": "startup_removal_failed"}))
            return 1
        print(json.dumps({"ok": True, "removed": True}, separators=(",", ":")))
        return 0

    if args.command == "status":
        payload, result = _read_status(paths)
        print(json.dumps(payload, separators=(",", ":"), sort_keys=True))
        return result
    if args.command == "stop":
        wait_seconds = float(args.wait_seconds)
        if (
            not math.isfinite(wait_seconds)
            or wait_seconds < 0
            or wait_seconds > _STOP_WAIT_MAX_SECONDS
        ):
            print(json.dumps({"ok": False, "detail_code": "invalid_stop_wait"}))
            return 2
        _request_stop(paths)
        if wait_seconds > 0 and not _wait_for_supervisor_stop(paths, wait_seconds):
            print(json.dumps({"ok": False, "detail_code": "stop_timeout"}, separators=(",", ":")))
            return 1
        print(json.dumps({"ok": True, "requested": True}, separators=(",", ":")))
        return 0

    if args.command == "core":
        asyncio.run(_run_core(paths))
        return 0
    if args.command == "voice-agent":
        from rex.background.voice_agent import run_voice_agent

        health = asyncio.run(
            run_voice_agent(
                args.user,
                paths,
                activation_mode=args.activation_mode,
                origin_device_id=args.origin_device_id,
            )
        )
        print(json.dumps(health.to_dict(), separators=(",", ":"), sort_keys=True))
        return 0 if health.state is HealthState.STOPPED else 1
    if args.command == "supervisor":
        runtime = build_supervisor(
            paths,
            user_id=args.user,
            activation_mode=args.activation_mode,
            origin_device_id=args.origin_device_id,
        )
        _install_stop_signal_handlers(paths)
        try:
            runtime.run()
        except AlreadyRunningError:
            return 2
        return 0
    raise AssertionError(f"Unhandled background command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
