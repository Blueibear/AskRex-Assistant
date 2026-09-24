"""Structured mobile API errors (issue #323).

Every mobile error uses the repository's nested error envelope extended with
``retryable`` and ``request_id``::

    {
      "error": {
        "code": "AUTH_INVALID_CREDENTIALS",
        "message": "Invalid username or password.",
        "retryable": false,
        "request_id": "<request-id>"
      }
    }

Error text must never reveal whether an arbitrary username, session ID, or
private resource exists, and must never contain tokens, passwords, hashes, or
request bodies.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Stable mobile error codes (master spec §5.2).
BAD_REQUEST = "BAD_REQUEST"
AUTH_INVALID_CREDENTIALS = "AUTH_INVALID_CREDENTIALS"
AUTH_TOKEN_EXPIRED = "AUTH_TOKEN_EXPIRED"
AUTH_TOKEN_INVALID = "AUTH_TOKEN_INVALID"
AUTH_TOKEN_REVOKED = "AUTH_TOKEN_REVOKED"
AUTH_SESSION_REVOKED = "AUTH_SESSION_REVOKED"
AUTH_REFRESH_REUSED = "AUTH_REFRESH_REUSED"
FORBIDDEN = "FORBIDDEN"
PERMISSION_DENIED = "PERMISSION_DENIED"
APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
STRONG_AUTH_REQUIRED = "STRONG_AUTH_REQUIRED"
STRONG_AUTH_INVALID = "STRONG_AUTH_INVALID"
PAIRING_INVALID = "PAIRING_INVALID"
PAIRING_PENDING = "PAIRING_PENDING"
NOT_FOUND = "NOT_FOUND"
NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
# Cross-transport idempotency (issue #323 Session 2): a reused message ID
# with a different semantic payload conflicts; a duplicate of a request that
# is still executing is reported without re-execution.
IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
REQUEST_IN_PROGRESS = "REQUEST_IN_PROGRESS"
UNSUPPORTED_API_VERSION = "UNSUPPORTED_API_VERSION"
TLS_REQUIRED = "TLS_REQUIRED"
INVALID_MEDIA = "INVALID_MEDIA"
PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
RATE_LIMITED = "RATE_LIMITED"
BACKEND_UNAVAILABLE = "BACKEND_UNAVAILABLE"
INTERNAL_ERROR = "INTERNAL_ERROR"

# HTTP status → mobile error code for uncaught werkzeug HTTPExceptions.
_HTTP_STATUS_TO_CODE: dict[int, str] = {
    400: BAD_REQUEST,
    401: AUTH_TOKEN_INVALID,
    403: FORBIDDEN,
    404: NOT_FOUND,
    405: BAD_REQUEST,
    409: BAD_REQUEST,
    413: PAYLOAD_TOO_LARGE,
    415: INVALID_MEDIA,
    426: UNSUPPORTED_API_VERSION,
    429: RATE_LIMITED,
    500: INTERNAL_ERROR,
    501: NOT_IMPLEMENTED,
    503: BACKEND_UNAVAILABLE,
}

_RETRYABLE_STATUSES = {429, 503}

_INSTALLED_KEY = "rex_mobile_error_handlers_installed"


class MobileApiError(Exception):
    """Expected mobile API error translated to the nested envelope."""

    def __init__(
        self,
        code: str,
        message: str,
        http_status: int,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        # ``http_status`` is the canonical internal name.  Preserve the
        # conventional exception attribute too, so callers that need to
        # distinguish expected API failures do not depend on implementation
        # details of this envelope class.
        self.status_code = http_status
        self.retryable = retryable
        self.details = details


def _current_request_id() -> str | None:
    from flask import g  # noqa: PLC0415

    return getattr(g, "request_id", None)


def mobile_error_response(
    code: str,
    message: str,
    http_status: int,
    *,
    retryable: bool = False,
    details: dict[str, Any] | None = None,
) -> tuple[Any, int]:
    """Return a Flask ``(response, status)`` tuple with the mobile envelope."""
    from flask import jsonify  # noqa: PLC0415

    error: dict[str, Any] = {
        "code": code,
        "message": message,
        "retryable": retryable,
        "request_id": _current_request_id(),
    }
    if details is not None:
        error["details"] = details
    return jsonify({"error": error}), http_status


def install_mobile_error_handlers(app: Any) -> None:
    """Install the mobile error envelope handlers on *app* (idempotent)."""
    if app.extensions.get(_INSTALLED_KEY):
        return
    app.extensions[_INSTALLED_KEY] = True

    from werkzeug.exceptions import HTTPException  # noqa: PLC0415

    @app.errorhandler(MobileApiError)
    def _handle_mobile_error(exc: MobileApiError) -> tuple[Any, int]:
        return mobile_error_response(
            exc.code,
            exc.message,
            exc.http_status,
            retryable=exc.retryable,
            details=exc.details,
        )

    @app.errorhandler(HTTPException)
    def _handle_http_exception(exc: HTTPException) -> tuple[Any, int]:
        status = exc.code or 500
        code = _HTTP_STATUS_TO_CODE.get(status, INTERNAL_ERROR if status >= 500 else BAD_REQUEST)
        message = exc.description or exc.name or "An error occurred."
        return mobile_error_response(code, message, status, retryable=status in _RETRYABLE_STATUSES)

    @app.errorhandler(Exception)
    def _handle_unexpected_exception(exc: Exception) -> tuple[Any, int]:
        # Log only the exception and request ID — never request bodies.
        logger.exception("Unhandled mobile API exception (request_id=%s)", _current_request_id())
        return mobile_error_response(INTERNAL_ERROR, "An unexpected error occurred.", 500)


__all__ = [
    "APPROVAL_REQUIRED",
    "AUTH_INVALID_CREDENTIALS",
    "AUTH_REFRESH_REUSED",
    "AUTH_SESSION_REVOKED",
    "AUTH_TOKEN_EXPIRED",
    "AUTH_TOKEN_INVALID",
    "AUTH_TOKEN_REVOKED",
    "BACKEND_UNAVAILABLE",
    "BAD_REQUEST",
    "FORBIDDEN",
    "IDEMPOTENCY_CONFLICT",
    "INTERNAL_ERROR",
    "INVALID_MEDIA",
    "MobileApiError",
    "NOT_FOUND",
    "NOT_IMPLEMENTED",
    "PAIRING_INVALID",
    "PAIRING_PENDING",
    "PAYLOAD_TOO_LARGE",
    "PERMISSION_DENIED",
    "RATE_LIMITED",
    "REQUEST_IN_PROGRESS",
    "STRONG_AUTH_INVALID",
    "STRONG_AUTH_REQUIRED",
    "TLS_REQUIRED",
    "UNSUPPORTED_API_VERSION",
    "install_mobile_error_handlers",
    "mobile_error_response",
]
