import errno
import json
import os
import threading
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from .storage import AtomicJsonStore


def _current_process_started_filetime() -> int:
    if os.name != "nt":
        return 116_444_736_000_000_000 + (time.time_ns() // 100)

    import ctypes
    from ctypes import wintypes

    creation = wintypes.FILETIME()
    exit_time = wintypes.FILETIME()
    kernel = wintypes.FILETIME()
    user = wintypes.FILETIME()
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    handle = kernel32.GetCurrentProcess()
    if not kernel32.GetProcessTimes(
        handle,
        ctypes.byref(creation),
        ctypes.byref(exit_time),
        ctypes.byref(kernel),
        ctypes.byref(user),
    ):
        raise OSError(ctypes.get_last_error(), "GetProcessTimes failed")
    return (creation.dwHighDateTime << 32) | creation.dwLowDateTime


_PROCESS_STARTED_FILETIME: int | None = None


def _process_started_filetime() -> int:
    global _PROCESS_STARTED_FILETIME
    if _PROCESS_STARTED_FILETIME is None:
        _PROCESS_STARTED_FILETIME = _current_process_started_filetime()
    return _PROCESS_STARTED_FILETIME


def _acquire_os_file_lock(handle) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                raise BlockingIOError("lock is already held") from exc
            raise
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _release_os_file_lock(handle) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class SupervisorLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle = None

    def acquire(self) -> None:
        if self._handle is not None:
            raise RuntimeError("development orchestrator lock is already owned by this instance")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b", buffering=0)
        try:
            _acquire_os_file_lock(handle)
        except BlockingIOError as exc:
            handle.close()
            raise RuntimeError("development orchestrator is already running") from exc
        except Exception:
            handle.close()
            raise

        try:
            handle.seek(0)
            handle.write(f"{os.getpid()}\n".encode("ascii"))
            handle.truncate()
            handle.flush()
            os.fsync(handle.fileno())
        except Exception:
            try:
                _release_os_file_lock(handle)
            finally:
                handle.close()
            raise
        self._handle = handle

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        self._handle = None
        try:
            _release_os_file_lock(handle)
        finally:
            handle.close()

    def __enter__(self) -> "SupervisorLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


_CONTROL_THREAD_LOCKS: dict[str, threading.RLock] = defaultdict(threading.RLock)
_CONTROL_DEPTH = threading.local()


class ControlPlaneLock:
    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self._key = str(self.path).casefold()
        self._thread_lock = _CONTROL_THREAD_LOCKS[self._key]
        self._file_lock = SupervisorLock(self.path)
        self._outermost = False

    def __enter__(self):
        self._thread_lock.acquire()
        depths = getattr(_CONTROL_DEPTH, "depths", {})
        depth = depths.get(self._key, 0)
        if depth == 0:
            try:
                self._file_lock.acquire()
            except Exception:
                self._thread_lock.release()
                raise
            self._outermost = True
        depths[self._key] = depth + 1
        _CONTROL_DEPTH.depths = depths
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        depths = getattr(_CONTROL_DEPTH, "depths", {})
        depth = depths.get(self._key, 1) - 1
        if depth <= 0:
            depths.pop(self._key, None)
            if self._outermost:
                self._file_lock.release()
        else:
            depths[self._key] = depth
        _CONTROL_DEPTH.depths = depths
        self._thread_lock.release()


def write_heartbeat(
    path: Path,
    *,
    now: datetime | None = None,
    pid: int | None = None,
    process_started_filetime: int | None = None,
) -> None:
    stamp = now or datetime.now(UTC)
    heartbeat_pid = os.getpid() if pid is None else pid
    started_filetime = (
        _process_started_filetime()
        if process_started_filetime is None
        else process_started_filetime
    )
    if isinstance(heartbeat_pid, bool) or not isinstance(heartbeat_pid, int) or heartbeat_pid <= 0:
        raise ValueError("heartbeat PID must be a positive integer")
    if (
        isinstance(started_filetime, bool)
        or not isinstance(started_filetime, int)
        or started_filetime <= 0
    ):
        raise ValueError("heartbeat process FILETIME must be a positive integer")
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("heartbeat timestamp must be timezone-aware")
    AtomicJsonStore(path).write(
        {
            "pid": heartbeat_pid,
            "process_started_filetime": started_filetime,
            "timestamp": stamp.astimezone(UTC).isoformat(),
        }
    )


def read_heartbeat(path: Path) -> dict | None:
    if not path.is_file():
        return None
    for attempt in range(5):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except PermissionError:
            if attempt == 4:
                return None
            time.sleep(0.01 * (attempt + 1))
        except (OSError, json.JSONDecodeError):
            return None
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
