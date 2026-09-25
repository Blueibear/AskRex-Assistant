from __future__ import annotations

import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from scripts.dev_orchestrator.evidence import EvidenceError, _git, build_review_evidence
from scripts.dev_orchestrator.types import OrchestratorConfig, TaskItem, WorkerState
from scripts.dev_orchestrator.validation import (
    load_validation_receipt,
    run_iteration_validation,
)


def test_git_evidence_forces_utf8_decoding(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_check_output(command, **kwargs):
        captured.update(kwargs)
        return "diff with unicode: → ✓"

    monkeypatch.setattr(subprocess, "check_output", fake_check_output)

    assert _git(tmp_path, "diff") == "diff with unicode: → ✓"
    assert captured["encoding"] == "utf-8"
    assert captured["errors"] == "replace"
    assert "text" not in captured


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


def test_review_evidence_includes_matching_canonical_issue_outside_coordination_budget(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "backend"
    base = _git_repo(repo)
    head = _commit(repo, "base\nchanged\n")
    config = _config(tmp_path, repo, [sys.executable, "-c", "print('OK')"])
    assert run_iteration_validation(
        config,
        "backend",
        "backend-test-004-lm-studio-model-discovery",
        base_head=base,
        head=head,
    ).passed

    issue = config.coordination_root / "issues" / "TEST-004.md"
    issue.parent.mkdir(parents=True, exist_ok=True)
    issue.write_text(
        "# TEST-004\n\nStatus: fixed-needs-retest\nOwner: backend\n",
        encoding="utf-8",
    )
    state = WorkerState(
        role="backend",
        task=TaskItem(
            "backend-test-004-lm-studio-model-discovery",
            "Fix TEST-004 model discovery",
        ),
        task_base_head=base,
    )
    noisy_coordination = "COORD-START\n" + ("C" * 20_000) + "\nCOORD-END\n"

    bundle = build_review_evidence(
        config,
        "backend",
        state,
        state.task,
        noisy_coordination,
        "inv-task-issue",
    )

    assert "## task_issues" in bundle.text
    assert "### issues/TEST-004.md" in bundle.text
    assert "Status: fixed-needs-retest" in bundle.text
    assert "Owner: backend" in bundle.text
    assert "[truncated coordination]" in bundle.text
    assert bundle.truncated is False


def test_review_evidence_includes_task_relevant_outgoing_coordination(tmp_path: Path) -> None:
    repo = tmp_path / "backend"
    base = _git_repo(repo)
    head = _commit(repo, "base\nchanged\n")
    config = _config(tmp_path, repo, [sys.executable, "-c", "print('OK')"])
    assert run_iteration_validation(config, "backend", "B-COORD", base_head=base, head=head).passed
    mailbox = config.coordination_root / "mailbox" / "testing"
    mailbox.mkdir(parents=True)
    message = mailbox / "MSG-task-retest-00-backend.md"
    message.write_text(
        "# AskRex Coordination Message\n\n"
        "From: backend\n"
        "To: testing\n"
        "Priority: high\n"
        "Related: B-COORD\n"
        "Needs response: no\n\n"
        "## Message\n"
        "Please retest the bounded B-COORD behavior after supervisor validation.\n",
        encoding="utf-8",
    )
    state = WorkerState(
        role="backend",
        task=TaskItem("B-COORD", "review"),
        task_base_head=base,
    )

    bundle = build_review_evidence(
        config,
        "backend",
        state,
        state.task,
        "coordination snapshot",
        "inv-coord",
    )

    assert "## outgoing_coordination" in bundle.text
    assert "MSG-task-retest-00-backend.md" in bundle.text
    assert "Please retest the bounded B-COORD behavior" in bundle.text
    assert bundle.truncated is False


def test_review_evidence_includes_supervisor_validated_coordination_artifact(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "backend"
    base = _git_repo(repo)
    head = _commit(repo, "base\nchanged\n")
    base_config = _config(tmp_path, repo, [sys.executable, "-c", "print('placeholder')"])

    artifact = (
        base_config.coordination_root
        / "mailbox"
        / "testing"
        / "MSG-validated-artifact-00-backend.md"
    )
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(
        "# AskRex Coordination Message\n\n"
        "From: backend\n"
        "To: testing\n"
        "Related: TEST-005\n\n"
        "## Message\n"
        "VALIDATED-ARTIFACT-CONTENT\n",
        encoding="utf-8",
    )
    config = replace(
        base_config,
        metadata={
            "iteration_validation": {
                "enabled": True,
                "backend": {
                    "gates": [
                        {
                            "name": "artifact proof",
                            "command": [
                                sys.executable,
                                "-c",
                                f"print({str(artifact)!r})",
                            ],
                        }
                    ]
                },
            }
        },
    )
    assert run_iteration_validation(
        config, "backend", "TEST-005-CLOSEOUT", base_head=base, head=head
    ).passed
    state = WorkerState(
        role="backend",
        task=TaskItem("TEST-005-CLOSEOUT", "coordination closeout"),
        task_base_head=base,
    )

    bundle = build_review_evidence(
        config,
        "backend",
        state,
        state.task,
        "coordination",
        "inv-validated-artifact",
    )

    assert "## validated_artifacts" in bundle.text
    assert "mailbox/testing/MSG-validated-artifact-00-backend.md" in bundle.text
    assert "VALIDATED-ARTIFACT-CONTENT" in bundle.text
    assert bundle.truncated is False


def test_review_evidence_does_not_follow_validated_gate_path_outside_coordination_roots(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "backend"
    base = _git_repo(repo)
    head = _commit(repo, "base\nchanged\n")
    base_config = _config(tmp_path, repo, [sys.executable, "-c", "print('placeholder')"])

    outside = tmp_path / "outside-secret.txt"
    outside.write_text("OUTSIDE-SECRET-CONTENT\n", encoding="utf-8")
    config = replace(
        base_config,
        metadata={
            "iteration_validation": {
                "enabled": True,
                "backend": {
                    "gates": [
                        {
                            "name": "outside path",
                            "command": [
                                sys.executable,
                                "-c",
                                f"print({str(outside)!r})",
                            ],
                        }
                    ]
                },
            }
        },
    )
    assert run_iteration_validation(
        config, "backend", "B-OUTSIDE", base_head=base, head=head
    ).passed
    state = WorkerState(
        role="backend",
        task=TaskItem("B-OUTSIDE", "review"),
        task_base_head=base,
    )

    bundle = build_review_evidence(
        config,
        "backend",
        state,
        state.task,
        "coordination",
        "inv-outside",
    )

    assert "OUTSIDE-SECRET-CONTENT" not in bundle.text
    assert "No validated coordination artifacts were referenced" in bundle.text


def test_truncated_outgoing_coordination_is_noncritical(tmp_path: Path) -> None:
    repo = tmp_path / "backend"
    base = _git_repo(repo)
    head = _commit(repo, "base\nchanged\n")
    config = _config(tmp_path, repo, [sys.executable, "-c", "print('OK')"])
    assert run_iteration_validation(
        config, "backend", "B-LONG-COORD", base_head=base, head=head
    ).passed
    mailbox = config.coordination_root / "mailbox" / "testing"
    mailbox.mkdir(parents=True)
    for index in range(8):
        (mailbox / f"MSG-long-{index:02d}-00-backend.md").write_text(
            "# AskRex Coordination Message\n\n"
            "From: backend\n"
            "To: testing\n"
            "Priority: normal\n"
            "Related: B-LONG-COORD\n"
            "Needs response: no\n\n"
            "## Message\n" + ("X" * 4000) + "\n",
            encoding="utf-8",
        )
    state = WorkerState(
        role="backend",
        task=TaskItem("B-LONG-COORD", "review"),
        task_base_head=base,
    )

    bundle = build_review_evidence(
        config,
        "backend",
        state,
        state.task,
        "coordination",
        "inv-long-coord",
    )

    assert "[truncated outgoing_coordination]" in bundle.text
    assert bundle.truncated is False
    assert "outgoing_coordination" not in bundle.truncation_reasons


def test_review_evidence_keeps_complete_task_diff_with_bounded_context(tmp_path: Path) -> None:
    repo = tmp_path / "backend"
    base = _git_repo(repo)
    payload = "START-LARGE-DIFF\n" + ("D" * 20_000) + "\nEND-LARGE-DIFF\n"
    (repo / "large.txt").write_text(payload, encoding="utf-8")
    subprocess.run(["git", "add", "large.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "large diff"], cwd=repo, check=True)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    config = _config(tmp_path, repo, [sys.executable, "-c", "print('OK')"])
    assert run_iteration_validation(
        config, "backend", "B-COMPLETE-DIFF", base_head=base, head=head
    ).passed
    state = WorkerState(
        role="backend",
        task=TaskItem("B-COMPLETE-DIFF", "review"),
        task_base_head=base,
    )
    coordination = "COORD-START\n" + ("C" * 20_000) + "\nCOORD-END\n"

    bundle = build_review_evidence(
        config,
        "backend",
        state,
        state.task,
        coordination,
        "inv-complete",
    )

    assert "START-LARGE-DIFF" in bundle.text
    assert "END-LARGE-DIFF" in bundle.text
    assert "[truncated task_diff]" not in bundle.text
    assert "[truncated coordination]" in bundle.text
    assert bundle.truncated is False
    assert len(bundle.text) <= config.openai_max_input_chars


def test_review_evidence_includes_current_changed_file_contents_for_small_diff(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "backend"
    base = _git_repo(repo)
    tests_dir = repo / "tests"
    tests_dir.mkdir()
    regression = tests_dir / "test_regression.py"
    regression.write_text(
        "def test_existing_context():\n"
        "    marker = 'FULL-CURRENT-CONTEXT'\n"
        "    assert marker\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "tests/test_regression.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "add regression"], cwd=repo, check=True)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    config = _config(tmp_path, repo, [sys.executable, "-c", "print('OK')"])
    assert run_iteration_validation(
        config,
        "backend",
        "B-CURRENT-CONTEXT",
        base_head=base,
        head=head,
    ).passed
    state = WorkerState(
        role="backend",
        task=TaskItem("B-CURRENT-CONTEXT", "review"),
        task_base_head=base,
    )

    bundle = build_review_evidence(
        config,
        "backend",
        state,
        state.task,
        "coordination",
        "inv-current-context",
    )

    assert "## changed_file_contents" in bundle.text
    assert "### tests/test_regression.py" in bundle.text
    assert "FULL-CURRENT-CONTEXT" in bundle.text
    assert bundle.truncated is False


def test_large_changed_file_snapshot_is_noncritical_when_task_diff_is_complete(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "backend"
    _git_repo(repo)
    large = repo / "large_source.py"
    large.write_text(
        "HEADER = 'BASE'\n" + ("X = 'context'\n" * 4000),
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "large_source.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "large source"], cwd=repo, check=True)
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()

    source = large.read_text(encoding="utf-8")
    large.write_text(
        source.replace("HEADER = 'BASE'", "HEADER = 'CHANGED'", 1),
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "large_source.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "small change"], cwd=repo, check=True)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()

    config = _config(tmp_path, repo, [sys.executable, "-c", "print('OK')"])
    assert run_iteration_validation(
        config,
        "backend",
        "B-LARGE-CURRENT-CONTEXT",
        base_head=base,
        head=head,
    ).passed
    state = WorkerState(
        role="backend",
        task=TaskItem("B-LARGE-CURRENT-CONTEXT", "review"),
        task_base_head=base,
    )

    bundle = build_review_evidence(
        config,
        "backend",
        state,
        state.task,
        "coordination",
        "inv-large-current-context",
    )

    assert "HEADER = 'CHANGED'" in bundle.text
    assert "[truncated changed_file_contents]" in bundle.text
    assert "[truncated task_diff]" not in bundle.text
    assert bundle.truncated is False
    assert "changed_file_contents" not in bundle.truncation_reasons


def test_truncated_diff_stat_is_noncritical_when_task_diff_is_complete(tmp_path: Path) -> None:
    repo = tmp_path / "backend"
    base = _git_repo(repo)
    for index in range(180):
        path = repo / f"generated-file-with-a-long-name-{index:03d}.txt"
        path.write_text(f"value-{index}\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "many files"], cwd=repo, check=True)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    config = _config(tmp_path, repo, [sys.executable, "-c", "print('OK')"])
    assert run_iteration_validation(
        config, "backend", "B-DIFF-STAT", base_head=base, head=head
    ).passed
    state = WorkerState(
        role="backend",
        task=TaskItem("B-DIFF-STAT", "review"),
        task_base_head=base,
    )

    bundle = build_review_evidence(
        config,
        "backend",
        state,
        state.task,
        "coordination",
        "inv-diff-stat",
    )

    assert "[truncated diff_stat]" in bundle.text
    assert "[truncated task_diff]" not in bundle.text
    assert "generated-file-with-a-long-name-179.txt" in bundle.text
    assert bundle.truncated is False
    assert "diff_stat" not in bundle.truncation_reasons


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


def test_verbose_validation_output_does_not_critically_truncate_review_bundle(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "backend"
    base = _git_repo(repo)
    head = _commit(repo, "base\nchanged\n")
    noisy = "print('START-VALIDATION'); " "print('X' * 40000); " "print('112 passed');"
    config = _config(tmp_path, repo, [sys.executable, "-c", noisy])
    assert run_iteration_validation(config, "backend", "B-NOISY", base_head=base, head=head).passed
    state = WorkerState(
        role="backend",
        task=TaskItem("B-NOISY", "review"),
        task_base_head=base,
    )

    bundle = build_review_evidence(
        config,
        "backend",
        state,
        state.task,
        "coordination",
        "inv-noisy",
    )

    assert bundle.truncated is False
    assert "validation" not in bundle.truncation_reasons
    assert "gate: focused" in bundle.text
    assert "exit_code: 0" in bundle.text
    assert "START-VALIDATION" in bundle.text
    assert "112 passed" in bundle.text
    assert "[truncated gate_stdout]" in bundle.text


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
