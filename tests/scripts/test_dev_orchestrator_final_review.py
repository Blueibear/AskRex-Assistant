from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.dev_orchestrator import completion
from scripts.dev_orchestrator import supervisor as supervisor_module
from scripts.dev_orchestrator.cli import save_config, set_observe_only
from scripts.dev_orchestrator.completion import LocalGate
from scripts.dev_orchestrator.handoff import (
    acknowledge_handoff,
    advance_handoff,
    read_pending_result,
)
from scripts.dev_orchestrator.routing import SOL_MODEL
from scripts.dev_orchestrator.runner import CliAgentInvoker, ProcessResult
from scripts.dev_orchestrator.supervisor import Supervisor
from scripts.dev_orchestrator.types import AgentResult, WorkerState, WorkerStatus
from scripts.dev_orchestrator.validation import load_validation_receipt
from tests.scripts.test_dev_orchestrator_completion import _accept, _config, _success_checks


@pytest.fixture
def final_checkpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ASKREX_PROVIDER_POLICY", "subscription_only")
    config = replace(_config(tmp_path), metadata={"iteration_validation": {"enabled": False}})
    save_config(config)
    _accept(config, "backend")
    root = config.coordination_root
    (root / "scripts").mkdir()
    (root / "scripts/check-coordination.ps1").write_text(
        "param([string]$Role)\nWrite-Output $Role\n", encoding="utf-8"
    )
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=config.backend_root, text=True
    ).strip()
    branch = subprocess.check_output(
        ["git", "branch", "--show-current"], cwd=config.backend_root, text=True
    ).strip()
    command = (sys.executable, "-c", "print('FINAL-GATE-PASSED')")
    manifest = replace(
        completion.COMPLETION_MANIFESTS["backend"],
        local_gates=(LocalGate("fixture final gate", command),),
    )
    monkeypatch.setitem(completion.COMPLETION_MANIFESTS, "backend", manifest)
    pr = {
        "state": "OPEN",
        "headRefOid": head,
        "headRefName": branch,
        "baseRefName": manifest.base_branch,
        "statusCheckRollup": _success_checks("backend"),
    }
    real_query = completion._run

    def query(repo, *args):
        if args[:3] == ("gh", "pr", "view"):
            return subprocess.CompletedProcess(args, 0, json.dumps(pr), "")
        return real_query(repo, *args)

    monkeypatch.setattr(completion, "_run", query)
    monkeypatch.setattr(completion, "_ci_workflow_present", lambda _repo: True)
    real_which = completion.shutil.which
    monkeypatch.setattr(
        completion.shutil, "which", lambda name: "gh" if name == "gh" else real_which(name)
    )
    observed = SimpleNamespace(
        config=config,
        head=head,
        pr=pr,
        local_runs=0,
        prompts=[],
        outcome="pass",
        callback=None,
        crash_after_review=False,
    )
    real_run = subprocess.run

    def run(arguments, **kwargs):
        if arguments[0] == "powershell.exe":
            return subprocess.CompletedProcess(arguments, 0, b"FINAL-HOST-CHECK\n", b"")
        if tuple(arguments) == command:
            observed.local_runs += 1
        return real_run(arguments, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    invoker = object.__new__(CliAgentInvoker)
    invoker.config = config

    def execute(arguments, repo, role, provider, invocation_id, **kwargs):
        assert provider == "codex"
        assert SOL_MODEL in arguments
        assert kwargs == {"phase": "review", "task_id": "FINAL-VERIFY-backend"}
        observed.prompts.append(arguments[-1])
        result = AgentResult(
            observed.outcome,
            "Final review fixture",
            "Resolve final review finding",
            role=role,
            task_id=kwargs["task_id"],
            invocation_id=invocation_id,
        )
        pending = {
            "phase": "review",
            "role": role,
            "task_id": result.task_id,
            "invocation_id": invocation_id,
            "pre_head": head,
            "post_head": head,
            "result": asdict(result),
        }
        advance_handoff(
            config,
            role,
            pre_head=head,
            post_head=head,
            invocation_id=invocation_id,
            provider="codex",
            pending_result=pending,
        )
        if observed.callback:
            observed.callback()
        if observed.crash_after_review:
            raise RuntimeError("simulated crash after durable final review")
        return ProcessResult(0, json.dumps(asdict(result)), "")

    monkeypatch.setattr(invoker, "_execute_agent", execute)
    observed.invoker = invoker
    observed.supervisor = Supervisor(config, invoker)
    return observed


def _claim(checkpoint):
    checkpoint.supervisor._done_claim(
        "backend", WorkerState("backend", status=WorkerStatus.PLANNING), "final context"
    )


def test_gate_ready_done_claim_dispatches_complete_final_codex_review(final_checkpoint) -> None:
    checkpoint = final_checkpoint
    _claim(checkpoint)

    state = checkpoint.supervisor.load_state("backend")
    assert state.status is WorkerStatus.DONE
    assert len(checkpoint.prompts) == 1
    assert checkpoint.local_runs == 1
    prompt = checkpoint.prompts[0]
    for text in (
        "FINAL-VERIFY-backend",
        checkpoint.head,
        "FINAL-GATE-PASSED",
        "FINAL-HOST-CHECK",
        "docs match current behavior",
        "hardware path passed",
        "peer contract acknowledged",
        "Lint & Format Check",
        "SUCCESS",
        "no registered competing writer",
    ):
        assert text in prompt
    receipt = load_validation_receipt(
        checkpoint.config,
        "backend",
        "FINAL-VERIFY-backend",
        base_head=checkpoint.head,
        head=checkpoint.head,
    )
    assert [gate.name for gate in receipt.gates] == ["fixture final gate"]
    assert read_pending_result(checkpoint.config, "backend") is None


@pytest.mark.parametrize("outcome", ["changes_required", "failed"])
def test_final_review_rejection_never_marks_done(final_checkpoint, outcome) -> None:
    checkpoint = final_checkpoint
    checkpoint.outcome = outcome
    _claim(checkpoint)

    state = checkpoint.supervisor.load_state("backend")
    assert len(checkpoint.prompts) == 1
    assert state.status is not WorkerStatus.DONE
    if outcome == "changes_required":
        assert state.status is WorkerStatus.IMPLEMENTING
        assert state.task.task_id == "FINAL-backend"
    else:
        assert state.status is WorkerStatus.BLOCKED_SYSTEM
        assert state.task.task_id == "FINAL-VERIFY-backend"


@pytest.mark.parametrize("missing", ["receipt", "capture"])
def test_final_review_missing_evidence_stops_before_dispatch(
    final_checkpoint, monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    checkpoint = final_checkpoint
    real_review = checkpoint.invoker.review

    def review_with_missing_evidence(*args):
        root = checkpoint.config.coordination_root
        if missing == "receipt":
            path = root / "validation/backend-FINAL-VERIFY-backend.json"
        else:
            manifest = json.loads(
                (root / "evidence/supervisor-checks/backend-FINAL-VERIFY-backend.json").read_text()
            )
            path = root / manifest["final"]["stdout_path"]
        path.unlink()
        return real_review(*args)

    monkeypatch.setattr(checkpoint.invoker, "review", review_with_missing_evidence)
    _claim(checkpoint)

    state = checkpoint.supervisor.load_state("backend")
    assert checkpoint.prompts == []
    assert state.status is WorkerStatus.BLOCKED_SYSTEM
    assert state.blocker_kind == "incomplete_evidence"
    assert state.task.task_id == "FINAL-VERIFY-backend"


def test_paused_final_review_resumes_without_implementation_or_revalidation(
    final_checkpoint, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkpoint = final_checkpoint
    real_finish = supervisor_module.finish_checks

    def finish_then_pause(*args, **kwargs):
        real_finish(*args, **kwargs)
        set_observe_only(checkpoint.config.coordination_root, True)

    monkeypatch.setattr(supervisor_module, "finish_checks", finish_then_pause)
    _claim(checkpoint)
    parked = checkpoint.supervisor.load_state("backend")
    assert parked.status is WorkerStatus.REVIEWING
    assert parked.task.task_id == "FINAL-VERIFY-backend"
    assert parked.task_base_head == checkpoint.head
    assert checkpoint.prompts == []
    receipt = checkpoint.config.coordination_root / "validation/backend-FINAL-VERIFY-backend.json"
    original_receipt = receipt.read_bytes()

    set_observe_only(checkpoint.config.coordination_root, False)
    resumed = Supervisor(checkpoint.config, checkpoint.invoker)
    resumed._run_role("backend")

    assert resumed.load_state("backend").status is WorkerStatus.DONE
    assert checkpoint.local_runs == 1
    assert len(checkpoint.prompts) == 1
    assert receipt.read_bytes() == original_receipt


def test_durable_final_review_pass_recovers_without_another_reviewer(final_checkpoint) -> None:
    checkpoint = final_checkpoint
    checkpoint.crash_after_review = True
    with pytest.raises(RuntimeError, match="simulated crash"):
        _claim(checkpoint)

    parked = checkpoint.supervisor.load_state("backend")
    assert parked.status is WorkerStatus.REVIEWING
    assert parked.task.task_id == "FINAL-VERIFY-backend"
    assert read_pending_result(checkpoint.config, "backend") is not None

    resumed = Supervisor(checkpoint.config, checkpoint.invoker)
    resumed._run_role("backend")

    assert resumed.load_state("backend").status is WorkerStatus.DONE
    assert len(checkpoint.prompts) == 1
    assert checkpoint.local_runs == 1
    assert read_pending_result(checkpoint.config, "backend") is None


@pytest.mark.parametrize("changed_gate", ["ci", "acceptance"])
def test_final_pass_rechecks_current_completion_requirements(
    final_checkpoint, changed_gate: str
) -> None:
    checkpoint = final_checkpoint

    def invalidate_gate():
        if changed_gate == "ci":
            checkpoint.pr["statusCheckRollup"][0]["conclusion"] = "FAILURE"
        else:
            (checkpoint.config.coordination_root / "completion/backend-acceptance.json").unlink()

    checkpoint.callback = invalidate_gate
    _claim(checkpoint)

    state = checkpoint.supervisor.load_state("backend")
    assert len(checkpoint.prompts) == 1
    assert checkpoint.local_runs == 1
    assert state.status is not WorkerStatus.DONE
    assert state.status is (
        WorkerStatus.IMPLEMENTING if changed_gate == "ci" else WorkerStatus.BLOCKED_USER
    )


@pytest.mark.parametrize("changed", ["local_gate", "required_ci"])
def test_final_review_resume_rejects_a_changed_completion_manifest(
    final_checkpoint, monkeypatch: pytest.MonkeyPatch, changed: str
) -> None:
    checkpoint = final_checkpoint
    real_finish = supervisor_module.finish_checks

    def finish_then_pause(*args, **kwargs):
        real_finish(*args, **kwargs)
        set_observe_only(checkpoint.config.coordination_root, True)

    monkeypatch.setattr(supervisor_module, "finish_checks", finish_then_pause)
    _claim(checkpoint)
    manifest = completion.COMPLETION_MANIFESTS["backend"]
    if changed == "local_gate":
        manifest = replace(
            manifest,
            local_gates=(LocalGate("new mandatory gate", (sys.executable, "-c", "print('NEW')")),),
        )
    else:
        manifest = replace(
            manifest, required_checks=(*manifest.required_checks, "New mandatory CI")
        )
    monkeypatch.setitem(completion.COMPLETION_MANIFESTS, "backend", manifest)
    set_observe_only(checkpoint.config.coordination_root, False)

    resumed = Supervisor(checkpoint.config, checkpoint.invoker)
    resumed._run_role("backend")

    state = resumed.load_state("backend")
    assert state.status is WorkerStatus.BLOCKED_SYSTEM
    assert state.blocker_kind == "incomplete_evidence"
    assert checkpoint.prompts == []
    assert checkpoint.local_runs == 1


def test_final_review_cannot_complete_a_different_newly_accepted_head(final_checkpoint) -> None:
    checkpoint = final_checkpoint

    def advance_reviewed_revision():
        repo = checkpoint.config.backend_root
        (repo / "README.md").write_text("new revision after review\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "new revision"], cwd=repo, check=True)
        new_head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo, text=True
        ).strip()
        acknowledge_handoff(checkpoint.config, "backend", source="test")
        _accept(checkpoint.config, "backend")
        checkpoint.pr["headRefOid"] = new_head

    checkpoint.callback = advance_reviewed_revision
    _claim(checkpoint)

    state = checkpoint.supervisor.load_state("backend")
    assert len(checkpoint.prompts) == 1
    assert checkpoint.local_runs == 1
    assert state.status is WorkerStatus.BLOCKED_SYSTEM
    assert state.blocker_kind == "incomplete_evidence"
    assert "revision changed" in state.blocked_reason
