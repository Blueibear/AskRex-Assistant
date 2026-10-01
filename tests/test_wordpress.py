"""Offline tests for the rex.wordpress package (Cycle 6.1).

All tests run without network access.  The HTTP layer is replaced by
unittest.mock.patch on ``requests.get``.

Coverage targets
----------------
- Config parsing and validation for ``wordpress.sites[]``
- Service error handling: missing site, disabled site, missing credential
- WordPressClient health() builds correct URL and passes correct auth
- Auth check is made when auth is configured, skipped when auth_method=none
- HTTP 401/403 responses are handled gracefully (auth_ok=False, not an error)
- Non-JSON body produces a clear error
- Connection errors produce a clear error
"""

from __future__ import annotations

import argparse
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from rex.wordpress.client import WordPressClient, WPHealthResult
from rex.wordpress.config import (
    WordPressConfig,
    WordPressSiteConfig,
    load_wordpress_config,
)
from rex.wordpress.service import (
    WordPressMissingCredentialError,
    WordPressService,
    WordPressSiteDisabledError,
    WordPressSiteNotFoundError,
)

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _mock_public_addrinfo(*_args, **_kwargs):
    """Return a stable public address for SSRF-safe URL validation in tests."""
    return [(0, 0, 0, "", ("93.184.216.34", 0))]


@pytest.fixture(autouse=True)
def _patch_getaddrinfo():
    """Keep tests fully offline by mocking DNS used in SSRF checks."""
    with patch("socket.getaddrinfo", side_effect=_mock_public_addrinfo):
        yield


def _make_site_config(
    *,
    site_id: str = "myblog",
    base_url: str = "https://example.com",
    enabled: bool = True,
    auth_method: str = "none",
    credential_ref: str = "",
    timeout_seconds: int = 15,
) -> WordPressSiteConfig:
    """Build a :class:`WordPressSiteConfig` for testing."""
    return WordPressSiteConfig(
        id=site_id,
        base_url=base_url,
        enabled=enabled,
        auth_method=auth_method,
        credential_ref=credential_ref,
        timeout_seconds=timeout_seconds,
    )


def _make_service(
    sites: list[WordPressSiteConfig],
    *,
    credential_value: str | None = "admin:testpass",
) -> WordPressService:
    """Build a :class:`WordPressService` backed by a mock CredentialManager."""
    creds = MagicMock()
    creds.get_token.return_value = credential_value
    config = WordPressConfig(sites=sites)
    return WordPressService(wp_config=config, credential_manager=creds)


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


class TestWordPressConfig:
    """Tests for rex.wordpress.config."""

    def test_basic_parse(self):
        """Parse a minimal site entry."""
        raw = {
            "wordpress": {
                "sites": [
                    {
                        "id": "myblog",
                        "base_url": "https://example.com",
                    }
                ]
            }
        }
        cfg = load_wordpress_config(raw)
        assert len(cfg.sites) == 1
        site = cfg.sites[0]
        assert site.id == "myblog"
        assert site.base_url == "https://example.com"
        assert site.enabled is True
        assert site.auth_method == "none"

    def test_trailing_slash_stripped(self):
        """base_url trailing slash is stripped."""
        site = WordPressSiteConfig(id="x", base_url="https://example.com/", auth_method="none")
        assert site.base_url == "https://example.com"

    def test_invalid_scheme_rejected(self):
        """Non-http(s) schemes raise ValidationError."""
        with pytest.raises(ValidationError):
            WordPressSiteConfig(id="x", base_url="ftp://example.com", auth_method="none")

    def test_invalid_auth_method_rejected(self):
        """Unknown auth_method raises ValidationError."""
        with pytest.raises(ValidationError):
            WordPressSiteConfig(
                id="x",
                base_url="https://example.com",
                auth_method="oauth2",
            )

    def test_all_auth_methods_accepted(self):
        """All supported auth methods parse correctly."""
        for method in ("none", "application_password", "basic"):
            site = WordPressSiteConfig(id="x", base_url="https://example.com", auth_method=method)
            assert site.auth_method == method

    def test_missing_wordpress_section(self):
        """Missing wordpress key returns empty config."""
        cfg = load_wordpress_config({})
        assert cfg.sites == []

    def test_missing_sites_key(self):
        """Missing sites key returns empty config."""
        cfg = load_wordpress_config({"wordpress": {}})
        assert cfg.sites == []

    def test_disabled_site(self):
        """Disabled site is excluded from list_enabled."""
        raw = {
            "wordpress": {
                "sites": [
                    {"id": "a", "base_url": "https://a.example.com", "enabled": True},
                    {"id": "b", "base_url": "https://b.example.com", "enabled": False},
                ]
            }
        }
        cfg = load_wordpress_config(raw)
        enabled = cfg.list_enabled()
        assert len(enabled) == 1
        assert enabled[0].id == "a"

    def test_get_site_found(self):
        """get_site returns the matching entry."""
        raw = {"wordpress": {"sites": [{"id": "myblog", "base_url": "https://example.com"}]}}
        cfg = load_wordpress_config(raw)
        site = cfg.get_site("myblog")
        assert site is not None
        assert site.id == "myblog"

    def test_get_site_not_found(self):
        """get_site returns None for unknown IDs."""
        cfg = load_wordpress_config({})
        assert cfg.get_site("nonexistent") is None

    def test_needs_credential_property(self):
        """needs_credential is True iff auth_method requires a secret."""
        assert (
            WordPressSiteConfig(
                id="x", base_url="https://example.com", auth_method="none"
            ).needs_credential
            is False
        )
        for method in ("application_password", "basic"):
            assert (
                WordPressSiteConfig(
                    id="x", base_url="https://example.com", auth_method=method
                ).needs_credential
                is True
            )


# ---------------------------------------------------------------------------
# Service error-handling tests
# ---------------------------------------------------------------------------


class TestWordPressServiceErrors:
    """Tests for service-level error cases."""

    def test_site_not_found(self):
        """Missing site ID raises WordPressSiteNotFoundError."""
        service = _make_service([])
        with pytest.raises(WordPressSiteNotFoundError, match="myblog"):
            service.health("myblog")

    def test_site_disabled(self):
        """Disabled site raises WordPressSiteDisabledError."""
        site = _make_site_config(site_id="myblog", enabled=False)
        service = _make_service([site])
        with pytest.raises(WordPressSiteDisabledError, match="myblog"):
            service.health("myblog")

    def test_missing_credential_ref(self):
        """auth_method=application_password with no credential_ref raises error."""
        site = _make_site_config(
            auth_method="application_password",
            credential_ref="",
        )
        service = _make_service([site])
        with pytest.raises(WordPressMissingCredentialError, match="credential_ref"):
            service.health(site.id)

    def test_missing_credential_value(self):
        """credential_ref present but not in CredentialManager raises error."""
        site = _make_site_config(
            auth_method="application_password",
            credential_ref="wp:myblog",
        )
        service = _make_service([site], credential_value=None)
        with pytest.raises(WordPressMissingCredentialError, match="wp:myblog"):
            service.health(site.id)

    def test_credential_bad_format(self):
        """Credential without ':' raises WordPressMissingCredentialError."""
        site = _make_site_config(
            auth_method="basic",
            credential_ref="wp:myblog",
        )
        service = _make_service([site], credential_value="just-a-token-no-colon")
        with pytest.raises(WordPressMissingCredentialError, match="username:password"):
            service.health(site.id)


# ---------------------------------------------------------------------------
# WordPressClient HTTP tests
# ---------------------------------------------------------------------------


class TestWordPressClientHealth:
    """Tests for WordPressClient.health() using mocked requests.get."""

    def _mock_response(self, json_data, *, status_code: int = 200):
        resp = MagicMock()
        resp.status_code = status_code
        resp.json.return_value = json_data
        resp.raise_for_status.return_value = None
        return resp

    def _mock_error_response(self, status_code: int):
        import requests

        resp = MagicMock()
        resp.status_code = status_code
        http_err = requests.HTTPError(response=resp)
        resp.raise_for_status.side_effect = http_err
        return resp

    def test_health_calls_correct_url(self):
        """health() calls GET /wp-json on the correct base URL."""
        client = WordPressClient("https://example.com", site_id="myblog")
        with patch("requests.get") as mock_get:
            mock_get.return_value = self._mock_response(
                {"name": "My Blog", "namespaces": ["wp/v2"]}
            )
            client.health()

        assert mock_get.called
        called_url = mock_get.call_args[0][0]
        assert called_url == "https://example.com/wp-json"

    def test_health_no_auth_when_auth_none(self):
        """health() passes auth=None to requests.get when no auth configured."""
        client = WordPressClient("https://example.com", auth=None)
        with patch("requests.get") as mock_get:
            mock_get.return_value = self._mock_response({"name": "Blog"})
            client.health()

        kwargs = mock_get.call_args[1]
        assert kwargs.get("auth") is None

    def test_health_passes_auth_when_configured(self):
        """health() uses correct auth for the auth check (/wp-json/wp/v2/users/me)."""
        client = WordPressClient(
            "https://example.com",
            auth=("admin", "secret_password"),
            site_id="myblog",
        )

        def _side_effect(url, **kwargs):
            if "users/me" in url:
                return self._mock_response({"id": 1, "name": "admin"})
            return self._mock_response({"name": "Blog", "namespaces": ["wp/v2"]})

        with patch("requests.get", side_effect=_side_effect) as mock_get:
            result = client.health()

        # auth should be passed on the users/me call
        auth_calls = [c for c in mock_get.call_args_list if "users/me" in c[0][0]]
        assert len(auth_calls) == 1
        assert auth_calls[0][1].get("auth") == ("admin", "secret_password")
        assert result.auth_ok is True

    def test_health_wp_detected_from_namespaces(self):
        """WP is detected when 'namespaces' key is present."""
        client = WordPressClient("https://example.com")
        with patch("requests.get") as mock_get:
            mock_get.return_value = self._mock_response(
                {"namespaces": ["wp/v2", "wc/v3"], "name": "Shop"}
            )
            result = client.health()

        assert result.ok is True
        assert result.wp_detected is True
        assert result.site_name == "Shop"

    def test_health_site_name_and_url_extracted(self):
        """site_name and site_url are extracted from /wp-json response."""
        client = WordPressClient("https://example.com")
        with patch("requests.get") as mock_get:
            mock_get.return_value = self._mock_response(
                {
                    "name": "My WordPress Blog",
                    "url": "https://example.com",
                    "namespaces": ["wp/v2"],
                }
            )
            result = client.health()

        assert result.site_name == "My WordPress Blog"
        assert result.site_url == "https://example.com"

    def test_health_connection_error(self):
        """Connection errors produce ok=False with an error message."""
        import requests as _requests

        client = WordPressClient("https://unreachable.example.com")
        with patch(
            "requests.get",
            side_effect=_requests.ConnectionError("Connection refused"),
        ):
            result = client.health()

        assert result.ok is False
        assert result.reachable is False
        assert result.error is not None

    def test_health_auth_check_returns_false_on_401(self):
        """401 on /users/me sets auth_ok=False (not a fatal error)."""
        import requests as _requests

        client = WordPressClient(
            "https://example.com",
            auth=("admin", "wrongpass"),
        )

        def _side_effect(url, **kwargs):
            if "users/me" in url:
                err_resp = MagicMock()
                err_resp.status_code = 401
                raise _requests.HTTPError(response=err_resp)
            return self._mock_response({"name": "Blog", "namespaces": ["wp/v2"]})

        with patch("requests.get", side_effect=_side_effect):
            result = client.health()

        assert result.ok is True  # main check passed
        assert result.auth_ok is False

    def test_health_no_auth_check_when_no_auth(self):
        """auth_ok is None when auth_method=none (no auth check performed)."""
        client = WordPressClient("https://example.com", auth=None)
        with patch("requests.get") as mock_get:
            mock_get.return_value = self._mock_response({"name": "Blog", "namespaces": ["wp/v2"]})
            result = client.health()

        assert result.auth_ok is None
        # Should only be called once (/wp-json), NOT for /users/me
        assert mock_get.call_count == 1

    def test_health_timeout_passed(self):
        """Timeout value from config is forwarded to requests.get."""
        client = WordPressClient("https://example.com", timeout=42)
        with patch("requests.get") as mock_get:
            mock_get.return_value = self._mock_response({"name": "Blog"})
            client.health()

        kwargs = mock_get.call_args[1]
        assert kwargs.get("timeout") == 42


class TestWordPressClientSecurity:
    """Security-focused tests for URL validation and error sanitization."""

    def test_rejects_localhost_base_url(self):
        """localhost is rejected to reduce SSRF risk."""
        with pytest.raises(ValueError, match="localhost"):
            WordPressClient("https://localhost")

    def test_rejects_embedded_credentials_in_base_url(self):
        """Embedded credentials in base_url are rejected."""
        with pytest.raises(ValueError, match="embedded credentials"):
            url = "https://user:pass@example.com"  # pragma: allowlist secret -- URL validation fixture
            WordPressClient(url)

    def test_timeout_error_message_sanitized(self):
        """Timeout errors are normalized without leaking request details."""
        import requests

        client = WordPressClient("https://example.com")
        with patch("requests.get", side_effect=requests.Timeout("secret-token")):
            result = client.health()

        assert result.ok is False
        assert result.error == "Request timed out"

    def test_http_error_message_sanitized(self):
        """HTTP errors expose only status code in user-facing errors."""
        import requests

        client = WordPressClient("https://example.com")
        response = requests.Response()
        response.status_code = 401
        err = requests.HTTPError("https://user:pass@example.com", response=response)

        with patch("requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.raise_for_status.side_effect = err
            mock_get.return_value = mock_resp
            result = client.health()

        assert result.ok is False
        assert result.error == "HTTP error from WordPress API (status=401)"


# ---------------------------------------------------------------------------
# CLI integration tests
# ---------------------------------------------------------------------------


class TestCmdWpHealth:
    """Tests for cmd_wp routing and output via service mock."""

    def _make_args(self, site_id: str = "myblog") -> argparse.Namespace:
        args = argparse.Namespace()
        args.wp_command = "health"
        args.site = site_id
        return args

    def test_health_ok_output(self, capsys):
        """Successful health check prints status and returns 0."""
        from rex.cli import cmd_wp

        mock_service = MagicMock()
        mock_service.health.return_value = WPHealthResult(
            ok=True,
            reachable=True,
            wp_detected=True,
            site_name="My Blog",
            site_url="https://example.com",
            auth_ok=None,
        )

        with patch("rex.wordpress.service.get_wordpress_service", return_value=mock_service):
            rc = cmd_wp(self._make_args())

        assert rc == 0
        out = capsys.readouterr().out
        assert "myblog" in out
        assert "OK" in out

    def test_health_site_not_found(self, capsys):
        """Unknown site ID prints error and returns 1."""
        from rex.cli import cmd_wp

        mock_service = MagicMock()
        mock_service.health.side_effect = WordPressSiteNotFoundError(
            "No WordPress site with id 'myblog' found in config."
        )

        with patch("rex.wordpress.service.get_wordpress_service", return_value=mock_service):
            rc = cmd_wp(self._make_args())

        assert rc == 1
        out = capsys.readouterr().out
        assert "Error" in out

    def test_health_missing_credential(self, capsys):
        """Missing credential prints error and returns 1."""
        from rex.cli import cmd_wp

        mock_service = MagicMock()
        mock_service.health.side_effect = WordPressMissingCredentialError(
            "Credential not configured."
        )

        with patch("rex.wordpress.service.get_wordpress_service", return_value=mock_service):
            rc = cmd_wp(self._make_args())

        assert rc == 1

    def test_health_failed_result(self, capsys):
        """Failed health result returns exit code 1."""
        from rex.cli import cmd_wp

        mock_service = MagicMock()
        mock_service.health.return_value = WPHealthResult(
            ok=False,
            reachable=False,
            error="Connection refused",
        )

        with patch("rex.wordpress.service.get_wordpress_service", return_value=mock_service):
            rc = cmd_wp(self._make_args())

        assert rc == 1
