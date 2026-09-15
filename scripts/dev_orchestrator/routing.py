from __future__ import annotations

from decimal import Decimal

from .types import OrchestratorConfig, WorkerState

TERRA_MODEL = "gpt-5.6-terra"
SOL_MODEL = "gpt-5.6-sol"
ASTRA_MODEL = "gpt-6-astra"
CLAUDE_ROUTINE_MODEL = "sonnet"
CLAUDE_ESCALATION_MODEL = "opus"
SUPPORTED_OPENAI_MODELS = frozenset({TERRA_MODEL, SOL_MODEL, ASTRA_MODEL})
OPENAI_HARD_MONTHLY_CAP_USD = Decimal("30.00")


def validate_openai_policy(config: OrchestratorConfig) -> None:
    models = (
        config.openai_review_model,
        config.openai_escalation_model,
        config.openai_planning_model,
        config.openai_astra_model,
    )
    if any(model not in SUPPORTED_OPENAI_MODELS for model in models):
        raise ValueError("unsupported OpenAI model policy")
    if config.openai_monthly_budget_usd > OPENAI_HARD_MONTHLY_CAP_USD:
        raise ValueError("OpenAI monthly budget exceeds the $30.00 hard cap")
    if config.openai_monthly_budget_usd <= 0:
        raise ValueError("OpenAI monthly budget must be positive")
    ceilings = (
        config.openai_timeout_seconds,
        config.openai_max_input_chars,
        config.openai_max_output_tokens,
        config.openai_max_calls_per_cycle,
    )
    if any(value <= 0 for value in ceilings):
        raise ValueError("OpenAI request ceilings must be positive")
    if config.openai_max_astra_calls_per_escalation != 1:
        raise ValueError("Astra is limited to exactly one call per escalation episode")
    if config.openai_worker_enabled and (
        not config.openai_project_id.strip() or not config.openai_project_hard_limit_confirmed
    ):
        raise ValueError("OpenAI worker requires a confirmed dedicated project hard limit")


def select_implementer_model(state: WorkerState, config: OrchestratorConfig | None = None) -> str:
    threshold = (
        config or OrchestratorConfig.default(__import__("pathlib").Path("."))
    ).implementation_escalation_after
    if state.implementation_failures >= threshold:
        return CLAUDE_ESCALATION_MODEL
    return CLAUDE_ROUTINE_MODEL


def select_reviewer_model(state: WorkerState, config: OrchestratorConfig | None = None) -> str:
    threshold = (
        config or OrchestratorConfig.default(__import__("pathlib").Path("."))
    ).review_escalation_after
    if state.review_failures >= threshold:
        return SOL_MODEL
    return TERRA_MODEL
