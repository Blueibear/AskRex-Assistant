from __future__ import annotations

from pathlib import Path

from scripts.dev_orchestrator.supervisor import Supervisor
from scripts.dev_orchestrator.types import AgentResult, OrchestratorConfig, TaskItem, WorkerStatus


class FakeInvoker:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []
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
        raise AssertionError(f"unexpected fake invocation: {role}/{phase}")

    def implement(self, role, state, task, context, model):
        return self._take(role, "implement", task.task_id)

    def review(self, role, state, task, context, model):
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


def result(outcome: str, *, task_id: str = "", task_prompt: str = "", summary: str = "ok") -> AgentResult:
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
    invoker.add("backend", "implement", AgentResult("blocked_user", "Need Start menu retest", "Retest", True, "physical Windows retest"))
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
    assert mobile.status is WorkerStatus.DONE
    assert ("backend", "lead", "") in invoker.calls
    assert ("mobile", "lead", "") in invoker.calls


def test_codex_usage_limit_at_review_requests_reset_and_preserves_other_workstream(tmp_path: Path) -> None:
    from scripts.dev_orchestrator.runner import AgentInvocationError
    invoker = FakeInvoker()
    invoker.add("backend", "review", AgentInvocationError("codex", "usage_limit", "weekly limit reached"))
    invoker.add("mobile", "implement", result("continue"))
    config = make_config(tmp_path, observe_only=False)
    supervisor = Supervisor(config, invoker)
    supervisor.save_state(__import__("scripts.dev_orchestrator.types", fromlist=["WorkerState"]).WorkerState(
        role="backend", status=WorkerStatus.REVIEWING, task=TaskItem("B-1", "Fix backend")
    ))
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
    invoker.add("backend", "lead", result("assign", task_id="B-1", task_prompt="Reframed backend fix"))
    config = make_config(tmp_path, observe_only=False)
    supervisor = Supervisor(config, invoker)
    supervisor.save_state(WorkerState(
        role="backend",
        status=WorkerStatus.IMPLEMENTING,
        task=TaskItem("B-1", "Fix backend"),
        implementation_failures=3,
    ))

    supervisor.run_cycle()

    state = supervisor.load_state("backend")
    assert ("backend", "lead", "B-1") in invoker.calls
    assert state.status is WorkerStatus.IMPLEMENTING
    assert state.task is not None
    assert state.task.prompt == "Reframed backend fix"
