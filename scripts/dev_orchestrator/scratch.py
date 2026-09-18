from __future__ import annotations

import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from .handoff import HandoffRequired


def _remove_scratch_tree(path: Path, *, attempts: int = 60, delay_seconds: float = 0.25) -> None:
    for attempt in range(attempts):
        try:
            tempfile.TemporaryDirectory._rmtree(str(path), ignore_errors=False)
            return
        except FileNotFoundError:
            return
        except PermissionError as exc:
            if getattr(exc, "winerror", None) != 32:
                raise
            if attempt + 1 >= attempts:
                return
            time.sleep(delay_seconds)


@contextmanager
def _temporary_scratch_root(prefix: str, *, parent: Path | None = None):
    if parent is not None:
        parent = parent.resolve()
        parent.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix=prefix, dir=parent))
    cleanup = {"remove": True}
    try:
        yield root, cleanup
    finally:
        if cleanup["remove"]:
            _remove_scratch_tree(root)


def _clone_scratch_repo(repo: Path, pre_head: str, scratch: Path) -> None:
    cloned = subprocess.run(
        [
            "git",
            "clone",
            "--quiet",
            "--no-hardlinks",
            "--no-checkout",
            str(repo),
            str(scratch),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if cloned.returncode != 0:
        raise HandoffRequired(f"cannot create Claude scratch clone: {cloned.stderr.strip()}")
    checked = subprocess.run(
        ["git", "checkout", "--quiet", "--detach", pre_head],
        cwd=scratch,
        capture_output=True,
        text=True,
        check=False,
    )
    if checked.returncode != 0:
        raise HandoffRequired(
            f"cannot checkout leased HEAD in scratch clone: {checked.stderr.strip()}"
        )
