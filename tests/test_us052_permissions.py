"""Tests for US-052: Permissions system.

Covers:
- Permission enum values
- grant_permission / revoke_permission / check_permission / get_permissions
- bootstrap_admin_if_first_user: first user gets admin, subsequent users do not
- API endpoints: GET /api/user/permissions
- API endpoints: POST /api/admin/permissions/grant (admin only)
- API endpoints: POST /api/admin/permissions/revoke (admin only)
- 403 returned when non-admin calls admin endpoints
"""

from __future__ import annotations

from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point auth and permissions at a temp directory."""
    monkeypatch.setenv("REX_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("REX_JWT_SECRET", "test-perm-secret")
    return tmp_path


@pytest.fixture()
def flask_client(tmp_data_dir: Path):  # type: ignore[override]
    """Return a Flask test client wired to a temp data dir."""
    from rex.gui_app import _create_flask_app

    app = _create_flask_app(ui_enabled=False)
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def _register_and_login(client, username: str, password: str = "pass1234") -> str:
    """Register the first user and return their JWT token."""
    setup_token = client.application.config.get("SETUP_TOKEN") or ""
    client.post(
        "/api/auth/register",
        json={"username": username, "password": password},
        headers={"X-Setup-Token": setup_token},
    )
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    return resp.get_json()["token"]


# ---------------------------------------------------------------------------
# Unit tests: Permission enum
# ---------------------------------------------------------------------------


class TestPermissionEnum:
    def test_all_values_present(self) -> None:
        from rex.permissions import Permission

        names = {p.value for p in Permission}
        assert names == {
            "computer_control",
            "email_send",
            "sms_send",
            "ha_control",
            "openclaw_execute",
            "admin",
        }

    def test_permission_is_string_enum(self) -> None:
        from rex.permissions import Permission

        assert Permission.admin == "admin"


# ---------------------------------------------------------------------------
# Unit tests: grant / revoke / check / get
# ---------------------------------------------------------------------------


class TestGrantRevoke:
    def test_grant_and_check(self, tmp_data_dir: Path) -> None:
        from rex.permissions import Permission, check_permission, grant_permission

        grant_permission("user-1", Permission.email_send)
        assert check_permission("user-1", Permission.email_send) is True

    def test_check_returns_false_when_not_granted(self, tmp_data_dir: Path) -> None:
        from rex.permissions import Permission, check_permission

        assert check_permission("user-no-perms", Permission.sms_send) is False

    def test_grant_by_string(self, tmp_data_dir: Path) -> None:
        from rex.permissions import check_permission, grant_permission

        grant_permission("user-2", "ha_control")
        assert check_permission("user-2", "ha_control") is True

    def test_revoke_removes_permission(self, tmp_data_dir: Path) -> None:
        from rex.permissions import (
            Permission,
            check_permission,
            grant_permission,
            revoke_permission,
        )

        grant_permission("user-3", Permission.computer_control)
        assert check_permission("user-3", Permission.computer_control) is True
        revoke_permission("user-3", Permission.computer_control)
        assert check_permission("user-3", Permission.computer_control) is False

    def test_revoke_noop_when_not_held(self, tmp_data_dir: Path) -> None:
        """Revoking a permission that was never granted should not raise."""
        from rex.permissions import Permission, revoke_permission

        revoke_permission("user-4", Permission.admin)  # should not raise

    def test_grant_duplicate_is_idempotent(self, tmp_data_dir: Path) -> None:
        from rex.permissions import Permission, check_permission, grant_permission

        grant_permission("user-5", Permission.admin)
        grant_permission("user-5", Permission.admin)  # second call must not raise
        assert check_permission("user-5", Permission.admin) is True

    def test_get_permissions_returns_list(self, tmp_data_dir: Path) -> None:
        from rex.permissions import Permission, get_permissions, grant_permission

        grant_permission("user-6", Permission.email_send)
        grant_permission("user-6", Permission.sms_send)
        perms = get_permissions("user-6")
        assert set(perms) == {"email_send", "sms_send"}

    def test_invalid_permission_string_raises(self, tmp_data_dir: Path) -> None:
        from rex.permissions import check_permission

        with pytest.raises(ValueError):
            check_permission("user-7", "not_a_real_perm")


# ---------------------------------------------------------------------------
# Unit tests: bootstrap_admin_if_first_user
# ---------------------------------------------------------------------------


class TestBootstrapAdmin:
    def test_first_user_gets_admin(self, tmp_data_dir: Path) -> None:
        from rex.permissions import Permission, bootstrap_admin_if_first_user, check_permission

        bootstrap_admin_if_first_user("user-first")
        assert check_permission("user-first", Permission.admin) is True

    def test_second_user_does_not_get_admin(self, tmp_data_dir: Path) -> None:
        from rex.permissions import Permission, bootstrap_admin_if_first_user, check_permission

        bootstrap_admin_if_first_user("user-first")
        bootstrap_admin_if_first_user("user-second")
        assert check_permission("user-second", Permission.admin) is False

    def test_bootstrap_idempotent_for_same_user(self, tmp_data_dir: Path) -> None:
        from rex.permissions import Permission, bootstrap_admin_if_first_user, check_permission

        bootstrap_admin_if_first_user("user-first")
        bootstrap_admin_if_first_user("user-first")  # must not raise
        assert check_permission("user-first", Permission.admin) is True


# ---------------------------------------------------------------------------
# API tests: /api/user/permissions
# ---------------------------------------------------------------------------


class TestGetMyPermissionsEndpoint:
    def test_returns_permissions_for_admin_user(self, flask_client: object) -> None:
        token = _register_and_login(flask_client, "admin_user")  # type: ignore[arg-type]
        resp = flask_client.get(  # type: ignore[attr-defined]
            "/api/user/permissions",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "admin" in data["permissions"]

    def test_unauthenticated_returns_401(self, flask_client: object) -> None:
        resp = flask_client.get("/api/user/permissions")  # type: ignore[attr-defined]
        assert resp.status_code == 401

    def test_second_user_has_no_admin(self, flask_client: object) -> None:
        _register_and_login(flask_client, "first_user")  # type: ignore[arg-type]
        token2 = _register_and_login(flask_client, "second_user")  # type: ignore[arg-type]
        resp = flask_client.get(  # type: ignore[attr-defined]
            "/api/user/permissions",
            headers={"Authorization": f"Bearer {token2}"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "admin" not in data["permissions"]


# ---------------------------------------------------------------------------
# API tests: /api/admin/permissions/grant
# ---------------------------------------------------------------------------


class TestGrantPermissionEndpoint:
    def test_admin_can_grant_permission(self, flask_client: object) -> None:
        # Register first user (gets admin) and a second user
        admin_token = _register_and_login(flask_client, "admin_u")  # type: ignore[arg-type]
        # Register second user; capture their id from the register response
        resp = flask_client.post(  # type: ignore[attr-defined]
            "/api/auth/register",
            json={"username": "target_u", "password": "pass1234"},  # pragma: allowlist secret
        )
        target_id = resp.get_json()["id"]

        resp = flask_client.post(  # type: ignore[attr-defined]
            "/api/admin/permissions/grant",
            json={"user_id": target_id, "permission": "email_send"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True

    def test_non_admin_gets_403(self, flask_client: object) -> None:
        _register_and_login(flask_client, "admin_u2")  # type: ignore[arg-type]
        non_admin_token = _register_and_login(flask_client, "non_admin_u")  # type: ignore[arg-type]

        resp = flask_client.post(  # type: ignore[attr-defined]
            "/api/admin/permissions/grant",
            json={"user_id": "some-id", "permission": "email_send"},
            headers={"Authorization": f"Bearer {non_admin_token}"},
        )
        assert resp.status_code == 403

    def test_unauthenticated_returns_401(self, flask_client: object) -> None:
        resp = flask_client.post(  # type: ignore[attr-defined]
            "/api/admin/permissions/grant",
            json={"user_id": "x", "permission": "email_send"},
        )
        assert resp.status_code == 401

    def test_missing_fields_returns_400(self, flask_client: object) -> None:
        admin_token = _register_and_login(flask_client, "admin_u3")  # type: ignore[arg-type]
        resp = flask_client.post(  # type: ignore[attr-defined]
            "/api/admin/permissions/grant",
            json={"user_id": "x"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 400

    def test_invalid_permission_returns_400(self, flask_client: object) -> None:
        admin_token = _register_and_login(flask_client, "admin_u4")  # type: ignore[arg-type]
        resp = flask_client.post(  # type: ignore[attr-defined]
            "/api/admin/permissions/grant",
            json={"user_id": "some-id", "permission": "fake_perm"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# API tests: /api/admin/permissions/revoke
# ---------------------------------------------------------------------------


class TestRevokePermissionEndpoint:
    def test_admin_can_revoke_permission(self, flask_client: object) -> None:
        admin_token = _register_and_login(flask_client, "admin_rv")  # type: ignore[arg-type]
        resp = flask_client.post(  # type: ignore[attr-defined]
            "/api/auth/register",
            json={
                "username": "target_rv",
                "password": "pass1234",
            },  # pragma: allowlist secret -- fixture
        )
        target_id = resp.get_json()["id"]

        # First grant, then revoke
        flask_client.post(  # type: ignore[attr-defined]
            "/api/admin/permissions/grant",
            json={"user_id": target_id, "permission": "sms_send"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        resp = flask_client.post(  # type: ignore[attr-defined]
            "/api/admin/permissions/revoke",
            json={"user_id": target_id, "permission": "sms_send"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True

    def test_non_admin_gets_403(self, flask_client: object) -> None:
        _register_and_login(flask_client, "admin_rv2")  # type: ignore[arg-type]
        non_admin_token = _register_and_login(flask_client, "non_admin_rv")  # type: ignore[arg-type]

        resp = flask_client.post(  # type: ignore[attr-defined]
            "/api/admin/permissions/revoke",
            json={"user_id": "some-id", "permission": "sms_send"},
            headers={"Authorization": f"Bearer {non_admin_token}"},
        )
        assert resp.status_code == 403
