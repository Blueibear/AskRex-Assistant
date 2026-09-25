"""Authenticated WebSocket chat transport (issue #323, Session 2).

``WebSocket /mobile/chat/stream`` served through Flask-Sock /
simple-websocket (validated on Windows + Python 3.11) inside the same Flask
runtime — no second API framework.

Protocol (master spec §7):

- The URL never carries a token.  The first frame must be
  ``{"type": "auth", "access_token": ..., "client": {...}}`` within the
  authentication timeout; nothing else is processed before authentication.
- Token validation is byte-for-byte the same service as HTTP
  (:func:`rex.mobile_api.auth.authenticate_token`); success binds an
  immutable principal to the connection — later frames can never replace
  identity.
- ``auth_ok`` / ``auth_error`` frames; close codes ``4401`` (missing,
  invalid, expired, or revoked authentication), ``4408`` (auth timeout),
  ``4429`` (connection/message rate limits).  ``4403`` is RESERVED for a
  future authenticated authorization denial: the canonical permission model
  (``rex.permissions``) gates tools at dispatch time, not chat access, so
  Session 2 has no real authenticated-but-forbidden condition and no code
  path emits 4403.  Clients must still handle it defensively.
- Chat frames validate the canonical schema, reserve the shared
  ``(user_id, message_id)`` idempotency record, then ``ack`` with
  ``message_id``/``accepted_at`` — reservation strictly precedes the ack.
- Session status is revalidated before every chat frame and on a bounded
  idle interval; a revoked session closes with 4401.
- Malformed frames produce structured protocol errors, never assistant
  text.  Frames, tokens, and message bodies are never logged.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from importlib.util import find_spec
from typing import Any

from rex.mobile_api import errors as merr
from rex.mobile_api import events as mev
from rex.mobile_api import idempotency as idem
from rex.mobile_api.auth import MobilePrincipal, authenticate_token, revalidate_principal
from rex.mobile_api.authorization import ROUTE_SCOPES
from rex.mobile_api.chat import STATUS_COMPLETED
from rex.mobile_api.errors import MobileApiError
from rex.mobile_api.services import MobileApiServices
from rex.mobile_api.validation import parse_chat_payload, parse_websocket_client

logger = logging.getLogger(__name__)

# Close codes (master spec §7.1).
CLOSE_UNAUTHENTICATED = 4401
# RESERVED: no Session 2 code path closes with 4403.  Chat access is not
# gated by rex.permissions (permissions authorize tools at dispatch), so an
# authenticated principal is never "forbidden" from chat today.  The code
# stays in the wire contract for future server-side authorization denial.
CLOSE_FORBIDDEN = 4403
CLOSE_AUTH_TIMEOUT = 4408
CLOSE_RATE_LIMITED = 4429

AUTH_TIMEOUT_SECONDS = 10.0
IDLE_POLL_SECONDS = 5.0
SESSION_RECHECK_SECONDS = 5.0
MAX_FRAME_BYTES = 64 * 1024
CONNECTIONS_PER_MINUTE = 30

# Stored when the socket drops mid-execution (mirrors the SSE transport).
CLIENT_DISCONNECTED = idem.CLIENT_DISCONNECTED


class SlidingWindowLimiter:
    """Small thread-safe sliding-window rate limiter (per arbitrary key)."""

    def __init__(self, limit: int, window_seconds: float = 60.0) -> None:
        self._limit = limit
        self._window = window_seconds
        self._events: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, now: float | None = None) -> bool:
        stamp = time.monotonic() if now is None else now
        cutoff = stamp - self._window
        with self._lock:
            events = [t for t in self._events.get(key, []) if t > cutoff]
            if len(events) >= self._limit:
                self._events[key] = events
                return False
            events.append(stamp)
            self._events[key] = events
            return True


def _messages_per_minute(rate_limit: str) -> int:
    """Parse the leading integer of a Flask-Limiter rate string."""
    try:
        return max(1, int(rate_limit.strip().split()[0]))
    except (ValueError, IndexError):
        return 30


class MobileWebSocketServer:
    """Per-app WebSocket protocol handler (connection state machine)."""

    def __init__(self, services: MobileApiServices) -> None:
        self._services = services
        self._connection_limiter = SlidingWindowLimiter(CONNECTIONS_PER_MINUTE)
        self._message_limit = _messages_per_minute(services.config.rate_limit_chat)

    # ── Frame helpers (no bodies or tokens are ever logged) ─────────────

    @staticmethod
    def _send(ws: Any, event: dict[str, Any]) -> None:
        ws.send(mev.encode_event(event))

    @staticmethod
    def _close(ws: Any, code: int, reason: str) -> None:
        try:
            ws.close(code, reason)
        except Exception:  # pragma: no cover - already closed
            logger.debug("WebSocket close failed", exc_info=True)

    def _protocol_error(
        self, ws: Any, code: str, message: str, *, message_id: str | None = None
    ) -> None:
        self._send(ws, mev.error_event(code, message, message_id=message_id))

    # ── Authentication ───────────────────────────────────────────────────

    def _authenticate(self, ws: Any) -> MobilePrincipal | None:
        """Run the first-frame auth handshake; None means the socket closed."""
        try:
            raw = ws.receive(timeout=AUTH_TIMEOUT_SECONDS)
        except Exception:
            return None
        if raw is None:
            self._close(ws, CLOSE_AUTH_TIMEOUT, "Authentication timeout")
            return None
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8", errors="replace")
        if len(raw) > MAX_FRAME_BYTES:
            self._close(ws, CLOSE_UNAUTHENTICATED, "Authentication frame too large")
            return None
        try:
            frame = json.loads(raw)
        except ValueError:
            frame = None
        if not isinstance(frame, dict) or frame.get("type") != "auth":
            # Chat/ping/malformed before authentication: reject and close.
            self._send(
                ws,
                mev.auth_error_event(
                    merr.AUTH_TOKEN_INVALID, "The first frame must be an auth frame."
                ),
            )
            self._close(ws, CLOSE_UNAUTHENTICATED, "Not authenticated")
            return None

        token = frame.get("access_token")
        if (
            not isinstance(token, str)
            or not token.strip()
            or set(frame)
            != {
                "type",
                "access_token",
                "client",
            }
        ):
            self._send(
                ws,
                mev.auth_error_event(merr.AUTH_TOKEN_INVALID, "Invalid auth frame."),
            )
            self._close(ws, CLOSE_UNAUTHENTICATED, "Not authenticated")
            return None
        try:
            parse_websocket_client(frame.get("client"))
        except MobileApiError as exc:
            self._send(ws, mev.auth_error_event(exc.code, exc.message))
            self._close(ws, CLOSE_UNAUTHENTICATED, "Not authenticated")
            return None

        try:
            principal = authenticate_token(self._services, token.strip())
        except MobileApiError as exc:
            self._send(ws, mev.auth_error_event(exc.code, exc.message))
            self._close(ws, CLOSE_UNAUTHENTICATED, "Not authenticated")
            return None
        if ROUTE_SCOPES["chat.websocket"] not in principal.scopes:
            self._send(
                ws,
                mev.auth_error_event(
                    merr.FORBIDDEN,
                    "This paired device is not authorized for chat.",
                ),
            )
            self._close(ws, CLOSE_FORBIDDEN, "Capability not granted")
            return None

        from rex.mobile_api import users as musers  # noqa: PLC0415

        projection = musers.build_user_projection(
            self._services.db_path, principal.user_id, principal.username
        )
        self._send(ws, mev.auth_ok_event(principal.session_id, projection))
        return principal

    def _session_active(self, principal: MobilePrincipal) -> bool:
        try:
            revalidate_principal(
                self._services,
                principal,
                required_scope=ROUTE_SCOPES["chat.websocket"],
            )
            return True
        except MobileApiError:
            return False

    # ── Connection loop ─────────────────────────────────────────────────

    def handle(self, ws: Any, remote_addr: str) -> None:
        if not self._connection_limiter.allow(remote_addr or "unknown"):
            self._close(ws, CLOSE_RATE_LIMITED, "Connection rate limited")
            return

        principal = self._authenticate(ws)
        if principal is None:
            return

        message_limiter = SlidingWindowLimiter(self._message_limit)
        last_session_check = time.monotonic()
        logger.info("Mobile WebSocket authenticated (session=%s)", principal.session_id)

        while True:
            try:
                raw = ws.receive(timeout=IDLE_POLL_SECONDS)
            except Exception:
                return
            now = time.monotonic()
            if now - last_session_check >= SESSION_RECHECK_SECONDS:
                if not self._require_active_session(ws, principal):
                    return
                last_session_check = now
            frame = self._parse_authenticated_frame(ws, raw)
            if frame is None:
                continue
            keep_open, checked_session = self._dispatch_authenticated_frame(
                ws, principal, frame, message_limiter
            )
            if not keep_open:
                return
            if checked_session:
                last_session_check = time.monotonic()

    def _require_active_session(self, ws: Any, principal: MobilePrincipal) -> bool:
        if self._session_active(principal):
            return True
        self._close(ws, CLOSE_UNAUTHENTICATED, "Session revoked")
        return False

    def _parse_authenticated_frame(self, ws: Any, raw: Any) -> dict[str, Any] | None:
        if raw is None:
            return None
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8", errors="replace")
        if len(raw) > MAX_FRAME_BYTES:
            self._protocol_error(ws, merr.BAD_REQUEST, "Frame too large.")
            return None
        try:
            frame = json.loads(raw)
        except ValueError:
            self._protocol_error(ws, merr.BAD_REQUEST, "Frame is not valid JSON.")
            return None
        if not isinstance(frame, dict) or not isinstance(frame.get("type"), str):
            self._protocol_error(ws, merr.BAD_REQUEST, "Frame must have a type.")
            return None
        return frame

    def _dispatch_authenticated_frame(
        self,
        ws: Any,
        principal: MobilePrincipal,
        frame: dict[str, Any],
        message_limiter: SlidingWindowLimiter,
    ) -> tuple[bool, bool]:
        frame_type = frame["type"]
        if not self._require_active_session(ws, principal):
            return False, False
        if frame_type == "ping":
            self._send(ws, mev.pong_event(self._now_iso()))
            return True, False
        if frame_type == "auth":
            self._protocol_error(ws, merr.BAD_REQUEST, "Connection is already authenticated.")
            return True, False
        if frame_type != "chat":
            self._protocol_error(ws, merr.BAD_REQUEST, "Unsupported frame type.")
            return True, False
        if not message_limiter.allow(principal.session_id):
            self._close(ws, CLOSE_RATE_LIMITED, "Message rate limited")
            return False, False
        return self._handle_chat_frame(ws, principal, frame), True

    def _now_iso(self) -> str:
        return self._services.clock().isoformat()

    def _handle_chat_frame(
        self, ws: Any, principal: MobilePrincipal, frame: dict[str, Any]
    ) -> bool:
        """Process one chat frame.  Returns False when the socket is gone."""
        services = self._services
        try:
            chat_request = parse_chat_payload(frame)
        except MobileApiError as exc:
            self._protocol_error(
                ws,
                exc.code,
                exc.message,
                message_id=str(frame.get("message_id") or "") or None,
            )
            return True

        request_hash = idem.compute_request_hash(chat_request.semantic_fields())
        reservation = services.message_store.reserve(
            principal.user_id,
            chat_request.message_id,
            chat_request.conversation_id,
            request_hash,
        )

        if reservation.outcome == idem.CONFLICT:
            self._protocol_error(
                ws,
                merr.IDEMPOTENCY_CONFLICT,
                "This message ID was already used with a different request.",
                message_id=chat_request.message_id,
            )
            return True

        # Reservation is durable — acknowledge (original ack for duplicates).
        self._send(ws, mev.ack_event(chat_request.message_id, self._now_iso()))

        if reservation.outcome == idem.DUPLICATE_COMPLETED:
            stored = json.loads(reservation.response_json or "{}")
            self._send(
                ws,
                mev.message_done_event(
                    chat_request.message_id,
                    chat_request.conversation_id,
                    str(stored.get("response", "")),
                    status=str(stored.get("status", STATUS_COMPLETED)),
                    attachments=list(stored.get("attachments", [])),
                ),
            )
            return True
        if reservation.outcome == idem.DUPLICATE_FAILED:
            self._send(
                ws,
                mev.error_event(
                    reservation.error_code or merr.INTERNAL_ERROR,
                    "A previous attempt of this message failed. " "Send a new message ID to retry.",
                    message_id=chat_request.message_id,
                ),
            )
            return True
        if reservation.outcome == idem.DUPLICATE_PROCESSING:
            self._send(
                ws,
                mev.error_event(
                    merr.REQUEST_IN_PROGRESS,
                    "This message is already being processed.",
                    message_id=chat_request.message_id,
                    retryable=True,
                ),
            )
            return True

        try:
            attachments = services.attachment_store.validate_references(
                user_id=principal.user_id,
                device_id=principal.paired_device_id,
                conversation_id=chat_request.conversation_id,
                attachment_ids=chat_request.attachment_ids,
            )
        except MobileApiError as exc:
            store = services.message_store
            store.fail(principal.user_id, chat_request.message_id, exc.code)
            self._send(
                ws,
                mev.error_event(
                    exc.code,
                    exc.message,
                    message_id=chat_request.message_id,
                    retryable=exc.retryable,
                    details=exc.details,
                ),
            )
            return True

        # New reservation: execute exactly once via the canonical Assistant
        # with the connection's immutable principal identity.
        store = services.message_store
        collected: list[str] = []
        try:

            def authorization_check() -> None:
                revalidate_principal(
                    services,
                    principal,
                    required_scope=ROUTE_SCOPES["chat.websocket"],
                )

            def status_observer(update) -> None:  # noqa: ANN001
                self._send(ws, mev.status_event(chat_request.message_id, update))

            iterator = iter(
                services.chat_service.stream(
                    chat_request.message,
                    user_id=principal.user_id,
                    capability_scopes=principal.scopes,
                    capability_permissions=principal.permissions,
                    authorization_check=authorization_check,
                    strong_auth_authority=services.strong_auth_authority,
                    strong_auth_principal=principal,
                    strong_auth_approval_id=chat_request.strong_auth_approval_id,
                    status_observer=status_observer,
                )
            )
            while True:
                if not self._require_active_session(ws, principal):
                    store.fail(
                        principal.user_id,
                        chat_request.message_id,
                        merr.AUTH_SESSION_REVOKED,
                    )
                    return False
                try:
                    chunk = next(iterator)
                except StopIteration:
                    break
                if not self._require_active_session(ws, principal):
                    store.fail(
                        principal.user_id,
                        chat_request.message_id,
                        merr.AUTH_SESSION_REVOKED,
                    )
                    return False
                collected.append(chunk)
                self._send(ws, mev.token_event(chat_request.message_id, chunk))
        except MobileApiError as exc:
            store.fail(principal.user_id, chat_request.message_id, exc.code)
            try:
                self._send(
                    ws,
                    mev.error_event(
                        exc.code,
                        exc.message,
                        message_id=chat_request.message_id,
                        retryable=exc.retryable,
                        details=exc.details,
                    ),
                )
            except Exception:
                return False
            return True
        except Exception as exc:
            if _is_connection_closed(exc):
                # Socket dropped mid-stream: coherent terminal state so a
                # replayed ID never re-executes the Assistant or tools.
                store.fail(principal.user_id, chat_request.message_id, CLIENT_DISCONNECTED)
                return False
            logger.exception("Mobile WebSocket chat failed (session=%s)", principal.session_id)
            store.fail(principal.user_id, chat_request.message_id, merr.INTERNAL_ERROR)
            try:
                self._send(
                    ws,
                    mev.error_event(
                        merr.INTERNAL_ERROR,
                        "An unexpected error occurred.",
                        message_id=chat_request.message_id,
                    ),
                )
            except Exception:
                return False
            return True

        full_content = "".join(collected)
        body: dict[str, object] = {
            "request_id": None,
            "message_id": chat_request.message_id,
            "conversation_id": chat_request.conversation_id,
            "response": full_content,
            "status": STATUS_COMPLETED,
            "events": [],
            "attachments": [attachment.public_dict() for attachment in attachments],
        }
        # Terminal result is stored before message_done is announced.
        store.complete(
            principal.user_id, chat_request.message_id, json.dumps(body, ensure_ascii=True)
        )
        try:
            self._send(
                ws,
                mev.message_done_event(
                    chat_request.message_id,
                    chat_request.conversation_id,
                    full_content,
                    status=STATUS_COMPLETED,
                    attachments=[attachment.public_dict() for attachment in attachments],
                ),
            )
        except Exception:
            return False
        return True


def _is_connection_closed(exc: Exception) -> bool:
    try:
        from simple_websocket import ConnectionClosed, ConnectionError  # noqa: PLC0415

        return isinstance(exc, (ConnectionClosed, ConnectionError))
    except ImportError:  # pragma: no cover - only without the dependency
        return False


def websocket_dependency_available() -> bool:
    """True when the validated Flask WebSocket stack is importable."""
    return find_spec("flask_sock") is not None and find_spec("simple_websocket") is not None


def register_websocket(app: Any, services: MobileApiServices) -> bool:
    """Register ``WebSocket /mobile/chat/stream`` when the stack is present.

    Returns True when the route was registered; False (and the
    ``websocket_chat`` capability stays false) when the dependency is
    missing.
    """
    if not websocket_dependency_available():
        logger.info("flask-sock not installed; mobile WebSocket chat disabled")
        return False

    from flask import request  # noqa: PLC0415
    from flask_sock import Sock  # noqa: PLC0415

    sock = Sock(app)
    server = MobileWebSocketServer(services)
    app.extensions["mobile_api_websocket"] = server

    @sock.route("/mobile/chat/stream")
    def mobile_chat_stream(ws: Any) -> None:  # pragma: no cover - thin wiring
        server.handle(ws, request.remote_addr or "unknown")

    return True


__all__ = [
    "AUTH_TIMEOUT_SECONDS",
    "CLOSE_AUTH_TIMEOUT",
    "CLOSE_FORBIDDEN",
    "CLOSE_RATE_LIMITED",
    "CLOSE_UNAUTHENTICATED",
    "CONNECTIONS_PER_MINUTE",
    "MAX_FRAME_BYTES",
    "MobileWebSocketServer",
    "SlidingWindowLimiter",
    "register_websocket",
    "websocket_dependency_available",
]
