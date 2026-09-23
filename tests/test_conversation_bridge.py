"""Focused tests for the private Electron canonical-conversation bridge."""

from __future__ import annotations

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
