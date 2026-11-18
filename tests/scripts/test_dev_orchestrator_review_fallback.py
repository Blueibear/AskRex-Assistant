from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scripts.dev_orchestrator.cli import save_config
from scripts.dev_orchestrator.review_fallback import (
    fallback_active,
    load_hourly_review,
    review_receipt_path,
)
from scripts.dev_orchestrator.storage import AtomicJsonStore
from scripts.dev_orchestrator.supervisor import Supervisor
from scripts.dev_orchestrator.types import OrchestratorConfig, TaskItem, WorkerState, WorkerStatus
from scripts.dev_orchestrator.validation import _receipt_path

BASE = "a" * 40
HEAD = "b" * 40
TASK = "backend-fix-001"


def _setup(tmp_path: Path):
    now = datetime.now(UTC)
    config = replace(
        OrchestratorConfig.default(tmp_path),
        observe_only=False,
        metadata={
            "review_fallback": {
                "enabled": True,
                "reason": "openai_weekly_usage_limit",
                "expires_at": (now + timedelta(days=1)).isoformat(),
            }
        },
    )
    save_config(config)
    state = WorkerState(
        role="backend",
        status=WorkerStatus.REVIEWING,
        task=TaskItem(TASK, "Implement a scoped backend fix"),
        task_base_head=BASE,
    )
    validation = {
        "role": "backend",
        "task_id": TASK,
        "base_head": BASE,
        "head": HEAD,
        "passed_at": (now - timedelta(minutes=5)).isoformat(),
        "gates": [
            {
                "name": "pytest",
                "command": ["py", "-3.11", "-m", "pytest", "-q"],
                "cwd": ".",
                "exit_code": 0,
                "stdout": "passed",
                "stderr": "",
            }
        ],
    }
    validation_path = _receipt_path(config, "backend", TASK)
    AtomicJsonStore(validation_path).write(validation)
    review = {
        "schema_version": 1,
        "source": "chatgpt-hourly-review",
        "role": "backend",
        "task_id": TASK,
        "base_head": BASE,
        "head": HEAD,
        "review_run_id": "abc123def4567890",
        "reviewed_at": now.isoformat(),
        "validation_sha256": hashlib.sha256(validation_path.read_bytes()).hexdigest(),
        "outcome": "pass",
        "summary": "Reviewed the exact diff and all supplied validation evidence.",
        "next_action": "Proceed to normal coordination handoff.",
        "evidence_references": ["git diff aaaaa..bbbbb", "validation/backend-fix-001"],
    }
    return config, state, review, now


def _write_review(config: OrchestratorConfig, review: dict) -> Path:
    path = review_receipt_path(config, "backend", TASK, HEAD)
    AtomicJsonStore(path).write(review)
    return path


def test_hourly_review_accepts_exact_revision_and_validation(tmp_path: Path) -> None:
    config, state, review, now = _setup(tmp_path)
    assert (
        load_hourly_review(
            config, "backend", state, head=HEAD, implementation_provider="claude_cloud", now=now
        )
        is None
    )
    _write_review(config, review)
    result = load_hourly_review(
        config, "backend", state, head=HEAD, implementation_provider="claude_cloud", now=now
    )
    assert result is not None
    assert result.outcome == "pass"
    assert result.invocation_id == "chatgpt-hourly-abc123def4567890"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source", "claude_cloud"),
        ("head", "c" * 40),
        ("base_head", "d" * 40),
        ("validation_sha256", "0" * 64),
        ("review_run_id", ""),
        ("evidence_references", []),
    ],
)
def test_hourly_review_rejects_unbound_or_unsupported_evidence(
    tmp_path: Path, field: str, value
) -> None:
    config, state, review, now = _setup(tmp_path)
    review[field] = value
    _write_review(config, review)
    with pytest.raises(ValueError):
        load_hourly_review(
            config, "backend", state, head=HEAD, implementation_provider="claude", now=now
        )


def test_hourly_review_rejects_same_provider_and_disabled_gates(tmp_path: Path) -> None:
    config, state, review, now = _setup(tmp_path)
    _write_review(config, review)
    with pytest.raises(ValueError, match="Claude implementation"):
        load_hourly_review(
            config, "backend", state, head=HEAD, implementation_provider="codex", now=now
        )
    disabled = replace(
        config,
        metadata={**config.metadata, "iteration_validation": {"enabled": False}},
    )
    with pytest.raises(ValueError, match="disabled validation"):
        load_hourly_review(
            disabled, "backend", state, head=HEAD, implementation_provider="claude", now=now
        )


def test_hourly_review_expires_without_changing_normal_review_policy(tmp_path: Path) -> None:
    config, state, review, now = _setup(tmp_path)
    _write_review(config, review)
    assert not fallback_active(config, now=now + timedelta(days=2))
    assert (
        load_hourly_review(
            config,
            "backend",
            state,
            head=HEAD,
            implementation_provider="claude_cloud",
            now=now + timedelta(days=2),
        )
        is None
    )


def test_supervisor_waits_for_hourly_review_then_accepts_exact_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, state, review, _now = _setup(tmp_path)
    AtomicJsonStore(tmp_path / "handoff" / "backend.json").write({"last_provider": "claude_cloud"})
    supervisor = Supervisor(config, object())
    monkeypatch.setattr(supervisor, "_capture_task_base_head", lambda _role: HEAD)
    supervisor.save_state(state)

    supervisor._review("backend", state, "bounded coordination evidence")
    waiting = supervisor.load_state("backend")
    assert waiting.status is WorkerStatus.BLOCKED_SYSTEM
    assert waiting.blocker_kind == "review_fallback_wait"
    assert waiting.resume_status is WorkerStatus.REVIEWING

    supervisor._run_role("backend")
    assert supervisor.load_state("backend").blocker_kind == "review_fallback_wait"

    _write_review(config, review)
    supervisor._run_role("backend")
    completed = supervisor.load_state("backend")
    assert completed.status is WorkerStatus.IDLE
    assert completed.task is None


def test_supervisor_does_not_accept_invalid_hourly_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, state, review, _now = _setup(tmp_path)
    review["source"] = "claude_cloud"
    _write_review(config, review)
    AtomicJsonStore(tmp_path / "handoff" / "backend.json").write({"last_provider": "claude_cloud"})
    supervisor = Supervisor(config, object())
    monkeypatch.setattr(supervisor, "_capture_task_base_head", lambda _role: HEAD)
    supervisor.save_state(state)

    supervisor._review("backend", state, "bounded context")
    blocked = supervisor.load_state("backend")
    assert blocked.status is WorkerStatus.BLOCKED_SYSTEM
    assert blocked.blocker_kind == "review_fallback_invalid"
    assert blocked.resume_status is WorkerStatus.REVIEWING
    supervisor._run_role("backend")
    assert supervisor.load_state("backend").blocker_kind == "review_fallback_invalid"


def test_hourly_review_findings_require_action(tmp_path: Path) -> None:
    config, state, review, now = _setup(tmp_path)
    review["outcome"] = "changes_required"
    review["next_action"] = ""
    _write_review(config, review)
    with pytest.raises(ValueError, match="actionable"):
        load_hourly_review(
            config, "backend", state, head=HEAD, implementation_provider="claude", now=now
        )
    review["next_action"] = "Fix missing authorization check."
    _write_review(config, review)
    result = load_hourly_review(
        config, "backend", state, head=HEAD, implementation_provider="claude", now=now
    )
    assert result is not None and result.outcome == "changes_required"


@pytest.mark.parametrize(
    ("provider", "task_id", "prompt"),
    [
        ("codex", TASK, "Ordinary task"),
        ("claude", "FINAL-VERIFY-backend", "Final gate"),
        ("claude", "SECURITY-REPO-20261111", "Security gate"),
        ("claude", "backend-security-exact-head-review", "Security gate"),
        ("claude", TASK, "Explicit Codex independent approval required"),
    ],
)
def test_hourly_fallback_preserves_explicit_codex_gates(
    tmp_path: Path, provider: str, task_id: str, prompt: str
) -> None:
    from scripts.dev_orchestrator.handoff import repo_snapshot
    from scripts.dev_orchestrator.types import AgentResult
    from tests.scripts.test_dev_orchestrator_routing import _safe_config

    runtime, backend, _mobile = _safe_config(tmp_path)
    config, state, _review, _now = _setup(runtime.coordination_root)
    config = replace(runtime, observe_only=False, metadata=config.metadata)
    save_config(config)
    head, _dirty = repo_snapshot(backend)
    state = replace(state, task=TaskItem(task_id, prompt), task_base_head=head)
    handoff = AtomicJsonStore(config.coordination_root / "handoff" / "backend.json")
    handoff.write({**handoff.read(), "last_provider": provider})
    calls = []

    class ReviewInvoker:
        def review(self, role, _state, task, _context, model):
            calls.append((role, task.task_id, model))
            return AgentResult(
                outcome="changes_required",
                summary="Independent review required",
                next_action="Wait for required Codex gate",
                role=role,
                task_id=task.task_id,
            )

    supervisor = Supervisor(config, ReviewInvoker())
    supervisor.save_state(state)
    supervisor._review("backend", state, "bounded context")
    assert calls and calls[0][1] == task_id
    assert supervisor.load_state("backend").status is WorkerStatus.IMPLEMENTING
