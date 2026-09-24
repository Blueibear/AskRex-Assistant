"""Strict request payload validation helpers for mobile routes.

All external payloads are untrusted.  JSON is parsed strictly enough to
distinguish malformed JSON (400) from a wrong content type (415) and from an
empty object.  Client-supplied identity/authorization fields are never
accepted anywhere.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from rex.mobile_api import errors as merr
from rex.mobile_api.errors import MobileApiError
from rex.mobile_api.sessions import DeviceInfo

_DEVICE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_MAX_DEVICE_TEXT_LENGTH = 128
_MAX_APP_VERSION_LENGTH = 64
_WS_CLIENT_PLATFORMS = {"ios", "android", "web"}
_MAX_TOKEN_LENGTH = 512
_MAX_CREDENTIAL_LENGTH = 512

# Chat payload bounds (issue #323 Session 2).
MAX_CHAT_MESSAGE_CHARS = 8_000
_MAX_CLIENT_CONTEXT_KEYS = 16
_MAX_CLIENT_CONTEXT_VALUE_CHARS = 256
_CHAT_MODE = "mobile_text"

# The only fields a chat request may carry.  Client-supplied identity or
# authorization fields (user_id, role, permissions, risk, approval,
# biometric, ...) are rejected outright — identity comes exclusively from
# validated credentials.
_ALLOWED_CHAT_FIELDS = {
    "type",
    "message_id",
    "conversation_id",
    "sent_at",
    "message",
    "mode",
    "client_context",
    "strong_auth_approval_id",
    "attachment_ids",
}


def parse_json_body() -> dict[str, Any]:
    """Return the request's JSON object, failing closed on bad input.

    Raises:
        MobileApiError: 415 ``INVALID_MEDIA`` for a non-JSON content type,
            400 ``BAD_REQUEST`` for malformed JSON or a non-object payload,
            413 ``PAYLOAD_TOO_LARGE`` beyond the configured JSON body limit.
    """
    from flask import current_app, request  # noqa: PLC0415

    # The Flask-level MAX_CONTENT_LENGTH admits large multipart voice
    # uploads; JSON bodies keep the tighter configured limit, checked
    # before any parsing work.
    services = current_app.extensions.get("mobile_api_services")
    if services is not None and request.content_length is not None:
        if request.content_length > services.config.max_json_bytes:
            raise MobileApiError(merr.PAYLOAD_TOO_LARGE, "Request body is too large.", 413)

    if request.mimetype != "application/json":
        raise MobileApiError(
            merr.INVALID_MEDIA,
            "Content-Type must be application/json.",
            415,
        )
    try:
        payload = request.get_json(force=False, silent=False)
    except Exception as exc:
        raise MobileApiError(merr.BAD_REQUEST, "Request body is not valid JSON.", 400) from exc
    if not isinstance(payload, dict):
        raise MobileApiError(merr.BAD_REQUEST, "Request body must be a JSON object.", 400)
    return payload


def require_string_field(
    payload: dict[str, Any],
    name: str,
    *,
    max_length: int = _MAX_CREDENTIAL_LENGTH,
) -> str:
    """Return a required non-empty string field, or raise 400."""
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise MobileApiError(merr.BAD_REQUEST, f"Field '{name}' is required.", 400)
    if len(value) > max_length:
        raise MobileApiError(merr.BAD_REQUEST, f"Field '{name}' is too long.", 400)
    return value


def parse_refresh_token_field(payload: dict[str, Any]) -> str:
    """Return the ``refresh_token`` field without echoing its value anywhere."""
    value = payload.get("refresh_token")
    if not isinstance(value, str) or not value.strip() or len(value) > _MAX_TOKEN_LENGTH:
        raise MobileApiError(merr.BAD_REQUEST, "Field 'refresh_token' is required.", 400)
    return value.strip()


def parse_device_info(payload: dict[str, Any]) -> DeviceInfo:
    """Validate optional login device metadata.

    ``device_id`` is an app-generated stable random identifier — validated
    for length/format but never trusted as identity.  A missing device object
    gets a server-generated random device ID.

    Raises:
        MobileApiError: 400 when the device object or device_id is invalid.
    """
    device = payload.get("device")
    if device is None:
        return DeviceInfo(device_id=str(uuid.uuid4()))
    if not isinstance(device, dict):
        raise MobileApiError(merr.BAD_REQUEST, "Field 'device' must be an object.", 400)

    device_id = device.get("device_id")
    if device_id is None:
        device_id = str(uuid.uuid4())
    elif not isinstance(device_id, str) or not _DEVICE_ID_PATTERN.fullmatch(device_id):
        raise MobileApiError(merr.BAD_REQUEST, "Field 'device.device_id' is invalid.", 400)

    def _text(name: str) -> str:
        value = device.get(name, "")
        if value is None:
            return ""
        if not isinstance(value, str):
            raise MobileApiError(merr.BAD_REQUEST, f"Field 'device.{name}' must be a string.", 400)
        return value.strip()[:_MAX_DEVICE_TEXT_LENGTH]

    return DeviceInfo(
        device_id=device_id,
        name=_text("name"),
        platform=_text("platform"),
        app_version=_text("app_version"),
    )


def parse_websocket_client(client: Any) -> dict[str, str]:
    """Validate required WebSocket client metadata without trusting it as identity."""
    if not isinstance(client, dict):
        raise MobileApiError(merr.BAD_REQUEST, "Field 'client' must be an object.", 400)
    allowed = {"platform", "app_version", "device_id"}
    unknown = set(client) - allowed
    if unknown:
        names = ", ".join(sorted(unknown))
        raise MobileApiError(merr.BAD_REQUEST, f"Unsupported client field(s): {names}.", 400)
    if set(client) != allowed:
        raise MobileApiError(
            merr.BAD_REQUEST,
            "Fields 'client.platform', 'client.app_version', and 'client.device_id' are required.",
            400,
        )
    platform = client["platform"]
    if not isinstance(platform, str) or platform not in _WS_CLIENT_PLATFORMS:
        raise MobileApiError(merr.BAD_REQUEST, "Field 'client.platform' is invalid.", 400)
    app_version = client["app_version"]
    if (
        not isinstance(app_version, str)
        or not app_version.strip()
        or len(app_version) > _MAX_APP_VERSION_LENGTH
    ):
        raise MobileApiError(merr.BAD_REQUEST, "Field 'client.app_version' is invalid.", 400)
    device_id = client["device_id"]
    if not isinstance(device_id, str) or not _DEVICE_ID_PATTERN.fullmatch(device_id):
        raise MobileApiError(merr.BAD_REQUEST, "Field 'client.device_id' is invalid.", 400)
    return {
        "platform": platform,
        "app_version": app_version.strip(),
        "device_id": device_id,
    }


@dataclass(frozen=True)
class ChatRequest:
    """A validated mobile chat request (HTTP body or WebSocket frame)."""

    message_id: str
    conversation_id: str
    sent_at: str
    message: str
    mode: str = _CHAT_MODE
    client_context: dict[str, str] = field(default_factory=dict)
    strong_auth_approval_id: str | None = None
    attachment_ids: tuple[str, ...] = ()

    def semantic_fields(self) -> dict[str, Any]:
        """Return the semantic execution fields used for the request hash."""
        return {
            "message_id": self.message_id,
            "conversation_id": self.conversation_id,
            "sent_at": self.sent_at,
            "message": self.message,
            "mode": self.mode,
            "client_context": self.client_context,
            "strong_auth_approval_id": self.strong_auth_approval_id,
            "attachment_ids": list(self.attachment_ids),
        }


def _require_uuid_field(payload: dict[str, Any], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise MobileApiError(merr.BAD_REQUEST, f"Field '{name}' is required.", 400)
    value = value.strip()
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError, TypeError) as exc:
        raise MobileApiError(merr.BAD_REQUEST, f"Field '{name}' must be a UUID.", 400) from exc
    return value


def _require_iso_timestamp(payload: dict[str, Any], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise MobileApiError(merr.BAD_REQUEST, f"Field '{name}' is required.", 400)
    value = value.strip()
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MobileApiError(
            merr.BAD_REQUEST, f"Field '{name}' must be an ISO-8601 timestamp.", 400
        ) from exc
    return value


def _parse_client_context(payload: dict[str, Any]) -> dict[str, str]:
    context = payload.get("client_context")
    if context is None:
        return {}
    if not isinstance(context, dict):
        raise MobileApiError(merr.BAD_REQUEST, "Field 'client_context' must be an object.", 400)
    if len(context) > _MAX_CLIENT_CONTEXT_KEYS:
        raise MobileApiError(merr.BAD_REQUEST, "Field 'client_context' has too many keys.", 400)
    parsed: dict[str, str] = {}
    for key, value in context.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise MobileApiError(
                merr.BAD_REQUEST, "Field 'client_context' must map strings to strings.", 400
            )
        if len(value) > _MAX_CLIENT_CONTEXT_VALUE_CHARS:
            raise MobileApiError(
                merr.BAD_REQUEST, "Field 'client_context' contains an oversized value.", 400
            )
        parsed[key] = value
    return parsed


def parse_chat_payload(payload: dict[str, Any]) -> ChatRequest:
    """Validate a chat request payload (HTTP body or WebSocket chat frame).

    Rejects unknown fields outright, which covers every client-supplied
    identity/authorization field (``user_id``, ``role``, ``permissions``,
    risk, approval, and biometric claims) — the server principal is the only
    identity.

    Raises:
        MobileApiError: 400 ``BAD_REQUEST`` on any invalid field.
    """
    unknown = set(payload) - _ALLOWED_CHAT_FIELDS
    if unknown:
        names = ", ".join(sorted(unknown))
        raise MobileApiError(merr.BAD_REQUEST, f"Unsupported field(s): {names}.", 400)

    message = payload.get("message")
    if not isinstance(message, str) or not message.strip():
        raise MobileApiError(merr.BAD_REQUEST, "Field 'message' is required.", 400)
    if len(message) > MAX_CHAT_MESSAGE_CHARS:
        raise MobileApiError(merr.BAD_REQUEST, "Field 'message' is too long.", 400)

    mode = payload.get("mode", _CHAT_MODE)
    if mode != _CHAT_MODE:
        raise MobileApiError(merr.BAD_REQUEST, f"Field 'mode' must be '{_CHAT_MODE}'.", 400)

    approval_id = payload.get("strong_auth_approval_id")
    if approval_id is not None:
        if not isinstance(approval_id, str) or not approval_id.strip() or len(approval_id) > 128:
            raise MobileApiError(
                merr.BAD_REQUEST,
                "Field 'strong_auth_approval_id' is invalid.",
                400,
            )
        approval_id = approval_id.strip()

    attachment_ids_raw = payload.get("attachment_ids", [])
    if not isinstance(attachment_ids_raw, list) or len(attachment_ids_raw) > 5:
        raise MobileApiError(merr.BAD_REQUEST, "Field 'attachment_ids' is invalid.", 400)
    attachment_ids: list[str] = []
    for value in attachment_ids_raw:
        if not isinstance(value, str):
            raise MobileApiError(merr.BAD_REQUEST, "Field 'attachment_ids' is invalid.", 400)
        try:
            attachment_id = str(uuid.UUID(value))
        except (ValueError, TypeError, AttributeError) as exc:
            raise MobileApiError(merr.BAD_REQUEST, "Field 'attachment_ids' is invalid.", 400) from exc
        if attachment_id in attachment_ids:
            raise MobileApiError(merr.BAD_REQUEST, "Field 'attachment_ids' contains duplicates.", 400)
        attachment_ids.append(attachment_id)

    return ChatRequest(
        message_id=_require_uuid_field(payload, "message_id"),
        conversation_id=_require_uuid_field(payload, "conversation_id"),
        sent_at=_require_iso_timestamp(payload, "sent_at"),
        message=message.strip(),
        mode=mode,
        client_context=_parse_client_context(payload),
        strong_auth_approval_id=approval_id,
        attachment_ids=tuple(attachment_ids),
    )


__all__ = [
    "MAX_CHAT_MESSAGE_CHARS",
    "ChatRequest",
    "parse_chat_payload",
    "parse_device_info",
    "parse_json_body",
    "parse_websocket_client",
    "parse_refresh_token_field",
    "require_string_field",
]
