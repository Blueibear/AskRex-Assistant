from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from dataclasses import replace
from types import SimpleNamespace

import pytest

from scripts.dev_orchestrator import completion
from scripts.dev_orchestrator import supervisor as supervisor_module
from scripts.dev_orchestrator.cli import save_config
from scripts.dev_orchestrator.handoff import (
    acknowledge_handoff,
    read_pending_result,
    validate_handoff,
)
from scripts.dev_orchestrator.storage import AtomicJsonStore
from scripts.dev_orchestrator.types import TaskItem, WorkerState, WorkerStatus
from tests.scripts.test_dev_orchestrator_completion import _config
from tests.scripts.test_dev_orchestrator_final_review import final_checkpoint as final_checkpoint
from tests.scripts.test_dev_orchestrator_planning_wake import (
    planning_checkpoint as planning_checkpoint,
)


def _missing_ci_and_pr(monkeypatch):
    real_run = completion._run
    calls = []

    def observed_run(repo, *args):
        if args[:3] == ("gh", "pr", "view"):
            calls.append(args)
            return subprocess.CompletedProcess(
                args, 1, "", 'no pull requests found for branch "task"'
            )
        if args[:3] == ("gh", "pr", "list"):
            calls.append(args)
            return subprocess.CompletedProcess(args, 0, "[]", "")
        return real_run(repo, *args)

    real_which = completion.shutil.which
    monkeypatch.setattr(
        completion.shutil, "which", lambda name: "gh" if name == "gh" else real_which(name)
    )
    monkeypatch.setattr(completion, "_run", observed_run)
    monkeypatch.setattr(completion, "_ci_workflow_present", lambda repo: False)
    return calls


def test_mixed_completion_preserves_local_work_and_human_requirements(tmp_path, monkeypatch):
    config = _config(tmp_path, "mobile")
    _missing_ci_and_pr(monkeypatch)

    gate = completion.evaluate_completion(config, "mobile")

    assert gate.ready is False
    assert gate.needs_user is True
    assert gate.blocker_kind == "acceptance"
    assert any("physical acceptance" in reason for reason in gate.reasons)
    assert any("documentation truth" in reason for reason in gate.reasons)
    assert gate.local_remediation == ("missing_ci_workflow",)
    assert gate.publication_reasons
    assert not (config.coordination_root / "completion/mobile-acceptance.json").exists()


def test_done_with_missing_ci_assigns_local_repair_before_physical_hold(
    planning_checkpoint, monkeypatch
):
    checkpoint = planning_checkpoint
    _missing_ci_and_pr(monkeypatch)
    monkeypatch.setattr(
        supervisor_module,
        "prepare_final_review",
        lambda config, role, **kwargs: completion._completion_preflight(config, role),
    )

    checkpoint.supervisor.run_role("mobile")

    state = checkpoint.supervisor.load_state("mobile")
    assert state.status is WorkerStatus.IMPLEMENTING
    assert state.task.task_id == "FINAL-mobile-ci-workflow"
    assert "CI workflow" in state.task.prompt
    assert "physical" in state.task.prompt
    assert "Do not push" in state.task.prompt
    assert state.last_result_invocation_id == checkpoint.results[0].invocation_id
    assert len(checkpoint.prompts) == 1
    assert read_pending_result(checkpoint.config, "mobile") is None
    assert not (checkpoint.root / "completion/mobile-acceptance.json").exists()


def test_legacy_mixed_hold_classifies_once_without_replaying_consumed_plan(
    planning_checkpoint, monkeypatch
):
    checkpoint = planning_checkpoint
    checkpoint.supervisor.run_role("mobile")
    store = AtomicJsonStore(checkpoint.root / "state/mobile.json")
    old = store.read()
    old.pop("completion_preflight_fingerprint", None)
    store.write(old)
    original_invocation = old["last_result_invocation_id"]
    message = next((checkpoint.root / "mailbox/supervisor").glob("MSG-*.md"))
    message_bytes = message.read_bytes()
    _missing_ci_and_pr(monkeypatch)
    calls = []

    def preflight(config, role):
        calls.append(role)
        return completion._completion_preflight(config, role)

    monkeypatch.setattr(supervisor_module, "completion_preflight", preflight, raising=False)
    checkpoint.supervisor.run_role("mobile")

    state = checkpoint.supervisor.load_state("mobile")
    assert state.status is WorkerStatus.IMPLEMENTING
    assert state.task.task_id == "FINAL-mobile-ci-workflow"
    assert state.last_result_invocation_id == original_invocation
    assert calls == ["mobile"]
    assert len(checkpoint.prompts) == 1
    assert message.read_bytes() == message_bytes
    assert len(list((checkpoint.root / "mailbox/supervisor").glob("MSG-*.md"))) == 1
    assert read_pending_result(checkpoint.config, "mobile") is None
    assert not (checkpoint.root / "completion/mobile-acceptance.json").exists()


def _pure_acceptance(config, role):
    return completion.CompletionGate(
        False,
        ("iPhone physical acceptance is required", "documentation truth acceptance is required"),
        needs_user=True,
        blocker_kind="acceptance",
        acceptance_reasons=("iPhone physical acceptance is required",),
    )


def _legacy_hold(checkpoint):
    checkpoint.supervisor.run_role("mobile")
    store = AtomicJsonStore(checkpoint.root / "state/mobile.json")
    raw = store.read()
    raw.pop("completion_preflight_fingerprint", None)
    store.write(raw)
    return checkpoint.supervisor.load_state("mobile")


def test_legacy_pure_acceptance_classifies_once_without_model_or_acceptance(
    planning_checkpoint, monkeypatch
):
    checkpoint = planning_checkpoint
    _legacy_hold(checkpoint)
    calls = []

    def preflight(config, role):
        calls.append(role)
        return _pure_acceptance(config, role)

    monkeypatch.setattr(supervisor_module, "completion_preflight", preflight)
    for _ in range(3):
        checkpoint.supervisor.run_role("mobile")
    state = checkpoint.supervisor.load_state("mobile")
    assert state.status is WorkerStatus.BLOCKED_USER
    assert state.blocker_kind == "acceptance"
    assert state.task is None
    assert len(checkpoint.prompts) == 1
    assert calls == ["mobile"]
    assert len(state.completion_preflight_fingerprint) == 64
    assert not (checkpoint.root / "completion/mobile-acceptance.json").exists()


def test_material_acceptance_change_rechecks_but_peer_invocation_churn_does_not(
    planning_checkpoint, monkeypatch
):
    checkpoint = planning_checkpoint
    _legacy_hold(checkpoint)
    calls = []

    def preflight(config, role):
        calls.append(role)
        return _pure_acceptance(config, role)

    monkeypatch.setattr(supervisor_module, "completion_preflight", preflight)
    checkpoint.supervisor.run_role("mobile")
    handoff = AtomicJsonStore(checkpoint.root / "handoff/backend.json")
    peer = handoff.read()
    peer["last_invocation_id"] = "unrelated-peer-plan"
    peer["advanced_at"] = "2026-11-18T20:00:00+00:00"
    handoff.write(peer)
    checkpoint.supervisor.run_role("mobile")
    assert calls == ["mobile"]
    acceptance = AtomicJsonStore(checkpoint.root / "completion/mobile-acceptance.json")
    acceptance.write({"physical": {"confirmed": False, "evidence": "still not physically tested"}})
    checkpoint.supervisor.run_role("mobile")
    assert calls == ["mobile", "mobile"]
    assert len(checkpoint.prompts) == 1
    assert checkpoint.supervisor.load_state("mobile").status is WorkerStatus.BLOCKED_USER


@pytest.mark.parametrize("damage", ["missing_input", "replaced_lease", "foreign_pending"])
def test_legacy_reclassification_refuses_unproven_or_unresolved_lineage(
    planning_checkpoint, monkeypatch, damage
):
    checkpoint = planning_checkpoint
    held = _legacy_hold(checkpoint)
    if damage == "missing_input":
        digest = hashlib.sha256(held.last_result_invocation_id.encode()).hexdigest()
        (checkpoint.root / "model-inputs" / f"mobile-{digest}" / "input.txt").unlink()
    elif damage == "replaced_lease":
        acknowledge_handoff(checkpoint.config, "mobile", source="test")
        validate_handoff(checkpoint.config, "mobile")
    else:
        handoff = AtomicJsonStore(checkpoint.root / "handoff/mobile.json")
        value = handoff.read()
        value["pending_result"] = {"invocation_id": "foreign-unconsumed-invocation"}
        handoff.write(value)
    state_before = checkpoint.supervisor.load_state("mobile")
    handoff_before = (checkpoint.root / "handoff/mobile.json").read_bytes()
    calls = []
    monkeypatch.setattr(supervisor_module, "completion_preflight", lambda *args: calls.append(args))
    checkpoint.supervisor.run_role("mobile")
    assert not calls
    assert checkpoint.supervisor.load_state("mobile") == state_before
    assert (checkpoint.root / "handoff/mobile.json").read_bytes() == handoff_before
    assert len(checkpoint.prompts) == 1


def test_migration_state_save_interruption_retries_without_replaying_the_plan(
    planning_checkpoint, monkeypatch
):
    checkpoint = planning_checkpoint
    held = _legacy_hold(checkpoint)
    _missing_ci_and_pr(monkeypatch)
    monkeypatch.setattr(supervisor_module, "completion_preflight", completion._completion_preflight)
    before = checkpoint.supervisor.load_state("mobile")
    original = checkpoint.supervisor.save_state

    def interrupted(state):
        if state.task and state.task.task_id == "FINAL-mobile-ci-workflow":
            raise OSError("interrupted before remediation state commit")
        return original(state)

    monkeypatch.setattr(checkpoint.supervisor, "save_state", interrupted)
    checkpoint.supervisor.run_role("mobile")
    assert checkpoint.supervisor.load_state("mobile") == before
    assert read_pending_result(checkpoint.config, "mobile") is None
    monkeypatch.setattr(checkpoint.supervisor, "save_state", original)
    checkpoint.supervisor.run_role("mobile")
    state = checkpoint.supervisor.load_state("mobile")
    assert state.task.task_id == "FINAL-mobile-ci-workflow"
    assert state.last_result_invocation_id == held.last_result_invocation_id
    assert len(checkpoint.prompts) == 1


@pytest.mark.parametrize("descendant", [False, True])
def test_accepted_ci_obligation_is_not_reassigned_at_same_or_descendant_head(
    planning_checkpoint, monkeypatch, descendant
):
    checkpoint = planning_checkpoint
    _missing_ci_and_pr(monkeypatch)
    monkeypatch.setattr(
        supervisor_module,
        "prepare_final_review",
        lambda config, role, **kwargs: completion._completion_preflight(config, role),
    )
    checkpoint.supervisor.run_role("mobile")
    assigned = checkpoint.supervisor.load_state("mobile")
    checkpoint.supervisor.completed_tasks.record(
        "mobile",
        assigned.task,
        completed_head=checkpoint.head,
        invocation_id="independent-ci-review",
        source="independent_review",
    )
    ledger_before = (checkpoint.root / "completed-tasks/mobile.json").read_bytes()
    if descendant:
        (checkpoint.repo / "later-source.txt").write_text(
            "unrelated later task\n", encoding="utf-8"
        )
        subprocess.run(["git", "add", "later-source.txt"], cwd=checkpoint.repo, check=True)
        subprocess.run(
            ["git", "commit", "-qm", "test: later unrelated checkpoint"],
            cwd=checkpoint.repo,
            check=True,
        )
        acknowledge_handoff(checkpoint.config, "mobile", source="test")
    checkpoint.supervisor.save_state(WorkerState("mobile"))
    checkpoint.supervisor.run_role("mobile")
    state = checkpoint.supervisor.load_state("mobile")
    assert state.status is WorkerStatus.BLOCKED_SYSTEM
    assert state.blocker_kind == "completion_remediation"
    assert state.task is None
    assert "Reviewed completion obligation remains unsatisfied" in state.blocked_reason
    assert (checkpoint.root / "completed-tasks/mobile.json").read_bytes() == ledger_before
    for _ in range(3):
        checkpoint.supervisor.run_role("mobile")
    assert len(checkpoint.prompts) == 2


@pytest.mark.parametrize(
    "kind,with_task",
    [
        ("human", False),
        ("auth", False),
        ("github_auth", False),
        ("retest", True),
        ("acceptance", True),
    ],
)
def test_task_specific_and_human_gates_do_not_enter_local_remediation(
    planning_checkpoint, monkeypatch, kind, with_task
):
    checkpoint = planning_checkpoint
    held = _legacy_hold(checkpoint)
    protected = replace(
        held,
        blocker_kind=kind,
        task=TaskItem("TEST-999", "Await required testing") if with_task else None,
    )
    checkpoint.supervisor.save_state(protected)
    before = (checkpoint.root / "state/mobile.json").read_bytes()
    calls = []
    monkeypatch.setattr(supervisor_module, "completion_preflight", lambda *args: calls.append(args))
    checkpoint.supervisor.run_role("mobile")
    assert not calls
    assert (checkpoint.root / "state/mobile.json").read_bytes() == before
    assert len(checkpoint.prompts) == 1


@pytest.mark.parametrize("failure", ["network", "malformed", "wrong_shape", "existing_unknown"])
def test_unknown_github_view_failure_cannot_authorize_publication(tmp_path, monkeypatch, failure):
    config = _config(tmp_path, "mobile")
    _missing_ci_and_pr(monkeypatch)
    original = completion._run

    def observed_run(repo, *args):
        if args[:3] == ("gh", "pr", "list"):
            replies = {
                "network": (1, "", "connection timeout"),
                "malformed": (0, "not-json", ""),
                "wrong_shape": (0, "{}", ""),
                "existing_unknown": (0, '[{"state":"OPEN"}]', ""),
            }
            code, stdout, stderr = replies[failure]
            return subprocess.CompletedProcess(args, code, stdout, stderr)
        return original(repo, *args)

    monkeypatch.setattr(completion, "_run", observed_run)
    gate = completion.evaluate_completion(config, "mobile")
    assert not gate.ready
    assert gate.system_error
    assert gate.publication_reasons == ()
    assert gate.local_remediation == ("missing_ci_workflow",)


def _publication_gate(checkpoint, monkeypatch):
    _missing_ci_and_pr(monkeypatch)
    monkeypatch.setattr(completion, "_ci_workflow_present", lambda repo: True)
    monkeypatch.setattr(
        supervisor_module,
        "prepare_final_review",
        lambda config, role, **kwargs: completion._completion_preflight(config, role),
    )


@pytest.mark.parametrize(
    "status,kind,worker_status",
    [
        ("waiting", "host_publication", WorkerStatus.BLOCKED_SYSTEM),
        ("system_error", "host_publication_system", WorkerStatus.BLOCKED_SYSTEM),
    ],
)
def test_publication_waits_reconcile_without_scratch_models_or_pending_results(
    planning_checkpoint, monkeypatch, status, kind, worker_status
):
    checkpoint = planning_checkpoint
    _publication_gate(checkpoint, monkeypatch)
    calls = []

    def publisher(role, acceptance, *, resume_denied=False):
        current = checkpoint.supervisor.load_state(role)
        assert current.task.task_id == "FINAL-VERIFY-mobile"
        assert read_pending_result(checkpoint.config, role) is None
        assert current.last_result_invocation_id == checkpoint.results[0].invocation_id
        assert acceptance and any("physical" in item for item in acceptance)
        calls.append(role)
        return SimpleNamespace(status=status, reason="Actual host publication is not ready")

    monkeypatch.setattr(checkpoint.supervisor, "_publish_completion", publisher)
    checkpoint.supervisor.run_role("mobile")
    state = checkpoint.supervisor.load_state("mobile")
    assert state.status is worker_status
    assert state.blocker_kind == kind
    assert state.task.task_id == "FINAL-VERIFY-mobile"
    checkpoint.supervisor.run_role("mobile")
    assert calls == ["mobile", "mobile"]
    assert len(checkpoint.prompts) == 1
    assert read_pending_result(checkpoint.config, "mobile") is None
    assert not (checkpoint.root / "completion/mobile-acceptance.json").exists()


def test_pause_after_done_prevents_host_publication_but_consumes_valid_result(
    planning_checkpoint, monkeypatch
):
    checkpoint = planning_checkpoint
    _publication_gate(checkpoint, monkeypatch)
    checkpoint.after_model = lambda: save_config(replace(checkpoint.config, observe_only=True))
    calls = []
    monkeypatch.setattr(
        checkpoint.supervisor, "_publish_completion", lambda *args: calls.append(args)
    )
    checkpoint.supervisor.run_role("mobile")
    assert not calls
    assert read_pending_result(checkpoint.config, "mobile") is None
    state = checkpoint.supervisor.load_state("mobile")
    assert state.blocker_kind == "host_publication"
    assert state.last_result_invocation_id == checkpoint.results[0].invocation_id
    checkpoint.supervisor.run_role("mobile")
    assert not calls
    assert len(checkpoint.prompts) == 1


def test_host_publication_does_not_hold_global_controller_lock(planning_checkpoint, monkeypatch):
    checkpoint = planning_checkpoint
    _publication_gate(checkpoint, monkeypatch)
    observed = []

    def publisher(role, acceptance, *, resume_denied=False):
        command = (
            "from pathlib import Path; "
            "from scripts.dev_orchestrator.lifecycle import ControlPlaneLock; "
            "p=Path(" + repr(str(checkpoint.root / "control-plane.lock")) + "); "
            "lock=ControlPlaneLock(p); lock.__enter__(); lock.__exit__(None,None,None)"
        )
        completed = subprocess.run(
            [sys.executable, "-c", command], capture_output=True, text=True, timeout=10
        )
        assert completed.returncode == 0, completed.stderr
        observed.append(role)
        return SimpleNamespace(status="waiting", reason="publication remains pending")

    monkeypatch.setattr(checkpoint.supervisor, "_publish_completion", publisher)
    checkpoint.supervisor.run_role("mobile")
    assert observed == ["mobile"]
    assert len(checkpoint.prompts) == 1


@pytest.mark.parametrize("waiting_ci", [False, True])
def test_published_draft_never_confirms_human_acceptance_and_ci_wait_is_host_only(
    planning_checkpoint, monkeypatch, waiting_ci
):
    checkpoint = planning_checkpoint
    _publication_gate(checkpoint, monkeypatch)
    original = completion._run
    published = False
    checks_green = not waiting_ci
    lease = AtomicJsonStore(checkpoint.root / "handoff/mobile.json").read()
    payload = {
        "state": "OPEN",
        "headRefOid": checkpoint.head,
        "headRefName": lease["branch"],
        "baseRefName": completion.COMPLETION_MANIFESTS["mobile"].base_branch,
    }

    def observed_run(repo, *args):
        if published and args[:3] == ("gh", "pr", "view"):
            checks = (
                [
                    {"name": name, "conclusion": "SUCCESS"}
                    for name in completion.COMPLETION_MANIFESTS["mobile"].required_checks
                ]
                if checks_green
                else []
            )
            return subprocess.CompletedProcess(
                args, 0, json.dumps({**payload, "statusCheckRollup": checks}), ""
            )
        return original(repo, *args)

    monkeypatch.setattr(completion, "_run", observed_run)
    calls = []

    def publisher(role, acceptance, *, resume_denied=False):
        nonlocal published
        published = True
        calls.append(role)
        return SimpleNamespace(status="published", reason="Actual mock host draft exists")

    monkeypatch.setattr(checkpoint.supervisor, "_publish_completion", publisher)
    checkpoint.supervisor.run_role("mobile")
    state = checkpoint.supervisor.load_state("mobile")
    if waiting_ci:
        assert state.blocker_kind == "completion_ci_wait"
        checkpoint.supervisor.run_role("mobile")
        assert checkpoint.supervisor.load_state("mobile").blocker_kind == "completion_ci_wait"
        checks_green = True
        checkpoint.supervisor.run_role("mobile")
        state = checkpoint.supervisor.load_state("mobile")
    assert state.status is WorkerStatus.BLOCKED_USER
    assert state.blocker_kind == "acceptance"
    assert state.task is None
    assert calls == ["mobile"]
    assert len(checkpoint.prompts) == 1
    assert read_pending_result(checkpoint.config, "mobile") is None
    assert not (checkpoint.root / "completion/mobile-acceptance.json").exists()


def test_legacy_consumed_final_review_can_reclassify_without_rerunning_gates(
    final_checkpoint, monkeypatch
):
    checkpoint = final_checkpoint
    root = checkpoint.config.coordination_root

    def physical_still_missing():
        store = AtomicJsonStore(root / "completion/backend-acceptance.json")
        payload = store.read()
        payload["physical"]["confirmed"] = False
        store.write(payload)

    checkpoint.callback = physical_still_missing
    checkpoint.supervisor._done_claim(
        "backend", WorkerState("backend", status=WorkerStatus.PLANNING), "final context"
    )
    held = checkpoint.supervisor.load_state("backend")
    assert held.status is WorkerStatus.BLOCKED_USER
    assert held.blocker_kind == "acceptance"
    assert len(checkpoint.prompts) == 1
    assert checkpoint.local_runs == 1
    receipt = root / "validation/backend-FINAL-VERIFY-backend.json"
    receipt_before = receipt.read_bytes()
    state_store = AtomicJsonStore(root / "state/backend.json")
    raw = state_store.read()
    raw.pop("completion_preflight_fingerprint", None)
    state_store.write(raw)
    _missing_ci_and_pr(monkeypatch)
    monkeypatch.setattr(supervisor_module, "completion_preflight", completion._completion_preflight)

    checkpoint.supervisor.run_role("backend")

    current = checkpoint.supervisor.load_state("backend")
    assert current.status is WorkerStatus.IMPLEMENTING
    assert current.task.task_id == "FINAL-backend-ci-workflow"
    assert current.last_result_invocation_id == held.last_result_invocation_id
    assert len(checkpoint.prompts) == 1
    assert checkpoint.local_runs == 1
    assert receipt.read_bytes() == receipt_before
    assert read_pending_result(checkpoint.config, "backend") is None


def test_ci_task_prompt_carries_exact_required_check_names_and_local_contract(
    planning_checkpoint, monkeypatch
):
    checkpoint = planning_checkpoint
    _missing_ci_and_pr(monkeypatch)
    monkeypatch.setattr(
        supervisor_module,
        "prepare_final_review",
        lambda config, role, **kwargs: completion._completion_preflight(config, role),
    )
    checkpoint.supervisor.run_role("mobile")
    prompt = checkpoint.supervisor.load_state("mobile").task.prompt
    manifest = completion.COMPLETION_MANIFESTS["mobile"]
    for name in manifest.required_checks:
        assert name in prompt
    for gate in manifest.local_gates:
        assert gate.name in prompt
        assert str(list(gate.command)) in prompt
        assert "cwd=" + gate.cwd in prompt
    assert "additional required CI verification" in prompt
    assert "trusted host owns draft publication and canonical validation" in prompt


@pytest.mark.parametrize("optional", ["FAILURE", "CANCELLED", "SKIPPED"])
@pytest.mark.parametrize("required_pending", [False, True])
def test_optional_checks_do_not_create_required_ci_remediation(
    tmp_path, monkeypatch, optional, required_pending
):
    from tests.scripts.test_dev_orchestrator_completion import _mock_pr, _success_checks

    config = _config(tmp_path, "mobile")
    checks = _success_checks("mobile")
    if required_pending:
        checks[0]["conclusion"] = "PENDING"
    checks.append({"name": "Optional experimental audit", "conclusion": optional})
    _mock_pr(monkeypatch, config, "mobile", statusCheckRollup=checks)

    gate = completion.completion_preflight(config, "mobile")

    expected = (
        ("ci_blocking_checks",)
        if not required_pending and optional in {"FAILURE", "CANCELLED"}
        else ()
    )
    assert gate.local_remediation == expected
    assert "ci_checks" not in gate.local_remediation
    assert gate.waiting_for_ci is required_pending
    assert not gate.ready
    assert gate.needs_user
    if optional in {"FAILURE", "CANCELLED"}:
        assert any("CI contains failing checks" in reason for reason in gate.reasons)


def test_publication_auth_waits_for_existing_explicit_resume(planning_checkpoint, monkeypatch):
    from scripts.dev_orchestrator.cli import resume_confirmed_human_blocker

    checkpoint = planning_checkpoint
    _publication_gate(checkpoint, monkeypatch)
    calls = []

    def publisher(role, acceptance, *, resume_denied=False):
        calls.append(role)
        return SimpleNamespace(status="auth_required", reason="Confirmed workflow scope denial")

    monkeypatch.setattr(checkpoint.supervisor, "_publish_completion", publisher)
    checkpoint.supervisor.run_role("mobile")
    held = checkpoint.supervisor.load_state("mobile")
    assert held.status is WorkerStatus.BLOCKED_USER
    assert held.blocker_kind == "github_auth"
    assert held.resume_status is WorkerStatus.IMPLEMENTING
    assert held.task.task_id == "FINAL-VERIFY-mobile"
    assert held.completion_host_action == "publication"
    for _ in range(3):
        checkpoint.supervisor.run_role("mobile")
    assert calls == ["mobile"]
    assert checkpoint.supervisor.load_state("mobile") == held
    resume_confirmed_human_blocker(checkpoint.root, "mobile", "github_auth")
    checkpoint.supervisor.run_role("mobile")
    assert calls == ["mobile", "mobile"]
    assert checkpoint.supervisor.load_state("mobile").blocker_kind == "github_auth"
    assert len(checkpoint.prompts) == 1
    assert read_pending_result(checkpoint.config, "mobile") is None


def test_required_failure_identity_uses_source_content_and_typed_check_facts(tmp_path, monkeypatch):
    from tests.scripts.test_dev_orchestrator_completion import _mock_pr, _success_checks

    config = _config(tmp_path, "mobile")
    repo = config.mobile_root
    checks = _success_checks("mobile")
    checks[0]["conclusion"] = "FAILURE"

    def observed_gate():
        _mock_pr(monkeypatch, config, "mobile", statusCheckRollup=checks)
        return completion.completion_preflight(config, "mobile")

    original = observed_gate()
    assert original.local_remediation == ("ci_checks",)
    assert len(original.remediation_fingerprint) == 64
    subprocess.run(
        ["git", "commit", "--allow-empty", "-qm", "test: no source content change"],
        cwd=repo,
        check=True,
    )
    acknowledge_handoff(config, "mobile", source="test")
    same_source = observed_gate()
    assert same_source.head != original.head
    assert same_source.remediation_fingerprint == original.remediation_fingerprint

    (repo / "changed-source.txt").write_text(
        "new source with a later observed failure\n", encoding="utf-8"
    )
    subprocess.run(["git", "add", "changed-source.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "test: changed material source"], cwd=repo, check=True)
    acknowledge_handoff(config, "mobile", source="test")
    changed_source = observed_gate()
    assert changed_source.remediation_fingerprint != original.remediation_fingerprint

    checks[0]["conclusion"] = "SUCCESS"
    checks[1]["conclusion"] = "FAILURE"
    changed_failure = observed_gate()
    assert changed_failure.remediation_fingerprint != changed_source.remediation_fingerprint
    assert changed_failure.head == changed_source.head


def test_host_publication_settles_consumed_plan_after_preparation_clear_interruption(
    planning_checkpoint, monkeypatch
):
    checkpoint = planning_checkpoint
    _publication_gate(checkpoint, monkeypatch)
    original = supervisor_module.clear_pending_result
    failed_once = False

    def interrupted(config, role, **kwargs):
        nonlocal failed_once
        if not failed_once:
            failed_once = True
            raise OSError("interrupted after accepted preparation state before pending cleanup")
        return original(config, role, **kwargs)

    monkeypatch.setattr(supervisor_module, "clear_pending_result", interrupted)
    checkpoint.supervisor.run_role("mobile")
    state = checkpoint.supervisor.load_state("mobile")
    assert state.last_result_invocation_id == checkpoint.results[0].invocation_id
    assert read_pending_result(checkpoint.config, "mobile") is not None
    fresh = supervisor_module.Supervisor(checkpoint.config, checkpoint.invoker)
    calls = []

    def publisher(role, acceptance, *, resume_denied=False):
        assert read_pending_result(checkpoint.config, role) is None
        assert not resume_denied
        calls.append(role)
        return SimpleNamespace(status="waiting", reason="Mock host reconciliation remains pending")

    monkeypatch.setattr(fresh, "_publish_completion", publisher)
    fresh.run_role("mobile")
    assert calls == ["mobile"]
    assert len(checkpoint.prompts) == 1
    assert read_pending_result(checkpoint.config, "mobile") is None
    assert fresh.load_state("mobile").blocker_kind == "host_publication"


def test_auth_resume_permission_is_consumed_before_each_denied_attempt(
    planning_checkpoint, monkeypatch
):
    from scripts.dev_orchestrator.cli import resume_confirmed_human_blocker

    class Interrupted(BaseException):
        pass

    checkpoint = planning_checkpoint
    _publication_gate(checkpoint, monkeypatch)
    permissions = []

    def publisher(role, acceptance, *, resume_denied=False):
        current = checkpoint.supervisor.load_state(role)
        assert current.status is WorkerStatus.BLOCKED_SYSTEM
        assert current.blocker_kind == "host_publication"
        assert current.completion_host_action == "publication"
        permissions.append(resume_denied)
        if len(permissions) == 2:
            raise Interrupted("after publisher records the second definitive denial")
        return SimpleNamespace(status="auth_required", reason="Confirmed workflow scope denial")

    monkeypatch.setattr(checkpoint.supervisor, "_publish_completion", publisher)
    checkpoint.supervisor.run_role("mobile")
    resume_confirmed_human_blocker(checkpoint.root, "mobile", "github_auth")
    with pytest.raises(Interrupted):
        checkpoint.supervisor.run_role("mobile")
    checkpoint.supervisor.run_role("mobile")
    assert permissions == [False, True, False]
    held = checkpoint.supervisor.load_state("mobile")
    assert held.status is WorkerStatus.BLOCKED_USER
    assert held.blocker_kind == "github_auth"
    checkpoint.supervisor.run_role("mobile")
    assert permissions == [False, True, False]
    assert len(checkpoint.prompts) == 1


def test_host_wait_after_final_review_preserves_canonical_receipt_and_captures(
    final_checkpoint, monkeypatch
):
    checkpoint = final_checkpoint
    root = checkpoint.config.coordination_root

    def pull_request_became_absent():
        _missing_ci_and_pr(monkeypatch)
        monkeypatch.setattr(completion, "_ci_workflow_present", lambda repo: True)

    checkpoint.callback = pull_request_became_absent
    calls = []

    def publisher(role, acceptance, *, resume_denied=False):
        assert read_pending_result(checkpoint.config, role) is None
        calls.append(role)
        return SimpleNamespace(status="waiting", reason="Host must reconcile prior publication")

    monkeypatch.setattr(checkpoint.supervisor, "_publish_completion", publisher)
    checkpoint.supervisor._done_claim(
        "backend", WorkerState("backend", status=WorkerStatus.PLANNING), "final context"
    )
    assert checkpoint.supervisor.load_state("backend").blocker_kind == "host_publication"
    receipt = root / "validation/backend-FINAL-VERIFY-backend.json"
    checks = root / "evidence/supervisor-checks/backend-FINAL-VERIFY-backend.json"
    before = {str(path): path.read_bytes() for path in (receipt, checks)}
    runs = checkpoint.local_runs
    host_checks = checkpoint.host_checks
    for _ in range(2):
        checkpoint.supervisor.run_role("backend")
    assert calls == ["backend", "backend", "backend"]
    assert checkpoint.local_runs == runs == 1
    assert checkpoint.host_checks == host_checks
    assert len(checkpoint.prompts) == 1
    assert {str(path): path.read_bytes() for path in (receipt, checks)} == before
    assert read_pending_result(checkpoint.config, "backend") is None


@pytest.mark.parametrize("legacy", [False, True])
def test_first_completion_observation_error_retries_without_a_model(
    planning_checkpoint, monkeypatch, legacy
):
    checkpoint = planning_checkpoint
    if legacy:
        _legacy_hold(checkpoint)
    _missing_ci_and_pr(monkeypatch)
    original = completion._run
    unavailable = True

    def observed_run(repo, *args):
        if args[:3] == ("gh", "pr", "list") and unavailable:
            return subprocess.CompletedProcess(
                args, 1, "", "connection timeout during read-only list"
            )
        return original(repo, *args)

    monkeypatch.setattr(completion, "_run", observed_run)
    monkeypatch.setattr(supervisor_module, "completion_preflight", completion._completion_preflight)
    monkeypatch.setattr(
        supervisor_module,
        "prepare_final_review",
        lambda config, role, **kwargs: completion._completion_preflight(config, role),
    )
    checkpoint.supervisor.run_role("mobile")
    held = checkpoint.supervisor.load_state("mobile")
    assert held.blocker_kind == "completion_observation_wait"
    assert held.status is WorkerStatus.BLOCKED_SYSTEM
    assert held.completion_host_action == "observe"
    assert held.task.task_id == "FINAL-VERIFY-mobile"
    assert "connection timeout" in held.blocked_reason
    checkpoint.supervisor.run_role("mobile")
    assert len(checkpoint.prompts) == 1
    assert read_pending_result(checkpoint.config, "mobile") is None
    unavailable = False
    checkpoint.supervisor.run_role("mobile")
    assigned = checkpoint.supervisor.load_state("mobile")
    assert assigned.task.task_id == "FINAL-mobile-ci-workflow"
    assert assigned.status is WorkerStatus.IMPLEMENTING
    assert assigned.completion_host_action == ""
    assert len(checkpoint.prompts) == 1


@pytest.mark.parametrize("conclusion,expected_wait", [("SUCCESS", False), ("IN_PROGRESS", True)])
def test_zero_required_names_distinguishes_completed_rollup_from_work_in_progress(
    tmp_path, monkeypatch, conclusion, expected_wait
):
    from tests.scripts.test_dev_orchestrator_completion import _mock_pr

    config = _config(tmp_path, "mobile")
    _mock_pr(
        monkeypatch,
        config,
        "mobile",
        statusCheckRollup=[{"name": "Unrelated optional job", "conclusion": conclusion}],
    )
    gate = completion.completion_preflight(config, "mobile")
    assert gate.waiting_for_ci is expected_wait
    assert gate.local_remediation == (() if expected_wait else ("ci_checks",))
    assert not gate.ready


@pytest.mark.parametrize("publication", [False, True])
def test_queued_work_keeps_priority_during_nonhuman_completion_wait(
    planning_checkpoint, monkeypatch, publication
):
    checkpoint = planning_checkpoint
    _publication_gate(checkpoint, monkeypatch)
    journal = checkpoint.root / "test-publication-journal.json"
    journal.write_bytes(b'{"status":"unresolved admitted host action"}\n')
    journal_before = journal.read_bytes()
    calls = []

    def publisher(role, acceptance, *, resume_denied=False):
        calls.append(role)
        return SimpleNamespace(status="waiting", reason="Query-only publication reconciliation")

    monkeypatch.setattr(checkpoint.supervisor, "_publish_completion", publisher)
    if not publication:
        monkeypatch.setattr(
            supervisor_module,
            "prepare_final_review",
            lambda config, role, **kwargs: completion.CompletionGate(
                False,
                ("Required CI checks are still pending",),
                waiting_for_ci=True,
                head=checkpoint.head,
            ),
        )
    checkpoint.supervisor.run_role("mobile")
    assert checkpoint.supervisor.load_state("mobile").task.task_id == "FINAL-VERIFY-mobile"
    original_calls = list(calls)
    checkpoint.supervisor.enqueue(
        "mobile", TaskItem("new-explicit-role-work", "Do this authorized work.")
    )
    checkpoint.supervisor.run_role("mobile")
    assigned = checkpoint.supervisor.load_state("mobile")
    assert assigned.status is WorkerStatus.IMPLEMENTING
    assert assigned.task.task_id == "new-explicit-role-work"
    assert assigned.completion_host_action == ""
    assert calls == original_calls
    assert len(checkpoint.prompts) == 1
    assert read_pending_result(checkpoint.config, "mobile") is None
    assert AtomicJsonStore(checkpoint.root / "queues/mobile.json").read() == []
    assert journal.read_bytes() == journal_before


@pytest.mark.parametrize("auth_command", ["view", "list"])
def test_observation_auth_hold_uses_explicit_resume_and_no_publication(
    planning_checkpoint, monkeypatch, auth_command
):
    from scripts.dev_orchestrator.cli import resume_confirmed_human_blocker

    checkpoint = planning_checkpoint
    _legacy_hold(checkpoint)
    _missing_ci_and_pr(monkeypatch)
    original = completion._run
    mode = "network"
    queries = []

    def observed_run(repo, *args):
        if args[:2] == ("gh", "pr"):
            queries.append(args)
            if args[2] == auth_command and mode == "auth":
                return subprocess.CompletedProcess(args, 1, "", "HTTP 401: requires authentication")
            if args[2] == "list" and mode == "network":
                return subprocess.CompletedProcess(args, 1, "", "read-only connection timeout")
        return original(repo, *args)

    monkeypatch.setattr(completion, "_run", observed_run)
    checkpoint.supervisor.run_role("mobile")
    assert checkpoint.supervisor.load_state("mobile").blocker_kind == "completion_observation_wait"
    mode = "auth"
    checkpoint.supervisor.run_role("mobile")
    held = checkpoint.supervisor.load_state("mobile")
    assert held.status is WorkerStatus.BLOCKED_USER
    assert held.blocker_kind == "github_auth"
    assert held.completion_host_action == "observe"
    assert held.resume_status is WorkerStatus.IMPLEMENTING
    query_count = len(queries)
    for _ in range(3):
        checkpoint.supervisor.run_role("mobile")
    assert len(queries) == query_count
    assert checkpoint.supervisor.load_state("mobile") == held
    mode = "available"
    resume_confirmed_human_blocker(checkpoint.root, "mobile", "github_auth")
    checkpoint.supervisor.run_role("mobile")
    current = checkpoint.supervisor.load_state("mobile")
    assert current.task.task_id == "FINAL-mobile-ci-workflow"
    assert current.completion_host_action == ""
    assert len(checkpoint.prompts) == 1
    assert read_pending_result(checkpoint.config, "mobile") is None


def test_queued_work_cannot_release_a_publication_auth_hold(planning_checkpoint, monkeypatch):
    checkpoint = planning_checkpoint
    _publication_gate(checkpoint, monkeypatch)
    calls = []

    def publisher(role, acceptance, *, resume_denied=False):
        calls.append(role)
        return SimpleNamespace(status="auth_required", reason="Confirmed workflow scope denial")

    monkeypatch.setattr(checkpoint.supervisor, "_publish_completion", publisher)
    checkpoint.supervisor.run_role("mobile")
    held = checkpoint.supervisor.load_state("mobile")
    checkpoint.supervisor.enqueue(
        "mobile", TaskItem("queued-after-auth", "Explicit work waits for auth resume.")
    )
    queue_before = (checkpoint.root / "queues/mobile.json").read_bytes()
    for _ in range(2):
        checkpoint.supervisor.run_role("mobile")
    assert checkpoint.supervisor.load_state("mobile") == held
    assert calls == ["mobile"]
    assert len(checkpoint.prompts) == 1
    assert (checkpoint.root / "queues/mobile.json").read_bytes() == queue_before


def test_additional_hard_failed_ci_is_not_hidden_by_physical_acceptance(
    planning_checkpoint, monkeypatch
):
    from tests.scripts.test_dev_orchestrator_completion import _mock_pr, _success_checks

    checkpoint = planning_checkpoint
    checks = _success_checks("mobile") + [{"name": "Expo Doctor", "conclusion": "FAILURE"}]
    _mock_pr(monkeypatch, checkpoint.config, "mobile", statusCheckRollup=checks)
    monkeypatch.setattr(
        supervisor_module,
        "prepare_final_review",
        lambda config, role, **kwargs: completion._completion_preflight(config, role),
    )
    checkpoint.supervisor.run_role("mobile")
    state = checkpoint.supervisor.load_state("mobile")
    assert state.status is WorkerStatus.IMPLEMENTING
    assert state.task.task_id.startswith("FINAL-mobile-ci-blocking-checks-")
    assert len(state.task.task_id.rsplit("-", 1)[1]) == 64
    assert "Expo Doctor=FAILURE" in state.task.prompt
    assert "completion-blocking" in state.task.prompt
    assert "Do not waive or disable a check merely to obtain a pass" in state.task.prompt
    assert "physical" in state.task.prompt
    assert len(checkpoint.prompts) == 1
    assert read_pending_result(checkpoint.config, "mobile") is None
    assert not (checkpoint.root / "completion/mobile-acceptance.json").exists()


def test_changed_completion_contract_invalidates_only_the_host_observation_cache(
    planning_checkpoint, monkeypatch
):
    checkpoint = planning_checkpoint
    _legacy_hold(checkpoint)
    calls = []

    def preflight(config, role):
        calls.append(role)
        return _pure_acceptance(config, role)

    monkeypatch.setattr(supervisor_module, "completion_preflight", preflight)
    checkpoint.supervisor.run_role("mobile")
    manifest = completion.COMPLETION_MANIFESTS["mobile"]
    with_checks = replace(
        manifest, required_checks=manifest.required_checks + ("New mandatory CI",)
    )
    monkeypatch.setitem(completion.COMPLETION_MANIFESTS, "mobile", with_checks)
    checkpoint.supervisor.run_role("mobile")
    assert calls == ["mobile", "mobile"]
    with_local_gate = replace(
        with_checks,
        local_gates=with_checks.local_gates
        + (
            completion.LocalGate(
                "new local contract", (sys.executable, "-c", "print('not executed')")
            ),
        ),
    )
    monkeypatch.setitem(completion.COMPLETION_MANIFESTS, "mobile", with_local_gate)
    checkpoint.supervisor.run_role("mobile")
    checkpoint.supervisor.run_role("mobile")
    assert calls == ["mobile", "mobile", "mobile"]
    assert len(checkpoint.prompts) == 1
    assert checkpoint.supervisor.load_state("mobile").status is WorkerStatus.BLOCKED_USER


@pytest.mark.parametrize("observation_wait", [False, True])
def test_accepted_obligation_contradiction_allows_only_explicit_new_work(
    planning_checkpoint, monkeypatch, observation_wait
):
    checkpoint = planning_checkpoint
    _missing_ci_and_pr(monkeypatch)
    monkeypatch.setattr(
        supervisor_module,
        "prepare_final_review",
        lambda config, role, **kwargs: completion._completion_preflight(config, role),
    )
    checkpoint.supervisor.run_role("mobile")
    assigned = checkpoint.supervisor.load_state("mobile")
    checkpoint.supervisor.completed_tasks.record(
        "mobile",
        assigned.task,
        completed_head=checkpoint.head,
        invocation_id="test-independent-ci-review",
        source="independent_review",
    )
    ledger_before = (checkpoint.root / "completed-tasks/mobile.json").read_bytes()
    original = completion._run
    unavailable = observation_wait

    def observed_run(repo, *args):
        if args[:3] == ("gh", "pr", "list") and unavailable:
            return subprocess.CompletedProcess(args, 1, "", "temporary read-only query failure")
        return original(repo, *args)

    monkeypatch.setattr(completion, "_run", observed_run)
    checkpoint.supervisor.save_state(WorkerState("mobile"))
    checkpoint.supervisor.run_role("mobile")
    if observation_wait:
        assert checkpoint.supervisor.load_state("mobile").completion_host_action == "observe"
        unavailable = False
        checkpoint.supervisor.run_role("mobile")
    contradicted = checkpoint.supervisor.load_state("mobile")
    assert contradicted.status is WorkerStatus.BLOCKED_SYSTEM
    assert contradicted.blocker_kind == "completion_remediation"
    assert contradicted.task is None
    assert contradicted.completion_host_action == ""
    checkpoint.supervisor.run_role("mobile")
    assert checkpoint.supervisor.load_state("mobile") == contradicted
    assert len(checkpoint.prompts) == 2
    checkpoint.supervisor.enqueue(
        "mobile",
        TaskItem("explicit-new-ci-regression", "Investigate this newly authorized regression."),
    )
    checkpoint.supervisor.run_role("mobile")
    resumed = checkpoint.supervisor.load_state("mobile")
    assert resumed.status is WorkerStatus.IMPLEMENTING
    assert resumed.task.task_id == "explicit-new-ci-regression"
    assert resumed.completion_host_action == ""
    assert len(checkpoint.prompts) == 2
    assert AtomicJsonStore(checkpoint.root / "queues/mobile.json").read() == []
    assert (checkpoint.root / "completed-tasks/mobile.json").read_bytes() == ledger_before


@pytest.mark.parametrize(
    "status,kind",
    [
        (WorkerStatus.BLOCKED_SYSTEM, "validation"),
        (WorkerStatus.BLOCKED_SYSTEM, "system"),
        (WorkerStatus.BLOCKED_SYSTEM, "incomplete_evidence"),
        (WorkerStatus.BLOCKED_USER, "human"),
        (WorkerStatus.BLOCKED_USER, "github_auth"),
    ],
)
def test_contradiction_queue_recovery_does_not_release_other_hard_gates(
    planning_checkpoint, status, kind
):
    checkpoint = planning_checkpoint
    held = _legacy_hold(checkpoint)
    protected = replace(
        held,
        status=status,
        blocker_kind=kind,
        blocked_reason="Preserve this independent gate",
        resume_status=None,
        completion_host_action="",
    )
    checkpoint.supervisor.save_state(protected)
    checkpoint.supervisor.enqueue(
        "mobile",
        TaskItem("explicit-work-behind-other-gate", "This does not override a separate gate."),
    )
    queue_before = (checkpoint.root / "queues/mobile.json").read_bytes()
    checkpoint.supervisor.run_role("mobile")
    assert checkpoint.supervisor.load_state("mobile") == protected
    assert (checkpoint.root / "queues/mobile.json").read_bytes() == queue_before
    assert len(checkpoint.prompts) == 1
    assert read_pending_result(checkpoint.config, "mobile") is None
