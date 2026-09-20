import json
import os
import threading
import time
from pathlib import Path
from typing import Any


def _replace_file_windows(source: Path, destination: Path) -> bool:
    """Atomically replace an existing file with Windows ReplaceFileW."""
    if os.name != "nt" or not destination.exists():
        return False

    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    replace_file = kernel32.ReplaceFileW
    replace_file.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    replace_file.restype = ctypes.c_int
    return bool(replace_file(str(destination), str(source), None, 0, None, None))


class AtomicJsonStore:
    """Small atomic JSON file store for durable supervisor state."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def read(self, default: Any = None) -> Any:
        if not self.path.exists():
            return default
        with self.path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def write(self, value: Any) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(
            f".{self.path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(value, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            self._replace_with_retry(temporary)
        finally:
            temporary.unlink(missing_ok=True)

    def _replace_with_retry(self, temporary: Path) -> None:
        for attempt in range(5):
            try:
                os.replace(temporary, self.path)
                return
            except PermissionError:
                if attempt == 4:
                    if _replace_file_windows(temporary, self.path):
                        return
                    raise
                time.sleep(0.01 * (attempt + 1))


def atomic_write_text(path: Path, text: str) -> None:
    """Atomically replace a UTF-8 text file with Windows sharing-violation retry."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.text.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        for attempt in range(5):
            try:
                os.replace(temporary, path)
                return
            except PermissionError:
                if attempt == 4:
                    if _replace_file_windows(temporary, path):
                        return
                    raise
                time.sleep(0.01 * (attempt + 1))
    finally:
        temporary.unlink(missing_ok=True)
