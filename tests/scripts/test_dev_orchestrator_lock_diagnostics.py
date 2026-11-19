import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.dev_orchestrator.lifecycle import SupervisorLock


@pytest.mark.parametrize("initial_diagnostic", [None, b"999999999999999\n" * 3])
def test_supervisor_lock_keeps_exact_pid_bytes_across_acquisitions(
    tmp_path: Path, initial_diagnostic: bytes | None
) -> None:
    lock_path = tmp_path / "supervisor.lock"
    if initial_diagnostic is not None:
        lock_path.write_bytes(initial_diagnostic)
    expected = f"{os.getpid()}\n".encode("ascii")
    lock = SupervisorLock(lock_path)

    for _ in range(4):
        with lock:
            pass
        assert lock_path.read_bytes() == expected


def test_supervisor_lock_cross_process_ignores_mutable_pid_contents(tmp_path: Path) -> None:
    lock_path = tmp_path / "supervisor.lock"
    first = SupervisorLock(lock_path)
    child = """from pathlib import Path
import os
import sys
from scripts.dev_orchestrator.lifecycle import SupervisorLock
lock = SupervisorLock(Path(sys.argv[1]))
try:
    lock.acquire()
except RuntimeError:
    raise SystemExit(23)
else:
    print(os.getpid())
    lock.release()
"""
    first.acquire()
    try:
        blocked = subprocess.run(
            [sys.executable, "-c", child, str(lock_path)],
            cwd=Path.cwd(),
            capture_output=True,
            text=True,
            check=False,
        )
        assert blocked.returncode == 23, blocked.stdout + blocked.stderr
    finally:
        first.release()

    assert lock_path.read_bytes() == f"{os.getpid()}\n".encode("ascii")

    acquired = subprocess.run(
        [sys.executable, "-c", child, str(lock_path)],
        cwd=Path.cwd(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert acquired.returncode == 0, acquired.stdout + acquired.stderr
    assert lock_path.read_bytes() == f"{int(acquired.stdout.strip())}\n".encode("ascii")
