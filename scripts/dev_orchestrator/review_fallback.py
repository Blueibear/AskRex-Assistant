from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .storage import AtomicJsonStore
from .types import AgentResult, OrchestratorConfig, WorkerState
from .validation import _receipt_path, load_validation_receipt

_SHA = re.compile(r"[0-9a-f]{40}")
_TASK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
_ALLOWED_OUTCOMES = {"pass", "changes_required"}


def fallback_active(config: OrchestratorConfig, *, now: datetime | None = None) -> bool:
    policy = config.metadata.get("review_fallback", {})
    if not isinstance(policy, dict) or policy.get("enabled") is not True:
        return False
    if policy.get("reason") != "openai_weekly_usage_limit":
        raise ValueError("review fallback requires an explicit weekly usage-limit reason")
    try:
        expires = datetime.fromisoformat(str(policy["expires_at"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("review fallback requires a valid expiry") from exc
    if expires.tzinfo is None or expires.utcoffset() is None:
        raise ValueError("review fallback expiry must be timezone-aware")
    current = now or datetime.now(UTC)
    if expires > current + timedelta(days=7):
        raise ValueError("review fallback cannot be enabled more than seven days ahead")
    return current < expires


def review_receipt_path(config: OrchestratorConfig, role: str, task_id: str, head: str) -> Path:
    if role not in {"backend", "mobile"} or not _TASK_ID.fullmatch(task_id):
        raise ValueError("invalid fallback review role or task")
    if not _SHA.fullmatch(head):
        raise ValueError("invalid fallback review revision")
    return config.coordination_root / "review-fallback" / f"{role}-{task_id}-{head}.json"


def load_hourly_review(
    config: OrchestratorConfig,
    role: str,
    state: WorkerState,
    *,
    head: str,
    implementation_provider: str,
    now: datetime | None = None,
) -> AgentResult | None:
    if not fallback_active(config, now=now):
        return None
    if state.task is None or not state.task_base_head:
        raise ValueError("fallback review requires an assigned revision-bound task")
    if implementation_provider not in {"claude", "claude_cloud"}:
        raise ValueError("hourly ChatGPT fallback may only review Claude implementation")
    validation_settings = config.metadata.get("iteration_validation", {})
    if isinstance(validation_settings, dict) and validation_settings.get("enabled") is False:
        raise ValueError("fallback review cannot bypass disabled validation")

    receipt = load_validation_receipt(
        config, role, state.task.task_id, base_head=state.task_base_head, head=head
    )
    if not receipt.gates:
        raise ValueError("fallback review requires nonempty successful validation gates")
    path = review_receipt_path(config, role, state.task.task_id, head)
    if not path.is_file():
        return None
    payload = AtomicJsonStore(path).read(default=None)
    if not isinstance(payload, dict):
        raise ValueError("fallback review receipt must be a JSON object")
    expected = {
        "schema_version": 1,
        "source": "chatgpt-hourly-review",
        "role": role,
        "task_id": state.task.task_id,
        "base_head": state.task_base_head,
        "head": head,
    }
    if any(payload.get(key) != value for key, value in expected.items()):
        raise ValueError("fallback review receipt provenance or revision mismatch")
    digest = hashlib.sha256(
        _receipt_path(config, role, state.task.task_id).read_bytes()
    ).hexdigest()
    if payload.get("validation_sha256") != digest:
        raise ValueError("fallback review receipt does not bind current validation")
    outcome = payload.get("outcome")
    if outcome not in _ALLOWED_OUTCOMES:
        raise ValueError("fallback review outcome must be pass or changes_required")
    run_id = payload.get("review_run_id")
    if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9-]{12,100}", run_id):
        raise ValueError("fallback review requires a valid automation run ID")
    summary = payload.get("summary")
    if not isinstance(summary, str) or not 10 <= len(summary.strip()) <= 4000:
        raise ValueError("fallback review requires a bounded substantive summary")
    evidence = payload.get("evidence_references")
    if not isinstance(evidence, list) or not evidence or any(
        not isinstance(value, str) or not value.strip() or len(value) > 400
        for value in evidence
    ):
        raise ValueError("fallback review requires concrete evidence references")
    reviewed_at = payload.get("reviewed_at")
    try:
        stamp = datetime.fromisoformat(str(reviewed_at))
    except ValueError as exc:
        raise ValueError("fallback review timestamp is malformed") from exc
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("fallback review timestamp must be timezone-aware")
    current = now or datetime.now(UTC)
    validated_at = datetime.fromisoformat(receipt.passed_at)
    if not validated_at <= stamp <= current + timedelta(minutes=5):
        raise ValueError("fallback review must follow current successful validation")
    if stamp < current - timedelta(days=7):
        raise ValueError("fallback review has expired")
    next_action = payload.get("next_action")
    if not isinstance(next_action, str) or len(next_action) > 4000:
        raise ValueError("fallback review next action is invalid")
    if outcome == "changes_required" and not next_action.strip():
        raise ValueError("review findings require actionable remediation")
    return AgentResult(
        outcome=outcome,
        summary=summary.strip(),
        next_action=next_action.strip(),
        role=role,
        task_id=state.task.task_id,
        invocation_id=f"chatgpt-hourly-{run_id}",
    )
