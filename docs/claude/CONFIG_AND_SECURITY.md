# Claude Reference: Config and Security

Use this when a task touches config loading, secrets, environment variables,
network exposure, service auth, packaging, or security controls.

## Core Config Split

- Runtime settings belong in `config/rex_config.json`.
- Secrets belong in the OS-backed credential vault (`rex.credential_vault`,
  Windows DPAPI or macOS Keychain) — never in `rex_config.json` or GUI settings files. Plaintext
  reads require explicit unpackaged legacy/operator mode; packaged Electron
  strips and rejects that mode. New secret writes always use the vault.
- Do not put secrets in source-controlled JSON config.
- Non-secret legacy env vars should be migrated with
  `rex-config migrate-legacy-env`.
- The canonical wake-word section is `wakeword`; `wake_word` is legacy.

## Canonical Runtime Paths

- Core config, `.env`, and profile defaults resolve through `rex.runtime_paths`.
- `ASKREX_RUNTIME_DIR` overrides the runtime root for packaged or managed launches.
- `ASKREX_CONFIG_PATH`, `ASKREX_ENV_PATH`, `ASKREX_PROFILES_DIR`, `REX_DATA_DIR`,
  `ASKREX_HOUSEHOLD_DATA_DIR`, `ASKREX_USERS_DATA_DIR`, and `ASKREX_MEMORY_DIR`
  may override individual paths.
- Source checkouts default to the repository root. Installed Python use falls back
  to the platform user-data directory.
- Electron development bridges use the repository root; packaged bridges use
  `app.getPath('userData')` through `bridgeSpawnOptions()`.
- Shared state belongs under `data/household/`; private state belongs under
  `data/users/<validated-user-id>/`. `REX_DATA_DIR` retains its historical
  meaning as the exact shared-data root when the explicit household override is
  absent; its private sibling is `<REX_DATA_DIR>/users/` unless overridden.
- New persistence code must use `rex.runtime_paths`. Never use the bridge script
  directory, application archive, or packaged resources as writable storage.

### Runtime-data migration

Preview the known legacy `data/` and `~/.rex` moves without writing anything:

```bash
python scripts/migrate_runtime_data.py --user <user-id>
```

Apply only after reviewing the complete plan:

```bash
python scripts/migrate_runtime_data.py --user <user-id> --apply
```

The tool validates the owner, creates adjacent `.pre-runtime-root-migration.bak`
backups, retains every source, copies through a temporary path, treats identical
targets as already migrated, and refuses conflicting destinations. Re-running
it is safe.

## Canonical Secret-Loading Path

**Secrets come from the credential vault by default.** Never from
`rex_config.json` or `config/gui_settings.json`. Only explicit unpackaged
legacy/operator mode permits plaintext reads, where process environment wins.

### Python backend
- `CredentialManager` (`rex/credentials.py`) resolves a named credential from
  an opaque contextual reference. `set_token(..., persist=True)` writes
  through to the vault and raises
  `rex.credential_vault.VaultUnavailableError` if none is available; it never
  falls back to writing plaintext.
- Direct `AppConfig` secret fields (`ha_token`, `openai_api_key`,
  `anthropic_api_key`, `ollama_api_key`, `brave_api_key`, `speak_api_key`,
  `ha_secret`) resolve via `rex.config._secret_env_or_vault(env_var)` using the
  same fail-closed policy.
- `REX_JWT_SECRET`, `REX_PROXY_TOKEN`, and `REX_AGENT_TOKEN` use the same
  household vault authority and fail closed when absent.
- `rex_config.json` must never contain token or credential values; it may
  contain contextual opaque credential references only.

### Electron GUI (`gui/src/main/`)
- HA and settings save paths write/read back the vault entry and opaque
  reference before returning `{ok:true}`. They never read `.env`.
- `rex:setApiKey` / `rex:getApiKeys` route the supported provider keys through
  `vaultSetSecret` / `vaultHasSecret` with fixed caller-derived context.
- Integration settings save path: `rex:setSettings('integrations', ...)` (`handlers/settings.ts`) strips
  `haToken` before writing to `gui_settings.json`; if non-empty, writes it through the vault.
- Electron never performs vault cryptography itself — every vault operation
  spawns `bridge/rex_credential_vault_bridge.py` (stdin/stdout JSON, same
  convention as every other bridge) via `gui/src/main/credentialVault.ts`.
- `config/gui_settings.json` stores non-secret UI state only (URLs, providers,
  display preferences). It must not contain credential values.
- `mirrorToRexConfig` (`settingsMirror.ts`) and `rex:setSettings` return a
  truthful `{ok, error?}` — a vault or mirror failure must never show as a
  false "Saved" state in `SettingsPage.tsx` (check `result.ok` before calling
  `showSaved(...)`; show `addToast(result.error, 'error')` otherwise).

### GUI secret coverage

`gui_settings.json` contains no supported secret field. Home Assistant,
provider/search/weather/TTS/OpenClaw keys, Telegram, Twilio/SMS/phone,
email/calendar client secrets, and per-account email passwords/client secrets
are stripped and replaced by contextual opaque references. Blank input means
unchanged; deletion is separate and confirmed. The renderer receives only
blank secret fields plus `hasCredential`/reference metadata.

## Credential Vault (S4)

`rex/credential_vault.py` is the OS-backed desktop credential authority. Windows uses DPAPI (`pywin32`'s `win32crypt`); macOS uses the native Keychain through `keyring.backends.macOS`. Unsupported platforms fail closed with `VaultUnavailableError`.

- **Scopes**: `"household"` (installation-wide secrets) and
  pre-vault global behavior for every existing unscoped caller) and
  `"user"` + a validated `user_id` (bound to one Rex profile). Each scope
  binds secrets to scope, validated owner, opaque ref,
  integration, authorized account, and slot. Windows uses DPAPI entropy; macOS stores the same validated metadata inside the Keychain item. Stored metadata is checked
  against caller-expected context; swapping any dimension fails closed.
- **Storage**: Windows keeps base64 DPAPI ciphertext plus non-secret metadata in `vault.json` under the household/private runtime-data credentials directory. macOS keeps secret payloads in Keychain and stores only opaque credential references in `keychain-index.json` under the corresponding runtime-data directory.
- **Backend selection**: `get_credential_vault(scope=..., user_id=...)` picks DPAPI on `win32` and the native macOS Keychain backend on `darwin`. The macOS path verifies that `keyring` actually selected `keyring.backends.macOS`; alternate/plaintext keyring backends fail closed. Unsupported platforms raise `VaultUnavailableError`. `InMemoryCredentialVault` exists only for tests (`backend="memory"`) and is never selected implicitly.
- **Vault key convention**: the mapped env-var name from
  `CredentialManager.DEFAULT_CREDENTIAL_MAPPING` when one exists (e.g.
  service `openai` → key `OPENAI_API_KEY`), else the raw service/credential_ref
  string (e.g. `email:personal`). One namespace shared by `CredentialManager`,
  `rex.config`, and the migration script.
- **Electron access**: `bridge/rex_credential_vault_bridge.py` (actions
  `set`/`get`/`has`/`delete`) is the only path Electron uses;
  `gui/src/main/credentialVault.ts` wraps the bridge call as a promise and
  rejects clearly on any failure (bridge spawn failure, non-zero exit,
  malformed JSON, or `{ok:false}` — never resolves on failure).
- **Integrity**: Windows DPAPI storage uses interprocess locking; writes use a
  temporary file, flush/fsync, atomic replacement, and restrictive Windows ACL
  verification. ACL hardening failure aborts the write. macOS keeps secret-bearing payloads in Keychain; its local index contains opaque references only.
- **Migration**: `scripts/migrate_credentials_to_vault.py` is dry-run by
  default. Apply verifies ciphertext and registry readback before atomic source
  sanitation. It creates no plaintext backup or secret-derived output.

## Migration Path for Existing Users

For secrets already sitting in plaintext `.env` or `config/credentials.json`
run the vault migration instead of moving them by hand:

```bash
python scripts/migrate_credentials_to_vault.py --scope household --owner household
python scripts/migrate_credentials_to_vault.py --scope household --owner household --apply
```

Migration is all-or-nothing per source where practical. Identical destinations
are `already_migrated`; differing destinations are conflicts and leave the
source untouched. Recovery data remains encrypted in the vault with a
secret-free journal. See `docs/credentials.md` for the full contract.

## Security Defaults

- Prefer localhost binding.
- Anything that binds to a port must be authenticated and rate limited when it
  accepts non-health requests.
- Treat web content, email, SMS, tool results, plugin output, and external URLs
  as untrusted.
- Optional integrations must fail closed or degrade gracefully when unconfigured.
- Do not log tokens, passwords, API keys, or credential material.

## Service Secrets

| Secret | Used by |
|---|---|
| `OPENAI_API_KEY` | OpenAI LLM backend |
| `ANTHROPIC_API_KEY` | Anthropic LLM backend |
| `OLLAMA_API_KEY` | Cloud-hosted Ollama endpoint if auth is required |
| `HA_TOKEN`, `HA_SECRET` | Home Assistant integration |
| `BRAVE_API_KEY`, `SERPAPI_KEY`, `GOOGLE_API_KEY`, `GOOGLE_CSE_ID` | Search providers |
| `OPENWEATHERMAP_API_KEY` | Weather tool |
| `REX_SPEAK_API_KEY` | `rex-speak-api` `/speak` endpoint |
| `REX_TOOL_API_KEY` | `rex-tool-server` `/rex/tools/*` endpoint |
| `OPENCLAW_GATEWAY_TOKEN` | OpenClaw gateway client |
| `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER` | SMS/telephony |
| `REX_AGENT_API_KEY` or configured `REX_AGENT_TOKEN_ENV` | Computer agent |

## Runtime Config Areas

- `audio`
- `wakeword`
- `models`
- `runtime`
- `search`
- `home_assistant`
- `ollama`
- `openai`
- `anthropic`
- `calendar`
- `messaging`
- `notifications`
- `voice_identity`
- `computers`
- `wordpress`
- `woocommerce`
- `openclaw`
- `model_routing`
- `users`
- `workflows`

## Packaging and Dependencies

- Keep heavy ML/audio/CUDA dependencies optional.
- GPU installs are requirements-file based (`requirements-gpu*.txt`).
- Do not reintroduce GPU extras such as `.[gpu-cu118]`, `.[gpu-cu121]`, or
  `.[gpu-cu124]` unless the required PyTorch index behavior is fully handled.
- Guard runtime imports for optional packages.

## Network Surfaces

| Surface | Default | Auth |
|---|---:|---|
| `rex-gui` | `127.0.0.1:8765` | Local dashboard auth/session flow |
| `rex-speak-api` | `127.0.0.1:5005` | `REX_SPEAK_API_KEY` |
| `rex-tool-server` | `127.0.0.1:18790` | `REX_TOOL_API_KEY` |
| `rex-agent` | `127.0.0.1` by default | token env configured by `REX_AGENT_TOKEN_ENV` |
| deprecated legacy compatibility only: `flask_proxy.py` (see `SURFACE-CLASSIFICATION.md`) | `0.0.0.0:5000` | proxy token/local settings; not an active recommended runtime surface |

Do not present public exposure as the default.

## Integration Truths

- Email supports configured real backends and stub fallback.
- Calendar has ICS read-only and stub paths.
- Messaging supports Twilio when configured and stub mode otherwise.
- WordPress is primarily health/read monitoring.
- WooCommerce includes approval-gated write actions.
- Home Assistant TTS notification channel is optional and disabled by default.
- OpenClaw integration is HTTP-based, not an imported Python package.

## Assistant Identity Binding (issue #303)

- `rex.assistant.Assistant` never invents a user identity. Constructing it
  without `user_id` yields an *unbound* instance: no `"default"` fallback, no
  implicit `settings.user_id` inheritance, and no user-scoped reads or writes
  during construction (history preload, follow-up session, cache, credentials).
- Private request paths (intent shortcuts, response cache, early returns,
  history reads/writes, context construction, tool/action dispatch, streaming,
  completion recording) require an explicit validated identity — the bound
  constructor `user_id` or a per-request `active_user_id` — and fail closed
  with `rex.assistant_errors.IdentityRequiredError` otherwise. Invalid IDs
  raise `ValueError` from `rex.identity.validate_user_id` and are never
  sanitized.
- Caller migration: code that previously relied on `Assistant()` implicitly
  acting as the `default` profile must either pass `Assistant(user_id="default")`
  explicitly, resolve the intended profile with
  `rex.identity.resolve_entrypoint_user_id(settings, explicit_user=...)`
  (priority: explicit flag > `rex identify` session > `runtime.user_id` >
  explicit `"default"`), or supply `active_user_id` on each request.
- `user_id="default"` remains a valid explicit profile; legacy data owned by
  the `default` profile is not migrated, renamed, or reassigned.

## Security Checks

```bash
python scripts/security_audit.py
python -m rex doctor
```

---

## AppConfig Field Reference

This section is the authoritative map of every `AppConfig` field: where it is
read from, which source wins, and whether it is a secret, a runtime setting,
or a feature flag.

### Source Priority

```
vault reference  >  unset
rex_config.json  >  dataclass default        (non-secret runtime settings)
```

- **Secrets** are read from contextual vault references. Explicit unpackaged
  legacy/operator mode may read process environment or legacy credential JSON;
  packaged Electron rejects that mode.
- **Runtime settings** are read from `rex_config.json` only. Environment
  variables for non-secrets are ignored (since the legacy `ENV_MAPPING` was
  removed).
- **Secret JSON fields are ignored.** This includes historical
  `notifications.push_token`; migrate the value to the vault.
- **`REX_DEBUG`**: env-only boolean flag (`REX_DEBUG=1` sets `debug_mode=True`).

### Sub-config Access Pattern

> **Do not add new flat top-level `AppConfig` fields.** All new settings must
> use a sub-config group. Flat fields emit `DeprecationWarning` when accessed.

`AppConfig.__post_init__` builds seven derived sub-config objects from the flat
fields. **Always use the nested path in new code:**

| Sub-config | Access path | Source fields |
|---|---|---|
| `config.audio` | `AudioConfig` | `sample_rate`, `audio_input_device`, `audio_output_device`, `command_vad_rms_threshold` |
| `config.voice` | `VoiceConfig` | `tts_provider`, `tts_voice`, `tts_speed`, `whisper_model`, `whisper_device`, `wakeword`, `wakeword_threshold`, `wakeword_fallback_keyword`, `wakeword_backend` |
| `config.llm` | `LLMConfig` | `llm_provider`, `llm_model`, `ollama_base_url`, `llm_max_tokens`, `llm_temperature`, `llm_routing_mode` |
| `config.tools` | `ToolsConfig` | `tool_timeout_seconds` |
| `config.integrations` | `IntegrationsConfig` | `ha_base_url`, `email_provider`, `calendar_provider`, `music_assistant_url`, `shopping_pwa_pin`, `openclaw_gateway_url/timeout/max_retries` |
| `config.ui` | `UIConfig` | `ui_enabled` |
| `config.security` | `SecurityConfig` | `rate_limit`, `allowed_origins` |

Deprecated flat-field aliases that currently emit `DeprecationWarning`:
`llm_provider`, `tts_voice`, `whisper_device`, `openclaw_gateway_url`,
`model_name`, `tts_engine`, `wakeword_model`, `home_assistant_base_url`,
`tool_timeout`, `gui_port`, `api_key_env`, `rate_limit_per_minute`.

---

### Field Reference Tables

**Column key:**
- **JSON key** — dot-path in `rex_config.json` (e.g. `models.llm_provider`)
- **Logical name** — fixed vault registry name for a secret
- **Source** — `json-only`, `vault`, `env-flag`
- **Type** — `secret`, `runtime`, `feature-flag`
- **Default** — value when neither source is set

#### Wake Word (`wakeword.*` in rex_config.json)

| AppConfig field | JSON key | Source | Type | Default |
|---|---|---|---|---|
| `wakeword` | `wakeword.wakeword` | json-only | runtime | `"hey_rex"` |
| `wakeword_backend` | `wakeword.backend` | json-only | runtime | `"openwakeword"` |
| `wakeword_keyword` | `wakeword.keyword` | json-only | runtime | `None` |
| `wakeword_threshold` | `wakeword.threshold` | json-only | runtime | `0.5` |
| `wakeword_window` | `wakeword.window` | json-only | runtime | `1.0` |
| `wakeword_poll_interval` | `wakeword.poll_interval` | json-only | runtime | `0.01` |
| `wake_sound_path` | `wakeword.wake_sound_path` | json-only | runtime | `None` |
| `wakeword_model_path` | `wakeword.model_path` | json-only | runtime | `None` |
| `wakeword_embedding_path` | `wakeword.embedding_path` | json-only | runtime | `None` |
| `wakeword_fallback_to_builtin` | `wakeword.fallback_to_builtin` | json-only | runtime | `True` |
| `wakeword_fallback_keyword` | `wakeword.fallback_keyword` | json-only | runtime | `"hey jarvis"` |
| `wakeword_auto_gain` | `wakeword.auto_gain` | json-only | runtime | `True` |
| `wakeword_target_peak` | `wakeword.target_peak` | json-only | runtime | `0.35` |
| `wakeword_max_gain` | `wakeword.max_gain` | json-only | runtime | `30.0` |
| `wakeword_min_rms_for_gain` | `wakeword.min_rms_for_gain` | json-only | runtime | `0.0005` |

#### Audio (`audio.*` in rex_config.json)

| AppConfig field | JSON key | Source | Type | Default |
|---|---|---|---|---|
| `sample_rate` | `audio.sample_rate` | json-only | runtime | `16000` |
| `audio_input_device` | `audio.input_device_index` | json-only | runtime | `None` |
| `audio_output_device` | `audio.output_device_index` | json-only | runtime | `None` |
| `tts_output_device` | `audio.tts_output_device` | json-only | runtime | `None` |
| `wake_word_input_device` | `audio.wake_word_input_device` | json-only | runtime | `None` |

#### Runtime / Session (`runtime.*` in rex_config.json)

| AppConfig field | JSON key / Env var | Source | Type | Default |
|---|---|---|---|---|
| `command_duration` | `runtime.command_duration` | json-only | runtime | `5.0` |
| `command_vad_rms_threshold` | `runtime.command_vad_rms_threshold` | json-only | runtime | `0.003` |
| `detection_frame_seconds` | `runtime.detection_frame_seconds` | json-only | runtime | `1.0` |
| `capture_seconds` | `runtime.capture_seconds` | json-only | runtime | `5.0` |
| `memory_max_turns` | `runtime.memory_max_turns` | json-only | runtime | `50` |
| `transcripts_enabled` | `runtime.transcripts_enabled` | json-only | runtime | `True` |
| `transcripts_dir` | `runtime.transcripts_dir` | json-only | runtime | `"transcripts"` |
| `session_ttl_hours` | `runtime.session_ttl_hours` | json-only | runtime | `8` |
| `default_user` | `runtime.active_user` | json-only | runtime | `None` |
| `conversation_export` | `runtime.conversation_export` | json-only | runtime | `True` |
| `speak_language` | `runtime.speak_language` | json-only | runtime | `"en"` |
| `user_id` | `runtime.user_id` | json-only | runtime | `"default"` |
| `debug_logging` | `runtime.log_level` (`"DEBUG"` → `True`) | json-only | runtime | `False` |
| `debug_mode` | `REX_DEBUG` env var | env-flag | feature-flag | `False` |
| `file_logging_enabled` | `runtime.file_logging_enabled` | json-only | runtime | `False` |
| `log_path` | `runtime.log_path` | json-only | runtime | platform default |
| `error_log_path` | `runtime.error_log_path` | json-only | runtime | platform default |
| `memory_max_bytes` | `runtime.memory_max_bytes` | json-only | runtime | `131072` |
| `persist_history` | `runtime.persist_history` | json-only | runtime | `True` |
| `history_db_path` | `runtime.history_db_path` | json-only | runtime | canonical household `history.db` |
| `history_retention_days` | `runtime.history_retention_days` | json-only | runtime | `30` |
| `response_cache_ttl` | `response_cache.ttl` | json-only | runtime | `300.0` |

#### Models / STT / LLM / TTS (`models.*` in rex_config.json)

| AppConfig field | JSON key | Source | Type | Default |
|---|---|---|---|---|
| `whisper_model` | `models.stt_model` | json-only | runtime | `"base"` |
| `whisper_device` | `models.stt_device` | json-only | runtime | `"auto"` |
| `whisper_language` | `models.stt_language` | json-only | runtime | `"en"` |
| `stt_auto_gain` | `models.stt_auto_gain` | json-only | runtime | `True` |
| `stt_target_peak` | `models.stt_target_peak` | json-only | runtime | `0.45` |
| `stt_max_gain` | `models.stt_max_gain` | json-only | runtime | `12.0` |
| `stt_min_rms_for_gain` | `models.stt_min_rms_for_gain` | json-only | runtime | `0.0005` |
| `llm_provider` | `models.llm_provider` | json-only | runtime | `"transformers"` |
| `llm_model` | `models.llm_model` | json-only | runtime | `"sshleifer/tiny-gpt2"` |
| `llm_max_tokens` | `models.llm_max_tokens` | json-only | runtime | `120` |
| `llm_temperature` | `models.llm_temperature` | json-only | runtime | `0.7` |
| `llm_top_p` | `models.llm_top_p` | json-only | runtime | `0.9` |
| `llm_top_k` | `models.llm_top_k` | json-only | runtime | `50` |
| `llm_seed` | `models.llm_seed` | json-only | runtime | `42` |
| `tts_provider` | `models.tts_provider` | json-only | runtime | `"xtts"` |
| `tts_voice` | `models.tts_voice` | json-only | runtime | `None` |
| `tts_speed` | `models.tts_speed` | json-only | runtime | `1.08` |
| `tts_fast_short_reply_enabled` | `models.tts_fast_short_reply_enabled` | json-only | runtime | `True` |
| `tts_fast_short_reply_max_chars` | `models.tts_fast_short_reply_max_chars` | json-only | runtime | `140` |
| `voice_max_tokens` | *(dataclass default only)* | default | runtime | `150` |

#### API / Security (`api.*` in rex_config.json)

| AppConfig field | JSON key | Source | Type | Default |
|---|---|---|---|---|
| `rate_limit` | `api.rate_limit` | json-only | runtime | `"30/minute"` |
| `allowed_origins` | `api.allowed_origins` | json-only | runtime | `["*"]` |

#### Acknowledgment / Response Cache

| AppConfig field | JSON key | Source | Type | Default |
|---|---|---|---|---|
| `acknowledgment_sound` | `acknowledgment.sound` | json-only | runtime | `"chime"` |
| `acknowledgment_mode` | `acknowledgment.mode` | json-only | runtime | `"sound"` |
| `response_cache_ttl` | `response_cache.ttl` | json-only | runtime | `300.0` |

#### Secrets — LLM Providers (`vault`)

| AppConfig field | Env var | Source | Type |
|---|---|---|---|
| `openai_api_key` | `OPENAI_API_KEY` | vault | secret |
| `openai_model` | *(JSON:* `openai.model`*)* | json-only | runtime |
| `openai_base_url` | *(JSON:* `openai.base_url`*)* | json-only | runtime |
| `anthropic_api_key` | `ANTHROPIC_API_KEY` | vault | secret |
| `anthropic_model` | *(JSON:* `anthropic.model`*)* | json-only | runtime |
| `ollama_api_key` | `OLLAMA_API_KEY` | vault | secret |
| `ollama_base_url` | *(JSON:* `ollama.base_url`*)* | json-only | runtime |
| `ollama_use_cloud` | *(JSON:* `ollama.use_cloud`*)* | json-only | runtime |
| `brave_api_key` | `BRAVE_API_KEY` | vault | secret |
| `speak_api_key` | `REX_SPEAK_API_KEY` | vault | secret |

#### Home Assistant (`home_assistant.*` plus vault secrets)

| AppConfig field | JSON key / Env var | Source | Type | Default |
|---|---|---|---|---|
| `ha_base_url` | `home_assistant.base_url` | json-only | runtime | `None` |
| `ha_token` | `HA_TOKEN` | vault | secret | `None` |
| `ha_secret` | `HA_SECRET` | vault | secret | `None` |
| `ha_verify_ssl` | `home_assistant.verify_ssl` | json-only | runtime | `True` |
| `ha_timeout` | `home_assistant.timeout` | json-only | runtime | `10.0` |

#### Location / Weather

| AppConfig field | JSON key / Env var | Source | Type | Default |
|---|---|---|---|---|
| `default_location` | `location.default_location` | json-only | runtime | `None` |
| `default_timezone` | `location.default_timezone` | json-only | runtime | `None` |
| `openweathermap_api_key` | `OPENWEATHERMAP_API_KEY` | vault | secret | `None` |

#### Search

| AppConfig field | JSON key | Source | Type | Default |
|---|---|---|---|---|
| `search_providers` | `search.providers` | json-only | runtime | `"serpapi,brave,duckduckgo,google"` |

#### Email / Calendar / Integrations

| AppConfig field | JSON key | Source | Type | Default |
|---|---|---|---|---|
| `email_provider` | `email.provider` | json-only | runtime | `"none"` |
| `email_accounts` | `email.accounts` | json-only | runtime | `[]` |
| `email_default_account_id` | `email.default_account_id` | json-only | runtime | `""` |
| `user_email_accounts` | `users.{id}.email_accounts` | json-only | runtime | `{}` |
| `user_default_email_accounts` | `users.{id}.default_email_account_id` | json-only | runtime | `{}` |
| `calendar_provider` | `calendar.provider` | json-only | runtime | `"none"` |

#### Conversational Follow-ups (`conversation.followups.*` in rex_config.json)

| AppConfig field | JSON key | Source | Type | Default |
|---|---|---|---|---|
| `followups_enabled` | `conversation.followups.enabled` | json-only | feature-flag | `False` |
| `followups_max_per_session` | `conversation.followups.max_per_session` | json-only | runtime | `2` |
| `followups_lookback_hours` | `conversation.followups.lookback_hours` | json-only | runtime | `72` |
| `followups_expire_hours` | `conversation.followups.expire_hours` | json-only | runtime | `168` |

#### OpenClaw (`openclaw.*` plus vault secret)

| AppConfig field | JSON key / Env var | Source | Type | Default |
|---|---|---|---|---|
| `use_openclaw_tools` | `openclaw.use_tools` | json-only | feature-flag | `False` |
| `use_openclaw_voice_backend` | `openclaw.use_voice_backend` | json-only | feature-flag | `False` |
| `openclaw_gateway_url` | `openclaw.gateway_url` | json-only | runtime | `""` |
| `openclaw_gateway_timeout` | `openclaw.gateway_timeout` | json-only | runtime | `30` |
| `openclaw_gateway_max_retries` | `openclaw.gateway_max_retries` | json-only | runtime | `3` |
| `openclaw_gateway_token` | `OPENCLAW_GATEWAY_TOKEN` | vault | secret | `None` |

`openclaw.use_voice_backend` is retained only for legacy compatibility. The canonical voice loop ignores it with a warning and keeps the TurnEngine-backed `Assistant`; OpenClaw does not replace Rex as the primary assistant brain.

#### Telegram / Push Notifications

| AppConfig field | JSON key / Env var | Source | Type | Default |
|---|---|---|---|---|
| `telegram_bot_token` | `TELEGRAM_BOT_TOKEN` | vault | secret | `None` |
| `telegram_chat_id` | `telegram.chat_id` | json-only | runtime | `None` |
| `push_provider` | `notifications.push_provider` | json-only | runtime | `None` |
| `push_token` | `PUSH_TOKEN` | vault | secret | `None` |
| `push_topic` | `notifications.push_topic` | json-only | runtime | `None` |

> Historical `notifications.push_token` JSON values are ignored. Migrate them
> to the vault; do not add mixed plaintext secret sources.

#### Model Routing (`model_routing.*` in rex_config.json)

| AppConfig field | JSON key | Source | Type | Default |
|---|---|---|---|---|
| `llm_routing_mode` | `model_routing.llm_routing_mode` | json-only | runtime | `"local_preferred"` |
| `cloud_fallback_cooldown_seconds` | `model_routing.cloud_fallback_cooldown_seconds` | json-only | runtime | `3600` |
| `model_routing` | `model_routing.*` (parsed as `ModelRoutingConfig`) | json-only | runtime | all `""` |

`ModelRoutingConfig` sub-fields: `default`, `coding`, `reasoning`, `search`,
`vision`, `fast` — all `str`, default `""` (falls back to global `llm_model`).

#### Voice Identity / File Access / Computer Control

| AppConfig field | JSON key | Source | Type | Default |
|---|---|---|---|---|
| `speaker_id_threshold` | `voice_identity.speaker_id_threshold` | json-only | runtime | `0.75` |
| `tool_timeout_seconds` | *(dataclass default only)* | default | runtime | `10.0` |
| `allowed_file_roots` | `file_ops.allowed_roots` | json-only | runtime | `[home_dir]` |
| `computer_control_confirmation` | *(dataclass default only)* | default | runtime | `"dangerous_only"` |
| `require_confirm_system_changes` | `windows.require_confirm_system_changes` | json-only | runtime | `True` |

#### Miscellaneous / Profile

| AppConfig field | JSON key | Source | Type | Default |
|---|---|---|---|---|
| `active_profile` | `active_profile` (merged from profile) | json-only | runtime | `"default"` |
| `capabilities` | `capabilities` (merged from profile) | json-only | runtime | `[]` |
| `personality` | *(dataclass default only)* | default | runtime | `"Friendly"` |
| `autonomy_mode` | `models.autonomy_mode` | json-only | runtime | `"manual"` |
| `ui_enabled` | *(dataclass default only)* | default | feature-flag | `True` |
| `shopping_pwa_pin` | *(dataclass default only)* | default | runtime | `None` |
| `music_assistant_url` | *(dataclass default only)* | default | runtime | `None` |
| `music_assistant_token` | *(dataclass default only)* | default | runtime | `None` |
| `autonomy_budget_per_plan_usd` | *(dataclass default only)* | default | runtime | `0.0` |
| `autonomy_budget_per_step_usd` | *(dataclass default only)* | default | runtime | `0.0` |
| `device_room_map` | *(dataclass default only)* | default | runtime | `{}` |
| `contacts_file` | *(dataclass default only)* | default | runtime | `None` |
| `memory_max_bytes` | `runtime.memory_max_bytes` | json-only | runtime | `131072` |

> Fields marked **"dataclass default only"** cannot currently be changed via
> `rex_config.json`. They use their dataclass default value on every load.
> To make them configurable, add a `_get_nested` call in `build_app_config`.

---

### Precedence Rules Summary

1. **Secrets resolve from contextual vault references.** Explicit unpackaged
   legacy/operator mode is the only environment/config exception.
2. **Non-secret runtime settings come from JSON only.** Setting a non-secret
   field as an env var has no effect (the legacy `ENV_MAPPING` was removed).
3. **`REX_DEBUG=1`** (env) sets `debug_mode=True`; `runtime.log_level = "DEBUG"`
   (JSON) sets `debug_logging=True`. These are two distinct booleans.
4. **Sub-config objects are derived views**, not independently configurable.
   Setting `config.audio.sample_rate` at runtime does not persist; change
   `audio.sample_rate` in `rex_config.json` instead.
