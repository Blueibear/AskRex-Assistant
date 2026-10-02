"""Offline tests for the rex.ha_tts package (Cycle 8.3a).

All tests run without network access.  HTTP calls are replaced by
``unittest.mock.patch`` on ``requests.post``.  DNS resolution is mocked
via ``socket.getaddrinfo`` so the SSRF validator never touches the network.

Coverage
--------
- Config parsing (HaTtsConfig)
- SSRF validation: embedded credentials, localhost/private/reserved addresses
- SSRF validation: valid public-routable address passes
- HaTtsClient.speak(): correct URL, headers, payload construction
- HaTtsClient.speak(): empty message rejected
- HaTtsClient.speak(): missing entity_id rejected
- HaTtsClient.speak(): HTTP errors mapped to safe messages (no tokens surfaced)
- Notification channel routing: HA path called when enabled, no call when disabled
- build_ha_tts_client(): returns None when disabled or missing config
"""

from __future__ import annotations

import socket
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from rex.ha_tts.client import (
    HaTtsClient,
    TtsResult,
    _safe_error,
    _validate_base_url,
    _validate_remote_host,
)
from rex.ha_tts.config import HaTtsConfig

# ─────────────────────────────────────────────────────────────────────────────
# DNS mock helpers
# ─────────────────────────────────────────────────────────────────────────────


def _public_addrinfo(*_args, **_kwargs):
    """Stable public-routable address for SSRF-safe validation in tests."""
    return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))]


def _loopback_addrinfo(*_args, **_kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 0))]


def _private_addrinfo(*_args, **_kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("192.168.1.50", 0))]


def _link_local_addrinfo(*_args, **_kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("169.254.1.1", 0))]


# ─────────────────────────────────────────────────────────────────────────────
# HaTtsConfig
# ─────────────────────────────────────────────────────────────────────────────


class TestHaTtsConfig:
    def test_defaults(self):
        cfg = HaTtsConfig()
        assert cfg.enabled is False
        assert cfg.base_url is None
        assert cfg.token_ref is None
        assert cfg.default_entity_id is None
        assert cfg.default_tts_domain == "tts"
        assert cfg.default_tts_service == "speak"
        assert cfg.timeout_seconds == 10.0
        assert cfg.allow_http is False

    def test_enabled_with_all_fields(self):
        cfg = HaTtsConfig(
            enabled=True,
            base_url="https://ha.example.com:8123",
            token_ref="ha:mytoken",
            default_entity_id="media_player.living_room",
            default_tts_domain="tts",
            default_tts_service="google_say",
            timeout_seconds=15.0,
            allow_http=False,
        )
        assert cfg.enabled is True
        assert cfg.token_ref == "ha:mytoken"
        assert cfg.default_entity_id == "media_player.living_room"

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            HaTtsConfig.model_validate({"enabled": True, "unknown_key": "value"})


# ─────────────────────────────────────────────────────────────────────────────
# SSRF – _validate_remote_host
# ─────────────────────────────────────────────────────────────────────────────


class TestValidateRemoteHost:
    def test_empty_hostname_raises(self):
        with pytest.raises(ValueError, match="missing a hostname"):
            _validate_remote_host(None)

    def test_localhost_string_raises(self):
        with pytest.raises(ValueError, match="localhost"):
            _validate_remote_host("localhost")

    def test_localhost_localdomain_raises(self):
        with pytest.raises(ValueError, match="localhost"):
            _validate_remote_host("localhost.localdomain")

    @patch("socket.getaddrinfo", side_effect=_loopback_addrinfo)
    def test_loopback_ip_raises(self, _mock):
        with pytest.raises(ValueError, match="local or reserved"):
            _validate_remote_host("ha.internal")

    @patch("socket.getaddrinfo", side_effect=_private_addrinfo)
    def test_private_ip_raises(self, _mock):
        with pytest.raises(ValueError, match="local or reserved"):
            _validate_remote_host("ha.internal")

    @patch("socket.getaddrinfo", side_effect=_link_local_addrinfo)
    def test_link_local_ip_raises(self, _mock):
        with pytest.raises(ValueError, match="local or reserved"):
            _validate_remote_host("ha.internal")

    @patch("socket.getaddrinfo", side_effect=socket.gaierror("no such host"))
    def test_dns_failure_raises(self, _mock):
        with pytest.raises(ValueError, match="Could not resolve"):
            _validate_remote_host("nonexistent.invalid")

    @patch("socket.getaddrinfo", side_effect=_public_addrinfo)
    def test_public_ip_ok(self, _mock):
        # Should not raise
        _validate_remote_host("ha.example.com")


# ─────────────────────────────────────────────────────────────────────────────
# SSRF – _validate_base_url
# ─────────────────────────────────────────────────────────────────────────────


class TestValidateBaseUrl:
    @patch("socket.getaddrinfo", side_effect=_public_addrinfo)
    def test_https_ok(self, _mock):
        result = _validate_base_url("https://ha.example.com:8123/")
        assert result == "https://ha.example.com:8123"

    @patch("socket.getaddrinfo", side_effect=_public_addrinfo)
    def test_trailing_slash_stripped(self, _mock):
        result = _validate_base_url("https://ha.example.com/")
        assert not result.endswith("/")

    def test_http_rejected_by_default(self):
        with pytest.raises(ValueError, match="https"):
            _validate_base_url("http://ha.example.com")

    @patch("socket.getaddrinfo", side_effect=_public_addrinfo)
    def test_http_allowed_when_flag_set(self, _mock):
        result = _validate_base_url("http://ha.example.com", allow_http=True)
        assert result == "http://ha.example.com"

    def test_ftp_scheme_rejected(self):
        with pytest.raises(ValueError, match="scheme"):
            _validate_base_url("ftp://ha.example.com")

    def test_missing_host_rejected(self):
        with pytest.raises(ValueError, match="host"):
            _validate_base_url("https://")

    def test_embedded_credentials_rejected(self):
        with pytest.raises(ValueError, match="embedded credentials"):
            _validate_base_url("https://user:pass@ha.example.com")  # pragma: allowlist secret

    @patch("socket.getaddrinfo", side_effect=_loopback_addrinfo)
    def test_loopback_rejected(self, _mock):
        with pytest.raises(ValueError, match="local or reserved"):
            _validate_base_url("https://ha.home")

    @patch("socket.getaddrinfo", side_effect=_private_addrinfo)
    def test_private_rejected(self, _mock):
        with pytest.raises(ValueError, match="local or reserved"):
            _validate_base_url("https://ha.home")


# ─────────────────────────────────────────────────────────────────────────────
# HaTtsClient.speak()
# ─────────────────────────────────────────────────────────────────────────────


def _make_client() -> HaTtsClient:
    """Build a HaTtsClient with a mocked-out SSRF validation for unit tests."""
    with patch("socket.getaddrinfo", side_effect=_public_addrinfo):
        return HaTtsClient(
            base_url="https://ha.example.com:8123",
            token="testtoken123",
            default_entity_id="media_player.living_room",
        )


class TestHaTtsClientSpeak:
    def test_correct_url_and_headers(self):
        client = _make_client()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()

        with patch("requests.post", return_value=mock_response) as mock_post:
            result = client.speak("Hello world")

        assert result.ok is True
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert call_kwargs[0][0] == "https://ha.example.com:8123/api/services/tts/speak"
        headers = call_kwargs[1]["headers"]
        assert "Authorization" in headers
        assert headers["Authorization"].startswith("Bearer ")
        # Token must appear in the header but not in any log or result
        assert "testtoken123" in headers["Authorization"]

    def test_correct_payload(self):
        client = _make_client()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()

        with patch("requests.post", return_value=mock_response) as mock_post:
            client.speak("Test message", entity_id="media_player.bedroom")

        payload = mock_post.call_args[1]["json"]
        assert payload["entity_id"] == "media_player.bedroom"
        assert payload["message"] == "Test message"

    def test_default_entity_used_when_no_override(self):
        client = _make_client()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()

        with patch("requests.post", return_value=mock_response) as mock_post:
            client.speak("Hello")

        payload = mock_post.call_args[1]["json"]
        assert payload["entity_id"] == "media_player.living_room"

    def test_empty_message_rejected(self):
        client = _make_client()
        result = client.speak("   ")
        assert result.ok is False
        assert "empty" in (result.error or "").lower()

    def test_no_entity_id_rejected(self):
        with patch("socket.getaddrinfo", side_effect=_public_addrinfo):
            client = HaTtsClient(
                base_url="https://ha.example.com:8123",
                token="tok",
                default_entity_id=None,
            )
        result = client.speak("Hello")
        assert result.ok is False
        assert "entity_id" in (result.error or "").lower()

    def test_http_error_mapped_safely(self):
        import requests as req

        client = _make_client()
        mock_response = MagicMock()
        mock_response.status_code = 401
        err = req.HTTPError(response=mock_response)
        mock_response.raise_for_status.side_effect = err

        with patch("requests.post", return_value=mock_response):
            result = client.speak("Hello")

        assert result.ok is False
        # Token must not appear in error
        assert "testtoken123" not in (result.error or "")
        assert "401" in (result.error or "")

    def test_timeout_error_mapped_safely(self):
        import requests as req

        client = _make_client()
        with patch("requests.post", side_effect=req.Timeout()):
            result = client.speak("Hello")

        assert result.ok is False
        assert "timed out" in (result.error or "").lower()
        assert "testtoken123" not in (result.error or "")

    def test_connection_error_mapped_safely(self):
        import requests as req

        client = _make_client()
        with patch("requests.post", side_effect=req.ConnectionError()):
            result = client.speak("Hello")

        assert result.ok is False
        assert "connection" in (result.error or "").lower()

    def test_extra_data_merged_into_payload(self):
        client = _make_client()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()

        with patch("requests.post", return_value=mock_response) as mock_post:
            client.speak("Hi", extra_data={"language": "en-US"})

        payload = mock_post.call_args[1]["json"]
        assert payload.get("language") == "en-US"


# ─────────────────────────────────────────────────────────────────────────────
# build_ha_tts_client()
# ─────────────────────────────────────────────────────────────────────────────


class TestBuildHaTtsClient:
    def test_returns_none_when_disabled(self):
        from rex.ha_tts.client import build_ha_tts_client

        cfg = HaTtsConfig(enabled=False)
        with patch("rex.ha_tts.config.load_ha_tts_config", return_value=cfg):
            assert build_ha_tts_client() is None

    def test_returns_none_when_base_url_missing(self):
        from rex.ha_tts.client import build_ha_tts_client

        cfg = HaTtsConfig(enabled=True, token_ref="ha:tok")
        with patch("rex.ha_tts.config.load_ha_tts_config", return_value=cfg):
            assert build_ha_tts_client() is None

    def test_returns_none_when_token_ref_missing(self):
        from rex.ha_tts.client import build_ha_tts_client

        cfg = HaTtsConfig(enabled=True, base_url="https://ha.example.com")
        with patch("rex.ha_tts.config.load_ha_tts_config", return_value=cfg):
            assert build_ha_tts_client() is None

    def test_returns_none_when_credential_resolution_fails(self):
        from rex.ha_tts.client import build_ha_tts_client

        cfg = HaTtsConfig(
            enabled=True,
            base_url="https://ha.example.com",
            token_ref="ha:tok",
        )
        with (
            patch("rex.ha_tts.config.load_ha_tts_config", return_value=cfg),
            patch(
                "rex.credentials.CredentialManager.get_token",
                side_effect=KeyError("ha:tok not found"),
            ),
        ):
            assert build_ha_tts_client() is None

    def test_returns_none_when_token_empty(self):
        from rex.ha_tts.client import build_ha_tts_client

        cfg = HaTtsConfig(
            enabled=True,
            base_url="https://ha.example.com",
            token_ref="ha:tok",
        )
        with (
            patch("rex.ha_tts.config.load_ha_tts_config", return_value=cfg),
            patch("rex.credentials.CredentialManager.get_token", return_value=""),
        ):
            assert build_ha_tts_client() is None

    def test_returns_client_when_fully_configured(self):
        from rex.ha_tts.client import build_ha_tts_client

        cfg = HaTtsConfig(
            enabled=True,
            base_url="https://ha.example.com",
            token_ref="ha:tok",
            default_entity_id="media_player.test",
        )
        with (
            patch("rex.ha_tts.config.load_ha_tts_config", return_value=cfg),
            patch("rex.credentials.CredentialManager.get_token", return_value="secret-token"),
            patch("socket.getaddrinfo", side_effect=_public_addrinfo),
        ):
            client = build_ha_tts_client()
        assert client is not None
        assert client.default_entity_id == "media_player.test"

    def test_returns_none_when_base_url_is_private(self):
        from rex.ha_tts.client import build_ha_tts_client

        cfg = HaTtsConfig(
            enabled=True,
            base_url="https://ha.home",
            token_ref="ha:tok",
        )
        with (
            patch("rex.ha_tts.config.load_ha_tts_config", return_value=cfg),
            patch("rex.credentials.CredentialManager.get_token", return_value="tok"),
            patch("socket.getaddrinfo", side_effect=_private_addrinfo),
        ):
            result = build_ha_tts_client()
        assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# Notification channel routing
# ─────────────────────────────────────────────────────────────────────────────


class TestNotificationHaTtsChannel:
    """Verify the Notifier's _send_to_ha_tts method behaves correctly."""

    def _make_notifier(self, tmp_path):
        from rex.notification import Notifier

        return Notifier(storage_path=tmp_path)

    def test_no_call_when_channel_disabled(self, tmp_path):
        """When HA TTS is disabled, build_ha_tts_client returns None → no HTTP call."""
        from rex.notification import NotificationRequest, Notifier

        notifier = Notifier(storage_path=tmp_path)
        req = NotificationRequest(
            title="Test",
            body="Body",
            priority="normal",
            channel_preferences=["ha_tts"],
        )

        with (
            patch("rex.ha_tts.client.build_ha_tts_client", return_value=None),
            patch("requests.post") as mock_post,
        ):
            notifier._send_to_ha_tts(req)
            mock_post.assert_not_called()

    def test_calls_speak_when_enabled(self, tmp_path):
        from rex.notification import NotificationRequest, Notifier

        mock_client = MagicMock()
        mock_client.speak.return_value = TtsResult(ok=True)
        mock_client.tts_domain = "tts"
        mock_client.tts_service = "speak"
        mock_client.default_entity_id = "media_player.lounge"

        notifier = Notifier(storage_path=tmp_path)
        req = NotificationRequest(
            title="Alert",
            body="Something happened",
            priority="urgent",
            channel_preferences=["ha_tts"],
        )

        with patch("rex.ha_tts.client.build_ha_tts_client", return_value=mock_client):
            notifier._send_to_ha_tts(req)

        mock_client.speak.assert_called_once()
        call_kwargs = mock_client.speak.call_args
        assert "Alert" in call_kwargs[0][0]

    def test_raises_on_speak_failure(self, tmp_path):
        from rex.notification import NotificationRequest, Notifier

        mock_client = MagicMock()
        mock_client.speak.return_value = TtsResult(ok=False, error="HA TTS request timed out")
        mock_client.tts_domain = "tts"
        mock_client.tts_service = "speak"
        mock_client.default_entity_id = "media_player.lounge"

        notifier = Notifier(storage_path=tmp_path)
        req = NotificationRequest(
            title="Fail",
            body="body",
            priority="normal",
            channel_preferences=["ha_tts"],
        )

        with (
            patch("rex.ha_tts.client.build_ha_tts_client", return_value=mock_client),
            pytest.raises(RuntimeError, match="timed out"),
        ):
            notifier._send_to_ha_tts(req)

    def test_metadata_entity_id_override(self, tmp_path):
        from rex.notification import NotificationRequest, Notifier

        mock_client = MagicMock()
        mock_client.speak.return_value = TtsResult(ok=True)
        mock_client.tts_domain = "tts"
        mock_client.tts_service = "speak"
        mock_client.default_entity_id = "media_player.default"

        notifier = Notifier(storage_path=tmp_path)
        req = NotificationRequest(
            title="Hi",
            body="body",
            priority="normal",
            channel_preferences=["ha_tts"],
            metadata={"ha_entity_id": "media_player.bedroom"},
        )

        with patch("rex.ha_tts.client.build_ha_tts_client", return_value=mock_client):
            notifier._send_to_ha_tts(req)

        call_kwargs = mock_client.speak.call_args
        assert call_kwargs[1].get("entity_id") == "media_player.bedroom"

    def test_metadata_domain_and_service_override(self, tmp_path):
        from rex.notification import NotificationRequest, Notifier

        mock_client = MagicMock()
        mock_client.speak.return_value = TtsResult(ok=True)
        mock_client.tts_domain = "tts"
        mock_client.tts_service = "speak"

        notifier = Notifier(storage_path=tmp_path)
        req = NotificationRequest(
            title="Hi",
            body="body",
            priority="normal",
            channel_preferences=["ha_tts"],
            metadata={"ha_tts_domain": "custom_tts", "ha_tts_service": "announce"},
        )

        with patch("rex.ha_tts.client.build_ha_tts_client", return_value=mock_client):
            notifier._send_to_ha_tts(req)

        assert mock_client.speak.called
        assert mock_client.tts_domain == "tts"
        assert mock_client.tts_service == "speak"


# ─────────────────────────────────────────────────────────────────────────────
# _safe_error
# ─────────────────────────────────────────────────────────────────────────────


class TestSafeError:
    def test_timeout(self):
        import requests as req

        msg = _safe_error(req.Timeout())
        assert "timed out" in msg.lower()

    def test_http_error_with_status(self):
        import requests as req

        resp = MagicMock()
        resp.status_code = 403
        err = req.HTTPError(response=resp)
        msg = _safe_error(err)
        assert "403" in msg

    def test_connection_error(self):
        import requests as req

        msg = _safe_error(req.ConnectionError())
        assert "connection" in msg.lower()

    def test_generic_request_exception(self):
        import requests as req

        msg = _safe_error(req.RequestException())
        assert "request" in msg.lower() or "HA TTS" in msg

    def test_unknown_exception(self):
        msg = _safe_error(RuntimeError("boom"))
        assert "HA TTS" in msg
