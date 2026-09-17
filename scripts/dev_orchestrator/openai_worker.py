from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from .coordination import validate_agent_updates
from .evidence import EvidenceError, build_review_evidence
from .handoff import (
    HandoffRequired,
    advance_handoff,
    repo_snapshot,
    role_lease_lock_path,
    validate_handoff,
)
from .lifecycle import ControlPlaneLock
from .openai_budget import (
    OpenAIBudgetExceeded,
    OpenAIBudgetLedger,
    OpenAIBudgetPolicyError,
)
from .openai_transport import OpenAIResponsesTransport, OpenAITransportError
from .runner import AgentInvocationError
from .schema import validate_agent_result
from .scratch import _clone_scratch_repo, _temporary_scratch_root
from .types import AgentResult, OrchestratorConfig, TaskItem, WorkerState


class OpenAIModelWorker:
    """Read-only reasoning worker backed by the OpenAI Responses API."""

    def __init__(
        self,
        config: OrchestratorConfig,
        *,
        transport: Any | None = None,
        budget: Any | None = None,
        invocation_id_factory: Callable[[], str] | None = None,
    ) -> None:
        if not config.openai_worker_enabled:
            raise ValueError("OpenAI worker is disabled")
        if not config.openai_project_hard_limit_confirmed:
            raise ValueError("OpenAI project hard limit must be confirmed")
        if not config.openai_project_id.strip():
            raise ValueError("OpenAI project ID is required")
        self.config = config
        self.transport = transport or OpenAIResponsesTransport(
            project_id=config.openai_project_id,
            timeout_seconds=config.openai_timeout_seconds,
        )
        self.budget = budget or OpenAIBudgetLedger(
            config.coordination_root,
            monthly_cap_usd=config.openai_monthly_budget_usd,
        )
        self._invocation_id_factory = invocation_id_factory or (lambda: str(uuid4()))

    def _repo(self, role: str) -> Path:
        if role == "backend":
            root = self.config.backend_root
        elif role == "mobile":
            root = self.config.mobile_root
        else:
            raise ValueError(f"unsupported OpenAI worker role: {role}")
        if root is None:
            raise HandoffRequired(f"{role} repository root is not configured")
        return root.resolve()

    @staticmethod
    def _reasoning_effort(model: str) -> str:
        return "medium" if model.endswith("terra") else "high"

    @staticmethod
    def _binding_task_id(task: TaskItem | None) -> str:
        return task.task_id if task is not None else ""

    def _validate_binding(
        self,
        result: AgentResult,
        *,
        role: str,
        task: TaskItem | None,
        invocation_id: str,
    ) -> None:
        expected_task_id = self._binding_task_id(task)
        if result.role != role:
            raise AgentInvocationError("openai", "invalid_output", "result role binding mismatch")
        if result.task_id != expected_task_id:
            raise AgentInvocationError("openai", "invalid_output", "result task binding mismatch")
        if result.invocation_id != invocation_id:
            raise AgentInvocationError(
                "openai", "invalid_output", "result invocation binding mismatch"
            )

    def _prepare_request(
        self,
        *,
        model: str,
        instructions: str,
        input_text: str,
    ):
        if len(input_text) > self.config.openai_max_input_chars:
            raise AgentInvocationError("openai", "invalid_output", "evidence exceeds input bound")
        return self.transport.prepare(
            model=model,
            instructions=instructions,
            input_text=input_text,
            max_output_tokens=self.config.openai_max_output_tokens,
            reasoning_effort=self._reasoning_effort(model),
        )

    def _dispatch(
        self,
        *,
        prepared,
        model: str,
        budget_purpose: str = "",
        episode_key: str = "",
    ):
        try:
            reservation = self.budget.reserve(
                model=model,
                input_token_ceiling=prepared.input_token_ceiling,
                output_token_ceiling=prepared.max_output_tokens,
                purpose=budget_purpose,
                episode_key=episode_key,
            )
        except (OpenAIBudgetExceeded, OpenAIBudgetPolicyError) as exc:
            raise AgentInvocationError("openai", "budget", str(exc)) from exc
        try:
            response = self.transport.send(prepared)
        except OpenAITransportError as exc:
            self.budget.mark_uncertain(reservation.reservation_id)
            raise AgentInvocationError("openai", exc.category, str(exc)) from exc
        try:
            self.budget.reconcile(reservation.reservation_id, response.usage)
        except OpenAIBudgetPolicyError as exc:
            self.budget.mark_uncertain(reservation.reservation_id)
            raise AgentInvocationError("openai", "budget", str(exc)) from exc
        return response

    @staticmethod
    def _review_instructions() -> str:
        return (
            "Act as a read-only AskRex code reviewer. Use only the supplied bounded evidence. "
            "Do not infer unseen repository state, claim tool use, or treat your own output as "
            "verification evidence. Return pass only when the supplied evidence is sufficient."
        )

    @staticmethod
    def _lead_instructions(phase: Literal["plan", "adjudicate"]) -> str:
        if phase == "plan":
            return (
                "Act as a read-only AskRex planning lead. Use only the supplied coordination "
                "and state evidence. Return a canonical structured planning result."
            )
        return (
            "Act as a read-only AskRex adjudication lead. Use only the supplied coordination, "
            "task, and failure-state evidence. Return a canonical structured result."
        )

    def _lead_input(
        self,
        role: str,
        state: WorkerState,
        task: TaskItem | None,
        context: str,
        invocation_id: str,
        phase: Literal["plan", "adjudicate"],
    ) -> str:
        task_lines = ["task_id: ", "task_prompt: ", "task_feedback: "]
        if task is not None:
            task_lines = [
                f"task_id: {task.task_id}",
                f"task_prompt: {task.prompt}",
                f"task_feedback: {task.feedback}",
            ]
        header = [
            f"role: {role}",
            f"phase: {phase}",
            f"invocation_id: {invocation_id}",
            f"status: {state.status.value}",
            f"iteration: {state.iteration}",
            f"implementation_failures: {state.implementation_failures}",
            f"review_failures: {state.review_failures}",
            *task_lines,
            "coordination:",
        ]
        text = "\n".join(header) + "\n" + context
        if len(text) <= self.config.openai_max_input_chars:
            return text
        marker = "\n...[truncated coordination context]...\n"
        remaining = self.config.openai_max_input_chars - len(marker)
        if remaining <= 0:
            raise AgentInvocationError("openai", "invalid_output", "input bound is too small")
        head_chars = remaining // 2
        tail_chars = remaining - head_chars
        return text[:head_chars] + marker + text[-tail_chars:]

    def _parse_and_bind(
        self,
        output: Mapping[str, Any],
        *,
        role: str,
        task: TaskItem | None,
        invocation_id: str,
        allow_issue_updates: bool,
    ) -> AgentResult:
        try:
            result = validate_agent_result(output)
            self._validate_binding(
                result,
                role=role,
                task=task,
                invocation_id=invocation_id,
            )
            validate_agent_updates(
                self.config.coordination_root,
                role,
                result,
                allow_issue_updates=allow_issue_updates,
                task_id=self._binding_task_id(task),
            )
        except AgentInvocationError:
            raise
        except (TypeError, ValueError) as exc:
            raise AgentInvocationError("openai", "invalid_output", str(exc)) from exc
        return result

    def _persist(
        self,
        *,
        role: str,
        phase: str,
        task: TaskItem | None,
        invocation_id: str,
        pre_head: str,
        result: AgentResult,
    ) -> AgentResult:
        repo = self._repo(role)
        post_head, _dirty = repo_snapshot(repo)
        pending = {
            "phase": phase,
            "role": role,
            "task_id": self._binding_task_id(task),
            "invocation_id": invocation_id,
            "pre_head": pre_head,
            "post_head": post_head,
            "result": asdict(result),
        }
        advance_handoff(
            self.config,
            role,
            pre_head=pre_head,
            post_head=post_head,
            invocation_id=invocation_id,
            provider="openai",
            pending_result=pending,
        )
        return result

    def review(
        self,
        role: str,
        state: WorkerState,
        task: TaskItem,
        context: str,
        model: str,
    ) -> AgentResult:
        repo = self._repo(role)
        invocation_id = self._invocation_id_factory()
        with ControlPlaneLock(role_lease_lock_path(self.config, role)):
            validate_handoff(self.config, role)
            pre_head, dirty = repo_snapshot(repo)
            if dirty:
                raise HandoffRequired("OpenAI review requires a clean leased repository")
            try:
                with _temporary_scratch_root("askrex-openai-review-") as (scratch_root, _cleanup):
                    scratch_repo = scratch_root / "repo"
                    _clone_scratch_repo(repo, pre_head, scratch_repo)
                    snapshot_config = self.config.with_worker_root(role, scratch_repo)
                    evidence = build_review_evidence(
                        snapshot_config,
                        role,
                        state,
                        task,
                        context,
                        invocation_id,
                    )
            except EvidenceError as exc:
                raise AgentInvocationError("openai", "invalid_output", str(exc)) from exc
            prepared = self._prepare_request(
                model=model,
                instructions=self._review_instructions(),
                input_text=evidence.text,
            )
            response = self._dispatch(prepared=prepared, model=model)
            result = self._parse_and_bind(
                response.output,
                role=role,
                task=task,
                invocation_id=invocation_id,
                allow_issue_updates=True,
            )
            if evidence.truncated and result.outcome == "pass":
                raise AgentInvocationError(
                    "openai",
                    "incomplete_evidence",
                    "review evidence was truncated; route to a fallback reviewer with complete evidence",
                )
            return self._persist(
                role=role,
                phase="review",
                task=task,
                invocation_id=invocation_id,
                pre_head=pre_head,
                result=result,
            )

    def lead(
        self,
        role: str,
        state: WorkerState,
        task: TaskItem | None,
        context: str,
        *,
        phase: Literal["plan", "adjudicate"],
        model: str,
        budget_purpose: str = "",
        episode_key: str = "",
    ) -> AgentResult:
        if phase not in {"plan", "adjudicate"}:
            raise ValueError(f"unsupported OpenAI lead phase: {phase}")
        if phase == "adjudicate" and task is None:
            raise ValueError("OpenAI adjudication requires an active task")
        repo = self._repo(role)
        invocation_id = self._invocation_id_factory()
        with ControlPlaneLock(role_lease_lock_path(self.config, role)):
            validate_handoff(self.config, role)
            pre_head, dirty = repo_snapshot(repo)
            if dirty:
                raise HandoffRequired("OpenAI lead requires a clean leased repository")
            prepared = self._prepare_request(
                model=model,
                instructions=self._lead_instructions(phase),
                input_text=self._lead_input(
                    role,
                    state,
                    task,
                    context,
                    invocation_id,
                    phase,
                ),
            )
            response = self._dispatch(
                prepared=prepared,
                model=model,
                budget_purpose=budget_purpose,
                episode_key=episode_key,
            )
            result = self._parse_and_bind(
                response.output,
                role=role,
                task=task,
                invocation_id=invocation_id,
                allow_issue_updates=False,
            )
            return self._persist(
                role=role,
                phase=phase,
                task=task,
                invocation_id=invocation_id,
                pre_head=pre_head,
                result=result,
            )
