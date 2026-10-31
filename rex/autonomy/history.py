"""Execution history data model and persistent store for the Rex autonomy engine.

Provides:
- :class:`ExecutionRecord` — a Pydantic model capturing the outcome of one
  plan execution.
- :class:`HistoryStore` — an async SQLite-backed store for
  :class:`ExecutionRecord` objects.

The backing database is created automatically beneath the validated user's private
runtime directory on first access.  The migration (table creation) also runs on first access so
callers never need to manage schema versions.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import aiosqlite
from pydantic import BaseModel, Field

from rex.autonomy.models import Plan
from rex.runtime_paths import user_data_path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OutcomeType = Literal["success", "partial", "failed"]

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS execution_records (
    id              TEXT    PRIMARY KEY,
    goal            TEXT    NOT NULL,
    plan_json       TEXT    NOT NULL,
    outcome         TEXT    NOT NULL,
    duration_s      REAL    NOT NULL,
    replan_count    INTEGER NOT NULL,
    error_summary   TEXT,
    timestamp       TEXT    NOT NULL,
    total_cost_usd  REAL    NOT NULL DEFAULT 0.0
)
"""

_MIGRATE_ADD_COST_SQL = (
    "ALTER TABLE execution_records ADD COLUMN total_cost_usd REAL NOT NULL DEFAULT 0.0"
)

# ---------------------------------------------------------------------------
# ExecutionRecord model
# ---------------------------------------------------------------------------


class ExecutionRecord(BaseModel):
    """A record of a single plan execution.

    Args:
        id: Unique identifier for this record.
        goal: The natural-language goal that was planned.
        plan: The :class:`~rex.autonomy.models.Plan` that was executed
            (serialised to JSON for storage).
        outcome: One of ``"success"``, ``"partial"``, or ``"failed"``.
        duration_s: Wall-clock seconds from plan start to completion.
        replan_count: Number of times the plan was replanned during execution.
        error_summary: Human-readable summary of any errors; ``None`` on
            successful runs.
        timestamp: UTC datetime when execution completed.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    goal: str
    plan: Plan
    outcome: OutcomeType
    duration_s: float
    replan_count: int = 0
    error_summary: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    total_cost_usd: float = 0.0


# ---------------------------------------------------------------------------
# HistoryStore
# ---------------------------------------------------------------------------


class HistoryStore:
    """Async SQLite-backed store for :class:`ExecutionRecord` objects.

    Args:
        db_path: Explicit SQLite path, primarily for tests and migrations.
        user_id: Validated owner used when ``db_path`` is omitted.
    """

    def __init__(self, db_path: Path | None = None, *, user_id: str = "default") -> None:
        self._db_path = (
            Path(db_path)
            if db_path is not None
            else user_data_path(user_id, "autonomy", "execution_history.db")
        )

    # ------------------------------------------------------------------
    # Migration / initialisation
    # ------------------------------------------------------------------

    async def _ensure_table(self, conn: aiosqlite.Connection) -> None:
        """Create the ``execution_records`` table if it does not exist.

        Also applies any incremental migrations (e.g. adding ``total_cost_usd``
        to pre-existing databases).
        """
        await conn.execute(_CREATE_TABLE_SQL)
        # Migration: add total_cost_usd column to databases created before US-233.
        try:
            await conn.execute(_MIGRATE_ADD_COST_SQL)
        except Exception:
            # Column already exists — this is the expected case for new and
            # already-migrated databases.
            pass
        await conn.commit()

    async def _open(self) -> aiosqlite.Connection:
        """Open (and migrate) the database, creating parent dirs if needed."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(str(self._db_path))
        await self._ensure_table(conn)
        return conn

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def append(self, record: ExecutionRecord) -> None:
        """Persist *record* to the database.

        Args:
            record: The :class:`ExecutionRecord` to store.
        """
        plan_json = record.plan.model_dump_json()
        conn = await self._open()
        try:
            await conn.execute(
                """
                INSERT INTO execution_records
                    (id, goal, plan_json, outcome, duration_s, replan_count,
                     error_summary, timestamp, total_cost_usd)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    record.goal,
                    plan_json,
                    record.outcome,
                    record.duration_s,
                    record.replan_count,
                    record.error_summary,
                    record.timestamp.isoformat(),
                    record.total_cost_usd,
                ),
            )
            await conn.commit()
        finally:
            await conn.close()
        logger.debug("HistoryStore: appended record %s (outcome=%s)", record.id, record.outcome)

    async def recent(self, n: int = 20) -> list[ExecutionRecord]:
        """Return the *n* most recent records, newest first.

        Args:
            n: Maximum number of records to return.  Defaults to ``20``.

        Returns:
            A list of :class:`ExecutionRecord` objects, ordered by
            ``timestamp`` descending.
        """
        conn = await self._open()
        try:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT * FROM execution_records ORDER BY timestamp DESC, rowid DESC LIMIT ?",
                (n,),
            ) as cursor:
                rows = await cursor.fetchall()
        finally:
            await conn.close()
        return [_row_to_record(row) for row in rows]

    async def by_outcome(self, outcome: OutcomeType) -> list[ExecutionRecord]:
        """Return all records with the given *outcome*.

        Args:
            outcome: One of ``"success"``, ``"partial"``, or ``"failed"``.

        Returns:
            A list of :class:`ExecutionRecord` objects with the matching
            outcome, ordered by ``timestamp`` descending.
        """
        conn = await self._open()
        try:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT * FROM execution_records WHERE outcome = ? ORDER BY timestamp DESC, rowid DESC",
                (outcome,),
            ) as cursor:
                rows = await cursor.fetchall()
        finally:
            await conn.close()
        return [_row_to_record(row) for row in rows]


# ---------------------------------------------------------------------------
# Row → record helper
# ---------------------------------------------------------------------------


def _row_to_record(row: aiosqlite.Row) -> ExecutionRecord:
    """Convert a database row into an :class:`ExecutionRecord`."""
    plan = Plan.model_validate_json(row["plan_json"])
    return ExecutionRecord(
        id=row["id"],
        goal=row["goal"],
        plan=plan,
        outcome=row["outcome"],
        duration_s=row["duration_s"],
        replan_count=row["replan_count"],
        error_summary=row["error_summary"],
        timestamp=datetime.fromisoformat(row["timestamp"]),
        total_cost_usd=float(row["total_cost_usd"]),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "ExecutionRecord",
    "HistoryStore",
    "OutcomeType",
]
