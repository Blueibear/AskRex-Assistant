"""Credential vault for secure token handling.

This module provides a centralized credential management system that:
- Loads credentials from the OS-backed vault by opaque contextual reference
- Permits legacy plaintext environment/config reads only by explicit opt-in
- Provides methods to get, set, and refresh tokens
- Never returns secret-derived previews from status APIs
- Checks token expiry and supports refresh stubs

Credentials are loaded lazily and can be refreshed at runtime.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from rex.credential_vault import CredentialVaultBackend

logger = logging.getLogger(__name__)

# Default environment variable prefix for Rex credentials
ENV_PREFIX = "REX_"

# Default mapping of service names to environment variable names
# Format: service_name -> env_var_name (without prefix)
DEFAULT_CREDENTIAL_MAPPING: dict[str, str] = {
    "email": "EMAIL_TOKEN",
    "calendar": "CALENDAR_TOKEN",
    "home_assistant": "HA_TOKEN",
    "brave": "BRAVE_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "ollama": "OLLAMA_API_KEY",
    "serpapi": "SERPAPI_API_KEY",
    "github": "GITHUB_TOKEN",
    "speak": "SPEAK_API_KEY",
    "openweathermap": "OPENWEATHERMAP_API_KEY",
}

_CREDENTIAL_CONTEXTS: dict[str, tuple[str, str | None, str]] = {
    "REX_JWT_SECRET": ("authentication", None, "jwt_signing_secret"),
    "REX_PROXY_TOKEN": ("gui_proxy", None, "token"),
    "REX_AGENT_TOKEN": ("computer_agent", None, "token"),
    "HA_TOKEN": ("home_assistant", None, "token"),
    "HA_SECRET": ("home_assistant", None, "secret"),
    "TWILIO_ACCOUNT_SID": ("twilio", "sms", "account_sid"),
    "TWILIO_AUTH_TOKEN": ("twilio", "sms", "auth_token"),
    "TWILIO_FROM_NUMBER": ("twilio", "sms", "from_number"),
    "TWILIO_PHONE_ACCOUNT_SID": ("twilio", "phone", "account_sid"),
    "TWILIO_PHONE_AUTH_TOKEN": ("twilio", "phone", "auth_token"),
    "TWILIO_PHONE_NUMBER": ("twilio", "phone", "phone_number"),
    "TWILIO_TRANSFER_NUMBER": ("twilio", "phone", "transfer_number"),
    "TELEGRAM_BOT_TOKEN": ("telegram", None, "token"),
    "OPENCLAW_GATEWAY_TOKEN": ("openclaw_gateway", None, "token"),
    "CLAUDE_CODE_OAUTH_TOKEN": ("claude_code", None, "oauth_token"),
    "PUSH_TOKEN": ("push", None, "token"),
}


def credential_context_for_name(name: str) -> tuple[str, str | None, str]:
    """Return the fixed authorization context for a logical credential name."""
    explicit = _CREDENTIAL_CONTEXTS.get(name.upper())
    if explicit is not None:
        return explicit
    return credential_integration_for_name(name), None, credential_slot_for_name(name)


def credential_slot_for_name(name: str) -> str:
    explicit = _CREDENTIAL_CONTEXTS.get(name.upper())
    if explicit is not None:
        return explicit[2]
    upper = name.upper()
    if upper.endswith("API_KEY") or upper.endswith("_SID") or upper.endswith("_NUMBER"):
        return "api_key" if upper.endswith("API_KEY") else upper.lower()
    if upper.endswith("_SECRET"):
        return "secret"
    if upper.endswith("_PASSWORD"):
        return "password"
    return "token"


def credential_integration_for_name(name: str) -> str:
    explicit = _CREDENTIAL_CONTEXTS.get(name.upper())
    if explicit is not None:
        return explicit[0]
    reverse = {env_name: service for service, env_name in DEFAULT_CREDENTIAL_MAPPING.items()}
    if name in reverse:
        return reverse[name]
    upper = name.upper()
    for suffix in ("_API_KEY", "_KEY"):
        if upper.endswith(suffix):
            return name[: -len(suffix)].lower()
    return name.lower()


# Default path for credential config file
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "credentials.json"

# Explicit, clearly-named opt-in for reading secrets from plaintext
# env/.env/config.json sources. Left unset (the default), normal production
# operation is vault-only: a secret not in the vault is simply absent, it is
# never read from plaintext as an automatic fallback. Packaged Electron
# actively removes this variable from every bridge child's environment so an
# operator shell cannot accidentally weaken the packaged application. Python
# also rejects the flag whenever ASKREX_PACKAGED=1 as defense in depth.
LEGACY_PLAINTEXT_FALLBACK_ENV_VAR = "REX_ALLOW_PLAINTEXT_CREDENTIAL_FALLBACK"


def legacy_plaintext_fallback_enabled() -> bool:
    """Whether plaintext env/.env/config.json may be read as a credential source.

    Returns:
        True only outside packaged mode when
        ``REX_ALLOW_PLAINTEXT_CREDENTIAL_FALLBACK`` is explicitly set to one
        of the documented truthy values. False (fail closed) by default.
    """
    if os.getenv("ASKREX_PACKAGED", "").strip().lower() in {"1", "true", "yes", "on"}:
        return False
    return os.getenv(LEGACY_PLAINTEXT_FALLBACK_ENV_VAR, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


REDACTED_MARKER = "[redacted]"


def mask_token(token: str | None, *, visible_chars: int = 4) -> str:
    """Return a constant redaction marker for a token, revealing nothing about it.

    Args:
        token: The token to mask. Present only to keep the call signature at
            existing call sites; its value never influences the output.
        visible_chars: Unused. Retained for call-site compatibility; no
            characters of the token are ever revealed.

    Returns:
        ``"[empty]"`` if the token is None or empty, otherwise a constant
        redaction marker that leaks no prefix, suffix, length, or hash of
        the underlying secret.
    """
    del visible_chars
    if not token:
        return "[empty]"
    return REDACTED_MARKER


@dataclass
class Credential:
    """A credential containing a token and optional metadata.

    Attributes:
        name: The service name this credential is for.
        token: The secret token value.
        expires_at: Optional expiration datetime (UTC).
        scopes: Optional list of permission scopes.
        source: Where the credential was loaded from (env, config, runtime).
    """

    name: str
    token: str
    expires_at: datetime | None = None
    scopes: list[str] = field(default_factory=list)
    source: str = "unknown"

    def is_expired(self) -> bool:
        """Check if the credential has expired.

        Returns:
            True if expires_at is set and is in the past, False otherwise.
        """
        if self.expires_at is None:
            return False
        return datetime.now(UTC) > self.expires_at

    def __repr__(self) -> str:
        """Safe representation that masks the token."""
        return (
            f"Credential(name={self.name!r}, token={mask_token(self.token)!r}, "
            f"expires_at={self.expires_at!r}, scopes={self.scopes!r}, source={self.source!r})"
        )


class CredentialRefreshError(Exception):
    """Raised when credential refresh fails or is not implemented."""

    def __init__(self, service: str, message: str) -> None:
        self.service = service
        self.message = message
        super().__init__(f"Failed to refresh credential for '{service}': {message}")


class CredentialManager:
    """Central manager for loading and accessing credentials.

    Production reads resolve contextual opaque references through the OS-backed
    vault. Plaintext environment and JSON sources are considered only when the
    explicit legacy/operator flag is enabled. Runtime-only values and refresh
    handlers remain supported without adding persistence authority.

    Example:
        >>> manager = CredentialManager()
        >>> token = manager.get_token("email")
        >>> if token is None:
        ...     print("Email token not configured")
    """

    def __init__(
        self,
        *,
        credential_mapping: dict[str, str] | None = None,
        config_path: Path | str | None = None,
        env_prefix: str = ENV_PREFIX,
        refresh_handlers: dict[str, Callable[[str], str]] | None = None,
        vault: CredentialVaultBackend | None = None,
        use_vault: bool = True,
        scope: str = "household",
        user_id: str | None = None,
        vault_refs: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        """Initialize the credential manager.

        Args:
            credential_mapping: Mapping of service names to env var names.
                Defaults to DEFAULT_CREDENTIAL_MAPPING.
            config_path: Path to JSON config file for overrides.
                Defaults to config/credentials.json.
            env_prefix: Prefix for environment variables (default "REX_").
            refresh_handlers: Optional dict of service_name -> refresh callable.
                Each callable takes the current token and returns a new one.
            vault: Inject a specific credential vault backend (mainly for
                tests). When omitted and `use_vault` is True, one is
                resolved lazily via `rex.credential_vault.get_credential_vault`.
            use_vault: Set False to never consult the vault (e.g. callers
                that intentionally only want config/env sources).
            scope: Vault scope to resolve when no `vault` is injected -
                `"household"` (default, matches pre-vault global behavior)
                or `"user"` (requires `user_id`).
            user_id: Validated Rex user id, required when `scope="user"`.
        """
        self._mapping = credential_mapping or DEFAULT_CREDENTIAL_MAPPING.copy()
        self._config_path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
        self._env_prefix = env_prefix
        self._refresh_handlers = refresh_handlers or {}
        self._credentials: dict[str, Credential] = {}
        self._loaded = False
        self._config_invalid = False
        self._vault: CredentialVaultBackend | None = vault
        self._use_vault = use_vault
        self._vault_scope = scope
        self._vault_user_id = user_id
        self._vault_resolved = vault is not None
        self._vault_refs = dict(vault_refs) if vault_refs is not None else None

    def _load_from_env(self) -> None:
        """Load credentials from environment variables."""
        for service_name, env_var in self._mapping.items():
            # Try with prefix first, then without
            full_var = f"{self._env_prefix}{env_var}"
            token = os.getenv(full_var)
            if token is None:
                # Try without prefix (for standard vars like OPENAI_API_KEY)
                token = os.getenv(env_var)

            if token:
                self._credentials[service_name] = Credential(
                    name=service_name,
                    token=token,
                    source="env",
                )
                logger.debug("Loaded credential for %s from environment", service_name)

    def _load_from_config(self) -> None:
        """Load credentials from JSON config file if it exists."""
        self._config_invalid = False
        if not self._config_path.exists():
            logger.debug("Credential config file not found at %s", self._config_path)
            return

        try:
            with open(self._config_path, encoding="utf-8") as f:
                config = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load credential config: %s", e)
            self._config_invalid = True
            self._credentials.clear()
            return

        if not isinstance(config, dict):
            logger.warning("Invalid credential config format (expected dict)")
            self._config_invalid = True
            self._credentials.clear()
            return

        credentials_section = config.get("credentials", config)
        if not isinstance(credentials_section, dict):
            logger.warning("Invalid credentials section format")
            self._config_invalid = True
            self._credentials.clear()
            return

        for service_name, cred_data in credentials_section.items():
            if isinstance(cred_data, str):
                # Simple format: just the token
                token = cred_data
                expires_at = None
                scopes: list[str] = []
            elif isinstance(cred_data, dict):
                # Full format with metadata
                token = cred_data.get("token")  # type: ignore[assignment]
                if not token:
                    continue
                expires_at_str = cred_data.get("expires_at")
                expires_at = None
                if expires_at_str:
                    try:
                        expires_at = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))
                    except ValueError:
                        logger.warning("Invalid expires_at format for %s", service_name)
                scopes = cred_data.get("scopes", [])
                if not isinstance(scopes, list):
                    scopes = []
            else:
                continue

            # Config overrides env
            self._credentials[service_name] = Credential(
                name=service_name,
                token=token,
                expires_at=expires_at,
                scopes=scopes,
                source="config",
            )
            logger.debug("Loaded credential for %s from config", service_name)

    def _get_vault(self) -> CredentialVaultBackend | None:
        """Lazily resolve and cache the vault backend for this manager.

        A known platform/backend availability failure is cached as "no vault
        available". Unexpected construction failures are surfaced so a
        packaged process cannot silently downgrade or hide corruption.
        """
        if self._vault is not None:
            return self._vault
        if not self._use_vault or self._vault_resolved:
            return self._vault
        self._vault_resolved = True
        try:
            from rex.credential_vault import VaultUnavailableError, get_credential_vault

            self._vault = get_credential_vault(scope=self._vault_scope, user_id=self._vault_user_id)
        except VaultUnavailableError as exc:
            logger.debug("Credential vault unavailable: %s", exc)
            self._vault = None
        return self._vault

    def _vault_key_for_service(self, service_name: str) -> str:
        """Map a service name to its vault key (mapped env var name, or itself)."""
        return self._mapping.get(service_name, service_name)

    def _configured_vault_refs(self) -> dict[str, dict[str, Any]]:
        if self._vault_refs is not None:
            section: object = self._vault_refs
        else:
            try:
                from rex.config_manager import load_config

                raw = load_config() or {}
            except Exception as exc:
                from rex.credential_vault import VaultCorruptedError

                raise VaultCorruptedError(
                    "Credential reference registry could not be loaded"
                ) from exc
            root = raw.get("credential_refs")
            if root is None:
                return {}
            if not isinstance(root, dict):
                from rex.credential_vault import VaultCorruptedError

                raise VaultCorruptedError("Credential reference registry is invalid")
            if self._vault_scope == "household":
                section = root.get("household")
            else:
                users = root.get("users")
                if users is not None and not isinstance(users, dict):
                    from rex.credential_vault import VaultCorruptedError

                    raise VaultCorruptedError("User credential reference registry is invalid")
                section = users.get(self._vault_user_id) if isinstance(users, dict) else None
            if section is None:
                return {}
        if not isinstance(section, dict) or any(
            not isinstance(key, str) or not isinstance(value, dict)
            for key, value in section.items()
        ):
            from rex.credential_vault import VaultCorruptedError

            raise VaultCorruptedError("Credential reference registry section is invalid")
        return section

    def _vault_context_for_service(
        self,
        service_name: str,
        *,
        integration: str | None = None,
        account: str | None = None,
        slot: str | None = None,
    ) -> tuple[str, str, str | None, str] | None:
        logical_name = self._vault_key_for_service(service_name)
        record = self._configured_vault_refs().get(logical_name)
        if record is None:
            return None
        ref = record.get("ref")
        allowed_fields = {"ref", "integration", "account", "slot"}
        if "migrated_from" in record:
            allowed_fields.add("migrated_from")
        if (
            set(record) != allowed_fields
            or not isinstance(ref, str)
            or (
                "migrated_from" in record
                and record.get("migrated_from") not in {"env", "credentials.json"}
            )
        ):
            from rex.credential_vault import VaultCorruptedError

            raise VaultCorruptedError("Credential reference metadata is invalid")
        from rex.credential_vault import VaultCorruptedError, validate_credential_ref

        try:
            validate_credential_ref(ref)
        except ValueError as exc:
            raise VaultCorruptedError("Credential reference metadata is invalid") from exc
        derived_integration, derived_account, derived_slot = credential_context_for_name(
            logical_name
        )
        expected_integration = integration or derived_integration
        expected_slot = slot or derived_slot
        expected_account = account if account is not None else derived_account
        if (
            record.get("integration") != expected_integration
            or record.get("account") != expected_account
            or record.get("slot") != expected_slot
        ):
            from rex.credential_vault import VaultCorruptedError

            raise VaultCorruptedError("Credential reference context metadata is invalid")
        return ref, expected_integration, expected_account, expected_slot

    def _load_from_vault(self) -> None:
        """Load credentials from the vault, if one is available.

        Runs last in `_ensure_loaded()` so vault entries take priority over
        both config file and environment sources, and independently of
        whether the config file itself was valid.
        """
        contexts: list[tuple[str, tuple[str, str, str | None, str]]] = []
        for service_name in self._mapping:
            context = self._vault_context_for_service(service_name)
            if context is not None:
                contexts.append((service_name, context))
        if not contexts:
            return
        vault = self._get_vault()
        if vault is None:
            return

        for service_name, context in contexts:
            ref, integration, account, slot = context
            try:
                token = vault.get_secret(ref, integration=integration, account=account, slot=slot)
            except Exception:
                logger.warning("Failed to read configured vault credential for %s", service_name)
                raise
            if not token:
                continue
            self._credentials[service_name] = Credential(
                name=service_name,
                token=token,
                source="vault",
            )
            logger.debug("Loaded credential for %s from vault", service_name)

    def _ensure_loaded(self) -> None:
        """Ensure credentials are loaded (lazy loading).

        Plaintext env/.env/config.json sources are only consulted when
        `legacy_plaintext_fallback_enabled()` is true (an explicit,
        non-production opt-in - see its docstring). By default this manager
        is vault-only: a secret not present in the vault is simply absent,
        never silently read from plaintext. The vault is always consulted
        last regardless, so it wins when both a legacy source and the vault
        have a value for the same service.
        """
        if not self._loaded:
            if legacy_plaintext_fallback_enabled():
                self._load_from_env()
                self._load_from_config()
                if self._config_invalid:
                    self._credentials.clear()
            self._load_from_vault()
            self._loaded = True

    def reload(self) -> None:
        """Reload all credentials from environment and config.

        This clears the cache and reloads from all sources.
        Runtime credentials set via set_token() will be preserved.
        """
        runtime_creds = {
            name: cred for name, cred in self._credentials.items() if cred.source == "runtime"
        }
        self._credentials.clear()
        self._loaded = False
        self._ensure_loaded()
        # Restore runtime credentials
        self._credentials.update(runtime_creds)
        logger.info("Reloaded credentials")

    def get_credential(
        self,
        service_name: str,
        *,
        integration: str | None = None,
        account: str | None = None,
        slot: str | None = None,
    ) -> Credential | None:
        """Get the full credential object for a service.

        Args:
            service_name: Name of the service (e.g., "email", "openai").

        Returns:
            Credential object or None if not found.
        """
        self._ensure_loaded()
        if service_name not in self._mapping and service_name not in self._credentials:
            context = self._vault_context_for_service(
                service_name, integration=integration, account=account, slot=slot
            )
            vault = self._get_vault() if context is not None else None
            if vault is not None and context is not None:
                ref, expected_integration, expected_account, expected_slot = context
                token = vault.get_secret(
                    ref,
                    integration=expected_integration,
                    account=expected_account,
                    slot=expected_slot,
                )
                if token:
                    self._credentials[service_name] = Credential(
                        name=service_name, token=token, source="vault"
                    )
        return self._credentials.get(service_name)

    def get_token(
        self,
        service_name: str,
        *,
        auto_refresh: bool = True,
        integration: str | None = None,
        account: str | None = None,
        slot: str | None = None,
    ) -> str | None:
        """Get the token for a service.

        If the token is expired and auto_refresh is True, attempts to refresh it.

        Args:
            service_name: Name of the service (e.g., "email", "openai").
            auto_refresh: Whether to attempt refresh if token is expired.

        Returns:
            Token string or None if not found or refresh failed.
        """
        credential = self.get_credential(
            service_name, integration=integration, account=account, slot=slot
        )
        if credential is None:
            return None

        if credential.is_expired() and auto_refresh:
            try:
                new_token = self.refresh_token(service_name)
                return new_token
            except CredentialRefreshError:
                logger.warning("Token for %s is expired and refresh failed", service_name)
                return None

        return credential.token

    def set_token(
        self,
        service_name: str,
        token: str,
        *,
        expires_at: datetime | None = None,
        scopes: list[str] | None = None,
        persist: bool = False,
        integration: str | None = None,
        account: str | None = None,
        slot: str | None = None,
        credential_ref: str | None = None,
    ) -> str | None:
        """Set or update a token at runtime, optionally persisting it to the vault.

        Args:
            service_name: Name of the service.
            token: The new token value.
            expires_at: Optional expiration datetime (UTC).
            scopes: Optional list of permission scopes.
            persist: When True, write the token through to the credential
                vault (in addition to updating the in-memory value). Raises
                `rex.credential_vault.VaultUnavailableError` if no vault is
                available - this method never falls back to writing
                plaintext config/env on a persist failure.
        """
        self._ensure_loaded()
        logical_name = self._vault_key_for_service(service_name)
        persisted_ref: str | None = None
        if persist:
            from rex.credential_vault import VaultUnavailableError, generate_credential_ref

            vault = self._get_vault()
            if vault is None:
                raise VaultUnavailableError(
                    f"Cannot persist credential for {service_name!r}: "
                    "no credential vault is available on this platform."
                )
            derived_integration, derived_account, derived_slot = credential_context_for_name(
                logical_name
            )
            expected_integration = integration or derived_integration
            expected_slot = slot or derived_slot
            expected_account = account if account is not None else derived_account
            persisted_ref = credential_ref or generate_credential_ref()
            vault.set_secret(
                persisted_ref,
                token,
                integration=expected_integration,
                account=expected_account,
                slot=expected_slot,
            )
            if (
                vault.get_secret(
                    persisted_ref,
                    integration=expected_integration,
                    account=expected_account,
                    slot=expected_slot,
                )
                != token
            ):
                raise VaultUnavailableError("Credential vault readback verification failed")
        self._credentials[service_name] = Credential(
            name=service_name,
            token=token,
            expires_at=expires_at,
            scopes=scopes or [],
            source="vault" if persist else "runtime",
        )
        logger.debug(
            "Set credential for %s (%s)",
            service_name,
            "persisted to vault" if persist else "runtime",
        )
        return persisted_ref

    def refresh_token(self, service_name: str) -> str:
        """Refresh a token for a service.

        If a refresh handler is registered for the service, it will be called.
        Otherwise, raises CredentialRefreshError.

        Args:
            service_name: Name of the service to refresh.

        Returns:
            The new token value.

        Raises:
            CredentialRefreshError: If refresh fails or no handler is registered.
        """
        if service_name not in self._refresh_handlers:
            raise CredentialRefreshError(
                service_name,
                "No refresh handler registered. Token refresh not implemented for this service.",
            )

        credential = self.get_credential(service_name)
        current_token = credential.token if credential else ""

        try:
            handler = self._refresh_handlers[service_name]
            new_token = handler(current_token)
        except Exception as e:
            raise CredentialRefreshError(service_name, str(e)) from e

        # Update stored credential
        self.set_token(
            service_name,
            new_token,
            expires_at=credential.expires_at if credential else None,
            scopes=credential.scopes if credential else None,
        )
        logger.info("Refreshed token for %s", service_name)
        return new_token

    def register_refresh_handler(
        self,
        service_name: str,
        handler: Callable[[str], str],
    ) -> None:
        """Register a refresh handler for a service.

        Args:
            service_name: Name of the service.
            handler: Callable that takes current token and returns new token.
        """
        self._refresh_handlers[service_name] = handler
        logger.debug("Registered refresh handler for %s", service_name)

    def list_services(self) -> list[str]:
        """List all services that have credentials configured.

        Returns:
            List of service names with available credentials.
        """
        self._ensure_loaded()
        return list(self._credentials.keys())

    def has_token(self, service_name: str) -> bool:
        """Check if a token is available for a service.

        Args:
            service_name: Name of the service.

        Returns:
            True if token exists and is not expired.
        """
        credential = self.get_credential(service_name)
        if credential is None:
            return False
        return not credential.is_expired()

    def add_credential_mapping(self, service_name: str, env_var: str) -> None:
        """Add a custom credential mapping.

        Args:
            service_name: Name of the service.
            env_var: Environment variable name (without prefix).
        """
        self._mapping[service_name] = env_var
        # If already loaded, check for this new mapping
        if self._loaded:
            token = None
            if legacy_plaintext_fallback_enabled():
                token = os.getenv(f"{self._env_prefix}{env_var}") or os.getenv(env_var)
            if token and service_name not in self._credentials:
                self._credentials[service_name] = Credential(
                    name=service_name,
                    token=token,
                    source="env",
                )

    def get_credential_info(self, service_name: str) -> dict[str, Any] | None:
        """Get non-secret credential state without deriving output from the token.

        Args:
            service_name: Name of the service.

        Returns:
            Dict with credential metadata or None.
        """
        credential = self.get_credential(service_name)
        if credential is None:
            return None
        return {
            "name": credential.name,
            "has_credential": True,
            "expires_at": credential.expires_at.isoformat() if credential.expires_at else None,
            "scopes": credential.scopes,
            "source": credential.source,
            "is_expired": credential.is_expired(),
        }


# Global credential manager instance
_credential_manager: CredentialManager | None = None


def get_credential_manager() -> CredentialManager:
    """Get the global credential manager instance.

    Creates a new instance if one doesn't exist.

    Returns:
        The global CredentialManager instance.
    """
    global _credential_manager
    if _credential_manager is None:
        _credential_manager = CredentialManager()
    return _credential_manager


def set_credential_manager(manager: CredentialManager | None) -> None:
    """Set the global credential manager instance.

    The global singleton must remain household/system-scoped. Installing a
    per-user-scoped manager here would make one user's credentials the
    process-wide default, racing across overlapping requests for other
    users (the same global-mutable-identity hazard documented for
    `Assistant` in CLAUDE.md). Per-user credential access must construct
    and use a local `CredentialManager(scope="user", user_id=...)` instead
    of routing through this global.

    Args:
        manager: The CredentialManager instance to use globally, or None to
            clear it.

    Raises:
        ValueError: If `manager` is scoped to a specific user.
    """
    global _credential_manager
    if manager is not None and getattr(manager, "_vault_scope", "household") == "user":
        raise ValueError(
            "The global credential manager must remain household-scoped; "
            "construct a per-user CredentialManager(scope='user', user_id=...) "
            "locally instead of installing one globally."
        )
    _credential_manager = manager


def get_persisted_credential(
    logical_name: str,
    *,
    scope: str = "household",
    user_id: str | None = None,
    integration: str | None = None,
    account: str | None = None,
    slot: str | None = None,
) -> str | None:
    """Resolve one credential with a request-local, scope-bound manager.

    This helper deliberately never uses the global manager, so user-scoped
    secrets cannot survive in process-global cache across requests.
    """
    manager = CredentialManager(
        credential_mapping={logical_name: logical_name},
        scope=scope,
        user_id=user_id,
    )
    return manager.get_token(
        logical_name,
        integration=integration,
        account=account,
        slot=slot,
    )


__all__ = [
    "Credential",
    "CredentialManager",
    "CredentialRefreshError",
    "LEGACY_PLAINTEXT_FALLBACK_ENV_VAR",
    "get_credential_manager",
    "legacy_plaintext_fallback_enabled",
    "set_credential_manager",
    "mask_token",
    "REDACTED_MARKER",
    "DEFAULT_CREDENTIAL_MAPPING",
    "credential_context_for_name",
    "get_persisted_credential",
]
