from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from dataclasses import asdict, replace
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.dev_orchestrator import completion, coordination_checks
from scripts.dev_orchestrator import supervisor as supervisor_module
from scripts.dev_orchestrator.cli import save_config, set_observe_only
from scripts.dev_orchestrator.completion import LocalGate
from scripts.dev_orchestrator.handoff import (
    acknowledge_handoff,
    advance_handoff,
    read_pending_result,
)
from scripts.dev_orchestrator.model_blockers import capture_supplied_input
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
    failure_flag = tmp_path / "fail-final-gate"
    command = (
        sys.executable,
        "-c",
        "from pathlib import Path; print('FINAL-GATE-PASSED'); "
        f"raise SystemExit(1 if Path({str(failure_flag)!r}).exists() else 0)",
    )
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
        host_checks=0,
        failure_flag=failure_flag,
        prompts=[],
        outcome="pass",
        callback=None,
        crash_after_review=False,
    )
    real_run = subprocess.run

    def run(arguments, **kwargs):
        if arguments[0] == "powershell.exe":
            observed.host_checks += 1
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
        assert kwargs == {
            "phase": "review",
            "task_id": "FINAL-VERIFY-backend",
            "input_context": kwargs["input_context"],
        }
        assert isinstance(kwargs["input_context"], str)
        observed.prompts.append(arguments[-1])
        provenance = capture_supplied_input(
            config,
            role,
            "review",
            kwargs["task_id"],
            invocation_id,
            observed.head,
            kwargs["input_context"],
            arguments[-1],
            input_kind="cli_prompt_utf8",
        )
        assert provenance is not None
        result = AgentResult(
            observed.outcome,
            "Final review fixture",
            "Resolve final review finding",
            role=role,
            task_id=kwargs["task_id"],
            invocation_id=invocation_id,
            blocker_reason=(
                "Await the required independent evidence."
                if observed.outcome == "blocked_system"
                else ""
            ),
        )
        pending = {
            "phase": "review",
            "role": role,
            "task_id": result.task_id,
            "invocation_id": invocation_id,
            "pre_head": observed.head,
            "post_head": observed.head,
            "result": asdict(result),
            "input_provenance": provenance,
        }
        advance_handoff(
            config,
            role,
            pre_head=observed.head,
            post_head=observed.head,
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


def _preparation_artifacts(checkpoint):
    root = checkpoint.config.coordination_root
    return (
        root / "validation/backend-FINAL-VERIFY-backend.json",
        root / "evidence/supervisor-checks/backend-FINAL-VERIFY-backend.json",
    )


def _interrupt_preparation(checkpoint, monkeypatch, boundary):
    target = {
        "opening": "begin_checks",
        "receipt": "prepare_final_review",
        "closing": "finish_checks",
    }[boundary]
    original = getattr(supervisor_module, target)

    def interrupt(*args, **kwargs):
        result = original(*args, **kwargs)
        if boundary == "receipt":
            assert result.ready
        raise RuntimeError(f"interrupted after {boundary}")

    with monkeypatch.context() as patch:
        patch.setattr(supervisor_module, target, interrupt)
        with pytest.raises(RuntimeError, match=f"interrupted after {boundary}"):
            _claim(checkpoint)
    state = checkpoint.supervisor.load_state("backend")
    assert state.status is WorkerStatus.IMPLEMENTING
    assert state.task.task_id == "FINAL-VERIFY-backend"
    assert checkpoint.prompts == []


@pytest.mark.parametrize("boundary", ["receipt", "closing"])
def test_final_preparation_recovers_durable_success_without_repeating_gates(
    final_checkpoint, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    checkpoint = final_checkpoint
    _interrupt_preparation(checkpoint, monkeypatch, boundary)
    receipt_path, manifest_path = _preparation_artifacts(checkpoint)
    original_receipt = receipt_path.read_bytes()
    original_manifest = manifest_path.read_bytes()
    manifest = json.loads(original_manifest)
    original_initial = manifest["initial"]
    captures = {
        stream: (
            checkpoint.config.coordination_root / original_initial[f"{stream}_path"]
        ).read_bytes()
        for stream in ("stdout", "stderr")
    }
    assert checkpoint.local_runs == 1
    assert checkpoint.host_checks == (1 if boundary == "receipt" else 2)

    resumed = Supervisor(checkpoint.config, checkpoint.invoker)
    resumed._run_role("backend")

    assert resumed.load_state("backend").status is WorkerStatus.DONE
    assert checkpoint.local_runs == 1
    assert checkpoint.host_checks == 2
    assert len(checkpoint.prompts) == 1
    assert receipt_path.read_bytes() == original_receipt
    recovered = json.loads(manifest_path.read_bytes())
    assert recovered["attempt_id"] == manifest["attempt_id"]
    assert recovered["initial"] == original_initial
    for stream, data in captures.items():
        assert (
            checkpoint.config.coordination_root / original_initial[f"{stream}_path"]
        ).read_bytes() == data
    if boundary == "closing":
        assert manifest_path.read_bytes() == original_manifest


def test_final_preparation_preserves_opening_when_no_success_was_written(
    final_checkpoint, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkpoint = final_checkpoint
    _interrupt_preparation(checkpoint, monkeypatch, "opening")
    receipt_path, manifest_path = _preparation_artifacts(checkpoint)
    original = json.loads(manifest_path.read_bytes())
    assert not receipt_path.exists()
    assert checkpoint.local_runs == 0

    resumed = Supervisor(checkpoint.config, checkpoint.invoker)
    resumed._run_role("backend")

    assert resumed.load_state("backend").status is WorkerStatus.DONE
    recovered = json.loads(manifest_path.read_bytes())
    assert recovered["initial"] == original["initial"]
    assert recovered["attempt_id"] == original["attempt_id"]
    assert checkpoint.local_runs == 1
    assert checkpoint.host_checks == 2
    assert len(checkpoint.prompts) == 1


@pytest.mark.parametrize(
    "damage",
    [
        "receipt_null",
        "receipt_list",
        "manifest_null",
        "missing_manifest",
        "missing_initial",
        "initial_head",
        "current_head",
        "initial_output",
        "script",
        "command",
        "lease",
        "writer",
        "cloud",
        "completion_manifest",
        "stale_receipt",
        "new_opening",
        "malformed_final",
    ],
)
def test_final_preparation_rejects_invalid_partial_checkpoint_without_overwriting(
    final_checkpoint, monkeypatch: pytest.MonkeyPatch, damage: str
) -> None:
    checkpoint = final_checkpoint
    _interrupt_preparation(checkpoint, monkeypatch, "receipt")
    receipt_path, manifest_path = _preparation_artifacts(checkpoint)
    manifest = json.loads(manifest_path.read_bytes())
    root = checkpoint.config.coordination_root
    if damage.startswith("receipt_"):
        receipt_path.write_text("null" if damage == "receipt_null" else "[]", encoding="utf-8")
    elif damage == "manifest_null":
        manifest_path.write_text("null", encoding="utf-8")
    elif damage == "missing_manifest":
        manifest_path.unlink()
    elif damage in {"missing_initial", "initial_head", "command", "malformed_final"}:
        if damage == "missing_initial":
            del manifest["initial"]
        elif damage == "initial_head":
            manifest["initial"]["head"] = "1" * 40
            manifest["initial"]["lease"]["head"] = "1" * 40
        elif damage == "command":
            manifest["initial"]["command"] = ["unrelated-check"]
        else:
            manifest["final"] = []
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    elif damage == "initial_output":
        (root / manifest["initial"]["stdout_path"]).write_text("changed output", encoding="utf-8")
    elif damage == "script":
        (root / "scripts/check-coordination.ps1").write_text("changed script", encoding="utf-8")
    elif damage == "current_head":
        _advance_final_head(checkpoint)
    elif damage == "lease":
        acknowledge_handoff(checkpoint.config, "backend", source="test")
    elif damage == "new_opening":
        state = checkpoint.supervisor.load_state("backend")
        coordination_checks.begin_checks(
            checkpoint.config,
            "backend",
            state.task,
            checkpoint.head,
            checkpoint.head,
            recovered=True,
        )
    elif damage in {"writer", "cloud"}:
        directory = root / ("active-agents" if damage == "writer" else "cloud-sessions")
        directory.mkdir(exist_ok=True)
        (directory / "backend.json").write_text("{}", encoding="utf-8")
    elif damage == "completion_manifest":
        current = completion.COMPLETION_MANIFESTS["backend"]
        monkeypatch.setitem(
            completion.COMPLETION_MANIFESTS,
            "backend",
            replace(
                current, local_gates=(LocalGate("changed", (sys.executable, "-c", "print('NEW')")),)
            ),
        )
    else:
        receipt = json.loads(receipt_path.read_bytes())
        receipt["passed_at"] = (
            datetime.fromisoformat(manifest["initial"]["observed_at"]) - timedelta(seconds=1)
        ).isoformat()
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    original_receipt = receipt_path.read_bytes()
    original_manifest = manifest_path.read_bytes() if manifest_path.exists() else None
    original_host_checks = checkpoint.host_checks
    existing_captures = {
        path: path.read_bytes() for path in (root / "evidence/supervisor-checks").glob("*.txt")
    }

    resumed = Supervisor(checkpoint.config, checkpoint.invoker)
    resumed._run_role("backend")

    state = resumed.load_state("backend")
    assert state.status is WorkerStatus.BLOCKED_SYSTEM
    assert state.blocker_kind == "incomplete_evidence"
    assert state.resume_status is None
    assert checkpoint.prompts == []
    assert checkpoint.local_runs == 1
    assert checkpoint.host_checks == original_host_checks
    assert receipt_path.read_bytes() == original_receipt
    assert (manifest_path.read_bytes() if manifest_path.exists() else None) == original_manifest
    assert {
        path: path.read_bytes() for path in (root / "evidence/supervisor-checks").glob("*.txt")
    } == existing_captures


@pytest.mark.parametrize(
    "damage", ["receipt_output", "receipt_after_closing", "missing_receipt", "script"]
)
def test_final_preparation_rejects_invalid_complete_checkpoint_without_overwriting(
    final_checkpoint, monkeypatch: pytest.MonkeyPatch, damage: str
) -> None:
    checkpoint = final_checkpoint
    _interrupt_preparation(checkpoint, monkeypatch, "closing")
    receipt_path, manifest_path = _preparation_artifacts(checkpoint)
    manifest = json.loads(manifest_path.read_bytes())
    receipt = json.loads(receipt_path.read_bytes())
    if damage == "missing_receipt":
        receipt_path.unlink()
    elif damage == "script":
        (checkpoint.config.coordination_root / "scripts/check-coordination.ps1").write_text(
            "changed script", encoding="utf-8"
        )
    else:
        if damage == "receipt_output":
            receipt["gates"][0]["stdout"] = "tampered but valid receipt"
        else:
            receipt["passed_at"] = (
                datetime.fromisoformat(manifest["final"]["observed_at"]) + timedelta(seconds=1)
            ).isoformat()
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        if damage == "receipt_after_closing":
            # Keep the byte binding valid so this checks the independent time bound.
            manifest["validation_sha256"] = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    original_receipt = receipt_path.read_bytes() if receipt_path.exists() else None
    original_manifest = manifest_path.read_bytes()

    resumed = Supervisor(checkpoint.config, checkpoint.invoker)
    resumed._run_role("backend")

    state = resumed.load_state("backend")
    assert state.status is WorkerStatus.BLOCKED_SYSTEM
    assert state.blocker_kind == "incomplete_evidence"
    assert checkpoint.prompts == []
    assert checkpoint.local_runs == 1
    assert checkpoint.host_checks == 2
    assert (receipt_path.read_bytes() if receipt_path.exists() else None) == original_receipt
    assert manifest_path.read_bytes() == original_manifest


def _advance_final_head(checkpoint):
    repo = checkpoint.config.backend_root
    (repo / "README.md").write_text("reviewed final repair\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "repair final finding"], cwd=repo, check=True)
    checkpoint.head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()
    acknowledge_handoff(checkpoint.config, "backend", source="test")
    _accept(checkpoint.config, "backend")
    checkpoint.pr["headRefOid"] = checkpoint.head


@pytest.mark.parametrize("previous", ["review_rejection", "local_gate_failure"])
def test_explicit_new_final_claim_preserves_prior_proof_and_runs_new_validation(
    final_checkpoint, previous: str
) -> None:
    checkpoint = final_checkpoint
    if previous == "review_rejection":
        checkpoint.outcome = "changes_required"
    else:
        checkpoint.failure_flag.write_text("fail this attempt", encoding="utf-8")
    _claim(checkpoint)
    first = checkpoint.supervisor.load_state("backend")
    assert first.status is WorkerStatus.IMPLEMENTING
    if previous == "review_rejection":
        assert first.task.task_id == "FINAL-backend"
    else:
        assert first.task.task_id.startswith("FINAL-backend-local-validation-")
        identity = first.task.task_id.rsplit("-", 1)[1]
        assert len(identity) == 64 and all(
            character in "0123456789abcdef" for character in identity
        )
    receipt_path, manifest_path = _preparation_artifacts(checkpoint)
    old_proof = [path.read_bytes() for path in (receipt_path, manifest_path) if path.exists()]
    old_manifest = json.loads(manifest_path.read_bytes())
    root = checkpoint.config.coordination_root
    captures = {
        path: path.read_bytes() for path in (root / "evidence/supervisor-checks").glob("*.txt")
    }
    if previous == "review_rejection":
        _advance_final_head(checkpoint)
        checkpoint.outcome = "pass"
    else:
        checkpoint.failure_flag.unlink()
    checkpoint.supervisor.save_state(WorkerState("backend", status=WorkerStatus.PLANNING))

    _claim(checkpoint)

    state = checkpoint.supervisor.load_state("backend")
    assert state.status is WorkerStatus.DONE
    assert state.task_base_head == checkpoint.head
    assert checkpoint.local_runs == 2
    assert len(checkpoint.prompts) == (2 if previous == "review_rejection" else 1)
    assert json.loads(manifest_path.read_bytes())["attempt_id"] != old_manifest["attempt_id"]
    archived = [
        path.read_bytes()
        for path in (root / "evidence/final-preparation-history/backend").glob("*.json")
    ]
    assert sorted(archived) == sorted(old_proof)
    for path, data in captures.items():
        assert path.read_bytes() == data


@pytest.mark.parametrize(
    "boundary", ["archive_copy", "receipt_retired", "manifest_retired", "task_saved"]
)
def test_new_final_claim_retirement_recovers_durable_planner_result(
    final_checkpoint, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    checkpoint = final_checkpoint
    checkpoint.outcome = "changes_required"
    _claim(checkpoint)
    checkpoint.outcome = "pass"
    receipt_path, manifest_path = _preparation_artifacts(checkpoint)
    old_proof = [receipt_path.read_bytes(), manifest_path.read_bytes()]
    checkpoint.supervisor.save_state(WorkerState("backend", status=WorkerStatus.PLANNING))
    planner_calls = []

    def plan(role, state, context):
        planner_calls.append(role)
        assert len(planner_calls) == 1
        result = AgentResult(
            "done",
            "Ready for new final verification",
            "Review independently",
            role=role,
            invocation_id="durable-new-final-claim",
        )
        provenance = capture_supplied_input(
            checkpoint.config,
            role,
            "plan",
            "",
            result.invocation_id,
            checkpoint.head,
            context,
            context,
            input_kind="cli_prompt_utf8",
        )
        assert provenance is not None
        advance_handoff(
            checkpoint.config,
            role,
            pre_head=checkpoint.head,
            post_head=checkpoint.head,
            invocation_id=result.invocation_id,
            provider="codex",
            pending_result={
                "phase": "plan",
                "role": role,
                "task_id": "",
                "invocation_id": result.invocation_id,
                "pre_head": checkpoint.head,
                "post_head": checkpoint.head,
                "result": asdict(result),
                "input_provenance": provenance,
            },
        )
        return result

    monkeypatch.setattr(checkpoint.invoker, "lead", plan)
    real_write = coordination_checks.atomic_write_text
    real_unlink = Path.unlink
    real_save = checkpoint.supervisor.save_state

    def write_then_interrupt(path, text):
        real_write(path, text)
        if "final-preparation-history" in path.parts:
            raise RuntimeError("interrupted final retirement")

    def unlink_then_interrupt(path, *args, **kwargs):
        real_unlink(path, *args, **kwargs)
        target = receipt_path if boundary == "receipt_retired" else manifest_path
        if path == target:
            raise RuntimeError("interrupted final retirement")

    def save_then_interrupt(state):
        real_save(state)
        if state.task and state.task.task_id == "FINAL-VERIFY-backend":
            raise RuntimeError("interrupted final retirement")

    with monkeypatch.context() as patch:
        if boundary == "archive_copy":
            patch.setattr(coordination_checks, "atomic_write_text", write_then_interrupt)
        elif boundary == "task_saved":
            patch.setattr(checkpoint.supervisor, "save_state", save_then_interrupt)
        else:
            patch.setattr(Path, "unlink", unlink_then_interrupt)
        with pytest.raises(RuntimeError, match="interrupted final retirement"):
            checkpoint.supervisor._run_role("backend")

    parked = checkpoint.supervisor.load_state("backend")
    if boundary == "task_saved":
        assert parked.status is WorkerStatus.IMPLEMENTING
        assert parked.task.task_id == "FINAL-VERIFY-backend"
        assert read_pending_result(checkpoint.config, "backend") is None
    else:
        assert parked.status is WorkerStatus.PLANNING
        pending = read_pending_result(checkpoint.config, "backend")
        assert pending and pending["phase"] == "plan"
    assert checkpoint.local_runs == 1

    resumed = Supervisor(checkpoint.config, checkpoint.invoker)
    resumed._run_role("backend")

    assert resumed.load_state("backend").status is WorkerStatus.DONE
    assert planner_calls == ["backend"]
    assert checkpoint.local_runs == 2
    assert len(checkpoint.prompts) == 2
    archived = [
        path.read_bytes()
        for path in (
            checkpoint.config.coordination_root / "evidence/final-preparation-history/backend"
        ).glob("*.json")
    ]
    assert sorted(archived) == sorted(old_proof)
    assert read_pending_result(checkpoint.config, "backend") is None


@pytest.mark.parametrize("failure", ["copy_error", "archive_collision", "writer"])
def test_new_final_claim_never_deletes_proof_when_retirement_is_unproven(
    final_checkpoint, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    checkpoint = final_checkpoint
    checkpoint.outcome = "changes_required"
    _claim(checkpoint)
    receipt_path, manifest_path = _preparation_artifacts(checkpoint)
    receipt_bytes = receipt_path.read_bytes()
    manifest_bytes = manifest_path.read_bytes()
    history = checkpoint.config.coordination_root / "evidence/final-preparation-history/backend"
    if failure == "copy_error":
        original_write = coordination_checks.atomic_write_text

        def fail_archive(path, text):
            if "final-preparation-history" in path.parts:
                raise OSError("archive storage unavailable")
            original_write(path, text)

        monkeypatch.setattr(coordination_checks, "atomic_write_text", fail_archive)
    elif failure == "archive_collision":
        history.mkdir(parents=True)
        archive = history / f"validation-{hashlib.sha256(receipt_bytes).hexdigest()}.json"
        archive.write_bytes(b"unexpected archive content")
    else:
        active = checkpoint.config.coordination_root / "active-agents"
        active.mkdir(exist_ok=True)
        (active / "backend.json").write_text("{}", encoding="utf-8")

    _claim(checkpoint)

    state = checkpoint.supervisor.load_state("backend")
    assert state.status is WorkerStatus.BLOCKED_SYSTEM
    assert state.blocker_kind == "incomplete_evidence"
    assert receipt_path.read_bytes() == receipt_bytes
    assert manifest_path.read_bytes() == manifest_bytes
    assert checkpoint.local_runs == 1
    assert len(checkpoint.prompts) == 1
    if failure == "archive_collision":
        assert archive.read_bytes() == b"unexpected archive content"


def test_reviewing_final_receipt_corruption_reaches_its_preserving_gate(
    final_checkpoint, monkeypatch
):
    checkpoint = final_checkpoint
    _interrupt_preparation(checkpoint, monkeypatch, "closing")
    state = checkpoint.supervisor.load_state("backend")
    checkpoint.supervisor.save_state(replace(state, status=WorkerStatus.REVIEWING))
    receipt_path, manifest_path = _preparation_artifacts(checkpoint)
    receipt_path.write_text("[]", encoding="utf-8")
    receipt_before = receipt_path.read_bytes()
    manifest_before = manifest_path.read_bytes()

    resumed = Supervisor(checkpoint.config, checkpoint.invoker)
    resumed._run_role("backend")

    parked = resumed.load_state("backend")
    assert parked.status is WorkerStatus.BLOCKED_SYSTEM
    assert parked.blocker_kind == "incomplete_evidence"
    assert parked.resume_status is None
    assert receipt_path.read_bytes() == receipt_before
    assert manifest_path.read_bytes() == manifest_before
    assert checkpoint.local_runs == 1
    assert checkpoint.host_checks == 2
    assert not checkpoint.prompts


def test_parked_final_dependency_waits_unchanged_then_rejects_corrupt_proof(
    final_checkpoint, monkeypatch
):
    checkpoint = final_checkpoint
    _interrupt_preparation(checkpoint, monkeypatch, "closing")
    checkpoint.outcome = "blocked_system"
    resumed = Supervisor(checkpoint.config, checkpoint.invoker)
    resumed._run_role("backend")
    waiting = resumed.load_state("backend")
    assert waiting.blocker_kind == "model_dependency"
    assert waiting.resume_status is WorkerStatus.REVIEWING
    assert waiting.model_blocker_fingerprint
    assert read_pending_result(checkpoint.config, "backend") is None
    assert len(checkpoint.prompts) == 1

    Supervisor(checkpoint.config, checkpoint.invoker)._run_role("backend")
    assert len(checkpoint.prompts) == 1
    assert resumed.load_state("backend") == waiting

    receipt_path, manifest_path = _preparation_artifacts(checkpoint)
    receipt_path.write_text("[]", encoding="utf-8")
    receipt_before = receipt_path.read_bytes()
    manifest_before = manifest_path.read_bytes()
    Supervisor(checkpoint.config, checkpoint.invoker)._run_role("backend")

    parked = resumed.load_state("backend")
    assert parked.status is WorkerStatus.BLOCKED_SYSTEM
    assert parked.blocker_kind == "incomplete_evidence"
    assert parked.resume_status is None
    assert parked.last_result_invocation_id == waiting.last_result_invocation_id
    assert parked.review_failures == waiting.review_failures
    assert receipt_path.read_bytes() == receipt_before
    assert manifest_path.read_bytes() == manifest_before
    assert checkpoint.local_runs == 1
    assert checkpoint.host_checks == 2
    assert len(checkpoint.prompts) == 1
