from __future__ import annotations

import subprocess
import sys

import pytest

from rex.background.lock import AlreadyRunningError, SingleInstanceLock


def test_second_lock_is_rejected(tmp_path) -> None:
    lock_path = tmp_path / "runtime.lock"

    with SingleInstanceLock(lock_path):
        with pytest.raises(AlreadyRunningError):
            with SingleInstanceLock(lock_path):
                raise AssertionError("a second live lock acquisition must be rejected")


def test_lock_releases_after_context_exit(tmp_path) -> None:
    lock_path = tmp_path / "runtime.lock"

    with SingleInstanceLock(lock_path):
        pass

    with SingleInstanceLock(lock_path):
        pass


def test_lock_releases_after_process_exit(tmp_path) -> None:
    lock_path = tmp_path / "runtime.lock"
    child_code = f"""
from pathlib import Path
from rex.background.lock import SingleInstanceLock

path = Path({str(lock_path)!r})
with SingleInstanceLock(path):
    pass
"""

    completed = subprocess.run(
        [sys.executable, "-c", child_code],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr

    with SingleInstanceLock(lock_path):
        pass


def test_lock_creates_only_parent_directory_and_lock_file(tmp_path) -> None:
    lock_path = tmp_path / "nested" / "runtime.lock"

    with SingleInstanceLock(lock_path):
        assert lock_path.parent.is_dir()
        assert lock_path.exists()
