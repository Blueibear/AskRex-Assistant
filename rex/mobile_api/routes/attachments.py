"""Authenticated temporary per-conversation attachment ingestion."""

from __future__ import annotations

import uuid
from typing import Any

from flask import Blueprint, g, jsonify, request

from rex.mobile_api import errors as merr
from rex.mobile_api.attachments import sanitized_filename
from rex.mobile_api.auth import require_mobile_auth, revalidate_principal
from rex.mobile_api.authorization import ROUTE_SCOPES
from rex.mobile_api.errors import MobileApiError
from rex.mobile_api.services import MobileApiServices


def _conversation_id() -> str:
    value = request.form.get("conversation_id")
    if not isinstance(value, str):
        raise MobileApiError(merr.BAD_REQUEST, "Field 'conversation_id' is required.", 400)
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise MobileApiError(merr.BAD_REQUEST, "Field 'conversation_id' must be a UUID.", 400) from exc


def build_attachments_blueprint(services: MobileApiServices, limiter: Any) -> Blueprint:
    bp = Blueprint("mobile_attachments", __name__, url_prefix="/mobile")

    @bp.post("/attachments")
    @limiter.limit(services.config.rate_limit_chat)
    @require_mobile_auth(required_scope=ROUTE_SCOPES["attachments.upload"])
    def upload_attachment() -> Any:
        if not request.content_type or "multipart/form-data" not in request.content_type:
            raise MobileApiError(merr.INVALID_MEDIA, "Content-Type must be multipart/form-data.", 415)
        if request.content_length is not None and request.content_length > (
            services.config.max_attachment_bytes + 128 * 1024
        ):
            raise MobileApiError(merr.PAYLOAD_TOO_LARGE, "The attachment is too large.", 413)
        if (
            set(request.form) != {"conversation_id"}
            or set(request.files) != {"attachment"}
            or len(request.files.getlist("attachment")) != 1
        ):
            raise MobileApiError(
                merr.BAD_REQUEST,
                "Exactly conversation_id and one attachment file are required.",
                400,
            )
        conversation_id = _conversation_id()
        part = request.files.get("attachment")
        if part is None or not part.filename:
            raise MobileApiError(merr.BAD_REQUEST, "One attachment file is required.", 400)
        data = bytes(part.read())
        if not data:
            raise MobileApiError(merr.INVALID_MEDIA, "The attachment is empty.", 415)
        if len(data) > services.config.max_attachment_bytes:
            raise MobileApiError(merr.PAYLOAD_TOO_LARGE, "The attachment is too large.", 413)
        revalidate_principal(
            services,
            g.mobile_principal,
            required_scope=ROUTE_SCOPES["attachments.upload"],
        )
        attachment = services.attachment_store.create(
            user_id=g.mobile_principal.user_id,
            device_id=g.mobile_principal.paired_device_id,
            conversation_id=conversation_id,
            filename=sanitized_filename(part.filename),
            data=data,
            max_per_conversation=services.config.max_attachments_per_conversation,
        )
        return (
            jsonify(
                {
                    "request_id": getattr(g, "request_id", None),
                    "status": "ready",
                    "attachment": attachment.public_dict(),
                }
            ),
            201,
        )

    return bp


__all__ = ["build_attachments_blueprint"]
