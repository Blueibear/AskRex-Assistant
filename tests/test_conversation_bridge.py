"""Focused tests for the private Electron canonical-conversation bridge."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from bridge.rex_conversation_bridge import process_payload
from rex.history_store import HistoryStore


def request(action: str, **extra: object) -> dict[str, object]:
    return {"action": action, "user": "alice", "data_scope": "private", **extra}


@pytest.fixture()
def store(tmp_path: Path) -> HistoryStore:
    return HistoryStore(tmp_path / "history.db")


def test_bridge_creates_lists_opens_renames_and_archives_canonical_conversation(
    store: HistoryStore,
) -> None:
    created = process_payload(request("create", title="Original"), store=store)["result"]
    assert isinstance(created, dict)
    conversation_id = created["id"]
    assert isinstance(conversation_id, str)

    store.save_turn("alice", "user", "Remember this", datetime.now(UTC), conversation_id=conversation_id)
    listed = process_payload(request("list"), store=store)["result"]
    assert isinstance(listed, list)
    assert [(item["id"], item["title"]) for item in listed] == [(conversation_id, "Original")]

    opened = process_payload(request("open", conversation_id=conversation_id), store=store)["result"]
    assert isinstance(opened, dict)
    assert opened["conversation"]["id"] == conversation_id
    assert [turn["content"] for turn in opened["messages"]] == ["Remember this"]

    renamed = process_payload(request("rename", conversation_id=conversation_id, title="Renamed"), store=store)["result"]
    assert isinstance(renamed, dict)
    assert renamed["title"] == "Renamed"

    assert process_payload(request("archive", conversation_id=conversation_id), store=store)["result"] == {"archived": True}
    assert process_payload(request("list"), store=store)["result"] == []


def test_bridge_does_not_disclose_or_open_another_users_conversation(store: HistoryStore) -> None:
    created = process_payload(request("create"), store=store)["result"]
    assert isinstance(created, dict)

    with pytest.raises(KeyError, match="Conversation not found"):
        process_payload(
            {"action": "open", "user": "bob", "data_scope": "private", "conversation_id": created["id"]},
            store=store,
        )


def test_bridge_lists_and_opens_legacy_history_after_upgrade(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy-history.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE turns (id INTEGER PRIMARY KEY, user_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, timestamp TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO turns (user_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
            ("alice", "user", "persisted before upgrade", datetime.now(UTC).isoformat()),
        )

    store = HistoryStore(db_path)
    listed = process_payload(request("list"), store=store)["result"]

    assert isinstance(listed, list)
    assert len(listed) == 1
    opened = process_payload(request("open", conversation_id=listed[0]["id"]), store=store)["result"]
    assert opened["messages"][0]["content"] == "persisted before upgrade"
