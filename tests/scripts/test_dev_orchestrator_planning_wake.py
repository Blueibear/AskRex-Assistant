from __future__ import annotations

import hashlib
import json
import re
import sys
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.dev_orchestrator import runner
from scripts.dev_orchestrator import supervisor as supervisor_module
from scripts.dev_orchestrator.cli import save_config
from scripts.dev_orchestrator.completion import CompletionGate
from scripts.dev_orchestrator.handoff import read_pending_result, repo_snapshot
from scripts.dev_orchestrator.storage import AtomicJsonStore
from scripts.dev_orchestrator.supervisor import Supervisor
from scripts.dev_orchestrator.types import (
    AgentResult,
    CoordinationMessage,
    TaskItem,
    WorkerState,
    WorkerStatus,
)
from scripts.dev_orchestrator.worker_context import WorkerContext
from tests.scripts.test_dev_orchestrator_final_review import final_checkpoint as final_checkpoint
from tests.scripts.test_dev_orchestrator_routing import _safe_config


@pytest.fixture
def planning_checkpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("ASKREX_PROVIDER_POLICY", "subscription_only")
    config, _backend, repo = _safe_config(tmp_path)
    config = replace(config, observe_only=False)
    save_config(config)
    root = config.coordination_root
    (root / "mailbox/mobile").mkdir(parents=True, exist_ok=True)
    head, dirty = repo_snapshot(repo)
    assert not dirty
    observed = SimpleNamespace(
        config=config,
        root=root,
        repo=repo,
        head=head,
        prompts=[],
        results=[],
        after_model=None,
        during_gate=None,
        outcome="done",
        final_gates=0,
    )
    real_run = runner.run_command

    def model(command, cwd, timeout_seconds=1800, **kwargs):
        assert Path(command[0]).stem.casefold() == "codex"
        prompt = kwargs["stdin_text"]
        assert "AskRex-Orchestrator-Invocation-ID:" in prompt
        observed.prompts.append(prompt)
        invocation = re.search(r"AskRex-Orchestrator-Invocation-ID: ([0-9a-f-]+)", prompt).group(1)
        assigned = observed.outcome == "assign"
        result = AgentResult(
            observed.outcome,
            (
                "The supplied input has no other work."
                if not assigned
                else "Review the new peer request."
            ),
            "Final acceptance remains required." if not assigned else "Reply through coordination.",
            role="mobile",
            invocation_id=invocation,
            task_id=(
                "mobile-peer-contract-response"
                if assigned
                else "mobile-bounded-snapshot-triage-20261118"
            ),
            task_prompt=(
                "Review the new peer proposal without granting authority." if assigned else ""
            ),
            coordination_messages=(
                CoordinationMessage(
                    "supervisor",
                    "normal",
                    "R16-03",
                    False,
                    "PLANNER-OWN-OUTPUT: final acceptance remains unconfirmed.",
                ),
            ),
        )
        observed.results.append(result)
        completed = real_run(
            [sys.executable, "-c", "print(" + repr(json.dumps(asdict(result))) + ")"],
            cwd,
            timeout_seconds,
            **kwargs,
        )
        if observed.after_model:
            observed.after_model()
        return completed

    def completion_gate(_config, role, *, head):
        assert role == "mobile" and head == observed.head
        observed.final_gates += 1
        if observed.during_gate:
            observed.during_gate()
        return CompletionGate(
            False,
            (
                "documentation truth acceptance is required",
                "iPhone physical acceptance is required",
                "cross-repo acceptance is required",
                "required CI and pull request are unavailable",
            ),
            needs_user=True,
            blocker_kind="acceptance",
        )

    monkeypatch.setattr(runner, "run_command", model)
    # These tests exercise planning and its actual durable runner/publication path.
    # The final gate stays false; no physical/CI acceptance is fabricated.
    monkeypatch.setattr(supervisor_module, "retire_final_preparation", lambda *a: None)
    monkeypatch.setattr(supervisor_module, "final_preparation_checkpoint", lambda *a: "open")
    monkeypatch.setattr(supervisor_module, "prepare_final_review", completion_gate)
    observed.invoker = runner.CliAgentInvoker(config)
    observed.supervisor = Supervisor(config, observed.invoker)
    observed.supervisor.save_state(WorkerState("mobile"))
    return observed


def peer_request(checkpoint):
    path = checkpoint.root / "mailbox/mobile/MSG-new-backend-proposal.md"
    path.write_text(
        "From: backend\nTo: mobile\nRelated: R16-03 private status contract\n"
        "Needs response: yes\nInvocation: genuine-peer-amendment\n\n"
        "NEW-PEER-PROPOSAL: explicitly accept this amended schema or request changes; "
        "no implementation has begun.\n",
        encoding="utf-8",
    )
    return path


def _assert_assigned(checkpoint):
    state = checkpoint.supervisor.load_state("mobile")
    assert state.status is WorkerStatus.IMPLEMENTING
    assert state.task.task_id == "mobile-peer-contract-response"
    assert state.blocked_reason == ""
    assert state.blocker_kind == ""
    assert state.planning_input_fingerprint == ""
    assert "NEW-PEER-PROPOSAL:" in checkpoint.prompts[-1]
    assert read_pending_result(checkpoint.config, "mobile") is None


def test_peer_request_during_plan_done_prevents_stale_completion(planning_checkpoint):
    checkpoint = planning_checkpoint
    checkpoint.after_model = lambda: peer_request(checkpoint)
    checkpoint.supervisor.run_role("mobile")
    state = checkpoint.supervisor.load_state("mobile")
    assert state.status in {WorkerStatus.IDLE, WorkerStatus.PLANNING}
    assert state.task is None
    assert state.last_result_invocation_id == checkpoint.results[0].invocation_id
    assert checkpoint.final_gates == 0
    assert read_pending_result(checkpoint.config, "mobile") is None
    assert "NEW-PEER-PROPOSAL:" not in checkpoint.prompts[0]
    messages = tuple((checkpoint.root / "mailbox/supervisor").glob("MSG-*.md"))
    assert len(messages) == 1
    original_message = messages[0].read_bytes()
    checkpoint.after_model = None
    checkpoint.outcome = "assign"
    Supervisor(checkpoint.config, checkpoint.invoker).run_role("mobile")
    assert len(checkpoint.prompts) == 2
    _assert_assigned(checkpoint)
    assert messages[0].read_bytes() == original_message
    assert checkpoint.final_gates == 0


def test_new_peer_request_wakes_only_taskless_completion_hold(planning_checkpoint):
    checkpoint = planning_checkpoint
    checkpoint.supervisor.run_role("mobile")
    state = checkpoint.supervisor.load_state("mobile")
    assert state.status is WorkerStatus.BLOCKED_USER
    assert state.blocker_kind == "acceptance"
    assert state.task is None
    assert checkpoint.final_gates == 1
    for _ in range(3):
        Supervisor(checkpoint.config, checkpoint.invoker).run_role("mobile")
    assert len(checkpoint.prompts) == 1
    assert checkpoint.supervisor.load_state("mobile") == state
    peer_request(checkpoint)
    checkpoint.outcome = "assign"
    Supervisor(checkpoint.config, checkpoint.invoker).run_role("mobile")
    assert len(checkpoint.prompts) == 2
    _assert_assigned(checkpoint)
    assert checkpoint.final_gates == 1


def test_peer_arrival_during_final_gate_keeps_original_plan_baseline(planning_checkpoint):
    checkpoint = planning_checkpoint
    checkpoint.during_gate = lambda: peer_request(checkpoint)
    checkpoint.supervisor.run_role("mobile")
    assert checkpoint.supervisor.load_state("mobile").blocker_kind == "acceptance"
    assert "NEW-PEER-PROPOSAL:" not in checkpoint.prompts[0]
    checkpoint.during_gate = None
    checkpoint.outcome = "assign"
    Supervisor(checkpoint.config, checkpoint.invoker).run_role("mobile")
    assert len(checkpoint.prompts) == 2
    _assert_assigned(checkpoint)
    assert checkpoint.final_gates == 1


@pytest.mark.parametrize("legacy_base", ["empty", "admitted_head"])
def test_legacy_acceptance_hold_uses_immutable_consumed_plan_input(
    planning_checkpoint, monkeypatch, legacy_base
):
    checkpoint = planning_checkpoint

    # Exact fe458 formula, before taskless planning scope was normalized.
    def old_fingerprint(packet, role, phase, state, head):
        payload = {
            "schema_version": 1,
            "role": role,
            "phase": phase,
            "task": state.task.to_dict() if state.task else None,
            "task_base_head": state.task_base_head,
            "head": head,
            "external_sources": sorted(packet.external_sources),
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("utf-8")
        ).hexdigest()

    checkpoint.supervisor.save_state(
        WorkerState(
            "mobile", task_base_head=checkpoint.head if legacy_base == "admitted_head" else ""
        )
    )
    with monkeypatch.context() as legacy:
        legacy.setattr(WorkerContext, "fingerprint", old_fingerprint)
        checkpoint.supervisor.run_role("mobile")
    store = AtomicJsonStore(checkpoint.root / "state/mobile.json")
    legacy = store.read()
    legacy.pop("planning_input_fingerprint", None)
    store.write(legacy)
    invocation = legacy["last_result_invocation_id"]
    digest = hashlib.sha256(invocation.encode()).hexdigest()
    directory = checkpoint.root / "model-inputs" / f"mobile-{digest}"
    preserved = {path.name: path.read_bytes() for path in directory.iterdir()}
    # Mutable most-recent admission is not legacy authority.
    AtomicJsonStore(checkpoint.root / "phase-admissions/mobile.json").write(
        {"unrelated": "later observation"}
    )
    for _ in range(2):
        Supervisor(checkpoint.config, checkpoint.invoker).run_role("mobile")
    assert len(checkpoint.prompts) == 1
    peer_request(checkpoint)
    checkpoint.outcome = "assign"
    Supervisor(checkpoint.config, checkpoint.invoker).run_role("mobile")
    assert len(checkpoint.prompts) == 2
    _assert_assigned(checkpoint)
    assert {path.name: path.read_bytes() for path in directory.iterdir()} == preserved


def test_unchanged_corrected_done_returns_to_acceptance_without_repeated_calls(planning_checkpoint):
    checkpoint = planning_checkpoint
    checkpoint.supervisor.run_role("mobile")
    peer_request(checkpoint)
    Supervisor(checkpoint.config, checkpoint.invoker).run_role("mobile")
    assert len(checkpoint.prompts) == 2
    held = checkpoint.supervisor.load_state("mobile")
    assert held.status is WorkerStatus.BLOCKED_USER
    assert held.blocker_kind == "acceptance"
    assert held.task is None
    assert len(held.planning_input_fingerprint) == 64
    assert checkpoint.final_gates == 2
    for _ in range(3):
        Supervisor(checkpoint.config, checkpoint.invoker).run_role("mobile")
    assert len(checkpoint.prompts) == 2
    assert checkpoint.supervisor.load_state("mobile") == held
    assert not (checkpoint.root / "completion/mobile-acceptance.json").exists()


@pytest.mark.parametrize(
    ("kind", "has_task", "resume"),
    [
        ("human", False, WorkerStatus.PLANNING),
        ("auth", False, WorkerStatus.PLANNING),
        ("github_auth", False, WorkerStatus.PLANNING),
        ("usage_limit", False, WorkerStatus.PLANNING),
        ("acceptance", True, WorkerStatus.PLANNING),
        ("acceptance", False, WorkerStatus.IMPLEMENTING),
        ("retest", True, WorkerStatus.PLANNING),
        ("dependency", True, WorkerStatus.IMPLEMENTING),
    ],
)
def test_external_input_does_not_release_other_user_gates(
    planning_checkpoint, kind, has_task, resume
):
    checkpoint = planning_checkpoint
    state = WorkerState(
        "mobile",
        status=WorkerStatus.BLOCKED_USER,
        task=TaskItem("held-task", "Requires the existing human boundary.") if has_task else None,
        task_base_head=checkpoint.head,
        blocked_reason="Keep the original gate.",
        blocker_kind=kind,
        resume_status=resume,
        planning_input_fingerprint="a" * 64,
    )
    checkpoint.supervisor.save_state(state)
    peer_request(checkpoint)
    Supervisor(checkpoint.config, checkpoint.invoker).run_role("mobile")
    assert checkpoint.supervisor.load_state("mobile") == state
    assert checkpoint.prompts == []
    assert read_pending_result(checkpoint.config, "mobile") is None


@pytest.mark.parametrize(
    "corruption", ["bytes", "manifest_missing", "head", "phase", "fingerprint"]
)
def test_unproven_legacy_baseline_cannot_release_acceptance(planning_checkpoint, corruption):
    checkpoint = planning_checkpoint
    checkpoint.supervisor.run_role("mobile")
    state_store = AtomicJsonStore(checkpoint.root / "state/mobile.json")
    legacy = state_store.read()
    legacy.pop("planning_input_fingerprint")
    state_store.write(legacy)
    invocation = legacy["last_result_invocation_id"]
    digest = hashlib.sha256(invocation.encode()).hexdigest()
    directory = checkpoint.root / "model-inputs" / f"mobile-{digest}"
    manifest_store = AtomicJsonStore(directory / "manifest.json")
    if corruption == "bytes":
        with (directory / "input.txt").open("ab") as handle:
            handle.write(b"\nCORRUPTED")
    elif corruption == "manifest_missing":
        (directory / "manifest.json").unlink()
    elif corruption == "fingerprint":
        legacy["planning_input_fingerprint"] = "unbound"
        state_store.write(legacy)
    else:
        manifest = manifest_store.read()
        replacement = "0" * 40 if corruption == "head" else "review"
        manifest["admission"][corruption] = replacement
        manifest["phase_admission"]["input_provenance"][corruption] = replacement
        if corruption == "phase":
            manifest["phase"] = replacement
            manifest["phase_admission"]["phase"] = replacement
        manifest_store.write(manifest)
    held = checkpoint.supervisor.load_state("mobile")
    peer_request(checkpoint)
    Supervisor(checkpoint.config, checkpoint.invoker).run_role("mobile")
    assert len(checkpoint.prompts) == 1
    assert checkpoint.supervisor.load_state("mobile") == held
    assert read_pending_result(checkpoint.config, "mobile") is None
    assert checkpoint.final_gates == 1


@pytest.mark.parametrize("queued", [False, True])
def test_acceptance_wake_preserves_an_unexpected_pending_result(planning_checkpoint, queued):
    checkpoint = planning_checkpoint
    checkpoint.supervisor.run_role("mobile")
    held = checkpoint.supervisor.load_state("mobile")
    handoff = AtomicJsonStore(checkpoint.root / "handoff/mobile.json")
    record = handoff.read()
    pending = {"invocation_id": "unrelated-unconsumed-result", "phase": "plan", "task_id": ""}
    handoff.write({**record, "pending_result": pending})
    queue = AtomicJsonStore(checkpoint.root / "queues/mobile.json")
    if queued:
        queue.write(
            [TaskItem("explicit-work", "Preserve this task until pending is resolved.").to_dict()]
        )
    queued_before = queue.read(default=[])
    peer_request(checkpoint)
    Supervisor(checkpoint.config, checkpoint.invoker).run_role("mobile")
    assert len(checkpoint.prompts) == 1
    assert checkpoint.supervisor.load_state("mobile") == held
    assert read_pending_result(checkpoint.config, "mobile") == pending
    assert queue.read(default=[]) == queued_before


def test_paused_controller_does_not_wake_acceptance_for_new_input(planning_checkpoint):
    checkpoint = planning_checkpoint
    checkpoint.supervisor.run_role("mobile")
    held = checkpoint.supervisor.load_state("mobile")
    peer_request(checkpoint)
    paused = replace(checkpoint.config, observe_only=True)
    save_config(paused)
    Supervisor(paused, checkpoint.invoker).run_role("mobile")
    assert len(checkpoint.prompts) == 1
    assert checkpoint.supervisor.load_state("mobile") == held
    assert checkpoint.final_gates == 1


@pytest.mark.parametrize("crash", ["before_state", "after_state"])
def test_stale_done_restart_keeps_original_input_and_publishes_once(
    planning_checkpoint, monkeypatch, crash
):
    checkpoint = planning_checkpoint
    checkpoint.after_model = lambda: peer_request(checkpoint)
    interrupted = False
    real_save = Supervisor.save_state
    real_clear = supervisor_module.clear_pending_result

    def save(self, state):
        nonlocal interrupted
        if (
            crash == "before_state"
            and not interrupted
            and state.status is WorkerStatus.IDLE
            and state.planning_input_fingerprint
        ):
            interrupted = True
            raise OSError("interrupt before stale done state commit")
        return real_save(self, state)

    def clear(config, role, *, invocation_id):
        nonlocal interrupted
        if crash == "after_state" and not interrupted:
            interrupted = True
            raise OSError("interrupt after stale done state commit")
        return real_clear(config, role, invocation_id=invocation_id)

    monkeypatch.setattr(Supervisor, "save_state", save)
    monkeypatch.setattr(supervisor_module, "clear_pending_result", clear)
    checkpoint.supervisor.run_role("mobile")
    assert interrupted
    assert len(checkpoint.prompts) == 1
    pending = read_pending_result(checkpoint.config, "mobile")
    assert pending["invocation_id"] == checkpoint.results[0].invocation_id
    original_baseline = pending["input_provenance"]["fingerprint"]
    first_messages = tuple((checkpoint.root / "mailbox/supervisor").glob("MSG-*.md"))
    assert len(first_messages) == 1
    original_message = first_messages[0].read_bytes()
    checkpoint.after_model = None
    checkpoint.outcome = "assign"
    Supervisor(checkpoint.config, checkpoint.invoker).run_role("mobile")
    if crash == "before_state":
        state = checkpoint.supervisor.load_state("mobile")
        assert len(checkpoint.prompts) == 1
        assert state.planning_input_fingerprint == original_baseline
        assert state.last_result_invocation_id == checkpoint.results[0].invocation_id
        assert state.status is WorkerStatus.IDLE
        assert read_pending_result(checkpoint.config, "mobile") is None
        Supervisor(checkpoint.config, checkpoint.invoker).run_role("mobile")
    assert len(checkpoint.prompts) == 2
    _assert_assigned(checkpoint)
    assert first_messages[0].read_bytes() == original_message
    assert checkpoint.final_gates == 0


def test_explicit_queue_clears_prior_planning_baseline(planning_checkpoint, monkeypatch):
    checkpoint = planning_checkpoint
    checkpoint.supervisor.run_role("mobile")
    held = checkpoint.supervisor.load_state("mobile")
    assert held.planning_input_fingerprint
    queue = AtomicJsonStore(checkpoint.root / "queues/mobile.json")
    queue.write([TaskItem("queued-authorized-work", "Continue this explicit task.").to_dict()])
    seen = []
    monkeypatch.setattr(
        Supervisor, "_implement", lambda self, role, state, context: seen.append(state)
    )
    Supervisor(checkpoint.config, checkpoint.invoker).run_role("mobile")
    assert len(seen) == 1
    assert seen[0].task.task_id == "queued-authorized-work"
    assert seen[0].planning_input_fingerprint == ""
    assert queue.read() == []
    assert len(checkpoint.prompts) == 1
    assert not (checkpoint.root / "completion/mobile-acceptance.json").exists()


@pytest.mark.parametrize("legacy", [False, True])
def test_same_head_new_lease_cannot_inherit_consumed_planning_authority(
    planning_checkpoint, legacy
):
    from scripts.dev_orchestrator.handoff import acknowledge_handoff, validate_handoff

    checkpoint = planning_checkpoint
    checkpoint.supervisor.run_role("mobile")
    if legacy:
        store = AtomicJsonStore(checkpoint.root / "state/mobile.json")
        state = store.read()
        state.pop("planning_input_fingerprint")
        store.write(state)
    held = checkpoint.supervisor.load_state("mobile")
    handoff_path = checkpoint.root / "handoff/mobile.json"
    previous = AtomicJsonStore(handoff_path).read()
    assert previous["last_invocation_id"] == held.last_result_invocation_id
    acknowledge_handoff(checkpoint.config, "mobile", source="test")
    validate_handoff(checkpoint.config, "mobile")
    current = AtomicJsonStore(handoff_path).read()
    assert current["head"] == previous["head"] == checkpoint.head
    assert current["lease_id"] != previous["lease_id"]
    assert "last_invocation_id" not in current
    current_bytes = handoff_path.read_bytes()
    peer_request(checkpoint)
    checkpoint.outcome = "assign"
    Supervisor(checkpoint.config, checkpoint.invoker).run_role("mobile")
    assert len(checkpoint.prompts) == 1
    assert checkpoint.supervisor.load_state("mobile") == held
    assert handoff_path.read_bytes() == current_bytes


def test_old_taskless_model_dependency_does_not_self_wake_on_scope_normalization(
    planning_checkpoint, monkeypatch
):
    checkpoint = planning_checkpoint

    def old_fingerprint(packet, role, phase, state, head):
        payload = {
            "schema_version": 1,
            "role": role,
            "phase": phase,
            "task": state.task.to_dict() if state.task else None,
            "task_base_head": state.task_base_head,
            "head": head,
            "external_sources": sorted(packet.external_sources),
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("utf-8")
        ).hexdigest()

    checkpoint.outcome = "blocked_system"
    checkpoint.supervisor.save_state(WorkerState("mobile", task_base_head=checkpoint.head))
    with monkeypatch.context() as legacy:
        legacy.setattr(WorkerContext, "fingerprint", old_fingerprint)
        checkpoint.supervisor.run_role("mobile")
    held = checkpoint.supervisor.load_state("mobile")
    assert held.blocker_kind == "model_dependency"
    for _ in range(2):
        Supervisor(checkpoint.config, checkpoint.invoker).run_role("mobile")
    assert len(checkpoint.prompts) == 1
    assert checkpoint.supervisor.load_state("mobile") == held
    peer_request(checkpoint)
    checkpoint.outcome = "assign"
    Supervisor(checkpoint.config, checkpoint.invoker).run_role("mobile")
    assert len(checkpoint.prompts) == 2
    _assert_assigned(checkpoint)


@pytest.mark.parametrize("corruption", [None, "foreign_role", "input_bytes"])
def test_consumed_plan_cleanup_after_final_preparation_interruption(
    planning_checkpoint, monkeypatch, corruption
):
    checkpoint = planning_checkpoint
    real_clear = supervisor_module.clear_pending_result
    interrupted = False

    def clear(config, role, *, invocation_id):
        nonlocal interrupted
        if not interrupted:
            interrupted = True
            raise OSError("interrupt after final-preparation state, before journal clear")
        return real_clear(config, role, invocation_id=invocation_id)

    monkeypatch.setattr(supervisor_module, "clear_pending_result", clear)
    checkpoint.supervisor.run_role("mobile")
    assert interrupted
    assert len(checkpoint.prompts) == 1
    pending = read_pending_result(checkpoint.config, "mobile")
    invocation = checkpoint.results[0].invocation_id
    assert pending["invocation_id"] == invocation
    assert checkpoint.supervisor.load_state("mobile").last_result_invocation_id == invocation
    Supervisor(checkpoint.config, checkpoint.invoker).run_role("mobile")
    held = checkpoint.supervisor.load_state("mobile")
    assert held.blocker_kind == "acceptance"
    assert read_pending_result(checkpoint.config, "mobile") is not None
    messages = tuple((checkpoint.root / "mailbox/supervisor").glob("MSG-*.md"))
    assert len(messages) == 1
    original = messages[0].read_bytes()
    if corruption == "foreign_role":
        store = AtomicJsonStore(checkpoint.root / "handoff/mobile.json")
        value = store.read()
        value["pending_result"]["role"] = "backend"
        store.write(value)
    elif corruption == "input_bytes":
        supplied = pending["input_provenance"]["supplied_input"]
        with (checkpoint.root / supplied["path"]).open("ab") as handle:
            handle.write(b"CORRUPTED")
    pending_before = read_pending_result(checkpoint.config, "mobile")
    peer_request(checkpoint)
    checkpoint.outcome = "assign"
    Supervisor(checkpoint.config, checkpoint.invoker).run_role("mobile")
    if corruption:
        assert len(checkpoint.prompts) == 1
        assert checkpoint.supervisor.load_state("mobile") == held
        assert read_pending_result(checkpoint.config, "mobile") == pending_before
    else:
        assert len(checkpoint.prompts) == 2
        _assert_assigned(checkpoint)
    assert messages[0].read_bytes() == original


def test_context_read_failure_after_publication_retains_original_plan_result(
    planning_checkpoint, monkeypatch
):
    checkpoint = planning_checkpoint
    fail_context = False
    real_context = Supervisor._worker_context

    def changed_during_model():
        nonlocal fail_context
        peer_request(checkpoint)
        fail_context = True

    def context(self, role, state):
        if fail_context:
            raise OSError("new context temporarily unavailable")
        return real_context(self, role, state)

    checkpoint.after_model = changed_during_model
    monkeypatch.setattr(Supervisor, "_worker_context", context)
    checkpoint.supervisor.run_role("mobile")
    assert len(checkpoint.prompts) == 1
    pending = read_pending_result(checkpoint.config, "mobile")
    assert pending["result"]["outcome"] == "done"
    original_baseline = pending["input_provenance"]["fingerprint"]
    messages = tuple((checkpoint.root / "mailbox/supervisor").glob("MSG-*.md"))
    assert len(messages) == 1
    original = messages[0].read_bytes()
    fail_context = False
    checkpoint.after_model = None
    Supervisor(checkpoint.config, checkpoint.invoker).run_role("mobile")
    state = checkpoint.supervisor.load_state("mobile")
    assert state.status is WorkerStatus.IDLE
    assert state.planning_input_fingerprint == original_baseline
    assert state.last_result_invocation_id == pending["invocation_id"]
    assert read_pending_result(checkpoint.config, "mobile") is None
    assert len(checkpoint.prompts) == 1
    assert messages[0].read_bytes() == original
    assert checkpoint.final_gates == 0


def test_done_without_original_input_provenance_cannot_declare_completion(
    planning_checkpoint, monkeypatch
):
    checkpoint = planning_checkpoint
    real_accept = Supervisor._accept_result

    def interrupted(*args, **kwargs):
        raise OSError("interrupt before result acceptance")

    monkeypatch.setattr(Supervisor, "_accept_result", interrupted)
    checkpoint.supervisor.run_role("mobile")
    handoff = AtomicJsonStore(checkpoint.root / "handoff/mobile.json")
    record = handoff.read()
    record["pending_result"].pop("input_provenance")
    handoff.write(record)
    before = record["pending_result"]
    monkeypatch.setattr(Supervisor, "_accept_result", real_accept)
    Supervisor(checkpoint.config, checkpoint.invoker).run_role("mobile")
    assert len(checkpoint.prompts) == 1
    assert checkpoint.final_gates == 0
    assert read_pending_result(checkpoint.config, "mobile") == before
    state = checkpoint.supervisor.load_state("mobile")
    assert state.status is WorkerStatus.BLOCKED_SYSTEM
    assert "planning input provenance" in state.blocked_reason
    assert not list((checkpoint.root / "mailbox/supervisor").glob("MSG-*.md"))


def test_consumed_final_review_cleanup_preserves_new_acceptance_failure(
    final_checkpoint, monkeypatch
):
    checkpoint = final_checkpoint
    root = checkpoint.config.coordination_root
    real_clear = supervisor_module.clear_pending_result
    interrupted = False

    def clear(config, role, *, invocation_id):
        nonlocal interrupted
        if not interrupted:
            interrupted = True
            raise OSError("interrupt after acceptance state, before final-review journal clear")
        return real_clear(config, role, invocation_id=invocation_id)

    def invalidate_acceptance():
        store = AtomicJsonStore(root / "completion/backend-acceptance.json")
        value = store.read()
        value["physical"]["confirmed"] = False
        store.write(value)

    checkpoint.callback = invalidate_acceptance
    monkeypatch.setattr(supervisor_module, "clear_pending_result", clear)
    with pytest.raises(OSError, match="after acceptance state"):
        checkpoint.supervisor._done_claim(
            "backend", WorkerState("backend", status=WorkerStatus.PLANNING), "final context"
        )
    held = checkpoint.supervisor.load_state("backend")
    pending = read_pending_result(checkpoint.config, "backend")
    assert held.blocker_kind == "acceptance"
    assert held.task is None
    assert pending["phase"] == "review"
    assert pending["task_id"] == "FINAL-VERIFY-backend"
    assert pending["invocation_id"] == held.last_result_invocation_id
    assert len(checkpoint.prompts) == 1
    Supervisor(checkpoint.config, checkpoint.invoker).run_role("backend")
    assert read_pending_result(checkpoint.config, "backend") is None
    assert checkpoint.supervisor.load_state("backend") == held
    assert len(checkpoint.prompts) == 1
    assert (
        AtomicJsonStore(root / "completion/backend-acceptance.json").read()["physical"]["confirmed"]
        is False
    )
