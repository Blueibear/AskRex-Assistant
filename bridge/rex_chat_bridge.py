"""Single-turn Rex chat bridge.

Reads a JSON payload from stdin: {"message": "<text>"}
Writes a JSON response to stdout: {"ok": true, "reply": "<text>"}
                               or {"ok": false, "error": "<text>"}

Used by the Electron GUI main process (src/main/handlers/chat.ts) to forward
one chat message to the Rex backend and return the response.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

from rex.bridge_utils import bridge_error_response


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read())
        message = str(payload.get("message", ""))
        user_id = str(payload.get("user") or "")
        conversation_id = payload.get("conversation_id")
        if payload.get("data_scope") != "private":
            raise PermissionError("Chat requires private Electron data scope")
        if conversation_id:
            from rex.history_store import HistoryStore  # type: ignore[import]
            from rex.identity import validate_user_id  # type: ignore[import]
            from rex.runtime_paths import household_data_path  # type: ignore[import]

            user_id = validate_user_id(user_id)
            if not any(item["id"] == str(conversation_id) for item in HistoryStore(household_data_path("history.db")).list_conversations(user_id)):
                raise PermissionError("Unknown private conversation")
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"Bad input: {exc}"}), flush=True)
        sys.exit(1)

    async def run() -> str:
        if os.environ.get("ASKREX_ARTIFACT_SMOKE") == "1":
            from rex.identity import validate_user_id  # type: ignore[import]

            validate_user_id(user_id)
            if message != "AskRex installed artifact smoke test":
                raise ValueError("Unexpected artifact smoke message")
            return "AskRex installed artifact chat verified"

        from rex import settings  # type: ignore[import]
        from rex.assistant import Assistant  # type: ignore[import]
        from rex.identity import validate_user_id  # type: ignore[import]
        from rex.logging_utils import configure_logging  # type: ignore[import]
        from rex.plugins import load_plugins, shutdown_plugins  # type: ignore[import]
        from rex.runtime.invocation import turn_invocation  # type: ignore[import]
        from rex.runtime.turn import TurnSource  # type: ignore[import]
        from rex.services import initialize_services  # type: ignore[import]

        configure_logging()
        initialize_services()
        plugin_specs = load_plugins()
        # Deliberate single-user profile selection (issue #303): Assistant no
        # longer invents an identity when user_id is omitted.
        assistant = Assistant(
            history_limit=settings.max_memory_items,
            plugins=plugin_specs,
            user_id=validate_user_id(user_id),
            conversation_id=str(conversation_id) if conversation_id else None,
        )
        try:
            with turn_invocation(TurnSource.ELECTRON):
                reply = await assistant.generate_reply(message)
            return str(reply)
        finally:
            shutdown_plugins(plugin_specs)

    try:
        reply = asyncio.run(run())
        print(json.dumps({"ok": True, "reply": reply}), flush=True)
    except Exception as exc:
        print(json.dumps(bridge_error_response(exc)), flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
