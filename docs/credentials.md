# Credential Management

AskRex desktop credentials are stored in an OS-backed vault. Windows uses
DPAPI through `pywin32`; macOS uses the native Keychain through `keyring`;
plaintext `.env`,
`config/credentials.json`, and `gui_settings.json` are not credential
authorities.

## Authority policy

- Packaged Electron is vault-only. Every Python bridge receives
  `ASKREX_PACKAGED=1`, and the launcher removes
  `REX_ALLOW_PLAINTEXT_CREDENTIAL_FALLBACK` from the child environment.
- Unpackaged operator and CI workflows may deliberately set
  `REX_ALLOW_PLAINTEXT_CREDENTIAL_FALLBACK=1`. This enables legacy process
  environment/config reads, with environment values taking precedence. It
  never enables plaintext writes.
- macOS production uses the native Keychain backend and fails closed if `keyring` selects anything other than `keyring.backends.macOS`. Unsupported platforms such as Linux fail closed. `InMemoryCredentialVault` is an injected test fake and is never selected automatically.
- `CredentialManager.set_token(..., persist=True)` and all Electron save paths
  write only to the vault. A vault, readback, reference-registry, or settings
  mirror failure is returned as a failure.

## Context and storage

Vault entries use opaque `cred_...` references. The OS-backed secret is context-bound to:

- scope (`household` or `user`);
- validated owner/user;
- logical reference;
- integration;
- authorized account; and
- credential slot.

The same context is recorded in the non-secret reference registry and checked
against caller-expected context before lookup. Schema corruption, unknown
metadata, reference swapping, and account/slot/user mismatches fail closed.
Callers must not enumerate vault metadata and then use that metadata as their
own authorization input.

On Windows, household/user vault data is stored in `credentials/vault.json` under the corresponding runtime-data directory and protected with DPAPI, interprocess locking, atomic replacement, and restrictive ACLs. On macOS, the secret-bearing payload is stored in Keychain; the runtime-data directory contains only `credentials/keychain-index.json`, an atomically replaced list of opaque references with restrictive file permissions where supported.

Global `CredentialManager` state is household-only. User/account credentials
must use a request-local `CredentialManager(scope="user", user_id=...)` or
`get_persisted_credential(..., scope="user", user_id=...)` with caller-derived
integration, account, and slot.

## Electron behavior

The Electron main process calls `bridge/rex_credential_vault_bridge.py`; secret
values never return to the renderer. The renderer receives only blank secret
inputs plus `hasCredential` and opaque-reference metadata. Blank input means
unchanged. Deletion is a separate confirmed operation.

Supported desktop settings cover Home Assistant, OpenAI, OpenRouter, Anthropic,
Ollama, Brave, SerpAPI, Google, OpenWeather, Telegram, Twilio/SMS/phone,
ElevenLabs/TTS, OpenClaw gateway, email/calendar client secrets, and per-account
email passwords/client secrets. Stored/configured status is distinct from
connected, authenticated, or verified status.

## Migrating legacy plaintext

The migration is dry-run by default and requires an explicit scope and owner:

```powershell
python scripts/migrate_credentials_to_vault.py --scope household --owner household
python scripts/migrate_credentials_to_vault.py --scope household --owner household --apply
```

Use `--scope user --owner <validated-user-id>` only for a source whose secrets
belong to that Rex user. Do not reuse one source for multiple owners.

For each source, apply mode:

1. Parses all supported candidates without printing secret-derived data.
2. Treats an identical destination as `already_migrated` and a different
   destination as a conflict that leaves the source untouched.
3. Writes each entry, reads it back with caller-expected context, persists and
   reads back the opaque reference registry, then atomically sanitizes the
   source.
4. Rolls back staged entries and registry changes on failure where practical.
   Any retained recovery entry remains encrypted in the vault and is named only
   by a secret-free journal.

The command creates no plaintext backup, preview, prefix/suffix, length, stable
hash, or raw exception output. It is idempotent and restart-safe. Dry-run does
not construct or write a vault, registry, source, backup, or journal.

## Troubleshooting

- `VaultUnavailableError`: the required OS backend is unavailable or unsafe (for example, missing Windows DPAPI/`pywin32`, a non-native macOS keyring backend, or an unsupported platform). Packaged operation must stop the save; it must not create a plaintext fallback.
- `VaultCorruptedError`: schema, metadata, ciphertext, reference, or expected
  context did not validate. Do not delete or rewrite the store automatically;
  investigate the source of tampering/corruption.
- Credential shown as configured but not connected: storage succeeded, but no
  live provider authentication has been proved. Run the integration's explicit
  connection test.

Never include secret values, masked fragments, hashes, lengths, or raw provider
exceptions in user-facing migration output or logs. Emit opaque refs only where
the recovery/reference protocol requires them.
