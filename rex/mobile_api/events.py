"""Canonical mobile streaming events (issue #323, Session 2).

One builder per canonical event type, shared by the SSE and WebSocket
transports so the wire grammar cannot drift between them.  Every wire field
is ``snake_case`` and every payload is valid JSON — malformed internal data
is never emitted as assistant prose.

Truthfulness: events are only emitted from real runtime boundaries.  The
current Assistant streaming surface exposes text tokens and a terminal
completion; ``tool_call`` / ``tool_result`` / ``approval_required`` builders
exist for the documented contract but the backend emits them only when the
corresponding real action boundary produces them — never by parsing
generated prose.
"""

from __future__ import annotations

import json
from typing import Any

from rex.runtime.status import TurnStatusUpdate

# Canonical event type names (wire contract).
EVENT_AUTH_OK = "auth_ok"
EVENT_AUTH_ERROR = "auth_error"
EVENT_ACK = "ack"
EVENT_TOKEN = "token"
EVENT_STATUS = "status"
EVENT_TOOL_CALL = "tool_call"
EVENT_TOOL_RESULT = "tool_result"
EVENT_APPROVAL_REQUIRED = "approval_required"
EVENT_MESSAGE_DONE = "message_done"
EVENT_ERROR = "error"
EVENT_PING = "ping"
EVENT_PONG = "pong"


def token_event(message_id: str, content: str) -> dict[str, Any]:
    return {"type": EVENT_TOKEN, "message_id": message_id, "content": content}


def status_event(message_id: str, update: TurnStatusUpdate) -> dict[str, Any]:
    return {"type": EVENT_STATUS, "message_id": message_id, **update.to_dict()}


def message_done_event(
    message_id: str,
    conversation_id: str,
    full_content: str,
    status: str = "completed",
    attachments: list[dict[str, object]] | None = None,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "type": EVENT_MESSAGE_DONE,
        "message_id": message_id,
        "conversation_id": conversation_id,
        "full_content": full_content,
        "status": status,
    }
    # Terminal frames mirror the HTTP terminal payload.  These are safe
    # provenance records only: attachment bytes and storage paths never cross
    # a chat transport boundary.
    if attachments is not None:
        event["attachments"] = attachments
    return event


def error_event(
    code: str,
    message: str,
    *,
    message_id: str | None = None,
    retryable: bool = False,
    request_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "type": EVENT_ERROR,
        "code": code,
        "message": message,
        "retryable": retryable,
        "request_id": request_id,
    }
    if message_id is not None:
        event["message_id"] = message_id
    if details is not None:
        event["details"] = details
    return event


def ack_event(message_id: str, accepted_at: str) -> dict[str, Any]:
    return {"type": EVENT_ACK, "message_id": message_id, "accepted_at": accepted_at}


def auth_ok_event(session_id: str, user: dict[str, Any]) -> dict[str, Any]:
    return {"type": EVENT_AUTH_OK, "session_id": session_id, "user": user}


def auth_error_event(code: str, message: str) -> dict[str, Any]:
    return {"type": EVENT_AUTH_ERROR, "code": code, "message": message}


def pong_event(sent_at: str) -> dict[str, Any]:
    return {"type": EVENT_PONG, "sent_at": sent_at}


def encode_event(event: dict[str, Any]) -> str:
    """Serialize an event to canonical JSON (one line, no NaN)."""
    return json.dumps(event, ensure_ascii=True, allow_nan=False, separators=(",", ":"))


def format_sse(event: dict[str, Any]) -> str:
    """Format an event as one Server-Sent Events ``data:`` frame."""
    return f"data: {encode_event(event)}\n\n"


__all__ = [
    "EVENT_ACK",
    "EVENT_APPROVAL_REQUIRED",
    "EVENT_AUTH_ERROR",
    "EVENT_AUTH_OK",
    "EVENT_ERROR",
    "EVENT_MESSAGE_DONE",
    "EVENT_PING",
    "EVENT_PONG",
    "EVENT_STATUS",
    "EVENT_TOKEN",
    "EVENT_TOOL_CALL",
    "EVENT_TOOL_RESULT",
    "ack_event",
    "auth_error_event",
    "auth_ok_event",
    "encode_event",
    "error_event",
    "format_sse",
    "message_done_event",
    "pong_event",
    "status_event",
    "token_event",
]
