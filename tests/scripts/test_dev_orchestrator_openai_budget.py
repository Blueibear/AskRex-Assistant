from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from scripts.dev_orchestrator.openai_budget import (
    HARD_MONTHLY_CAP_USD,
    OpenAIBudgetExceeded,
    OpenAIBudgetLedger,
    OpenAIBudgetPolicyError,
    OpenAIUsage,
)


def _clock(stamp: datetime):
    current = [stamp]

    def now() -> datetime:
        return current[0]

    return current, now


def test_reservation_is_durable_and_reconciliation_releases_unused_risk(tmp_path: Path) -> None:
    _, now = _clock(datetime(2026, 9, 15, 12, tzinfo=UTC))
    ledger = OpenAIBudgetLedger(tmp_path, now_fn=now)
    reservation = ledger.reserve(
        model="gpt-5.6-sol",
        input_token_ceiling=100_000,
        output_token_ceiling=10_000,
    )

    assert reservation.month == "2026-09"
    assert reservation.reserved_usd == Decimal("0.700000")
    assert ledger.status()["spent_or_reserved_usd"] == Decimal("0.700000")

    reloaded = OpenAIBudgetLedger(tmp_path, now_fn=now)
    assert reloaded.status()["spent_or_reserved_usd"] == Decimal("0.700000")
    raw = __import__("json").loads(
        (tmp_path / "usage" / "openai-api-spend.json").read_text(encoding="utf-8")
    )
    entry = raw["months"]["2026-09"]["reservations"][0]
    assert set(entry) == {
        "reservation_id",
        "model",
        "purpose",
        "episode_key",
        "reserved_usd",
        "actual_usd",
        "input_tokens",
        "output_tokens",
        "status",
        "created_at",
        "updated_at",
    }
    assert entry["purpose"] == ""
    assert entry["episode_key"] == ""

    actual = reloaded.reconcile(
        reservation.reservation_id,
        OpenAIUsage(input_tokens=10_000, output_tokens=1_000),
    )
    assert actual == Decimal("0.070000")
    assert reloaded.status()["spent_or_reserved_usd"] == Decimal("0.070000")


def test_exact_monthly_cap_is_allowed_and_any_overage_is_refused(tmp_path: Path) -> None:
    _, now = _clock(datetime(2026, 9, 15, 12, tzinfo=UTC))
    ledger = OpenAIBudgetLedger(tmp_path, now_fn=now)
    reservation = ledger.reserve(
        model="gpt-6-astra",
        input_token_ceiling=0,
        output_token_ceiling=600_000,
    )
    assert reservation.reserved_usd == HARD_MONTHLY_CAP_USD
    assert ledger.status()["remaining_usd"] == Decimal("0.00")

    with pytest.raises(OpenAIBudgetExceeded):
        ledger.reserve(
            model="gpt-5.6-terra",
            input_token_ceiling=1,
            output_token_ceiling=1,
        )


def test_unresolved_and_uncertain_reservations_remain_fully_charged(tmp_path: Path) -> None:
    _, now = _clock(datetime(2026, 9, 15, 12, tzinfo=UTC))
    ledger = OpenAIBudgetLedger(tmp_path, now_fn=now)
    reservation = ledger.reserve(
        model="gpt-5.6-terra",
        input_token_ceiling=100_000,
        output_token_ceiling=10_000,
    )
    initial = ledger.status()["spent_or_reserved_usd"]
    assert initial == reservation.reserved_usd

    ledger.mark_uncertain(reservation.reservation_id)
    assert ledger.status()["spent_or_reserved_usd"] == initial

    reloaded = OpenAIBudgetLedger(tmp_path, now_fn=now)
    assert reloaded.status()["spent_or_reserved_usd"] == initial


def test_malformed_usage_fails_closed_and_keeps_full_reservation(tmp_path: Path) -> None:
    _, now = _clock(datetime(2026, 9, 15, 12, tzinfo=UTC))
    ledger = OpenAIBudgetLedger(tmp_path, now_fn=now)
    reservation = ledger.reserve(
        model="gpt-5.6-sol",
        input_token_ceiling=10_000,
        output_token_ceiling=1_000,
    )

    with pytest.raises(OpenAIBudgetPolicyError):
        ledger.reconcile(reservation.reservation_id, None)
    assert ledger.status()["spent_or_reserved_usd"] == reservation.reserved_usd

    with pytest.raises(OpenAIBudgetPolicyError):
        ledger.reconcile(reservation.reservation_id, OpenAIUsage(input_tokens=-1, output_tokens=1))

    assert ledger.status()["spent_or_reserved_usd"] == reservation.reserved_usd


def test_usage_above_reserved_ceiling_fails_closed(tmp_path: Path) -> None:
    _, now = _clock(datetime(2026, 9, 15, 12, tzinfo=UTC))
    ledger = OpenAIBudgetLedger(tmp_path, now_fn=now)
    reservation = ledger.reserve(
        model="gpt-5.6-sol",
        input_token_ceiling=1_000,
        output_token_ceiling=100,
    )

    with pytest.raises(OpenAIBudgetPolicyError):
        ledger.reconcile(
            reservation.reservation_id,
            OpenAIUsage(input_tokens=100_000, output_tokens=10_000),
        )

    assert ledger.status()["spent_or_reserved_usd"] == reservation.reserved_usd


def test_unknown_model_pricing_is_rejected_before_reservation(tmp_path: Path) -> None:
    _, now = _clock(datetime(2026, 9, 15, 12, tzinfo=UTC))
    ledger = OpenAIBudgetLedger(tmp_path, now_fn=now)

    with pytest.raises(OpenAIBudgetPolicyError):
        ledger.reserve(
            model="gpt-unknown",
            input_token_ceiling=1_000,
            output_token_ceiling=1_000,
        )


def test_utc_month_rollover_starts_a_new_bucket_without_deleting_history(tmp_path: Path) -> None:
    current, now = _clock(datetime(2026, 9, 30, 23, 59, tzinfo=UTC))
    ledger = OpenAIBudgetLedger(tmp_path, now_fn=now)
    september = ledger.reserve(
        model="gpt-6-astra",
        input_token_ceiling=0,
        output_token_ceiling=600_000,
    )
    assert september.month == "2026-09"

    current[0] = datetime(2026, 10, 1, 0, 1, tzinfo=UTC)
    october = ledger.reserve(
        model="gpt-5.6-terra",
        input_token_ceiling=1_000,
        output_token_ceiling=100,
    )
    assert october.month == "2026-10"
    assert ledger.status()["month"] == "2026-10"
    assert ledger.status()["remaining_usd"] < HARD_MONTHLY_CAP_USD

    raw = (tmp_path / "usage" / "openai-api-spend.json").read_text(encoding="utf-8")
    assert '"2026-09"' in raw
    assert '"2026-10"' in raw


def test_concurrent_full_cap_reservations_cannot_race_past_limit(tmp_path: Path) -> None:
    import threading

    _, now = _clock(datetime(2026, 9, 15, 12, tzinfo=UTC))
    ledgers = [OpenAIBudgetLedger(tmp_path, now_fn=now) for _ in range(2)]
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def reserve_full(ledger: OpenAIBudgetLedger) -> None:
        barrier.wait()
        try:
            ledger.reserve(
                model="gpt-6-astra",
                input_token_ceiling=0,
                output_token_ceiling=600_000,
            )
        except OpenAIBudgetExceeded:
            outcomes.append("blocked")
        else:
            outcomes.append("reserved")

    threads = [threading.Thread(target=reserve_full, args=(ledger,)) for ledger in ledgers]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert sorted(outcomes) == ["blocked", "reserved"]
    assert ledgers[0].status()["spent_or_reserved_usd"] == HARD_MONTHLY_CAP_USD


def test_long_context_reservation_applies_verified_multipliers(tmp_path: Path) -> None:
    _, now = _clock(datetime(2026, 9, 15, 12, tzinfo=UTC))
    ledger = OpenAIBudgetLedger(tmp_path, now_fn=now)

    reservation = ledger.reserve(
        model="gpt-5.6-sol",
        input_token_ceiling=272_001,
        output_token_ceiling=1_000,
    )

    assert reservation.reserved_usd == Decimal("2.75001")


def test_configured_cap_cannot_exceed_hard_monthly_cap(tmp_path: Path) -> None:
    _, now = _clock(datetime(2026, 9, 15, 12, tzinfo=UTC))

    with pytest.raises(OpenAIBudgetPolicyError):
        OpenAIBudgetLedger(
            tmp_path,
            monthly_cap_usd=Decimal("30.01"),
            now_fn=now,
        )
