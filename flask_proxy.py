"""Legacy Flask proxy compatibility surface.

This module keeps the root-level ``flask_proxy`` import available for the
legacy proxy/search/contracts API documented in ``docs/UI_SURFACES.md``.
The Electron app and ``rex-gui`` are the primary supported interfaces; this
surface remains intentionally small and compatibility-focused.
"""

from __future__ import annotations

import hmac
import importlib
import importlib.util
import json
import os
from types import SimpleNamespace
from typing import Any

from flask import Flask, abort, jsonify, redirect, request
from flask import g as flask_g
from flask_cors import CORS

import utils.env_loader  # noqa: F401  # Auto-loads .env on import
from rex.contracts import CONTRACT_VERSION
from rex.contracts.core import ALL_MODELS
from rex.health import check_config, create_health_blueprint
from rex.http_errors import (
    BAD_REQUEST,
    INTERNAL_ERROR,
    SERVICE_UNAVAILABLE,
    error_response,
    install_error_envelope_handler,
)
from rex.memory_utils import load_memory_profile, load_users_map, resolve_user_key
from rex.rate_limiter import install_rate_limiter
from rex.request_logging import install_request_logging
from rex.startup import log_service_ready, run_startup_sequence

_TESTING_MODE = os.getenv("REX_TESTING", "").lower() in {"1", "true", "yes"}

# Import dashboard blueprint if the compatibility dashboard package exposes one.
dashboard_bp = getattr(importlib.import_module("rex.dashboard"), "dashboard_bp", None)
_DASHBOARD_AVAILABLE = dashboard_bp is not None

_CONTRACTS_AVAILABLE = True

g: Any = SimpleNamespace() if _TESTING_MODE else flask_g

# --- Startup sequence — config → database → migration → service init ---
if not _TESTING_MODE:
    run_startup_sequence()

# --- Flask Setup ---
app = Flask(__name__)
install_request_logging(app)
install_error_envelope_handler(app)
install_rate_limiter(app)
app.register_blueprint(create_health_blueprint(checks=[check_config]))

# CORS Configuration: Restrict origins based on environment.
ALLOWED_ORIGINS = os.getenv(
    "REX_ALLOWED_ORIGINS",
    "http://localhost:3000,http://localhost:5000,http://127.0.0.1:3000,http://127.0.0.1:5000",
)
_CORS_ORIGINS = [origin.strip() for origin in ALLOWED_ORIGINS.split(",") if origin.strip()]

CORS(
    app,
    resources={
        r"/*": {
            "origins": _CORS_ORIGINS,
            "methods": ["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
            "allow_headers": [
                "Content-Type",
                "Authorization",
                "X-Rex-Proxy-Token",
                "X-Dashboard-Token",
                "Cf-Access-Authenticated-User-Email",
            ],
            "supports_credentials": True,
            "max_age": 600,
        }
    },
)

if _DASHBOARD_AVAILABLE:
    app.register_blueprint(dashboard_bp)

# Register inbound SMS webhook blueprint (config-driven; disabled by default).
try:
    register_inbound_sms_webhook = importlib.import_module(
        "rex.messaging_backends.webhook_wiring"
    ).register_inbound_sms_webhook
    _INBOUND_SMS_REGISTERED = register_inbound_sms_webhook(app)
except Exception as _inbound_exc:
    app.logger.debug("Inbound SMS webhook wiring skipped: %s", _inbound_exc)
    _INBOUND_SMS_REGISTERED = False

# Wire retention cleanup scheduler jobs (config-driven; safe to skip on failure).
try:
    from rex.retention import wire_retention_cleanup

    _RETENTION_CLEANUP_WIRED = wire_retention_cleanup()
except Exception as _retention_exc:
    app.logger.debug("Retention cleanup wiring skipped: %s", _retention_exc)
    _RETENTION_CLEANUP_WIRED = False

# --- Optional Plugin: Web Search ---
search_web = None
try:
    spec = importlib.util.find_spec("plugins.web_search")
    if spec:
        mod = importlib.import_module("plugins.web_search")
        search_web = getattr(mod, "search_web", None)
except Exception as exc:
    app.logger.warning("Failed to load search plugin: %s", exc)

# --- Constants ---
USERS_MAP = load_users_map()
PROXY_TOKEN = os.getenv("REX_PROXY_TOKEN")
ALLOW_LOCAL = os.getenv("REX_PROXY_ALLOW_LOCAL") == "1"

if not _TESTING_MODE:
    log_service_ready()


def _summarize_memory(profile: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    if not isinstance(profile, dict):
        return summary

    if isinstance(name := profile.get("name"), str):
        summary["name"] = name

    if isinstance(role := profile.get("role"), str):
        summary["role"] = role

    prefs = profile.get("preferences")
    if isinstance(prefs, dict):
        summary["preferences"] = {
            "tone": prefs.get("tone"),
            "topics": [topic for topic in prefs.get("topics", []) if isinstance(topic, str)],
        }

    return summary


def _extract_shared_secret() -> str | None:
    auth_header = request.headers.get("Authorization", "")
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip() or None
    return request.headers.get("X-Rex-Proxy-Token")


def _is_loopback_address(addr: str | None) -> bool:
    if not addr:
        return False
    if addr.startswith("::ffff:"):
        addr = addr.split("::ffff:", 1)[-1]
    return addr in {"127.0.0.1", "::1"}


def _testing_memory_profile(user_key: str | None) -> dict[str, Any]:
    name = user_key or "test-user"
    return {"name": name, "role": "test user", "preferences": {"topics": []}}


# --- Public Endpoints (no auth required) ---
_PUBLIC_PATHS = frozenset({"/contracts", "/"})
_DASHBOARD_PREFIXES = (
    "/dashboard",
    "/api/dashboard",
    "/api/settings",
    "/api/chat",
    "/api/scheduler",
    "/api/notifications",
    "/api/voice",
)
_WEBHOOK_PREFIXES = ("/webhooks/",)


def _is_memory_exempt_path(path: str) -> bool:
    return (
        path in _PUBLIC_PATHS
        or any(path.startswith(prefix) for prefix in _DASHBOARD_PREFIXES)
        or any(path.startswith(prefix) for prefix in _WEBHOOK_PREFIXES)
    )


def _resolve_cf_user_key(email: str) -> str:
    resolved_user_key = resolve_user_key(email, USERS_MAP)
    if not resolved_user_key:
        abort(403, "Access denied for this user.")
    return resolved_user_key


def _resolve_token_user_key(token: str) -> str:
    if not PROXY_TOKEN or not hmac.compare_digest(token, PROXY_TOKEN):
        abort(403, "Invalid or missing proxy token.")
    resolved_user_key = resolve_user_key(os.getenv("REX_ACTIVE_USER"), USERS_MAP)
    if not resolved_user_key:
        abort(403, "REX_ACTIVE_USER is not configured or invalid.")
    return resolved_user_key


def _resolve_local_user_key() -> str:
    active_user = os.getenv("REX_ACTIVE_USER")
    resolved_user_key = resolve_user_key(active_user, USERS_MAP)
    if not resolved_user_key and _TESTING_MODE and active_user:
        resolved_user_key = active_user.strip().lower()
    if not resolved_user_key:
        abort(403, "Local access not permitted: REX_ACTIVE_USER missing.")
    return resolved_user_key


def _resolve_request_user_key() -> str:
    email = request.headers.get("Cf-Access-Authenticated-User-Email")
    token = _extract_shared_secret()

    if email:
        return _resolve_cf_user_key(email)
    if token:
        return _resolve_token_user_key(token)
    if ALLOW_LOCAL and _is_loopback_address(request.remote_addr):
        return _resolve_local_user_key()

    abort(403, "No authenticated identity provided.")


def _load_request_memory_profile(user_key: str) -> dict[str, Any]:
    try:
        return load_memory_profile(user_key)
    except FileNotFoundError:
        if _TESTING_MODE:
            return _testing_memory_profile(user_key)
        abort(500, f"Memory file not found for user '{user_key}'.")
    except json.JSONDecodeError:
        abort(500, f"Memory file for user '{user_key}' is invalid JSON.")


@app.before_request
def load_user_memory() -> None:
    if _is_memory_exempt_path(request.path):
        return

    if _TESTING_MODE:
        g.__dict__.clear()

    g.user_key = _resolve_request_user_key()
    g.user_folder = os.path.join("Memory", g.user_key)
    g.memory = _load_request_memory_profile(g.user_key)


@app.route("/")
def index():
    return redirect("/dashboard")


@app.route("/whoami")
def whoami():
    return jsonify(
        {
            "user": g.user_key,
            "profile": _summarize_memory(g.memory),
        }
    )


@app.route("/search")
def search():
    if not search_web:
        return error_response(SERVICE_UNAVAILABLE, "Web search plugin is not installed.", 503)

    query = request.args.get("q")
    if not query:
        return error_response(BAD_REQUEST, "Missing query", 400)

    try:
        result = search_web(query)
    except Exception as exc:
        app.logger.exception("Web search failed")
        return error_response(INTERNAL_ERROR, f"Search provider error: {exc}", 502)

    return jsonify({"query": query, "result": result})


@app.route("/contracts")
def contracts():
    """Return contract schema metadata for discoverability."""
    if not _CONTRACTS_AVAILABLE:
        return error_response(SERVICE_UNAVAILABLE, "Contracts module not available", 503)

    model_names = [model.__name__ for model in ALL_MODELS]
    return jsonify(
        {
            "contract_version": CONTRACT_VERSION,
            "schema_docs_path": "docs/contracts/",
            "models": model_names,
        }
    )


if __name__ == "__main__":
    app.run(host=os.getenv("REX_FLASK_PROXY_HOST", "127.0.0.1"), port=5000)
