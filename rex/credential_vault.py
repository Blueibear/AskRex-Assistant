"""OS-backed credential vault for desktop secrets (S4).

Secrets (API keys, tokens, passwords) live only in an OS-backed credential
authority. Windows uses DPAPI via ``pywin32``; macOS uses the native Keychain
through the ``keyring`` macOS backend. Normal config files
(``rex_config.json``, ``gui_settings.json``, ``.env``) must only ever hold
an opaque reference (the vault *key*), never the secret value.

Two scopes are supported, mirroring ``rex.runtime_paths``' existing
household/private split:

- ``"household"`` — shared, installation-wide secrets (API keys, HA token).
  Matches today's behavior for every existing unscoped credential consumer.
- ``"user"`` — bound to one validated Rex ``user_id``.

Windows binds DPAPI ciphertext to the Windows login plus scope/context
entropy. macOS stores each opaque reference in the native Keychain with the
same validated scope/owner/integration/account/slot metadata, so cross-context
reference reuse fails closed on both platforms.

Windows DPAPI storage hardening (S4 correction pass):

- The on-disk JSON store is a versioned envelope (``__version__`` +
  ``entries``); an unrecognized/missing version fails closed with
  ``VaultCorruptedError`` instead of being silently accepted.
- Every mutating operation (and, for simplicity/correctness, every read)
  holds an OS-level interprocess file lock (``msvcrt.locking``) around the
  read-modify-write cycle, so concurrent writers cannot lose updates and
  concurrent readers never observe a torn write.
- Writes go through a per-process temp file, ``fsync``, then an atomic
  ``os.replace`` onto the real path - a crash mid-write cannot corrupt the
  store.
- The vault file (and lock file) get their ACL reset to grant access to the
  current Windows user only. ACL hardening failure is fatal; packaged
  production never silently downgrades to DPAPI with a broadly readable
  ciphertext store.
- Every entry's stored ``scope``/``owner`` metadata is validated against the
  vault instance performing the read before its ciphertext is decrypted (or,
  for ``list_entries``, before it is surfaced at all) - metadata that has
  been copied, tampered with, or otherwise ended up inconsistent with the
  vault instance's own identity fails closed rather than returning
  data for the wrong owner. Callers that know which ``integration``/
  ``account`` they expect (e.g. ``CredentialManager``) can pass those
  through to ``get_secret`` for an additional cross-check.

This module never falls back to plaintext persistence. If the OS backend is
unavailable, callers get ``VaultUnavailableError`` and must fail closed -
see ``rex/credentials.py`` and ``rex/config.py`` for how read paths degrade
gracefully to their pre-vault behavior (only in an explicit, non-production
opt-in mode - see ``rex.credentials.legacy_plaintext_fallback_enabled``)
while write paths never do.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import secrets
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO, Protocol, TypeVar, runtime_checkable

logger = logging.getLogger(__name__)

try:
    import win32crypt
    import win32cryptcon

    _PYWIN32_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only when pywin32 is absent
    win32crypt = None  # optional Windows-only dependency
    win32cryptcon = None  # optional Windows-only dependency
    _PYWIN32_AVAILABLE = False

try:
    import msvcrt

    _MSVCRT_AVAILABLE = True
except ImportError:  # pragma: no cover - msvcrt is stdlib but Windows-only
    msvcrt = None  # type: ignore[assignment]
    _MSVCRT_AVAILABLE = False

try:
    import fcntl

    _FCNTL_AVAILABLE = True
except ImportError:  # pragma: no cover - fcntl is POSIX-only
    fcntl = None  # type: ignore[assignment]
    _FCNTL_AVAILABLE = False

try:
    import ntsecuritycon
    import win32api
    import win32security

    _WIN32SECURITY_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only when pywin32 is absent
    ntsecuritycon = None
    win32api = None
    win32security = None
    _WIN32SECURITY_AVAILABLE = False

try:
    import keyring as _keyring
except ImportError:  # pragma: no cover - depends on platform/runtime packaging
    _keyring = None

_ENTROPY_PREFIX = b"askrex-credential-vault:v1:"
_VALID_SCOPES = {"household", "user"}
_SCHEMA_VERSION = 2
_LOCK_TIMEOUT_SECONDS = 10.0
_LOCK_POLL_SECONDS = 0.05
_WINDOWS_REPLACE_TIMEOUT_SECONDS = 2.0
_REFERENCE_PATTERN = re.compile(r"^cred_[A-Za-z0-9_-]{32}$")
_CONTEXT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$")
_T = TypeVar("_T")


class VaultUnavailableError(Exception):
    """The OS-backed vault cannot be used in this process/platform.

    Raised at construction/factory time, or when the vault is transiently
    unusable (e.g. a lock acquisition timeout) - never a signal to silently
    fall back to plaintext persistence. Write paths must surface this to the
    caller; read paths may fail through to pre-vault behavior only under an
    explicit, non-production opt-in (see
    ``rex.credentials.legacy_plaintext_fallback_enabled``).
    """


class VaultCorruptedError(Exception):
    """A vault entry could not be decrypted or trusted.

    Covers a genuinely corrupted store, an unsupported/missing schema
    version, and a scope/owner/integration/account metadata mismatch
    (including a copied or swapped reference) - all of these must fail
    closed rather than return wrong data.
    """


@dataclass(frozen=True)
class VaultEntryMetadata:
    """Non-secret metadata about one vault entry. Never carries the value."""

    key: str
    integration: str
    account: str | None
    slot: str
    scope: str
    owner: str
    created_at: str
    updated_at: str


@runtime_checkable
class CredentialVaultBackend(Protocol):
    """Contract shared by every vault backend implementation."""

    def set_secret(
        self,
        key: str,
        value: str,
        *,
        integration: str,
        account: str | None,
        slot: str,
    ) -> None: ...

    def get_secret(
        self, key: str, *, integration: str, account: str | None, slot: str
    ) -> str | None: ...

    def delete_secret(
        self, key: str, *, integration: str, account: str | None, slot: str
    ) -> bool: ...

    def has_secret(self, key: str, *, integration: str, account: str | None, slot: str) -> bool: ...

    def list_entries(self) -> list[VaultEntryMetadata]: ...


def _validate_scope(scope: str, user_id: str | None) -> tuple[str, str]:
    """Return (scope, owner) after validating. owner is 'household' or the user_id."""
    if scope not in _VALID_SCOPES:
        raise ValueError(f"Unknown vault scope: {scope!r}")
    if scope == "user":
        if not user_id:
            raise ValueError("user_id is required when scope='user'")
        from rex.identity import validate_user_id

        return scope, validate_user_id(user_id)
    return scope, "household"


def generate_credential_ref() -> str:
    """Return a random opaque reference suitable for non-secret config."""
    return f"cred_{secrets.token_urlsafe(24)}"


def validate_credential_ref(key: str) -> str:
    """Validate and return an opaque credential reference."""
    if not isinstance(key, str) or not _REFERENCE_PATTERN.fullmatch(key):
        raise ValueError("Credential reference is not a valid opaque reference")
    return key


def _validate_context(
    key: str, integration: str, account: str | None, slot: str
) -> tuple[str, str, str | None, str]:
    """Validate the caller-owned authorization context for one credential."""
    key = validate_credential_ref(key)
    for label, value in (("integration", integration), ("slot", slot)):
        if not isinstance(value, str) or not _CONTEXT_PATTERN.fullmatch(value):
            raise ValueError(f"Credential {label} is invalid")
    if account is not None and (
        not isinstance(account, str) or not _CONTEXT_PATTERN.fullmatch(account)
    ):
        raise ValueError("Credential account is invalid")
    return key, integration, account, slot


def _entropy_for_entry(
    scope: str,
    owner: str,
    key: str,
    integration: str,
    account: str | None,
    slot: str,
) -> bytes:
    """Bind DPAPI ciphertext to every caller-validated context dimension."""
    fields = (scope, owner, key, integration, account or "", slot)
    return hashlib.sha256(_ENTROPY_PREFIX + "\0".join(fields).encode("utf-8")).digest()


class InMemoryCredentialVault:
    """In-memory vault backend.

    Test/dev-only. Never selected implicitly by a production code path -
    ``get_credential_vault()`` only returns this when the caller explicitly
    passes ``backend="memory"``. Honors the same metadata-validation
    contract as ``WindowsDpapiCredentialVault`` (scope/owner/integration/
    account checked on every read) so tests that use this backend for
    speed/portability exercise real isolation behavior, not a stub that
    merely looks similar.
    """

    def __init__(
        self,
        *,
        scope: str = "household",
        user_id: str | None = None,
        store: dict[str, tuple[str, VaultEntryMetadata]] | None = None,
    ) -> None:
        self._scope, self._owner = _validate_scope(scope, user_id)
        # `store` may be shared across instances (e.g. a test simulating two
        # scopes' entries landing in one physical store) - defaults to a
        # private dict, matching one-vault-per-scope production usage.
        self._entries: dict[str, tuple[str, VaultEntryMetadata]] = (
            store if store is not None else {}
        )

    def set_secret(
        self,
        key: str,
        value: str,
        *,
        integration: str,
        account: str | None,
        slot: str,
    ) -> None:
        key, integration, account, slot = _validate_context(key, integration, account, slot)
        if not isinstance(value, str) or not value:
            raise ValueError("Credential value must be a non-empty string")
        now = datetime.now(UTC).isoformat()
        existing = self._entries.get(key)
        if existing is not None:
            self._validated(
                key,
                existing[1],
                integration=integration,
                account=account,
                slot=slot,
            )
        created_at = existing[1].created_at if existing else now
        meta = VaultEntryMetadata(
            key=key,
            integration=integration,
            account=account,
            slot=slot,
            scope=self._scope,
            owner=self._owner,
            created_at=created_at,
            updated_at=now,
        )
        self._entries[key] = (value, meta)

    def _validated(
        self,
        key: str,
        meta: VaultEntryMetadata,
        *,
        integration: str,
        account: str | None,
        slot: str,
    ) -> None:
        if meta.scope != self._scope or meta.owner != self._owner:
            raise VaultCorruptedError(
                f"Vault entry {key!r} scope/owner metadata does not match this vault; "
                "refusing to use it."
            )
        if meta.integration != integration:
            raise VaultCorruptedError(f"Vault entry {key!r} integration metadata mismatch.")
        if meta.account != account:
            raise VaultCorruptedError(f"Vault entry {key!r} account metadata mismatch.")
        if meta.slot != slot:
            raise VaultCorruptedError(f"Vault entry {key!r} credential-slot metadata mismatch.")

    def get_secret(
        self, key: str, *, integration: str, account: str | None, slot: str
    ) -> str | None:
        key, integration, account, slot = _validate_context(key, integration, account, slot)
        entry = self._entries.get(key)
        if entry is None:
            return None
        value, meta = entry
        self._validated(key, meta, integration=integration, account=account, slot=slot)
        return value

    def delete_secret(self, key: str, *, integration: str, account: str | None, slot: str) -> bool:
        key, integration, account, slot = _validate_context(key, integration, account, slot)
        entry = self._entries.get(key)
        if entry is None:
            return False
        self._validated(key, entry[1], integration=integration, account=account, slot=slot)
        del self._entries[key]
        return True

    def has_secret(self, key: str, *, integration: str, account: str | None, slot: str) -> bool:
        return self.get_secret(key, integration=integration, account=account, slot=slot) is not None

    def list_entries(self) -> list[VaultEntryMetadata]:
        result = []
        for key, (_, meta) in self._entries.items():
            if meta.scope != self._scope or meta.owner != self._owner:
                raise VaultCorruptedError("Vault entry scope/owner metadata is invalid")
            _validate_context(key, meta.integration, meta.account, meta.slot)
            result.append(meta)
        return result


_ENTRY_FIELDS = {
    "ciphertext",
    "integration",
    "account",
    "slot",
    "scope",
    "owner",
    "created_at",
    "updated_at",
}


def _validate_schema(raw: object) -> dict:
    """Validate the on-disk envelope shape; fail closed on anything unexpected."""
    if not isinstance(raw, dict):
        raise VaultCorruptedError("Vault file has an invalid top-level structure.")
    version = raw.get("__version__")
    if version != _SCHEMA_VERSION:
        raise VaultCorruptedError(
            f"Vault file schema version {version!r} is not supported "
            f"(expected {_SCHEMA_VERSION}); refusing to read it."
        )
    entries = raw.get("entries")
    if not isinstance(entries, dict):
        raise VaultCorruptedError("Vault file is missing a valid 'entries' section.")
    if set(raw) != {"__version__", "entries"}:
        raise VaultCorruptedError("Vault file contains unexpected top-level fields.")
    for key, entry in entries.items():
        if not isinstance(entry, dict) or set(entry) != _ENTRY_FIELDS:
            raise VaultCorruptedError("Vault entry has an invalid schema.")
        try:
            _validate_context(
                key,
                entry["integration"],
                entry["account"],
                entry["slot"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise VaultCorruptedError("Vault entry has invalid context metadata.") from exc
        if entry["scope"] not in _VALID_SCOPES or not isinstance(entry["owner"], str):
            raise VaultCorruptedError("Vault entry has invalid ownership metadata.")
        if entry["scope"] == "household" and entry["owner"] != "household":
            raise VaultCorruptedError("Vault entry has invalid household ownership metadata.")
        if entry["scope"] == "user":
            try:
                from rex.identity import validate_user_id

                validate_user_id(entry["owner"])
            except (TypeError, ValueError) as exc:
                raise VaultCorruptedError(
                    "Vault entry has invalid user ownership metadata."
                ) from exc
        if not isinstance(entry["ciphertext"], str):
            raise VaultCorruptedError("Vault entry ciphertext is invalid.")
        for timestamp_field in ("created_at", "updated_at"):
            timestamp = entry[timestamp_field]
            if not isinstance(timestamp, str):
                raise VaultCorruptedError("Vault entry timestamp is invalid.")
            try:
                datetime.fromisoformat(timestamp)
            except ValueError as exc:
                raise VaultCorruptedError("Vault entry timestamp is invalid.") from exc
    return raw


def _replace_windows_file_with_retry(source: Path, destination: Path) -> None:
    """Atomically replace a vault file despite short-lived Windows sharing locks."""
    deadline = time.monotonic() + _WINDOWS_REPLACE_TIMEOUT_SECONDS
    while True:
        try:
            os.replace(source, destination)
            return
        except OSError as exc:
            if getattr(exc, "winerror", None) not in {5, 32}:
                raise
            if time.monotonic() >= deadline:
                raise VaultUnavailableError(
                    "Could not atomically replace the credential vault after "
                    "waiting for a transient Windows file lock."
                ) from exc
            time.sleep(_LOCK_POLL_SECONDS)


def _harden_file_acl(path: Path) -> None:
    """Restrict *path* to the current Windows user or fail closed."""
    if not _WIN32SECURITY_AVAILABLE:
        raise VaultUnavailableError("Windows ACL support is unavailable for the credential vault")
    try:
        username = win32api.GetUserName()
        user_sid, _domain, _type = win32security.LookupAccountName("", username)
        dacl = win32security.ACL()
        dacl.AddAccessAllowedAce(
            win32security.ACL_REVISION, ntsecuritycon.FILE_ALL_ACCESS, user_sid
        )
        security_descriptor = win32security.GetFileSecurity(
            str(path), win32security.DACL_SECURITY_INFORMATION
        )
        security_descriptor.SetSecurityDescriptorDacl(1, dacl, 0)
        win32security.SetFileSecurity(
            str(path), win32security.DACL_SECURITY_INFORMATION, security_descriptor
        )
    except Exception as exc:
        raise VaultUnavailableError("Could not secure the credential vault filesystem ACL") from exc


class WindowsDpapiCredentialVault:
    """Windows DPAPI-backed credential vault.

    One JSON file per scope holds base64 DPAPI ciphertext plus non-secret
    metadata per key, guarded by an interprocess file lock and written
    atomically. ``CRYPTPROTECT_UI_FORBIDDEN`` is always set so a
    background/bridge process fails instead of hanging on a Windows UI
    prompt it can never show.
    """

    _scope: str
    _owner: str
    _vault_path: Path
    _lock_path: Path

    def __init__(
        self, *, scope: str = "household", user_id: str | None = None, vault_path: Path
    ) -> None:
        if sys.platform != "win32" or not _PYWIN32_AVAILABLE:
            raise VaultUnavailableError(
                "The Windows credential vault requires pywin32 on Windows " "(pip install pywin32)."
            )
        self._scope, self._owner = _validate_scope(scope, user_id)
        self._vault_path = vault_path
        self._lock_path = vault_path.with_name(vault_path.name + ".lock")

    # -- interprocess locking -------------------------------------------------

    def _acquire_lock(self) -> BinaryIO:
        self._vault_path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self._lock_path, "a+b")  # noqa: SIM115 - lifetime spans lock hold
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())
        _harden_file_acl(self._lock_path)
        if not _MSVCRT_AVAILABLE:  # pragma: no cover - Windows always has msvcrt
            handle.close()
            raise VaultUnavailableError("Windows interprocess locking is unavailable")
        deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
        while True:
            try:
                handle.seek(0)
                locking = msvcrt.locking  # type: ignore[attr-defined, unused-ignore]
                locking(handle.fileno(), msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined, unused-ignore]
                return handle
            except OSError:
                if time.monotonic() >= deadline:
                    handle.close()
                    raise VaultUnavailableError(
                        "Timed out waiting for the credential vault file lock; "
                        "another process may be holding it."
                    ) from None
                time.sleep(_LOCK_POLL_SECONDS)

    def _release_lock(self, handle: BinaryIO) -> None:
        try:
            if _MSVCRT_AVAILABLE:  # pragma: no branch - Windows always has msvcrt
                handle.seek(0)
                locking = msvcrt.locking  # type: ignore[attr-defined, unused-ignore]
                locking(handle.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined, unused-ignore]
        finally:
            handle.close()

    def _with_lock(self, fn: Callable[[], _T]) -> _T:
        handle = self._acquire_lock()
        try:
            return fn()
        finally:
            self._release_lock(handle)

    # -- store I/O --------------------------------------------------------------

    def _read_store_unlocked(self) -> dict:
        if not self._vault_path.exists():
            return {"__version__": _SCHEMA_VERSION, "entries": {}}
        try:
            raw = json.loads(self._vault_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise VaultCorruptedError(f"Vault file unreadable or corrupted: {exc}") from exc
        return _validate_schema(raw)

    def _write_store_unlocked(self, store: dict) -> None:
        self._vault_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._vault_path.with_name(f"{self._vault_path.name}.tmp-{os.getpid()}")
        payload = json.dumps(store, indent=2)
        try:
            with open(tmp_path, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            _harden_file_acl(tmp_path)
            _replace_windows_file_with_retry(tmp_path, self._vault_path)
            _harden_file_acl(self._vault_path)
        finally:
            try:
                tmp_path.unlink()
            except FileNotFoundError:
                pass

    def _encrypt(
        self, value: str, key: str, integration: str, account: str | None, slot: str
    ) -> str:
        assert win32crypt is not None and win32cryptcon is not None  # __init__ already guarded this
        blob = win32crypt.CryptProtectData(
            value.encode("utf-8"),
            "AskRex credential",
            _entropy_for_entry(self._scope, self._owner, key, integration, account, slot),
            None,
            None,
            win32cryptcon.CRYPTPROTECT_UI_FORBIDDEN,
        )
        return base64.b64encode(blob).decode("ascii")

    def _decrypt(
        self,
        ciphertext_b64: str,
        key: str,
        integration: str,
        account: str | None,
        slot: str,
    ) -> str:
        try:
            assert win32crypt is not None and win32cryptcon is not None  # __init__ guarded this
            blob = base64.b64decode(ciphertext_b64, validate=True)
            _description, plaintext = win32crypt.CryptUnprotectData(
                blob,
                _entropy_for_entry(self._scope, self._owner, key, integration, account, slot),
                None,
                None,
                win32cryptcon.CRYPTPROTECT_UI_FORBIDDEN,
            )
            return plaintext.decode("utf-8")  # type: ignore[no-any-return]
        except Exception as exc:
            raise VaultCorruptedError(
                f"Could not decrypt vault entry {key!r}: wrong scope or corrupted data."
            ) from exc

    def _validate_entry(
        self,
        key: str,
        entry: dict,
        *,
        integration: str,
        account: str | None,
        slot: str,
    ) -> None:
        if entry.get("scope") != self._scope or entry.get("owner") != self._owner:
            raise VaultCorruptedError(
                f"Vault entry {key!r} scope/owner metadata does not match this vault; "
                "refusing to use it."
            )
        if entry.get("integration") != integration:
            raise VaultCorruptedError(f"Vault entry {key!r} integration metadata mismatch.")
        if entry.get("account") != account:
            raise VaultCorruptedError(f"Vault entry {key!r} account metadata mismatch.")
        if entry.get("slot") != slot:
            raise VaultCorruptedError(f"Vault entry {key!r} credential-slot metadata mismatch.")

    # -- public API ---------------------------------------------------------

    def set_secret(
        self,
        key: str,
        value: str,
        *,
        integration: str,
        account: str | None,
        slot: str,
    ) -> None:
        key, integration, account, slot = _validate_context(key, integration, account, slot)
        if not isinstance(value, str) or not value:
            raise ValueError("Credential value must be a non-empty string")

        def _do() -> None:
            store = self._read_store_unlocked()
            now = datetime.now(UTC).isoformat()
            existing = store["entries"].get(key, {})
            if existing:
                self._validate_entry(
                    key,
                    existing,
                    integration=integration,
                    account=account,
                    slot=slot,
                )
            store["entries"][key] = {
                "ciphertext": self._encrypt(value, key, integration, account, slot),
                "integration": integration,
                "account": account,
                "slot": slot,
                "scope": self._scope,
                "owner": self._owner,
                "created_at": existing.get("created_at", now),
                "updated_at": now,
            }
            self._write_store_unlocked(store)

        self._with_lock(_do)

    def get_secret(
        self, key: str, *, integration: str, account: str | None, slot: str
    ) -> str | None:
        key, integration, account, slot = _validate_context(key, integration, account, slot)

        def _do() -> str | None:
            store = self._read_store_unlocked()
            entry = store["entries"].get(key)
            if entry is None:
                return None
            self._validate_entry(key, entry, integration=integration, account=account, slot=slot)
            return self._decrypt(entry["ciphertext"], key, integration, account, slot)

        return self._with_lock(_do)

    def delete_secret(self, key: str, *, integration: str, account: str | None, slot: str) -> bool:
        key, integration, account, slot = _validate_context(key, integration, account, slot)

        def _do() -> bool:
            store = self._read_store_unlocked()
            if key not in store["entries"]:
                return False
            self._validate_entry(
                key,
                store["entries"][key],
                integration=integration,
                account=account,
                slot=slot,
            )
            del store["entries"][key]
            self._write_store_unlocked(store)
            return True

        return self._with_lock(_do)

    def has_secret(self, key: str, *, integration: str, account: str | None, slot: str) -> bool:
        return self.get_secret(key, integration=integration, account=account, slot=slot) is not None

    def list_entries(self) -> list[VaultEntryMetadata]:
        def _do() -> list[VaultEntryMetadata]:
            store = self._read_store_unlocked()
            result = []
            for k, v in store["entries"].items():
                if v.get("scope") != self._scope or v.get("owner") != self._owner:
                    raise VaultCorruptedError("Vault entry scope/owner metadata is invalid")
                result.append(
                    VaultEntryMetadata(
                        key=k,
                        integration=v.get("integration", ""),
                        account=v.get("account"),
                        slot=v["slot"],
                        scope=v.get("scope", self._scope),
                        owner=v.get("owner", self._owner),
                        created_at=v.get("created_at", ""),
                        updated_at=v.get("updated_at", ""),
                    )
                )
            return result

        return self._with_lock(_do)


class MacOSKeychainCredentialVault:
    """macOS Keychain-backed credential vault.

    Secret values and their authorization metadata live inside one Keychain
    generic-password item per opaque credential reference. A small local index
    contains only opaque references so list_entries() can enumerate this vault.
    """

    def __init__(
        self,
        *,
        scope: str = "household",
        user_id: str | None = None,
        index_path: Path | None = None,
    ) -> None:
        if _keyring is None:
            raise VaultUnavailableError(
                "The macOS credential vault requires the 'keyring' package."
            )
        try:
            selected_backend = _keyring.get_keyring()
        except Exception as exc:
            raise VaultUnavailableError("Could not initialize the macOS Keychain backend") from exc
        backend_module = type(selected_backend).__module__.lower()
        if not backend_module.startswith("keyring.backends.macos"):
            raise VaultUnavailableError(
                "The selected keyring backend is not the native macOS Keychain backend."
            )
        self._keyring_backend = selected_backend
        self._scope, self._owner = _validate_scope(scope, user_id)
        owner_hash = hashlib.sha256(self._owner.encode("utf-8")).hexdigest()[:16]
        self._service = f"com.askrex.credentials.{self._scope}.{owner_hash}"
        self._index_path = index_path or _default_vault_path(scope, user_id).with_name(
            "keychain-index.json"
        )
        self._lock_path = self._index_path.with_name(self._index_path.name + ".lock")
        self._lock = threading.RLock()

    def _acquire_process_lock(self) -> BinaryIO:
        self._index_path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self._lock_path, "a+b")  # noqa: SIM115 - held across transaction
        try:
            os.chmod(self._lock_path, 0o600)
        except OSError:
            pass
        if _FCNTL_AVAILABLE:
            try:
                fcntl_module: Any = fcntl
                fcntl_module.flock(handle.fileno(), fcntl_module.LOCK_EX)
            except OSError as exc:
                handle.close()
                raise VaultUnavailableError(
                    "Could not lock macOS Keychain credential index"
                ) from exc
        return handle

    @staticmethod
    def _release_process_lock(handle: BinaryIO) -> None:
        try:
            if _FCNTL_AVAILABLE:
                fcntl_module: Any = fcntl
                fcntl_module.flock(handle.fileno(), fcntl_module.LOCK_UN)
        finally:
            handle.close()

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        with self._lock:
            handle = self._acquire_process_lock()
            try:
                yield
            finally:
                self._release_process_lock(handle)

    def _read_index(self) -> set[str]:
        if not self._index_path.exists():
            return set()
        try:
            raw = json.loads(self._index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise VaultCorruptedError("macOS Keychain credential index is invalid") from exc
        if not isinstance(raw, dict) or set(raw) != {"__version__", "keys"}:
            raise VaultCorruptedError("macOS Keychain credential index schema is invalid")
        if raw.get("__version__") != 1 or not isinstance(raw.get("keys"), list):
            raise VaultCorruptedError("macOS Keychain credential index version is invalid")
        keys = raw["keys"]
        if not all(isinstance(key, str) and _REFERENCE_PATTERN.fullmatch(key) for key in keys):
            raise VaultCorruptedError("macOS Keychain credential index contains invalid keys")
        return set(keys)

    def _write_index(self, keys: set[str]) -> None:
        self._index_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {"__version__": 1, "keys": sorted(keys)},
            sort_keys=True,
            separators=(",", ":"),
        )
        tmp = self._index_path.with_name(
            f".{self._index_path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
        )
        try:
            tmp.write_text(payload, encoding="utf-8")
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            os.replace(tmp, self._index_path)
            try:
                os.chmod(self._index_path, 0o600)
            except OSError:
                pass
        finally:
            tmp.unlink(missing_ok=True)

    def _read_payload(self, key: str) -> dict[str, Any] | None:
        try:
            raw = self._keyring_backend.get_password(self._service, key)
        except Exception as exc:
            raise VaultUnavailableError("macOS Keychain read failed") from exc
        if raw is None:
            return None
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise VaultCorruptedError("macOS Keychain credential payload is invalid") from exc
        expected_fields = {
            "value",
            "integration",
            "account",
            "slot",
            "scope",
            "owner",
            "created_at",
            "updated_at",
        }
        if not isinstance(payload, dict) or set(payload) != expected_fields:
            raise VaultCorruptedError("macOS Keychain credential payload schema is invalid")
        return payload

    def _validate_payload(
        self,
        key: str,
        payload: dict[str, Any],
        *,
        integration: str,
        account: str | None,
        slot: str,
    ) -> None:
        if payload.get("scope") != self._scope or payload.get("owner") != self._owner:
            raise VaultCorruptedError(
                f"Keychain entry {key!r} scope/owner metadata does not match this vault"
            )
        if payload.get("integration") != integration:
            raise VaultCorruptedError(f"Keychain entry {key!r} integration metadata mismatch")
        if payload.get("account") != account:
            raise VaultCorruptedError(f"Keychain entry {key!r} account metadata mismatch")
        if payload.get("slot") != slot:
            raise VaultCorruptedError(f"Keychain entry {key!r} credential-slot metadata mismatch")

    def set_secret(
        self,
        key: str,
        value: str,
        *,
        integration: str,
        account: str | None,
        slot: str,
    ) -> None:
        key, integration, account, slot = _validate_context(key, integration, account, slot)
        if not isinstance(value, str) or not value:
            raise ValueError("Credential value must be a non-empty string")
        with self._transaction():
            existing = self._read_payload(key)
            if existing is not None:
                self._validate_payload(
                    key,
                    existing,
                    integration=integration,
                    account=account,
                    slot=slot,
                )
            now = datetime.now(UTC).isoformat()
            payload = {
                "value": value,
                "integration": integration,
                "account": account,
                "slot": slot,
                "scope": self._scope,
                "owner": self._owner,
                "created_at": existing.get("created_at", now) if existing else now,
                "updated_at": now,
            }
            try:
                self._keyring_backend.set_password(
                    self._service, key, json.dumps(payload, sort_keys=True)
                )
            except Exception as exc:
                raise VaultUnavailableError("macOS Keychain write failed") from exc
            keys = self._read_index()
            keys.add(key)
            self._write_index(keys)

    def get_secret(
        self, key: str, *, integration: str, account: str | None, slot: str
    ) -> str | None:
        key, integration, account, slot = _validate_context(key, integration, account, slot)
        with self._transaction():
            payload = self._read_payload(key)
            if payload is None:
                return None
            self._validate_payload(
                key,
                payload,
                integration=integration,
                account=account,
                slot=slot,
            )
            value = payload.get("value")
            if not isinstance(value, str) or not value:
                raise VaultCorruptedError("macOS Keychain credential value is invalid")
            return value

    def delete_secret(self, key: str, *, integration: str, account: str | None, slot: str) -> bool:
        key, integration, account, slot = _validate_context(key, integration, account, slot)
        with self._transaction():
            payload = self._read_payload(key)
            if payload is None:
                return False
            self._validate_payload(
                key,
                payload,
                integration=integration,
                account=account,
                slot=slot,
            )
            try:
                self._keyring_backend.delete_password(self._service, key)
            except Exception as exc:
                raise VaultUnavailableError("macOS Keychain delete failed") from exc
            keys = self._read_index()
            keys.discard(key)
            self._write_index(keys)
            return True

    def has_secret(self, key: str, *, integration: str, account: str | None, slot: str) -> bool:
        return self.get_secret(key, integration=integration, account=account, slot=slot) is not None

    def list_entries(self) -> list[VaultEntryMetadata]:
        with self._transaction():
            result: list[VaultEntryMetadata] = []
            for key in sorted(self._read_index()):
                payload = self._read_payload(key)
                if payload is None:
                    raise VaultCorruptedError(
                        f"macOS Keychain index references missing entry {key!r}"
                    )
                if payload.get("scope") != self._scope or payload.get("owner") != self._owner:
                    raise VaultCorruptedError(
                        "macOS Keychain entry scope/owner metadata is invalid"
                    )
                result.append(
                    VaultEntryMetadata(
                        key=key,
                        integration=str(payload.get("integration", "")),
                        account=payload.get("account"),
                        slot=str(payload.get("slot", "")),
                        scope=self._scope,
                        owner=self._owner,
                        created_at=str(payload.get("created_at", "")),
                        updated_at=str(payload.get("updated_at", "")),
                    )
                )
            return result


def _default_vault_path(scope: str, user_id: str | None) -> Path:
    from rex.runtime_paths import household_data_path, user_data_path

    if scope == "user":
        if not user_id:
            raise ValueError("user_id is required when scope='user'")
        return user_data_path(user_id, "credentials", "vault.json")
    return household_data_path("credentials", "vault.json")


def get_credential_vault(
    *,
    scope: str = "household",
    user_id: str | None = None,
    backend: str | None = None,
    vault_path_override: Path | None = None,
) -> CredentialVaultBackend:
    """Return the vault backend for *scope* (and *user_id* when scope='user').

    ``backend="memory"`` always returns the in-memory test/dev backend
    regardless of platform. Windows uses DPAPI and macOS uses the user's native
    Keychain through ``keyring``. Other platforms fail closed unless a later
    reviewed OS-backed backend is added.
    """
    if backend == "memory":
        return InMemoryCredentialVault(scope=scope, user_id=user_id)
    if sys.platform == "win32":
        vault_path = vault_path_override or _default_vault_path(scope, user_id)
        return WindowsDpapiCredentialVault(scope=scope, user_id=user_id, vault_path=vault_path)
    if sys.platform == "darwin":
        index_path = vault_path_override or _default_vault_path(scope, user_id).with_name(
            "keychain-index.json"
        )
        return MacOSKeychainCredentialVault(
            scope=scope,
            user_id=user_id,
            index_path=index_path,
        )
    raise VaultUnavailableError(
        f"No production credential vault backend is available on platform {sys.platform!r}."
    )


__all__ = [
    "CredentialVaultBackend",
    "InMemoryCredentialVault",
    "MacOSKeychainCredentialVault",
    "VaultCorruptedError",
    "VaultEntryMetadata",
    "VaultUnavailableError",
    "WindowsDpapiCredentialVault",
    "generate_credential_ref",
    "get_credential_vault",
    "validate_credential_ref",
]
