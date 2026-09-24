"""Authenticated mobile chat routes (issue #323, Session 2).

``POST /mobile/chat``          — non-streaming chat.
``POST /mobile/chat/stream``   — SSE streaming chat.

Both routes:

- require the Session 1 mobile principal (Bearer access token);
- reject any client-supplied identity/authorization field;
- validate ``message_id``/``conversation_id``/``sent_at``/size/mode/context;
- require a matching ``Idempotency-Key`` header when present;
- durably reserve ``(user_id, message_id)`` *before* any acknowledgement or
  Assistant/tool execution (shared with the WebSocket transport);
- call the canonical ``Assistant`` with an explicit ``active_user_id``;
- report normal conversational completion as ``completed`` (never
  ``verified``) and translate backend failure into structured errors.

Duplicate handling (see :mod:`rex.mobile_api.idempotency`): an exact
completed duplicate replays the stored terminal result; an exact duplicate
still in progress reports ``REQUEST_IN_PROGRESS`` without re-executing; a
reused message ID with different semantics returns ``IDEMPOTENCY_CONFLICT``.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from flask import Blueprint, Response, g, jsonify, request

from rex.mobile_api import errors as merr
from rex.mobile_api import events as mev
from rex.mobile_api import idempotency as idem
from rex.mobile_api.auth import require_mobile_auth, revalidate_principal
from rex.mobile_api.authorization import ROUTE_SCOPES
from rex.mobile_api.chat import STATUS_COMPLETED
from rex.mobile_api.errors import MobileApiError
from rex.mobile_api.services import MobileApiServices
from rex.mobile_api.validation import ChatRequest, parse_chat_payload, parse_json_body

logger = logging.getLogger(__name__)

CLIENT_DISCONNECTED = idem.CLIENT_DISCONNECTED

_FAILED_REPLAY_STATUS: dict[str, int] = {
    merr.BACKEND_UNAVAILABLE: 503,
    merr.BAD_REQUEST: 400,
    merr.PAYLOAD_TOO_LARGE: 413,
    merr.INVALID_MEDIA: 415,
}


def _validated_chat_request() -> ChatRequest:
    payload = parse_json_body()
    chat_request = parse_chat_payload(payload)
    header_key = request.headers.get("Idempotency-Key")
    if header_key is not None and header_key.strip() != chat_request.message_id:
        raise MobileApiError(
            merr.IDEMPOTENCY_CONFLICT,
            "Idempotency-Key header does not match message_id.",
            409,
        )
    return chat_request


def _reserve(services: MobileApiServices, user_id: str, req: ChatRequest) -> idem.Reservation:
    request_hash = idem.compute_request_hash(req.semantic_fields())
    return services.message_store.reserve(
        user_id, req.message_id, req.conversation_id, request_hash
    )


def _conflict_error() -> MobileApiError:
    return MobileApiError(
        merr.IDEMPOTENCY_CONFLICT,
        "This message ID was already used with a different request.",
        409,
    )


def _in_progress_error() -> MobileApiError:
    return MobileApiError(
        merr.REQUEST_IN_PROGRESS,
        "This message is already being processed.",
        409,
        retryable=True,
    )


def _failed_replay_error(error_code: str | None) -> MobileApiError:
    code = error_code or merr.INTERNAL_ERROR
    status = _FAILED_REPLAY_STATUS.get(code, 500)
    if code == CLIENT_DISCONNECTED:
        code, status = merr.INTERNAL_ERROR, 500
    return MobileApiError(
        code,
        "A previous attempt of this message failed. Send a new message ID to retry.",
        status,
    )


def build_chat_blueprint(services: MobileApiServices, limiter: Any) -> Blueprint:
    bp = Blueprint("mobile_chat", __name__, url_prefix="/mobile")
    cfg = services.config

    @bp.post("/chat")
    @limiter.limit(cfg.rate_limit_chat)
    @require_mobile_auth(required_scope=ROUTE_SCOPES["chat.send"])
    def chat() -> Any:
        principal = g.mobile_principal
        chat_request = _validated_chat_request()
        reservation = _reserve(services, principal.user_id, chat_request)

        if reservation.outcome == idem.CONFLICT:
            raise _conflict_error()
        if reservation.outcome == idem.DUPLICATE_PROCESSING:
            raise _in_progress_error()
        if reservation.outcome == idem.DUPLICATE_FAILED:
            raise _failed_replay_error(reservation.error_code)
        if reservation.outcome == idem.DUPLICATE_COMPLETED:
            # Exact duplicate: replay the stored terminal result, no execution.
            stored = json.loads(reservation.response_json or "{}")
            return jsonify(stored), 200
        try:
            attachments = services.attachment_store.validate_references(
                user_id=principal.user_id,
                device_id=principal.paired_device_id,
                conversation_id=chat_request.conversation_id,
                attachment_ids=chat_request.attachment_ids,
            )
        except MobileApiError as exc:
            services.message_store.fail(principal.user_id, chat_request.message_id, exc.code)
            raise

        def authorization_check() -> None:
            revalidate_principal(
                services,
                principal,
                required_scope=ROUTE_SCOPES["chat.send"],
            )

        status_events: list[dict[str, Any]] = []

        def status_observer(update) -> None:  # noqa: ANN001
            status_events.append(mev.status_event(chat_request.message_id, update))

        try:
            completion = services.chat_service.generate(
                chat_request.message,
                user_id=principal.user_id,
                device_id=principal.paired_device_id,
                capability_scopes=principal.scopes,
                capability_permissions=principal.permissions,
                authorization_check=authorization_check,
                strong_auth_authority=services.strong_auth_authority,
                strong_auth_principal=principal,
                strong_auth_approval_id=chat_request.strong_auth_approval_id,
                status_observer=status_observer,
            )
            revalidate_principal(
                services,
                principal,
                required_scope=ROUTE_SCOPES["chat.send"],
            )
        except MobileApiError as exc:
            services.message_store.fail(principal.user_id, chat_request.message_id, exc.code)
            raise
        except Exception:
            services.message_store.fail(
                principal.user_id, chat_request.message_id, merr.INTERNAL_ERROR
            )
            raise

        body: dict[str, object] = {
            "request_id": getattr(g, "request_id", None),
            "message_id": chat_request.message_id,
            "conversation_id": chat_request.conversation_id,
            "response": completion,
            "status": STATUS_COMPLETED,
            "events": status_events,
            "attachments": [attachment.public_dict() for attachment in attachments],
        }
        # Persist the terminal result before responding so a retry replays
        # exactly what this client received.
        services.message_store.complete(
            principal.user_id,
            chat_request.message_id,
            json.dumps(body, ensure_ascii=True),
        )
        return jsonify(body), 200

    @bp.post("/chat/stream")
    @limiter.limit(cfg.rate_limit_chat)
    @require_mobile_auth(required_scope=ROUTE_SCOPES["chat.stream"])
    def chat_stream() -> Any:
        principal = g.mobile_principal
        chat_request = _validated_chat_request()
        request_id = getattr(g, "request_id", None)
        reservation = _reserve(services, principal.user_id, chat_request)

        if reservation.outcome == idem.CONFLICT:
            raise _conflict_error()
        if reservation.outcome == idem.DUPLICATE_PROCESSING:
            raise _in_progress_error()
        if reservation.outcome == idem.DUPLICATE_FAILED:
            raise _failed_replay_error(reservation.error_code)
        if reservation.outcome == idem.DUPLICATE_COMPLETED:
            stored = json.loads(reservation.response_json or "{}")
            replay = mev.message_done_event(
                chat_request.message_id,
                chat_request.conversation_id,
                str(stored.get("response", "")),
                status=str(stored.get("status", STATUS_COMPLETED)),
            )
            return _sse_response(iter([mev.format_sse(replay)]))
        try:
            attachments = services.attachment_store.validate_references(
                user_id=principal.user_id,
                device_id=principal.paired_device_id,
                conversation_id=chat_request.conversation_id,
                attachment_ids=chat_request.attachment_ids,
            )
        except MobileApiError as exc:
            services.message_store.fail(principal.user_id, chat_request.message_id, exc.code)
            raise

        user_id = principal.user_id
        message_id = chat_request.message_id
        conversation_id = chat_request.conversation_id
        message = chat_request.message
        store = services.message_store
        chat_service = services.chat_service

        def authorization_check() -> None:
            revalidate_principal(
                services,
                principal,
                required_scope=ROUTE_SCOPES["chat.stream"],
            )

        def generate():
            # No Flask request-context access inside the generator: every
            # needed value was captured above.
            collected: list[str] = []
            pending_status: list[dict[str, Any]] = []
            finished = False

            def status_observer(update) -> None:  # noqa: ANN001
                pending_status.append(mev.status_event(message_id, update))

            def flush_status():
                while pending_status:
                    yield mev.format_sse(pending_status.pop(0))

            try:
                try:
                    iterator = iter(
                        chat_service.stream(
                            message,
                            user_id=user_id,
                            device_id=principal.paired_device_id,
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
                        revalidate_principal(
                            services,
                            principal,
                            required_scope=ROUTE_SCOPES["chat.stream"],
                        )
                        try:
                            chunk = next(iterator)
                        except StopIteration:
                            yield from flush_status()
                            break
                        yield from flush_status()
                        revalidate_principal(
                            services,
                            principal,
                            required_scope=ROUTE_SCOPES["chat.stream"],
                        )
                        collected.append(chunk)
                        yield mev.format_sse(mev.token_event(message_id, chunk))
                except MobileApiError as exc:
                    yield from flush_status()
                    store.fail(user_id, message_id, exc.code)
                    finished = True
                    yield mev.format_sse(
                        mev.error_event(
                            exc.code,
                            exc.message,
                            message_id=message_id,
                            retryable=exc.retryable,
                            request_id=request_id,
                            details=exc.details,
                        )
                    )
                    return
                except Exception:
                    yield from flush_status()
                    logger.exception("Mobile SSE stream failed (request_id=%s)", request_id)
                    store.fail(user_id, message_id, merr.INTERNAL_ERROR)
                    finished = True
                    yield mev.format_sse(
                        mev.error_event(
                            merr.INTERNAL_ERROR,
                            "An unexpected error occurred.",
                            message_id=message_id,
                            request_id=request_id,
                        )
                    )
                    return

                full_content = "".join(collected)
                body: dict[str, object] = {
                    "request_id": request_id,
                    "message_id": message_id,
                    "conversation_id": conversation_id,
                    "response": full_content,
                    "status": STATUS_COMPLETED,
                    "events": [],
                    "attachments": [attachment.public_dict() for attachment in attachments],
                }
                # Terminal result is stored before message_done is emitted.
                store.complete(user_id, message_id, json.dumps(body, ensure_ascii=True))
                finished = True
                yield mev.format_sse(
                    mev.message_done_event(
                        message_id, conversation_id, full_content, status=STATUS_COMPLETED
                    )
                )
            finally:
                if not finished:
                    # Client disconnected mid-stream: leave a coherent
                    # terminal state so a replayed ID never re-executes.
                    try:
                        store.fail(user_id, message_id, CLIENT_DISCONNECTED)
                    except Exception:  # pragma: no cover - cleanup path
                        logger.warning(
                            "Failed to record disconnected stream (request_id=%s)",
                            request_id,
                        )

        return _sse_response(generate())

    return bp


def _sse_response(iterator) -> Response:
    response = Response(iterator, mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    return response


__all__ = ["CLIENT_DISCONNECTED", "build_chat_blueprint"]
