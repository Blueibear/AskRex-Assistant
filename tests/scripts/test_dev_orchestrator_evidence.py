from __future__ import annotations

import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from scripts.dev_orchestrator.evidence import EvidenceError, build_review_evidence
from scripts.dev_orchestrator.types import OrchestratorConfig, TaskItem, WorkerState
from scripts.dev_orchestrator.validation import (
    load_validation_receipt,
    run_iteration_validation,
)


def _git_repo(path: Path) -> str:
    path.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    (path / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=path, check=True)
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=path, text=True).strip()


def _config(tmp_path: Path, repo: Path, command: list[str]) -> OrchestratorConfig:
    root = tmp_path / "coordination"
    root.mkdir()
    mobile = tmp_path / "mobile"
    frozen = tmp_path / "frozen"
    mobile.mkdir()
    frozen.mkdir()
    return OrchestratorConfig(
        coordination_root=root,
        backend_root=repo,
        mobile_root=mobile,
        frozen_worktree=frozen,
        observe_only=False,
        metadata={
            "iteration_validation": {
                "enabled": True,
                "backend": {"gates": [{"name": "focused", "command": command}]},
            }
        },
    )


def _commit(path: Path, text: str) -> str:
    (path / "README.md").write_text(text, encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "change"], cwd=path, check=True)
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=path, text=True).strip()


def test_validation_receipt_persists_and_binds_revision(tmp_path: Path) -> None:
    repo = tmp_path / "backend"
    base = _git_repo(repo)
    head = _commit(repo, "base\nvalidated\n")
    config = _config(tmp_path, repo, [sys.executable, "-c", "print('VALIDATION-OK')"])

    report = run_iteration_validation(
        config,
        "backend",
        "B-EVIDENCE",
        base_head=base,
        head=head,
    )

    assert report.passed is True
    assert report.receipt is not None
    assert report.receipt.role == "backend"
    assert report.receipt.task_id == "B-EVIDENCE"
    assert report.receipt.base_head == base
    assert report.receipt.head == head
    assert report.receipt.gates[0].name == "focused"
    assert report.receipt.gates[0].exit_code == 0
    assert "VALIDATION-OK" in report.receipt.gates[0].stdout
    loaded = load_validation_receipt(config, "backend", "B-EVIDENCE", base_head=base, head=head)
    assert loaded == report.receipt
    with pytest.raises(ValueError, match="revision"):
        load_validation_receipt(config, "backend", "B-EVIDENCE", base_head=head, head=head)


def test_failed_revalidation_removes_stale_receipt(tmp_path: Path) -> None:
    repo = tmp_path / "backend"
    base = _git_repo(repo)
    head = _commit(repo, "base\nvalidated\n")
    passing = _config(tmp_path, repo, [sys.executable, "-c", "print('PASS')"])
    report = run_iteration_validation(
        passing,
        "backend",
        "B-STALE",
        base_head=base,
        head=head,
    )
    assert report.passed is True

    failing = replace(
        passing,
        metadata={
            "iteration_validation": {
                "enabled": True,
                "backend": {
                    "gates": [
                        {
                            "name": "focused",
                            "command": [sys.executable, "-c", "import sys; sys.exit(7)"],
                        }
                    ]
                },
            }
        },
    )
    failed = run_iteration_validation(
        failing,
        "backend",
        "B-STALE",
        base_head=base,
        head=head,
    )
    assert failed.passed is False
    with pytest.raises(FileNotFoundError):
        load_validation_receipt(failing, "backend", "B-STALE", base_head=base, head=head)


def test_review_evidence_is_revision_bound_and_secret_free(tmp_path: Path) -> None:
    repo = tmp_path / "backend"
    base = _git_repo(repo)
    (repo / "private.txt").write_text("PRIVATE-CONVERSATION\n", encoding="utf-8")
    subprocess.run(["git", "add", "private.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "private base"], cwd=repo, check=True)
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    (repo / "feature.txt").write_text("FEATURE-MARKER\n", encoding="utf-8")
    subprocess.run(["git", "add", "feature.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "feature"], cwd=repo, check=True)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    config = _config(tmp_path, repo, [sys.executable, "-c", "print('VALIDATION-OK')"])
    report = run_iteration_validation(config, "backend", "B-EVIDENCE", base_head=base, head=head)
    assert report.passed is True
    state = WorkerState(
        role="backend",
        task=TaskItem("B-EVIDENCE", "Review feature", "prior feedback"),
        task_base_head=base,
    )
    bundle = build_review_evidence(
        config,
        "backend",
        state,
        state.task,
        "coordination ok\nOPENAI_API_KEY=sk-supersecretvalue",
        "inv-123",
    )

    assert bundle.base_head == base
    assert bundle.head == head
    assert bundle.truncated is False
    assert "FEATURE-MARKER" in bundle.text
    assert "VALIDATION-OK" in bundle.text
    assert "Review feature" in bundle.text
    assert "prior feedback" in bundle.text
    assert "inv-123" in bundle.text
    assert "PRIVATE-CONVERSATION" not in bundle.text
    assert "sk-supersecretvalue" not in bundle.text
    assert str(config.frozen_worktree) not in bundle.text


def test_review_evidence_rejects_dirty_or_unvalidated_revision(tmp_path: Path) -> None:
    repo = tmp_path / "backend"
    base = _git_repo(repo)
    head = _commit(repo, "base\nchanged\n")
    config = _config(tmp_path, repo, [sys.executable, "-c", "pass"])
    state = WorkerState(role="backend", task=TaskItem("B-DIRTY", "review"), task_base_head=base)

    with pytest.raises(EvidenceError, match="receipt"):
        build_review_evidence(config, "backend", state, state.task, "coordination", "inv-1")

    assert run_iteration_validation(config, "backend", "B-DIRTY", base_head=base, head=head).passed
    (repo / "scratch.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(EvidenceError, match="dirty"):
        build_review_evidence(config, "backend", state, state.task, "coordination", "inv-2")


def test_review_evidence_marks_truncated_sections_explicitly(tmp_path: Path) -> None:
    repo = tmp_path / "backend"
    base = _git_repo(repo)
    (repo / "large.txt").write_text(
        "START-MARKER\n" + ("X" * 6000) + "\nEND-MARKER\n", encoding="utf-8"
    )
    subprocess.run(["git", "add", "large.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "large"], cwd=repo, check=True)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    config = _config(tmp_path, repo, [sys.executable, "-c", "print('OK')"])
    assert run_iteration_validation(config, "backend", "B-LARGE", base_head=base, head=head).passed
    state = WorkerState(role="backend", task=TaskItem("B-LARGE", "review"), task_base_head=base)

    bundle = build_review_evidence(
        config,
        "backend",
        state,
        state.task,
        "coordination",
        "inv-large",
        max_section_chars=500,
    )

    assert bundle.truncated is True
    assert "task_diff" in bundle.truncation_reasons
    assert "START-MARKER" in bundle.text
    assert "END-MARKER" in bundle.text
    assert "[truncated task_diff]" in bundle.text


def test_validation_receipt_rejects_tampered_success_evidence(tmp_path: Path) -> None:
    import json

    repo = tmp_path / "backend"
    base = _git_repo(repo)
    head = _commit(repo, "base\nvalidated\n")
    config = _config(tmp_path, repo, [sys.executable, "-c", "print('OK')"])
    assert run_iteration_validation(config, "backend", "B-TAMPER", base_head=base, head=head).passed
    path = config.coordination_root / "validation" / "backend-B-TAMPER.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["gates"][0]["exit_code"] = 1
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="successful"):
        load_validation_receipt(config, "backend", "B-TAMPER", base_head=base, head=head)


def test_validation_receipt_rejects_unsafe_task_id(tmp_path: Path) -> None:
    repo = tmp_path / "backend"
    _git_repo(repo)
    config = _config(tmp_path, repo, [sys.executable, "-c", "pass"])
    with pytest.raises(ValueError, match="safe"):
        run_iteration_validation(config, "backend", "../escape", base_head="x", head="")
