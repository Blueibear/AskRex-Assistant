"""Tests for US-118: Request payload schema validation on POST/PUT endpoints.

Acceptance criteria:
- every POST and PUT endpoint declares a required schema
- requests with missing required fields return 400 with specific field name(s)
- requests with incorrect field types return 400 with a descriptive message
- validation runs before any business logic executes
- Typecheck passes
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from flask import Flask, g

from rex.validation import (
    ChatRequest,
    CreateJobRequest,
    LoginRequest,
    validate_json_body,
)

# ---------------------------------------------------------------------------
# Fixtures — minimal Flask app wired with validate_json_body
# ---------------------------------------------------------------------------


@pytest.fixture()
def app() -> Flask:
    """Minimal Flask app with three validated POST endpoints."""
    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True

    @flask_app.route("/chat", methods=["POST"])
    @validate_json_body(ChatRequest)
    def chat_endpoint() -> Any:
        body: ChatRequest = g.validated_body
        return {"message": body.message}, 200

    @flask_app.route("/login", methods=["POST"])
    @validate_json_body(LoginRequest)
    def login_endpoint() -> Any:
        body: LoginRequest = g.validated_body
        return {"password_len": len(body.password)}, 200

    @flask_app.route("/jobs", methods=["POST"])
    @validate_json_body(CreateJobRequest)
    def jobs_endpoint() -> Any:
        body: CreateJobRequest = g.validated_body
        return {"name": body.name, "schedule": body.schedule}, 200

    return flask_app


@pytest.fixture()
def client(app: Flask):  # type: ignore[type-arg]
    return app.test_client()


def _post_json(client: Any, path: str, body: dict) -> Any:
    return client.post(
        path,
        data=json.dumps(body),
        content_type="application/json",
    )


def _assert_400_with_field(resp: Any, field_name: str) -> None:
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["error"]["code"] == "BAD_REQUEST"
    assert field_name in data["error"]["message"]


# ---------------------------------------------------------------------------
# validate_json_body unit tests
# ---------------------------------------------------------------------------


class TestValidateJsonBodyDecorator:
    def test_missing_json_body_returns_400(self, client: Any) -> None:
        """Empty body (no Content-Type: application/json) returns 400."""
        resp = client.post("/chat", data="not json", content_type="text/plain")
        assert resp.status_code == 400

    def test_missing_json_body_error_envelope(self, client: Any) -> None:
        resp = client.post("/chat", data="", content_type="text/plain")
        data = resp.get_json()
        assert data["error"]["code"] == "BAD_REQUEST"

    def test_valid_body_calls_handler(self, client: Any) -> None:
        resp = _post_json(client, "/chat", {"message": "hello"})
        assert resp.status_code == 200
        assert resp.get_json()["message"] == "hello"

    def test_validated_body_stored_in_g(self, app: Flask) -> None:
        """g.validated_body contains the Pydantic model on success."""
        with app.test_client() as c:
            resp = _post_json(c, "/chat", {"message": "hi"})
        assert resp.status_code == 200

    def test_validation_before_business_logic(self, app: Flask) -> None:
        """Handler must not be called when validation fails."""
        handler_called = []

        inner = Flask(__name__)
        inner.config["TESTING"] = True

        @inner.route("/guarded", methods=["POST"])
        @validate_json_body(ChatRequest)
        def guarded() -> Any:
            handler_called.append(True)
            return {"ok": True}, 200

        with inner.test_client() as c:
            # Empty message should fail validation — handler must not run
            resp = _post_json(c, "/guarded", {"message": "   "})

        assert resp.status_code == 400
        assert not handler_called, "Handler must NOT be called when validation fails"


# ---------------------------------------------------------------------------
# ChatRequest schema
# ---------------------------------------------------------------------------


class TestChatRequestSchema:
    def test_valid_message(self, client: Any) -> None:
        resp = _post_json(client, "/chat", {"message": "What's the weather?"})
        assert resp.status_code == 200

    def test_missing_message_field_returns_400(self, client: Any) -> None:
        resp = _post_json(client, "/chat", {})
        _assert_400_with_field(resp, "message")

    def test_blank_message_returns_400(self, client: Any) -> None:
        """Whitespace-only message should fail validation."""
        resp = _post_json(client, "/chat", {"message": "   "})
        _assert_400_with_field(resp, "message")

    def test_non_string_message_returns_400(self, client: Any) -> None:
        resp = _post_json(client, "/chat", {"message": 42})
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["error"]["code"] == "BAD_REQUEST"

    def test_message_too_long_returns_400(self, client: Any) -> None:
        resp = _post_json(client, "/chat", {"message": "x" * 32_001})
        _assert_400_with_field(resp, "message")

    def test_400_message_names_field(self, client: Any) -> None:
        resp = _post_json(client, "/chat", {"message": ""})
        data = resp.get_json()
        assert "message" in data["error"]["message"]


# ---------------------------------------------------------------------------
# LoginRequest schema
# ---------------------------------------------------------------------------


class TestLoginRequestSchema:
    def test_valid_password(self, client: Any) -> None:
        resp = _post_json(client, "/login", {"password": "secret"})
        assert resp.status_code == 200

    def test_empty_password_accepted(self, client: Any) -> None:
        """Password defaults to '' — an empty string is allowed at schema level."""
        resp = _post_json(client, "/login", {})
        assert resp.status_code == 200

    def test_password_too_long_returns_400(self, client: Any) -> None:
        resp = _post_json(client, "/login", {"password": "x" * 1025})
        _assert_400_with_field(resp, "password")

    def test_non_string_password_returns_400(self, client: Any) -> None:
        resp = _post_json(client, "/login", {"password": 12345})
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["error"]["code"] == "BAD_REQUEST"

    def test_400_message_descriptive(self, client: Any) -> None:
        """400 message must describe the problem, not be a generic error."""
        resp = _post_json(client, "/login", {"password": "x" * 1025})
        data = resp.get_json()
        assert len(data["error"]["message"]) > 0


# ---------------------------------------------------------------------------
# CreateJobRequest schema
# ---------------------------------------------------------------------------


class TestCreateJobRequestSchema:
    def test_valid_minimal_job(self, client: Any) -> None:
        resp = _post_json(client, "/jobs", {"name": "My Job"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["name"] == "My Job"
        assert data["schedule"] == "interval:3600"  # default

    def test_missing_name_returns_400(self, client: Any) -> None:
        resp = _post_json(client, "/jobs", {"schedule": "interval:60"})
        _assert_400_with_field(resp, "name")

    def test_blank_name_returns_400(self, client: Any) -> None:
        resp = _post_json(client, "/jobs", {"name": "   "})
        _assert_400_with_field(resp, "name")

    def test_non_string_name_returns_400(self, client: Any) -> None:
        resp = _post_json(client, "/jobs", {"name": 999})
        assert resp.status_code == 400

    def test_name_too_long_returns_400(self, client: Any) -> None:
        resp = _post_json(client, "/jobs", {"name": "n" * 257})
        _assert_400_with_field(resp, "name")

    def test_non_bool_enabled_returns_400(self, client: Any) -> None:
        resp = _post_json(client, "/jobs", {"name": "job", "enabled": "yes"})
        assert resp.status_code == 400

    def test_non_dict_metadata_returns_400(self, client: Any) -> None:
        resp = _post_json(client, "/jobs", {"name": "job", "metadata": "bad"})
        assert resp.status_code == 400

    def test_defaults_applied(self, client: Any) -> None:
        resp = _post_json(client, "/jobs", {"name": "myjob"})
        data = resp.get_json()
        assert data["schedule"] == "interval:3600"

    def test_400_message_includes_field_name(self, client: Any) -> None:
        resp = _post_json(client, "/jobs", {})
        data = resp.get_json()
        assert "name" in data["error"]["message"]


# ---------------------------------------------------------------------------
# Pydantic model unit tests
# ---------------------------------------------------------------------------


class TestChatRequestModel:
    def test_valid(self) -> None:
        m = ChatRequest(message="hello")
        assert m.message == "hello"

    def test_blank_raises(self) -> None:
        with pytest.raises(Exception, match="message"):
            ChatRequest(message="   ")

    def test_too_long_raises(self) -> None:
        with pytest.raises(Exception, match="message"):
            ChatRequest(message="x" * 32_001)


class TestLoginRequestModel:
    def test_default_empty(self) -> None:
        m = LoginRequest()
        assert m.password == ""

    def test_valid_password(self) -> None:
        m = LoginRequest(password="secret")  # pragma: allowlist secret -- validation fixture
        assert m.password == "secret"

    def test_too_long_raises(self) -> None:
        with pytest.raises(Exception, match="password"):
            LoginRequest(password="x" * 1025)


class TestCreateJobRequestModel:
    def test_defaults(self) -> None:
        m = CreateJobRequest(name="test")
        assert m.schedule == "interval:3600"
        assert m.enabled is True
        assert m.metadata == {}

    def test_blank_name_raises(self) -> None:
        with pytest.raises(Exception, match="name"):
            CreateJobRequest(name="  ")

    def test_name_too_long_raises(self) -> None:
        with pytest.raises(Exception, match="name"):
            CreateJobRequest(name="n" * 257)
