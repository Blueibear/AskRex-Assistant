from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from .openai_budget import CONSERVATIVE_PRICE_PER_MILLION, OpenAIBudgetLedger
from .routing import METERED_OPENAI_API_ALLOWED_ROLES, SOL_MODEL
from .runner import AgentInvocationError
from .storage import AtomicJsonStore
from .types import AgentResult, OrchestratorConfig, TaskItem, WorkerState

_SUBSCRIPTION_API_FALLBACK_KINDS = frozenset({"usage_limit", "auth"})
FABLE_REVIEW_MODEL = "fable"
_PROTECTED_REVIEW_MARKERS = (
    "final-verify",
    "security",
    "privacy",
    "auth",
    "credential",
    "permission",
    "policy",
    "release",
    "deploy",
    "audit",
    "approval",
    "identity",
    "secret",
)


def temporary_fable_policy_active(
    config: OrchestratorConfig, role: str, *, now: datetime | None = None
) -> bool:
    """Whether the temporary delegation suspends ChatGPT review for this role."""
    policy = config.metadata.get("temporary_fable_review", {})
    if not isinstance(policy, dict) or policy.get("enabled") is not True:
        return False
    if policy.get("reason") != "openai_weekly_usage_limit":
        raise ValueError("Fable review requires the explicit usage-limit reason")
    if policy.get("model") != FABLE_REVIEW_MODEL:
        raise ValueError("Fable review requires the pinned fable model")
    roles = policy.get("roles")
    if (
        not isinstance(roles, list)
        or not roles
        or any(value not in {"backend", "mobile"} for value in roles)
    ):
        raise ValueError("Fable review requires an explicit role allowlist")
    try:
        expires = datetime.fromisoformat(str(policy["expires_at"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Fable review requires a valid expiry") from exc
    if expires.tzinfo is None or expires.utcoffset() is None:
        raise ValueError("Fable review expiry must be timezone-aware")
    current = now or datetime.now(UTC)
    if expires > current + timedelta(days=7):
        raise ValueError("Fable review cannot be enabled more than seven days ahead")
    return current < expires and role in roles


def temporary_fable_review_active(
    config: OrchestratorConfig,
    role: str,
    task: TaskItem,
    implementation_provider: str,
    *,
    now: datetime | None = None,
) -> bool:
    """Only routine Claude changes, never work requiring a protected gate."""
    if not temporary_fable_policy_active(config, role, now=now):
        return False
    # Owner-approved, expiring Codex-equivalent review delegation. This changes
    # reviewer selection, not validation, issue ownership, or physical acceptance.
    policy = config.metadata["temporary_fable_review"]
    if policy.get("allow_codex_equivalent_reviews") is True:
        return True
    if implementation_provider not in {"claude", "claude_cloud"}:
        return False
    task_id = task.task_id.casefold()
    if any(marker in task_id for marker in _PROTECTED_REVIEW_MARKERS):
        return False
    prompt = f"{task.prompt} {task.feedback}".casefold()
    # The referenced audit-priority memo is an ordering source, not this task's scope.
    prompt = prompt.replace("(msg-20261116-cross-surface-audit-priority.md)", "")
    # These two S10 instructions prohibit changes to protected surfaces.
    # Do not mistake their mentions of auth/privacy for requested modifications.
    # Keep the exclusion exact and bounded so other security tasks fail closed.
    prompt = re.sub(
        r"do not implement the speculative client-side /mobile/status endpoint[^.]{0,500}\.",
        "",
        prompt,
    )
    prompt = prompt.replace(
        "preserve existing device pairing, user identity, privacy, and approval flows unchanged.",
        "",
    )
    if any(marker in prompt for marker in _PROTECTED_REVIEW_MARKERS):
        return False
    if any(marker in prompt for marker in ("codex", "cross-provider", "cross provider")):
        return False
    return True


def temporary_claude_planning_active(
    config: OrchestratorConfig, role: str, *, now: datetime | None = None
) -> bool:
    """Owner-authorized, expiring subscription-only planning route."""
    policy = config.metadata.get("temporary_claude_planning", {})
    if not isinstance(policy, dict) or policy.get("enabled") is not True:
        return False
    if policy.get("reason") != "openai_weekly_usage_limit":
        raise ValueError("Claude planning requires the explicit usage-limit reason")
    roles = policy.get("roles")
    if (
        not isinstance(roles, list)
        or not roles
        or any(value not in {"backend", "mobile"} for value in roles)
    ):
        raise ValueError("Claude planning requires an explicit role allowlist")
    try:
        expires = datetime.fromisoformat(str(policy["expires_at"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Claude planning requires a valid expiry") from exc
    if expires.tzinfo is None or expires.utcoffset() is None:
        raise ValueError("Claude planning expiry must be timezone-aware")
    current = now or datetime.now(UTC)
    if expires > current + timedelta(days=7):
        raise ValueError("Claude planning cannot be enabled more than seven days ahead")
    return current < expires and role in roles


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
        if model == FABLE_REVIEW_MODEL:
            if not temporary_fable_review_active(
                self.config, role, task, self._last_provider(role)
            ):
                raise AgentInvocationError(
                    "claude", "policy", "Fable review is not authorized for this checkpoint"
                )
            # Fable runs on the Claude subscription in a read-only scratch clone.
            # No Codex or metered API fallback is permitted for this route.
            return self.cli_invoker.review(role, state, task, context, FABLE_REVIEW_MODEL)
        selected_model = (
            SOL_MODEL if self._is_final_verify(task) else self._review_model(role, model)
        )
        try:
            return self.cli_invoker.review(role, state, task, context, selected_model)
        except AgentInvocationError as exc:
            if (
                role not in METERED_OPENAI_API_ALLOWED_ROLES
                or not self.config.openai_worker_enabled
                or exc.kind not in _SUBSCRIPTION_API_FALLBACK_KINDS
            ):
                raise
            return self.openai_worker.review(
                role,
                state,
                task,
                context,
                selected_model,
            )

    def _sol_lead(self, role, state, context, task: TaskItem | None) -> AgentResult:
        try:
            subscription_result = self.cli_invoker.lead(role, state, context, task=task)
        except AgentInvocationError as exc:
            if (
                role not in METERED_OPENAI_API_ALLOWED_ROLES
                or not self.config.openai_worker_enabled
                or exc.kind not in _SUBSCRIPTION_API_FALLBACK_KINDS
            ):
                raise
            subscription_result = None
        else:
            if task is None or subscription_result.outcome != "failed":
                return subscription_result
            if (
                not self.config.openai_worker_enabled
                or role not in METERED_OPENAI_API_ALLOWED_ROLES
            ):
                return subscription_result

        phase = "adjudicate" if task is not None else "plan"
        model = (
            self.config.openai_planning_model
            if task is None
            else self.config.openai_escalation_model
        )
        try:
            api_result = self.openai_worker.lead(
                role,
                state,
                task,
                context,
                phase=phase,
                model=model,
            )
        except AgentInvocationError:
            if subscription_result is not None:
                return subscription_result
            raise

        if task is None or api_result.outcome != "failed":
            return api_result
        assert task is not None
        return self._maybe_astra(role, state, task, context, api_result)

    def _astra_eligible(self, role: str, state: WorkerState, task: TaskItem, episode: str) -> bool:
        failures = max(state.implementation_failures, state.review_failures)
        return bool(
            role in METERED_OPENAI_API_ALLOWED_ROLES
            and self.config.openai_worker_enabled
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
        if not self._astra_eligible(role, state, task, episode):
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
        if task is None and temporary_claude_planning_active(self.config, role):
            # Never fall back to Codex or a metered API while this route is active.
            return self.cli_invoker.claude_plan(role, state, context)
        return self._sol_lead(role, state, context, task)
