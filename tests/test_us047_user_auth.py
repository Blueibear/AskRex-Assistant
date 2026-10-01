"""Tests for US-047: User authentication (login system).

Covers:
- User registration (create_user)
- Login / bad password (authenticate)
- Token validation (get_current_user)
- Flask API endpoints: /api/auth/register, /api/auth/login, /api/auth/logout
"""

from __future__ import annotations

from datetime import UTC
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point auth at a temp directory so tests don't touch real data."""
    monkeypatch.setenv("REX_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("REX_JWT_SECRET", "test-secret-key")
    return tmp_path


@pytest.fixture()
def flask_client(tmp_data_dir: Path):  # type: ignore[override]
    """Return a Flask test client wired to a temp data dir."""
    from rex.gui_app import _create_flask_app

    app = _create_flask_app(ui_enabled=False)
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


# ---------------------------------------------------------------------------
# Unit tests for rex.auth
# ---------------------------------------------------------------------------


class TestCreateUser:
    def test_creates_user_and_returns_id_and_username(self, tmp_data_dir: Path) -> None:
        from rex.auth import create_user

        user = create_user("alice", "s3cr3t")
        assert user["username"] == "alice"
        assert "id" in user

    def test_duplicate_username_raises(self, tmp_data_dir: Path) -> None:
        from rex.auth import create_user

        create_user("bob", "pass1")
        with pytest.raises(ValueError, match="already taken"):
            create_user("bob", "pass2")

    def test_empty_username_raises(self, tmp_data_dir: Path) -> None:
        from rex.auth import create_user

        with pytest.raises(ValueError, match="username must not be empty"):
            create_user("", "password")

    def test_empty_password_raises(self, tmp_data_dir: Path) -> None:
        from rex.auth import create_user

        with pytest.raises(ValueError, match="password must not be empty"):
            create_user("carol", "")


class TestAuthenticate:
    def test_returns_token_on_valid_credentials(self, tmp_data_dir: Path) -> None:
        from rex.auth import authenticate, create_user

        create_user("dave", "hunter2")
        token = authenticate("dave", "hunter2")
        assert isinstance(token, str)
        assert len(token) > 10

    def test_bad_password_raises(self, tmp_data_dir: Path) -> None:
        from rex.auth import authenticate, create_user

        create_user("eve", "correct")
        with pytest.raises(ValueError, match="invalid username or password"):
            authenticate("eve", "wrong")

    def test_unknown_user_raises(self, tmp_data_dir: Path) -> None:
        from rex.auth import authenticate

        with pytest.raises(ValueError, match="invalid username or password"):
            authenticate("nobody", "pass")


class TestGetCurrentUser:
    def test_decodes_valid_token(self, tmp_data_dir: Path) -> None:
        from rex.auth import authenticate, create_user, get_current_user

        create_user("frank", "secret")
        token = authenticate("frank", "secret")
        user = get_current_user(token)
        assert user["username"] == "frank"
        assert "id" in user

    def test_empty_token_raises(self, tmp_data_dir: Path) -> None:
        from rex.auth import get_current_user

        with pytest.raises(ValueError, match="no token provided"):
            get_current_user("")

    def test_invalid_token_raises(self, tmp_data_dir: Path) -> None:
        from rex.auth import get_current_user

        with pytest.raises(ValueError, match="invalid token"):
            get_current_user("not.a.jwt")

    def test_expired_token_raises(self, tmp_data_dir: Path) -> None:
        """Manually craft an already-expired JWT."""
        from datetime import datetime, timedelta

        import jwt

        payload = {
            "sub": "test-id",
            "username": "grace",
            "iat": datetime.now(UTC) - timedelta(hours=48),
            "exp": datetime.now(UTC) - timedelta(hours=24),
        }
        expired_token = jwt.encode(payload, "test-secret-key", algorithm="HS256")

        from rex.auth import get_current_user

        with pytest.raises(ValueError, match="expired"):
            get_current_user(expired_token)


# ---------------------------------------------------------------------------
# Flask endpoint tests
# ---------------------------------------------------------------------------


def _get_setup_token(client: object) -> str:
    """Return the single-use setup token from the Flask app config."""
    return client.application.config.get("SETUP_TOKEN") or ""  # type: ignore[attr-defined]


class TestRegisterEndpoint:
    def test_register_returns_201_with_user(self, flask_client: object) -> None:
        resp = flask_client.post(  # type: ignore[attr-defined]
            "/api/auth/register",
            json={
                "username": "henry",
                "password": "passw0rd",
            },  # pragma: allowlist secret -- registration fixture
            headers={"X-Setup-Token": _get_setup_token(flask_client)},
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["username"] == "henry"
        assert "id" in data

    def test_register_duplicate_returns_409(self, flask_client: object) -> None:
        # First user requires the setup token.
        flask_client.post(  # type: ignore[attr-defined]
            "/api/auth/register",
            json={
                "username": "ivan",
                "password": "pass",
            },  # pragma: allowlist secret -- registration fixture
            headers={"X-Setup-Token": _get_setup_token(flask_client)},
        )
        # Second registration of the same username: user_count>0, no token needed.
        resp = flask_client.post(  # type: ignore[attr-defined]
            "/api/auth/register",
            json={"username": "ivan", "password": "pass"},
        )
        assert resp.status_code == 409

    def test_register_missing_fields_returns_400(self, flask_client: object) -> None:
        # Pass a valid token so the field-validation path is reached.
        resp = flask_client.post(  # type: ignore[attr-defined]
            "/api/auth/register",
            json={"username": "judy"},
            headers={"X-Setup-Token": _get_setup_token(flask_client)},
        )
        assert resp.status_code == 400


class TestLoginEndpoint:
    def test_login_returns_token(self, flask_client: object) -> None:
        flask_client.post(  # type: ignore[attr-defined]
            "/api/auth/register",
            json={
                "username": "kate",
                "password": "secret",
            },  # pragma: allowlist secret -- registration fixture
            headers={"X-Setup-Token": _get_setup_token(flask_client)},
        )
        resp = flask_client.post(  # type: ignore[attr-defined]
            "/api/auth/login",
            json={"username": "kate", "password": "secret"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "token" in data

    def test_login_bad_password_returns_401(self, flask_client: object) -> None:
        flask_client.post(  # type: ignore[attr-defined]
            "/api/auth/register",
            json={
                "username": "leo",
                "password": "correct",
            },  # pragma: allowlist secret -- registration fixture
            headers={"X-Setup-Token": _get_setup_token(flask_client)},
        )
        resp = flask_client.post(  # type: ignore[attr-defined]
            "/api/auth/login",
            json={
                "username": "leo",
                "password": "wrong",
            },  # pragma: allowlist secret -- invalid-login fixture
        )
        assert resp.status_code == 401

    def test_login_missing_fields_returns_400(self, flask_client: object) -> None:
        resp = flask_client.post(  # type: ignore[attr-defined]
            "/api/auth/login",
            json={"username": "mia"},
        )
        assert resp.status_code == 400


class TestLogoutEndpoint:
    def test_logout_returns_ok(self, flask_client: object) -> None:
        resp = flask_client.post("/api/auth/logout")  # type: ignore[attr-defined]
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True


# ---------------------------------------------------------------------------
# JWT secret hardening (US-RR-006)
# ---------------------------------------------------------------------------


class TestGetJWTSecret:
    def test_raises_runtime_error_when_secret_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No hardcoded fallback — missing REX_JWT_SECRET must raise RuntimeError."""
        monkeypatch.delenv("REX_JWT_SECRET", raising=False)
        from rex.auth import get_jwt_secret

        with pytest.raises(RuntimeError, match="REX_JWT_SECRET is not set"):
            get_jwt_secret()

    def test_returns_secret_when_env_var_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("REX_JWT_SECRET", "my-strong-test-secret")
        from rex.auth import get_jwt_secret

        assert get_jwt_secret() == "my-strong-test-secret"
