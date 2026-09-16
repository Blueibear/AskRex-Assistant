from __future__ import annotations

import hashlib
from typing import Any

from .openai_budget import CONSERVATIVE_PRICE_PER_MILLION, OpenAIBudgetLedger
from .routing import SOL_MODEL
from .runner import AgentInvocationError
from .storage import AtomicJsonStore
from .types import AgentResult, OrchestratorConfig, TaskItem, WorkerState


def escalation_episode_key(role: str, task: TaskItem, state: WorkerState) -> str:
    return hashlib.sha256(f"{role}\0{task.task_id}\0{state.task_base_head}".encode()).hexdigest()


class ProviderRoutingInvoker:
    """Deterministic phase-to-provider router for Ralph model invocations."""

    def __init__(
        self,
        config: OrchestratorConfig,
        *,
        cli_invoker: Any,
        openai_worker: Any | None = None,
        budget: OpenAIBudgetLedger | Any | None = None,
    ) -> None:
        self.config = config
        self.cli_invoker = cli_invoker
        self.openai_worker = openai_worker
        self.budget = budget or OpenAIBudgetLedger(
            config.coordination_root,
            monthly_cap_usd=config.openai_monthly_budget_usd,
        )
        if config.openai_worker_enabled and openai_worker is None:
            raise ValueError("enabled OpenAI routing requires an OpenAI worker")

    def _last_provider(self, role: str) -> str:
        record = AtomicJsonStore(self.config.coordination_root / "handoff" / f"{role}.json").read(
            default={}
        )
        if not isinstance(record, dict):
            return ""
        return str(record.get("last_provider", ""))

    def implement(self, role, state, task, context, model) -> AgentResult:
        return self.cli_invoker.implement(role, state, task, context, model)

    def _review_model(self, role: str, requested_model: str) -> str:
        if self._last_provider(role) == "codex" or requested_model == SOL_MODEL:
            return self.config.openai_escalation_model
        return self.config.openai_review_model

    @staticmethod
    def _is_final_verify(task: TaskItem) -> bool:
        return task.task_id.upper().startswith("FINAL-VERIFY-")

    def review(self, role, state, task, context, model) -> AgentResult:
        if self._is_final_verify(task):
            return self.cli_invoker.review(role, state, task, context, SOL_MODEL)

        selected_model = self._review_model(role, model)
        if not self.config.openai_worker_enabled:
            return self.cli_invoker.review(role, state, task, context, selected_model)
        try:
            return self.openai_worker.review(
                role,
                state,
                task,
                context,
                selected_model,
            )
        except AgentInvocationError:
            return self.cli_invoker.review(
                role,
                state,
                task,
                context,
                selected_model,
            )

    def _sol_lead(self, role, state, context, task: TaskItem | None) -> AgentResult:
        if not self.config.openai_worker_enabled:
            return self.cli_invoker.lead(role, state, context, task=task)
        phase = "adjudicate" if task is not None else "plan"
        model = (
            self.config.openai_planning_model
            if task is None
            else self.config.openai_escalation_model
        )
        try:
            result = self.openai_worker.lead(
                role,
                state,
                task,
                context,
                phase=phase,
                model=model,
            )
        except AgentInvocationError:
            fallback = self.cli_invoker.lead(role, state, context, task=task)
            if task is None or fallback.outcome != "failed":
                return fallback
            return self._maybe_astra(role, state, task, context, fallback)
        if task is None or result.outcome != "failed":
            return result

        fallback = self.cli_invoker.lead(role, state, context, task=task)
        if fallback.outcome != "failed":
            return fallback
        return self._maybe_astra(role, state, task, context, fallback)

    def _astra_eligible(self, state: WorkerState, task: TaskItem, episode: str) -> bool:
        failures = max(state.implementation_failures, state.review_failures)
        return bool(
            self.config.openai_worker_enabled
            and self.config.openai_astra_enabled
            and failures >= self.config.astra_adjudication_after
            and self.config.openai_astra_model in CONSERVATIVE_PRICE_PER_MILLION
            and not self.budget.has_astra_attempt(episode)
        )

    def _maybe_astra(
        self,
        role: str,
        state: WorkerState,
        task: TaskItem,
        context: str,
        fallback: AgentResult,
    ) -> AgentResult:
        episode = escalation_episode_key(role, task, state)
        if not self._astra_eligible(state, task, episode):
            return fallback
        return self.openai_worker.lead(
            role,
            state,
            task,
            context,
            phase="adjudicate",
            model=self.config.openai_astra_model,
            budget_purpose="astra_adjudication",
            episode_key=episode,
        )

    def lead(self, role, state, context, task=None) -> AgentResult:
        return self._sol_lead(role, state, context, task)
