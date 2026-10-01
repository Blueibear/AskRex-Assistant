"""Tests for US-109: Request and response logging middleware.

Acceptance criteria:
- middleware logs each request: method, path, client IP (anonymized), timestamp
- middleware logs each response: status code, duration in milliseconds
- request and response log entries share a common request ID for correlation
- request body and response body are NOT logged by default
- Typecheck passes
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from flask import Flask, g, jsonify

from rex.request_logging import _anonymize_ip, _log_full_ip, install_request_logging

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app() -> Flask:
    """Create a minimal instrumented Flask app for testing."""
    app = Flask(__name__)
    install_request_logging(app)

    @app.route("/hello", methods=["GET"])
    def hello() -> Any:
        return jsonify({"ok": True}), 200

    @app.route("/echo", methods=["POST"])
    def echo() -> Any:
        return jsonify({"echoed": True}), 201

    @app.route("/fail", methods=["GET"])
    def fail() -> Any:
        return jsonify({"error": "bad"}), 500

    return app


# ---------------------------------------------------------------------------
# IP anonymisation
# ---------------------------------------------------------------------------


class TestAnonymizeIp:
    def test_ipv4_last_octet_replaced(self) -> None:
        assert _anonymize_ip("192.168.1.42") == "192.168.1.0"

    def test_ipv4_zeros_last_octet(self) -> None:
        assert _anonymize_ip("10.0.0.1") == "10.0.0.0"

    def test_ipv4_already_zero(self) -> None:
        assert _anonymize_ip("127.0.0.1") == "127.0.0.0"

    def test_ipv6_last_half_replaced(self) -> None:
        result = _anonymize_ip("2001:db8::1234")
        assert result.endswith(":0")

    def test_localhost_ipv4(self) -> None:
        assert _anonymize_ip("127.0.0.1") == "127.0.0.0"

    def test_empty_string_returns_unknown(self) -> None:
        assert _anonymize_ip("") == "unknown"

    def test_none_like_empty(self) -> None:
        # Empty string passed
        assert _anonymize_ip("") == "unknown"


# ---------------------------------------------------------------------------
# _log_full_ip
# ---------------------------------------------------------------------------


class TestLogFullIp:
    def test_false_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("REX_LOG_FULL_IP", raising=False)
        assert _log_full_ip() is False

    def test_true_when_env_is_1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("REX_LOG_FULL_IP", "1")
        assert _log_full_ip() is True

    def test_true_when_env_is_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("REX_LOG_FULL_IP", "true")
        assert _log_full_ip() is True

    def test_false_when_env_is_0(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("REX_LOG_FULL_IP", "0")
        assert _log_full_ip() is False


# ---------------------------------------------------------------------------
# Request logging
# ---------------------------------------------------------------------------


class TestRequestLogging:
    @pytest.fixture()
    def client(self) -> Any:
        app = _make_app()
        app.config["TESTING"] = True
        return app.test_client()

    def test_request_logged_on_get(self, client: Any) -> None:
        with patch("rex.request_logging.logger") as mock_logger:
            client.get("/hello", environ_base={"REMOTE_ADDR": "10.0.0.5"})
        # Find the REQUEST call
        request_calls = [c for c in mock_logger.info.call_args_list if "REQUEST" in str(c)]
        assert len(request_calls) >= 1

    def test_request_log_includes_method(self, client: Any) -> None:
        with patch("rex.request_logging.logger") as mock_logger:
            client.get("/hello", environ_base={"REMOTE_ADDR": "10.0.0.1"})
        all_logged = " ".join(str(c) for c in mock_logger.info.call_args_list)
        assert "GET" in all_logged

    def test_request_log_includes_path(self, client: Any) -> None:
        with patch("rex.request_logging.logger") as mock_logger:
            client.get("/hello", environ_base={"REMOTE_ADDR": "10.0.0.1"})
        all_logged = " ".join(str(c) for c in mock_logger.info.call_args_list)
        assert "/hello" in all_logged

    def test_request_log_anonymizes_ip_by_default(self, client: Any) -> None:
        with (
            patch("rex.request_logging.logger") as mock_logger,
            patch("rex.request_logging._log_full_ip", return_value=False),
        ):
            client.get("/hello", environ_base={"REMOTE_ADDR": "192.168.1.99"})
        all_logged = " ".join(str(c) for c in mock_logger.info.call_args_list)
        # Anonymized: last octet is 0
        assert "192.168.1.0" in all_logged
        # Full IP must not appear
        assert "192.168.1.99" not in all_logged

    def test_request_log_uses_full_ip_when_configured(self, client: Any) -> None:
        with (
            patch("rex.request_logging.logger") as mock_logger,
            patch("rex.request_logging._log_full_ip", return_value=True),
        ):
            client.get("/hello", environ_base={"REMOTE_ADDR": "192.168.1.99"})
        all_logged = " ".join(str(c) for c in mock_logger.info.call_args_list)
        assert "192.168.1.99" in all_logged

    def test_post_request_logged(self, client: Any) -> None:
        with patch("rex.request_logging.logger") as mock_logger:
            client.post(
                "/echo",
                json={
                    "secret": "should-not-appear"  # pragma: allowlist secret -- redaction fixture
                },
                environ_base={"REMOTE_ADDR": "10.0.0.1"},
            )
        all_logged = " ".join(str(c) for c in mock_logger.info.call_args_list)
        assert "POST" in all_logged
        assert "/echo" in all_logged


# ---------------------------------------------------------------------------
# Response logging
# ---------------------------------------------------------------------------


class TestResponseLogging:
    @pytest.fixture()
    def client(self) -> Any:
        app = _make_app()
        app.config["TESTING"] = True
        return app.test_client()

    def test_response_logged_on_get(self, client: Any) -> None:
        with patch("rex.request_logging.logger") as mock_logger:
            client.get("/hello", environ_base={"REMOTE_ADDR": "10.0.0.1"})
        response_calls = [c for c in mock_logger.info.call_args_list if "RESPONSE" in str(c)]
        assert len(response_calls) >= 1

    def test_response_log_includes_status_code(self, client: Any) -> None:
        with patch("rex.request_logging.logger") as mock_logger:
            client.get("/hello", environ_base={"REMOTE_ADDR": "10.0.0.1"})
        all_logged = " ".join(str(c) for c in mock_logger.info.call_args_list)
        assert "200" in all_logged

    def test_response_log_500_status(self, client: Any) -> None:
        with patch("rex.request_logging.logger") as mock_logger:
            client.get("/fail", environ_base={"REMOTE_ADDR": "10.0.0.1"})
        all_logged = " ".join(str(c) for c in mock_logger.info.call_args_list)
        assert "500" in all_logged

    def test_response_log_includes_duration_ms(self, client: Any) -> None:
        with patch("rex.request_logging.logger") as mock_logger:
            client.get("/hello", environ_base={"REMOTE_ADDR": "10.0.0.1"})
        # Find RESPONSE call and check 'ms' is mentioned
        response_calls = [str(c) for c in mock_logger.info.call_args_list if "RESPONSE" in str(c)]
        assert any("ms" in c for c in response_calls)

    def test_response_log_duration_is_non_negative(self, client: Any) -> None:
        """Duration should be a non-negative integer."""
        captured_extra: list[dict] = []

        def fake_info(msg: str, *args: Any, **kwargs: Any) -> None:
            extra = kwargs.get("extra", {})
            if "duration_ms" in extra:
                captured_extra.append(extra)

        with patch("rex.request_logging.logger") as mock_logger:
            mock_logger.info.side_effect = fake_info
            client.get("/hello", environ_base={"REMOTE_ADDR": "10.0.0.1"})

        assert any(e["duration_ms"] >= 0 for e in captured_extra)


# ---------------------------------------------------------------------------
# Request ID correlation
# ---------------------------------------------------------------------------


class TestRequestIdCorrelation:
    @pytest.fixture()
    def app_instance(self) -> Flask:
        return _make_app()

    def test_request_id_set_in_g(self, app_instance: Flask) -> None:
        """g.request_id is set during request handling."""
        captured: list[str] = []

        @app_instance.route("/capture")
        def _capture() -> str:

            captured.append(getattr(g, "request_id", ""))
            return "ok"

        app_instance.config["TESTING"] = True
        client = app_instance.test_client()
        client.get("/capture", environ_base={"REMOTE_ADDR": "10.0.0.1"})
        assert len(captured) == 1
        assert captured[0]  # non-empty UUID

    def test_request_id_shared_between_request_and_response_logs(self, app_instance: Flask) -> None:
        """Both request and response log entries must include the same request_id."""
        captured_extras: list[dict] = []

        def fake_info(msg: str, *args: Any, **kwargs: Any) -> None:
            extra = kwargs.get("extra", {})
            if "request_id" in extra:
                captured_extras.append(dict(extra))

        app_instance.config["TESTING"] = True
        with patch("rex.request_logging.logger") as mock_logger:
            mock_logger.info.side_effect = fake_info
            with app_instance.test_client() as client:
                client.get("/hello", environ_base={"REMOTE_ADDR": "10.0.0.1"})

        assert len(captured_extras) == 2
        ids = [e["request_id"] for e in captured_extras]
        assert ids[0] == ids[1], "Request and response must share the same request_id"

    def test_request_id_is_uuid_format(self, app_instance: Flask) -> None:
        """request_id must be a UUID string."""
        import re

        uuid_re = re.compile(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
        )
        captured_extras: list[dict] = []

        def fake_info(msg: str, *args: Any, **kwargs: Any) -> None:
            extra = kwargs.get("extra", {})
            if "request_id" in extra:
                captured_extras.append(dict(extra))

        app_instance.config["TESTING"] = True
        with patch("rex.request_logging.logger") as mock_logger:
            mock_logger.info.side_effect = fake_info
            with app_instance.test_client() as client:
                client.get("/hello", environ_base={"REMOTE_ADDR": "10.0.0.1"})

        assert captured_extras, "No log entries captured"
        for entry in captured_extras:
            assert uuid_re.match(
                entry["request_id"]
            ), f"request_id {entry['request_id']!r} is not a valid UUID4"

    def test_different_requests_get_different_ids(self, app_instance: Flask) -> None:
        request_ids: list[str] = []

        def fake_info(msg: str, *args: Any, **kwargs: Any) -> None:
            extra = kwargs.get("extra", {})
            if "REQUEST" in msg and "request_id" in extra:
                request_ids.append(extra["request_id"])

        app_instance.config["TESTING"] = True
        with patch("rex.request_logging.logger") as mock_logger:
            mock_logger.info.side_effect = fake_info
            with app_instance.test_client() as client:
                client.get("/hello", environ_base={"REMOTE_ADDR": "10.0.0.1"})
                client.get("/hello", environ_base={"REMOTE_ADDR": "10.0.0.1"})

        assert len(request_ids) == 2
        assert request_ids[0] != request_ids[1]


# ---------------------------------------------------------------------------
# Body NOT logged
# ---------------------------------------------------------------------------


class TestBodyNotLogged:
    @pytest.fixture()
    def client(self) -> Any:
        app = _make_app()
        app.config["TESTING"] = True
        return app.test_client()

    def test_request_body_not_in_logs(self, client: Any) -> None:
        secret_payload = '{"password": "super-secret-value-12345"}'  # pragma: allowlist secret -- redaction fixture
        with patch("rex.request_logging.logger") as mock_logger:
            client.post(
                "/echo",
                data=secret_payload,
                content_type="application/json",
                environ_base={"REMOTE_ADDR": "10.0.0.1"},
            )
        all_logged = " ".join(str(c) for c in mock_logger.info.call_args_list)
        assert "super-secret-value-12345" not in all_logged
        assert "password" not in all_logged

    def test_response_body_not_in_logs(self, client: Any) -> None:
        with patch("rex.request_logging.logger") as mock_logger:
            client.get("/hello", environ_base={"REMOTE_ADDR": "10.0.0.1"})
        all_logged = " ".join(str(c) for c in mock_logger.info.call_args_list)
        # The response body is {"ok": true} — "ok" should not appear in logs
        # (it would appear if body were logged)
        assert '"ok"' not in all_logged


# ---------------------------------------------------------------------------
# install_request_logging integration
# ---------------------------------------------------------------------------


class TestInstallRequestLogging:
    def test_install_adds_before_request_hook(self) -> None:
        app = Flask(__name__)
        install_request_logging(app)
        # Flask stores before_request functions by name
        func_names = {f.__name__ for f in app.before_request_funcs.get(None, [])}
        assert "_log_request" in func_names

    def test_install_adds_after_request_hook(self) -> None:
        app = Flask(__name__)
        install_request_logging(app)
        func_names = {f.__name__ for f in app.after_request_funcs.get(None, [])}
        assert "_log_response" in func_names

    def test_idempotent_install(self) -> None:
        """Calling install twice should not duplicate hooks."""
        app = Flask(__name__)
        install_request_logging(app)
        install_request_logging(app)
        before_names = [f.__name__ for f in app.before_request_funcs.get(None, [])]
        assert before_names.count("_log_request") == 1
        after_names = [f.__name__ for f in app.after_request_funcs.get(None, [])]
        assert after_names.count("_log_response") == 1
