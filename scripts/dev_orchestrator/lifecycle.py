import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path

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
    stamp = now or datetime.now(UTC)
    AtomicJsonStore(path).write({"pid": pid or os.getpid(), "timestamp": stamp.isoformat()})


def read_heartbeat(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


class HeartbeatPump:
    def __init__(self, path: Path, *, interval_seconds: float = 30.0) -> None:
        if interval_seconds <= 0:
            raise ValueError("heartbeat interval must be positive")
        self.path = path
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            write_heartbeat(self.path)

    def __enter__(self) -> "HeartbeatPump":
        write_heartbeat(self.path)
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="askrex-dev-orchestrator-heartbeat",
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=max(1.0, self.interval_seconds * 2))
        write_heartbeat(self.path)
        self._thread = None
