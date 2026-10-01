"""Tests for the Rex GUI chat API endpoints (US-UI-002)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture()
def tmp_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point auth and history storage at a temp directory."""
    monkeypatch.setenv("REX_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("REX_JWT_SECRET", "test-secret-key")
    return tmp_path


@pytest.fixture()
def flask_client(tmp_data_dir: Path):  # type: ignore[override]
    from rex.gui_app import _create_flask_app

    app = _create_flask_app(ui_enabled=False)
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


@pytest.fixture()
def auth_header(flask_client: object) -> dict[str, str]:
    """Register + login a test user and return the Authorization header."""
    setup_token = flask_client.application.config.get("SETUP_TOKEN") or ""  # type: ignore[attr-defined]
    flask_client.post(  # type: ignore[attr-defined]
        "/api/auth/register",
        json={
            "username": "testuser",
            "password": "s3cr3t",  # pragma: allowlist secret -- registration fixture
        },
        headers={"X-Setup-Token": setup_token},
    )
    resp = flask_client.post(  # type: ignore[attr-defined]
        "/api/auth/login",
        json={"username": "testuser", "password": "s3cr3t"},  # pragma: allowlist secret
    )
    token = resp.get_json()["token"]
    return {"Authorization": f"Bearer {token}"}


def test_chat_history_empty(flask_client: object, auth_header: dict) -> None:
    """GET /api/chat/history returns an empty list initially."""
    resp = flask_client.get("/api/chat/history", headers=auth_header)  # type: ignore[attr-defined]
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert isinstance(data, list)


def test_chat_history_requires_auth(flask_client: object) -> None:
    """GET /api/chat/history returns 401 without a token."""
    resp = flask_client.get("/api/chat/history")  # type: ignore[attr-defined]
    assert resp.status_code == 401


def test_chat_send_requires_message(flask_client: object, auth_header: dict) -> None:
    """POST /api/chat/send with empty message returns 400."""
    resp = flask_client.post(  # type: ignore[attr-defined]
        "/api/chat/send",
        data=json.dumps({"message": ""}),
        content_type="application/json",
        headers=auth_header,
    )
    assert resp.status_code == 400


def test_chat_send_requires_auth(flask_client: object) -> None:
    """POST /api/chat/send returns 401 without a token."""
    resp = flask_client.post(  # type: ignore[attr-defined]
        "/api/chat/send",
        data=json.dumps({"message": "hello"}),
        content_type="application/json",
    )
    assert resp.status_code == 401


def test_chat_send_stores_user_message(flask_client: object, auth_header: dict) -> None:
    """POST /api/chat/send adds the user message to history."""
    resp = flask_client.post(  # type: ignore[attr-defined]
        "/api/chat/send",
        data=json.dumps({"message": "hello rex"}),
        content_type="application/json",
        headers=auth_header,
    )
    assert resp.status_code == 200
    body = b"".join(resp.response).decode()
    assert "data:" in body

    history_resp = flask_client.get("/api/chat/history", headers=auth_header)  # type: ignore[attr-defined]
    history = json.loads(history_resp.data)
    roles = [m["role"] for m in history]
    assert "user" in roles
    assert "assistant" in roles


def test_chat_send_sse_content_type(flask_client: object, auth_header: dict) -> None:
    """POST /api/chat/send returns text/event-stream content type."""
    resp = flask_client.post(  # type: ignore[attr-defined]
        "/api/chat/send",
        data=json.dumps({"message": "ping"}),
        content_type="application/json",
        headers=auth_header,
    )
    assert resp.status_code == 200
    assert "text/event-stream" in resp.content_type


def test_chat_clear(flask_client: object, auth_header: dict) -> None:
    """POST /api/chat/clear empties the user's history."""
    flask_client.post(  # type: ignore[attr-defined]
        "/api/chat/send",
        data=json.dumps({"message": "test message"}),
        content_type="application/json",
        headers=auth_header,
    )
    resp = flask_client.post("/api/chat/clear", headers=auth_header)  # type: ignore[attr-defined]
    assert resp.status_code == 200

    history_resp = flask_client.get("/api/chat/history", headers=auth_header)  # type: ignore[attr-defined]
    assert json.loads(history_resp.data) == []


def test_chat_clear_requires_auth(flask_client: object) -> None:
    """POST /api/chat/clear returns 401 without a token."""
    resp = flask_client.post("/api/chat/clear")  # type: ignore[attr-defined]
    assert resp.status_code == 401


def test_history_store_save_and_clear(tmp_path: Path) -> None:
    """Unit test: history_store save_turn / clear_history."""
    from datetime import UTC, datetime

    from rex.history_store import HistoryStore

    store = HistoryStore(tmp_path / "history.db")
    store.save_turn("user-1", "user", "hello", datetime.now(UTC))

    history = store.load_history("user-1")
    assert len(history) == 1
    assert history[0]["role"] == "user"
    assert history[0]["content"] == "hello"

    store.clear_history("user-1")
    assert store.load_history("user-1") == []
