import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .storage import AtomicJsonStore


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    try:
        os.kill(pid, 0)
    except (OSError, PermissionError):
        return False
    return True


class SupervisorLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._owned = False

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = str(os.getpid()).encode("ascii")
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                try:
                    existing = int(self.path.read_text(encoding="ascii").strip())
                except (OSError, ValueError):
                    existing = -1
                if _pid_is_alive(existing):
                    raise RuntimeError("development orchestrator is already running")
                self.path.unlink(missing_ok=True)
                continue
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
            self._owned = True
            return
        raise RuntimeError("could not acquire development orchestrator lock")

    def release(self) -> None:
        if self._owned:
            self.path.unlink(missing_ok=True)
            self._owned = False

    def __enter__(self) -> "SupervisorLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


def write_heartbeat(
    path: Path,
    *,
    now: datetime | None = None,
    pid: int | None = None,
) -> None:
    stamp = now or datetime.now(timezone.utc)
    AtomicJsonStore(path).write(
        {"pid": pid or os.getpid(), "timestamp": stamp.isoformat()}
    )


def read_heartbeat(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
