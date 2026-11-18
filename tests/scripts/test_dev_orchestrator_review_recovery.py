from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from scripts.dev_orchestrator import cli, review_recovery, runner
from scripts.dev_orchestrator.evidence import EvidenceError, ReviewEvidenceBundle
from scripts.dev_orchestrator.handoff import repo_snapshot
from scripts.dev_orchestrator.storage import AtomicJsonStore
from scripts.dev_orchestrator.supervisor import Supervisor
from scripts.dev_orchestrator.types import WorkerStatus
from tests.scripts.test_dev_orchestrator_postprocessing import (
    postprocess_checkpoint as postprocess_checkpoint,
)


def _write(path, value):
    AtomicJsonStore(path).write(value)


def _change(path, mutate):
    value = json.loads(path.read_bytes())
    mutate(value)
    _write(path, value)


def _authority(root):
    # Existing OS locks intentionally refresh diagnostic PID bytes. They are
    # synchronization, not checkpoint, policy, receipt, or ledger state.
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and path.suffix != ".lock"
    }


@pytest.fixture
def held_review(postprocess_checkpoint, monkeypatch, request):
    checkpoint = postprocess_checkpoint
    checkpoint.write_draft = False
    original = checkpoint.supervisor.load_state("backend")
    if getattr(request, "param", "readonly") == "changed":
        checkpoint.write_draft = True
        checkpoint.draft = "Verified field: value\n"
        original = replace(
            original, task=replace(original.task, prompt="Implement the bounded field contract.")
        )
    checkpoint.supervisor.save_state(replace(original, iteration=12, review_failures=7))
    checkpoint.supervisor.run_role("backend")
    ready = checkpoint.supervisor.load_state("backend")
    assert ready.status is WorkerStatus.REVIEWING, ready.blocked_reason
    assert ready.task.feedback == "" and original.task.feedback
    checkpoint.head, dirty = repo_snapshot(checkpoint.repo)
    assert not dirty

    def incomplete(*_args, **_kwargs):
        raise EvidenceError("review-critical evidence truncated: task_diff")

    with monkeypatch.context() as blocked:
        blocked.setattr(runner, "build_review_evidence", incomplete)
        checkpoint.supervisor.run_role("backend")
    held = checkpoint.supervisor.load_state("backend")
    assert held.status is WorkerStatus.BLOCKED_SYSTEM, held.blocked_reason
    assert held.blocker_kind == "incomplete_evidence"
    assert held.iteration == 13 and held.review_failures == 7
    assert len(checkpoint.calls) == 1
    checkpoint.config = replace(checkpoint.config, observe_only=True)
    cli.save_config(checkpoint.config)
    checkpoint.root = checkpoint.config.coordination_root
    checkpoint.state_path = checkpoint.root / "state/backend.json"
    checkpoint.lease_path = checkpoint.root / "handoff/backend.json"
    checkpoint.result_dir = (
        checkpoint.root / "postprocessing-failures" / ("backend-" + held.last_result_invocation_id)
    )
    checkpoint.result_path = checkpoint.result_dir / "completed-result.json"
    checkpoint.input_dir = (
        checkpoint.root
        / "model-inputs"
        / ("backend-" + hashlib.sha256(held.last_result_invocation_id.encode()).hexdigest())
    )
    checkpoint.receipt_path = checkpoint.root / "validation/backend-B-AUDIT.json"
    checkpoint.checks_path = checkpoint.root / "evidence/supervisor-checks/backend-B-AUDIT.json"
    checkpoint.review_path = checkpoint.root / "phase-admissions/backend.json"
    _change(checkpoint.state_path, lambda state: state.update(future_extension={"keep": [1, 2]}))

    def forbidden(*_args, **_kwargs):
        raise AssertionError("recovery must not initialize or invoke a worker")

    monkeypatch.setattr(cli, "_build_active_invoker", forbidden)
    monkeypatch.setattr(Supervisor, "__init__", forbidden)
    monkeypatch.setattr(runner, "run_command", forbidden)
    return checkpoint


def _recover(checkpoint):
    return Supervisor.revalidate_review_evidence(checkpoint.config, "backend")


@pytest.mark.parametrize("held_review", ["readonly", "changed"], indirect=True)
def test_revalidation_restores_only_same_task_review_checkpoint(held_review):
    checkpoint = held_review
    original = _authority(checkpoint.root)
    record = json.loads(checkpoint.result_path.read_bytes())
    assert not Path(record["scratch_path"]).exists()
    before = json.loads(checkpoint.state_path.read_bytes())
    report = _recover(checkpoint)
    after = json.loads(checkpoint.state_path.read_bytes())
    assert after == {**before, "status": "reviewing", "blocked_reason": "", "blocker_kind": ""}
    assert after["task"]["feedback"] == ""
    assert after["iteration"] == 13 and after["review_failures"] == 7
    assert report["implementation_invocation_id"] == before["last_result_invocation_id"]
    assert report["base_head"] == before["task_base_head"]
    assert report["head"] == checkpoint.head
    assert (
        report["state_before_sha256"] == hashlib.sha256(original["state/backend.json"]).hexdigest()
    )
    assert (
        report["state_after_sha256"]
        == hashlib.sha256(checkpoint.state_path.read_bytes()).hexdigest()
    )
    assert report["evidence_chars"] > 0 and len(report["evidence_sha256"]) == 64
    expected = {**original, "state/backend.json": checkpoint.state_path.read_bytes()}
    assert _authority(checkpoint.root) == expected
    assert len(checkpoint.calls) == 1
    with pytest.raises(ValueError, match="not a regular"):
        _recover(checkpoint)
    assert _authority(checkpoint.root) == expected


def test_equivalent_absolute_receipt_directory_is_valid(held_review):
    checkpoint = held_review
    _change(
        checkpoint.receipt_path,
        lambda value: value["gates"][0].update(cwd=str(checkpoint.repo.resolve())),
    )
    _change(
        checkpoint.checks_path,
        lambda value: value.update(
            validation_sha256=hashlib.sha256(checkpoint.receipt_path.read_bytes()).hexdigest()
        ),
    )
    before = _authority(checkpoint.root)
    assert _recover(checkpoint)["status"] == "reviewing"
    assert _authority(checkpoint.root) == {
        **before,
        "state/backend.json": checkpoint.state_path.read_bytes(),
    }


def test_cli_revalidation_has_no_invoker_and_prints_proof(held_review, capsys):
    checkpoint = held_review
    args = [
        "revalidate-review-evidence",
        "--coordination-root",
        str(checkpoint.root),
        "--role",
        "backend",
    ]
    parsed = cli.build_parser().parse_args(args)
    assert parsed.command == "revalidate-review-evidence" and parsed.role == "backend"
    assert cli.main(args) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "reviewing"
    assert report["task_id"] == checkpoint.task.task_id
    assert len(checkpoint.calls) == 1


@pytest.mark.parametrize(
    "case",
    [
        "active_config",
        "stale_config",
        "wrong_status",
        "human",
        "resume_status",
        "final_task",
        "task_prompt",
        "task_base",
        "result_id",
        "counter",
        "active",
        "cloud",
        "pending",
        "lease",
        "dirty",
        "head",
        "result_missing",
        "result_outcome",
        "result_user",
        "result_exit",
        "stdout",
        "input",
        "original_admission",
        "review_admission",
        "receipt_head",
        "receipt_base",
        "empty_gates",
        "gate_command",
        "gate_cwd",
        "escape_cwd",
        "capture",
        "script",
        "opening_head",
        "capture_lease",
        "capture_missing",
    ],
)
def test_revalidation_refuses_unproven_checkpoint_without_authority_writes(held_review, case):
    checkpoint = held_review
    state_changes = {
        "wrong_status": {"status": "implementing"},
        "human": {"status": "blocked_user", "blocker_kind": "human"},
        "resume_status": {"resume_status": "reviewing"},
        "task_base": {"task_base_head": "0" * 40},
        "result_id": {"last_result_invocation_id": "11111111-1111-4111-8111-111111111111"},
        "counter": {"review_failures": -1},
    }
    if case in state_changes:
        _change(checkpoint.state_path, lambda state: state.update(state_changes[case]))
    elif case in {"active_config", "stale_config"}:
        _change(
            checkpoint.root / "orchestrator-config.json",
            lambda config: config.update(observe_only=False),
        )
        if case == "active_config":
            checkpoint.config = replace(checkpoint.config, observe_only=False)
    elif case == "final_task":
        _change(
            checkpoint.state_path,
            lambda state: state["task"].update(task_id="FINAL-VERIFY-backend"),
        )
    elif case == "task_prompt":
        _change(checkpoint.state_path, lambda state: state["task"].update(prompt="Different scope"))
    elif case in {"active", "cloud"}:
        directory = "active-agents" if case == "active" else "cloud-sessions"
        _write(checkpoint.root / directory / "backend.json", {"unresolved": True})
    elif case == "pending":
        _change(
            checkpoint.lease_path,
            lambda lease: lease.update(pending_result={"invocation_id": "unconsumed"}),
        )
    elif case == "lease":
        _change(checkpoint.lease_path, lambda lease: lease.update(lease_id="different-lease"))
    elif case == "dirty":
        (checkpoint.repo / "unowned.txt").write_text("external writer", encoding="utf-8")
    elif case == "head":
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "External checkpoint"],
            cwd=checkpoint.repo,
            check=True,
            capture_output=True,
        )
    elif case == "result_missing":
        checkpoint.result_path.unlink()
    elif case in {"result_outcome", "result_user"}:
        change = {"outcome": "continue"} if case == "result_outcome" else {"needs_user": True}
        _change(checkpoint.result_path, lambda value: value["result"].update(change))
    elif case == "result_exit":
        _change(
            checkpoint.result_path, lambda value: value["provenance"].update(process_exit_code=1)
        )
    elif case in {"stdout", "input"}:
        path = (
            checkpoint.result_dir / "model-stdout.log"
            if case == "stdout"
            else checkpoint.input_dir / "input.txt"
        )
        path.write_bytes(path.read_bytes() + b"changed")
    elif case == "original_admission":
        _change(
            checkpoint.input_dir / "manifest.json",
            lambda value: value["admission"].update(head="0" * 40),
        )
    elif case == "review_admission":
        _change(checkpoint.review_path, lambda value: value.update(phase="implement"))
    elif case in {
        "receipt_head",
        "receipt_base",
        "empty_gates",
        "gate_command",
        "gate_cwd",
        "escape_cwd",
    }:

        def invalid_receipt(value):
            if case == "receipt_head":
                value["head"] = "0" * 40
            elif case == "receipt_base":
                value["base_head"] = "0" * 40
            elif case == "empty_gates":
                value["gates"] = []
            elif case in {"gate_cwd", "escape_cwd"}:
                value["gates"][0]["cwd"] = "different" if case == "gate_cwd" else ".."
            else:
                value["gates"][0]["command"] = ["echo", "not the configured gate"]

        _change(checkpoint.receipt_path, invalid_receipt)
        # Bind the new hash to prove that gate identity, not merely the capture
        # digest, prevents a changed or empty receipt from granting recovery.
        _change(
            checkpoint.checks_path,
            lambda value: value.update(
                validation_sha256=hashlib.sha256(checkpoint.receipt_path.read_bytes()).hexdigest()
            ),
        )
    elif case == "capture":
        manifest = json.loads(checkpoint.checks_path.read_bytes())
        (checkpoint.root / manifest["final"]["stdout_path"]).write_bytes(b"changed")
    elif case == "script":
        (checkpoint.root / "scripts/check-coordination.ps1").write_text("changed", encoding="utf-8")
    elif case == "opening_head":
        _change(checkpoint.checks_path, lambda value: value["initial"].update(head="0" * 40))
    elif case == "capture_lease":
        _change(
            checkpoint.checks_path, lambda value: value["initial"]["lease"].update(lease_id="wrong")
        )
    elif case == "capture_missing":
        _change(checkpoint.checks_path, lambda value: value.update(initial=None))
    else:
        raise AssertionError(case)
    before = _authority(checkpoint.root)
    with pytest.raises((ValueError, RuntimeError, OSError)):
        _recover(checkpoint)
    assert _authority(checkpoint.root) == before
    assert len(checkpoint.calls) == 1


def test_complete_packet_is_required_before_state_can_change(held_review, monkeypatch):
    checkpoint = held_review
    before = _authority(checkpoint.root)
    monkeypatch.setattr(
        review_recovery,
        "build_review_evidence",
        lambda *_args, **_kwargs: ReviewEvidenceBundle(
            "partial", True, ("task_diff",), checkpoint.head, checkpoint.head
        ),
    )
    with pytest.raises(ValueError, match="current review evidence is incomplete"):
        _recover(checkpoint)
    assert _authority(checkpoint.root) == before


@pytest.mark.parametrize("changed", ["state", "config", "lease", "result", "active"])
def test_recovery_rechecks_exact_inputs_at_commit(held_review, monkeypatch, changed):
    checkpoint = held_review
    real_build = review_recovery.build_review_evidence
    expected = {}

    def racing_change(*args, **kwargs):
        bundle = real_build(*args, **kwargs)
        if changed == "state":
            _change(checkpoint.state_path, lambda value: value.update(review_failures=8))
        elif changed == "config":
            _change(
                checkpoint.root / "orchestrator-config.json",
                lambda value: value.update(observe_only=False),
            )
        elif changed == "lease":
            _change(checkpoint.lease_path, lambda value: value.update(lease_id="new-owner"))
        elif changed == "result":
            checkpoint.result_path.write_bytes(checkpoint.result_path.read_bytes() + b"\n")
        else:
            _write(checkpoint.root / "active-agents/backend.json", {"new": True})
        expected.update(_authority(checkpoint.root))
        return bundle

    monkeypatch.setattr(review_recovery, "build_review_evidence", racing_change)
    with pytest.raises(ValueError, match="evidence changed|unresolved activity"):
        _recover(checkpoint)
    assert _authority(checkpoint.root) == expected


def test_refused_state_write_preserves_checkpoint(held_review, monkeypatch):
    checkpoint = held_review
    before = _authority(checkpoint.root)

    def fail_write(*_args, **_kwargs):
        raise PermissionError("fixture sharing violation")

    monkeypatch.setattr(AtomicJsonStore, "write", fail_write)
    with pytest.raises(PermissionError):
        _recover(checkpoint)
    assert _authority(checkpoint.root) == before
