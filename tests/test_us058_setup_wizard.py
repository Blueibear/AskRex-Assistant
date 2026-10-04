"""Tests for US-058: First-run setup wizard API.

Covers:
- GET /api/setup/status returns needs_setup=True when no users exist
- GET /api/setup/status returns needs_setup=False after a user exists
- POST /api/setup/complete creates user and returns ok
- POST /api/setup/complete rejects missing username/password
- POST /api/setup/complete returns 409 if setup already done
- POST /api/setup/complete delegates secrets to transactional vault persistence
- POST /api/setup/complete writes non-secret settings to rex_config.json
- persistence failure rolls back the newly-created user and setup authority
"""

from __future__ import annotations

from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("REX_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("REX_JWT_SECRET", "test-us058-secret")
    return tmp_path


@pytest.fixture()
def flask_client(tmp_data_dir: Path):  # type: ignore[override]
    from rex.gui_app import _create_flask_app

    app = _create_flask_app(ui_enabled=False)
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def _setup_token(client: object) -> str:
    """Return the single-use setup token from the Flask app config."""
    return client.application.config.get("SETUP_TOKEN") or ""  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# GET /api/setup/status
# ---------------------------------------------------------------------------


class TestSetupStatus:
    def test_needs_setup_true_when_no_users(self, flask_client) -> None:  # type: ignore[override]
        resp = flask_client.get("/api/setup/status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["needs_setup"] is True

    def test_needs_setup_false_after_registration(self, flask_client) -> None:  # type: ignore[override]
        flask_client.post(
            "/api/auth/register",
            json={
                "username": "alice",
                "password": "pass1234",  # pragma: allowlist secret -- registration fixture
            },
            headers={"X-Setup-Token": _setup_token(flask_client)},
        )
        resp = flask_client.get("/api/setup/status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["needs_setup"] is False

    def test_no_auth_required(self, flask_client) -> None:  # type: ignore[override]
        resp = flask_client.get("/api/setup/status")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# POST /api/setup/complete
# ---------------------------------------------------------------------------


class TestSetupComplete:
    def test_creates_user_returns_ok(
        self, flask_client, tmp_data_dir: Path
    ) -> None:  # type: ignore[override]
        resp = flask_client.post(
            "/api/setup/complete",
            json={
                "username": "bob",
                "password": "securepass1",  # pragma: allowlist secret -- registration fixture
                "llm_provider": "local",
                "tts_provider": "none",
            },
            headers={"X-Setup-Token": _setup_token(flask_client)},
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["ok"] is True
        assert "user_id" in data

    def test_missing_username_returns_400(self, flask_client) -> None:  # type: ignore[override]
        resp = flask_client.post(
            "/api/setup/complete",
            json={"password": "securepass1"},  # pragma: allowlist secret -- validation fixture
            headers={"X-Setup-Token": _setup_token(flask_client)},
        )
        assert resp.status_code == 400

    def test_missing_password_returns_400(self, flask_client) -> None:  # type: ignore[override]
        resp = flask_client.post(
            "/api/setup/complete",
            json={"username": "bob"},
            headers={"X-Setup-Token": _setup_token(flask_client)},
        )
        assert resp.status_code == 400

    def test_second_setup_attempt_returns_403(self, flask_client) -> None:  # type: ignore[override]
        """After setup completes the token is consumed; re-running returns 403."""
        payload = {
            "username": "carol",
            "password": "securepass1",  # pragma: allowlist secret
            "llm_provider": "local",
            "tts_provider": "none",
        }
        flask_client.post(
            "/api/setup/complete",
            json=payload,
            headers={"X-Setup-Token": _setup_token(flask_client)},
        )
        resp = flask_client.post("/api/setup/complete", json=payload)
        assert resp.status_code == 403

    def test_sets_needs_setup_to_false(self, flask_client) -> None:  # type: ignore[override]
        flask_client.post(
            "/api/setup/complete",
            json={
                "username": "dave",
                "password": "securepass1",  # pragma: allowlist secret -- fixture
                "llm_provider": "local",
                "tts_provider": "none",
            },
            headers={"X-Setup-Token": _setup_token(flask_client)},
        )
        status_resp = flask_client.get("/api/setup/status")
        assert status_resp.get_json()["needs_setup"] is False

    def test_delegates_openai_key_to_secure_persistence(
        self, flask_client, monkeypatch: pytest.MonkeyPatch
    ) -> None:  # type: ignore[override]
        captured: dict[str, object] = {}

        def fake_persist(values, *, config_path=None, update_config=None):
            captured["values"] = dict(values)
            config: dict[str, object] = {}
            if update_config is not None:
                update_config(config)
            captured["config"] = config
            return {"OPENAI_API_KEY": "cred_" + "A" * 32}  # pragma: allowlist secret

        monkeypatch.setattr("rex.credential_persistence.persist_household_secrets", fake_persist)

        flask_client.post(
            "/api/setup/complete",
            json={
                "username": "eve",
                "password": "securepass1",  # pragma: allowlist secret -- fixture
                "llm_provider": "openai",
                "llm_api_key": "sk-test-key",  # pragma: allowlist secret
                "tts_provider": "none",
            },
            headers={"X-Setup-Token": _setup_token(flask_client)},
        )

        assert captured["values"] == {
            "OPENAI_API_KEY": "sk-test-key",  # pragma: allowlist secret -- environment fixture
        }
        assert captured["config"] == {
            "llm": {"provider": "openai"},
            "tts_provider": "none",
        }

    def test_secure_persistence_failure_rolls_back_user_and_does_not_consume_token(
        self, flask_client, monkeypatch: pytest.MonkeyPatch
    ) -> None:  # type: ignore[override]
        def fail(*_args, **_kwargs):
            raise RuntimeError("vault unavailable")

        monkeypatch.setattr("rex.credential_persistence.persist_household_secrets", fail)
        setup_token = _setup_token(flask_client)
        response = flask_client.post(
            "/api/setup/complete",
            json={
                "username": "rollback-user",
                "password": "securepass1",  # pragma: allowlist secret -- fixture
                "llm_provider": "openai",
                "llm_api_key": "secret-marker",  # pragma: allowlist secret
            },
            headers={"X-Setup-Token": setup_token},
        )
        assert response.status_code == 500
        assert "secret-marker" not in response.get_data(as_text=True)
        assert flask_client.get("/api/setup/status").get_json()["needs_setup"] is True
        assert flask_client.application.config["SETUP_TOKEN"] == setup_token

    def test_deferred_home_assistant_ignores_supplied_ha_values(
        self, flask_client, monkeypatch: pytest.MonkeyPatch
    ) -> None:  # type: ignore[override]
        captured: dict[str, object] = {}

        def fake_persist(values, *, config_path=None, update_config=None):
            captured["values"] = dict(values)
            config: dict[str, object] = {}
            if update_config is not None:
                update_config(config)
            captured["config"] = config
            return {}

        monkeypatch.setattr("rex.credential_persistence.persist_household_secrets", fake_persist)

        flask_client.post(
            "/api/setup/complete",
            json={
                "username": "frank",
                "password": "securepass1",  # pragma: allowlist secret -- registration fixture
                "llm_provider": "local",
                "tts_provider": "none",
                "ha_base_url": "http://ha.local:8123",
                "ha_token": "should-be-ignored",  # pragma: allowlist secret -- ignored fixture value
                "defer_home_assistant": True,
            },
            headers={"X-Setup-Token": _setup_token(flask_client)},
        )

        assert captured["values"] == {}
        assert "home_assistant" not in captured["config"]

    def test_defer_field_parsed_strictly_as_boolean(
        self, flask_client, monkeypatch: pytest.MonkeyPatch
    ) -> None:  # type: ignore[override]
        """String 'false' must not be treated as true; only bool True defers."""
        captured: dict[str, object] = {}

        def fake_persist(values, *, config_path=None, update_config=None):
            captured["values"] = dict(values)
            config: dict[str, object] = {}
            if update_config is not None:
                update_config(config)
            captured["config"] = config
            return {}

        monkeypatch.setattr("rex.credential_persistence.persist_household_secrets", fake_persist)

        # String "false" should NOT defer
        flask_client.post(
            "/api/setup/complete",
            json={
                "username": "george",
                "password": "securepass1",  # pragma: allowlist secret -- registration fixture
                "llm_provider": "local",
                "tts_provider": "none",
                "ha_base_url": "http://ha.local:8123",
                "ha_token": "test-token",
                "defer_home_assistant": "false",
            },
            headers={"X-Setup-Token": _setup_token(flask_client)},
        )

        # When not deferred, HA values should be persisted
        assert captured["values"] == {"HA_TOKEN": "test-token"}
        assert captured["config"].get("home_assistant", {}).get("base_url") == "http://ha.local:8123"  # type: ignore[union-attr]
