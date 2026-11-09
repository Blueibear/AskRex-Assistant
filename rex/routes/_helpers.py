"""Shared helpers shared across Rex route blueprints."""

from __future__ import annotations

import hmac
import logging
import re
from pathlib import Path
from typing import Any

# Compiled once at import time; used to redact home-dir paths in log output.
_HOME_DIR_RE = re.compile(re.escape(str(Path.home())), re.IGNORECASE)


def _redact_log_line(line: str) -> str:
    """Replace home-directory paths in a log line with ``~``."""
    return _HOME_DIR_RE.sub("~", line)


def _require_auth() -> tuple[dict[str, Any], None] | tuple[None, Any]:
    """Extract and validate the Bearer token from the current request.

    Returns:
        ``(user_dict, None)`` on success.
        ``(None, flask_response)`` on failure — caller should return the response.
    """
    from flask import jsonify, request

    from rex.auth import get_current_user

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None, (jsonify({"error": "authentication required"}), 401)
    token = auth_header[len("Bearer ") :]
    try:
        return get_current_user(token), None
    except ValueError as exc:
        return None, (jsonify({"error": str(exc)}), 401)


def _require_setup_token() -> tuple[None, None] | tuple[None, Any]:
    """Validate the X-Setup-Token header for pre-setup routes.

    Returns:
        ``(None, None)`` when the token is valid.
        ``(None, flask_response)`` when the token is missing, wrong, or consumed.
    """
    from flask import current_app, jsonify, request

    expected: str | None = current_app.config.get("SETUP_TOKEN")
    if not expected:
        return None, (jsonify({"error": "setup already completed"}), 403)
    provided = request.headers.get("X-Setup-Token", "")
    if not provided or not hmac.compare_digest(provided, expected):
        return None, (jsonify({"error": "forbidden"}), 403)
    return None, None


def _log_nonfatal_exception(message: str) -> None:
    """Log a best-effort route side effect failure without changing the response."""
    from flask import current_app, has_app_context

    logger = current_app.logger if has_app_context() else logging.getLogger(__name__)
    logger.debug(message, exc_info=True)
