from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from scripts.dev_orchestrator.provider_routing import (
    FABLE_REVIEW_MODEL,
    ProviderRoutingInvoker,
    temporary_fable_policy_active,
    temporary_fable_review_active,
)
from scripts.dev_orchestrator.runner import AgentInvocationError
from scripts.dev_orchestrator.storage import AtomicJsonStore
from scripts.dev_orchestrator.supervisor import Supervisor
from scripts.dev_orchestrator.types import (
    AgentResult,
    OrchestratorConfig,
    TaskItem,
    WorkerState,
    WorkerStatus,
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


def test_owner_authorized_codex_equivalent_reviews(tmp_path):
    cfg = config(tmp_path)
    cfg = replace(
        cfg,
        metadata={
            "temporary_fable_review": {
                **cfg.metadata["temporary_fable_review"],
                "allow_codex_equivalent_reviews": True,
            }
        },
    )
    for task in (
        TaskItem("backend-security", "Review authentication policy"),
        TaskItem("FINAL-VERIFY-mobile", "Review release readiness"),
        TaskItem("mobile-r16-01-status-payload-truth", "Review status payload"),
    ):
        assert temporary_fable_review_active(cfg, "mobile", task, "codex")
        assert temporary_fable_review_active(cfg, "backend", task, "claude")


def test_s10_no_change_guardrails_remain_eligible_for_fable(tmp_path):
    cfg = config(tmp_path)
    task = TaskItem(
        "mobile-s10-mock-removal-capability-gating-20261116",
        "Remove silent production mock fallbacks per P0 directive "
        "(MSG-20261116-cross-surface-audit-priority.md). "
        "Do NOT implement the speculative client-side /mobile/status endpoint or "
        "build dependent screens until backend publishes an authenticated heartbeat "
        "contract; this has not landed in this snapshot. "
        "Preserve existing device pairing, user identity, privacy, and approval flows unchanged.",
    )
    assert temporary_fable_review_active(cfg, "mobile", task, "claude")
    assert temporary_fable_review_active(cfg, "mobile", task, "claude_cloud")

    # The protected reference is safe only as a negative scope constraint.
    assert not temporary_fable_review_active(
        cfg, "mobile", replace(task, feedback="Change authentication checks"), "claude"
    )
    assert not temporary_fable_review_active(
        cfg,
        "mobile",
        replace(task, prompt=task.prompt + " Also revise the security policy."),
        "claude",
    )
    assert not temporary_fable_review_active(
        cfg,
        "mobile",
        replace(task, task_id="mobile-s10-privacy-policy-change"),
        "claude",
    )


def test_stale_hourly_wait_reenters_fable_routing(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    cfg = replace(
        cfg,
        observe_only=False,
        metadata={
            **cfg.metadata,
            "review_fallback": {
                "enabled": True,
                "reason": "openai_weekly_usage_limit",
                "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            },
        },
    )
    assert temporary_fable_policy_active(cfg, "mobile")
    AtomicJsonStore(cfg.coordination_root / "handoff" / "mobile.json").write(
        {"last_provider": "claude"}
    )
    supervisor = Supervisor(cfg, FakeCli())
    state = WorkerState(
        role="mobile",
        status=WorkerStatus.BLOCKED_SYSTEM,
        task=TaskItem("mobile-feature", "Fix a layout bug"),
        task_base_head="a" * 40,
        blocked_reason="Awaiting hourly ChatGPT review receipt",
        blocker_kind="review_fallback_wait",
        resume_status=WorkerStatus.REVIEWING,
    )
    supervisor.save_state(state)
    observed = []
    monkeypatch.setattr(
        supervisor,
        "_review",
        lambda role, resumed, context: observed.append(
            (role, resumed.status, resumed.blocker_kind)
        ),
    )

    supervisor._run_role("mobile")

    assert observed == [("mobile", WorkerStatus.REVIEWING, "")]
    assert supervisor.load_state("mobile").status is WorkerStatus.REVIEWING


def test_s10_review_selects_fable_ahead_of_hourly_receipt(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    cfg = replace(
        cfg,
        metadata={
            **cfg.metadata,
            "review_fallback": {
                "enabled": True,
                "reason": "openai_weekly_usage_limit",
                "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            },
        },
    )
    AtomicJsonStore(cfg.coordination_root / "handoff" / "mobile.json").write(
        {"last_provider": "claude"}
    )
    cli = FakeCli()
    supervisor = Supervisor(cfg, cli)
    task = TaskItem(
        "mobile-s10-mock-removal-capability-gating-20261116",
        "Remove mocks per (MSG-20261116-cross-surface-audit-priority.md). "
        "Do NOT implement the speculative client-side /mobile/status endpoint "
        "until the authenticated heartbeat has not landed in this snapshot. "
        "Preserve existing device pairing, user identity, privacy, and approval flows unchanged.",
    )
    state = WorkerState(
        role="mobile", status=WorkerStatus.REVIEWING, task=task, task_base_head="a" * 40
    )
    monkeypatch.setattr(supervisor, "_accept_result", lambda *args, **kwargs: False)

    supervisor._review("mobile", state, "bounded evidence")

    assert cli.calls == [("mobile", task.task_id, FABLE_REVIEW_MODEL)]
    assert not list((cfg.coordination_root / "review-fallback").glob("*.json"))


def test_protected_work_keeps_codex_gate_without_chatgpt_receipt(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    cfg = replace(
        cfg,
        metadata={
            **cfg.metadata,
            "review_fallback": {
                "enabled": True,
                "reason": "openai_weekly_usage_limit",
                "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            },
        },
    )
    AtomicJsonStore(cfg.coordination_root / "handoff" / "backend.json").write(
        {"last_provider": "claude"}
    )
    cli = FakeCli()
    supervisor = Supervisor(cfg, cli)
    state = WorkerState(
        role="backend",
        status=WorkerStatus.REVIEWING,
        task=TaskItem("backend-security-fix", "Improve authentication policy"),
        task_base_head="a" * 40,
    )
    monkeypatch.setattr(supervisor, "_accept_result", lambda *args, **kwargs: False)

    supervisor._review("backend", state, "bounded evidence")

    assert cli.calls == [("backend", "backend-security-fix", "gpt-5.6-terra")]
    assert not list((cfg.coordination_root / "review-fallback").glob("*.json"))


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
        invalid = replace(
            cfg,
            metadata={
                "temporary_fable_review": {**cfg.metadata["temporary_fable_review"], **change}
            },
        )
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
