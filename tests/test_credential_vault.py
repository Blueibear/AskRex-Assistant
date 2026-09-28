"""Security contract tests for the S4 OS-backed credential vault."""

from __future__ import annotations

import json
import multiprocessing
import sys
import threading
from pathlib import Path

import pytest

from rex import credential_vault as vault_module
from rex.credential_vault import (
    InMemoryCredentialVault,
    MacOSKeychainCredentialVault,
    VaultCorruptedError,
    VaultUnavailableError,
    generate_credential_ref,
    get_credential_vault,
)


def _ref(character: str = "A") -> str:
    return f"cred_{character * 32}"


def _write_dpapi_entry_in_child(vault_path: str, index: int, start_event) -> None:
    """Spawn-safe worker used to prove the lock works across processes."""
    start_event.wait(timeout=30)
    vault = get_credential_vault(scope="household", vault_path_override=Path(vault_path))
    vault.set_secret(
        _ref(chr(65 + index)),
        f"process-value-{index}",
        integration="test",
        account=None,
        slot="token",
    )


class TestInMemoryCredentialVault:
    def test_roundtrip_requires_complete_matching_context(self):
        vault = InMemoryCredentialVault(scope="user", user_id="alice")
        vault.set_secret(_ref(), "secret", integration="email", account="primary", slot="password")
        assert (
            vault.get_secret(_ref(), integration="email", account="primary", slot="password")
            == "secret"
        )

    @pytest.mark.parametrize(
        ("integration", "account", "slot"),
        [
            ("calendar", "primary", "password"),
            ("email", "other", "password"),
            ("email", "primary", "client_secret"),
        ],
    )
    def test_reference_swapping_across_context_fails_closed(self, integration, account, slot):
        vault = InMemoryCredentialVault(scope="user", user_id="alice")
        vault.set_secret(_ref(), "secret", integration="email", account="primary", slot="password")
        with pytest.raises(VaultCorruptedError):
            vault.get_secret(_ref(), integration=integration, account=account, slot=slot)

    def test_reference_swapping_across_user_fails_closed(self):
        shared_store: dict = {}
        alice = InMemoryCredentialVault(scope="user", user_id="alice", store=shared_store)
        bob = InMemoryCredentialVault(scope="user", user_id="bob", store=shared_store)
        alice.set_secret(
            _ref(), "alice-secret", integration="email", account="primary", slot="password"
        )
        with pytest.raises(VaultCorruptedError):
            bob.get_secret(_ref(), integration="email", account="primary", slot="password")

    def test_list_does_not_silently_skip_foreign_or_tampered_metadata(self):
        shared_store: dict = {}
        household = InMemoryCredentialVault(store=shared_store)
        alice = InMemoryCredentialVault(scope="user", user_id="alice", store=shared_store)
        household.set_secret(_ref(), "secret", integration="openai", account=None, slot="api_key")
        with pytest.raises(VaultCorruptedError):
            alice.list_entries()

    def test_invalid_reference_and_empty_secret_are_rejected(self):
        vault = InMemoryCredentialVault()
        with pytest.raises(ValueError):
            vault.set_secret(
                "OPENAI_API_KEY", "secret", integration="openai", account=None, slot="api_key"
            )
        with pytest.raises(ValueError):
            vault.set_secret(_ref(), "", integration="openai", account=None, slot="api_key")

    def test_metadata_contains_no_secret_derived_fields(self):
        vault = InMemoryCredentialVault()
        marker = "secret-marker-with-unique-content"
        vault.set_secret(_ref(), marker, integration="openai", account=None, slot="api_key")
        metadata = vault.list_entries()[0]
        assert marker not in repr(metadata)
        assert not hasattr(metadata, "value")
        assert not hasattr(metadata, "preview")
        assert not hasattr(metadata, "hash")

    def test_generated_references_are_opaque_and_unique(self):
        refs = {generate_credential_ref() for _ in range(100)}
        assert len(refs) == 100
        assert all(ref.startswith("cred_") and len(ref) == 37 for ref in refs)


def test_schema_and_metadata_tampering_fail_closed_without_dpapi():
    valid_entry = {
        "ciphertext": "AA==",
        "integration": "openai",
        "account": None,
        "slot": "api_key",
        "scope": "household",
        "owner": "household",
        "created_at": "2026-08-01T00:00:00+00:00",
        "updated_at": "2026-08-01T00:00:00+00:00",
    }
    valid = {"__version__": 2, "entries": {_ref(): valid_entry}}
    assert vault_module._validate_schema(valid) == valid

    mutations = [
        {**valid, "__version__": 999},
        {**valid, "unexpected": True},
        {"__version__": 2, "entries": {_ref(): {**valid_entry, "owner": "alice"}}},
        {"__version__": 2, "entries": {_ref(): {**valid_entry, "slot": "bad slot"}}},
        {"__version__": 2, "entries": {_ref(): {**valid_entry, "extra": "x"}}},
    ]
    for mutation in mutations:
        with pytest.raises(VaultCorruptedError):
            vault_module._validate_schema(mutation)


class _FakeKeyring:
    __module__ = "keyring.backends.macOS"

    def __init__(self):
        self.values: dict[tuple[str, str], str] = {}

    def get_keyring(self):
        return self

    def set_password(self, service: str, username: str, password: str) -> None:
        self.values[(service, username)] = password

    def get_password(self, service: str, username: str) -> str | None:
        return self.values.get((service, username))

    def delete_password(self, service: str, username: str) -> None:
        self.values.pop((service, username), None)


class TestMacOSKeychainCredentialVault:
    def test_roundtrip_keeps_secret_out_of_local_index(self, tmp_path, monkeypatch):
        fake = _FakeKeyring()
        monkeypatch.setattr(vault_module, "_keyring", fake)
        path = tmp_path / "keychain-index.json"
        vault = MacOSKeychainCredentialVault(index_path=path)
        marker = "mac-keychain-secret-marker"
        vault.set_secret(_ref(), marker, integration="openai", account=None, slot="api_key")

        assert (
            vault.get_secret(_ref(), integration="openai", account=None, slot="api_key") == marker
        )
        assert marker not in path.read_text(encoding="utf-8")
        assert vault.list_entries()[0].key == _ref()

    def test_context_mismatch_fails_closed(self, tmp_path, monkeypatch):
        fake = _FakeKeyring()
        monkeypatch.setattr(vault_module, "_keyring", fake)
        vault = MacOSKeychainCredentialVault(index_path=tmp_path / "index.json")
        vault.set_secret(_ref(), "secret", integration="email", account="primary", slot="password")
        with pytest.raises(VaultCorruptedError):
            vault.get_secret(
                _ref(),
                integration="calendar",
                account="primary",
                slot="password",
            )

    def test_factory_selects_macos_keychain(self, tmp_path, monkeypatch):
        fake = _FakeKeyring()
        monkeypatch.setattr(vault_module, "_keyring", fake)
        monkeypatch.setattr(vault_module.sys, "platform", "darwin")
        vault = get_credential_vault(vault_path_override=tmp_path / "index.json")
        assert isinstance(vault, MacOSKeychainCredentialVault)

    def test_non_native_keyring_backend_fails_closed(self, tmp_path, monkeypatch):
        fake = _FakeKeyring()
        fake.get_keyring = lambda: object()
        monkeypatch.setattr(vault_module, "_keyring", fake)
        with pytest.raises(VaultUnavailableError, match="native macOS Keychain"):
            MacOSKeychainCredentialVault(index_path=tmp_path / "index.json")

    def test_delete_removes_keychain_secret_and_index(self, tmp_path, monkeypatch):
        fake = _FakeKeyring()
        monkeypatch.setattr(vault_module, "_keyring", fake)
        vault = MacOSKeychainCredentialVault(index_path=tmp_path / "index.json")
        vault.set_secret(_ref(), "secret", integration="openai", account=None, slot="api_key")
        assert vault.delete_secret(_ref(), integration="openai", account=None, slot="api_key")
        assert vault.get_secret(_ref(), integration="openai", account=None, slot="api_key") is None
        assert vault.list_entries() == []


def test_unsupported_platform_production_has_no_implicit_backend(monkeypatch):
    monkeypatch.setattr(vault_module.sys, "platform", "linux")
    with pytest.raises(VaultUnavailableError):
        get_credential_vault()


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI vault is Windows-only")
class TestWindowsDpapiCredentialVault:
    def _vault(self, tmp_path, *, scope="household", user_id=None, path=None):
        return get_credential_vault(
            scope=scope,
            user_id=user_id,
            vault_path_override=path or tmp_path / "vault.json",
        )

    def test_real_dpapi_roundtrip_and_ciphertext_only_store(self, tmp_path):
        path = tmp_path / "vault.json"
        vault = self._vault(tmp_path, path=path)
        marker = "real-dpapi-secret-marker"
        vault.set_secret(_ref(), marker, integration="openai", account=None, slot="api_key")
        assert (
            vault.get_secret(_ref(), integration="openai", account=None, slot="api_key") == marker
        )
        raw_text = path.read_text(encoding="utf-8")
        assert marker not in raw_text
        assert json.loads(raw_text)["__version__"] == 2

    def test_tampered_schema_and_context_fail_closed(self, tmp_path):
        path = tmp_path / "vault.json"
        vault = self._vault(tmp_path, path=path)
        vault.set_secret(_ref(), "secret", integration="openai", account=None, slot="api_key")
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["entries"][_ref()]["integration"] = "anthropic"
        path.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(VaultCorruptedError):
            vault.get_secret(_ref(), integration="openai", account=None, slot="api_key")

    def test_different_rex_user_cannot_use_copied_reference(self, tmp_path):
        path = tmp_path / "shared.json"
        alice = self._vault(tmp_path, scope="user", user_id="alice", path=path)
        alice.set_secret(
            _ref(), "alice-secret", integration="email", account="primary", slot="password"
        )
        bob = self._vault(tmp_path, scope="user", user_id="bob", path=path)
        with pytest.raises(VaultCorruptedError):
            bob.get_secret(_ref(), integration="email", account="primary", slot="password")

    def test_concurrent_writers_preserve_every_update(self, tmp_path):
        path = tmp_path / "vault.json"
        errors: list[BaseException] = []

        def write(index: int) -> None:
            try:
                vault = self._vault(tmp_path, path=path)
                vault.set_secret(
                    _ref(chr(65 + index)),
                    f"value-{index}",
                    integration="test",
                    account=None,
                    slot="token",
                )
            except BaseException as exc:  # noqa: BLE001 - asserted below
                errors.append(exc)

        threads = [threading.Thread(target=write, args=(index,)) for index in range(12)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        assert not errors
        assert len(self._vault(tmp_path, path=path).list_entries()) == 12

    def test_concurrent_process_writers_preserve_every_update(self, tmp_path):
        path = tmp_path / "vault.json"
        context = multiprocessing.get_context("spawn")
        start_event = context.Event()
        processes = [
            context.Process(
                target=_write_dpapi_entry_in_child,
                args=(str(path), index, start_event),
            )
            for index in range(8)
        ]
        for process in processes:
            process.start()
        start_event.set()
        for process in processes:
            process.join(timeout=45)
        assert all(process.exitcode == 0 for process in processes)
        assert len(self._vault(tmp_path, path=path).list_entries()) == 8

    def test_acl_hardening_failure_is_fatal(self, tmp_path, monkeypatch):
        vault = self._vault(tmp_path)

        def fail(_path):
            raise VaultUnavailableError("ACL denied")

        monkeypatch.setattr(vault_module, "_harden_file_acl", fail)
        with pytest.raises(VaultUnavailableError, match="ACL denied"):
            vault.set_secret(_ref(), "secret", integration="openai", account=None, slot="api_key")

    def test_vault_acl_contains_only_current_user(self, tmp_path):
        import win32api
        import win32security

        path = tmp_path / "vault.json"
        vault = self._vault(tmp_path, path=path)
        vault.set_secret(_ref(), "secret", integration="openai", account=None, slot="api_key")
        descriptor = win32security.GetFileSecurity(
            str(path), win32security.DACL_SECURITY_INFORMATION
        )
        dacl = descriptor.GetSecurityDescriptorDacl()
        current_sid, _domain, _type = win32security.LookupAccountName("", win32api.GetUserName())
        ace_sids = [dacl.GetAce(index)[2] for index in range(dacl.GetAceCount())]
        assert ace_sids and all(sid == current_sid for sid in ace_sids)


def test_windows_atomic_replace_retries_transient_sharing_errors(monkeypatch, tmp_path):
    source = tmp_path / "source.tmp"
    destination = tmp_path / "vault.json"
    source.write_text("payload", encoding="utf-8")
    calls = {"count": 0}

    def flaky_replace(_source, _destination):
        calls["count"] += 1
        if calls["count"] < 3:
            exc = PermissionError("transient sharing violation")
            exc.winerror = 5
            raise exc

    monkeypatch.setattr(vault_module.os, "replace", flaky_replace)
    monkeypatch.setattr(vault_module.time, "sleep", lambda _seconds: None)

    vault_module._replace_windows_file_with_retry(source, destination)
    assert calls["count"] == 3
