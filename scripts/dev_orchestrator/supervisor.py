from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Protocol

from .coordination import build_coordination_context
from .routing import select_implementer_model, select_reviewer_model
from .storage import AtomicJsonStore
from .types import AgentResult, OrchestratorConfig, TaskItem, WorkerState, WorkerStatus


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
        claude_session_id=str(data.get("claude_session_id", "")),
        codex_session_id=str(data.get("codex_session_id", "")),
    )


class Supervisor:
    def __init__(self, config: OrchestratorConfig, invoker: AgentInvoker) -> None:
        self.config = config
        self.invoker = invoker
        self.root = config.coordination_root

    def _state_store(self, role: str) -> AtomicJsonStore:
        return AtomicJsonStore(self.root / "state" / f"{role}.json")

    def _queue_store(self, role: str) -> AtomicJsonStore:
        return AtomicJsonStore(self.root / "queues" / f"{role}.json")

    def load_state(self, role: str) -> WorkerState:
        return _state_from_dict(role, self._state_store(role).read())

    def save_state(self, state: WorkerState) -> None:
        self._state_store(state.role).write(state.to_dict())

    def enqueue(self, role: str, task: TaskItem) -> None:
        store = self._queue_store(role)
        queue = list(store.read(default=[]))
        queue.append(task.to_dict())
        store.write(queue)

    def _dequeue(self, role: str) -> TaskItem | None:
        store = self._queue_store(role)
        queue = list(store.read(default=[]))
        if not queue:
            return None
        task = TaskItem(**queue.pop(0))
        store.write(queue)
        return task

    def _repo_root(self, role: str) -> Path:
        root = self.config.backend_root if role == "backend" else self.config.mobile_root
        if root is None:
            raise ValueError(f"missing repository root for {role}")
        if self.config.frozen_worktree and root.resolve() == self.config.frozen_worktree.resolve():
            raise ValueError("frozen worktree cannot be used by supervisor")
        return root

    def run_cycle(self) -> None:
        for role in ("backend", "mobile"):
            self._run_role(role)

    def _run_role(self, role: str) -> None:
        state = self.load_state(role)
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

    def _plan(self, role: str, state: WorkerState, context: str) -> None:
        result = self.invoker.lead(role, replace(state, status=WorkerStatus.PLANNING), context)
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
            self.save_state(replace(state, status=WorkerStatus.DONE, blocked_reason=""))
            return
        self._save_block_or_failure(state, result)

    def _implement(self, role: str, state: WorkerState, context: str) -> None:
        assert state.task is not None
        model = select_implementer_model(state, self.config)
        result = self.invoker.implement(role, state, state.task, context, model)
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
            self.save_state(
                replace(
                    state,
                    status=WorkerStatus.IMPLEMENTING,
                    implementation_failures=state.implementation_failures + 1,
                    blocked_reason=result.summary,
                )
            )
            return
        self._save_block_or_failure(state, result)

    def _review(self, role: str, state: WorkerState, context: str) -> None:
        assert state.task is not None
        model = select_reviewer_model(state, self.config)
        result = self.invoker.review(role, state, state.task, context, model)
        if result.outcome == "pass":
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
            feedback = "\n\n".join(
                part for part in (result.summary, result.next_action) if part
            )
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
            )
        )
