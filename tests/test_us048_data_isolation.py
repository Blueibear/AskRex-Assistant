"""Tests for US-048: Per-user data isolation.

Covers:
- Memory profiles keyed by user ID
- Conversation history keyed by user ID
- Config preferences stored per user
- API requests require valid session token
- User A cannot see User B's data
"""

from __future__ import annotations

from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect auth, history, and memory storage to a temp directory."""
    monkeypatch.setenv("REX_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("REX_JWT_SECRET", "isolation-test-secret")
    return tmp_path


@pytest.fixture()
def flask_client(tmp_data_dir: Path):  # type: ignore[override]
    from rex.gui_app import _create_flask_app

    app = _create_flask_app(ui_enabled=False)
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def _register_and_login(client: object, username: str, password: str = "pass123") -> str:
    """Register a user, log in, and return the Bearer token.

    Passes the setup token because this helper always creates the first user.
    """
    setup_token = client.application.config.get("SETUP_TOKEN") or ""  # type: ignore[attr-defined]
    client.post(  # type: ignore[attr-defined]
        "/api/auth/register",
        json={"username": username, "password": password},
        headers={"X-Setup-Token": setup_token},
    )
    resp = client.post(  # type: ignore[attr-defined]
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    return resp.get_json()["token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Memory profile isolation
# ---------------------------------------------------------------------------


class TestMemoryProfilesKeyedByUser:
    def test_register_creates_memory_profile(
        self, flask_client: object, tmp_data_dir: Path
    ) -> None:
        """Registering a user creates a Memory profile under the user's ID."""
        setup_token = flask_client.application.config.get("SETUP_TOKEN") or ""  # type: ignore[attr-defined]
        resp = flask_client.post(  # type: ignore[attr-defined]
            "/api/auth/register",
            json={"username": "alice", "password": "secret"},  # pragma: allowlist secret
            headers={"X-Setup-Token": setup_token},
        )
        assert resp.status_code == 201
        user_id = resp.get_json()["id"]

        # Memory profile should exist at Memory/<user_id>/core.json
        from rex.identity import get_user_profile

        profile = get_user_profile(user_id)
        assert profile is not None
        assert profile["name"] == "alice"

    def test_two_users_have_separate_profiles(
        self, flask_client: object, tmp_data_dir: Path
    ) -> None:
        """Two different users get separate Memory profiles."""
        setup_token = flask_client.application.config.get("SETUP_TOKEN") or ""  # type: ignore[attr-defined]
        resp_a = flask_client.post(  # type: ignore[attr-defined]
            "/api/auth/register",
            json={"username": "alice", "password": "pass"},  # pragma: allowlist secret
            headers={"X-Setup-Token": setup_token},
        )
        # Second user: user_count > 0, no token required.
        resp_b = flask_client.post(  # type: ignore[attr-defined]
            "/api/auth/register",
            json={"username": "bob", "password": "pass"},  # pragma: allowlist secret -- fixture
        )
        id_a = resp_a.get_json()["id"]
        id_b = resp_b.get_json()["id"]

        from rex.identity import get_user_profile

        assert get_user_profile(id_a) is not None
        assert get_user_profile(id_b) is not None
        assert id_a != id_b


# ---------------------------------------------------------------------------
# Conversation history isolation
# ---------------------------------------------------------------------------


class TestConversationHistoryKeyedByUser:
    def test_history_is_empty_for_new_user(self, flask_client: object) -> None:
        """A newly registered user has no conversation history."""
        token = _register_and_login(flask_client, "charlie")
        resp = flask_client.get(  # type: ignore[attr-defined]
            "/api/chat/history", headers=_auth(token)
        )
        assert resp.status_code == 200
        assert resp.get_json() == []

    def test_user_a_cannot_see_user_b_history(self, flask_client: object) -> None:
        """Messages sent by User A are not visible to User B."""
        token_a = _register_and_login(flask_client, "alice")
        token_b = _register_and_login(flask_client, "bob")

        # Alice sends a message
        flask_client.post(  # type: ignore[attr-defined]
            "/api/chat/send",
            json={"message": "Alice's private message"},
            headers=_auth(token_a),
        )

        # Bob's history must be empty
        resp = flask_client.get(  # type: ignore[attr-defined]
            "/api/chat/history", headers=_auth(token_b)
        )
        bob_history = resp.get_json()
        contents = [m.get("content", "") for m in bob_history]
        assert "Alice's private message" not in contents

    def test_user_b_cannot_see_user_a_history(self, flask_client: object) -> None:
        """Messages sent by User B are not visible to User A."""
        token_a = _register_and_login(flask_client, "alice")
        token_b = _register_and_login(flask_client, "bob")

        # Bob sends a message
        flask_client.post(  # type: ignore[attr-defined]
            "/api/chat/send",
            json={"message": "Bob's secret"},
            headers=_auth(token_b),
        )

        # Alice's history must be empty
        resp = flask_client.get(  # type: ignore[attr-defined]
            "/api/chat/history", headers=_auth(token_a)
        )
        alice_history = resp.get_json()
        contents = [m.get("content", "") for m in alice_history]
        assert "Bob's secret" not in contents

    def test_clear_only_affects_requesting_user(self, flask_client: object) -> None:
        """Clearing history for User A does not affect User B's history."""
        token_a = _register_and_login(flask_client, "alice")
        token_b = _register_and_login(flask_client, "bob")

        # Both send messages
        flask_client.post(  # type: ignore[attr-defined]
            "/api/chat/send", json={"message": "Alice msg"}, headers=_auth(token_a)
        )
        flask_client.post(  # type: ignore[attr-defined]
            "/api/chat/send", json={"message": "Bob msg"}, headers=_auth(token_b)
        )

        # Alice clears her history
        flask_client.post("/api/chat/clear", headers=_auth(token_a))  # type: ignore[attr-defined]

        # Alice's history is empty
        alice_resp = flask_client.get("/api/chat/history", headers=_auth(token_a))  # type: ignore[attr-defined]
        assert alice_resp.get_json() == []

        # Bob's history is intact
        bob_resp = flask_client.get("/api/chat/history", headers=_auth(token_b))  # type: ignore[attr-defined]
        assert len(bob_resp.get_json()) > 0


# ---------------------------------------------------------------------------
# API auth enforcement
# ---------------------------------------------------------------------------


class TestApiRequiresToken:
    def test_chat_history_without_token_returns_401(self, flask_client: object) -> None:
        resp = flask_client.get("/api/chat/history")  # type: ignore[attr-defined]
        assert resp.status_code == 401

    def test_chat_send_without_token_returns_401(self, flask_client: object) -> None:
        resp = flask_client.post(  # type: ignore[attr-defined]
            "/api/chat/send", json={"message": "hi"}
        )
        assert resp.status_code == 401

    def test_chat_clear_without_token_returns_401(self, flask_client: object) -> None:
        resp = flask_client.post("/api/chat/clear")  # type: ignore[attr-defined]
        assert resp.status_code == 401

    def test_preferences_get_without_token_returns_401(self, flask_client: object) -> None:
        resp = flask_client.get("/api/user/preferences")  # type: ignore[attr-defined]
        assert resp.status_code == 401

    def test_preferences_patch_without_token_returns_401(self, flask_client: object) -> None:
        resp = flask_client.patch(  # type: ignore[attr-defined]
            "/api/user/preferences", json={"tts_voice": "en-US"}
        )
        assert resp.status_code == 401

    def test_invalid_token_returns_401(self, flask_client: object) -> None:
        resp = flask_client.get(  # type: ignore[attr-defined]
            "/api/chat/history",
            headers={"Authorization": "Bearer not.a.valid.jwt"},
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Per-user preferences
# ---------------------------------------------------------------------------


class TestPerUserPreferences:
    def test_preferences_empty_for_new_user(self, flask_client: object) -> None:
        token = _register_and_login(flask_client, "dave")
        resp = flask_client.get(  # type: ignore[attr-defined]
            "/api/user/preferences", headers=_auth(token)
        )
        assert resp.status_code == 200
        assert isinstance(resp.get_json(), dict)

    def test_patch_preferences_persists(self, flask_client: object) -> None:
        token = _register_and_login(flask_client, "eve")
        flask_client.patch(  # type: ignore[attr-defined]
            "/api/user/preferences",
            json={"tts_voice": "en-US-GuyNeural", "wake_word": "hey rex"},
            headers=_auth(token),
        )
        resp = flask_client.get(  # type: ignore[attr-defined]
            "/api/user/preferences", headers=_auth(token)
        )
        prefs = resp.get_json()
        assert prefs.get("tts_voice") == "en-US-GuyNeural"
        assert prefs.get("wake_word") == "hey rex"

    def test_preferences_scoped_per_user(self, flask_client: object) -> None:
        """User A's preferences do not bleed into User B's."""
        token_a = _register_and_login(flask_client, "alice")
        token_b = _register_and_login(flask_client, "bob")

        flask_client.patch(  # type: ignore[attr-defined]
            "/api/user/preferences",
            json={"tts_voice": "alice-voice"},
            headers=_auth(token_a),
        )

        resp_b = flask_client.get(  # type: ignore[attr-defined]
            "/api/user/preferences", headers=_auth(token_b)
        )
        assert resp_b.get_json().get("tts_voice") != "alice-voice"
