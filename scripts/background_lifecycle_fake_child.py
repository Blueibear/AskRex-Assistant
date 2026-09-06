"""Self-contained Core/Voice stand-in for the packaged lifecycle smoke.

This fixture intentionally imports no AskRex modules. The installed supervisor
must come from the packaged managed runtime, while this deterministic child only
speaks the same content-free endpoint/health file protocol needed to exercise
real subprocess, single-instance, liveness, and orderly-stop mechanics.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

_HEARTBEAT_SECONDS = 1.0
_POLL_SECONDS = 0.1


@dataclass(frozen=True, slots=True)
class _Paths:
    state_dir: Path
    core_endpoint_file: Path
    voice_agent_health_file: Path
    stop_file: Path


def _paths(runtime_root: Path) -> _Paths:
    state_dir = runtime_root.expanduser().resolve() / "background"
    return _Paths(
        state_dir=state_dir,
        core_endpoint_file=state_dir / "core-endpoint.json",
        voice_agent_health_file=state_dir / "voice-agent-health.json",
        stop_file=state_dir / "stop.request",
    )


def _atomic_write(path: Path, payload: dict[str, object]) -> None:
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


def _run_core(paths: _Paths) -> int:
    _atomic_write(
        paths.core_endpoint_file,
        {
            "host": "127.0.0.1",
            "port": 51999,
            "token": "f" * 32,
            "pid": os.getpid(),
        },
    )
    while not paths.stop_file.exists():
        time.sleep(_POLL_SECONDS)
    return 0


def _run_voice_agent(paths: _Paths) -> int:
    def _write_ready() -> None:
        _atomic_write(
            paths.voice_agent_health_file,
            {
                "component": "voice_agent",
                "state": "ready",
                "detail_code": None,
                "observed_at": time.time(),
                "pid": os.getpid(),
            },
        )

    _write_ready()
    last_heartbeat = time.monotonic()
    while not paths.stop_file.exists():
        time.sleep(_POLL_SECONDS)
        if time.monotonic() - last_heartbeat >= _HEARTBEAT_SECONDS:
            _write_ready()
            last_heartbeat = time.monotonic()
    return 0


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(
            "usage: background_lifecycle_fake_child.py <core|voice_agent> <runtime_root>",
            file=sys.stderr,
        )
        return 2
    role, runtime_root = argv
    paths = _paths(Path(runtime_root))
    if role == "core":
        return _run_core(paths)
    if role == "voice_agent":
        return _run_voice_agent(paths)
    print(f"unknown role: {role!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
