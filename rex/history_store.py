"""Conversation history persistence backed by SQLite.

Stores conversation turns (user / assistant messages) per user so that
sessions can be resumed after restarts.

Default database path: ``data/household/history.db`` (configurable via ``db_path``).
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from rex.identity import validate_user_id
from rex.runtime_paths import household_data_path

logger = logging.getLogger(__name__)


_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS turns (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id   TEXT    NOT NULL,
    role      TEXT    NOT NULL,
    content   TEXT    NOT NULL,
    timestamp TEXT    NOT NULL
);
"""

_CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_turns_user_ts ON turns (user_id, timestamp);
"""

_CREATE_CONVERSATIONS_SQL = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived_at TEXT
);
"""

_LEGACY_CONVERSATION_TITLE = "Previous conversation"


def _conversation_id(value: str) -> str:
    """Validate the opaque, canonical UUID used to address a conversation."""
    return str(uuid.UUID(value))


class HistoryStore:
    """Thread-safe SQLite-backed store for conversation turns.

    Args:
        db_path: Path to the SQLite database file.  Defaults to
            ``data/household/history.db``. Parent directories are created if they
            do not exist.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = Path(db_path) if db_path is not None else household_data_path("history.db")
        self._lock = threading.Lock()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as conn:
            conn.execute(_CREATE_TABLE_SQL)
            conn.execute(_CREATE_INDEX_SQL)
            conn.execute(_CREATE_CONVERSATIONS_SQL)
            # This is safe for existing installations: SQLite adds the nullable
            # column once, and legacy rows remain available through the old API.
            columns = {row[1] for row in conn.execute("PRAGMA table_info(turns)")}
            if "conversation_id" not in columns:
                conn.execute("ALTER TABLE turns ADD COLUMN conversation_id TEXT")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_turns_conversation_ts ON turns (user_id, conversation_id, id)"
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        """Commit or roll back a transaction, then always close its database handle."""
        conn = self._connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _migrate_legacy_turns(self, conn: sqlite3.Connection) -> None:
        """Expose pre-conversation history through one canonical thread per user.

        Earlier installations wrote turns without ``conversation_id``.  Moving
        those rows when the conversation list is first requested keeps the old
        non-conversation callers compatible while making the desktop's
        canonical list/open controls able to recover the persisted transcript.
        """
        legacy_users = conn.execute(
            """
            SELECT user_id, MIN(timestamp) AS created_at, MAX(timestamp) AS updated_at
            FROM turns
            WHERE conversation_id IS NULL
            GROUP BY user_id
            """
        ).fetchall()
        for row in legacy_users:
            conversation_id = str(uuid.uuid4())
            conn.execute(
                """
                INSERT INTO conversations (id, user_id, title, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    conversation_id,
                    row["user_id"],
                    _LEGACY_CONVERSATION_TITLE,
                    row["created_at"],
                    row["updated_at"],
                ),
            )
            conn.execute(
                "UPDATE turns SET conversation_id = ? WHERE user_id = ? AND conversation_id IS NULL",
                (conversation_id, row["user_id"]),
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def save_turn(
        self,
        user_id: str,
        role: str,
        content: str,
        timestamp: datetime,
        conversation_id: str | None = None,
    ) -> None:
        """Persist a single conversation turn.

        Args:
            user_id:   Identifier for the user/session.
            role:      ``"user"`` or ``"assistant"``.
            content:   Message text.
            timestamp: When the turn occurred.  Stored as UTC ISO-8601.
        """
        user_id = validate_user_id(user_id)
        if conversation_id is not None:
            conversation_id = _conversation_id(conversation_id)
        ts = timestamp.astimezone(UTC).isoformat()
        with self._lock:
            with self._connection() as conn:
                if conversation_id is not None:
                    conversation = conn.execute(
                        "SELECT 1 FROM conversations WHERE id = ? AND user_id = ? AND archived_at IS NULL",
                        (conversation_id, user_id),
                    ).fetchone()
                    if conversation is None:
                        raise KeyError("Conversation not found")
                conn.execute(
                    "INSERT INTO turns (user_id, role, content, timestamp, conversation_id) VALUES (?, ?, ?, ?, ?)",
                    (user_id, role, content, ts, conversation_id),
                )
                if conversation_id is not None:
                    conn.execute(
                        "UPDATE conversations SET updated_at = ? WHERE id = ? AND user_id = ?",
                        (ts, conversation_id, user_id),
                    )

    def load_history(self, user_id: str, limit: int = 50, conversation_id: str | None = None) -> list[dict]:
        """Return the most recent *limit* turns for *user_id*, oldest first.

        Args:
            user_id: Identifier for the user/session.
            limit:   Maximum number of turns to return (default: 50).

        Returns:
            List of dicts with keys ``id``, ``user_id``, ``role``,
            ``content``, ``timestamp``.
        """
        user_id = validate_user_id(user_id)
        if conversation_id is not None:
            conversation_id = _conversation_id(conversation_id)
        with self._lock:
            with self._connection() as conn:
                cursor = conn.execute(
                    """
                    SELECT id, user_id, role, content, timestamp
                    FROM (
                        SELECT id, user_id, role, content, timestamp
                        FROM turns
                        WHERE user_id = ? AND (conversation_id = ? OR (? IS NULL AND conversation_id IS NULL))
                        ORDER BY id DESC
                        LIMIT ?
                    ) sub
                    ORDER BY id ASC
                    """,
                    (user_id, conversation_id, conversation_id, limit),
                )
                return [dict(row) for row in cursor.fetchall()]

    def create_conversation(self, user_id: str, title: str = "New conversation") -> dict:
        user_id = validate_user_id(user_id)
        now = datetime.now(UTC).isoformat()
        conversation_id = str(uuid.uuid4())
        title = title.strip()[:120] or "New conversation"
        with self._lock:
            with self._connection() as conn:
                conn.execute(
                    "INSERT INTO conversations (id, user_id, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                    (conversation_id, user_id, title, now, now),
                )
        return {"id": conversation_id, "title": title, "created_at": now, "updated_at": now, "archived_at": None}

    def list_conversations(self, user_id: str, include_archived: bool = False) -> list[dict]:
        user_id = validate_user_id(user_id)
        archived_clause = "" if include_archived else "AND archived_at IS NULL"
        with self._lock:
            with self._connection() as conn:
                self._migrate_legacy_turns(conn)
                rows = conn.execute(
                    f"SELECT id, title, created_at, updated_at, archived_at FROM conversations WHERE user_id = ? {archived_clause} ORDER BY updated_at DESC, id DESC",
                    (user_id,),
                ).fetchall()
                return [dict(row) for row in rows]

    def rename_conversation(self, user_id: str, conversation_id: str, title: str) -> dict:
        user_id = validate_user_id(user_id)
        conversation_id = _conversation_id(conversation_id)
        title = title.strip()[:120]
        if not title:
            raise ValueError("Conversation title is required")
        now = datetime.now(UTC).isoformat()
        with self._lock:
            with self._connection() as conn:
                cursor = conn.execute("UPDATE conversations SET title = ?, updated_at = ? WHERE id = ? AND user_id = ? AND archived_at IS NULL", (title, now, conversation_id, user_id))
                if cursor.rowcount != 1:
                    raise KeyError("Conversation not found")
        return {"id": conversation_id, "title": title, "updated_at": now}

    def archive_conversation(self, user_id: str, conversation_id: str) -> None:
        user_id = validate_user_id(user_id)
        conversation_id = _conversation_id(conversation_id)
        now = datetime.now(UTC).isoformat()
        with self._lock:
            with self._connection() as conn:
                cursor = conn.execute("UPDATE conversations SET archived_at = ?, updated_at = ? WHERE id = ? AND user_id = ? AND archived_at IS NULL", (now, now, conversation_id, user_id))
                if cursor.rowcount != 1:
                    raise KeyError("Conversation not found")

    def clear_history(self, user_id: str) -> None:
        """Delete all conversation turns for *user_id*.

        Args:
            user_id: Identifier for the user/session whose history to clear.
        """
        user_id = validate_user_id(user_id)
        with self._lock:
            with self._connection() as conn:
                conn.execute("DELETE FROM turns WHERE user_id = ?", (user_id,))

    def prune(self, user_id: str, keep_days: int = 30) -> int:
        """Delete turns older than *keep_days* for *user_id*.

        Args:
            user_id:   Identifier for the user/session.
            keep_days: Turns older than this many days are deleted.

        Returns:
            Number of rows deleted.
        """
        from datetime import timedelta

        user_id = validate_user_id(user_id)
        cutoff = datetime.now(UTC) - timedelta(days=keep_days)
        cutoff_ts = cutoff.isoformat()
        with self._lock:
            with self._connection() as conn:
                cursor = conn.execute(
                    "DELETE FROM turns WHERE user_id = ? AND timestamp < ?",
                    (user_id, cutoff_ts),
                )
                return cursor.rowcount


__all__ = ["HistoryStore"]
