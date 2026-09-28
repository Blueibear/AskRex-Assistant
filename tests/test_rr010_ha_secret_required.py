"""US-RR-010: HA blueprint must not mount when HA_SECRET is unset."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import flask
import pytest

from rex.ha_bridge import HABridge, create_blueprint


def _make_bridge(
    base_url: str = "http://ha.local:8123",
    token: str = "[test-ha-token-placeholder]",
    secret: str = "",
) -> HABridge:
    with patch("rex.ha_bridge.requests") as mock_requests:
        mock_session = MagicMock()
        mock_requests.Session.return_value = mock_session
        bridge = HABridge(base_url=base_url, token=token, secret=secret)
        bridge._session = mock_session
    return bridge


def _stub_response(bridge: HABridge, json_data: Any, status_code: int = 200) -> None:
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.json.return_value = json_data
    mock_response.content = b"ok"
    mock_response.raise_for_status = MagicMock()
    bridge._session.request.return_value = mock_response


# ------------------------------------------------------------------ #
# Negative: no secret → create_blueprint raises RuntimeError
# ------------------------------------------------------------------ #


class TestNoSecretRaisesOnBlueprint:
    def test_create_blueprint_raises_when_secret_empty(self) -> None:
        """create_blueprint() must raise RuntimeError when bridge has no secret."""
        bridge = _make_bridge(secret="")
        with pytest.raises(RuntimeError, match="HA_SECRET"):
            create_blueprint(bridge)

    def test_create_blueprint_raises_when_secret_none(self) -> None:
        """create_blueprint() must raise RuntimeError when bridge secret is None."""
        bridge = _make_bridge(secret="")
        bridge._secret = None  # type: ignore[assignment]
        with pytest.raises(RuntimeError, match="HA_SECRET"):
            create_blueprint(bridge)


# ------------------------------------------------------------------ #
# Negative: routes return 404 when blueprint is not registered
# ------------------------------------------------------------------ #


class TestRoutesReturn404WhenBlueprintNotMounted:
    def _app_without_ha_blueprint(self) -> flask.Flask:
        app = flask.Flask(__name__)
        # intentionally do NOT register the HA blueprint
        return app

    def test_entities_returns_404_without_blueprint(self) -> None:
        client = self._app_without_ha_blueprint().test_client()
        resp = client.get("/ha/entities")
        assert resp.status_code == 404

    def test_script_returns_404_without_blueprint(self) -> None:
        client = self._app_without_ha_blueprint().test_client()
        resp = client.post("/ha/script", json={"script": "script.test"})
        assert resp.status_code == 404


# ------------------------------------------------------------------ #
# Positive: secret set → blueprint registers and enforces the secret
# ------------------------------------------------------------------ #


class TestSecretSetAllowsRegistrationAndEnforces:
    def _app_with_ha_blueprint(self, secret: str) -> flask.Flask:
        bridge = _make_bridge(secret=secret)
        _stub_response(bridge, [])
        bp = create_blueprint(bridge)
        app = flask.Flask(__name__)
        app.register_blueprint(bp)
        return app

    def test_entities_returns_200_with_valid_secret(self) -> None:
        """/ha/entities returns 200 when HA_SECRET is set and header matches."""
        client = self._app_with_ha_blueprint("[test-ha-secret-placeholder]").test_client()
        resp = client.get("/ha/entities", headers={"HASS_SECRET": "[test-ha-secret-placeholder]"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert "entities" in data

    def test_entities_returns_403_with_wrong_secret(self) -> None:
        """/ha/entities returns 403 when HASS_SECRET header is wrong."""
        client = self._app_with_ha_blueprint("[test-ha-secret-placeholder]").test_client()
        resp = client.get(
            "/ha/entities", headers={"HASS_SECRET": "[test-ha-wrong-secret-placeholder]"}
        )
        assert resp.status_code == 403

    def test_entities_returns_403_with_no_secret_header(self) -> None:
        """/ha/entities returns 403 when HASS_SECRET header is absent."""
        client = self._app_with_ha_blueprint("[test-ha-secret-placeholder]").test_client()
        resp = client.get("/ha/entities")
        assert resp.status_code == 403

    def test_script_returns_403_with_wrong_secret(self) -> None:
        """/ha/script returns 403 when HASS_SECRET header is wrong."""
        client = self._app_with_ha_blueprint("[test-ha-secret-placeholder]").test_client()
        resp = client.post(
            "/ha/script",
            json={"script": "script.test"},
            headers={"HASS_SECRET": "[test-ha-wrong-secret-placeholder]"},
        )
        assert resp.status_code == 403

    def test_script_returns_403_with_no_secret_header(self) -> None:
        """/ha/script returns 403 when HASS_SECRET header is absent."""
        client = self._app_with_ha_blueprint("[test-ha-secret-placeholder]").test_client()
        resp = client.post("/ha/script", json={"script": "script.test"})
        assert resp.status_code == 403

    def test_script_with_valid_secret_cannot_bypass_human_confirmation(self) -> None:
        """Shared-secret legacy route must not dispatch sensitive script actions."""
        bridge = _make_bridge(secret="[test-ha-secret-placeholder]")
        _stub_response(bridge, {})
        app = flask.Flask(__name__)
        app.register_blueprint(create_blueprint(bridge))
        client = app.test_client()

        resp = client.post(
            "/ha/script",
            json={"script": "script.test"},
            headers={"HASS_SECRET": "[test-ha-secret-placeholder]"},
        )

        assert resp.status_code == 409
        payload = resp.get_json()
        assert payload["status"] == "confirmation_required"
        assert "confirmation_token" not in payload
        bridge._session.request.assert_not_called()
