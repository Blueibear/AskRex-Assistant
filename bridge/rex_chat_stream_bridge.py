"""Streaming Rex chat bridge.

Reads JSON from stdin:  {"message": "<text>"}
Writes NDJSON to stdout, one JSON object per line:
  {"type": "token", "token": "<text>"}   – a chunk of the LLM response
  {"type": "status", "status": "<kind>"} – non-terminal response status
  {"type": "done"}                        – stream complete (last line)
  {"type": "error", "error": "<text>"}   – error; process exits 1

Falls back to non-streaming (emits the full reply as a single "token" line)
when the backend's Assistant class does not expose generate_reply_stream.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import traceback
from collections.abc import Mapping


def emit(obj: dict) -> None:  # noqa: ANN001
    print(json.dumps(obj), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Streaming Rex chat bridge. "
            'Reads JSON from stdin: {"message": "<text>"}. '
            'Writes NDJSON to stdout: {"type": "token", "token": "..."} per chunk, '
            'then {"type": "done"} when complete, '
            'or {"type": "error", "error": "..."} on failure.'
        )
    )
    parser.parse_args()

    try:
        payload = json.loads(sys.stdin.read())
        message = str(payload.get("message", ""))
        user_id = str(payload.get("user") or "")
        conversation_id = payload.get("conversation_id")
        if payload.get("data_scope") != "private":
            raise PermissionError("Chat requires private Electron data scope")
        if conversation_id:
            from rex.history_store import HistoryStore
            from rex.identity import validate_user_id
            from rex.runtime_paths import household_data_path

            user_id = validate_user_id(user_id)
            if not any(item["id"] == str(conversation_id) for item in HistoryStore(household_data_path("history.db")).list_conversations(user_id)):
                raise PermissionError("Unknown private conversation")
    except Exception as exc:
        emit({"type": "error", "error": f"Bad input: {exc}"})
        sys.exit(1)

    async def run() -> None:
        from rex import settings
        from rex.assistant import Assistant
        from rex.identity import validate_user_id
        from rex.logging_utils import configure_logging
        from rex.plugins import load_plugins, shutdown_plugins
        from rex.runtime.events import EventKind
        from rex.runtime.invocation import turn_invocation
        from rex.runtime.status import TurnStatusProjector
        from rex.runtime.turn import TurnSource
        from rex.services import initialize_services

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

        def emit_turn_status(update) -> None:  # noqa: ANN001
            emit({"type": "status", **update.to_dict()})

        status_projector = TurnStatusProjector(emit_turn_status)

        def observe_turn(event) -> None:  # noqa: ANN001
            status_projector.observe(event)
            if (
                event.kind is EventKind.CAPABILITY_PROGRESS
                and event.details.get("stage") == "recovery"
                and isinstance(event.details.get("recovery"), Mapping)
            ):
                emit({"type": "recovery", "recovery": dict(event.details["recovery"])})
            if (
                event.kind is EventKind.RESPONSE_PROGRESS
                and event.details.get("stage") == "output_validation"
                and event.details.get("status") == "model_failure"
            ):
                emit({"type": "status", "status": "model_failure"})

        try:
            # Electron owns transport and provenance only; Assistant owns the brain.
            with turn_invocation(TurnSource.ELECTRON):
                stream_fn = getattr(assistant, "stream_reply", None) or getattr(
                    assistant, "generate_reply_stream", None
                )
                if stream_fn is not None:
                    async for token in stream_fn(message, event_observer=observe_turn):
                        emit({"type": "token", "token": str(token)})
                else:
                    reply = await assistant.generate_reply(message, event_observer=observe_turn)
                    emit({"type": "token", "token": str(reply)})
        finally:
            shutdown_plugins(plugin_specs)

    try:
        asyncio.run(run())
        emit({"type": "done"})
    except Exception as exc:
        emit({"type": "error", "error": str(exc), "traceback": traceback.format_exc()})
        sys.exit(1)


if __name__ == "__main__":
    main()
