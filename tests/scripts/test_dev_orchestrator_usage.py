from datetime import UTC, datetime
from pathlib import Path

from scripts.dev_orchestrator.alerts import AlertSink
from scripts.dev_orchestrator.usage import UsageBudget, handle_usage_limit


def fixed_now() -> datetime:
    return datetime(2026, 9, 10, 20, 0, tzinfo=UTC)


def test_usage_budget_starts_with_three_resets() -> None:
    budget = UsageBudget()
    assert budget.banked_resets_remaining == 3
    assert budget.reserve_last_reset is True


def test_codex_limit_prefers_claude_fallback_without_consuming_reset(tmp_path: Path) -> None:
    sink = AlertSink(tmp_path / "alerts", now=fixed_now)
    budget = UsageBudget()
    decision = handle_usage_limit(
        budget, sink, role="backend", provider="codex", reason="weekly limit", claude_available=True
    )
    assert decision.action == "fallback_claude"
    assert decision.budget.banked_resets_remaining == 3
    assert list((tmp_path / "alerts").glob("*.json")) == []


def test_reset_recommendation_does_not_decrement_before_confirmation(tmp_path: Path) -> None:
    sink = AlertSink(tmp_path / "alerts", now=fixed_now)
    budget = UsageBudget()
    decision = handle_usage_limit(
        budget,
        sink,
        role="backend",
        provider="codex",
        reason="weekly limit",
        claude_available=False,
    )
    assert decision.action == "request_reset"
    assert decision.blocked_user is True
    assert decision.budget.banked_resets_remaining == 3
    alerts = list((tmp_path / "alerts").glob("*.json"))
    assert len(alerts) == 1
    text = alerts[0].read_text(encoding="utf-8")
    assert "backend" in text
    assert "3 banked reset" in text
    assert "weekly limit" in text


def test_last_reset_is_reserved_by_default(tmp_path: Path) -> None:
    sink = AlertSink(tmp_path / "alerts", now=fixed_now)
    budget = UsageBudget(banked_resets_remaining=1, reserve_last_reset=True)
    decision = handle_usage_limit(
        budget, sink, role="mobile", provider="codex", reason="weekly limit", claude_available=False
    )
    assert decision.action == "preserve_last_reset"
    assert decision.blocked_user is True
    assert decision.budget.banked_resets_remaining == 1


def test_identical_active_alerts_are_deduplicated(tmp_path: Path) -> None:
    sink = AlertSink(tmp_path / "alerts", now=fixed_now)
    first = sink.emit(role="backend", kind="usage_reset", message="Use one reset")
    second = sink.emit(role="backend", kind="usage_reset", message="Use one reset")
    assert first == second
    assert len(list((tmp_path / "alerts").glob("*.json"))) == 1


def test_confirmed_reset_count_changes_only_from_confirmed_value() -> None:
    budget = UsageBudget()
    unchanged = budget.with_confirmed_remaining(3)
    consumed = budget.with_confirmed_remaining(2)
    assert unchanged.banked_resets_remaining == 3
    assert consumed.banked_resets_remaining == 2


def test_claude_usage_limit_requests_user_without_spending_codex_reset(tmp_path: Path) -> None:
    sink = AlertSink(tmp_path / "alerts", now=fixed_now)
    budget = UsageBudget()
    decision = handle_usage_limit(
        budget,
        sink,
        role="mobile",
        provider="claude",
        reason="Claude allowance exhausted",
        claude_available=False,
    )
    assert decision.action == "wait_for_claude"
    assert decision.blocked_user is True
    assert decision.budget.banked_resets_remaining == 3
