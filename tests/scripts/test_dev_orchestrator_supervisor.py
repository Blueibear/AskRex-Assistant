from __future__ import annotations

import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from scripts.dev_orchestrator.supervisor import Supervisor
from scripts.dev_orchestrator.types import (
    AgentResult,
    CoordinationMessage,
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
        metadata={"iteration_validation": {"enabled": False}},
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
    assert state.idle_context_fingerprint == ""

    invoker.add(
        "backend",
        "lead",
        result("assign", task_id="B-2", task_prompt="Continue the active campaign"),
    )
    supervisor._run_role("backend")
    awakened = supervisor.load_state("backend")
    assert awakened.status is WorkerStatus.IMPLEMENTING
    assert awakened.task is not None
    assert awakened.task.task_id == "B-2"
    assert awakened.idle_context_fingerprint == ""


def test_idle_fingerprint_does_not_hide_explicit_queued_work(tmp_path: Path) -> None:
    invoker = FakeInvoker()
    invoker.add("backend", "implement", result("ready_for_review"))
    invoker.add("backend", "review", result("pass"))
    supervisor = Supervisor(make_config(tmp_path, observe_only=False), invoker)
    supervisor.enqueue("backend", TaskItem("B-1", "Finish initial task"))

    supervisor.run_cycle()
    supervisor.run_cycle()
    idle = supervisor.load_state("backend")
    assert idle.status is WorkerStatus.IDLE
    assert idle.idle_context_fingerprint == ""

    invoker.add("backend", "implement", result("ready_for_review"))
    supervisor.enqueue("backend", TaskItem("B-2", "Explicit queued work"))
    supervisor._run_role("backend")

    state = supervisor.load_state("backend")
    assert ("backend", "implement", "B-2") in invoker.calls
    assert state.status is WorkerStatus.REVIEWING
    assert state.task == TaskItem("B-2", "Explicit queued work")
    assert state.idle_context_fingerprint == ""


def test_ready_for_review_fails_deterministic_validation_before_review(tmp_path: Path) -> None:
    invoker = FakeInvoker()
    invoker.add("backend", "implement", result("ready_for_review"))
    config = replace(
        make_config(tmp_path, observe_only=False),
        metadata={
            "iteration_validation": {
                "enabled": True,
                "backend": {
                    "gates": [
                        {
                            "name": "focused backend validation",
                            "command": [
                                sys.executable,
                                "-c",
                                "import sys; print('BROKEN-GATE'); sys.exit(7)",
                            ],
                        }
                    ]
                },
            }
        },
    )
    supervisor = Supervisor(config, invoker)
    supervisor.enqueue("backend", TaskItem("B-VALIDATE", "Fix backend"))

    supervisor.run_cycle()

    state = supervisor.load_state("backend")
    assert state.status is WorkerStatus.IMPLEMENTING
    assert state.task is not None
    assert "focused backend validation" in state.task.feedback
    assert "Exit code: 7" in state.task.feedback
    assert "BROKEN-GATE" in state.task.feedback
    assert not any(phase == "review" for _role, phase, _task in invoker.calls)


def test_validation_feedback_is_bounded_for_windows_provider_prompts(tmp_path: Path) -> None:
    invoker = FakeInvoker()
    invoker.add("backend", "implement", result("ready_for_review"))
    config = replace(
        make_config(tmp_path, observe_only=False),
        metadata={
            "iteration_validation": {
                "enabled": True,
                "backend": {
                    "gates": [
                        {
                            "name": "bounded validation",
                            "command": [
                                sys.executable,
                                "-c",
                                "import sys; print('X' * 30000); print('DECISIVE-TAIL'); sys.exit(5)",
                            ],
                        }
                    ]
                },
            }
        },
    )
    supervisor = Supervisor(config, invoker)
    supervisor.enqueue("backend", TaskItem("B-BOUNDED", "Fix backend"))

    supervisor.run_cycle()

    state = supervisor.load_state("backend")
    assert state.task is not None
    assert len(state.task.feedback) < 6000
    assert "DECISIVE-TAIL" in state.task.feedback
    assert "output truncated" in state.task.feedback


def test_failed_validation_does_not_publish_ready_for_review_coordination(tmp_path: Path) -> None:
    invoker = FakeInvoker()
    invoker.add(
        "backend",
        "implement",
        AgentResult(
            "ready_for_review",
            "ready",
            "review",
            invocation_id="test-validation-message-1",
            coordination_messages=(
                CoordinationMessage("mobile", "high", "B-VALIDATE", False, "Unvalidated contract"),
            ),
        ),
    )
    config = replace(
        make_config(tmp_path, observe_only=False),
        metadata={
            "iteration_validation": {
                "enabled": True,
                "backend": {
                    "gates": [
                        {
                            "name": "reject contract",
                            "command": [sys.executable, "-c", "import sys; sys.exit(4)"],
                        }
                    ]
                },
            }
        },
    )
    supervisor = Supervisor(config, invoker)
    supervisor.enqueue("backend", TaskItem("B-VALIDATE", "Fix backend"))

    supervisor.run_cycle()

    assert supervisor.load_state("backend").status is WorkerStatus.IMPLEMENTING
    assert list((supervisor.root / "mailbox" / "mobile").glob("MSG-*.md")) == []


def test_ready_for_review_advances_only_after_deterministic_validation_passes(
    tmp_path: Path,
) -> None:
    invoker = FakeInvoker()
    invoker.add("backend", "implement", result("ready_for_review"))
    config = replace(
        make_config(tmp_path, observe_only=False),
        metadata={
            "iteration_validation": {
                "enabled": True,
                "backend": {
                    "gates": [
                        {
                            "name": "focused backend validation",
                            "command": [sys.executable, "-c", "print('VALIDATION_OK')"],
                        }
                    ]
                },
            }
        },
    )
    supervisor = Supervisor(config, invoker)
    supervisor.enqueue("backend", TaskItem("B-VALIDATE", "Fix backend"))

    supervisor.run_cycle()

    state = supervisor.load_state("backend")
    assert state.status is WorkerStatus.REVIEWING
    assert state.task == TaskItem("B-VALIDATE", "Fix backend")


def test_task_prefix_validation_override_replaces_role_default(tmp_path: Path) -> None:
    invoker = FakeInvoker()
    invoker.add("backend", "implement", result("ready_for_review"))
    config = replace(
        make_config(tmp_path, observe_only=False),
        metadata={
            "iteration_validation": {
                "enabled": True,
                "backend": {
                    "gates": [{"name": "default pass", "command": [sys.executable, "-c", "pass"]}],
                    "overrides": [
                        {
                            "task_prefix": "S35-",
                            "gates": [
                                {
                                    "name": "S35 focused validation",
                                    "command": [
                                        sys.executable,
                                        "-c",
                                        "import sys; print('S35-BROKEN'); sys.exit(9)",
                                    ],
                                }
                            ],
                        }
                    ],
                },
            }
        },
    )
    supervisor = Supervisor(config, invoker)
    supervisor.enqueue("backend", TaskItem("S35-BACKEND", "Fix speech"))

    supervisor.run_cycle()

    state = supervisor.load_state("backend")
    assert state.status is WorkerStatus.IMPLEMENTING
    assert state.task is not None
    assert "S35 focused validation" in state.task.feedback
    assert "S35-BROKEN" in state.task.feedback
    assert "default pass" not in state.task.feedback


def test_validation_infrastructure_failure_blocks_system(tmp_path: Path) -> None:
    invoker = FakeInvoker()
    invoker.add("backend", "implement", result("ready_for_review"))
    config = replace(
        make_config(tmp_path, observe_only=False),
        metadata={
            "iteration_validation": {
                "enabled": True,
                "backend": {
                    "gates": [
                        {
                            "name": "missing runner",
                            "command": ["definitely-not-an-askrex-executable-9d31"],
                        }
                    ]
                },
            }
        },
    )
    supervisor = Supervisor(config, invoker)
    supervisor.enqueue("backend", TaskItem("B-VALIDATE", "Fix backend"))

    supervisor.run_cycle()

    state = supervisor.load_state("backend")
    assert state.status is WorkerStatus.BLOCKED_SYSTEM
    assert state.blocker_kind == "system"
    assert state.resume_status is WorkerStatus.IMPLEMENTING
    assert "missing runner" in state.blocked_reason



def test_review_phase_rejects_continue_before_coordination_side_effects(tmp_path: Path) -> None:
    invoker = FakeInvoker()
    invoker.add(
        "backend",
        "review",
        AgentResult(
            "continue",
            "invalid review outcome",
            "should not publish",
            coordination_messages=(
                CoordinationMessage(
                    to="testing",
                    priority="high",
                    related="TEST-005",
                    needs_response=True,
                    body="THIS-MUST-NOT-BE-PUBLISHED",
                ),
            ),
        ),
    )
    supervisor = Supervisor(make_config(tmp_path, observe_only=False), invoker)
    state = WorkerState(
        role="backend",
        status=WorkerStatus.REVIEWING,
        task=TaskItem("B-REVIEW-PHASE", "Review only"),
    )

    supervisor._review("backend", state, "ctx")

    saved = supervisor.load_state("backend")
    assert saved.status is WorkerStatus.BLOCKED_SYSTEM
    assert saved.resume_status is WorkerStatus.REVIEWING
    assert "invalid review agent result" in saved.blocked_reason
    mailbox = supervisor.root / "mailbox" / "testing"
    assert not list(mailbox.glob("*.md"))


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


def test_coordination_only_second_review_rejection_escalates_and_astra_can_close(
    tmp_path: Path, monkeypatch
) -> None:
    invoker = FakeInvoker()
    invoker.add(
        "backend",
        "review",
        result(
            "changes_required",
            summary="Repeat the same already-satisfied coordination evidence.",
        ),
    )
    invoker.add("backend", "lead", result("done", summary="Closeout evidence is sufficient"))
    supervisor = Supervisor(make_config(tmp_path, observe_only=False), invoker)
    monkeypatch.setattr(supervisor, "_has_current_green_validation", lambda *_args: True)
    state = WorkerState(
        role="backend",
        status=WorkerStatus.REVIEWING,
        task=TaskItem(
            "backend-closeout-evidence-v1",
            (
                "Complete only the coordination closeout. Do not change the accepted "
                "implementation. Do not modify backend source/tests."
            ),
        ),
        task_base_head="abc123",
        review_failures=1,
    )

    supervisor._review("backend", state, "ctx")

    saved = supervisor.load_state("backend")
    assert ("backend", "review", "backend-closeout-evidence-v1") in invoker.calls
    assert ("backend", "lead", "backend-closeout-evidence-v1") in invoker.calls
    assert saved.status is WorkerStatus.IDLE
    assert saved.task is None


def test_coordination_review_circuit_clears_pending_review_before_adjudication(
    tmp_path: Path, monkeypatch
) -> None:
    from scripts.dev_orchestrator.handoff import read_pending_result
    from scripts.dev_orchestrator.storage import AtomicJsonStore

    config = make_config(tmp_path, observe_only=False)
    invoker = FakeInvoker()
    invoker.add("backend", "lead", result("done", summary="closeout complete"))
    supervisor = Supervisor(config, invoker)
    monkeypatch.setattr(supervisor, "_has_current_green_validation", lambda *_args: True)
    task = TaskItem(
        "backend-closeout-evidence-v1",
        (
            "Complete only the coordination closeout. Do not change the accepted "
            "implementation. Do not modify backend source/tests."
        ),
    )
    state = WorkerState(
        role="backend",
        status=WorkerStatus.REVIEWING,
        task=task,
        task_base_head="abc123",
        review_failures=1,
    )
    supervisor.save_state(state)
    invocation_id = "review-pending-before-adjudication"
    AtomicJsonStore(config.coordination_root / "handoff" / "backend.json").write(
        {
            "owner": "supervisor",
            "pending_result": {
                "role": "backend",
                "phase": "review",
                "task_id": task.task_id,
                "invocation_id": invocation_id,
                "pre_head": "abc123",
                "post_head": "abc123",
                "result": {
                    "outcome": "changes_required",
                    "summary": "repeat stale closeout objection",
                    "next_action": "repeat evidence",
                    "needs_user": False,
                    "blocker_reason": "",
                    "task_id": task.task_id,
                    "task_prompt": "",
                    "role": "backend",
                    "invocation_id": invocation_id,
                    "coordination_messages": [],
                    "issue_updates": [],
                },
            }
        }
    )

    supervisor._review("backend", state, "ctx")

    saved = supervisor.load_state("backend")
    assert read_pending_result(config, "backend") is None
    assert ("backend", "lead", task.task_id) in invoker.calls
    assert saved.status is WorkerStatus.IDLE
    assert saved.task is None


def test_astra_done_cannot_close_product_code_task_even_with_green_receipt(
    tmp_path: Path, monkeypatch
) -> None:
    invoker = FakeInvoker()
    invoker.add("backend", "lead", result("done", summary="done"))
    supervisor = Supervisor(make_config(tmp_path, observe_only=False), invoker)
    monkeypatch.setattr(supervisor, "_has_current_green_validation", lambda *_args: True)
    state = WorkerState(
        role="backend",
        status=WorkerStatus.IMPLEMENTING,
        task=TaskItem("B-SOURCE", "Modify backend source and tests."),
        task_base_head="abc123",
    )

    supervisor._adjudicate("backend", state, "ctx")

    saved = supervisor.load_state("backend")
    assert saved.status is WorkerStatus.BLOCKED_SYSTEM
    assert "product-code task" in saved.blocked_reason


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


def test_runner_pipe_block_retries_with_recovery_then_exhausts(
    tmp_path: Path, monkeypatch
) -> None:
    reason = (
        "CreateProcess: Failed to create unified exec process: "
        "timed out after 15000ms connecting runner pipe-in"
    )
    blocked = AgentResult(
        outcome="blocked_user",
        summary=reason,
        next_action="Restore terminal execution",
        needs_user=True,
        blocker_reason=reason,
        task_id="B-PIPE",
    )
    invoker = FakeInvoker()
    invoker.add("backend", "implement", blocked, blocked, blocked, blocked)
    resets: list[bool] = []
    monkeypatch.setattr(
        "scripts.dev_orchestrator.supervisor.recover_codex_windows_terminal_runner",
        lambda: resets.append(True) or True,
    )
    supervisor = Supervisor(make_config(tmp_path, observe_only=False), invoker)
    supervisor.save_state(
        WorkerState(
            role="backend",
            status=WorkerStatus.IMPLEMENTING,
            task=TaskItem("B-PIPE", "Exercise runner recovery"),
        )
    )

    supervisor._run_role("backend")

    first = supervisor.load_state("backend")
    assert first.status is WorkerStatus.BLOCKED_SYSTEM
    assert first.blocker_kind == "system"
    assert first.resume_status is WorkerStatus.IMPLEMENTING
    assert first.implementation_failures == 1
    assert "automatic retry 1/4" in first.blocked_reason

    supervisor._run_role("backend")
    second = supervisor.load_state("backend")
    assert second.status is WorkerStatus.BLOCKED_SYSTEM
    assert second.resume_status is WorkerStatus.IMPLEMENTING
    assert second.implementation_failures == 2
    assert "automatic retry 2/4" in second.blocked_reason

    supervisor._run_role("backend")
    third = supervisor.load_state("backend")
    assert third.status is WorkerStatus.BLOCKED_SYSTEM
    assert third.resume_status is WorkerStatus.IMPLEMENTING
    assert third.implementation_failures == 3
    assert "automatic retry 3/4" in third.blocked_reason

    supervisor._run_role("backend")
    exhausted = supervisor.load_state("backend")
    assert exhausted.status is WorkerStatus.BLOCKED_SYSTEM
    assert exhausted.blocker_kind == "system"
    assert exhausted.resume_status is None
    assert exhausted.implementation_failures == 4
    assert "recovery exhausted after 4 attempts" in exhausted.blocked_reason
    assert resets == [True, True, True, True]


def test_runner_pipe_invocation_error_recovers_before_retry(
    tmp_path: Path, monkeypatch
) -> None:
    from scripts.dev_orchestrator.runner import AgentInvocationError

    reason = (
        'CreateProcess: Failed to create unified exec process: '
        'timed out after 15000ms connecting runner pipe-in'
    )
    resets: list[bool] = []
    monkeypatch.setattr(
        'scripts.dev_orchestrator.supervisor.recover_codex_windows_terminal_runner',
        lambda: resets.append(True) or True,
    )
    supervisor = Supervisor(make_config(tmp_path, observe_only=False), FakeInvoker())
    state = WorkerState(
        role='backend',
        status=WorkerStatus.IMPLEMENTING,
        task=TaskItem('B-PIPE-EXC', 'Exercise invocation-error runner recovery'),
    )

    supervisor._handle_invocation_error(
        'backend',
        state,
        'implement',
        AgentInvocationError('codex', 'transient', reason),
        'ctx',
    )

    saved = supervisor.load_state('backend')
    assert resets == [True]
    assert saved.status is WorkerStatus.BLOCKED_SYSTEM
    assert saved.blocker_kind == 'system'
    assert saved.resume_status is WorkerStatus.IMPLEMENTING
    assert saved.implementation_failures == 1
    assert 'automatic retry 1/4' in saved.blocked_reason


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


def test_passing_test_issue_review_hands_retest_to_testing_and_keeps_backend_moving(
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
    assert state.status is WorkerStatus.IDLE
    assert state.task is None
    assert state.blocker_kind == ""
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


def test_owner_defer_releases_existing_retest_block(tmp_path: Path) -> None:
    invoker = FakeInvoker()
    invoker.add(
        "backend", "lead", result("assign", task_id="US-101", task_prompt="Continue campaign")
    )
    config = replace(
        make_config(tmp_path, observe_only=False),
        deferred_issue_ids=("STORY-S35-SPEECH-ROUTER",),
    )
    issue = config.coordination_root / "issues" / "STORY-S35-SPEECH-ROUTER.md"
    issue.write_text(
        "# STORY-S35-SPEECH-ROUTER\n\nStatus: fixed-needs-retest\nOwner: backend\n",
        encoding="utf-8",
    )
    supervisor = Supervisor(config, invoker)
    supervisor.save_state(
        WorkerState(
            role="backend",
            status=WorkerStatus.BLOCKED_USER,
            task=TaskItem("STORY-S35-SPEECH-ROUTER", "Await canonical testing verification"),
            blocker_kind="retest",
            resume_status=WorkerStatus.PLANNING,
        )
    )

    supervisor._run_role("backend")

    state = supervisor.load_state("backend")
    assert state.status is WorkerStatus.IMPLEMENTING
    assert state.task is not None
    assert state.task.task_id == "US-101"


def test_dequeue_skips_owner_deferred_task_prefix(tmp_path: Path) -> None:
    invoker = FakeInvoker()
    config = replace(
        make_config(tmp_path, observe_only=False),
        deferred_task_prefixes=("S35-",),
    )
    supervisor = Supervisor(config, invoker)
    supervisor.enqueue("backend", TaskItem("S35-BACKEND", "deferred"))
    supervisor.enqueue("backend", TaskItem("US-101", "production readiness"))

    task = supervisor._dequeue("backend")

    assert task == TaskItem("US-101", "production readiness")
    remaining = supervisor._queue_store("backend").read(default=[])
    assert [item["task_id"] for item in remaining] == ["S35-BACKEND"]


def test_planner_assignment_rejects_owner_deferred_task_prefix(tmp_path: Path) -> None:
    invoker = FakeInvoker()
    invoker.add(
        "backend",
        "lead",
        result(
            "assign",
            task_id="S35-BACKEND-CANONICAL-CONTRACT-002",
            task_prompt="old speech work",
        ),
    )
    config = replace(
        make_config(tmp_path, observe_only=False),
        deferred_task_prefixes=("S35-",),
    )
    supervisor = Supervisor(config, invoker)

    supervisor._run_role("backend")

    state = supervisor.load_state("backend")
    assert state.status is WorkerStatus.BLOCKED_SYSTEM
    assert state.task is None
    assert "deferred task prefix" in state.blocked_reason.lower()


def test_done_claim_does_not_promote_owner_deferred_issue(tmp_path: Path, monkeypatch) -> None:
    from scripts.dev_orchestrator import supervisor as supervisor_module
    from scripts.dev_orchestrator.completion import CompletionGate

    invoker = FakeInvoker()
    config = replace(
        make_config(tmp_path, observe_only=False),
        deferred_issue_ids=("STORY-S35-SPEECH-ROUTER",),
    )
    issue = config.coordination_root / "issues" / "STORY-S35-SPEECH-ROUTER.md"
    issue.write_text(
        "# STORY-S35-SPEECH-ROUTER\n\nStatus: open\nOwner: backend\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        supervisor_module,
        "evaluate_completion",
        lambda *_args: CompletionGate(False, ("release campaign remains active",), False),
    )

    supervisor = Supervisor(config, invoker)
    supervisor._done_claim("backend", WorkerState("backend"), "ctx")

    state = supervisor.load_state("backend")
    assert state.status is WorkerStatus.IMPLEMENTING
    assert state.task is not None
    assert state.task.task_id == "FINAL-backend"


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


def test_validation_infrastructure_retry_reuses_durable_result_without_reinvoking_worker(
    tmp_path: Path,
) -> None:
    task = TaskItem("B-RECOVER-VALIDATE", "Validate durable implementation")
    failing_config = replace(
        make_config(tmp_path, observe_only=False),
        metadata={
            "iteration_validation": {
                "enabled": True,
                "backend": {
                    "gates": [
                        {
                            "name": "missing runner",
                            "command": ["definitely-not-an-askrex-executable-9d31"],
                        }
                    ]
                },
            }
        },
    )
    invoker = FakeInvoker()
    supervisor = Supervisor(failing_config, invoker)
    supervisor.save_state(WorkerState(role="backend", status=WorkerStatus.IMPLEMENTING, task=task))
    pending = {
        "phase": "implement",
        "role": "backend",
        "task_id": task.task_id,
        "invocation_id": "inv-validation-recover-1",
        "pre_head": "aaa",
        "post_head": "bbb",
        "result": {
            "outcome": "ready_for_review",
            "summary": "implementation already published",
            "next_action": "validate it",
            "needs_user": False,
            "blocker_reason": "",
            "task_id": task.task_id,
            "task_prompt": "",
            "role": "backend",
            "invocation_id": "inv-validation-recover-1",
            "coordination_messages": [],
            "issue_updates": [],
        },
    }
    lease = failing_config.coordination_root / "handoff" / "backend.json"
    lease.parent.mkdir(parents=True, exist_ok=True)
    lease.write_text(
        __import__("json").dumps({"owner": "supervisor", "head": "bbb", "pending_result": pending}),
        encoding="utf-8",
    )

    supervisor._run_role("backend")

    blocked = supervisor.load_state("backend")
    assert blocked.status is WorkerStatus.BLOCKED_SYSTEM
    assert invoker.calls == []
    assert "pending_result" in __import__("json").loads(lease.read_text(encoding="utf-8"))

    passing_config = replace(
        failing_config,
        metadata={
            "iteration_validation": {
                "enabled": True,
                "backend": {
                    "gates": [
                        {"name": "validation restored", "command": [sys.executable, "-c", "pass"]}
                    ]
                },
            }
        },
    )
    recovered = Supervisor(passing_config, invoker)
    recovered._run_role("backend")

    state = recovered.load_state("backend")
    assert state.status is WorkerStatus.REVIEWING
    assert invoker.calls == []
    assert "pending_result" not in __import__("json").loads(lease.read_text(encoding="utf-8"))


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


def test_task_base_head_is_stable_until_task_completes(tmp_path: Path) -> None:
    import subprocess

    config = make_config(tmp_path, observe_only=False)
    assert config.backend_root is not None
    repo = config.backend_root
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
    base_head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()

    invoker = FakeInvoker()
    invoker.add("backend", "implement", result("continue"), result("ready_for_review"))
    invoker.add("backend", "review", result("changes_required", summary="fix"))
    invoker.add("backend", "implement", result("ready_for_review"))
    invoker.add("backend", "review", result("pass"))
    supervisor = Supervisor(config, invoker)
    supervisor.enqueue("backend", TaskItem("B-BASE", "work"))

    supervisor._run_role("backend")
    assert supervisor.load_state("backend").task_base_head == base_head
    supervisor._run_role("backend")
    assert supervisor.load_state("backend").task_base_head == base_head
    supervisor._run_role("backend")
    assert supervisor.load_state("backend").task_base_head == base_head
    supervisor._run_role("backend")
    assert supervisor.load_state("backend").task_base_head == base_head
    supervisor._run_role("backend")
    completed = supervisor.load_state("backend")
    assert completed.status is WorkerStatus.IDLE
    assert completed.task_base_head == ""


def test_openai_api_failure_categories_never_enter_banked_reset_flow(tmp_path: Path) -> None:
    from scripts.dev_orchestrator.runner import AgentInvocationError

    expected_user = {"auth", "billing", "budget"}
    categories = (
        "auth",
        "rate_limit",
        "billing",
        "budget",
        "timeout",
        "transient",
        "invalid_output",
        "failed",
    )
    for kind in categories:
        case_root = tmp_path / kind
        case_root.mkdir()
        config = make_config(case_root, observe_only=False)
        supervisor = Supervisor(config, FakeInvoker())
        original = WorkerState(
            role="backend",
            status=WorkerStatus.REVIEWING,
            task=TaskItem("B-API", "Review API failure"),
            review_failures=2,
        )
        supervisor._handle_invocation_error(
            "backend",
            original,
            "review",
            AgentInvocationError("openai", kind, f"openai {kind}"),
            "ctx",
        )
        saved = supervisor.load_state("backend")
        expected_status = (
            WorkerStatus.BLOCKED_USER if kind in expected_user else WorkerStatus.BLOCKED_SYSTEM
        )
        assert saved.status is expected_status
        assert saved.blocker_kind != "usage_limit"
        assert saved.resume_status is WorkerStatus.REVIEWING
        assert saved.review_failures == 2
        assert config.banked_resets_remaining == 3
        alerts = list((config.coordination_root / "alerts").glob("*.json"))
        assert not any(
            "banked reset" in path.read_text(encoding="utf-8").lower() for path in alerts
        )


def test_openai_usage_limit_classification_fails_closed_without_reset_flow(tmp_path: Path) -> None:
    from scripts.dev_orchestrator.runner import AgentInvocationError

    config = make_config(tmp_path, observe_only=False)
    supervisor = Supervisor(config, FakeInvoker())
    state = WorkerState(
        role="backend",
        status=WorkerStatus.REVIEWING,
        task=TaskItem("B-API", "Review API failure"),
    )

    supervisor._handle_invocation_error(
        "backend",
        state,
        "review",
        AgentInvocationError("openai", "usage_limit", "invalid API classification"),
        "ctx",
    )

    saved = supervisor.load_state("backend")
    assert saved.status is WorkerStatus.BLOCKED_SYSTEM
    assert saved.blocker_kind == "system"
    assert saved.resume_status is WorkerStatus.REVIEWING
    assert "invalid" in saved.blocked_reason.lower()
    assert not list((config.coordination_root / "alerts").glob("*.json"))


def test_openai_api_blockers_preserve_exact_resume_phase(tmp_path: Path) -> None:
    from scripts.dev_orchestrator.runner import AgentInvocationError

    cases = (
        ("implement", WorkerStatus.IMPLEMENTING, WorkerStatus.IMPLEMENTING),
        ("review", WorkerStatus.REVIEWING, WorkerStatus.REVIEWING),
        ("plan", WorkerStatus.IDLE, WorkerStatus.PLANNING),
        ("lead", WorkerStatus.IDLE, WorkerStatus.PLANNING),
        ("adjudicate", WorkerStatus.IMPLEMENTING, WorkerStatus.IMPLEMENTING),
        ("adjudicate", WorkerStatus.REVIEWING, WorkerStatus.REVIEWING),
    )
    for index, (phase, starting_status, expected_resume) in enumerate(cases):
        case_root = tmp_path / f"case-{index}"
        case_root.mkdir()
        config = make_config(case_root, observe_only=False)
        supervisor = Supervisor(config, FakeInvoker())
        state = WorkerState(
            role="backend",
            status=starting_status,
            task=TaskItem("B-PHASE", "Preserve phase"),
        )

        supervisor._handle_invocation_error(
            "backend",
            state,
            phase,
            AgentInvocationError("openai", "transient", "temporary API failure"),
            "ctx",
        )

        saved = supervisor.load_state("backend")
        assert saved.status is WorkerStatus.BLOCKED_SYSTEM
        assert saved.resume_status is expected_resume
        assert saved.task == state.task


def test_review_pass_records_completed_task_and_planner_cannot_resurrect_it(
    tmp_path: Path,
) -> None:
    invoker = FakeInvoker()
    invoker.add("backend", "implement", result("ready_for_review"))
    invoker.add("backend", "review", result("pass"))
    config = make_config(tmp_path, observe_only=False)
    repo = config.backend_root
    assert repo is not None
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "tests@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "AskRex Tests"], cwd=repo, check=True)
    (repo / "tracked.txt").write_text("baseline\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=repo, check=True, capture_output=True)
    supervisor = Supervisor(config, invoker)
    supervisor.enqueue(
        "backend",
        TaskItem(
            "backend-us101-root-capture",
            "Complete US-101 speculative privacy root capture.",
        ),
    )

    supervisor.run_cycle()
    supervisor.run_cycle()
    assert supervisor.load_state("backend").status is WorkerStatus.IDLE

    records = supervisor.completed_tasks.records("backend")
    assert len(records) == 1
    assert "US-101" in records[0]["keys"]

    invoker.add(
        "backend",
        "lead",
        result(
            "assign",
            task_id="backend-us101-speculative-failure-privacy-root-capture",
            task_prompt="Reopen US-101 because an old mailbox message still says needs response.",
        ),
    )
    supervisor._run_role("backend")

    state = supervisor.load_state("backend")
    assert state.status is WorkerStatus.IDLE
    assert state.task is None
    assert not any(call[:2] == ("backend", "implement") for call in invoker.calls[3:])
    alerts = list((config.coordination_root / "alerts").glob("duplicate-completed-task-backend-*.json"))
    assert len(alerts) == 1


def test_completed_task_remains_closed_when_reviewed_head_is_ancestor(
    tmp_path: Path,
) -> None:
    config = make_config(tmp_path, observe_only=False)
    repo = config.backend_root
    assert repo is not None
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "tests@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "AskRex Tests"], cwd=repo, check=True)
    (repo / "tracked.txt").write_text("one\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "one"], cwd=repo, check=True, capture_output=True)
    completed_head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()

    supervisor = Supervisor(config, FakeInvoker())
    completed = TaskItem("backend-us101-done", "Finish US-101.")
    supervisor.completed_tasks.record(
        "backend",
        completed,
        completed_head=completed_head,
        source="independent_review",
    )

    (repo / "tracked.txt").write_text("two\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "two"], cwd=repo, check=True, capture_output=True)
    current_head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()

    record = supervisor.completed_tasks.matching_record(
        "backend",
        TaskItem("backend-us101-reopen", "Old evidence asks to reopen US-101."),
        repo=repo,
        current_head=current_head,
    )
    assert record is not None
    assert record["completed_head"] == completed_head
