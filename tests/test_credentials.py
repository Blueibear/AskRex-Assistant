"""Tests for the credential manager module."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from rex.credentials import (
    Credential,
    CredentialManager,
    CredentialRefreshError,
    get_credential_manager,
    mask_token,
    set_credential_manager,
)

VAULT_REF = "cred_" + "V" * 32
OPENAI_VAULT_REFS = {
    "OPENAI_API_KEY": {
        "ref": VAULT_REF,
        "integration": "openai",
        "account": None,
        "slot": "api_key",
    }
}


class TestMaskToken:
    """Tests for the mask_token function."""

    def test_mask_token_normal(self):
        """A non-empty token is replaced with a constant redaction marker."""
        assert mask_token("abcd1234efgh5678") == "[redacted]"

    def test_mask_token_short(self):
        """Short tokens are also fully redacted, not length-revealing."""
        assert mask_token("short") == "[redacted]"

    def test_mask_token_empty(self):
        """Test masking an empty token."""
        assert mask_token("") == "[empty]"

    def test_mask_token_none(self):
        """Test masking None."""
        assert mask_token(None) == "[empty]"

    def test_mask_token_reveals_no_prefix_suffix_or_length(self):
        """Different tokens (including different lengths) mask identically."""
        assert mask_token("abcdefghijklmnop") == mask_token("xy") == "[redacted]"
        assert mask_token("abcdefghijklmnop", visible_chars=2) == "[redacted]"


class TestCredential:
    """Tests for the Credential dataclass."""

    def test_credential_creation(self):
        """Test creating a credential."""
        cred = Credential(
            name="test",
            token="secret123",
            expires_at=None,
            scopes=["read", "write"],
            source="env",
        )
        assert cred.name == "test"
        assert cred.token == "secret123"
        assert cred.expires_at is None
        assert cred.scopes == ["read", "write"]
        assert cred.source == "env"

    def test_credential_not_expired_when_no_expiry(self):
        """Test that credential without expiry is not expired."""
        cred = Credential(name="test", token="secret")
        assert not cred.is_expired()

    def test_credential_not_expired_when_future(self):
        """Test that credential with future expiry is not expired."""
        future = datetime.now(UTC) + timedelta(hours=1)
        cred = Credential(name="test", token="secret", expires_at=future)
        assert not cred.is_expired()

    def test_credential_expired_when_past(self):
        """Test that credential with past expiry is expired."""
        past = datetime.now(UTC) - timedelta(hours=1)
        cred = Credential(name="test", token="secret", expires_at=past)
        assert cred.is_expired()

    def test_credential_repr_masks_token(self):
        """repr reveals no prefix, suffix, length, or other secret-derived content."""
        cred = Credential(name="test", token="supersecrettoken123")
        repr_str = repr(cred)
        assert "supersecrettoken123" not in repr_str
        assert "supe" not in repr_str
        assert "n123" not in repr_str
        assert "[redacted]" in repr_str


class TestCredentialManager:
    """Tests for the CredentialManager class."""

    @pytest.fixture(autouse=True)
    def _enable_explicit_legacy_mode(self, monkeypatch):
        """These compatibility tests intentionally exercise plaintext inputs."""
        monkeypatch.setenv("REX_ALLOW_PLAINTEXT_CREDENTIAL_FALLBACK", "1")

    def test_load_from_env_with_prefix(self):
        """Test loading credentials from environment with REX_ prefix."""
        with patch.dict(os.environ, {"REX_EMAIL_TOKEN": "env_token_123"}):
            manager = CredentialManager()
            token = manager.get_token("email")
            assert token == "env_token_123"

    def test_load_from_env_without_prefix(self):
        """Test loading credentials from environment without prefix."""
        with patch.dict(
            os.environ, {"OPENAI_API_KEY": "openai_key_123"}, clear=False
        ):  # pragma: allowlist secret -- environment fixture
            manager = CredentialManager()
            token = manager.get_token("openai")
            assert token == "openai_key_123"

    def test_load_from_config_file(self):
        """Test loading credentials from JSON config file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "credentials.json"
            config_data = {"credentials": {"test_service": "config_token_456"}}
            config_path.write_text(json.dumps(config_data))

            manager = CredentialManager(config_path=config_path)
            token = manager.get_token("test_service")
            assert token == "config_token_456"

    def test_load_from_config_file_with_metadata(self):
        """Test loading credentials with full metadata from config."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "credentials.json"
            future = datetime.now(UTC) + timedelta(days=1)
            config_data = {
                "credentials": {
                    "full_service": {
                        "token": "full_token_789",
                        "expires_at": future.isoformat(),
                        "scopes": ["read", "write"],
                    }
                }
            }
            config_path.write_text(json.dumps(config_data))

            manager = CredentialManager(config_path=config_path)
            cred = manager.get_credential("full_service")
            assert cred is not None
            assert cred.token == "full_token_789"
            assert cred.scopes == ["read", "write"]
            assert not cred.is_expired()

    def test_config_overrides_env(self):
        """Test that config file overrides environment variables."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "credentials.json"
            config_data = {"credentials": {"email": "config_email_token"}}
            config_path.write_text(json.dumps(config_data))

            with patch.dict(os.environ, {"REX_EMAIL_TOKEN": "env_email_token"}):
                manager = CredentialManager(config_path=config_path)
                token = manager.get_token("email")
                assert token == "config_email_token"

    def test_get_token_returns_none_when_not_found(self):
        """Test that get_token returns None for unknown services."""
        manager = CredentialManager(config_path=Path("/nonexistent/path.json"))
        assert manager.get_token("unknown_service") is None

    def test_set_token_at_runtime(self):
        """Test setting a token at runtime."""
        manager = CredentialManager(config_path=Path("/nonexistent/path.json"))
        manager.set_token("new_service", "runtime_token")
        assert manager.get_token("new_service") == "runtime_token"

    def test_set_token_with_expiry(self):
        """Test setting a token with expiry."""
        manager = CredentialManager(config_path=Path("/nonexistent/path.json"))
        future = datetime.now(UTC) + timedelta(hours=1)
        manager.set_token("expiring_service", "expiring_token", expires_at=future)

        cred = manager.get_credential("expiring_service")
        assert cred is not None
        assert cred.expires_at == future
        assert not cred.is_expired()

    def test_has_token_returns_true_when_valid(self):
        """Test has_token returns True for valid token."""
        manager = CredentialManager(config_path=Path("/nonexistent/path.json"))
        manager.set_token("valid_service", "valid_token")
        assert manager.has_token("valid_service")

    def test_has_token_returns_false_when_missing(self):
        """Test has_token returns False for missing token."""
        manager = CredentialManager(config_path=Path("/nonexistent/path.json"))
        assert not manager.has_token("missing_service")

    def test_has_token_returns_false_when_expired(self):
        """Test has_token returns False for expired token."""
        manager = CredentialManager(config_path=Path("/nonexistent/path.json"))
        past = datetime.now(UTC) - timedelta(hours=1)
        manager.set_token("expired_service", "expired_token", expires_at=past)
        assert not manager.has_token("expired_service")

    def test_list_services(self):
        """Test listing all services with credentials."""
        manager = CredentialManager(config_path=Path("/nonexistent/path.json"))
        manager.set_token("service_a", "token_a")
        manager.set_token("service_b", "token_b")

        services = manager.list_services()
        assert "service_a" in services
        assert "service_b" in services

    def test_reload_preserves_runtime_credentials(self):
        """Test that reload preserves runtime credentials."""
        manager = CredentialManager(config_path=Path("/nonexistent/path.json"))
        manager.set_token("runtime_service", "runtime_token")

        manager.reload()

        assert manager.get_token("runtime_service") == "runtime_token"

    def test_refresh_token_without_handler_raises_error(self):
        """Test that refresh_token raises error without handler."""
        manager = CredentialManager(config_path=Path("/nonexistent/path.json"))
        manager.set_token("no_refresh_service", "original_token")

        with pytest.raises(CredentialRefreshError) as exc_info:
            manager.refresh_token("no_refresh_service")

        assert "no_refresh_service" in str(exc_info.value)
        assert "not implemented" in str(exc_info.value).lower()

    def test_refresh_token_with_handler(self):
        """Test that refresh_token works with registered handler."""
        manager = CredentialManager(config_path=Path("/nonexistent/path.json"))
        manager.set_token("refreshable", "old_token")

        def refresh_handler(current: str) -> str:
            return "new_token_from_refresh"

        manager.register_refresh_handler("refreshable", refresh_handler)
        new_token = manager.refresh_token("refreshable")

        assert new_token == "new_token_from_refresh"
        assert manager.get_token("refreshable") == "new_token_from_refresh"

    def test_add_credential_mapping(self):
        """Test adding custom credential mapping."""
        with patch.dict(os.environ, {"REX_CUSTOM_VAR": "custom_value"}):
            manager = CredentialManager(config_path=Path("/nonexistent/path.json"))
            manager.add_credential_mapping("custom_service", "CUSTOM_VAR")

            # Force reload to pick up new mapping
            manager.reload()

            token = manager.get_token("custom_service")
            assert token == "custom_value"

    def test_get_credential_info_contains_no_secret_derived_preview(self):
        """Credential metadata must not contain secret-derived output."""
        manager = CredentialManager(config_path=Path("/nonexistent/path.json"))
        manager.set_token("info_service", "supersecrettoken123")

        info = manager.get_credential_info("info_service")
        assert info is not None
        assert info["has_credential"] is True
        assert "token_preview" not in info
        assert "supersecrettoken123" not in repr(info)

    def test_get_credential_info_returns_none_for_unknown(self):
        """Test that get_credential_info returns None for unknown service."""
        manager = CredentialManager(config_path=Path("/nonexistent/path.json"))
        assert manager.get_credential_info("unknown") is None

    def test_expired_token_triggers_auto_refresh(self):
        """Test that expired token triggers auto-refresh when handler exists."""
        manager = CredentialManager(config_path=Path("/nonexistent/path.json"))
        past = datetime.now(UTC) - timedelta(hours=1)
        manager.set_token("auto_refresh", "expired_token", expires_at=past)

        def refresh_handler(current: str) -> str:
            return "refreshed_token"

        manager.register_refresh_handler("auto_refresh", refresh_handler)

        # get_token with auto_refresh=True should refresh
        token = manager.get_token("auto_refresh", auto_refresh=True)
        assert token == "refreshed_token"

    def test_expired_token_without_handler_returns_none(self):
        """Test that expired token without handler returns None."""
        manager = CredentialManager(config_path=Path("/nonexistent/path.json"))
        past = datetime.now(UTC) - timedelta(hours=1)
        manager.set_token("no_handler", "expired_token", expires_at=past)

        # get_token with auto_refresh=True but no handler returns None
        token = manager.get_token("no_handler", auto_refresh=True)
        assert token is None


class TestGlobalCredentialManager:
    """Tests for global credential manager functions."""

    def test_get_credential_manager_returns_singleton(self):
        """Test that get_credential_manager returns singleton."""
        # Reset global state
        set_credential_manager(None)  # type: ignore

        manager1 = get_credential_manager()
        manager2 = get_credential_manager()

        assert manager1 is manager2

    def test_set_credential_manager_replaces_singleton(self):
        """Test that set_credential_manager replaces the singleton."""
        custom_manager = CredentialManager(config_path=Path("/custom/path.json"))
        set_credential_manager(custom_manager)

        assert get_credential_manager() is custom_manager

        # Reset for other tests
        set_credential_manager(None)  # type: ignore


class TestCredentialManagerEdgeCases:
    """Edge case tests for CredentialManager."""

    @pytest.fixture(autouse=True)
    def _enable_explicit_legacy_mode(self, monkeypatch):
        """These compatibility tests intentionally exercise plaintext inputs."""
        monkeypatch.setenv("REX_ALLOW_PLAINTEXT_CREDENTIAL_FALLBACK", "1")

    def test_invalid_config_json(self):
        """Test handling of invalid JSON in config file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "credentials.json"
            config_path.write_text("invalid json {{{")

            manager = CredentialManager(config_path=config_path)
            # Should not raise, just skip loading
            assert manager.list_services() == []

    def test_config_with_non_dict_root(self):
        """Test handling of non-dict root in config file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "credentials.json"
            config_path.write_text('["not", "a", "dict"]')

            manager = CredentialManager(config_path=config_path)
            assert manager.list_services() == []

    def test_custom_env_prefix(self):
        """Test custom environment variable prefix."""
        with patch.dict(os.environ, {"CUSTOM_EMAIL_TOKEN": "custom_prefix_token"}):
            manager = CredentialManager(
                env_prefix="CUSTOM_",
                config_path=Path("/nonexistent/path.json"),
            )
            token = manager.get_token("email")
            assert token == "custom_prefix_token"

    def test_lazy_loading(self):
        """Test that credentials are loaded lazily."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "credentials.json"
            # Don't create the file yet

            manager = CredentialManager(config_path=config_path)
            assert not manager._loaded

            # Create the file after manager creation
            config_data = {"credentials": {"lazy_service": "lazy_token"}}
            config_path.write_text(json.dumps(config_data))

            # Now access should trigger loading
            token = manager.get_token("lazy_service")
            assert token == "lazy_token"
            assert manager._loaded


class TestCredentialManagerVaultIntegration:
    """Tests for vault-as-source integration (S4)."""

    def test_vault_takes_priority_over_config_and_env(self):
        from rex.credential_vault import InMemoryCredentialVault

        vault = InMemoryCredentialVault()
        vault.set_secret(VAULT_REF, "vault-key", integration="openai", account=None, slot="api_key")

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "credentials.json"
            config_path.write_text(json.dumps({"credentials": {"openai": "config-key"}}))
            with patch.dict(
                os.environ, {"OPENAI_API_KEY": "env-key"}, clear=False  # pragma: allowlist secret
            ):  # pragma: allowlist secret
                manager = CredentialManager(
                    config_path=config_path, vault=vault, vault_refs=OPENAI_VAULT_REFS
                )
                assert manager.get_token("openai") == "vault-key"

    def test_vault_unavailable_falls_through_to_env_only_in_legacy_fallback_mode(self, monkeypatch):
        """Explicit operator opt-in preserves the pre-vault behavior."""
        from rex.credential_vault import VaultUnavailableError

        monkeypatch.setenv("REX_ALLOW_PLAINTEXT_CREDENTIAL_FALLBACK", "1")
        with patch.dict(os.environ, {"REX_EMAIL_TOKEN": "env_token_123"}, clear=False):
            manager = CredentialManager(
                config_path=Path("/nonexistent/path.json"),
                use_vault=True,
                scope="household",
            )
            with patch(
                "rex.credential_vault.get_credential_vault",
                side_effect=VaultUnavailableError("no vault on this platform"),
            ):
                assert manager.get_token("email") == "env_token_123"

    def test_vault_unavailable_fails_closed_by_default_ignoring_env_and_config(self, monkeypatch):
        """Without the explicit legacy opt-in, a vault-unavailable manager
        must not silently trust plaintext env or config.json - this is the
        production (packaged Windows) default."""
        from rex.credential_vault import VaultUnavailableError

        monkeypatch.delenv("REX_ALLOW_PLAINTEXT_CREDENTIAL_FALLBACK", raising=False)
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "credentials.json"
            config_path.write_text(json.dumps({"credentials": {"email": "config_token_456"}}))
            with patch.dict(os.environ, {"REX_EMAIL_TOKEN": "env_token_123"}, clear=False):
                manager = CredentialManager(
                    config_path=config_path,
                    use_vault=True,
                    scope="household",
                )
                with patch(
                    "rex.credential_vault.get_credential_vault",
                    side_effect=VaultUnavailableError("no vault on this platform"),
                ):
                    assert manager.get_token("email") is None
                    assert manager.list_services() == []

    def test_env_and_config_ignored_by_default_even_when_vault_is_available(self, monkeypatch):
        """The default (no legacy opt-in) mode is vault-only, not just
        vault-preferred - a live vault with no entry for a service must not
        fall back to a plaintext value that happens to be set."""
        from rex.credential_vault import InMemoryCredentialVault

        monkeypatch.delenv("REX_ALLOW_PLAINTEXT_CREDENTIAL_FALLBACK", raising=False)
        vault = InMemoryCredentialVault()
        with patch.dict(
            os.environ, {"OPENAI_API_KEY": "env-key"}, clear=False  # pragma: allowlist secret
        ):  # pragma: allowlist secret
            manager = CredentialManager(config_path=Path("/nonexistent/path.json"), vault=vault)
            assert manager.get_token("openai") is None

    def test_use_vault_false_never_touches_vault_module(self, monkeypatch):
        monkeypatch.setenv("REX_ALLOW_PLAINTEXT_CREDENTIAL_FALLBACK", "1")
        manager = CredentialManager(
            config_path=Path("/nonexistent/path.json"),
            use_vault=False,
        )
        with patch.dict(
            os.environ, {"OPENAI_API_KEY": "env-key"}, clear=False  # pragma: allowlist secret
        ):  # pragma: allowlist secret
            assert manager.get_token("openai") == "env-key"
        assert manager._get_vault() is None

    def test_unmapped_service_uses_raw_name_as_vault_key(self):
        from rex.credential_vault import InMemoryCredentialVault

        vault = InMemoryCredentialVault()
        vault.set_secret(
            VAULT_REF,
            "alice:hunter2",
            integration="email",
            account="personal",
            slot="password",
        )
        refs = {
            "email:personal": {
                "ref": VAULT_REF,
                "integration": "email",
                "account": "personal",
                "slot": "password",
            }
        }
        manager = CredentialManager(
            config_path=Path("/nonexistent/path.json"), vault=vault, vault_refs=refs
        )
        assert (
            manager.get_token(
                "email:personal", integration="email", account="personal", slot="password"
            )
            == "alice:hunter2"
        )

    def test_nonopaque_configured_reference_fails_closed_before_vault_lookup(self):
        from rex.credential_vault import InMemoryCredentialVault, VaultCorruptedError

        refs = {
            "OPENAI_API_KEY": {
                "ref": "OPENAI_API_KEY",
                "integration": "openai",
                "account": None,
                "slot": "api_key",
            }
        }
        manager = CredentialManager(vault=InMemoryCredentialVault(), vault_refs=refs)
        with pytest.raises(VaultCorruptedError):
            manager.get_token("openai")

    def test_malformed_user_registry_fails_closed(self, monkeypatch):
        from rex.credential_vault import VaultCorruptedError

        monkeypatch.setattr(
            "rex.config_manager.load_config",
            lambda: {"credential_refs": {"users": []}},
        )
        manager = CredentialManager(scope="user", user_id="alice")
        with pytest.raises(VaultCorruptedError):
            manager.get_token("openai")

    def test_vault_sourced_credential_reports_vault_source(self):
        from rex.credential_vault import InMemoryCredentialVault

        vault = InMemoryCredentialVault()
        vault.set_secret(VAULT_REF, "vault-key", integration="openai", account=None, slot="api_key")
        manager = CredentialManager(
            config_path=Path("/nonexistent/path.json"),
            vault=vault,
            vault_refs=OPENAI_VAULT_REFS,
        )
        info = manager.get_credential_info("openai")
        assert info is not None
        assert info["source"] == "vault"
        assert info["has_credential"] is True
        assert "token_preview" not in info

    def test_set_token_persist_true_writes_through_to_vault(self):
        from rex.credential_vault import InMemoryCredentialVault

        vault = InMemoryCredentialVault()
        manager = CredentialManager(config_path=Path("/nonexistent/path.json"), vault=vault)
        persisted_ref = manager.set_token("openai", "new-persisted-key", persist=True)

        assert persisted_ref is not None
        assert (
            vault.get_secret(persisted_ref, integration="openai", account=None, slot="api_key")
            == "new-persisted-key"
        )
        assert manager.get_token("openai") == "new-persisted-key"

    def test_set_token_persist_false_does_not_touch_vault(self):
        from rex.credential_vault import InMemoryCredentialVault

        vault = InMemoryCredentialVault()
        manager = CredentialManager(config_path=Path("/nonexistent/path.json"), vault=vault)
        manager.set_token("openai", "runtime-only-key")

        assert vault.list_entries() == []
        assert manager.get_token("openai") == "runtime-only-key"

    def test_set_token_persist_true_without_vault_raises(self):
        from rex.credential_vault import VaultUnavailableError

        manager = CredentialManager(config_path=Path("/nonexistent/path.json"), use_vault=False)
        with pytest.raises(VaultUnavailableError):
            manager.set_token("openai", "some-key", persist=True)

    def test_reload_refreshes_vault_sourced_credentials(self):
        from rex.credential_vault import InMemoryCredentialVault

        vault = InMemoryCredentialVault()
        vault.set_secret(VAULT_REF, "first-key", integration="openai", account=None, slot="api_key")
        manager = CredentialManager(
            config_path=Path("/nonexistent/path.json"),
            vault=vault,
            vault_refs=OPENAI_VAULT_REFS,
        )
        assert manager.get_token("openai") == "first-key"

        vault.set_secret(
            VAULT_REF, "second-key", integration="openai", account=None, slot="api_key"
        )
        manager.reload()
        assert manager.get_token("openai") == "second-key"


class TestLegacyPlaintextFallbackFlag:
    """rex.credentials.legacy_plaintext_fallback_enabled (S4 fail-closed gate)."""

    def test_disabled_by_default_when_unset(self, monkeypatch):
        from rex.credentials import legacy_plaintext_fallback_enabled

        monkeypatch.delenv("REX_ALLOW_PLAINTEXT_CREDENTIAL_FALLBACK", raising=False)
        assert legacy_plaintext_fallback_enabled() is False

    @pytest.mark.parametrize("value", ["0", "false", "False", "no", "NO", "off", "garbage", ""])
    def test_disabled_for_falsy_values(self, monkeypatch, value):
        from rex.credentials import legacy_plaintext_fallback_enabled

        monkeypatch.setenv("REX_ALLOW_PLAINTEXT_CREDENTIAL_FALLBACK", value)
        assert legacy_plaintext_fallback_enabled() is False

    @pytest.mark.parametrize("value", ["1", "true", "True", "yes", "on"])
    def test_enabled_for_truthy_values(self, monkeypatch, value):
        from rex.credentials import legacy_plaintext_fallback_enabled

        monkeypatch.delenv("ASKREX_PACKAGED", raising=False)
        monkeypatch.setenv("REX_ALLOW_PLAINTEXT_CREDENTIAL_FALLBACK", value)
        assert legacy_plaintext_fallback_enabled() is True

    def test_packaged_process_rejects_legacy_flag_even_when_injected(self, monkeypatch):
        from rex.credentials import legacy_plaintext_fallback_enabled

        monkeypatch.setenv("ASKREX_PACKAGED", "1")
        monkeypatch.setenv("REX_ALLOW_PLAINTEXT_CREDENTIAL_FALLBACK", "1")
        assert legacy_plaintext_fallback_enabled() is False


class TestGlobalCredentialManagerContaminationGuard:
    """set_credential_manager must never install a per-user-scoped manager
    as the process-wide global (S4) - that would race one user's
    credentials into request handling for other users."""

    def test_household_scoped_manager_is_accepted(self):
        manager = CredentialManager(config_path=Path("/nonexistent/path.json"), scope="household")
        try:
            set_credential_manager(manager)
            assert get_credential_manager() is manager
        finally:
            set_credential_manager(None)

    def test_default_scoped_manager_is_accepted(self):
        manager = CredentialManager(config_path=Path("/nonexistent/path.json"))
        try:
            set_credential_manager(manager)
            assert get_credential_manager() is manager
        finally:
            set_credential_manager(None)

    def test_user_scoped_manager_is_rejected(self):
        manager = CredentialManager(
            config_path=Path("/nonexistent/path.json"), scope="user", user_id="alice"
        )
        with pytest.raises(ValueError):
            set_credential_manager(manager)

    def test_none_clears_the_global(self):
        set_credential_manager(None)
        # A fresh global is created lazily and must be household-scoped.
        assert get_credential_manager() is not None
