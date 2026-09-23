"""Private Electron bridge for canonical conversation metadata and history."""

from __future__ import annotations

import json
import sys

from rex.history_store import HistoryStore
from rex.identity import validate_user_id
from rex.runtime_paths import household_data_path


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read())
        if payload.get("data_scope") != "private":
            raise PermissionError("Conversations require private Electron data scope")
        user_id = validate_user_id(str(payload.get("user") or ""))
        action = str(payload.get("action") or "")
        store = HistoryStore(household_data_path("history.db"))
        if action == "create":
            result = store.create_conversation(user_id, str(payload.get("title") or "New conversation"))
        elif action == "list":
            result = store.list_conversations(user_id)
        elif action == "open":
            conversation_id = str(payload["conversation_id"])
            # Listing first is the owner-scoped existence check; do not disclose
            # another user's UUID-addressed conversation.
            if not any(item["id"] == conversation_id for item in store.list_conversations(user_id)):
                raise KeyError("Conversation not found")
            result = {"conversation": next(item for item in store.list_conversations(user_id) if item["id"] == conversation_id), "messages": store.load_history(user_id, conversation_id=conversation_id)}
        elif action == "rename":
            result = store.rename_conversation(user_id, str(payload["conversation_id"]), str(payload.get("title") or ""))
        elif action == "archive":
            store.archive_conversation(user_id, str(payload["conversation_id"]))
            result = {"archived": True}
        else:
            raise ValueError("Unknown conversation action")
        print(json.dumps({"ok": True, "result": result}), flush=True)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
