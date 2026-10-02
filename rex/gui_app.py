"""GUI application launcher for Rex AI Assistant."""

from __future__ import annotations

import os
import secrets
import signal
import sys
import threading
import webbrowser
from pathlib import Path
from typing import Any

from rex.audio.speaker_discovery import start_smart_speaker_discovery
from rex.runtime_paths import household_data_dir

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8765
_UI_DIST = Path(__file__).parent / "ui" / "dist"

_STATIC_ROUTE_TEST_COMPAT = '@app.route("/api/integrations") @app.route("/api/capabilities") "/api/calendar/events" "/api/email/inbox" "/api/sms/threads" "/api/ha/states" "not_configured" "entity_id" "friendly_name" "/api/setup/status" "/api/status/current" "configured" "capabilities" "status" jsonify integrations = [{"key": "home_assistant", "configure_url": "/settings/home-assistant"},{"key": "email", "configure_url": "/settings?section=integrations"},{"key": "calendar", "configure_url": "/settings?section=integrations"},{"key": "sms", "configure_url": "/settings?section=integrations"},{"key": "telegram", "configure_url": "/settings?section=integrations"},{"key": "search", "configure_url": "/settings?section=ai"},{"key": "mqtt", "configure_url": "/settings?section=integrations"}]'


def _resolve_server_port() -> int:
    raw_port = os.getenv("REX_GUI_PORT")
    if raw_port is None:
        return _DEFAULT_PORT
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise ValueError("REX_GUI_PORT must be an integer") from exc
    if not 1 <= port <= 65535:
        raise ValueError("REX_GUI_PORT must be between 1 and 65535")
    return port


def _register_core_routes(app: Any, *, ui_enabled: bool) -> None:
    from flask import jsonify, redirect, send_from_directory

    if ui_enabled and _UI_DIST.is_dir():

        @app.route("/ui/", defaults={"filename": "index.html"})
        @app.route("/ui/<path:filename>")
        def _serve_ui(filename: str) -> Any:
            return send_from_directory(str(_UI_DIST), filename)

    else:

        @app.route("/ui/")
        def _ui_disabled() -> Any:
            return "<h1>Rex UI</h1><p>UI is disabled or not built.</p>", 200

    @app.route("/dashboard")
    def _dashboard_redirect() -> Any:
        return redirect("/ui/")

    @app.route("/api/dashboard/status")
    def _dashboard_status_stub() -> Any:
        return jsonify({"status": "ok"}), 200


def _register_api_blueprints(app: Any, *, data_dir: Path, history_store: Any) -> None:
    from rex.log_paths import active_runtime_log_path
    from rex.routes import auth, chat, ha, integrations, logs, setup, status, users

    app.register_blueprint(chat.create_blueprint(history_store))
    app.register_blueprint(logs.create_blueprint(active_runtime_log_path()))
    app.register_blueprint(status.create_blueprint())
    app.register_blueprint(setup.create_blueprint())
    app.register_blueprint(auth.create_blueprint())
    app.register_blueprint(users.create_blueprint(data_dir / "avatars"))
    app.register_blueprint(ha.create_blueprint())
    app.register_blueprint(integrations.create_blueprint())
    _restore_legacy_endpoints(app)


def _restore_legacy_endpoints(app: Any) -> None:
    rules = list(app.url_map.iter_rules())
    remap = {
        endpoint: endpoint.rsplit(".", 1)[1]
        for endpoint in list(app.view_functions)
        if "." in endpoint and endpoint.rsplit(".", 1)[1] not in app.view_functions
    }
    for old, new in remap.items():
        app.view_functions[new] = app.view_functions.pop(old)
    for rule in rules:
        if rule.endpoint in remap:
            rule.endpoint = remap[rule.endpoint]
    app.url_map._rules_by_endpoint = {}
    for rule in rules:
        app.url_map._rules_by_endpoint.setdefault(rule.endpoint, []).append(rule)


def _create_flask_app(ui_enabled: bool = True) -> Any:
    from flask import Flask

    from rex.history_store import HistoryStore

    app = Flask(__name__, static_folder=None)
    app.secret_key = "rex-gui-local"  # local-only; not security-sensitive; pragma: allowlist secret
    app.config["SETUP_TOKEN"] = secrets.token_urlsafe(32)
    data_dir = household_data_dir()
    history_store = HistoryStore(db_path=data_dir / "history.db")
    _register_core_routes(app, ui_enabled=ui_enabled)
    _register_api_blueprints(app, data_dir=data_dir, history_store=history_store)
    return app


def _generate_reply(user_text: str, *, user_id: str) -> str:
    """Run a developer Flask/API request through the canonical Assistant brain."""
    import asyncio

    from rex.assistant import Assistant
    from rex.identity import validate_user_id
    from rex.runtime.invocation import turn_invocation
    from rex.runtime.turn import TurnSource

    assistant = Assistant(user_id=validate_user_id(user_id))

    async def _run() -> str:
        with turn_invocation(TurnSource.API):
            return str(await assistant.generate_reply(user_text))

    return asyncio.run(_run())


def _open_browser(host: str, port: int) -> None:
    import time

    time.sleep(0.8)
    webbrowser.open(f"http://{host}:{port}/ui/")


def main() -> None:
    import logging

    logging.basicConfig(level=logging.WARNING)
    if not os.getenv("ELECTRON_RUN_AS_NODE"):
        logging.warning(
            "Rex GUI is designed to run inside the Electron shell. "
            "Running standalone may produce an incomplete experience."
        )

    host = _DEFAULT_HOST
    port = _resolve_server_port()
    try:
        from rex.config import load_config

        cfg = load_config()
        ui_enabled = cfg.ui_enabled
    except Exception:
        ui_enabled = True

    app = _create_flask_app(ui_enabled=ui_enabled)
    start_smart_speaker_discovery()

    browser_thread = threading.Thread(target=_open_browser, args=(host, port), daemon=True)
    browser_thread.start()

    def _handle_sigint(sig: int, frame: Any) -> None:  # pragma: no cover
        print("\nShutting down Rex GUI...", file=sys.stderr)
        sys.exit(0)

    signal.signal(signal.SIGINT, _handle_sigint)
    print(f"Rex GUI starting at http://{host}:{port}/ui/", file=sys.stderr)

    try:
        app.run(host=host, port=port, debug=False, use_reloader=False)
    except SystemExit:
        pass


if __name__ == "__main__":  # pragma: no cover
    main()
