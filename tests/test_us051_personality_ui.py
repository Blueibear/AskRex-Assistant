"""US-051: Personality preview and selection UI.

Tests the Flask backend personalities endpoint and the preferences-based
personality update path. The TypeScript UI changes are verified manually.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def flask_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Flask test client with temp data dir."""
    monkeypatch.setenv("REX_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("REX_JWT_SECRET", "test-secret-key-for-us051")
    from rex.gui_app import _create_flask_app

    app = _create_flask_app(ui_enabled=False)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture()
def auth_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Flask test client with a registered user and auth token."""
    monkeypatch.setenv("REX_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("REX_JWT_SECRET", "test-secret-key-for-us051")
    from rex.gui_app import _create_flask_app

    app = _create_flask_app(ui_enabled=False)
    app.config["TESTING"] = True
    with app.test_client() as c:
        setup_token = app.config.get("SETUP_TOKEN") or ""
        c.post(
            "/api/auth/register",
            json={
                "username": "testuser",
                "password": "Password1!",  # pragma: allowlist secret -- registration fixture
            },
            headers={"X-Setup-Token": setup_token},
        )
        login = c.post(
            "/api/auth/login",
            json={"username": "testuser", "password": "Password1!"},
        )
        token = login.get_json()["token"]
        yield c, token


def test_list_personalities_returns_all(flask_client):
    """GET /api/personalities lists all built-in personalities."""
    resp = flask_client.get("/api/personalities")
    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, list)
    names = [p["name"] for p in data]
    assert "Friendly" in names
    assert "Professional" in names
    assert "Minimal" in names


def test_list_personalities_has_greeting(flask_client):
    """Each personality entry includes a greeting for preview."""
    resp = flask_client.get("/api/personalities")
    data = resp.get_json()
    for p in data:
        assert "greeting" in p
        assert isinstance(p["greeting"], str)
        assert len(p["greeting"]) > 0


def test_list_personalities_has_tone_keywords(flask_client):
    """Each personality entry includes tone keywords."""
    resp = flask_client.get("/api/personalities")
    data = resp.get_json()
    for p in data:
        assert "tone_keywords" in p
        assert isinstance(p["tone_keywords"], list)
        assert len(p["tone_keywords"]) > 0


def test_list_personalities_no_auth_required(flask_client):
    """GET /api/personalities is publicly accessible (no token needed)."""
    resp = flask_client.get("/api/personalities")
    assert resp.status_code == 200


def test_set_personality_via_preferences(auth_client):
    """PATCH /api/user/preferences with personality stores the value."""
    c, token = auth_client
    headers = {"Authorization": f"Bearer {token}"}

    resp = c.patch(
        "/api/user/preferences",
        json={"personality": "Professional"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True

    resp2 = c.get("/api/user/preferences", headers=headers)
    assert resp2.status_code == 200
    prefs = resp2.get_json()
    assert prefs.get("personality") == "Professional"


def test_change_personality_takes_effect_on_next_read(auth_client):
    """Changing personality via preferences is immediately visible on next GET."""
    c, token = auth_client
    headers = {"Authorization": f"Bearer {token}"}

    for name in ("Friendly", "Minimal", "Professional"):
        c.patch("/api/user/preferences", json={"personality": name}, headers=headers)
        resp = c.get("/api/user/preferences", headers=headers)
        assert resp.get_json().get("personality") == name
