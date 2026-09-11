from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .types import AgentResult

_ALLOWED_OUTCOMES = {
    "continue",
    "ready_for_review",
    "pass",
    "changes_required",
    "assign",
    "done",
    "blocked_user",
    "blocked_system",
    "failed",
}


def validate_agent_result(data: Mapping[str, Any]) -> AgentResult:
    outcome = data.get("outcome")
    if outcome not in _ALLOWED_OUTCOMES:
        raise ValueError(f"invalid agent outcome: {outcome!r}")
    summary = data.get("summary")
    next_action = data.get("next_action")
    if not isinstance(summary, str) or not isinstance(next_action, str):
        raise ValueError("agent result requires string summary and next_action")
    return AgentResult(
        outcome=outcome,
        summary=summary,
        next_action=next_action,
        needs_user=bool(data.get("needs_user", False)),
        blocker_reason=str(data.get("blocker_reason", "")),
        task_id=str(data.get("task_id", "")),
        task_prompt=str(data.get("task_prompt", "")),
    )
