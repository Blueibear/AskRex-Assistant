from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Protocol

from .alerts import AlertSink
from .completion import evaluate_completion
from .coordination import (
    _issue_status,
    active_owned_issues,
    apply_agent_updates,
    build_coordination_context,
    recover_coordination_transactions,
)
from .handoff import (
    clear_pending_result,
    read_pending_result,
    role_lease_lock_path,
    validate_handoff,
)
from .lifecycle import ControlPlaneLock
from .routing import SOL_MODEL, select_implementer_model, select_reviewer_model
from .runner import AgentInvocationError
from .schema import validate_agent_result
from .storage import AtomicJsonStore
from .types import AgentResult, OrchestratorConfig, TaskItem, WorkerState, WorkerStatus
from .usage import UsageBudget, handle_usage_limit


class AgentInvoker(Protocol):
    def implement(self, role, state, task, context, model) -> AgentResult: ...

    def review(self, role, state, task, context, model) -> AgentResult: ...

    def lead(self, role, state, context, task=None) -> AgentResult: ...


def _state_from_dict(role: str, data: dict | None) -> WorkerState:
    if not data:
        return WorkerState(role=role)
    task_data = data.get("task")
    task = TaskItem(**task_data) if task_data else None
    return WorkerState(
        role=role,
        status=WorkerStatus(data.get("status", "idle")),
        task=task,
        iteration=int(data.get("iteration", 0)),
        implementation_failures=int(data.get("implementation_failures", 0)),
        review_failures=int(data.get("review_failures", 0)),
        blocked_reason=str(data.get("blocked_reason", "")),
        blocker_kind=str(data.get("blocker_kind", "")),
        resume_status=(WorkerStatus(data["resume_status"]) if data.get("resume_status") else None),
        claude_session_id=str(data.get("claude_session_id", "")),
        codex_session_id=str(data.get("codex_session_id", "")),
        last_result_invocation_id=str(data.get("last_result_invocation_id", "")),
    )


class Supervisor:
    def __init__(self, config: OrchestratorConfig, invoker: AgentInvoker) -> None:
        self.config = config
        self.invoker = invoker
        self.root = config.coordination_root
        recover_coordination_transactions(self.root)
        self.alerts = AlertSink(self.root / "alerts")
        self._result_context = threading.local()

    def _state_store(self, role: str) -> AtomicJsonStore:
        return AtomicJsonStore(self.root / "state" / f"{role}.json")

    def _queue_store(self, role: str) -> AtomicJsonStore:
        return AtomicJsonStore(self.root / "queues" / f"{role}.json")

    def load_state(self, role: str) -> WorkerState:
        return _state_from_dict(role, self._state_store(role).read())

    def save_state(self, state: WorkerState) -> None:
        context_role = getattr(self._result_context, "role", "")
        invocation_id = getattr(self._result_context, "invocation_id", "")
        if invocation_id and context_role == state.role:
            state = replace(state, last_result_invocation_id=invocation_id)
        with ControlPlaneLock(self.root / "control-plane.lock"):
            self._state_store(state.role).write(state.to_dict())
        if invocation_id and context_role == state.role:
            clear_pending_result(self.config, state.role, invocation_id=invocation_id)
            self._result_context.role = ""
            self._result_context.invocation_id = ""

    def enqueue(self, role: str, task: TaskItem) -> None:
        with ControlPlaneLock(self.root / "control-plane.lock"):
            store = self._queue_store(role)
            queue = list(store.read(default=[]))
            queue.append(task.to_dict())
            store.write(queue)

    def _dequeue(self, role: str) -> TaskItem | None:
        with ControlPlaneLock(self.root / "control-plane.lock"):
            store = self._queue_store(role)
            queue = list(store.read(default=[]))
            if not queue:
                return None
            task = TaskItem(**queue.pop(0))
            store.write(queue)
            return task

    def _set_result_context(self, role: str, result: AgentResult) -> None:
        if not result.invocation_id:
            return
        pending = read_pending_result(self.config, role)
        if not pending:
            return
        if str(pending.get("invocation_id", "")) != result.invocation_id:
            raise ValueError("durable result receipt invocation mismatch")
        self._result_context.role = role
        self._result_context.invocation_id = result.invocation_id

    def _recover_result(
        self, role: str, state: WorkerState, *, phase: str, task_id: str
    ) -> AgentResult | None:
        pending = read_pending_result(self.config, role)
        if not pending:
            return None
        invocation_id = str(pending.get("invocation_id", ""))
        if state.last_result_invocation_id and state.last_result_invocation_id == invocation_id:
            clear_pending_result(self.config, role, invocation_id=invocation_id)
            return None
        if str(pending.get("role", "")) != role:
            raise ValueError("pending result role mismatch")
        if str(pending.get("phase", "")) != phase:
            raise ValueError("pending result phase mismatch")
        if str(pending.get("task_id", "")) != task_id:
            raise ValueError("pending result task mismatch")
        raw_result = pending.get("result")
        if not isinstance(raw_result, dict):
            raise ValueError("pending result payload is invalid")
        result = validate_agent_result(raw_result)
        if result.role != role or result.invocation_id != invocation_id:
            raise ValueError("pending result identity mismatch")
        if phase in {"implement", "review"} and result.task_id != task_id:
            raise ValueError("pending result bound task mismatch")
        return result

    def _invoke_or_recover(
        self, role: str, state: WorkerState, *, phase: str, task_id: str, invoke
    ) -> AgentResult:
        result = self._recover_result(role, state, phase=phase, task_id=task_id)
        if result is None:
            result = invoke()
        self._set_result_context(role, result)
        return result

    def _repo_root(self, role: str) -> Path:
        root = self.config.backend_root if role == "backend" else self.config.mobile_root
        if root is None:
            raise ValueError(f"missing repository root for {role}")
        if self.config.frozen_worktree and root.resolve() == self.config.frozen_worktree.resolve():
            raise ValueError("frozen worktree cannot be used by supervisor")
        return root

    def run_cycle(self) -> None:
        if self.config.observe_only:
            return
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="askrex-workstream") as pool:
            futures = {role: pool.submit(self._run_role, role) for role in ("backend", "mobile")}
            for role, future in futures.items():
                try:
                    future.result()
                except Exception as exc:
                    state = self.load_state(role)
                    self.save_state(
                        replace(
                            state,
                            status=WorkerStatus.BLOCKED_SYSTEM,
                            blocked_reason=f"supervisor workstream failure: {exc}",
                            blocker_kind="system",
                            resume_status=state.status,
                        )
                    )

    def _run_role(self, role: str) -> None:
        state = self.load_state(role)
        if state.status is WorkerStatus.BLOCKED_SYSTEM:
            if state.resume_status is None:
                return
            state = replace(
                state,
                status=state.resume_status,
                blocked_reason="",
                blocker_kind="",
                resume_status=None,
            )
            self.save_state(state)
        if state.status is WorkerStatus.BLOCKED_USER and state.blocker_kind == "retest":
            if state.task is not None:
                issue = self.root / "issues" / f"{state.task.task_id}.md"
                if (
                    issue.is_file()
                    and _issue_status(issue.read_text(encoding="utf-8", errors="replace"))
                    == "verified"
                ):
                    state = WorkerState(role=role, status=WorkerStatus.IDLE)
                    self.save_state(state)
                else:
                    return
            else:
                pending_retests = [
                    issue
                    for issue in active_owned_issues(self.root, role)
                    if _issue_status(issue.read_text(encoding="utf-8", errors="replace"))
                    == "fixed-needs-retest"
                ]
                if pending_retests:
                    return
                state = WorkerState(
                    role=role,
                    status=WorkerStatus.IDLE,
                    claude_session_id=state.claude_session_id,
                    codex_session_id=state.codex_session_id,
                )
                self.save_state(state)
        if state.status in {WorkerStatus.BLOCKED_USER, WorkerStatus.DONE}:
            return
        if self.config.observe_only:
            return

        context = build_coordination_context(self.root, role)
        if state.task is None:
            queued = self._dequeue(role)
            if queued is not None:
                state = replace(state, task=queued, status=WorkerStatus.IMPLEMENTING)
            else:
                self._plan(role, state, context)
                return

        if state.status is WorkerStatus.REVIEWING:
            self._review(role, state, context)
            return
        self._implement(role, state, context)

    def _accept_result(
        self, role: str, state: WorkerState, result: AgentResult, *, allow_issue_updates: bool
    ) -> bool:
        if result.needs_user and result.outcome != "blocked_user":
            self.save_state(
                replace(
                    state,
                    status=WorkerStatus.BLOCKED_USER,
                    blocked_reason=result.blocker_reason or result.summary,
                    blocker_kind="human",
                    resume_status=state.status,
                )
            )
            return False
        if result.outcome == "blocked_user" and (
            not result.needs_user or not result.blocker_reason.strip()
        ):
            self.save_state(
                replace(
                    state,
                    status=WorkerStatus.BLOCKED_SYSTEM,
                    blocked_reason="invalid blocked_user agent result",
                    blocker_kind="system",
                    resume_status=state.status,
                )
            )
            return False
        try:
            apply_agent_updates(
                self.root,
                role,
                result,
                allow_issue_updates=allow_issue_updates,
                task_id=state.task.task_id if state.task else "",
            )
        except (ValueError, OSError) as exc:
            self.save_state(
                replace(
                    state,
                    status=WorkerStatus.BLOCKED_SYSTEM,
                    blocked_reason=f"agent result rejected by supervisor safety gate: {exc}",
                    blocker_kind="system",
                    resume_status=state.status,
                )
            )
            return False
        return True

    def _done_claim(self, role: str, state: WorkerState, context: str) -> None:
        issues = active_owned_issues(self.root, role)
        if issues:
            first = issues[0]
            text = first.read_text(encoding="utf-8", errors="replace")
            if "status: fixed-needs-retest" in text.lower():
                self.save_state(
                    replace(
                        state,
                        status=WorkerStatus.BLOCKED_USER,
                        task=TaskItem(first.stem, "Await canonical testing verification"),
                        blocked_reason=f"{first.stem} requires testing/retest before completion",
                        blocker_kind="retest",
                        resume_status=WorkerStatus.PLANNING,
                    )
                )
                return
            self.save_state(
                replace(
                    state,
                    status=WorkerStatus.IMPLEMENTING,
                    task=TaskItem(
                        first.stem, f"Resolve remaining owned issue {first.stem}.\n\n{text[:6000]}"
                    ),
                    blocked_reason="",
                )
            )
            return
        gate = evaluate_completion(self.config, role)
        if not gate.ready:
            reason = "; ".join(gate.reasons)
            if gate.needs_user:
                self.save_state(
                    replace(
                        state,
                        status=WorkerStatus.BLOCKED_USER,
                        task=None,
                        blocked_reason=reason,
                        blocker_kind=gate.blocker_kind or "human",
                        resume_status=WorkerStatus.PLANNING,
                    )
                )
                return
            prompt = "Satisfy deterministic final completion gates: " + reason
            self.save_state(
                replace(
                    state,
                    status=WorkerStatus.IMPLEMENTING,
                    task=TaskItem(f"FINAL-{role}", prompt),
                    blocked_reason="",
                )
            )
            return
        final_task = TaskItem(
            f"FINAL-VERIFY-{role}",
            "Independently verify final workstream readiness, tests, CI, docs, and safety gates.",
        )
        try:
            result = self._invoke_or_recover(
                role,
                state,
                phase="review",
                task_id=final_task.task_id,
                invoke=lambda: self.invoker.review(role, state, final_task, context, SOL_MODEL),
            )
        except AgentInvocationError as exc:
            self._handle_invocation_error(role, state, "review", exc, context)
            return
        if not self._accept_result(role, state, result, allow_issue_updates=False):
            return
        if result.outcome == "pass":
            with ControlPlaneLock(role_lease_lock_path(self.config, role)):
                post_review_gate = evaluate_completion(self.config, role)
                if post_review_gate.ready:
                    validate_handoff(self.config, role)
                    self.save_state(replace(state, status=WorkerStatus.DONE, blocked_reason=""))
                    return
            if not post_review_gate.ready:
                reason = "; ".join(post_review_gate.reasons)
                if post_review_gate.needs_user:
                    self.save_state(
                        replace(
                            state,
                            status=WorkerStatus.BLOCKED_USER,
                            task=None,
                            blocked_reason=reason,
                            blocker_kind=post_review_gate.blocker_kind or "human",
                            resume_status=WorkerStatus.PLANNING,
                        )
                    )
                else:
                    self.save_state(
                        replace(
                            state,
                            status=WorkerStatus.IMPLEMENTING,
                            task=TaskItem(
                                f"FINAL-{role}",
                                "Satisfy deterministic final completion gates: " + reason,
                            ),
                            blocked_reason="",
                        )
                    )
                return
            return
        if result.outcome == "changes_required":
            prompt = "\n\n".join(part for part in (result.summary, result.next_action) if part)
            self.save_state(
                replace(
                    state, status=WorkerStatus.IMPLEMENTING, task=TaskItem(f"FINAL-{role}", prompt)
                )
            )
            return
        self._save_block_or_failure(state, result)

    def _plan(self, role: str, state: WorkerState, context: str) -> None:
        try:
            result = self._invoke_or_recover(
                role,
                state,
                phase="plan",
                task_id="",
                invoke=lambda: self.invoker.lead(
                    role, replace(state, status=WorkerStatus.PLANNING), context
                ),
            )
        except AgentInvocationError as exc:
            self._handle_invocation_error(role, state, "lead", exc, context)
            return
        if not self._accept_result(role, state, result, allow_issue_updates=False):
            return
        if result.outcome == "assign":
            if not result.task_id or not result.task_prompt:
                raise ValueError("lead assign result requires task_id and task_prompt")
            self.save_state(
                replace(
                    state,
                    status=WorkerStatus.IMPLEMENTING,
                    task=TaskItem(result.task_id, result.task_prompt),
                    blocked_reason="",
                )
            )
            return
        if result.outcome == "done":
            self.save_state(state)
            self._done_claim(role, self.load_state(role), context)
            return
        self._save_block_or_failure(state, result)

    def _implement(self, role: str, state: WorkerState, context: str) -> None:
        assert state.task is not None
        model = select_implementer_model(state, self.config)
        try:
            result = self._invoke_or_recover(
                role,
                state,
                phase="implement",
                task_id=state.task.task_id,
                invoke=lambda: self.invoker.implement(role, state, state.task, context, model),
            )
        except AgentInvocationError as exc:
            self._handle_invocation_error(role, state, "implement", exc, context)
            return
        if not self._accept_result(role, state, result, allow_issue_updates=False):
            return
        if result.outcome == "ready_for_review":
            self.save_state(
                replace(state, status=WorkerStatus.REVIEWING, iteration=state.iteration + 1)
            )
            return
        if result.outcome == "continue":
            self.save_state(
                replace(state, status=WorkerStatus.IMPLEMENTING, iteration=state.iteration + 1)
            )
            return
        if result.outcome == "failed":
            failed_state = replace(
                state,
                status=WorkerStatus.IMPLEMENTING,
                implementation_failures=state.implementation_failures + 1,
                blocked_reason=result.summary,
            )
            if failed_state.implementation_failures >= self.config.astra_adjudication_after:
                self._adjudicate(role, failed_state, context)
            else:
                self.save_state(failed_state)
            return
        self._save_block_or_failure(state, result)

    def _review(self, role: str, state: WorkerState, context: str) -> None:
        assert state.task is not None
        model = select_reviewer_model(state, self.config)
        try:
            result = self._invoke_or_recover(
                role,
                state,
                phase="review",
                task_id=state.task.task_id,
                invoke=lambda: self.invoker.review(role, state, state.task, context, model),
            )
        except AgentInvocationError as exc:
            self._handle_invocation_error(role, state, "review", exc, context)
            return
        if not self._accept_result(
            role, state, result, allow_issue_updates=result.outcome == "pass"
        ):
            return
        if result.outcome == "pass":
            if result.issue_updates:
                issue_ids = ", ".join(update.issue_id for update in result.issue_updates)
                self.save_state(
                    replace(
                        state,
                        status=WorkerStatus.BLOCKED_USER,
                        blocked_reason=f"Awaiting testing verification for {issue_ids}",
                        blocker_kind="retest",
                        resume_status=WorkerStatus.IDLE,
                    )
                )
                return
            self.save_state(
                WorkerState(
                    role=role,
                    status=WorkerStatus.IDLE,
                    claude_session_id=state.claude_session_id,
                    codex_session_id=state.codex_session_id,
                )
            )
            return
        if result.outcome == "changes_required":
            feedback = "\n\n".join(part for part in (result.summary, result.next_action) if part)
            self.save_state(
                replace(
                    state,
                    status=WorkerStatus.IMPLEMENTING,
                    task=replace(state.task, feedback=feedback),
                    review_failures=state.review_failures + 1,
                    blocked_reason="",
                )
            )
            return
        if result.outcome == "failed":
            self.save_state(
                replace(
                    state,
                    status=WorkerStatus.REVIEWING,
                    review_failures=state.review_failures + 1,
                    blocked_reason=result.summary,
                )
            )
            return
        self._save_block_or_failure(state, result)

    def _adjudicate(self, role: str, state: WorkerState, context: str) -> None:
        assert state.task is not None
        try:
            result = self._invoke_or_recover(
                role,
                state,
                phase="adjudicate",
                task_id=state.task.task_id,
                invoke=lambda: self.invoker.lead(role, state, context, task=state.task),
            )
        except AgentInvocationError as exc:
            self._handle_invocation_error(role, state, "lead", exc, context)
            return
        if not self._accept_result(role, state, result, allow_issue_updates=False):
            return
        if result.outcome == "assign":
            if not result.task_id or not result.task_prompt:
                raise ValueError("Astra adjudication requires task_id and task_prompt")
            self.save_state(
                replace(
                    state,
                    status=WorkerStatus.IMPLEMENTING,
                    task=TaskItem(result.task_id, result.task_prompt),
                    implementation_failures=0,
                    review_failures=0,
                    blocked_reason="",
                )
            )
            return
        if result.outcome == "done":
            self.save_state(
                replace(
                    state,
                    status=WorkerStatus.BLOCKED_SYSTEM,
                    blocked_reason="Astra cannot mark an active task done before independent review",
                )
            )
            return
        self._save_block_or_failure(state, result)

    def _handle_invocation_error(
        self,
        role: str,
        state: WorkerState,
        phase: str,
        exc: AgentInvocationError,
        context: str,
    ) -> None:
        if exc.kind == "usage_limit":
            budget = UsageBudget(
                self.config.banked_resets_remaining,
                self.config.reserve_last_reset,
            )
            decision = handle_usage_limit(
                budget,
                self.alerts,
                role=role,
                provider=exc.provider,
                reason=exc.detail,
                claude_available=False,
            )
            self.save_state(
                replace(
                    state,
                    status=WorkerStatus.BLOCKED_USER if decision.blocked_user else state.status,
                    blocked_reason=(
                        f"{exc.provider} usage limit: {decision.action}; "
                        f"{decision.budget.banked_resets_remaining} banked resets recorded. {exc.detail}"
                    ),
                    blocker_kind="usage_limit",
                    resume_status={
                        "implement": WorkerStatus.IMPLEMENTING,
                        "review": WorkerStatus.REVIEWING,
                        "lead": WorkerStatus.PLANNING,
                    }.get(phase, state.status),
                )
            )
            return
        if exc.kind == "auth":
            self.alerts.emit(
                role=role,
                kind="auth",
                message=f"{role} needs account/login intervention for {exc.provider}: {exc.detail}",
            )
            self.save_state(
                replace(
                    state,
                    status=WorkerStatus.BLOCKED_USER,
                    blocked_reason=exc.detail,
                    blocker_kind="auth",
                    resume_status=state.status,
                )
            )
            return
        if phase == "implement":
            failed = replace(
                state,
                status=WorkerStatus.IMPLEMENTING,
                implementation_failures=state.implementation_failures + 1,
                blocked_reason=exc.detail,
            )
            if failed.implementation_failures >= self.config.astra_adjudication_after:
                self._adjudicate(role, failed, context)
            else:
                self.save_state(failed)
            return
        if phase == "review":
            failed = replace(
                state,
                status=WorkerStatus.REVIEWING,
                review_failures=state.review_failures + 1,
                blocked_reason=exc.detail,
            )
            if failed.review_failures >= self.config.astra_adjudication_after:
                self._adjudicate(role, failed, context)
            else:
                self.save_state(failed)
            return
        self.save_state(
            replace(state, status=WorkerStatus.BLOCKED_SYSTEM, blocked_reason=exc.detail)
        )

    def _save_block_or_failure(self, state: WorkerState, result: AgentResult) -> None:
        if result.outcome == "blocked_user" or result.needs_user:
            status = WorkerStatus.BLOCKED_USER
        else:
            status = WorkerStatus.BLOCKED_SYSTEM
        self.save_state(
            replace(
                state,
                status=status,
                blocked_reason=result.blocker_reason or result.summary,
                blocker_kind="human" if status is WorkerStatus.BLOCKED_USER else "system",
                resume_status=state.status,
            )
        )
