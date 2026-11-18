from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.dev_orchestrator import coordination_checks, runner
from scripts.dev_orchestrator.cli import save_config
from scripts.dev_orchestrator.completed_tasks import CompletedTaskLedger
from scripts.dev_orchestrator.handoff import read_pending_result, repo_snapshot
from scripts.dev_orchestrator.supervisor import Supervisor
from scripts.dev_orchestrator.types import (
    AgentResult,
    CoordinationMessage,
    TaskItem,
    WorkerState,
    WorkerStatus,
)
from scripts.dev_orchestrator.validation import run_iteration_validation
from tests.scripts.test_dev_orchestrator_routing import _safe_config


@pytest.fixture
def blocked_checkpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("ASKREX_PROVIDER_POLICY", "subscription_only")
    config, repo, _ = _safe_config(tmp_path)
    task = TaskItem(
        "backend-r16-03-private-status-contract",
        "After the current SECURITY-REPO-20261111 review/budget blocker is resolved, "
        "complete R16-03 in ROADMAP-20261116-CROSS-SURFACE-AUDIT.md. "
        "Propose a typed private status contract and obtain mobile agreement before implementation. "
        "Preserve minimal public heartbeat and all ownership and approval requirements.",
    )
    prior_task = TaskItem(
        "backend-security-exact-head-review-aa1f43f7",
        "Complete the exact-head SECURITY-REPO-20261111 independent review checkpoint.",
    )
    gate = [sys.executable, "-c", "print('CANONICAL-PRIOR-TASK-PASS')"]
    config = replace(
        config,
        observe_only=False,
        metadata={
            "iteration_validation": {
                "enabled": True,
                "backend": {
                    "overrides": [
                        {
                            "task_prefix": prior_task.task_id,
                            "gates": [{"name": "canonical", "command": gate, "cwd": "."}],
                        },
                        {
                            "task_prefix": task.task_id,
                            "gates": [{"name": "canonical", "command": gate, "cwd": "."}],
                        },
                    ]
                },
            }
        },
    )
    save_config(config)
    root = config.coordination_root
    (root / "scripts").mkdir(exist_ok=True)
    (root / "scripts/check-coordination.ps1").write_text("Write-Output 'check'\n", encoding="utf-8")
    (root / "issues").mkdir(exist_ok=True)
    (root / "mailbox/backend").mkdir(parents=True, exist_ok=True)
    (root / "mailbox/mobile").mkdir(parents=True, exist_ok=True)
    (root / "issues/SECURITY-REPO-20261111.md").write_text(
        "Owner: backend\nStatus: open\nThe wider security campaign remains open.\n",
        encoding="utf-8",
    )
    requirement = root / "issues/ROADMAP-20261116-CROSS-SURFACE-AUDIT.md"
    requirement.write_text(
        "# ROADMAP-20261116-CROSS-SURFACE-AUDIT\n"
        "Owner: both\nStatus: accepted-requirements; implementation-unverified\n"
        "### R16-03 | Backend | Design authenticated detailed status contract\n"
        "EXACT-REQUIREMENT: agree typed private status contract with mobile first; "
        "retain minimal public heartbeat; test authentication and no leakage.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        coordination_checks,
        "_check_command",
        lambda _config, role: [sys.executable, "-c", "import sys; print(sys.argv[1])", role],
    )
    head, dirty = repo_snapshot(repo)
    assert not dirty
    coordination_checks.begin_checks(config, "backend", prior_task, head, head)
    report = run_iteration_validation(
        config, "backend", prior_task.task_id, base_head=head, head=head
    )
    assert report.passed, report.feedback
    coordination_checks.finish_checks(config, "backend", prior_task, head, head)
    CompletedTaskLedger(root).record(
        "backend",
        prior_task,
        completed_head=head,
        invocation_id="accepted-prior-review",
        source="independent_review",
    )
    (root / "mailbox/backend/MSG-old-validation-failure.md").write_text(
        "From: supervisor maintenance\nTo: backend\nRelated: "
        + prior_task.task_id
        + "\nThe canonical backend gate remains FAILED at HEAD "
        + head
        + ". Fresh validation and independent review remain required.\n",
        encoding="utf-8",
    )
    observed = SimpleNamespace(
        config=config,
        repo=repo,
        task=task,
        prior_task=prior_task,
        head=head,
        root=root,
        requirement=requirement,
        prompts=[],
        results=[],
        after_model=None,
        before_model=None,
        outcome="blocked_system",
        proposal=True,
    )
    real_run = runner.run_command

    def model(command, cwd, timeout_seconds=1800, **kwargs):
        assert Path(command[0]).stem.casefold() == "codex"
        prompt = kwargs["stdin_text"]
        assert prompt.startswith(
            (
                "You are the backend AskRex implementation worker.",
                "Independently review AskRex backend",
            )
        )
        observed.prompts.append(prompt)
        invocation = re.search(r"AskRex-Orchestrator-Invocation-ID: ([0-9a-f-]+)", prompt).group(1)
        result = AgentResult(
            observed.outcome,
            "No files changed; exact peer agreement is absent.",
            "Wait for mobile to accept or request changes to the existing proposal.",
            role="backend",
            task_id=task.task_id,
            invocation_id=invocation,
            blocker_reason="The proposed contract has no mobile acknowledgment.",
            coordination_messages=(
                (
                    CoordinationMessage(
                        "mobile",
                        "high",
                        task.task_id,
                        True,
                        "PROPOSAL-ONLY: typed status contract; this is unimplemented and not approved.",
                    ),
                )
                if observed.proposal
                else ()
            ),
        )
        observed.results.append(result)
        if observed.before_model:
            observed.before_model()
        process_result = real_run(
            [sys.executable, "-c", "print(" + repr(json.dumps(asdict(result))) + ")"],
            cwd,
            timeout_seconds,
            **kwargs,
        )
        if observed.after_model:
            observed.after_model()
        return process_result

    monkeypatch.setattr(runner, "run_command", model)
    observed.invoker = runner.CliAgentInvoker(config)
    observed.supervisor = Supervisor(config, observed.invoker)
    observed.supervisor.save_state(
        WorkerState("backend", status=WorkerStatus.IMPLEMENTING, task=task, task_base_head=head)
    )
    return observed


def _peer_reply(
    checkpoint, body="MOBILE-REPLY: please change the optional field before agreement."
):
    path = checkpoint.root / "mailbox/backend/MSG-peer-contract-reply.md"
    path.write_text(
        "From: mobile\nTo: backend\nRelated: "
        + checkpoint.task.task_id
        + "\nNeeds response: yes\n"
        + body
        + "\n",
        encoding="utf-8",
    )
    return path


def test_real_worker_receives_explicit_requirements_and_scoped_prior_completion(blocked_checkpoint):
    checkpoint = blocked_checkpoint
    checkpoint.supervisor.run_role("backend")
    assert len(checkpoint.prompts) == 1
    prompt = checkpoint.prompts[0]
    assert "EXACT-REQUIREMENT:" in prompt
    assert "accepted-prior-review" in prompt
    assert "CANONICAL-PRIOR-TASK-PASS" in prompt
    assert "Prior-task baseline" in prompt
    assert "Historical coordination" in prompt
    assert "does not validate the current task" in prompt
    assert "The wider security campaign remains open." in prompt
    assert "globally approved" not in prompt


def test_real_model_block_waits_across_restart_without_self_waking(blocked_checkpoint):
    checkpoint = blocked_checkpoint
    checkpoint.supervisor.run_role("backend")
    initial = checkpoint.supervisor.load_state("backend")
    assert initial.status is WorkerStatus.BLOCKED_SYSTEM
    assert initial.blocker_kind == "model_dependency"
    assert initial.model_blocker_feedback
    assert initial.model_blocker_fingerprint
    assert read_pending_result(checkpoint.config, "backend") is None
    messages = sorted((checkpoint.root / "mailbox/mobile").glob("MSG-*.md"))
    assert len(messages) == 1
    before = {p: p.read_bytes() for p in messages}
    for _ in range(3):
        Supervisor(checkpoint.config, checkpoint.invoker).run_role("backend")
    assert len(checkpoint.prompts) == 1
    assert checkpoint.supervisor.load_state("backend") == initial
    checks = [
        json.loads(line)
        for line in (checkpoint.root / "logs/model-dependency-checks.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert len(checks) == 3
    assert all(
        row["blocked_invocation_id"] == initial.last_result_invocation_id
        and row["external_fingerprint"]
        == row["current_external_fingerprint"]
        == initial.model_blocker_fingerprint
        and row["observe_only"] is False
        for row in checks
    )
    assert {p: p.read_bytes() for p in messages} == before
    records = list((checkpoint.root / "model-blockers").glob("*.json"))
    assert len(records) == 1
    archived = json.loads(records[0].read_text(encoding="utf-8"))
    assert archived["result"] == json.loads(json.dumps(asdict(checkpoint.results[0])))
    assert archived["pending_result"]["result"] == archived["result"]
    assert (
        archived["pending_result"]["input_provenance"]["fingerprint"]
        == initial.model_blocker_fingerprint
    )


@pytest.mark.parametrize("timing", ["before_model", "after_model"])
def test_peer_change_during_model_run_wakes_with_prior_feedback_and_existing_proposal(
    blocked_checkpoint, timing
):
    checkpoint = blocked_checkpoint
    setattr(checkpoint, timing, lambda: _peer_reply(checkpoint))
    checkpoint.supervisor.run_role("backend")
    assert len(checkpoint.prompts) == 1
    assert "MOBILE-REPLY:" not in checkpoint.prompts[0]
    setattr(checkpoint, timing, None)
    checkpoint.proposal = False
    Supervisor(checkpoint.config, checkpoint.invoker).run_role("backend")
    assert len(checkpoint.prompts) == 2
    prompt = checkpoint.prompts[1]
    assert "MOBILE-REPLY:" in prompt
    assert "PROPOSAL-ONLY:" in prompt
    assert "Previous worker-reported blocker" in prompt
    assert "Wait for mobile to accept or request changes" in prompt
    assert "not approval" in prompt
    Supervisor(checkpoint.config, checkpoint.invoker).run_role("backend")
    assert len(checkpoint.prompts) == 2
    assert len(list((checkpoint.root / "mailbox/mobile").glob("MSG-*.md"))) == 1


def test_blocker_archive_survives_interruption_before_publication(blocked_checkpoint, monkeypatch):
    from scripts.dev_orchestrator.storage import AtomicJsonStore

    checkpoint = blocked_checkpoint
    original = AtomicJsonStore.write
    interrupted = False

    def write(store, value):
        nonlocal interrupted
        original(store, value)
        if store.path.parent.name == "model-blockers" and not interrupted:
            interrupted = True
            raise KeyboardInterrupt("after immutable model blocker archive")

    monkeypatch.setattr(AtomicJsonStore, "write", write)
    with pytest.raises(KeyboardInterrupt):
        checkpoint.supervisor.run_role("backend")
    pending = read_pending_result(checkpoint.config, "backend")
    assert pending is not None
    assert not list((checkpoint.root / "mailbox/mobile").glob("MSG-*.md"))
    archive = next((checkpoint.root / "model-blockers").glob("*.json"))
    original_bytes = archive.read_bytes()
    _peer_reply(checkpoint)
    monkeypatch.setattr(AtomicJsonStore, "write", original)
    recovered = Supervisor(checkpoint.config, checkpoint.invoker)
    recovered.run_role("backend")
    assert len(checkpoint.prompts) == 1
    assert archive.read_bytes() == original_bytes
    assert read_pending_result(checkpoint.config, "backend") is None
    assert len(list((checkpoint.root / "mailbox/mobile").glob("MSG-*.md"))) == 1
    checkpoint.proposal = False
    checkpoint.outcome = "continue"
    recovered.run_role("backend")
    assert len(checkpoint.prompts) == 2
    assert recovered.load_state("backend").iteration == 1
    assert not recovered.load_state("backend").model_blocker_feedback


def test_blocker_state_commit_before_journal_clear_is_consumed_once(
    blocked_checkpoint, monkeypatch
):
    from scripts.dev_orchestrator import supervisor as supervisor_module

    checkpoint = blocked_checkpoint
    original = supervisor_module.clear_pending_result

    def fail_clear(*args, **kwargs):
        raise OSError("injected journal cleanup failure")

    monkeypatch.setattr(supervisor_module, "clear_pending_result", fail_clear)
    checkpoint.supervisor.run_role("backend")
    committed = checkpoint.supervisor.load_state("backend")
    assert committed.blocker_kind == "model_dependency"
    assert committed.last_result_invocation_id == checkpoint.results[0].invocation_id
    assert read_pending_result(checkpoint.config, "backend") is not None
    archive = next((checkpoint.root / "model-blockers").glob("*.json"))
    before = archive.read_bytes()
    monkeypatch.setattr(supervisor_module, "clear_pending_result", original)
    resumed = Supervisor(checkpoint.config, checkpoint.invoker)
    resumed.run_role("backend")
    assert resumed.load_state("backend") == committed
    assert read_pending_result(checkpoint.config, "backend") is None
    assert len(checkpoint.prompts) == 1
    assert archive.read_bytes() == before
    assert len(list((checkpoint.root / "mailbox/mobile").glob("MSG-*.md"))) == 1


def test_host_archive_failure_keeps_pending_and_does_not_publish(blocked_checkpoint, monkeypatch):
    from scripts.dev_orchestrator.storage import AtomicJsonStore

    checkpoint = blocked_checkpoint
    original = AtomicJsonStore.write

    def fail_archive(store, value):
        if store.path.parent.name == "model-blockers":
            raise OSError("host evidence volume unavailable")
        return original(store, value)

    monkeypatch.setattr(AtomicJsonStore, "write", fail_archive)
    checkpoint.supervisor.run_role("backend")
    state = checkpoint.supervisor.load_state("backend")
    assert state.blocker_kind == "system"
    assert not state.model_blocker_fingerprint
    pending = read_pending_result(checkpoint.config, "backend")
    assert pending is not None
    assert not list((checkpoint.root / "mailbox/mobile").glob("MSG-*.md"))
    monkeypatch.setattr(AtomicJsonStore, "write", original)
    Supervisor(checkpoint.config, checkpoint.invoker).run_role("backend")
    assert len(checkpoint.prompts) == 1
    assert read_pending_result(checkpoint.config, "backend") is None
    assert len(list((checkpoint.root / "mailbox/mobile").glob("MSG-*.md"))) == 1


def test_admission_timestamps_and_outgoing_publication_do_not_wake(blocked_checkpoint):
    from scripts.dev_orchestrator.storage import AtomicJsonStore

    checkpoint = blocked_checkpoint
    checkpoint.supervisor.run_role("backend")
    store = AtomicJsonStore(checkpoint.root / "phase-admissions/backend.json")
    value = store.read()
    value["admitted_at"] = "2099-01-01T00:00:00+00:00"
    value["admission_id"] = "different-audit-id"
    store.write(value)
    (checkpoint.root / "mailbox/mobile/MSG-own-extra.md").write_text(
        "From: backend\nTo: mobile\nRelated: "
        + checkpoint.task.task_id
        + "\nPROPOSAL-ONLY: additional unaccepted author report.\n",
        encoding="utf-8",
    )
    for path in (checkpoint.root / "mailbox/backend").glob("*.md"):
        path.touch()
    Supervisor(checkpoint.config, checkpoint.invoker).run_role("backend")
    assert len(checkpoint.prompts) == 1
    assert checkpoint.supervisor.load_state("backend").blocker_kind == "model_dependency"


def test_legacy_parked_reason_gets_one_truthful_corrected_attempt(blocked_checkpoint):
    checkpoint = blocked_checkpoint
    state = checkpoint.supervisor.load_state("backend")
    checkpoint.supervisor.save_state(
        replace(
            state,
            status=WorkerStatus.BLOCKED_SYSTEM,
            blocker_kind="system",
            blocked_reason="Supplied historical evidence says the prior review is pending.",
            resume_status=WorkerStatus.IMPLEMENTING,
            last_result_invocation_id="consumed-legacy-model",
        )
    )
    checkpoint.supervisor.run_role("backend")
    assert len(checkpoint.prompts) == 1
    assert "Retained legacy parked reason" in checkpoint.prompts[0]
    assert "original consumed result has not been reconstructed" in checkpoint.prompts[0]
    assert "accepted-prior-review" in checkpoint.prompts[0]
    Supervisor(checkpoint.config, checkpoint.invoker).run_role("backend")
    assert len(checkpoint.prompts) == 1
    records = list((checkpoint.root / "model-blockers").glob("*.json"))
    assert len(records) == 1
    assert (
        json.loads(records[0].read_text(encoding="utf-8"))["invocation_id"]
        != "consumed-legacy-model"
    )


def test_agreement_allows_fresh_validation_but_not_automatic_completion(blocked_checkpoint):
    checkpoint = blocked_checkpoint
    checkpoint.supervisor.run_role("backend")
    _peer_reply(
        checkpoint, "MOBILE-REPLY: I accept the exact proposed contract for implementation."
    )
    checkpoint.proposal = False
    checkpoint.outcome = "ready_for_review"
    Supervisor(checkpoint.config, checkpoint.invoker).run_role("backend")
    state = checkpoint.supervisor.load_state("backend")
    assert state.status is WorkerStatus.REVIEWING
    assert not state.model_blocker_feedback
    assert not state.model_blocker_fingerprint
    assert state.iteration == 1
    assert len(checkpoint.prompts) == 2
    assert "I accept the exact proposed contract" in checkpoint.prompts[1]
    assert not CompletedTaskLedger(checkpoint.root).matching_record(
        "backend", checkpoint.task, repo=checkpoint.repo, current_head=checkpoint.head
    )


@pytest.mark.parametrize("missing", [True, False], ids=["missing", "oversized"])
def test_required_document_unavailable_blocks_before_model(blocked_checkpoint, missing):
    checkpoint = blocked_checkpoint
    if missing:
        checkpoint.requirement.unlink()
    else:
        checkpoint.requirement.write_text("REQUIRED " * 14000, encoding="utf-8")
    checkpoint.supervisor.run_role("backend")
    assert not checkpoint.prompts
    state = checkpoint.supervisor.load_state("backend")
    assert state.status is WorkerStatus.BLOCKED_SYSTEM
    assert (
        "requirement" in state.blocked_reason or "required worker evidence" in state.blocked_reason
    )


def test_newer_matching_failure_is_not_overridden_by_genuine_prior_pass(blocked_checkpoint):
    import subprocess

    from scripts.dev_orchestrator.validation import ValidationGate
    from scripts.dev_orchestrator.validation_failures import record_validation_failure

    checkpoint = blocked_checkpoint
    command = [sys.executable, "-c", "import sys; print('NEW-CURRENT-FAILURE'); sys.exit(2)"]
    outcome = subprocess.run(command, cwd=checkpoint.repo, capture_output=True, check=False)
    assert outcome.returncode == 2
    feedback = record_validation_failure(
        checkpoint.root,
        role="backend",
        task_id=checkpoint.task.task_id,
        base_head=checkpoint.head,
        head=checkpoint.head,
        gate=ValidationGate("new current gate", tuple(command)),
        cwd=checkpoint.repo,
        exit_code=outcome.returncode,
        stdout=outcome.stdout,
        stderr=outcome.stderr,
    )
    state = checkpoint.supervisor.load_state("backend")
    checkpoint.task = replace(checkpoint.task, feedback=feedback)
    checkpoint.supervisor.save_state(replace(state, task=checkpoint.task))
    checkpoint.supervisor.run_role("backend")
    prompt = checkpoint.prompts[0]
    assert "FAILED for task=" + checkpoint.task.task_id in prompt
    assert "NEW-CURRENT-FAILURE" in prompt
    assert "CANONICAL-PRIOR-TASK-PASS" in prompt
    assert "not superseded by a pass for another task or revision" in prompt


def test_current_failure_overrides_retained_older_same_task_receipt(blocked_checkpoint):
    import subprocess

    from scripts.dev_orchestrator.validation import ValidationGate
    from scripts.dev_orchestrator.validation_failures import record_validation_failure

    checkpoint = blocked_checkpoint
    coordination_checks.begin_checks(
        checkpoint.config, "backend", checkpoint.task, checkpoint.head, checkpoint.head
    )
    report = run_iteration_validation(
        checkpoint.config,
        "backend",
        checkpoint.task.task_id,
        base_head=checkpoint.head,
        head=checkpoint.head,
    )
    assert report.passed
    coordination_checks.finish_checks(
        checkpoint.config, "backend", checkpoint.task, checkpoint.head, checkpoint.head
    )
    receipt = checkpoint.root / "validation" / f"backend-{checkpoint.task.task_id}.json"
    genuine_pass = receipt.read_bytes()
    command = [sys.executable, "-c", "import sys; print('LATER-SAME-TASK-FAILURE'); sys.exit(1)"]
    failure = subprocess.run(command, cwd=checkpoint.repo, capture_output=True, check=False)
    record_validation_failure(
        checkpoint.root,
        role="backend",
        task_id=checkpoint.task.task_id,
        base_head=checkpoint.head,
        head=checkpoint.head,
        gate=ValidationGate("later gate", tuple(command)),
        cwd=checkpoint.repo,
        exit_code=failure.returncode,
        stdout=failure.stdout,
        stderr=failure.stderr,
    )
    checkpoint.supervisor.run_role("backend")
    host = (
        checkpoint.prompts[0]
        .split("### Current-task validation", 1)[1]
        .split("### Prior-task baseline", 1)[0]
    )
    assert "FAILED for task=" + checkpoint.task.task_id in host
    assert "LATER-SAME-TASK-FAILURE" in host
    assert "PASSED for task=" + checkpoint.task.task_id not in host
    assert receipt.read_bytes() == genuine_pass


def test_mobile_new_revision_failure_is_not_replaced_by_old_mobile_pass(tmp_path, monkeypatch):
    import subprocess

    from scripts.dev_orchestrator.handoff import acknowledge_handoff
    from scripts.dev_orchestrator.worker_context import build_worker_context

    config, _backend, mobile = _safe_config(tmp_path)
    root = config.coordination_root
    (root / "scripts").mkdir(exist_ok=True)
    (root / "scripts/check-coordination.ps1").write_text("Write-Output 'check'\n", encoding="utf-8")
    monkeypatch.setattr(
        coordination_checks,
        "_check_command",
        lambda _config, role: [sys.executable, "-c", "import sys; print(sys.argv[1])", role],
    )
    old_task = TaskItem("mobile-old-audit", "Read-only evidence audit.")
    new_task = TaskItem("mobile-current-chat-test", "Repair the current chat test import.")
    old_command = [sys.executable, "-c", "print('OLD-MOBILE-PASS')"]
    new_command = [
        sys.executable,
        "-c",
        "import sys; print('TransformError: react-native/index.js:27:7 Unexpected typeof'); sys.exit(1)",
    ]
    config = replace(
        config,
        observe_only=False,
        metadata={
            "iteration_validation": {
                "enabled": True,
                "mobile": {
                    "overrides": [
                        {
                            "task_prefix": old_task.task_id,
                            "gates": [{"name": "old", "command": old_command, "cwd": "."}],
                        },
                        {
                            "task_prefix": new_task.task_id,
                            "gates": [{"name": "current", "command": new_command, "cwd": "."}],
                        },
                    ]
                },
            }
        },
    )
    save_config(config)
    old_head, _ = repo_snapshot(mobile)
    coordination_checks.begin_checks(config, "mobile", old_task, old_head, old_head)
    passed = run_iteration_validation(
        config, "mobile", old_task.task_id, base_head=old_head, head=old_head
    )
    assert passed.passed
    coordination_checks.finish_checks(config, "mobile", old_task, old_head, old_head)
    CompletedTaskLedger(root).record(
        "mobile",
        old_task,
        completed_head=old_head,
        invocation_id="old-mobile-review",
        source="independent_review",
    )
    subprocess.run(
        ["git", "-c", "core.hooksPath=", "commit", "--allow-empty", "-m", "fixture next revision"],
        cwd=mobile,
        check=True,
        capture_output=True,
    )
    acknowledge_handoff(config, "mobile", source="test")
    new_head, _ = repo_snapshot(mobile)
    assert new_head != old_head
    failure = run_iteration_validation(
        config, "mobile", new_task.task_id, base_head=old_head, head=new_head
    )
    assert not failure.passed
    assert "TransformError" in failure.feedback
    state = WorkerState(
        "mobile",
        status=WorkerStatus.IMPLEMENTING,
        task=replace(new_task, feedback=failure.feedback),
        task_base_head=old_head,
    )
    packet = build_worker_context(config, "mobile", state, new_head)
    assert f"FAILED for task={new_task.task_id}, HEAD={new_head}" in packet.text
    assert "TransformError" in packet.text
    assert f"PASSED for task={old_task.task_id}, HEAD={new_head}" not in packet.text
    assert "does not validate the current task" in packet.text


def test_invalid_blocker_input_binding_preserves_pending_without_publication(
    blocked_checkpoint, monkeypatch
):
    from scripts.dev_orchestrator import supervisor as supervisor_module
    from scripts.dev_orchestrator.storage import AtomicJsonStore

    checkpoint = blocked_checkpoint
    original = supervisor_module.archive_model_blocker

    def interrupt_before_archive(*args, **kwargs):
        raise KeyboardInterrupt("retain pending before archive")

    monkeypatch.setattr(supervisor_module, "archive_model_blocker", interrupt_before_archive)
    with pytest.raises(KeyboardInterrupt):
        checkpoint.supervisor.run_role("backend")
    store = AtomicJsonStore(checkpoint.root / "handoff/backend.json")
    handoff = store.read()
    handoff["pending_result"]["input_provenance"]["task_id"] = "unrelated-task"
    store.write(handoff)
    before = store.path.read_bytes()
    monkeypatch.setattr(supervisor_module, "archive_model_blocker", original)
    Supervisor(checkpoint.config, checkpoint.invoker).run_role("backend")
    assert store.path.read_bytes() == before
    assert len(checkpoint.prompts) == 1
    assert not list((checkpoint.root / "mailbox/mobile").glob("MSG-*.md"))
    assert not list((checkpoint.root / "model-blockers").glob("*.json"))
    assert checkpoint.supervisor.load_state("backend").blocker_kind == "system"


def test_existing_current_receipt_does_not_self_wake_or_hide_a_peer_change(blocked_checkpoint):
    checkpoint = blocked_checkpoint
    coordination_checks.begin_checks(
        checkpoint.config, "backend", checkpoint.task, checkpoint.head, checkpoint.head
    )
    report = run_iteration_validation(
        checkpoint.config,
        "backend",
        checkpoint.task.task_id,
        base_head=checkpoint.head,
        head=checkpoint.head,
    )
    assert report.passed
    coordination_checks.finish_checks(
        checkpoint.config, "backend", checkpoint.task, checkpoint.head, checkpoint.head
    )
    receipt = checkpoint.root / "validation" / f"backend-{checkpoint.task.task_id}.json"
    original = receipt.read_bytes()
    checkpoint.supervisor.run_role("backend")
    assert len(checkpoint.prompts) == 1
    Supervisor(checkpoint.config, checkpoint.invoker).run_role("backend")
    assert len(checkpoint.prompts) == 1
    _peer_reply(checkpoint)
    checkpoint.proposal = False
    resumed = Supervisor(checkpoint.config, checkpoint.invoker)
    resumed.run_role("backend")
    assert len(checkpoint.prompts) == 2, resumed.load_state("backend").blocked_reason
    assert "MOBILE-REPLY:" in checkpoint.prompts[1]
    assert receipt.read_bytes() == original
    resumed.run_role("backend")
    assert len(checkpoint.prompts) == 2


def test_forged_wake_fingerprint_cannot_consume_a_durable_result(blocked_checkpoint, monkeypatch):
    from scripts.dev_orchestrator import supervisor as supervisor_module
    from scripts.dev_orchestrator.storage import AtomicJsonStore

    checkpoint = blocked_checkpoint
    original = supervisor_module.archive_model_blocker

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt("retain exact pending result")

    monkeypatch.setattr(supervisor_module, "archive_model_blocker", interrupt)
    with pytest.raises(KeyboardInterrupt):
        checkpoint.supervisor.run_role("backend")
    store = AtomicJsonStore(checkpoint.root / "handoff/backend.json")
    handoff = store.read()
    handoff["pending_result"]["input_provenance"]["fingerprint"] = "a" * 64
    store.write(handoff)
    before = store.path.read_bytes()
    monkeypatch.setattr(supervisor_module, "archive_model_blocker", original)
    Supervisor(checkpoint.config, checkpoint.invoker).run_role("backend")
    assert store.path.read_bytes() == before
    assert not list((checkpoint.root / "model-blockers").glob("*.json"))
    assert not list((checkpoint.root / "mailbox/mobile").glob("MSG-*.md"))
    assert len(checkpoint.prompts) == 1


def test_generic_task_id_selects_its_existing_outgoing_message(tmp_path):
    from scripts.dev_orchestrator.coordination import build_coordination_context

    root = tmp_path / "coordination"
    (root / "mailbox/mobile").mkdir(parents=True)
    (root / "mailbox/mobile/MSG-existing-proposal.md").write_text(
        "From: backend\nTo: mobile\nRelated: bounded-private-contract\n"
        "PROPOSED-GENERIC-CONTRACT: awaiting actual acknowledgment.\n",
        encoding="utf-8",
    )
    text = build_coordination_context(
        root,
        "backend",
        task_text="bounded-private-contract\nAgree the contract before implementation.",
    )
    assert "PROPOSED-GENERIC-CONTRACT" in text
    assert "proposal or report, not approval" in text


def test_repository_qualified_roadmap_draft_is_not_a_missing_shared_issue(tmp_path):
    from scripts.dev_orchestrator.coordination import build_coordination_context

    text = build_coordination_context(
        tmp_path,
        "backend",
        task_text="bounded-private-contract\nInspect docs/audits/ROADMAP-LOCAL-DRAFT.md without publishing it.",
    )
    assert "explicit shared requirement is missing" not in text


def test_corrupt_matching_failure_stream_blocks_without_a_provider(blocked_checkpoint):
    from scripts.dev_orchestrator.validation import ValidationGate
    from scripts.dev_orchestrator.validation_failures import record_validation_failure

    checkpoint = blocked_checkpoint
    record_validation_failure(
        checkpoint.root,
        role="backend",
        task_id=checkpoint.task.task_id,
        base_head=checkpoint.head,
        head=checkpoint.head,
        gate=ValidationGate("fixture diagnostic", (sys.executable, "-c", "raise SystemExit(1)")),
        cwd=checkpoint.repo,
        exit_code=1,
        stdout=b"captured failure",
        stderr=b"",
    )
    stream = next((checkpoint.root / "validation-failures").glob("*/stdout.log"))
    stream.write_bytes(b"changed bytes")
    checkpoint.supervisor.run_role("backend")
    assert not checkpoint.prompts
    assert "integrity mismatch" in checkpoint.supervisor.load_state("backend").blocked_reason


def test_explicit_requirement_cannot_follow_an_outside_directory_link(tmp_path):
    import os
    import subprocess

    from scripts.dev_orchestrator.coordination import build_coordination_context

    root = tmp_path / "coordination"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "ROADMAP-EXTERNAL.md").write_text(
        "OUTSIDE-AUTHORITY-DO-NOT-SUPPLY", encoding="utf-8"
    )
    link = root / "issues"
    if os.name == "nt":
        subprocess.run(
            ["cmd.exe", "/c", "mklink", "/J", str(link), str(outside)],
            check=True,
            capture_output=True,
        )
    else:
        link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="trusted.*issue|issue.*trusted|reparse"):
        build_coordination_context(
            root, "backend", task_text="bounded-task\nRead ROADMAP-EXTERNAL.md."
        )


def test_receipt_for_another_task_base_is_not_a_current_task_pass(blocked_checkpoint):
    from scripts.dev_orchestrator.worker_context import build_worker_context

    checkpoint = blocked_checkpoint
    coordination_checks.begin_checks(
        checkpoint.config, "backend", checkpoint.task, checkpoint.head, checkpoint.head
    )
    report = run_iteration_validation(
        checkpoint.config,
        "backend",
        checkpoint.task.task_id,
        base_head=checkpoint.head,
        head=checkpoint.head,
    )
    assert report.passed
    coordination_checks.finish_checks(
        checkpoint.config, "backend", checkpoint.task, checkpoint.head, checkpoint.head
    )
    state = replace(checkpoint.supervisor.load_state("backend"), task_base_head="0" * 40)
    packet = build_worker_context(checkpoint.config, "backend", state, checkpoint.head)
    current = packet.text.split("### Current-task validation", 1)[1].split(
        "### Prior-task baseline", 1
    )[0]
    assert "No matching current validation receipt" in current
    assert "PASSED for task=" + checkpoint.task.task_id not in current


def test_review_receives_late_peer_response_and_exact_rendered_input_is_retained(
    blocked_checkpoint,
):
    import hashlib

    checkpoint = blocked_checkpoint
    (checkpoint.root / "PROTOCOL.md").write_text(
        "REQUIRED-POLICY\n" + ("policy detail " * 650), encoding="utf-8"
    )
    _peer_reply(checkpoint, "ACTUAL-PEER-REVIEW-RESPONSE: change field shape before agreement.")
    for index in range(8):
        (checkpoint.root / f"mailbox/backend/MSG-unrelated-review-{index}.md").write_text(
            "From: supervisor\nRelated: unrelated-work\n" + "old history " * 350,
            encoding="utf-8",
        )
    coordination_checks.begin_checks(
        checkpoint.config, "backend", checkpoint.task, checkpoint.head, checkpoint.head
    )
    report = run_iteration_validation(
        checkpoint.config,
        "backend",
        checkpoint.task.task_id,
        base_head=checkpoint.head,
        head=checkpoint.head,
    )
    assert report.passed
    coordination_checks.finish_checks(
        checkpoint.config, "backend", checkpoint.task, checkpoint.head, checkpoint.head
    )
    checkpoint.supervisor.save_state(
        replace(checkpoint.supervisor.load_state("backend"), status=WorkerStatus.REVIEWING)
    )
    checkpoint.supervisor.run_role("backend")
    assert len(checkpoint.prompts) == 1
    assert "ACTUAL-PEER-REVIEW-RESPONSE" in checkpoint.prompts[0]
    record = json.loads(
        next((checkpoint.root / "model-blockers").glob("*.json")).read_text(encoding="utf-8")
    )
    supplied = record["input_provenance"]["supplied_input"]
    data = (checkpoint.root / supplied["path"]).read_bytes()
    assert data.decode("utf-8") == checkpoint.prompts[0]
    assert supplied["sha256"] == hashlib.sha256(data).hexdigest()
    assert supplied["bytes"] == len(data)
    assert record["input_provenance"]["source_context_sha256"] != supplied["sha256"]
    Supervisor(checkpoint.config, checkpoint.invoker).run_role("backend")
    assert len(checkpoint.prompts) == 1


def test_relevant_proposal_precedes_unrelated_full_size_mailbox_history(blocked_checkpoint):
    checkpoint = blocked_checkpoint
    (checkpoint.root / "PROTOCOL.md").write_text(
        "REQUIRED-POLICY\n" + ("policy detail " * 650), encoding="utf-8"
    )
    for index in range(20):
        (checkpoint.root / f"mailbox/backend/MSG-unrelated-{index:02}.md").write_text(
            "From: supervisor\nTo: backend\nRelated: unrelated-work\n" + "unrelated history " * 400,
            encoding="utf-8",
        )
    proposal = (
        "From: backend\nTo: mobile\nRelated: "
        + checkpoint.task.task_id
        + "\nNeeds response: yes\nEXISTING-BOUNDED-PROPOSAL\n"
        + "field detail " * 440
    )
    assert len(proposal) < 6000
    (checkpoint.root / "mailbox/mobile/MSG-existing-bound-proposal.md").write_text(
        proposal, encoding="utf-8"
    )
    checkpoint.proposal = False
    checkpoint.supervisor.run_role("backend")
    assert len(checkpoint.prompts) == 1
    assert "EXISTING-BOUNDED-PROPOSAL" in checkpoint.prompts[0]
    assert "EXACT-REQUIREMENT" in checkpoint.prompts[0]
    assert len(checkpoint.prompts[0]) < 110_000
    Supervisor(checkpoint.config, checkpoint.invoker).run_role("backend")
    assert len(checkpoint.prompts) == 1


def test_missing_requirement_does_not_lose_a_queued_task(blocked_checkpoint):
    from scripts.dev_orchestrator.storage import AtomicJsonStore

    checkpoint = blocked_checkpoint
    requirement = checkpoint.requirement.read_bytes()
    checkpoint.supervisor.save_state(WorkerState("backend"))
    checkpoint.supervisor.enqueue("backend", checkpoint.task)
    checkpoint.requirement.unlink()
    checkpoint.supervisor.run_role("backend")
    state = checkpoint.supervisor.load_state("backend")
    assert state.task == checkpoint.task
    assert state.task_base_head == checkpoint.head
    assert not checkpoint.prompts
    checkpoint.requirement.write_bytes(requirement)
    Supervisor(checkpoint.config, checkpoint.invoker).run_role("backend")
    assert len(checkpoint.prompts) == 1
    assert AtomicJsonStore(checkpoint.root / "queues/backend.json").read(default=[]) == []


@pytest.mark.parametrize("boundary", ["state", "queue"])
def test_queue_claim_survives_interruption_and_enqueue_without_losing_task(
    blocked_checkpoint, monkeypatch, boundary
):
    from scripts.dev_orchestrator.storage import AtomicJsonStore

    checkpoint = blocked_checkpoint
    checkpoint.supervisor.save_state(WorkerState("backend"))
    checkpoint.supervisor.enqueue("backend", checkpoint.task)
    real_write = AtomicJsonStore.write
    target = checkpoint.root / (
        "state/backend.json" if boundary == "state" else "queues/backend.json"
    )
    interrupted = False

    def stop_after_write(store, value):
        nonlocal interrupted
        real_write(store, value)
        if store.path == target and not interrupted:
            interrupted = True
            raise KeyboardInterrupt("queue claim interrupted after " + boundary)

    monkeypatch.setattr(AtomicJsonStore, "write", stop_after_write)
    with pytest.raises(KeyboardInterrupt):
        checkpoint.supervisor.run_role("backend")
    assert not checkpoint.prompts
    assert (checkpoint.root / "queue-claims/backend.json").exists()
    monkeypatch.setattr(AtomicJsonStore, "write", real_write)
    following = TaskItem("next-authorized-task", "Do the next bounded inspection.")
    restarted = Supervisor(checkpoint.config, checkpoint.invoker)
    restarted.enqueue("backend", following)
    assert restarted.load_state("backend").task == checkpoint.task
    assert restarted._queue_store("backend").read() == [following.to_dict()]
    assert not (checkpoint.root / "queue-claims/backend.json").exists()
    restarted.run_role("backend")
    assert len(checkpoint.prompts) == 1
    assert restarted.load_state("backend").task == checkpoint.task


@pytest.mark.parametrize("size", [8000, 110000], ids=["complete", "over-budget"])
def test_task_peer_reply_is_complete_or_dispatch_stops(blocked_checkpoint, size):
    checkpoint = blocked_checkpoint
    _peer_reply(
        checkpoint,
        "PEER-RESPONSE-START\n"
        + "x" * size
        + "\nPEER-CONDITION-AT-END: do not implement before this correction is agreed.",
    )
    checkpoint.supervisor.run_role("backend")
    if size < 100000:
        assert len(checkpoint.prompts) == 1
        assert "PEER-RESPONSE-START" in checkpoint.prompts[0]
        assert "PEER-CONDITION-AT-END" in checkpoint.prompts[0]
    else:
        assert not checkpoint.prompts
        state = checkpoint.supervisor.load_state("backend")
        assert state.task == checkpoint.task
        assert "bound" in state.blocked_reason


def test_tampered_actual_input_keeps_pending_blocker_and_does_not_publish(
    blocked_checkpoint, monkeypatch
):
    from scripts.dev_orchestrator import supervisor as supervisor_module

    checkpoint = blocked_checkpoint
    real_archive = supervisor_module.archive_model_blocker

    def stop_before_archive(*args, **kwargs):
        raise OSError("archive unavailable after genuine pending result")

    monkeypatch.setattr(supervisor_module, "archive_model_blocker", stop_before_archive)
    checkpoint.supervisor.run_role("backend")
    pending = read_pending_result(checkpoint.config, "backend")
    supplied = pending["input_provenance"]["supplied_input"]
    input_path = checkpoint.root / supplied["path"]
    original = input_path.read_bytes()
    input_path.write_bytes(original + b"\nchanged")
    monkeypatch.setattr(supervisor_module, "archive_model_blocker", real_archive)
    Supervisor(checkpoint.config, checkpoint.invoker).run_role("backend")
    assert read_pending_result(checkpoint.config, "backend") == pending
    assert not list((checkpoint.root / "model-blockers").glob("*.json"))
    assert not list((checkpoint.root / "mailbox/mobile").glob("MSG-*.md"))
    assert len(checkpoint.prompts) == 1
    input_path.write_bytes(original)
    Supervisor(checkpoint.config, checkpoint.invoker).run_role("backend")
    assert checkpoint.supervisor.load_state("backend").blocker_kind == "model_dependency"
    assert len(checkpoint.prompts) == 1


def test_exact_related_reply_outranks_crowded_issue_history_and_wakes(blocked_checkpoint):
    checkpoint = blocked_checkpoint
    for index in range(14):
        (checkpoint.root / f"mailbox/backend/MSG-issue-history-{index:02}.md").write_text(
            "From: supervisor\nTo: backend\nRelated: SECURITY-REPO-20261111\n"
            "ROADMAP-20261116-CROSS-SURFACE-AUDIT.md R16-03 historical note.\n",
            encoding="utf-8",
        )
    checkpoint.supervisor.run_role("backend")
    assert len(checkpoint.prompts) == 1
    _peer_reply(checkpoint, "EXACT-TASK-REPLY: change the proposed field before agreement.")
    Supervisor(checkpoint.config, checkpoint.invoker).run_role("backend")
    assert len(checkpoint.prompts) == 2
    assert "EXACT-TASK-REPLY" in checkpoint.prompts[-1]


def test_peer_reply_quoting_original_sender_is_not_own_output(blocked_checkpoint):
    checkpoint = blocked_checkpoint
    checkpoint.supervisor.run_role("backend")
    _peer_reply(
        checkpoint,
        "PEER-REPLY-WITH-QUOTE: the proposal needs this change.\n"
        "## Original proposal quoted for reference\nFrom: backend\nTo: mobile\n"
        "PROPOSAL-ONLY remains unapproved.",
    )
    Supervisor(checkpoint.config, checkpoint.invoker).run_role("backend")
    assert len(checkpoint.prompts) == 2
    assert "PEER-REPLY-WITH-QUOTE" in checkpoint.prompts[-1]


def test_supplied_input_junction_is_rejected_before_any_outside_write(blocked_checkpoint, tmp_path):
    import os
    import subprocess

    checkpoint = blocked_checkpoint
    outside = tmp_path / "outside-input-capture"
    outside.mkdir()
    link = checkpoint.root / "model-inputs"
    if os.name == "nt":
        subprocess.run(
            ["cmd.exe", "/c", "mklink", "/J", str(link), str(outside)],
            check=True,
            capture_output=True,
        )
    else:
        link.symlink_to(outside, target_is_directory=True)
    checkpoint.supervisor.run_role("backend")
    assert not checkpoint.prompts
    assert not list(outside.iterdir())
    assert "trusted" in checkpoint.supervisor.load_state("backend").blocked_reason


@pytest.mark.parametrize("phase", ["plan", "adjudicate"])
def test_supervisor_protected_lead_composition_never_silently_clips(blocked_checkpoint, phase):
    from scripts.dev_orchestrator.openai_worker import OpenAIModelWorker

    checkpoint = blocked_checkpoint
    if phase == "plan":
        state = WorkerState("backend", status=WorkerStatus.PLANNING)
    else:
        task = replace(
            checkpoint.task,
            prompt=checkpoint.task.prompt
            + " Coordination only evidence audit. Do not modify source.",
        )
        state = WorkerState(
            "backend", status=WorkerStatus.REVIEWING, task=task, task_base_head=checkpoint.head
        )
        coordination_checks.begin_checks(
            checkpoint.config, "backend", task, checkpoint.head, checkpoint.head
        )
        report = run_iteration_validation(
            checkpoint.config,
            "backend",
            task.task_id,
            base_head=checkpoint.head,
            head=checkpoint.head,
        )
        assert report.passed
        coordination_checks.finish_checks(
            checkpoint.config, "backend", task, checkpoint.head, checkpoint.head
        )
    checkpoint.supervisor.save_state(state)
    original = checkpoint.supervisor._worker_context("backend", state)
    limit = len(original.critical_text) + 150
    config = replace(checkpoint.config, openai_max_input_chars=limit)
    save_config(config)
    rendered = []
    worker = object.__new__(OpenAIModelWorker)
    worker.config = config

    class BoundCheckingLead:
        def lead(self, role, state, context, task=None):
            text = worker._lead_input(role, state, task, context, "bound-input-test", phase)
            rendered.append(text)
            return AgentResult(
                "idle" if phase == "plan" else "blocked_system",
                "No provider was invoked in this input-boundary fixture.",
                "Wait for complete evidence.",
                blocker_reason="Complete input required.",
            )

    supervisor = Supervisor(config, BoundCheckingLead())
    context = supervisor._worker_context("backend", state)
    if phase == "plan":
        supervisor._plan("backend", state, context)
    else:
        supervisor._adjudicate("backend", state, context)
    assert not rendered, "the actual supervisor composition must stop before clipped input is sent"
    parked = supervisor.load_state("backend")
    assert parked.status is WorkerStatus.BLOCKED_SYSTEM
    assert parked.blocker_kind == "incomplete_evidence"
