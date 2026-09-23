"""Tests for rex.history_store.HistoryStore."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from rex.history_store import HistoryStore


@pytest.fixture()
def store(tmp_path: Path) -> HistoryStore:
    """Create a fresh HistoryStore backed by a temp-dir DB."""
    return HistoryStore(db_path=tmp_path / "history.db")


def _ts(offset_hours: int = 0) -> datetime:
    """Return a UTC datetime offset by *offset_hours* from now."""
    return datetime.now(UTC) + timedelta(hours=offset_hours)


# ---------------------------------------------------------------------------
# Schema creation
# ---------------------------------------------------------------------------


def test_db_file_created(tmp_path: Path) -> None:
    db = tmp_path / "history.db"
    assert not db.exists()
    HistoryStore(db_path=db)
    assert db.exists()


def test_nested_parent_dirs_created(tmp_path: Path) -> None:
    db = tmp_path / "a" / "b" / "history.db"
    HistoryStore(db_path=db)
    assert db.exists()


# ---------------------------------------------------------------------------
# save_turn / load_history
# ---------------------------------------------------------------------------


def test_save_and_load_single_turn(store: HistoryStore) -> None:
    store.save_turn("alice", "user", "Hello", _ts())
    history = store.load_history("alice")
    assert len(history) == 1
    row = history[0]
    assert row["user_id"] == "alice"
    assert row["role"] == "user"
    assert row["content"] == "Hello"
    assert "timestamp" in row
    assert "id" in row


def test_load_returns_oldest_first(store: HistoryStore) -> None:
    store.save_turn("alice", "user", "first", _ts(-2))
    store.save_turn("alice", "assistant", "second", _ts(-1))
    store.save_turn("alice", "user", "third", _ts(0))
    history = store.load_history("alice")
    assert [r["content"] for r in history] == ["first", "second", "third"]


def test_load_limit_respected(store: HistoryStore) -> None:
    for i in range(10):
        store.save_turn("alice", "user", f"msg {i}", _ts(-10 + i))
    history = store.load_history("alice", limit=3)
    # Should return the 3 most recent, oldest first
    assert len(history) == 3
    assert history[-1]["content"] == "msg 9"


def test_load_empty_for_unknown_user(store: HistoryStore) -> None:
    history = store.load_history("nobody")
    assert history == []


def test_turns_isolated_per_user(store: HistoryStore) -> None:
    store.save_turn("alice", "user", "Hi Alice", _ts())
    store.save_turn("bob", "user", "Hi Bob", _ts())
    assert len(store.load_history("alice")) == 1
    assert store.load_history("alice")[0]["content"] == "Hi Alice"
    assert len(store.load_history("bob")) == 1
    assert store.load_history("bob")[0]["content"] == "Hi Bob"


def test_timestamp_stored_as_utc_iso(store: HistoryStore) -> None:
    ts = datetime(2026, 1, 15, 10, 0, 0, tzinfo=UTC)
    store.save_turn("alice", "user", "hello", ts)
    row = store.load_history("alice")[0]
    assert "2026-01-15" in row["timestamp"]


# ---------------------------------------------------------------------------
# prune
# ---------------------------------------------------------------------------


def test_prune_removes_old_turns(store: HistoryStore) -> None:
    old_ts = datetime.now(UTC) - timedelta(days=31)
    recent_ts = datetime.now(UTC) - timedelta(days=1)
    store.save_turn("alice", "user", "old message", old_ts)
    store.save_turn("alice", "user", "recent message", recent_ts)
    deleted = store.prune("alice", keep_days=30)
    assert deleted == 1
    history = store.load_history("alice")
    assert len(history) == 1
    assert history[0]["content"] == "recent message"


def test_prune_returns_zero_when_nothing_to_delete(store: HistoryStore) -> None:
    store.save_turn("alice", "user", "fresh", _ts())
    deleted = store.prune("alice", keep_days=30)
    assert deleted == 0
    assert len(store.load_history("alice")) == 1


def test_prune_is_idempotent(store: HistoryStore) -> None:
    old_ts = datetime.now(UTC) - timedelta(days=40)
    store.save_turn("alice", "user", "old", old_ts)
    first = store.prune("alice", keep_days=30)
    second = store.prune("alice", keep_days=30)
    assert first == 1
    assert second == 0


def test_prune_only_affects_specified_user(store: HistoryStore) -> None:
    old_ts = datetime.now(UTC) - timedelta(days=40)
    store.save_turn("alice", "user", "old", old_ts)
    store.save_turn("bob", "user", "old too", old_ts)
    deleted = store.prune("alice", keep_days=30)
    assert deleted == 1
    # Bob's old turn should still be there
    assert len(store.load_history("bob")) == 1


def test_prune_empty_user_returns_zero(store: HistoryStore) -> None:
    assert store.prune("nobody", keep_days=30) == 0


def test_canonical_conversations_are_persisted_and_isolated(store: HistoryStore) -> None:
    first = store.create_conversation("alice", "First")
    second = store.create_conversation("alice", "Second")
    store.save_turn("alice", "user", "first message", _ts(), conversation_id=first["id"])
    store.save_turn("alice", "user", "second message", _ts(), conversation_id=second["id"])

    assert {row["title"] for row in store.list_conversations("alice")} == {"First", "Second"}
    assert [row["content"] for row in store.load_history("alice", conversation_id=first["id"])] == ["first message"]
    assert [row["content"] for row in store.load_history("alice", conversation_id=second["id"])] == ["second message"]
    assert store.list_conversations("bob") == []


def test_conversation_rename_and_archive_hide_only_that_conversation(store: HistoryStore) -> None:
    first = store.create_conversation("alice", "First")
    second = store.create_conversation("alice", "Second")

    renamed = store.rename_conversation("alice", first["id"], "Renamed")
    store.archive_conversation("alice", second["id"])

    assert renamed["title"] == "Renamed"
    assert [row["id"] for row in store.list_conversations("alice")] == [first["id"]]
    assert {row["id"] for row in store.list_conversations("alice", include_archived=True)} == {first["id"], second["id"]}
