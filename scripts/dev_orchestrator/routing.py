from __future__ import annotations

from .types import OrchestratorConfig, WorkerState

TERRA_MODEL = "gpt-5.6-terra"
SOL_MODEL = "gpt-5.6-sol"
ASTRA_MODEL = "gpt-6-astra"
CLAUDE_ROUTINE_MODEL = "sonnet"
CLAUDE_ESCALATION_MODEL = "opus"


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
