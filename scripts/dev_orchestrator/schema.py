from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, ValidationError

from .types import AgentResult, CoordinationMessage, IssueUpdate

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
_ALLOWED_RECIPIENTS = {"backend", "mobile", "testing", "supervisor"}
_ALLOWED_PRIORITIES = {"low", "normal", "high", "critical"}
_ALLOWED_ISSUE_STATUSES = {"fixed-needs-retest"}

_PHASE_OUTCOMES = {
    "implement": frozenset(
        {"continue", "ready_for_review", "blocked_user", "blocked_system", "failed"}
    ),
    "review": frozenset(
        {"pass", "changes_required", "blocked_user", "blocked_system", "failed"}
    ),
    "plan": frozenset({"assign", "done", "blocked_user", "blocked_system", "failed"}),
    "adjudicate": frozenset(
        {"assign", "done", "blocked_user", "blocked_system", "failed"}
    ),
}


def allowed_outcomes_for_phase(phase: str) -> frozenset[str]:
    try:
        return _PHASE_OUTCOMES[phase]
    except KeyError as exc:
        raise ValueError(f"unsupported agent phase: {phase!r}") from exc


def validate_phase_outcome(result: AgentResult, phase: str) -> None:
    allowed = allowed_outcomes_for_phase(phase)
    if result.outcome not in allowed:
        expected = ", ".join(sorted(allowed))
        raise ValueError(
            f"agent outcome {result.outcome!r} is invalid for phase {phase!r}; "
            f"expected one of: {expected}"
        )

_SCHEMA = json.loads(
    Path(__file__).with_name("agent-result.schema.json").read_text(encoding="utf-8")
)
_SCHEMA_VALIDATOR = Draft202012Validator(_SCHEMA)


_OPENAI_UNSUPPORTED_SCHEMA_KEYWORDS = frozenset(
    {"$schema", "allOf", "not", "dependentRequired", "dependentSchemas", "if", "then", "else"}
)


def _openai_strict_schema_projection(node: Any) -> Any:
    if isinstance(node, dict):
        projected = {
            key: _openai_strict_schema_projection(value)
            for key, value in node.items()
            if key not in _OPENAI_UNSUPPORTED_SCHEMA_KEYWORDS
        }
        properties = projected.get("properties")
        if projected.get("type") == "object" and isinstance(properties, dict):
            projected["required"] = list(properties)
        return projected
    if isinstance(node, list):
        return [_openai_strict_schema_projection(value) for value in node]
    return node


def agent_result_schema_payload(*, openai_strict: bool = False) -> dict[str, Any]:
    schema = copy.deepcopy(_SCHEMA)
    if not openai_strict:
        return schema
    return _openai_strict_schema_projection(schema)


def _messages(data: Any) -> tuple[CoordinationMessage, ...]:
    if data is None:
        return ()
    if not isinstance(data, list):
        raise ValueError("coordination_messages must be a list")
    parsed: list[CoordinationMessage] = []
    for item in data:
        if not isinstance(item, Mapping):
            raise ValueError("coordination message must be an object")
        recipient = str(item.get("to", ""))
        priority = str(item.get("priority", ""))
        if recipient not in _ALLOWED_RECIPIENTS:
            raise ValueError(f"invalid coordination recipient: {recipient!r}")
        if priority not in _ALLOWED_PRIORITIES:
            raise ValueError(f"invalid coordination priority: {priority!r}")
        related = str(item.get("related", "")).strip()
        body = str(item.get("body", "")).strip()
        if not related or not body:
            raise ValueError("coordination message requires related and body")
        parsed.append(
            CoordinationMessage(
                to=recipient,
                priority=priority,
                related=related,
                needs_response=bool(item.get("needs_response", False)),
                body=body,
            )
        )
    return tuple(parsed)


def _issues(data: Any) -> tuple[IssueUpdate, ...]:
    if data is None:
        return ()
    if not isinstance(data, list):
        raise ValueError("issue_updates must be a list")
    parsed: list[IssueUpdate] = []
    for item in data:
        if not isinstance(item, Mapping):
            raise ValueError("issue update must be an object")
        issue_id = str(item.get("issue_id", "")).strip()
        status = str(item.get("status", "")).strip()
        note = str(item.get("note", "")).strip()
        if not issue_id or status not in _ALLOWED_ISSUE_STATUSES or not note:
            raise ValueError("invalid issue update")
        parsed.append(IssueUpdate(issue_id=issue_id, status=status, note=note))
    return tuple(parsed)


def validate_agent_result(data: Mapping[str, Any]) -> AgentResult:
    try:
        _SCHEMA_VALIDATOR.validate(dict(data))
    except ValidationError as exc:
        raise ValueError(f"agent result schema violation: {exc.message}") from exc
    outcome = data.get("outcome")
    if outcome not in _ALLOWED_OUTCOMES:
        raise ValueError(f"invalid agent outcome: {outcome!r}")
    summary = data.get("summary")
    next_action = data.get("next_action")
    if not isinstance(summary, str) or not isinstance(next_action, str):
        raise ValueError("agent result requires string summary and next_action")
    needs_user = bool(data.get("needs_user", False))
    blocker_reason = str(data.get("blocker_reason", ""))
    if needs_user and outcome != "blocked_user":
        raise ValueError(
            "agent result schema violation: needs_user=true requires outcome=blocked_user"
        )
    if outcome == "blocked_user" and (not needs_user or not blocker_reason.strip()):
        raise ValueError(
            "agent result schema violation: blocked_user requires needs_user=true and a blocker_reason"
        )
    return AgentResult(
        outcome=str(outcome),
        summary=summary,
        next_action=next_action,
        needs_user=needs_user,
        blocker_reason=blocker_reason,
        task_id=str(data.get("task_id", "")),
        task_prompt=str(data.get("task_prompt", "")),
        role=str(data.get("role", "")),
        invocation_id=str(data.get("invocation_id", "")),
        coordination_messages=_messages(data.get("coordination_messages")),
        issue_updates=_issues(data.get("issue_updates")),
    )
