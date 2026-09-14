from __future__ import annotations

from pathlib import Path

from scripts.dev_orchestrator.supervisor import Supervisor
from scripts.dev_orchestrator.types import (
    AgentResult,
    IssueUpdate,
    OrchestratorConfig,
    TaskItem,
    WorkerState,
    WorkerStatus,
)


class FakeInvoker:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.review_models: list[str] = []
        self.results: dict[tuple[str, str], list[AgentResult]] = {}

    def add(self, role: str, phase: str, *results: AgentResult) -> None:
        self.results.setdefault((role, phase), []).extend(results)

    def _take(self, role: str, phase: str, task_id: str = "") -> AgentResult:
        self.calls.append((role, phase, task_id))
        configured = self.results.get((role, phase), [])
        if configured:
            value = configured.pop(0)
            if isinstance(value, Exception):
                raise value
            return value
        if phase == "lead":
            return AgentResult("done", "No queued fake work", "")
        if phase == "review" and task_id.startswith("FINAL-VERIFY-"):
            return AgentResult("pass", "Final verification passed", "")
        raise AssertionError(f"unexpected fake invocation: {role}/{phase}")

    def implement(self, role, state, task, context, model):
        return self._take(role, "implement", task.task_id)

    def review(self, role, state, task, context, model):
        self.review_models.append(model)
        return self._take(role, "review", task.task_id)

    def lead(self, role, state, context, task=None):
        return self._take(role, "lead", task.task_id if task else "")


def make_config(tmp_path: Path, *, observe_only: bool) -> OrchestratorConfig:
    coordination = tmp_path / "coordination"
    coordination.mkdir()
    (coordination / "PROTOCOL.md").write_text("# Protocol\nCheck mailboxes.\n", encoding="utf-8")
    (coordination / "AGENT_BACKEND.md").write_text("Role: backend\n", encoding="utf-8")
    (coordination / "AGENT_MOBILE.md").write_text("Role: mobile\n", encoding="utf-8")
    (coordination / "mailbox" / "backend").mkdir(parents=True)
    (coordination / "mailbox" / "mobile").mkdir(parents=True)
    (coordination / "issues").mkdir()
    backend = tmp_path / "backend"
    mobile = tmp_path / "mobile"
    frozen = tmp_path / "rex-ai-pc-test"
    backend.mkdir()
    mobile.mkdir()
    frozen.mkdir()
    return OrchestratorConfig(
        coordination_root=coordination,
        backend_root=backend,
        mobile_root=mobile,
        frozen_worktree=frozen,
        observe_only=observe_only,
    )


def result(
    outcome: str, *, task_id: str = "", task_prompt: str = "", summary: str = "ok"
) -> AgentResult:
    return AgentResult(
        outcome=outcome,
        summary=summary,
        next_action=summary,
        task_id=task_id,
        task_prompt=task_prompt,
    )


def test_observe_only_never_invokes_models(tmp_path: Path) -> None:
    invoker = FakeInvoker()
    supervisor = Supervisor(make_config(tmp_path, observe_only=True), invoker)
    supervisor.enqueue("backend", TaskItem("B-1", "Do backend work"))

    supervisor.run_cycle()

    assert invoker.calls == []
    assert supervisor.load_state("backend").status is WorkerStatus.IDLE


def test_task_requires_implementation_and_independent_review_pass(tmp_path: Path) -> None:
    invoker = FakeInvoker()
    invoker.add("backend", "implement", result("ready_for_review"))
    invoker.add("backend", "review", result("pass"))
    supervisor = Supervisor(make_config(tmp_path, observe_only=False), invoker)
    supervisor.enqueue("backend", TaskItem("B-1", "Fix backend"))

    supervisor.run_cycle()
    assert supervisor.load_state("backend").status is WorkerStatus.REVIEWING

    supervisor.run_cycle()
    state = supervisor.load_state("backend")
    assert state.status is WorkerStatus.IDLE
    assert state.task is None


def test_review_changes_required_loops_back_to_implementation(tmp_path: Path) -> None:
    invoker = FakeInvoker()
    invoker.add("mobile", "implement", result("ready_for_review"))
    invoker.add("mobile", "review", result("changes_required", summary="Fix schema mismatch"))
    supervisor = Supervisor(make_config(tmp_path, observe_only=False), invoker)
    supervisor.enqueue("mobile", TaskItem("M-1", "Implement pairing"))

    supervisor.run_cycle()
    supervisor.run_cycle()

    state = supervisor.load_state("mobile")
    assert state.status is WorkerStatus.IMPLEMENTING
    assert state.review_failures == 1
    assert state.task is not None
    assert "Fix schema mismatch" in state.task.feedback


def test_backend_human_blocker_does_not_stop_mobile(tmp_path: Path) -> None:
    invoker = FakeInvoker()
    invoker.add(
        "backend",
        "implement",
        AgentResult(
            "blocked_user", "Need Start menu retest", "Retest", True, "physical Windows retest"
        ),
    )
    invoker.add("mobile", "implement", result("continue"))
    supervisor = Supervisor(make_config(tmp_path, observe_only=False), invoker)
    supervisor.enqueue("backend", TaskItem("B-1", "Fix startup"))
    supervisor.enqueue("mobile", TaskItem("M-1", "Continue pairing"))

    supervisor.run_cycle()

    assert supervisor.load_state("backend").status is WorkerStatus.BLOCKED_USER
    assert supervisor.load_state("mobile").status is WorkerStatus.IMPLEMENTING


def test_empty_queue_invokes_astra_lead_only_when_active(tmp_path: Path) -> None:
    invoker = FakeInvoker()
    invoker.add("backend", "lead", result("assign", task_id="B-2", task_prompt="Next backend task"))
    invoker.add("mobile", "lead", result("done"))
    supervisor = Supervisor(make_config(tmp_path, observe_only=False), invoker)

    supervisor.run_cycle()

    backend = supervisor.load_state("backend")
    mobile = supervisor.load_state("mobile")
    assert backend.status is WorkerStatus.IMPLEMENTING
    assert backend.task == TaskItem("B-2", "Next backend task")
    assert mobile.status is WorkerStatus.IMPLEMENTING
    assert mobile.task is not None and mobile.task.task_id == "FINAL-mobile"
    assert ("backend", "lead", "") in invoker.calls
    assert ("mobile", "lead", "") in invoker.calls


def test_codex_usage_limit_at_review_requests_reset_and_preserves_other_workstream(
    tmp_path: Path,
) -> None:
    from scripts.dev_orchestrator.runner import AgentInvocationError

    invoker = FakeInvoker()
    invoker.add(
        "backend", "review", AgentInvocationError("codex", "usage_limit", "weekly limit reached")
    )
    invoker.add("mobile", "implement", result("continue"))
    config = make_config(tmp_path, observe_only=False)
    supervisor = Supervisor(config, invoker)
    supervisor.save_state(
        __import__("scripts.dev_orchestrator.types", fromlist=["WorkerState"]).WorkerState(
            role="backend", status=WorkerStatus.REVIEWING, task=TaskItem("B-1", "Fix backend")
        )
    )
    supervisor.enqueue("mobile", TaskItem("M-1", "Continue mobile"))

    supervisor.run_cycle()

    backend = supervisor.load_state("backend")
    mobile = supervisor.load_state("mobile")
    assert backend.status is WorkerStatus.BLOCKED_USER
    assert "banked reset" in backend.blocked_reason.lower()
    assert mobile.status is WorkerStatus.IMPLEMENTING
    alerts = list((config.coordination_root / "alerts").glob("*.json"))
    assert len(alerts) == 1
    assert "3 banked reset" in alerts[0].read_text(encoding="utf-8")


def test_repeated_implementation_failure_triggers_astra_adjudication(tmp_path: Path) -> None:
    from scripts.dev_orchestrator.types import WorkerState

    invoker = FakeInvoker()
    invoker.add("backend", "implement", result("failed", summary="still failing"))
    invoker.add(
        "backend", "lead", result("assign", task_id="B-1", task_prompt="Reframed backend fix")
    )
    config = make_config(tmp_path, observe_only=False)
    supervisor = Supervisor(config, invoker)
    supervisor.save_state(
        WorkerState(
            role="backend",
            status=WorkerStatus.IMPLEMENTING,
            task=TaskItem("B-1", "Fix backend"),
            implementation_failures=3,
        )
    )

    supervisor.run_cycle()

    state = supervisor.load_state("backend")
    assert ("backend", "lead", "B-1") in invoker.calls
    assert state.status is WorkerStatus.IMPLEMENTING
    assert state.task is not None
    assert state.task.prompt == "Reframed backend fix"


def test_blocked_system_retries_from_saved_review_phase(tmp_path: Path) -> None:
    invoker = FakeInvoker()
    invoker.add("backend", "review", result("pass"))
    supervisor = Supervisor(make_config(tmp_path, observe_only=False), invoker)
    supervisor.save_state(
        WorkerState(
            role="backend",
            status=WorkerStatus.BLOCKED_SYSTEM,
            task=TaskItem("B-1", "Review me"),
            blocked_reason="transient reviewer encoding failure",
            blocker_kind="system",
            resume_status=WorkerStatus.REVIEWING,
        )
    )

    supervisor._run_role("backend")

    assert ("backend", "review", "B-1") in invoker.calls
    state = supervisor.load_state("backend")
    assert state.status is WorkerStatus.IDLE
    assert state.task is None


def test_codex_implemented_checkpoint_forces_sol_review(tmp_path: Path) -> None:
    from scripts.dev_orchestrator.routing import SOL_MODEL
    from scripts.dev_orchestrator.storage import AtomicJsonStore

    invoker = FakeInvoker()
    invoker.add("backend", "review", result("pass"))
    supervisor = Supervisor(make_config(tmp_path, observe_only=False), invoker)
    (supervisor.root / "handoff").mkdir()
    AtomicJsonStore(supervisor.root / "handoff" / "backend.json").write({"last_provider": "codex"})
    supervisor.save_state(
        WorkerState(
            role="backend",
            status=WorkerStatus.REVIEWING,
            task=TaskItem("B-CODEX", "Review Codex implementation"),
        )
    )

    supervisor._run_role("backend")

    assert invoker.review_models == [SOL_MODEL]


def test_usage_limit_records_exact_resume_phase(tmp_path: Path) -> None:
    from scripts.dev_orchestrator.runner import AgentInvocationError

    invoker = FakeInvoker()
    invoker.add("backend", "review", AgentInvocationError("codex", "usage_limit", "weekly limit"))
    supervisor = Supervisor(make_config(tmp_path, observe_only=False), invoker)
    supervisor.save_state(
        WorkerState(
            role="backend", status=WorkerStatus.REVIEWING, task=TaskItem("B-1", "Review me")
        )
    )

    supervisor.run_cycle()

    state = supervisor.load_state("backend")
    assert state.status is WorkerStatus.BLOCKED_USER
    assert state.blocker_kind == "usage_limit"
    assert state.resume_status is WorkerStatus.REVIEWING


def test_dependency_block_resumes_only_after_peer_moves_off_story_and_publishes_contract(
    tmp_path: Path,
) -> None:
    invoker = FakeInvoker()
    invoker.add("mobile", "implement", result("continue", task_id="S35-MOBILE"))
    supervisor = Supervisor(make_config(tmp_path, observe_only=False), invoker)
    supervisor.save_state(
        WorkerState(
            role="backend",
            status=WorkerStatus.IMPLEMENTING,
            task=TaskItem("S35-BACKEND", "Finish backend contract"),
        )
    )
    supervisor.save_state(
        WorkerState(
            role="mobile",
            status=WorkerStatus.BLOCKED_USER,
            task=TaskItem("S35-MOBILE", "Consume backend contract"),
            blocked_reason="Waiting for backend S35 contract; no user action required.",
            blocker_kind="dependency",
            resume_status=WorkerStatus.IMPLEMENTING,
        )
    )
    mailbox = supervisor.root / "mailbox" / "mobile" / "MSG-backend-s35.md"
    mailbox.write_text(
        "From: backend\nTo: mobile\nRelated: STORY-S35-SPEECH-ROUTER\nNeeds response: no\n",
        encoding="utf-8",
    )

    supervisor._run_role("mobile")
    assert invoker.calls == []
    assert supervisor.load_state("mobile").status is WorkerStatus.BLOCKED_USER

    supervisor.save_state(WorkerState(role="backend", status=WorkerStatus.IDLE))
    supervisor._run_role("mobile")

    assert ("mobile", "implement", "S35-MOBILE") in invoker.calls
    state = supervisor.load_state("mobile")
    assert state.status is WorkerStatus.IMPLEMENTING
    assert state.blocker_kind == ""


def test_final_completion_auth_gate_blocks_user_instead_of_assigning_work(
    tmp_path: Path, monkeypatch
) -> None:
    from scripts.dev_orchestrator import supervisor as supervisor_module
    from scripts.dev_orchestrator.completion import CompletionGate

    invoker = FakeInvoker()
    invoker.add("backend", "lead", result("done"))
    config = make_config(tmp_path, observe_only=False)
    supervisor = Supervisor(config, invoker)
    monkeypatch.setattr(
        supervisor_module,
        "evaluate_completion",
        lambda *_args: CompletionGate(False, ("GitHub CLI authentication required",), True),
    )

    supervisor._run_role("backend")
    state = supervisor.load_state("backend")
    assert state.status is WorkerStatus.BLOCKED_USER
    assert state.task is None
    assert state.blocker_kind == "human"


def test_backend_and_mobile_workstreams_execute_in_parallel(tmp_path: Path) -> None:
    import threading

    barrier = threading.Barrier(2, timeout=1.0)
    config = make_config(tmp_path, observe_only=False)

    class ParallelInvoker(FakeInvoker):
        def implement(self, role, state, task, context, model):
            barrier.wait()
            return result("continue")

    supervisor = Supervisor(config, ParallelInvoker())
    supervisor.enqueue("backend", TaskItem("B-1", "Backend"))
    supervisor.enqueue("mobile", TaskItem("M-1", "Mobile"))

    supervisor.run_cycle()

    assert supervisor.load_state("backend").status is WorkerStatus.IMPLEMENTING
    assert supervisor.load_state("mobile").status is WorkerStatus.IMPLEMENTING


def test_passing_test_issue_review_parks_for_testing_without_canonical_mutation(
    tmp_path: Path,
) -> None:
    config = make_config(tmp_path, observe_only=False)
    issue_dir = config.coordination_root / "issues"
    issue_dir.mkdir(parents=True, exist_ok=True)
    issue = issue_dir / "TEST-009.md"
    issue.write_text("# TEST-009\n\nStatus: open\nOwner: backend\n", encoding="utf-8")
    invoker = FakeInvoker()
    invoker.add(
        "backend",
        "review",
        AgentResult(
            "pass",
            "fixed",
            "retest",
            invocation_id="test-review-invocation",
            issue_updates=(IssueUpdate("TEST-009", "fixed-needs-retest", "ready"),),
        ),
    )
    supervisor = Supervisor(config, invoker)
    supervisor.save_state(
        WorkerState(
            role="backend",
            status=WorkerStatus.REVIEWING,
            task=TaskItem("TEST-009", "fix"),
        )
    )
    supervisor._run_role("backend")
    state = supervisor.load_state("backend")
    assert state.status is WorkerStatus.BLOCKED_USER
    assert state.blocker_kind == "retest"
    assert "Status: open" in issue.read_text(encoding="utf-8")
    assert list((config.coordination_root / "mailbox" / "testing").glob("RETEST-*.md"))


def test_final_sol_pass_revalidates_completion_before_done(tmp_path: Path, monkeypatch) -> None:
    from scripts.dev_orchestrator import supervisor as supervisor_module
    from scripts.dev_orchestrator.completion import CompletionGate

    invoker = FakeInvoker()
    config = make_config(tmp_path, observe_only=False)
    supervisor = Supervisor(config, invoker)
    gates = iter(
        [
            CompletionGate(True),
            CompletionGate(False, ("required CI changed during final review",), False),
        ]
    )
    monkeypatch.setattr(supervisor_module, "evaluate_completion", lambda *_args: next(gates))

    supervisor._done_claim("backend", WorkerState(role="backend"), "context")

    state = supervisor.load_state("backend")
    assert state.status is WorkerStatus.IMPLEMENTING
    assert state.task is not None
    assert state.task.task_id == "FINAL-backend"
    assert "CI changed" in state.task.prompt


def test_retest_block_requires_exact_verified_status(tmp_path: Path) -> None:
    invoker = FakeInvoker()
    supervisor = Supervisor(make_config(tmp_path, observe_only=False), invoker)
    issue = supervisor.root / "issues" / "TEST-011.md"
    issue.write_text(
        "# TEST-011\n\nStatus: verified-needs-followup\nOwner: backend\n",
        encoding="utf-8",
    )
    supervisor.save_state(
        WorkerState(
            role="backend",
            status=WorkerStatus.BLOCKED_USER,
            task=TaskItem("TEST-011", "Retest pending"),
            blocker_kind="retest",
        )
    )

    supervisor._run_role("backend")

    state = supervisor.load_state("backend")
    assert state.status is WorkerStatus.BLOCKED_USER
    assert state.task == TaskItem("TEST-011", "Retest pending")


def test_defensive_needs_user_cannot_pass_review(tmp_path: Path) -> None:
    invoker = FakeInvoker()
    supervisor = Supervisor(make_config(tmp_path, observe_only=False), invoker)
    supervisor.enqueue("backend", TaskItem("B-guard", "Guard human gate"))
    invoker.add("backend", "implement", result("ready_for_review"))
    supervisor.run_cycle()
    invoker.add(
        "backend",
        "review",
        AgentResult(
            "pass",
            "review needs user",
            "confirm hardware",
            needs_user=True,
            blocker_reason="hardware confirmation required",
        ),
    )
    supervisor.run_cycle()
    state = supervisor.load_state("backend")
    assert state.status is WorkerStatus.BLOCKED_USER
    assert state.task is not None
    assert state.blocker_kind == "human"


def test_completion_retest_block_reconciles_after_testing_verifies(tmp_path: Path) -> None:
    invoker = FakeInvoker()
    config = make_config(tmp_path, observe_only=False)
    supervisor = Supervisor(config, invoker)
    issue = config.coordination_root / "issues" / "TEST-777.md"
    issue.write_text(
        "# TEST-777\n\nStatus: fixed-needs-retest\nOwner: backend\n",
        encoding="utf-8",
    )

    supervisor._done_claim("backend", WorkerState("backend"), "ctx")
    blocked = supervisor.load_state("backend")
    assert blocked.status is WorkerStatus.BLOCKED_USER
    assert blocked.blocker_kind == "retest"
    assert blocked.task is not None and blocked.task.task_id == "TEST-777"

    issue.write_text("# TEST-777\n\nStatus: verified\nOwner: backend\n", encoding="utf-8")
    invoker.add("backend", "lead", result("assign", task_id="B-NEXT", task_prompt="Continue"))
    supervisor._run_role("backend")
    resumed = supervisor.load_state("backend")
    assert resumed.status is WorkerStatus.IMPLEMENTING
    assert resumed.task is not None and resumed.task.task_id == "B-NEXT"


def test_final_done_transition_holds_role_lease_lock(tmp_path: Path, monkeypatch) -> None:
    from scripts.dev_orchestrator import supervisor as supervisor_module
    from scripts.dev_orchestrator.completion import CompletionGate
    from scripts.dev_orchestrator.handoff import role_lease_lock_path

    invoker = FakeInvoker()
    config = make_config(tmp_path, observe_only=False)
    supervisor = Supervisor(config, invoker)
    monkeypatch.setattr(
        supervisor_module, "evaluate_completion", lambda *_args: CompletionGate(True)
    )
    observed = {"locked": False}

    def verify_locked(_config, role):
        observed["locked"] = role_lease_lock_path(config, role).exists()

    monkeypatch.setattr(supervisor_module, "validate_handoff", verify_locked)
    supervisor._done_claim("backend", WorkerState(role="backend"), "context")

    assert observed["locked"] is True
    assert supervisor.load_state("backend").status is WorkerStatus.DONE


def test_astra_adjudication_publishes_coordination_before_assignment(tmp_path: Path) -> None:
    from scripts.dev_orchestrator.types import CoordinationMessage

    invoker = FakeInvoker()
    invoker.add(
        "backend",
        "lead",
        AgentResult(
            "assign",
            "reassign",
            "continue",
            task_id="B-NEXT",
            task_prompt="Next task",
            invocation_id="astra-adjudication-1",
            coordination_messages=(
                CoordinationMessage("mobile", "high", "B-OLD", True, "Backend contract changed."),
            ),
        ),
    )
    config = make_config(tmp_path, observe_only=False)
    supervisor = Supervisor(config, invoker)
    state = WorkerState(
        role="backend", status=WorkerStatus.IMPLEMENTING, task=TaskItem("B-OLD", "Old")
    )

    supervisor._adjudicate("backend", state, "ctx")

    messages = list((config.coordination_root / "mailbox" / "mobile").glob("MSG-*.md"))
    assert len(messages) == 1
    assert "Backend contract changed." in messages[0].read_text(encoding="utf-8")
    assert supervisor.load_state("backend").task == TaskItem("B-NEXT", "Next task")


def test_astra_adjudication_publication_rejection_blocks_assignment(
    tmp_path: Path, monkeypatch
) -> None:
    from scripts.dev_orchestrator import supervisor as supervisor_module

    invoker = FakeInvoker()
    invoker.add(
        "backend",
        "lead",
        AgentResult(
            "assign",
            "reassign",
            "continue",
            task_id="B-NEXT",
            task_prompt="Next task",
            invocation_id="astra-adjudication-2",
        ),
    )
    config = make_config(tmp_path, observe_only=False)
    supervisor = Supervisor(config, invoker)
    original = WorkerState(
        role="backend", status=WorkerStatus.IMPLEMENTING, task=TaskItem("B-OLD", "Old")
    )
    supervisor.save_state(original)

    def reject(*_args, **_kwargs):
        raise ValueError("simulated publication rejection")

    monkeypatch.setattr(supervisor_module, "apply_agent_updates", reject)
    supervisor._adjudicate("backend", original, "ctx")

    state = supervisor.load_state("backend")
    assert state.status is WorkerStatus.BLOCKED_SYSTEM
    assert state.task == TaskItem("B-OLD", "Old")
    assert "publication rejection" in state.blocked_reason


def test_restart_consumes_durable_implementation_result_without_reinvoking_model(
    tmp_path: Path,
) -> None:
    config = make_config(tmp_path, observe_only=False)
    invoker = FakeInvoker()
    supervisor = Supervisor(config, invoker)
    task = TaskItem("B-RECOVER", "Recover durable result")
    supervisor.save_state(WorkerState(role="backend", status=WorkerStatus.IMPLEMENTING, task=task))
    pending = {
        "phase": "implement",
        "role": "backend",
        "task_id": task.task_id,
        "invocation_id": "inv-recover-1",
        "pre_head": "aaa",
        "post_head": "bbb",
        "result": {
            "outcome": "ready_for_review",
            "summary": "implementation already published",
            "next_action": "review it",
            "needs_user": False,
            "blocker_reason": "",
            "task_id": task.task_id,
            "task_prompt": "",
            "role": "backend",
            "invocation_id": "inv-recover-1",
            "coordination_messages": [],
            "issue_updates": [],
        },
    }
    lease = config.coordination_root / "handoff" / "backend.json"
    lease.parent.mkdir(parents=True, exist_ok=True)
    lease.write_text(
        __import__("json").dumps({"owner": "supervisor", "head": "bbb", "pending_result": pending}),
        encoding="utf-8",
    )

    supervisor._run_role("backend")

    state = supervisor.load_state("backend")
    assert state.status is WorkerStatus.REVIEWING
    assert state.task == task
    assert state.last_result_invocation_id == "inv-recover-1"
    assert invoker.calls == []
    persisted_lease = __import__("json").loads(lease.read_text(encoding="utf-8"))
    assert "pending_result" not in persisted_lease
