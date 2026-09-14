# AskRex Unified Delivery Backlog

- **Source evidence:** `docs/planning/CODEX_CURRENT_STATE_AUDIT.md` (2026-08-01, findings F-01–F-30, ten dependency-ordered batches)
- **Authoritative requirements:** `docs/planning/source-of-truth/REX_Unified_Build_Spec_UPDATED.md`, `docs/planning/source-of-truth/REX_ACTIVE_CHECKLIST.md`, `docs/planning/TEAM_LEAD_OPERATING_RULES.md`
- **Method:** This document reconciles the audit's findings against the two authoritative sources into one implementation-ready backlog. No new repository audit was performed. Historical checkboxes are not treated as evidence of completion; only the audit's verified/inference findings and cited line ranges are treated as current-state evidence.

## 1. Requirement traceability summary

### REX_Unified_Build_Spec_UPDATED.md

| Spec requirement | Current-state evidence | Status |
|---|---|---|
| "Rex must never pretend" (truthfulness rule, spec §3) | F-04, F-06, F-29, F-30 — fabricated SMS/notification/Test Voice/email success; mobile 501 scaffolds masked by mock fallbacks | Violated in production surfaces |
| Layered architecture: Intent/Context/Model/Planning/Tool/Verification (spec §4) | F-17 (core lifecycle real), F-18 (model routing not request-local), F-19 (planner/retrieval/skill-loader not wired), F-20 (no request-level recovery policy) | Partially built; not integrated end to end |
| External capability ecosystems / OpenClaw (spec §4, checklist all sections) | F-21 (HTTP boundary sound), F-22 (discovery/governance absent), F-23 (health can overclaim) | Static integration only; checklist 100% unchecked |
| Stable core runtime (spec §5) | Not re-audited here; no contradicting finding | Out of scope for this cycle |
| Voice layer: wake word, barge-in, latency, recovery (spec §6) | F-14 (hold-to-talk real), F-15 (wake mode lacks barge-in/device routing/real embeddings), F-16 (Test Voice fake, no latency gates) | Hold-to-talk production-viable; wake mode beta-only |
| Intent/Context engine (spec §7) | F-19 (context lacks ranked retrieval; planner not invoked) | Gap |
| Security/permissions/verification (implied throughout, reinforced by operating rules) | F-08, F-09, F-12, F-13 — no pairing authority, no enforced TLS boundary, plaintext desktop secrets, client-side-only biometric gating | Release blocker |

### REX_ACTIVE_CHECKLIST.md (OpenClaw dynamic skill/plugin integration)

Every checklist item (Core Bridge, Dynamic Plugin Discovery, Rex Tool Access Layer, Safety and Permissions, Verification Layer, GUI Integration, Long-Term Architecture Goals) is unchecked and confirmed unimplemented by F-22 and F-23. The checklist's architecture principle ("Rex remains orchestrator/verifier/safety layer, OpenClaw is optional") is currently satisfied structurally (F-21) but not functionally, since no dynamic discovery, approval, or GUI control exists. This checklist maps entirely into Batch 7 below.

### TEAM_LEAD_OPERATING_RULES.md

| Operating rule | Evidence | Status |
|---|---|---|
| Every real desktop capability discoverable/usable/configurable | F-01, F-02, F-03 | Violated |
| Mobile requests desktop-native actions through a paired session | F-06, F-08, F-09 | No pairing exists |
| Shared branding/tokens/terminology/navigation | F-27, F-28 | Diverged |
| No misleading controls or success messages | F-04, F-06, F-13, F-29, F-30 | Multiple violations |
| AskRex remains orchestrator; OpenClaw optional | F-17, F-21 | Preserved (positive control) |
| Device-bound pairing, per-device scopes/revocation/audit | F-07, F-08, F-13 | Absent |
| Encrypted transport, OS credential storage | F-09, F-12 | Absent |
| Verified updates, signed release | F-25, F-26 | Absent/unsigned-permitted |

## 2. Capability matrix

| Capability | Verified state | Desktop UI/settings | Mobile state | Security/permissions | Evidence | Release gap |
|---|---|---|---|---|---|---|
| Chat | Real, orchestrated via `Assistant.generate_reply()` | Exposed; default provider/model pair invalid | Real (auth/SSE/WS) | Identity-bound, fails closed | F-03, F-05, F-10, F-17 | Fix default pairing (S14) |
| Voice (hold-to-talk) | Real end-to-end pipeline | Exposed | Not present | Device-scoped, no pairing gate | F-14 | Preserve; add hardware evidence (S27) |
| Voice (wake word) | Beta; no barge-in, no verified device routing, synthetic enrollment default | Exposed as if production | N/A | Same as above | F-15, F-16 | Batch 8 |
| SMS | List real; send is in-memory fake `sent` | Exposed, misleading | N/A | No delivery verification | F-04 | S9 |
| Notifications | Fabricated seed data | Exposed, misleading | Fabricated fallback data | None | F-04, F-06 | S9, S10 |
| Home/Inbox/Automate/History (mobile) | Server `501` scaffolds; client shows realistic mock state | N/A | Misleading | None (server truthfully 501s) | F-06 | S10 |
| Home Assistant | Best-integrated; live behavior not verified | Exposed with settings | Server scaffold only | Live claims not proven | F-06 (scaffold), audit note | Out of this cycle (needs live HA) |
| Tool registry (28 tools) | Real, canonical lifecycle enforces permission/risk/confirmation/verification | Only 6 of 28 exposed as capabilities | N/A | Lifecycle sound where wired | F-01, F-17 | S12, S13 |
| Mobile auth/session | Real, well tested (114 tests) | N/A | Real | Strong but not device-paired | F-05, F-07 | Preserve; extend (S6) |
| Pairing/capability grant | Absent | Absent | Absent | Password-only enrollment | F-08 | S5, S6 |
| Transport encryption | Loopback-safe defaults; TLS not enforced for remote | N/A | Points to hosted URL, no pinning | Undefined topology | F-09 | S7 |
| Desktop credentials | Plaintext `.env`/JSON, global, not user-bound | Settings UI reports false "Saved" persistence for some fields | N/A | No OS vault | F-12 | S4 |
| Data ownership/roots | Fragmented (relative `data/`, `~/.rex`, install path) | N/A | Honors `REX_DATA_DIR` with relative default | No per-user partition proof | F-11 | S3 |
| Mobile biometric/high-risk auth | Client-local, fail-open branches | N/A | Present but not server-authoritative | Not cryptographically bound | F-13 | S8 |
| Model routing/fallback | No real provider failure fallback; possible race on shared mutable model field | N/A | N/A | N/A | F-18 | S15 |
| Planning/retrieval/skills | Constructed but never invoked in normal chat | N/A | N/A | N/A | F-19 | S16–S18 |
| Failure recovery | No request-level policy | N/A | N/A | N/A | F-20 | S19 |
| OpenClaw | HTTP boundary sound; no discovery/governance/GUI | Card points to unrelated settings | N/A | No allowlist/audit for dynamic discovery | F-21, F-22, F-23; checklist | S20–S23 |
| Branding/design tokens | Mobile retains OnSpace identity and separate token system | Canonical AskRex assets | OnSpace scheme/logo/name | N/A | F-27, F-28 | S28, S29 |
| Packaging/CI | Strong workflow design, unsigned artifacts permitted, version drift | N/A | No native build/signing/secret scan | N/A | F-24, F-25 | S30–S32 |
| Mobile release pipeline | No `eas.json`, no native signing, tracked credential | N/A | Critical exposure | None | F-26 | S1, S2, S33 |

## 3. Dependency-ordered roadmap (10 batches, 34 stories)

Priority key: **P0** release blocker / security-critical, **P1** required for shippability, **P2** important but can trail first release.

### Batch 1 — Contain mobile credential exposure and establish two-repo security gates

**S1 — Revoke and purge the exposed mobile PAT** (P0)
- Files/areas: mobile `scripts/reset-project.js:8`; provider-side token settings (external); mobile git history
- Evidence/root cause: F-26 — credential-like GitHub PAT pattern tracked in the reset script; scaffold captured a live credential
- Steps: (1) Revoke/rotate the token at the provider (external, user-owned action — not performed by an agent). (2) Replace the URL in `reset-project.js` with a credential-free placeholder. (3) Assess exposure window via `git log --all -- scripts/reset-project.js` and run an approved redacted historical secret scan; purge history only if confirmed necessary and approved by the user.
- Tests: mobile secret scan (new, see S2) must pass on the cleaned file
- Validation commands: `git log --all -- scripts/reset-project.js`; provider revocation confirmation (external)
- DoD: provider confirms old token unusable; tracked file contains no credential pattern; exposure assessment documented
- **External dependency:** provider-side revocation requires the account owner (James); cannot be completed by an agent

**S2 — Add secret scanning, dependency audit, and protected-push gates to mobile CI** (P0)
- Files/areas: mobile `.github/workflows/ci.yml:1-21`; new mobile secret-scan config
- Evidence: F-26 — mobile CI runs tests/lint/typecheck/Expo web export only, no secret scan or dependency audit
- Steps: (1) Add a dependency-free secret scan step (mirror desktop's tracked-file scanner referenced in the audit's post-audit remediation note). (2) Add `npm audit --audit-level=high` as a required job. (3) Enable branch protection requiring these checks (external GitHub settings action).
- Tests: new CI job must fail on a reintroduced test credential; must pass on current clean tree
- Validation commands: `npm.cmd audit --audit-level=high`; mobile secret scan script
- DoD: mobile CI blocks new secrets and high-severity dependency vulnerabilities on every PR

### Batch 2 — Canonicalize data roots, ownership, and OS credential storage

**S3 — Define and wire one Windows application data root** (P1)
- Files/areas: `gui/src/main/configStore.ts:11-22`; `rex/memory.py:91`; `rex/history_store.py:21`; `rex/assistant.py:133`; `rex/mobile_api/db.py:28-29`; `rex/autonomy/preferences.py:27`; `rex/notifications/models.py:31`
- Evidence/root cause: F-11 — subsystems chose storage locations independently (relative `data/`, install-relative config, global `~/.rex`); no canonical per-install/per-user root
- Steps: (1) Define one OS-appropriate app data root (e.g. `%LOCALAPPDATA%\AskRex`) and expose it via `REX_DATA_DIR`. (2) Set `REX_DATA_DIR` explicitly for every managed Python bridge Electron spawns. (3) Partition private (per validated user) vs. household-shared stores under that root. (4) Write a dry-run migration for existing relative `data/` and `~/.rex` content with conflict reporting.
- Tests: `tests/test_memory_isolation.py`, `tests/test_electron_session_isolation.py`, `tests/test_command_history.py`
- Validation commands: `py -3.11 -m pytest -q tests/test_memory_isolation.py tests/test_electron_session_isolation.py tests/test_command_history.py`; `cd gui; npm.cmd test; npm.cmd run build`
- DoD: two Rex users and two Windows OS users cannot read each other's private data; migration is dry-run-first, idempotent, and backed up; packaged Electron writes only under the approved root
- **Implemented 2026-08-01:** canonical runtime paths now separate `data/household/` from `data/users/<validated-user-id>/`; Electron passes explicit runtime, household, and users roots; legacy `REX_DATA_DIR` semantics remain compatible; `scripts/migrate_runtime_data.py` provides backed-up, conflict-safe dry-run/apply migration. Local evidence: 8,109 CI-selected tests passed at 82.49% coverage, 36 integration tests passed, 26 GUI tests passed, Electron streaming verification passed, and Ruff/Black/mypy/security gates passed. GitHub PR checks remain required before final completion.

**S4 — Move desktop secrets into an OS-backed credential vault** (P1)
- Files/areas: `gui/src/main/configStore.ts:59-114`; `gui/src/main/handlers/settings.ts:31-55`; `gui/src/main/settingsMirror.ts:95-116,143-145`; `rex/credentials.py:1-7,26-46,111-233`
- Evidence/root cause: F-12 — Electron writes API keys to a plaintext `.env`; Python `CredentialManager` reads env/plaintext JSON; no Windows Credential Manager/DPAPI implementation; some integration secrets appear "Saved" but are silently discarded on mirror failure
- Steps: (1) Introduce a Windows Credential Manager or DPAPI-backed provider storing only opaque references in config. (2) Bind each reference to the validated Rex user and integration account. (3) Migrate existing `.env`/JSON secrets into the vault with a one-time, logged migration. (4) Surface and stop swallowing mirror-persistence failures in the settings UI.
- Tests: `tests/test_credentials.py`, `tests/test_email_account_isolation.py`
- Validation commands: `py -3.11 -m pytest -q tests/test_credentials.py tests/test_email_account_isolation.py`; `cd gui; npm.cmd test; npm.cmd run typecheck`; `python scripts/security_audit.py --release-gate`
- DoD: no plaintext secret persists outside the vault; a failed save surfaces an error instead of a false "Saved" state; packaged two-OS-user credential isolation test passes

### Batch 3 — Device-bound pairing and enforced encrypted transport

**S5 — Implement desktop-owned pairing authority (QR/one-time-code enrollment)** (P0)
- Files/areas: new `rex/mobile_api/pairing.py` (or equivalent), `rex/mobile_api/routes/`; new Electron pairing IPC/UI under `gui/src/main/handlers/`, `gui/src/renderer/src`
- Evidence/root cause: F-08 — login only checks username/password and creates a session; no enrollment, approval, or ownership proof exists
- Steps: (1) Add a short-lived, single-use QR/code enrollment endpoint on the desktop gateway. (2) Require device proof-of-possession (public key exchange) during enrollment. (3) Add a desktop approval UI/IPC step before any grant is issued. (4) Persist grants with scopes and expiry (feeds S6).
- Tests: new `tests/mobile_api/test_pairing.py`
- Validation commands: `py -3.11 -m pytest -q tests/mobile_api/test_pairing.py`; `cd gui; npm.cmd test; npm.cmd run typecheck`
- DoD: password alone cannot create an action-capable device; code replay/expiry/wrong-desktop/key-mismatch fail closed

**S6 — Extend the session model with an immutable device identity and capability grant** (P0)
- Files/areas: `rex/mobile_api/db.py:83-120`; `rex/mobile_api/sessions.py:284-430`; `rex/mobile_api/validation.py:105-148`
- Evidence/root cause: F-07 — sessions carry device metadata but no device public key, pairing approval, grant/scopes, desktop owner, or strong-auth timestamp
- Steps: (1) Add a device-identity table (public key/thumbprint, approved user, desktop ID). (2) Add a grant table (scopes, creation/expiry, last strong-auth time, revocation reason). (3) Evaluate every mobile action request against both the authenticated user and the paired-device grant.
- Tests: `tests/mobile_api/test_login.py`, `tests/mobile_api/test_refresh.py`, `tests/mobile_api/test_session_endpoint.py`, `tests/mobile_api/test_pairing.py`
- Validation commands: `py -3.11 -m pytest -q tests/mobile_api/test_login.py tests/mobile_api/test_refresh.py tests/mobile_api/test_session_endpoint.py tests/mobile_api/test_pairing.py`
- DoD: revoking a device grant terminates HTTP/SSE/WS access and blocks pending scoped actions; grants constrain which actions a device may request

**S7 — Enforce TLS/pinning for non-loopback mobile transport** (P1)
- Files/areas: `rex/config.py:252-270`; `rex/mobile_api/app.py:95-105`; `rex/commands/mobile.py:60-61`; mobile `constants/config.ts:1`; `app/settings.tsx:291-532`
- Evidence/root cause: F-09 — `require_tls` defaults false and is advisory only; mobile default points to a hosted URL, not a paired local desktop
- Steps: (1) Define and document one supported topology (LAN-paired desktop, no default hosted URL). (2) Enforce TLS in-process for any non-loopback bind. (3) Pin the paired desktop's certificate/public key during pairing (extends S5). (4) Reject insecure URLs in production mobile builds.
- Tests: `tests/mobile_api/test_config.py`, `tests/mobile_api/test_cli.py`, `tests/mobile_api/test_app.py`
- Validation commands: `py -3.11 -m pytest -q tests/mobile_api/test_config.py tests/mobile_api/test_cli.py tests/mobile_api/test_app.py`; mobile `npm.cmd test`
- DoD: non-loopback plaintext binds are rejected; certificate/host mismatch and replay are tested and fail closed
- **External dependency:** physical LAN/WAN validation with a real phone is out of scope for this repo-only cycle

**S8 — Make high-risk mobile actions server-denied until a bound strong-auth assertion is verified** (P0)
- Status (2026-08-02): desktop/server implementation complete on `lead/desktop-s8-strong-auth`; full CI/merge verification pending. Mobile Face ID/passcode orchestration and physical-iPhone validation remain external follow-up gates.
- Files/areas: `rex/mobile_api/strong_auth.py`, `routes/strong_auth.py`, `routes/home.py`, `action_context.py`; mobile `hooks/useBiometric.ts`, `services/biometricService.ts`, and native device-key bridge
- Evidence/root cause: F-13 — client-side checks fail open on unavailable hardware or disabled settings; the server approvals endpoint is a 501 scaffold with no principal carrying a recent strong-auth assertion
- Steps: (1) Implement a server-authoritative approval protocol: challenge-bound, short-lived assertion tied to action hash, device grant, user, expiry, and one-time nonce. (2) Assign risk classification server-side, not client-side. (3) Fail closed on unavailable/cancel/error/timeout.
- Tests: `tests/mobile_api/test_strong_auth.py`, `test_strong_auth_routes.py`, `test_home_strong_auth.py`, `test_chat_strong_auth.py`, and pairing/grant suites
- Validation commands: `py -3.11 -m pytest -q tests/mobile_api`; mobile `npm test`, typecheck, lint, and physical-device security matrix
- DoD: no high/critical mobile action executes without a verified server-side assertion bound to the specific action and device grant
- **External dependency:** physical Face ID/passcode hardware test matrix

### Batch 4 — Make every visible surface truthful

**S9 — Remove Electron fake success and fabricated data** (P0)
- Files/areas: `gui/src/main/handlers/sms.ts:79-109`; `gui/src/pages/SmsPage.tsx:118`; `gui/src/main/handlers/notifications.ts:5-94`; `gui/src/main/handlers/settings.ts:58-60`; `gui/src/main/handlers/email.ts:62-67`
- Evidence/root cause: F-04 — `rex:sendSMS` reports `sent` without a real send bridge; notifications seed fabricated events; `rex:testVoice` always returns success without audio; email reply is a fixed template
- Steps: (1) Make SMS send fail closed with `not_configured`/`not_implemented` until a real send bridge exists. (2) Remove fabricated notification seed data from production init; gate any sample data behind an explicit developer-demo flag/banner. (3) Make Test Voice perform real synthesis (tracked jointly with S24). (4) Mark email reply generation as a template/draft explicitly, not a sent reply.
- Tests: `cd gui; npm.cmd test`
- Validation commands: `cd gui; npm.cmd test; npm.cmd run typecheck; npm.cmd run build`; `Select-String -Path gui/src/main/handlers/*.ts -Pattern 'Stub:|status: .sent.|sampleNotifications'`
- DoD: no Electron production handler returns a fabricated success/data payload; static scan for the cited patterns returns zero matches outside an explicit demo-gated path

**S10 — Remove mobile production mock fallbacks and gate tabs by server capability** (P0)
- Files/areas: mobile `services/homeService.ts:36-76`, `hooks/useHomeDevices.ts:29-76`, `services/notificationService.ts:32-91`, `hooks/useInbox.ts:32-70`, `services/automationService.ts:61-180`, `hooks/useAutomations.ts:34-76`, `services/auditService.ts:26-130`, `app/(tabs)/_layout.tsx:62-127`
- Evidence/root cause: F-06 — server truthfully returns `501 NOT_IMPLEMENTED` for Home/Inbox/Automate/History, but client data layers fall back to realistic mock state on failure/empty response
- Steps: (1) Remove production mock/fallback data from each service. (2) Gate each tab's visibility/enabled-state from `/mobile/capabilities`. (3) Show an explicit "not implemented" empty state instead of fabricated content.
- Tests: mobile unit tests for each service/hook
- Validation commands: `cd <mobile-repo>; npm.cmd test; npm.cmd run lint; npx.cmd tsc --noEmit`; `Select-String -Path services/*.ts,hooks/*.ts -Pattern 'return MOCK_|useState.*MOCK_'`
- DoD: with all scaffold capabilities false, no mobile tab renders fabricated door/garage/approval/workflow/audit data

**S11 — Add a surface-truth regression gate to both repos' release checks** (P1)
- Files/areas: desktop `scripts/security_audit.py`; `.github/workflows/ci.yml:55-70,512-536`; mobile CI (extends S2)
- Evidence/root cause: F-29, F-30 — green automated checks currently coexist with fabricated production data because no test asserts every visible action/state is server-authoritative
- Steps: (1) Enumerate every visible capability/action in a manifest (desktop and mobile). (2) Add a release test requiring each entry to be one of: verified-real, explicitly disabled, or developer-demo-gated. (3) Ban production imports of mock datasets and false `ok/sent/success` handlers via static scan in CI.
- Tests: new capability-truth matrix tests in both repos
- Validation commands: `python scripts/security_audit.py --release-gate`; mobile secret/truthfulness scan (new); `cd gui; npm.cmd test; npm.cmd run build`
- DoD: CI fails if a new fabricated-success handler or ungated mock is introduced

### Batch 5 — Unify the canonical capability registry with Electron configuration

**S12 — Build one typed capability manifest from the tool registry, IPC, and UI inventories** (P1)
- Files/areas: `rex/tools/registry.py:236-568`; `gui/src/main/integrationInventory.ts:135-146`; `gui/src/main/ipc.ts:1-46`; new shared manifest module
- Evidence/root cause: F-01 — 28 canonical tools exist but Electron's capability inventory exposes only 6; three inventories have no shared projection contract
- Steps: (1) Define one typed capability model: source, availability, read/write, risk, confirmation requirement, verifier state, settings destination, last health check. (2) Project the canonical tool registry and integration status into it. (3) Keep UI-only surfaces (e.g. Logs, Usage) as explicit non-tool capabilities in the same model.
- Tests: `tests/test_tool_registry.py`, `tests/test_tool_execution_lifecycle.py`
- Validation commands: `py -3.11 -m pytest -q tests/test_tool_registry.py tests/test_tool_execution_lifecycle.py`; `cd gui; npm.cmd test; npm.cmd run typecheck; npm.cmd run build`
- DoD: the manifest lists all 28 registry tools plus UI-only surfaces with no gaps against `rex/tools/registry.py`

**S13 — Add a Capabilities page and link every real capability to its settings/status destination** (P1)
- Files/areas: `gui/src/renderer/src/App.tsx:106-127`; `gui/src/layouts/AppLayout.tsx:133-336`; `gui/src/main/integrationInventory.ts:50-146`; `gui/src/pages/SettingsPage.tsx`
- Evidence/root cause: F-02 — History and Quick Actions have routes but no navigation entry; Music Assistant, n8n, ComfyUI, Plex, WooCommerce, WordPress, and several registry tools have no settings destination; MQTT/push/web search/OpenClaw cards point to unrelated or missing settings
- Steps: (1) Add one Capabilities page driven by the S12 manifest. (2) Add navigation entries for orphaned routes or explicitly mark them hidden/developer-only. (3) Point every integration card to its correct settings section or mark it truthfully unsupported. (4) Remove cards for capabilities that cannot be configured, or add a disabled-with-reason state.
- Tests: `cd gui; npm.cmd test`
- Validation commands: `cd gui; npm.cmd test; npm.cmd run typecheck; npm.cmd run build`; Electron harness: `node tmp_verify_capability_navigation.cjs`
- DoD: registry-to-UI parity test finds no orphan capability or navigation target

**S14 — Fix default AI provider/model validation** (P0)
- Files/areas: `gui/src/main/settingsDefaults.ts:41-53`; `gui/src/main/aiSettings.ts:38-56`
- Evidence/root cause: F-03 — default provider is `openai` while default model is `claude-sonnet-4`; `buildAiSettings()` accepts any provider's model names in one unvalidated field
- Steps: (1) Make the model catalog provider-specific. (2) Choose a valid default (local-safe or a configured provider). (3) Reject incompatible provider/model pairs at save time. (4) Add an explicit connection/model check before claiming a configuration is ready.
- Tests: GUI `aiSettings` tests; `tests/test_model_router.py`
- Validation commands: `cd gui; npm.cmd test -- aiSettings; npm.cmd run typecheck`; `py -3.11 -m pytest -q tests/test_model_router.py`
- DoD: a fresh install cannot save a provider/model pair the provider cannot execute

### Batch 6 — Complete assistant routing, retrieval, planning, and recovery

**S15 — Make model/provider selection request-local and concurrency-safe with real fallback** (P0)
- Files/areas: `rex/model_router.py:252-341`; `rex/assistant.py:766-825`; `rex/actions/dispatcher.py:303-314`; `rex/llm_client.py:573-665,772-861`
- Evidence/root cause: F-18 — `resolve_model()` can return an unavailable default; `Assistant` mutates a shared `self._llm.model_name` per request without a lock, spanning `await` work; only `TypeError` is handled at the generation boundary; `cloud_limit_hit()` has no call site
- Steps: (1) Pass the selected model/provider as immutable request-local context instead of mutating shared state. (2) Preflight provider configuration before generation. (3) Classify auth/quota/transient/model-not-found failures distinctly. (4) Execute a bounded, policy-defined fallback chain and surface degraded state. (5) Wire `cloud_limit_hit()` into the failure path.
- Tests: `tests/test_model_router.py`; new `tests/test_assistant_concurrency.py`
- Validation commands: `py -3.11 -m pytest -q tests/test_model_router.py tests/test_assistant_concurrency.py`; injected 401/404/429/5xx/local-offline scenarios
- DoD: concurrent mixed-model requests show no cross-request model leakage; injected provider failures follow the configured fallback chain

**S16 — Wire ranked long-term memory retrieval into context building** (P1)
- Files/areas: `rex/context/builder.py:214-341`; `rex/memory.py` (`LongTermMemory.search()`)
- Evidence/root cause: F-19 — context injects profile/facts/last-four-turns only; `LongTermMemory.search()` is never called by the context builder
- Steps: (1) Add a request-scoped retrieval step calling `LongTermMemory.search()` scoped to the validated user. (2) Rank by relevance, freshness, confidence, and source. (3) Bound the injected context size.
- Tests: `tests/test_us070_memory_search.py`; new multi-user retrieval isolation test
- Validation commands: `py -3.11 -m pytest -q tests/test_us070_memory_search.py`
- DoD: retrieved memory is user-isolated and demonstrably relevance-ranked in tests

**S17 — Wire the planner into the action dispatcher for multi-step requests** (P1)
- Files/areas: `rex/actions/dispatcher.py:173-240`; `rex/planner.py`; `rex/assistant.py:179-193`
- Evidence/root cause: F-19 — `Planner` is only constructed by a workflow CLI command, not by the Assistant/dispatcher
- Steps: (1) Add a decision point in the dispatcher: direct/tool/plan mode based on request complexity. (2) When plan mode triggers, generate an inspectable step list and execute each step through the canonical tool lifecycle (never bypassing it). (3) Add checkpoint/resume for interrupted plans.
- Tests: `tests/test_planner.py`; new interrupted-plan recovery test
- Validation commands: `py -3.11 -m pytest -q tests/test_planner.py tests/test_tool_execution_lifecycle.py`
- DoD: a multi-step chat request produces an inspectable plan whose steps are independently verified and resumable after interruption

**S18 — Load local skills dynamically via the existing skill loader** (P2)
- Files/areas: `rex/assistant.py:179-187`; `rex/skills/loader.py:72-115`
- Evidence/root cause: F-19 — Core Assistant constructs a skill registry/router but never calls `load_skills_from_directory()`
- Steps: (1) Call the loader during Assistant initialization for the validated user's skill directory. (2) Version the loaded registry and reject unsigned/unapproved skills per the security baseline.
- Tests: `tests/test_skill_loader.py`
- Validation commands: `py -3.11 -m pytest -q tests/test_skill_loader.py`
- DoD: a skill placed in the configured directory is discoverable in chat without a code change

**S19 — Add a request-level failure/recovery policy at the LLM/tool orchestration boundary** (P1)
- Files/areas: `rex/actions/dispatcher.py:226-240,290-314`; `rex/assistant.py:766-790`
- Evidence/root cause: F-20 — no bounded retry/replan/fallback wrapper exists around auto-tool selection/execution or LLM generation; provider/network/quota errors escape uncaught
- Steps: (1) Define typed failure categories (transient, auth, quota, tool-denied, verification-failed). (2) Add retry budgets gated by idempotency and risk (never retry unverified mutations). (3) Add safe replan and partial-result preservation with user-visible degraded status.
- Tests: new `tests/test_assistant_recovery.py`
- Validation commands: `py -3.11 -m pytest -q tests/test_model_router.py tests/test_tool_execution_lifecycle.py tests/test_assistant_recovery.py`
- DoD: an injected transient failure degrades gracefully with a structured partial result instead of terminating the turn

### Batch 7 — Complete the optional OpenClaw ecosystem control plane

**S20 — Build an OpenClaw discovery adapter with metadata/version/health sync** (P1)
- Files/areas: new `rex/openclaw/discovery.py`; `rex/openclaw/tool_bridge.py:100-188`; `rex/openclaw/tool_registry.py:28-30`
- Evidence/root cause: F-22, checklist "Dynamic Plugin Discovery" — no production call imports plugin/skill metadata or syncs the registry
- Steps: (1) Add a versioned adapter that imports plugin/skill metadata only (no execution) from the OpenClaw gateway. (2) Sync capability/version/health changes into a local registry. (3) Support runtime refresh without restarting Rex.
- Tests: new `tests/test_openclaw_discovery.py` against a fake gateway
- Validation commands: `py -3.11 -m pytest -q tests/test_openclaw_discovery.py tests/test_openclaw_contracts_audit.py`
- DoD: install/change/removal events on a fake gateway update the local registry without a restart

**S21 — Add allowlist/denylist approval, risk diff, and per-user/household grants for OpenClaw tools** (P1)
- Files/areas: `rex/openclaw/tool_bridge.py`; new permission-profile module; ties into `rex/tools/execution.py`
- Evidence/root cause: F-22, checklist "Safety and Permissions" — no allowlist/denylist, permission profiles, or approval flow exists
- Steps: (1) Present a capability/risk/permission diff for any newly discovered plugin/skill. (2) Require explicit approval before it becomes callable. (3) Store per-user/household grants. (4) Deny by default for unapproved capabilities.
- Tests: new `tests/test_openclaw_policy_gated_tools_e2e.py`
- Validation commands: `py -3.11 -m pytest -q tests/test_openclaw_policy_gated_tools_e2e.py`
- DoD: an unapproved discovered capability cannot be invoked; an approved one is scoped to the granting user/household

**S22 — Make OpenClaw health/readiness states explicit and non-overclaiming** (P1)
- Files/areas: `rex/openclaw/agent.py:122-144`; `rex/openclaw/tool_registry.py:28-30`; `gui/src/main/integrationStatus.ts:161-165`
- Evidence/root cause: F-23 — default tool health check always returns `(True, "OK")`; Electron status reflects configuration presence, not proven readiness
- Steps: (1) Introduce explicit states: disabled, configured, reachable, authenticated, discovered, approved, read-capable, write-capable, write-tested, verifier-capable, degraded. (2) Default unknown health to unknown/unavailable, never healthy.
- Tests: `tests/test_openclaw_agent_basic.py`, `tests/test_openclaw_tool_bridge_http.py`
- Validation commands: `py -3.11 -m pytest -q tests/test_openclaw_agent_basic.py tests/test_openclaw_tool_bridge_http.py tests/test_openclaw_policy_gated_tools_e2e.py`
- DoD: a configured-but-unreachable OpenClaw endpoint never reports as healthy

**S23 — Add an Electron OpenClaw settings/status page** (P2)
- Files/areas: `gui/src/main/integrationInventory.ts:127-131`; `gui/src/pages/SettingsPage.tsx`; new OpenClaw settings route
- Evidence/root cause: F-22, checklist "GUI Integration" — inventory card points to unrelated AI settings; no plugin/skill listing, enable/disable, or health display exists
- Steps: (1) Add a dedicated OpenClaw settings page showing endpoint config, discovered plugins/skills, health state (from S22), and per-item enable/disable. (2) Add permission-profile management UI (ties to S21).
- Tests: `cd gui; npm.cmd test`
- Validation commands: `cd gui; npm.cmd test; npm.cmd run typecheck`
- DoD: the OpenClaw integration card links to a page that reflects live discovery/health/permission state, not a static config stub

### Batch 8 — Close production voice behavior on packaged hardware

**S24 — Implement real Test Voice synthesis and playback** (P0)
- Files/areas: `gui/src/main/handlers/settings.ts:58-60`; `gui/src/pages/VoicePage.tsx:648-689`
- Evidence/root cause: F-16 — Test Voice always returns success without producing audio
- Steps: (1) Perform actual TTS synthesis using the configured engine/voice. (2) Play back through the selected output device. (3) Return explicit provider/device failure states instead of blanket success.
- Tests: `cd gui; npm.cmd test`
- Validation commands: `cd gui; npm.cmd test; npm.cmd run build`; packaged selected-device smoke test
- DoD: a broken TTS/device route surfaces a real failure instead of a false "OK"

**S25 — Share one device/volume contract across hold-to-talk and wake mode, add wake-mode barge-in** (P0)
- Files/areas: `gui/src/main/handlers/voice.ts:42-145`; `gui/src/main/settingsMirror.ts:41-80`; `bridge/rex_voice_bridge.py:517-557`; `rex/voice/loop.py:74-126,180-249,325-360,521-853`
- Evidence/root cause: F-15 — the wake bridge builds its microphone/TTS path without the Electron-selected device IDs; voice settings mirroring omits device IDs and volume; no barge-in/cancel-TTS path exists for wake mode
- Steps: (1) Extend settings mirroring to include input/output device IDs and volume for wake mode. (2) Add a cancellation primitive that stops playback/generation and rearms capture, shared with hold-to-talk. (3) Validate device removal, sleep/resume, and restart recovery paths.
- Tests: `tests/test_voice_loop.py`, `tests/test_voice_loop_fixes.py`, `tests/test_us137_voice_rearm.py`, `tests/test_us138_voice_roundtrip.py`
- Validation commands: `py -3.11 -m pytest -q tests/test_voice_loop.py tests/test_voice_loop_fixes.py tests/test_us137_voice_rearm.py tests/test_us138_voice_roundtrip.py`
- DoD: interrupting Rex mid-response in wake mode stops playback and rearms capture, matching hold-to-talk behavior
- **External dependency:** physical wake-word/barge-in hardware validation

**S26 — Replace the synthetic voice-enrollment backend with a real embedding backend** (P1)
- Files/areas: `rex/voice_identity/ui_service.py:45-66`; `rex/voice_identity/embedding_backends.py:70-100`
- Evidence/root cause: F-15 — GUI enrollment defaults to a `synthetic` backend that hashes raw audio bytes and ignores sample rate; documented for testing only
- Steps: (1) Disable release enrollment unless a real, healthy embedding backend is active. (2) Surface backend health in the enrollment UI.
- Tests: `tests/test_voice_enrollment.py`, `tests/test_voice_identifier.py`
- Validation commands: `py -3.11 -m pytest -q tests/test_voice_enrollment.py tests/test_voice_identifier.py`
- DoD: enrollment is blocked with an explicit message when only the synthetic backend is available

**S27 — Define latency thresholds and run the physical hardware validation matrix** (P1)
- Files/areas: `rex/voice/loop.py:74-126`; `tests/test_us167_voice_latency.py`
- Evidence/root cause: F-16 — timing instrumentation exists but no current hardware measurements or release thresholds are defined
- Steps: (1) Define wake-to-ack, STT, first-token, synthesis, first-audio, and total-response thresholds, separated by local/cloud and cold/warm start. (2) Gate release on measured thresholds using structured timing export.
- Tests: `tests/test_us167_voice_latency.py`, `tests/test_us010_voice_pipeline_timeouts.py`
- Validation commands: `py -3.11 -m pytest -q tests/test_us167_voice_latency.py tests/test_us010_voice_pipeline_timeouts.py`
- DoD: a signed/packaged build passes the defined thresholds on the physical microphone/speaker matrix
- **External dependency:** physical audio hardware

**S35 - Add provider-neutral SpeechRouter and VoiceStudio provider integration** (P1)
- Canonical design: `docs/voice/SPEECH_ROUTER_VOICESTUDIO.md`
- Owners: backend primary; mobile participates only through the authenticated AskRex gateway contract
- Goal: preserve the canonical Assistant/TurnEngine voice path while making STT and TTS independently routable across native/local, VoiceStudio, explicitly permitted cloud, and future providers
- Required first steps: inspect the current backend/mobile implementations and current `debpalash/VoiceStudio`; identify/evolve existing STT/TTS abstractions rather than creating a parallel voice architecture
- Privacy/security: `Local Only` must never silently fall back to cloud; provider transcripts remain untrusted input; provider services cannot determine identity, permissions, confirmation, action success, or verification
- Migration: adapt existing providers first, add VoiceStudio through HTTP/WebSocket service boundaries, preserve rollback, provider-independent voice aliases (`majel`, `james`, `cole`), and keep mobile provider-neutral
- Validation: native + VoiceStudio STT/TTS, independent provider selection, health/timeouts/errors, fallback policy, cloud permitted/prohibited, voice alias mapping, cancellation/stale output, desktop path, authenticated mobile path, privacy/logging, and reliability/latency evidence
- Non-goals: no full-duplex Voice Mode, no VoiceStudio fork/source merge, no direct iPhone-to-VoiceStudio path, no unrelated AskRex refactor
- DoD: desktop and mobile speech paths resolve through the canonical provider layer; VoiceStudio works as a replaceable local provider; native/cloud alternatives and Local Only remain supported; existing voice behavior and security boundaries remain intact

### Batch 9 — Unify brand, design tokens, terminology, navigation, and versions

**S28 — Replace mobile OnSpace identity with canonical AskRex brand assets** (P1)
- Files/areas: mobile `app.json:3-8`, `package.json:2-4`, `app/+not-found.tsx:1-6`, `app/settings.tsx:883-888`, `assets/images/logo.png`; `docs/BRANDING.md`
- Evidence/root cause: F-27 — mobile app/package/slug/scheme remain `onspace-app`/`onspaceapp`; footer says "Powered by OnSpace.AI"; logo asset hash differs from desktop
- Steps: (1) Replace app/package/slug/scheme/display metadata per `docs/BRANDING.md`. (2) Import canonical logo/icon/splash assets. (3) Remove all OnSpace references. (4) Derive displayed version/build from the runtime manifest instead of a hardcoded string.
- Tests: mobile unit/lint/typecheck
- Validation commands: `npm.cmd test; npm.cmd run lint; npx.cmd tsc --noEmit; npx.cmd expo config --type public`; text scan for `onspace|OnSpace`
- DoD: no tracked file references OnSpace; icon/splash/login/settings use approved AskRex assets

**S29 — Generate shared design tokens and capability-aware navigation from one source** (P2)
- Files/areas: desktop `gui/src/styles/tokens.css:1-11`, `gui/src/layouts/AppLayout.tsx:133-336`; mobile `constants/theme.ts:1-47`, `app/(tabs)/_layout.tsx:62-127`, `constants/Colors.ts`
- Evidence/root cause: F-28 — desktop/mobile define colors/spacing/navigation terminology independently; mobile also retains an unused Expo-template color module
- Steps: (1) Create one platform-neutral token/terminology source. (2) Generate CSS and TypeScript outputs from it. (3) Derive mobile navigation labels from capability state (ties to S10). (4) Delete the dead `constants/Colors.ts` template source.
- Tests: token-generation drift check; desktop/mobile unit tests
- Validation commands: `cd gui; npm.cmd test; npm.cmd run build`; mobile `npm.cmd test; npx.cmd tsc --noEmit`
- DoD: generated token outputs are drift-free between the two clients; no orphan token source remains

**S30 — Establish one product version/build source across Python, Electron, and mobile** (P1)
- Files/areas: `pyproject.toml:7`; `gui/package.json:3,15,59-65`; `gui/src/main/index.ts:16`; `.github/workflows/windows-electron-artifact.yml:66-99`
- Evidence/root cause: F-25 — Python is 1.4.1 while Electron is 1.0.0; workflow installer paths are hardcoded to 1.0.0; Electron app ID differs between runtime and Builder config
- Steps: (1) Define one version source of truth. (2) Synchronize `pyproject.toml`, `gui/package.json`, Electron app ID, and workflow hardcoded paths from it. (3) Add a CI check that fails on drift.
- Tests: new version-consistency script
- Validation commands: version manifest/consistency script; `cd gui; npm.cmd run dist`
- DoD: every artifact (Python package, Electron binary, installer filename, in-app About page) reports the same version for a given release commit

### Batch 10 — Establish signed desktop and private native mobile release trains

**S31 — Require Authenticode signing for release builds** (P0)
- Files/areas: `.github/workflows/windows-electron-artifact.yml:66-99`
- Evidence/root cause: F-25 — Windows CI passes with an unsigned artifact when certificate secrets are absent
- Steps: (1) Make the release workflow fail (not warn) when signing secrets are absent on a release trigger. (2) Keep unsigned builds allowed only for non-release CI runs.
- Tests: workflow dry-run
- Validation commands: `Get-AuthenticodeSignature 'gui/dist/AskRex Setup <version>.exe'`; `cd gui; npm.cmd run dist`
- DoD: a release-tagged CI run cannot publish an unsigned installer
- **External dependency:** code-signing certificate provisioning

**S32 — Implement signed updater with staged rollout and rollback** (P1)
- Files/areas: `gui/src/main/index.ts`; new `electron-updater` integration; release-please workflow
- Evidence/root cause: F-25 — no `electron-updater`/`autoUpdater` implementation exists; Release Please does not publish/attach the Windows artifact
- Steps: (1) Integrate `electron-updater` with signed update metadata. (2) Attach the installer and metadata to the GitHub release. (3) Implement staged rollout and a documented rollback path.
- Tests: offline signed-update metadata verification
- Validation commands: offline signed-update metadata verification and rollback test
- DoD: a clean-machine install can update and roll back using signed metadata, verified without a live server

**S33 — Establish native iOS/Android identifiers, signing, and private distribution** (P1)
- Files/areas: mobile `app.json:2-45`; new `eas.json` or equivalent native build config
- Evidence/root cause: F-26 — mobile CI has no native build/signing/entitlement/private-distribution workflow; `app.json` lacks final bundle/package identifiers
- Steps: (1) Define final iOS bundle ID / Android package identity. (2) Configure native signing (TestFlight internal / Android internal track). (3) Restrict distribution to explicit approved devices/users. (4) Add native build/install/smoke evidence to CI.
- Tests: native CI build/signature/install tests
- Validation commands: mobile CI native build/signature/install tests
- DoD: a private build installs only through the approved channel with a verified signature
- **External dependency:** Apple/Google developer account provisioning

**S34 — Produce one release evidence manifest combining mock/local/hardware/live-provider evidence** (P1)
- Files/areas: new `docs/release/RELEASE_EVIDENCE_TEMPLATE.md` or equivalent; ties into `scripts/verify_electron_package_contents.py`, `scripts/test_installed_electron_artifact.ps1`
- Evidence/root cause: F-24 — static workflow design is strong but hosted CI/artifact success cannot currently be claimed without execution evidence; F-30 — green checks can be misread as readiness
- Steps: (1) Define a release manifest template capturing exact commit, CI status, package-content report, installed-artifact smoke result, signature hash, and explicit labels for mock/local/hardware/live-provider evidence. (2) Require it as a release gate with zero waived Critical/High findings.
- Tests: manifest completeness check (script or checklist)
- Validation commands: `py -3.11 -m pytest -q`; `cd gui; npm.cmd ci; npm.cmd test; npm.cmd run typecheck; npm.cmd run build; npm.cmd run dist`; `python scripts/verify_electron_package_contents.py gui/dist/win-unpacked/resources`
- DoD: no release is approved without a completed manifest distinguishing evidence class per claim

## 4. Coverage checklist (assignment §5)

| Required coverage area | Batch(es) |
|---|---|
| Truthful surfaces | Batch 4 (S9–S11), reinforced by S24 |
| Runtime/data/credential isolation | Batch 2 (S3–S4) |
| Pairing/capability broker | Batch 3 (S5–S8) |
| Intelligence/context/model fallback | Batch 6 (S15–S19) |
| Voice | Batch 8 (S24-S27, S35), preserving F-14 and adding provider-neutral speech routing |
| OpenClaw | Batch 7 (S20–S23), fulfilling the active checklist |
| Design parity | Batch 9 (S28–S30) |
| Packaging/signing | Batch 10 (S31–S32) |
| Private mobile distribution | Batch 1 (S1–S2), Batch 10 (S33–S34) |

## 5. First-cycle selection (start immediately, no external dependency)

These five stories require no external credentials, hardware, or account provisioning and can start in this repository today:

1. **S2** — Add secret scanning/dependency audit gates to mobile CI
2. **S3** — Define and wire one Windows application data root
3. **S9** — Remove Electron fake success and fabricated data
4. **S12** — Build one typed capability manifest from the tool registry, IPC, and UI inventories
5. **S14** — Fix default AI provider/model validation

**External validation still tracked:** S1 (provider token revocation — needs account owner), S5–S8 physical phone/LAN/Face ID validation, S25/S27 audio hardware, and S31/S33 code-signing or Apple/Google account dependencies. The repository implementation of S5–S8 may proceed and merge independently when its non-hardware gates pass.

## 6. External/hardware dependency ledger

| Dependency | Blocks | Owner action needed |
|---|---|---|
| GitHub PAT revocation | S1 | James revokes/rotates via provider console |
| Physical microphone/speaker/wake-word hardware | S25, S27 | Device access during test cycle |
| Physical phone for LAN/WAN pairing | S7, S8 | Device access during test cycle |
| Windows code-signing certificate | S31, S32 | Certificate purchase/provisioning (no paid service without approval per operating rules) |
| Apple Developer / Google Play accounts | S33 | Account provisioning by James |

## 7. Agent Runtime / MCP roadmap amendment (2026-09-14)

The approved Agent Runtime, MCP, skills, delegation, and controlled self-extension program is a continuation of this backlog rather than a separate project. After S35, use dependency-ordered stories S36-S58 from `docs/planning/AGENT_RUNTIME_MCP_INTEGRATED_BACKLOG.md`.

Authoritative architecture: `docs/architecture/ADR-AGENT-RUNTIME-MCP-001.md` and `docs/superpowers/specs/2026-09-14-agent-runtime-mcp-skills-design.md`.

Source-of-truth acceptance checklist: `docs/planning/source-of-truth/REX_AGENT_RUNTIME_MCP_CHECKLIST.md`.

Implementation plan: `docs/superpowers/plans/2026-09-14-agent-runtime-mcp-implementation.md`.

Rex Core remains authoritative for identity, permissions, credentials, memory, model routing, capability metadata, action lifecycle, verification, audit, scheduling, and security. Agent configuration may reduce authority but may never create authority. MCP extends the canonical capability model and does not replace Rex internal coordination or the Ralph/supervisor workflow.
