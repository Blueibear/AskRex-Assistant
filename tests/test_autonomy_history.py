"""Unit tests for rex.autonomy.history — ExecutionRecord and HistoryStore."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from rex.autonomy.history import ExecutionRecord, HistoryStore, OutcomeType
from rex.autonomy.models import Plan, PlanStep

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_plan(goal: str = "Test goal") -> Plan:
    step = PlanStep(id="s1", tool="noop", description="Do nothing")
    return Plan(id=str(uuid.uuid4()), goal=goal, steps=[step])


def _make_record(
    goal: str = "Test goal",
    outcome: OutcomeType = "success",
    duration_s: float = 1.0,
    replan_count: int = 0,
    error_summary: str | None = None,
) -> ExecutionRecord:
    return ExecutionRecord(
        goal=goal,
        plan=_make_plan(goal),
        outcome=outcome,
        duration_s=duration_s,
        replan_count=replan_count,
        error_summary=error_summary,
    )


# ---------------------------------------------------------------------------
# ExecutionRecord model tests
# ---------------------------------------------------------------------------


class TestExecutionRecord:
    def test_id_auto_generated(self) -> None:
        record = _make_record()
        assert record.id != ""

    def test_two_records_have_different_ids(self) -> None:
        r1 = _make_record()
        r2 = _make_record()
        assert r1.id != r2.id

    def test_timestamp_is_utc(self) -> None:
        record = _make_record()
        assert record.timestamp.tzinfo is not None
        assert record.timestamp.tzinfo == UTC

    def test_fields_stored_correctly(self) -> None:
        record = _make_record(
            goal="My goal",
            outcome="partial",
            duration_s=3.14,
            replan_count=2,
            error_summary="Something broke",
        )
        assert record.goal == "My goal"
        assert record.outcome == "partial"
        assert record.duration_s == pytest.approx(3.14)
        assert record.replan_count == 2
        assert record.error_summary == "Something broke"

    def test_error_summary_defaults_to_none(self) -> None:
        record = _make_record()
        assert record.error_summary is None

    def test_replan_count_defaults_to_zero(self) -> None:
        record = _make_record()
        assert record.replan_count == 0

    def test_plan_field_is_plan_instance(self) -> None:
        record = _make_record()
        assert isinstance(record.plan, Plan)


# ---------------------------------------------------------------------------
# HistoryStore.append
# ---------------------------------------------------------------------------


class TestHistoryStoreAppend:
    @pytest.mark.asyncio
    async def test_append_does_not_raise(self, tmp_path: Path) -> None:
        store = HistoryStore(db_path=tmp_path / "test.db")
        record = _make_record()
        await store.append(record)  # should not raise

    @pytest.mark.asyncio
    async def test_appended_record_appears_in_recent(self, tmp_path: Path) -> None:
        store = HistoryStore(db_path=tmp_path / "test.db")
        record = _make_record(goal="Unique goal xyz")
        await store.append(record)

        results = await store.recent(n=10)
        assert any(r.id == record.id for r in results)

    @pytest.mark.asyncio
    async def test_appended_record_preserves_goal(self, tmp_path: Path) -> None:
        store = HistoryStore(db_path=tmp_path / "test.db")
        record = _make_record(goal="Preserve this goal")
        await store.append(record)

        results = await store.recent(n=1)
        assert results[0].goal == "Preserve this goal"

    @pytest.mark.asyncio
    async def test_appended_record_preserves_outcome(self, tmp_path: Path) -> None:
        store = HistoryStore(db_path=tmp_path / "test.db")
        record = _make_record(outcome="failed")
        await store.append(record)

        results = await store.recent(n=1)
        assert results[0].outcome == "failed"

    @pytest.mark.asyncio
    async def test_appended_record_preserves_duration(self, tmp_path: Path) -> None:
        store = HistoryStore(db_path=tmp_path / "test.db")
        record = _make_record(duration_s=42.5)
        await store.append(record)

        results = await store.recent(n=1)
        assert results[0].duration_s == pytest.approx(42.5)

    @pytest.mark.asyncio
    async def test_appended_record_preserves_replan_count(self, tmp_path: Path) -> None:
        store = HistoryStore(db_path=tmp_path / "test.db")
        record = _make_record(replan_count=3)
        await store.append(record)

        results = await store.recent(n=1)
        assert results[0].replan_count == 3

    @pytest.mark.asyncio
    async def test_appended_record_preserves_error_summary(self, tmp_path: Path) -> None:
        store = HistoryStore(db_path=tmp_path / "test.db")
        record = _make_record(outcome="failed", error_summary="Disk full")
        await store.append(record)

        results = await store.recent(n=1)
        assert results[0].error_summary == "Disk full"

    @pytest.mark.asyncio
    async def test_appended_record_preserves_plan_steps(self, tmp_path: Path) -> None:
        store = HistoryStore(db_path=tmp_path / "test.db")
        record = _make_record()
        await store.append(record)

        results = await store.recent(n=1)
        assert len(results[0].plan.steps) == len(record.plan.steps)
        assert results[0].plan.steps[0].tool == record.plan.steps[0].tool


# ---------------------------------------------------------------------------
# HistoryStore.recent
# ---------------------------------------------------------------------------


class TestHistoryStoreRecent:
    @pytest.mark.asyncio
    async def test_recent_returns_empty_list_when_no_records(self, tmp_path: Path) -> None:
        store = HistoryStore(db_path=tmp_path / "test.db")
        results = await store.recent()
        assert results == []

    @pytest.mark.asyncio
    async def test_recent_returns_all_when_fewer_than_n(self, tmp_path: Path) -> None:
        store = HistoryStore(db_path=tmp_path / "test.db")
        for _ in range(3):
            await store.append(_make_record())

        results = await store.recent(n=10)
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_recent_limits_to_n(self, tmp_path: Path) -> None:
        store = HistoryStore(db_path=tmp_path / "test.db")
        for _ in range(5):
            await store.append(_make_record())

        results = await store.recent(n=3)
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_recent_ordered_newest_first(self, tmp_path: Path) -> None:
        store = HistoryStore(db_path=tmp_path / "test.db")
        for i in range(3):
            await store.append(_make_record(goal=f"goal-{i}"))

        results = await store.recent(n=3)
        # Most recently appended has goal-2
        assert results[0].goal == "goal-2"

    @pytest.mark.asyncio
    async def test_recent_tied_timestamps_use_append_order(self, tmp_path: Path) -> None:
        store = HistoryStore(db_path=tmp_path / "test.db")
        timestamp = datetime(2026, 1, 1, tzinfo=UTC)
        for i in range(3):
            record = _make_record(goal=f"goal-{i}").model_copy(update={"timestamp": timestamp})
            await store.append(record)

        assert [r.goal for r in await store.recent(n=3)] == ["goal-2", "goal-1", "goal-0"]

    @pytest.mark.asyncio
    async def test_recent_default_n_is_20(self, tmp_path: Path) -> None:
        store = HistoryStore(db_path=tmp_path / "test.db")
        for _ in range(25):
            await store.append(_make_record())

        results = await store.recent()
        assert len(results) == 20


# ---------------------------------------------------------------------------
# HistoryStore.by_outcome
# ---------------------------------------------------------------------------


class TestHistoryStoreByOutcome:
    @pytest.mark.asyncio
    async def test_by_outcome_returns_only_matching(self, tmp_path: Path) -> None:
        store = HistoryStore(db_path=tmp_path / "test.db")
        await store.append(_make_record(outcome="success"))
        await store.append(_make_record(outcome="failed"))
        await store.append(_make_record(outcome="success"))

        results = await store.by_outcome("success")
        assert all(r.outcome == "success" for r in results)
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_by_outcome_returns_empty_when_none_match(self, tmp_path: Path) -> None:
        store = HistoryStore(db_path=tmp_path / "test.db")
        await store.append(_make_record(outcome="success"))

        results = await store.by_outcome("partial")
        assert results == []

    @pytest.mark.asyncio
    async def test_by_outcome_partial(self, tmp_path: Path) -> None:
        store = HistoryStore(db_path=tmp_path / "test.db")
        await store.append(_make_record(outcome="partial"))

        results = await store.by_outcome("partial")
        assert len(results) == 1
        assert results[0].outcome == "partial"

    @pytest.mark.asyncio
    async def test_by_outcome_failed(self, tmp_path: Path) -> None:
        store = HistoryStore(db_path=tmp_path / "test.db")
        await store.append(_make_record(outcome="failed", error_summary="Boom"))

        results = await store.by_outcome("failed")
        assert len(results) == 1
        assert results[0].error_summary == "Boom"

    @pytest.mark.asyncio
    async def test_by_outcome_ordered_newest_first(self, tmp_path: Path) -> None:
        store = HistoryStore(db_path=tmp_path / "test.db")
        await store.append(_make_record(goal="first-success", outcome="success"))
        await store.append(_make_record(goal="second-success", outcome="success"))

        results = await store.by_outcome("success")
        assert results[0].goal == "second-success"

    @pytest.mark.asyncio
    async def test_by_outcome_tied_timestamps_use_append_order(self, tmp_path: Path) -> None:
        store = HistoryStore(db_path=tmp_path / "test.db")
        timestamp = datetime(2026, 1, 1, tzinfo=UTC)
        for i in range(3):
            record = _make_record(goal=f"goal-{i}", outcome="success").model_copy(
                update={"timestamp": timestamp}
            )
            await store.append(record)

        assert [r.goal for r in await store.by_outcome("success")] == ["goal-2", "goal-1", "goal-0"]

    @pytest.mark.asyncio
    async def test_db_persists_across_store_instances(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        store1 = HistoryStore(db_path=db)
        await store1.append(_make_record(goal="Persistent goal"))

        store2 = HistoryStore(db_path=db)
        results = await store2.recent(n=1)
        assert results[0].goal == "Persistent goal"
