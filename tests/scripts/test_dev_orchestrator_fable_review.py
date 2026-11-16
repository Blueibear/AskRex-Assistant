from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from scripts.dev_orchestrator.provider_routing import (
    FABLE_REVIEW_MODEL,
    ProviderRoutingInvoker,
    temporary_fable_review_active,
)
from scripts.dev_orchestrator.runner import AgentInvocationError
from scripts.dev_orchestrator.storage import AtomicJsonStore
from scripts.dev_orchestrator.types import (
    AgentResult,
    OrchestratorConfig,
    TaskItem,
    WorkerState,
)


def config(tmp_path):
    root = tmp_path / "coordination"
    root.mkdir()
    return replace(
        OrchestratorConfig.default(root),
        metadata={
            "temporary_fable_review": {
                "enabled": True,
                "reason": "openai_weekly_usage_limit",
                "model": "fable",
                "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
                "roles": ["backend", "mobile"],
            }
        },
    )


def test_fable_review_only_for_ordinary_claude_work(tmp_path):
    cfg = config(tmp_path)
    normal = TaskItem("mobile-feature-123", "Fix a UI layout regression")
    assert temporary_fable_review_active(cfg, "mobile", normal, "claude")
    assert temporary_fable_review_active(cfg, "mobile", normal, "claude_cloud")
    assert not temporary_fable_review_active(cfg, "mobile", normal, "codex")
    for task in (
        TaskItem("FINAL-VERIFY-mobile", "Finish release"),
        TaskItem("backend-security-fix", "Check source"),
        TaskItem("mobile-feature", "Fix privacy access"),
        TaskItem("mobile-feature", "Requires independent Codex review"),
        TaskItem("mobile-feature", "Requires cross-provider review"),
    ):
        assert not temporary_fable_review_active(cfg, "mobile", task, "claude")


def test_fable_review_expiry_and_fail_closed_policy(tmp_path):
    cfg = config(tmp_path)
    task = TaskItem("mobile-feature", "Improve layout")
    now = datetime.now(UTC)
    assert not temporary_fable_review_active(
        cfg, "mobile", task, "claude", now=now + timedelta(days=2)
    )
    for change, error in (
        ({"model": "sonnet"}, "pinned fable"),
        ({"roles": ["unknown"]}, "allowlist"),
        ({"reason": "convenience"}, "usage-limit"),
        ({"expires_at": (now + timedelta(days=8)).isoformat()}, "seven days"),
        ({"expires_at": "not-a-date"}, "valid expiry"),
    ):
        invalid = replace(cfg, metadata={
            "temporary_fable_review": {
                **cfg.metadata["temporary_fable_review"], **change
            }
        })
        with pytest.raises(ValueError, match=error):
            temporary_fable_review_active(invalid, "mobile", task, "claude", now=now)


class FakeCli:
    def __init__(self, failure=None):
        self.calls = []
        self.failure = failure

    def review(self, role, state, task, context, model):
        self.calls.append((role, task.task_id, model))
        if self.failure:
            raise self.failure
        return AgentResult("pass", "Reviewed", "Continue", role=role, task_id=task.task_id)


def test_fable_review_uses_subscription_and_no_api_fallback(tmp_path):
    cfg = config(tmp_path)
    handoff = cfg.coordination_root / "handoff" / "mobile.json"
    AtomicJsonStore(handoff).write({"last_provider": "claude"})
    failure = AgentInvocationError("claude", "usage_limit", "Fable quota reached")
    cli = FakeCli(failure)
    router = ProviderRoutingInvoker(cfg, cli_invoker=cli)
    task = TaskItem("mobile-feature", "Improve layout")
    with pytest.raises(AgentInvocationError) as caught:
        router.review("mobile", WorkerState("mobile"), task, "ctx", FABLE_REVIEW_MODEL)
    assert caught.value is failure
    assert cli.calls == [("mobile", "mobile-feature", "fable")]


def test_fable_review_refuses_codex_and_protected_checkpoints(tmp_path):
    cfg = config(tmp_path)
    handoff = cfg.coordination_root / "handoff" / "backend.json"
    cli = FakeCli()
    router = ProviderRoutingInvoker(cfg, cli_invoker=cli)
    for provider, task in (
        ("codex", TaskItem("feature-123", "Improve UI")),
        ("claude", TaskItem("backend-security", "Fix vulnerability")),
    ):
        AtomicJsonStore(handoff).write({"last_provider": provider})
        with pytest.raises(AgentInvocationError, match="not authorized"):
            router.review("backend", WorkerState("backend"), task, "ctx", FABLE_REVIEW_MODEL)
    assert cli.calls == []
