import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.dev_orchestrator import runner
from tests.scripts.test_dev_orchestrator_safety import _git_repo


def test_non_repository_root_does_not_seal_ancestor_git_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ancestor = tmp_path / "ancestor"
    ancestor.mkdir()
    subprocess.run(["git", "init", "-q", str(ancestor)], check=True)
    frozen = ancestor / "frozen"
    frozen.mkdir()
    visited = []

    def capture_tree(root):
        visited.append(root)
        return ()

    monkeypatch.setattr(runner, "_tree_state", capture_tree)
    config = SimpleNamespace(backend_root=None, mobile_root=None, frozen_worktree=frozen)
    runner._custom_executor_repository_state(config)

    assert visited == [frozen.resolve()]


def test_linked_worktree_keeps_own_and_shared_git_metadata_sealed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "backend"
    _git_repo(repo)
    visited = []

    def capture_tree(root):
        visited.append(root)
        return ()

    monkeypatch.setattr(runner, "_tree_state", capture_tree)
    config = SimpleNamespace(backend_root=repo, mobile_root=None, frozen_worktree=None)
    runner._custom_executor_repository_state(config)

    metadata = repo.with_name("backend-source") / ".git"
    assert set(visited) == {
        repo.resolve(),
        metadata.resolve(),
        (metadata / "worktrees" / "backend").resolve(),
    }
