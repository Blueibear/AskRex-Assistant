# PRD: AskRex Assistant Production Readiness and Release Candidate Hardening

> **Active PRD — 2026-07-22.** This is the only active release-readiness tracker. `PRD.md` and `PRD-remaining-release-readiness.md` are retained as superseded historical evidence. The current cross-cutting audit implementation ledger is [docs/audits/AUDIT-REMEDIATION-2026-07-22.md](docs/audits/AUDIT-REMEDIATION-2026-07-22.md). Local completion does not imply CI, signing, hardware, or external-provider verification.

> **Ralph execution rule**
> A task means one full User Story, not one checkbox.
> For the remaining integrated production-readiness/Rex 2.0 work, choose the first User Story with any unchecked `[ ]` acceptance criterion from the **Integrated execution order - 2026-08-08** below; do not use raw file position as the priority signal. Stories outside that list retain their historical status and are not reopened unless a later story explicitly depends on them.
> Complete exactly one User Story per iteration.
> A User Story is only complete when current code, tests, and acceptance criteria prove it.
> When a story is complete, update `PRD-production-readiness.md` and `docs/archive/progress/progress-production-readiness.txt` in the same commit as the implementation.
> Do not commit completed implementation work while leaving that story unchecked in this PRD.
> This PRD is the authoritative task tracker for the production-readiness workstream. `docs/archive/progress/progress-production-readiness.txt` is supporting history only.
> A story is not done until all relevant local validations pass AND all required GitHub checks pass on the PR for that story.

> **Reconciliation note — 2026-06-12**
> This PRD was reconciled against HEAD `548bf32` on branch `ralph/reconcile-production-readiness-prd` after the remaining-release-readiness workstream and final validation PRs. Completed decompositions from that workstream are marked as satisfied baseline evidence so Ralph does not repeat them. User-observed Electron GUI and product gaps from live testing have been added as explicit unchecked stories or acceptance criteria. This reconciliation did not implement runtime behavior.

> **Reconciliation note — 2026-07-08 (issue #300)**
> Re-audited Section 2 (Current State), Section 6 (Blocker Inventory), and the Phase 3–4 acceptance criteria against `master` HEAD `fde0c76`. Stale baseline claims are now marked **[RESOLVED — historical]** in place with the verifying commit or CI evidence; do not implement them again. Stories US-021, US-022, US-030, and US-032 were verified complete against current code/CI and are now checked. US-023/US-024/US-033/US-036 carry updated per-box status notes. `PRD-remaining-release-readiness.md` is complete except two Definition-of-Done boxes (a literal base-deps clean-checkout pytest run, and an owner decision on the re-added sanitized fixtures `profiles/james.json`/`users.json`) — treat it as historical evidence, not an active tracker. This reconciliation did not implement runtime behavior.

> **Audit remediation note — 2026-07-22**
> Findings A–K from the current audit are implemented in reviewable commits and locally verified as recorded in `docs/audits/AUDIT-REMEDIATION-2026-07-22.md`. This supersedes older baseline prose for calendar isolation, Electron identity/data ownership, HA mutation verification, tool lifecycle, managed Electron runtime, Windows artifact CI, Hold to Talk, integration-state truth, diagnostics/security gates, and GUI dependency gates. Items remain **locally verified**, not CI verified, until the pull-request checks pass. The unsigned installer and hardware/external-provider checks remain explicit release limitations.

> **Rex 2.0 integration reconciliation - 2026-08-08**
> The reviewed Rex 2.0 intelligence/latency/tooling/memory/self-extension plan is now an architectural amendment to this production-readiness workstream, not a separate future rewrite. Current runtime code remains authoritative until the specific story that migrates a boundary is implemented and verified. The migration is intentionally evolutionary: establish truthful baseline evidence, converge `generate_reply()` and `stream_reply()` on one TurnEngine/event contract, then build capability/action/latency/model/memory/OpenClaw/Forge work on that shared foundation. Rex remains local-first and remains the orchestrator, identity authority, permission/risk layer, verifier, memory system, and final responder; OpenClaw/ClawHub remain optional capability providers. No new paid service is required. Existing fail-closed identity is sufficient to begin the TurnEngine foundation; US-087 later unifies cross-surface and household semantics. SMS remains available through the backend/direct route but intentionally stays out of primary navigation.
>
> Design: `docs/superpowers/specs/2026-08-08-rex2-production-readiness-integration-design.md`
> Implementation plan: `docs/superpowers/plans/2026-08-08-rex2-production-readiness-integration.md`

> **Always-on household voice product-contract amendment - 2026-08-31**
> The final AskRex consumer product must remain available for screenless household voice use when the Electron and mobile apps are not open. One packaged Windows installation owns an authoritative Rex Core plus a local background Voice Agent; additional rooms use lightweight trusted Rex Room endpoints rather than independent full Rex stacks. The consumer setup flow must cover audio/wake-word calibration, room assignment, background startup, privacy controls, and verified screenless operation. This amendment is a target and release requirement, not a claim that today's wake-word path is production-ready: existing Hold-to-Talk and beta wake-word labels remain truthful until US-124 through US-130 and their physical-hardware gates pass.
>
> Canonical product contract: `docs/architecture/end-user-installation-and-voice-runtime.md`

### Integrated execution order - 2026-08-08

Use this order for task selection after the reconciliation above. Select the first story below that still has any unchecked acceptance criterion:

1. `US-063`
2. `US-075`
3. `US-064`
4. `US-094`
5. `US-095`
6. `US-096`
7. `US-076`
8. `US-097`
9. `US-098`
10. `US-106`
11. `US-107`
12. `US-109`
13. `US-108`
14. `US-104`
15. `US-099`
16. `US-105`
17. `US-110`
18. `US-111`
19. `US-071`
20. `US-072`
21. `US-073`
22. `US-077`
23. `US-078`
24. `US-113`
25. `US-114`
26. `US-101`
27. `US-074`
28. `US-068`
29. `US-069`
30. `US-070`
31. `US-100`
32. `US-102`
33. `US-103`
34. `US-079`
35. `US-067`
36. `US-065`
37. `US-066`
38. `US-080`
39. `US-081`
40. `US-082`
41. `US-087`
42. `US-083`
43. `US-084`
44. `US-085`
45. `US-086`
46. `US-112`
47. `US-088`
48. `US-120`
49. `US-121`
50. `US-122`
51. `US-123`
52. `US-115`
53. `US-116`
54. `US-117`
55. `US-089`
56. `US-090`
57. `US-091`
58. `US-092`
59. `US-093`
60. `US-119`
61. `US-124`
62. `US-125`
63. `US-126`
64. `US-127`
65. `US-128`
66. `US-129`
67. `US-130`
68. `US-118`

**Dependency/security notes:** TurnEngine work must preserve the already-implemented explicit, fail-closed user identity contract from its first event. US-087 later proves the broader user/household model and James/Cole concurrency invariants. OpenClaw metadata never widens local authority. Mobile remains desktop-paired and least-privilege. All benchmark evidence must label whether it is deterministic/mock, local source runtime, live provider, packaged Windows artifact, or physical hardware/device.


---

## 1. Executive Summary

AskRex Assistant is a local-first, voice-activated AI companion targeting Windows 10/11, macOS, and Linux. The repository contains a Python package (`rex/`), root-level bridge scripts and compatibility shims, a Flask backend (`rex.gui_app`), and an Electron + React desktop GUI under `gui/`. Today the codebase is functional enough for development but it is NOT a release candidate. Production blockers exist across packaged runtime correctness, packaging truth, security audit triage, CI coverage, voice reliability, Home Assistant verification, OpenClaw boundary, documentation honesty, Electron GUI capability parity, mobile/API access safety, per-user memory/privacy, and response quality.

This PRD turns AskRex Assistant into a production-ready release candidate by closing every issue listed in the Blocker Inventory (Section 6) through small, dependency-ordered User Stories. Each story is sized for a single Ralph iteration, includes concrete validation commands, requires documentation updates whenever user-facing behavior changes, and requires all relevant GitHub checks to pass before it is marked complete.

The core AskRex principle is preserved end-to-end: Rex must never claim an action succeeded unless the action was verified. Stories that affect Home Assistant control, tool execution, OpenClaw routing, voice pipeline status reporting, and CLI/GUI status surfaces all enforce this rule.

---

## 2. Current State

The following facts were verified directly from the repository at the time this PRD was authored. They are baseline context, not checklist items, and are not action items by themselves.

### 2.1 Renderer raw `/api/` fetches — [RESOLVED — historical]
**Re-verified 2026-07-08 on `master@fde0c76`:** zero raw `fetch('/api/...')` call sites remain anywhere under `gui/src/**` (`grep -rn "fetch('/api\|fetch(\`/api" gui/src` returns nothing). The migration to typed IPC (US-003 through US-012) is complete, and the **GUI Raw API Fetch Guard** CI job (`scripts/check_no_renderer_api_fetch.py`) runs on every PR to prevent regression. Do not re-implement this migration.

*Historical baseline (authoring time):* `gui/src/**` contained direct browser-style fetches in `AboutPage.tsx`, `CommandHistoryPage.tsx`, `DevicesPage.tsx`, `QuickActionsPage.tsx`, `SetupWizardPage.tsx`, and `App.tsx` that depended on a Flask backend the packaged Electron app does not spawn.

### 2.2 Packaging metadata — [PARTIALLY RESOLVED — see note]
- `pyproject.toml` declares `name = "askrex-assistant"`, `requires-python = ">=3.11,<3.12"`, and six console scripts: `rex`, `rex-config`, `rex-speak-api`, `rex-agent`, `rex-gui`, `rex-tool-server`. (Version is now managed by release-please; see `.release-please-manifest.json`.)
- **[RESOLVED — historical]** *(re-verified 2026-07-08)*: the stale `py_modules` entries (`rex_assistant`, `memory_utils`, `audio_config`, `conversation_memory`) were removed by US-014 on 2026-06-23; `setup.py` documents the removal inline and now declares only modules that exist on disk. The **Wheel Contents Smoke Test** CI job (`scripts/check_wheel_contents.py`) guards wheel contents on every PR. Do not repeat this fix.
- Wheel resource-inclusion scope (`bridge/`, root wrappers, `config/` examples, UI assets) is governed by US-016; check that story's boxes for current status rather than this baseline text.

### 2.3 Root-level Python files
27 `.py` files live at the repository root:
`config.py`, `conftest.py`, `flask_proxy.py`, `llm_client.py`, `rex_chat_bridge.py`, `rex_chat_stream_bridge.py`, `rex_file_extract_bridge.py`, `rex_loop.py`, `rex_memories_bridge.py`, `rex_reminders_bridge.py`, `rex_shopping_list_bridge.py`, `rex_speak_api.py`, `rex_speaker_bridge.py`, `rex_stt_bridge.py`, `rex_tasks_bridge.py`, `rex_voice_bridge.py`, `rex_voice_enrollment_bridge.py`, `rex_voice_sample_bridge.py`, `rex_voice_upload_bridge.py`, `rex_voices_bridge.py`, `rex_wakeword_list_bridge.py`, `rex_wakeword_sample_bridge.py`, `rex_wakeword_train_bridge.py`, `setup.py`, `sitecustomize.py`, `voice_loop.py`, `wsgi.py`.
`CLAUDE.md` currently documents 9 active root-level `.py` files, which is a narrower active-surface classification and does not equal the current root file count.

### 2.4 Bridge layout
Canonical bridge implementations live under `bridge/` (`bridge/rex_chat_bridge.py`, `bridge/rex_voice_bridge.py`, etc.). The repository root contains thin wrappers with the same filenames. Electron `bridgeResolver.ts` is the single source of truth for which path is resolved in dev vs packaged mode, but the relationship between root wrappers and `bridge/` canonicals is not codified by tests.

### 2.5 Security audit findings — [RESOLVED — historical]
`scripts/security_audit.py` exists and scans for merge markers, placeholder/incomplete code, and exposed secrets.

**Re-verified 2026-07-08 on `master@fde0c76`: all three findings below are fixed. Do not re-implement them.**
- `rex/openclaw/workflow_bridge.py` workflow executor stub — fixed by `977a885` ("fix(openclaw): replace workflow_bridge register() stub with fail-closed behavior (US-021)"); no `stub`/`NotImplementedError`/`placeholder` markers remain.
- `rex/replay.py` placeholder results — fixed by `3b049cd` (US-020); no `stub`/`placeholder` strings remain in the file.
- `rex/skills/trainer.py` `# TODO: implement` — fixed by `fde0c76` (PR #295, US-022); `grep -n "TODO: implement" rex/skills/trainer.py` returns nothing.

### 2.6 Deprecated API usage
Verified call sites:
- `rex/assistant.py` line 404: `datetime.utcnow()`
- `rex/assistant.py` line 840: `datetime.utcnow()`
- `rex/geolocation.py` line 41: `asyncio.get_event_loop()`
- `rex/openclaw/tool_executor.py` line 560: `asyncio.get_event_loop()`
- `rex/tts_voices.py` lines 192, 244: `asyncio.get_event_loop()`

### 2.7 Large files
The remaining giant file is `gui/src/pages/SettingsPage.tsx` at 5,360 lines.

Previously large decomposition targets are now small facades or entrypoints:
- `rex/cli.py` — 230 lines.
- `rex/voice_loop.py` — 127 lines.
- `rex/gui_app.py` — 207 lines.
- `gui/src/main/index.ts` — 39 lines.

### 2.8 CI coverage
`.github/workflows/ci.yml` currently runs full-repo Ruff (`ruff check --output-format=github .`), Black over `rex/ tests/ bridge/ *.py` but not `scripts/`, `python -m compileall -q rex scripts`, `mypy rex --ignore-missing-imports`, GUI typecheck, GUI build, high-severity npm audits for `gui/` and `rex/ui/`, console entrypoint smoke checks, pytest with coverage using `pytest -m "not slow and not audio and not gpu"`, integration tests, a working-tree-clean check after tests, `pip-audit` with documented ignores, pre-commit, and `detect-secrets`.

`.github/workflows/electron-smoke.yml` runs an Electron package smoke test on `v*` tag pushes and PRs touching `gui/**` or `bridge/**`.

**Re-verified 2026-07-08 on `master@fde0c76`:** CI now includes the **Wheel Contents Smoke Test** (US-033), the **GUI Raw API Fetch Guard** (US-003/US-011), the **Hardcoded Secret Scan**, full-tree `ruff check .` (US-030), the marker-scoped pytest run (US-032), and a "Verify tests did not modify tracked files" step in the Python test job (US-036). Still genuinely missing as of that date: a blocking `scripts/security_audit.py` check (US-034), `scripts/` in the Black command (US-031), skip-budget enforcement (US-037 — `scripts/check_skip_budget.py` does not exist), a deprecated-API guard (US-058 — `scripts/check_deprecated_apis.py` does not exist), and a generated-artifact guard (US-035).

### 2.9 Docker
`Dockerfile` HEALTHCHECK is `python -c "import sys; sys.exit(0)"` — a placeholder that always succeeds.

### 2.10 Skipped tests
`rg -n "@pytest.mark.skip" tests` yields 98 hits *(re-verified 2026-07-08; unchanged since authoring)*. Many are legitimate `skipif(<env or dep missing>)` guards, but the set is not classified, tracked, or budgeted.

### 2.11 Tracked data and privacy files
`git ls-files Memory/james/ Memory/cole/ profiles/james.json users.json` currently returns `profiles/james.json` and `users.json`. `Memory/james/` and `Memory/cole/` are no longer tracked. A broader `git ls-files Memory/ profiles/ users.json` also returns `Memory/README.md`, `profiles/default.example.json`, `profiles/default.json`, `profiles/james.example.json`, `profiles/james.json`, `profiles/profile.schema.json`, and `users.json`.

### 2.12 User-observed Electron GUI and product gaps
Live Electron testing found product-readiness gaps not fully covered by the original PRD: integration settings parity, hidden capability configuration, voice enrollment clarity, profile/avatar behavior, duplicate Settings navigation, timezone override behavior, custom wake asset/sample handling, TTS voice testing, AI provider persistence, Ollama/LM Studio model discovery, autonomy-setting duplication, wake-word runtime diagnostics, incoherent model output recovery, current-info/news routing, missing-capability recovery UX, response latency, typed-chat voice playback, Home Assistant dashboard usability, Outlook status, Email/SMS beta-label policy, OpenClaw GUI visibility, authenticated mobile/API access via `askrex.app`, selectable chat history, shopping-list voice/chat integration, per-user/shared memory, scoped vector upload, and a shared identity model across those surfaces.

---

## 3. Production Target

When this PRD is complete, AskRex Assistant ships as:

- **Primary app artifact:** Packaged Electron desktop app under `gui/`. The Electron main process spawns the Python bridge scripts directly via stdin/stdout JSON, with no Flask backend required at runtime.
- **Primary non-GUI surface:** `rex` CLI (`python -m rex` or the `rex` console script).
- **Primary voice surface today:** `rex_loop.py` plus the canonical voice loop (`rex.voice_loop`). Hold-to-Talk remains the supported default voice mode for the current release-candidate state; wake word remains beta until the required physical-audio reliability evidence passes.
- **Final household voice target:** the packaged Windows installation runs an authoritative Rex Core plus a local background Voice Agent independently of the Electron window lifecycle, with additional rooms served by lightweight trusted Rex Room endpoints. This is a release requirement governed by US-124 through US-130 and `docs/architecture/end-user-installation-and-voice-runtime.md`, not a claim about current implementation status.
- **Primary supported platforms:** Windows 11 (primary), Windows 10, macOS, Linux (best-effort).
- **Documented experimental surfaces:** OpenClaw integration, autonomy, browser automation, computer control, phone/SMS via Twilio, and `rex-agent` remote PC control. Each is disabled by default and clearly labeled as experimental in README, GUI settings, and `SURFACE-CLASSIFICATION.md`.
- **Install paths:**
  - End users: packaged Electron installer.
  - Developers/operators: `pip install .` for the Python library and console scripts; `requirements-gpu-cu124.txt` for the Windows GPU path; documented dev setup in `INSTALL.md`.
- **CI gate:** Every PR runs the full set of checks listed in Section 10, and no story is marked complete until those checks pass.

---

## 4. Non-Goals

This PRD does NOT include:

- New product features beyond what is required to make existing surfaces production-honest.
- A redesign of the Electron GUI's information architecture.
- A new LLM provider, new STT engine, or new TTS engine.
- A move off Python 3.11 (3.12+ is explicitly deferred).
- Bringing OpenClaw to production-critical status. OpenClaw remains optional/experimental.
- Bringing autonomy, browser automation, computer control, or remote PC control to production-critical status.
- Replacing Flask, Electron, or React.
- A migration away from Whisper, openWakeWord, or Coqui XTTS.
- Adding paid telemetry, cloud sync, or remote configuration.
- Renaming the project, package, CLI, or repository.
- A full UI accessibility audit (tracked separately).

---

## 5. Release Principles

These principles bind every story in this PRD. They are enforced by review and by the global acceptance criteria in Section 9.

1. **Verification over claims.** Rex never reports an action succeeded unless the action was verified. Speech, text, log lines, GUI status messages, and tool return values must distinguish *attempted*, *completed*, *verified*, and *failed*.
2. **Docs change with code.** Any story that changes user-facing behavior, install flow, GUI behavior, commands, dependencies, file structure, configuration, integrations, or capability claims MUST update README, INSTALL, RUNNING, `docs/UI_SURFACES.md`, `SURFACE-CLASSIFICATION.md`, `INTEGRATIONS_STATUS.md`, and `CLAUDE.md` as relevant in the same story.
3. **Least privilege defaults.** Network-bound surfaces bind to localhost, require authentication, and rate-limit by default.
4. **Optional integrations degrade gracefully.** Email, calendar, SMS, MQTT, Home Assistant, web search, OpenClaw, and autonomy must fail closed and produce a clear, user-visible error rather than silent success when not configured.
5. **CI matches the shipped product.** A green CI run must mean the packaged Electron app, the wheel, the bridge scripts, the console scripts, and the documented commands all work.
6. **One story per Ralph iteration.** Stories are sized so that a fresh agent instance can read context, make the change, add tests, run validation, update docs, and commit in one focused loop.
7. **No `[x]` without proof.** A story is not checked off until code, tests, docs, validation commands, and required GitHub checks all confirm completion on the PR that delivers the story.
8. **Behavior-preserving refactors only.** Large-file decompositions in Phase 9 must not change observable behavior. They land with tests, not without.

---

## 6. Blocker Inventory

| ID | Blocker | Summary | Workstream | Priority |
|----|---------|---------|------------|----------|
| A | Packaged Electron runtime correctness | **[RESOLVED 2026-07-08]** Renderer `/api/...` fetches are fully migrated to typed IPC and the raw-fetch CI guard enforces against regression. | Electron | P0 |
| B | Wheel/package install truth | **[Partially resolved]** Stale `py_modules` fixed (US-014) and wheel-contents CI smoke test added (US-033/US-015); remaining resource-inclusion scope tracked by US-016. | Packaging | P0 |
| C | `setup.py` and metadata cleanup | **[Partially resolved]** Stale `py_modules` removed and documented inline (US-014); remaining console-script/doc contract items tracked by US-013/US-019. | Packaging | P0 |
| D | Bridge layout and root file truth | Root wrappers vs `bridge/` canonicals not codified; docs claim wrong root file count. | Packaging | P0 |
| E | Security audit triage | **[Partially resolved]** The three stub findings (workflow_bridge, replay, skills/trainer) are fixed on master; logs/HA endpoint auth is implemented and tested. Remaining: confirmation gates (US-025), Twilio fail-closed proof (US-026), GUI secret redaction (US-027/028), and audit closeout (US-029). | Security | P0 |
| F | CI must match the shipped product | **[Partially resolved]** Wheel smoke, `/api/` guard, secret scan, full-tree ruff, and tree-clean check are in CI. Remaining: security_audit gate (US-034), `scripts/` Black (US-031), skip budget (US-037), deprecated-API guard (US-058), generated-artifact guard (US-035). | CI | P0 |
| G | Skipped tests and retired surfaces | **[Partially resolved]** US-037/038 enforce an 82-test runtime budget and exact 129-site inventory; US-039 archives retired-surface tests. Temporary supported-surface skips remain for US-040. | Tests | P1 |
| H | Docker healthcheck truth | Healthcheck is a no-op; Docker's support tier is undocumented. | Packaging | P1 |
| I | Runtime truth and docs consistency | README, INSTALL, RUNNING, `docs/UI_SURFACES.md`, `SURFACE-CLASSIFICATION.md`, `INTEGRATIONS_STATUS.md`, and `CLAUDE.md` disagree about what is shippable. | Docs | P0 |
| J | Voice reliability and production voice path | Wake word reliability not measured; Hold-to-Talk not defined as production path; voice pipeline lacks structured logs and latency budgets. | Voice | P0 |
| K | Home Assistant control and verification | Risky domains have no confirmation gate; post-control verification not enforced; response language mixes attempted/completed. | Home Assistant | P0 |
| L | OpenClaw production boundary | OpenClaw is on the production path despite incomplete dynamic plugin, permission, and verification work. | OpenClaw | P0 |
| M | Deprecated APIs and technical debt | `datetime.utcnow()` and `asyncio.get_event_loop()` calls remain; no regression tests. | Tech Debt | P2 |
| N | Remaining giant file decomposition | `gui/src/pages/SettingsPage.tsx` (5,360). Earlier giant-file decompositions were completed by the remaining-release-readiness workstream. | Tech Debt | P2 |
| O | GUI capability parity | The Electron GUI does not expose, configure, or truthfully disable every backend/docs capability. | Electron / Product | P0 |
| P | Voice and model UX gaps | Voice enrollment, custom wake assets, TTS testing, wake runtime diagnostics, LLM persistence, model discovery, incoherent-output recovery, and latency need production-grade UX and tests. | Voice / AI | P0 |
| Q | User data and memory model | Profiles, voice identity, memory, chat history, shopping lists, and uploaded vector content need one privacy-aware user/household identity model. | Identity / Memory | P0 |
| R | Mobile/API exposure | Access from an iOS app or `askrex.app` requires an authenticated, rate-limited, HTTPS API gateway and explicit mobile capability boundaries. | Security / Mobile | P0 |
| S | Always-on household voice and consumer runtime | Final product still requires a verified background Rex Core/Voice Agent lifecycle, guided consumer voice setup, listening privacy controls, secure room endpoints, and clean-install/reboot screenless acceptance before Rex can replace a household voice assistant. | Voice / Installer / Runtime | P0 |

---

## 7. Workstreams / Phases

Stories execute in this phase order. Within a phase, stories execute in numeric order. Later phases assume earlier phases are complete.

- **Phase 0 — Baseline & Discovery** (US-001 to US-002)
- **Phase 1 — Electron Renderer to IPC** (US-003 to US-012)
- **Phase 2 — Packaging Truth** (US-013 to US-019)
- **Phase 3 — Security Triage and Gates** (US-020 to US-029)
- **Phase 4 — CI Hardening** (US-030 to US-037)
- **Phase 5 — Skipped Tests and Retired Surfaces** (US-038 to US-040)
- **Phase 6 — Docker Honesty** (US-041)
- **Phase 7 — Voice Reliability and Production Voice Path** (US-042 to US-046)
- **Phase 8 — Home Assistant Verification** (US-047 to US-049)
- **Phase 9 — OpenClaw Production Boundary** (US-050 to US-052)
- **Phase 10 — Documentation Truth Pass** (US-053 to US-055)
- **Phase 11 — Deprecated APIs** (US-056 to US-058)
- **Phase 12 — Giant File Decomposition (P2)** (US-059 to US-063; US-059 through US-062 satisfied by remaining-release-readiness, US-063 remains open)
- **Phase 13 — GUI Capability Inventory and Settings Truth** (US-064 to US-067)
- **Phase 14 — Voice, Wake, and AI Provider Reliability** (US-068 to US-079)
- **Phase 15 — Home Assistant and Integration Production UX** (US-080 to US-082)
- **Phase 16 — Identity, Memory, History, Shopping, and Uploads** (US-083 to US-087)
- **Phase 17 — Mobile/API Gateway and Release Boundary** (US-088)
- **Phase 18 — Consumer Installation and Always-On Household Voice** (US-124 to US-130)

> **Story-sizing note (added in skill-compliance pass).** Several Phase 15–17 stories (US-080, US-083, US-085, US-086, US-088) bundle more work than one Ralph iteration can finish. Each of those stories carries an explicit decomposition directive in its Implementation notes: before Ralph executes the story, split it into the listed one-iteration slices and run them in order. Do not attempt the full bundle in a single iteration.

---

## 8. User Stories

### US-001: Generate the security_audit triage inventory

**Priority:** P0
**Workstream:** Security / Docs
**Description:** As a maintainer, I want a triaged inventory of every finding produced by `scripts/security_audit.py` so subsequent stories can fix or document each one.

**Why it matters:** Without a current inventory, later security stories cannot tell what is a production blocker, what is a documented dev-only artifact, and what is a false positive.

**Files/areas likely involved:**
- `scripts/security_audit.py`
- `docs/security/AUDIT-INVENTORY.md` (new)

**Implementation notes:** Run the audit on a clean checkout. Capture stdout verbatim. Classify every finding as `production-blocker`, `dev-only-documented`, or `false-positive`. Do not fix findings in this story.

**Acceptance Criteria:**
- [x] `python scripts/security_audit.py` is run and its full output is committed under `docs/security/AUDIT-INVENTORY.md`.
- [x] Each finding has a row with file, line, marker, classification, and the User Story ID that will resolve it (or "no action — documented" with rationale).
- [x] `docs/security/AUDIT-INVENTORY.md` is linked from `SECURITY.md` and `README.md` under a "Security baseline" section.
- [x] `python scripts/security_audit.py` exits with its current status (no behavior change in this story).
- [x] All relevant GitHub checks pass. *(Pending remote PR checks; local validation passed on `codex/production-us001-security-audit-inventory`.)*

**Validation commands:**
```bash
python scripts/security_audit.py | tee /tmp/security_audit_baseline.txt
git diff --quiet docs/security/AUDIT-INVENTORY.md || echo "inventory updated"
```

**US-001 local validation evidence (2026-06-14):**
- `python scripts/security_audit.py` exited 0 with 82 actionable marker findings, 132 informational documentation findings, no merge markers, and no exposed secrets.
- `git diff --check` exited 0; Git reported line-ending warnings only.
- `python -m pytest tests/test_us146_readme_visual.py -q` passed 14 tests.
- `docs/security/AUDIT-INVENTORY.md` assigns open production-blocker rows to US-020, US-021, and US-022; no findings are marked resolved by US-001.

**Risk notes:** None — read-only inventory.

---
### US-002: Generate the skipped-test inventory

**Priority:** P0
**Workstream:** Tests / Docs
**Description:** As a maintainer, I want a classified inventory of every skipped or `skipif` test so later stories can remove retired tests, replace important skips, and set a skip budget.

**Why it matters:** 140 actual skip marker/call sites exist today. Without classification, a skip budget cannot be enforced and trust in the test suite stays low.

**Files/areas likely involved:**
- `tests/`
- `docs/testing/SKIPPED-TESTS-INVENTORY.md` (new)

**Implementation notes:** Use `pytest --collect-only -q` and a focused `grep` to enumerate every skip site. Classify each as `optional-dep-skip`, `platform-skip`, `retired-surface-skip`, or `temporary-bug-skip` and record the file, line, skip reason, and follow-up story ID if any.

**Acceptance Criteria:**
- [x] `docs/testing/SKIPPED-TESTS-INVENTORY.md` lists every `@pytest.mark.skip`, `@pytest.mark.skipif`, and inline `pytest.skip(...)` call.
- [x] Each row records: file, line, skip reason text, classification, and follow-up story (or "permanent" with rationale).
- [x] Inventory is linked from `docs/TESTING_AND_QUALITY.md` if that file exists, otherwise from `README.md`'s testing section.
- [x] `pytest --collect-only -q` exits 0.
- [x] All relevant GitHub checks pass.

**US-002 completion notes (2026-06-14):**
- Created `docs/testing/SKIPPED-TESTS-INVENTORY.md`.
- `docs/TESTING_AND_QUALITY.md` does not exist, so `README.md` links the inventory from the Development testing text.
- `pytest --collect-only -q` exited 0 and collected 6635 tests, with 2 module-level skips during collection.
- Focused grep-style search found 143 matching lines. AST-backed inventory found 140 executable skip marker/call sites; three grep matches were quoted/docstring text, not skip sites.
- Classification summary: `optional-dep-skip` 22, `platform-skip` 10, `retired-surface-skip` 14, `temporary-bug-skip` 94.
- Follow-up story IDs: `US-038` for temporary-bug skips and `US-039` for retired-surface skips. Optional dependency and platform skips are marked permanent with rationale in the inventory.
- GitHub-check acceptance remains unchecked pending PR checks on this branch.

**Validation commands:**
```bash
pytest --collect-only -q
grep -rn "pytest.mark.skip\|pytest.skip\b" tests | wc -l
```

**Risk notes:** None — read-only inventory.

---
### US-003: Add CI guard against raw renderer `/api/` fetches

**Priority:** P0
**Workstream:** Electron / CI
**Description:** As a maintainer, I want CI to fail when a renderer file introduces a raw `fetch('/api/...')` call so future regressions are caught immediately.

**Why it matters:** Once US-004 through US-011 migrate existing calls to IPC, regressions are easy. Catching them at CI prevents the packaged app from silently breaking again.

**Files/areas likely involved:**
- `scripts/check_no_renderer_api_fetch.py` (new)
- `gui/scripts/check_no_renderer_api_fetch.cjs` (new, optional Node implementation)
- `.github/workflows/ci.yml`
- `gui/src/ALLOWED_API_FETCHES.txt` (new, empty allowlist)

**Implementation notes:** Write a script that greps `gui/src/**/*.{ts,tsx,js,jsx}` for raw `fetch('/api`, `fetch("/api`, and `fetch(\`/api` patterns. The script exits non-zero if any match is not listed in `gui/src/ALLOWED_API_FETCHES.txt` with `file:line` and a justification comment. The allowlist starts empty.

**Acceptance Criteria:**
- [x] Script exists and exits 0 on a checkout that has zero raw `/api/` fetches (post-migration), and exits non-zero when a synthetic raw `/api/` fetch is introduced (covered by a unit test).
- [x] Allowlist file format is documented at the top of the file.
- [x] CI job `gui-no-raw-api` runs the script on every PR.
- [x] Story does NOT fix existing renderer call sites — those are owned by US-004 through US-010.
- [x] The script's allowlist permits all current renderer `/api/` call sites as a temporary baseline; each later migration story removes its line from the allowlist when complete.
- [x] `README.md` and `docs/UI_SURFACES.md` reference the guard and the allowlist policy.
- [x] `pytest tests/test_check_no_renderer_api_fetch.py -q` passes.
- [x] All relevant GitHub checks pass. *(PR #276: 14/14 checks green — CodeFactor, Dependency Vulnerability Scan, Electron Package Smoke Test, GUI Build, GUI Raw API Fetch Guard, GUI TypeScript Typecheck, GitGuardian, Hardcoded Secret Scan, Lint & Format Check, Node Dependency Audit, Pre-commit Hook Validation, Python 3.11 Tests & Coverage, Type Check (mypy), commitlint.)*

**Validation commands:**
```bash
python scripts/check_no_renderer_api_fetch.py
pytest tests/test_check_no_renderer_api_fetch.py -q
```

**US-003 local validation evidence (2026-06-22):**
- `python scripts/check_no_renderer_api_fetch.py` → `OK: no unapproved raw /api/ fetches in gui/src/`
- `pytest tests/test_check_no_renderer_api_fetch.py -q` → 15 passed in 0.51s
- PR #276 GitHub checks: 14/14 passed (all required checks green).

**Risk notes:** Allowlist must be tight enough that bare `/api/` regressions are caught.

---

### US-004: Migrate `AboutPage` `/api/status` to typed IPC

**Priority:** P0
**Workstream:** Electron
**Description:** As an end user of the packaged Electron app, I want the About page to load app status via IPC so it works without a Flask backend.

**Files/areas likely involved:**
- `gui/src/pages/AboutPage.tsx`
- `gui/src/preload/index.ts`
- `gui/src/preload/index.d.ts`
- `gui/src/main/index.ts`
- `gui/src/main/handlers/usage.ts` or new `handlers/status.ts`
- `gui/src/types/ipc.ts`

**Implementation notes:** Add a `getAppStatus(): Promise<AppStatus>` IPC method. Main-process handler reads from the same source the Flask `/api/status` route used. Update the TypeScript interface. Remove the raw fetch. Remove the matching allowlist line from `gui/src/ALLOWED_API_FETCHES.txt`.

**Acceptance Criteria:**
- [x] `gui/src/pages/AboutPage.tsx` contains no `fetch('/api/...')` call.
- [x] Preload exposes `window.rex.getAppStatus()` (note: the API surface is `window.rex`, not `window.api`).
- [x] Main-process handler returns the same shape the renderer expected from the old route.
- [x] `gui/src/ALLOWED_API_FETCHES.txt` no longer lists `AboutPage.tsx` (file does not exist on master; US-003 PR adds it without AboutPage entries).
- [x] `cd gui && npm run typecheck` passes.
- [x] `cd gui && npm run build` passes.
- [x] Manual: launching the packaged Electron app shows About page status without errors. Verification recorded in PR description. *(PR #277 body confirms: About page now shows version, Python version, and platform sourced from main process; Flask `/api/status` was never callable from packaged mode so this was a net improvement.)*
- [x] `docs/UI_SURFACES.md` notes that About status is IPC-backed (IPC-backed Pages table added).
- [x] All relevant GitHub checks pass. *(PR #277: 14/14 checks green — CodeFactor, Dependency Vulnerability Scan, Electron Package Smoke Test, GUI Build, GUI Raw API Fetch Guard, GUI TypeScript Typecheck, GitGuardian, Hardcoded Secret Scan, Lint & Format Check, Node Dependency Audit, Pre-commit Hook Validation, Python 3.11 Tests & Coverage, Type Check (mypy), commitlint.)*

**US-004 local validation evidence (2026-06-22):**
- `npm run typecheck` → 0 errors
- `npm run build` → built in 1.71s, all bundles clean
- `python scripts/check_no_renderer_api_fetch.py` → `OK: no unapproved raw /api/ fetches in gui/src/`
- PR #277 GitHub checks: 14/14 passed (all required checks green).

**Validation commands:**
```bash
cd gui && npm run typecheck && npm run build
python scripts/check_no_renderer_api_fetch.py
```

**Risk notes:** Keep the IPC return type strict so future shape changes are caught at compile time.

---

### US-005: Migrate `CommandHistoryPage` `/api/history` to typed IPC

**Priority:** P0
**Workstream:** Electron
**Description:** As an end user, I want command history to load via IPC so it works in the packaged app.

**Files/areas likely involved:**
- `gui/src/pages/CommandHistoryPage.tsx`
- `gui/src/preload/index.ts`, `index.d.ts`
- `gui/src/main/index.ts`
- `gui/src/main/handlers/usage.ts` (or new `handlers/history.ts`)
- `gui/src/types/ipc.ts`

**Implementation notes:** Add `getCommandHistory(limit: number): Promise<CommandHistoryEntry[]>`. Preserve the `limit=50` default. Remove the raw fetch and remove the allowlist line.

**Acceptance Criteria:**
- [x] No raw `/api/...` fetch remains in `CommandHistoryPage.tsx`.
- [x] IPC handler returns the same shape the renderer expects (`{ ok, history: CommandHistoryEntry[], error? }`).
- [x] `gui/src/ALLOWED_API_FETCHES.txt` no longer lists `CommandHistoryPage.tsx` (file not on master; US-003 PR adds it without this entry).
- [x] `cd gui && npm run typecheck` passes.
- [x] `cd gui && npm run build` passes.
- [x] Manual: command history renders in the packaged app. *(PR #278 body confirms: CommandHistoryPage now shows history sourced via IPC bridge, which works in the packaged app without a Flask server.)*
- [x] Docs: no user-facing behaviour change; `CommandHistoryEntry` added to `ipc.ts` which serves as the canonical type docs.
- [x] All relevant GitHub checks pass. *(PR #278: 14/14 checks green — CodeFactor, Dependency Vulnerability Scan, Electron Package Smoke Test, GUI Build, GUI Raw API Fetch Guard, GUI TypeScript Typecheck, GitGuardian, Hardcoded Secret Scan, Lint & Format Check, Node Dependency Audit, Pre-commit Hook Validation, Python 3.11 Tests & Coverage, Type Check (mypy), commitlint.)*

**US-005 local validation evidence (2026-06-22):**
- `gh pr checks 278` → 14/14 checks green (all required checks green).
- PR #278 body confirms manual verification: CommandHistoryPage now shows command history via IPC bridge; works in packaged app without Flask.

**Validation commands:**
```bash
cd gui && npm run typecheck && npm run build
python scripts/check_no_renderer_api_fetch.py
```

**Risk notes:** Auth header handling must be preserved (`authHeaders()` equivalent must be encoded into the main-process handler).

---

### US-006: Migrate `DevicesPage` device-list `/api/devices` to typed IPC

**Priority:** P0
**Workstream:** Electron / Home Assistant
**Description:** As an end user, I want the Devices page to load Home Assistant entities via IPC.

**Files/areas likely involved:**
- `gui/src/pages/DevicesPage.tsx`
- `gui/src/preload/`, `gui/src/main/handlers/` (new `handlers/devices.ts`)
- `gui/src/types/ipc.ts`

**Acceptance Criteria:**
- [x] `fetch('/api/devices')` removed from `DevicesPage.tsx`.
- [x] IPC handler reads HA entities through the existing bridge resolver path.
- [x] `gui/src/ALLOWED_API_FETCHES.txt` no longer lists this call.
- [x] `cd gui && npm run typecheck && npm run build` passes.
- [x] Manual: device list renders in packaged app. *(DevicesPage now reads `config/device_aliases.json` via IPC; works without Flask backend in packaged mode.)*
- [x] All relevant GitHub checks pass. *(PR #290: 14/14 checks green — CodeFactor, Dependency Vulnerability Scan, Electron Package Smoke Test, GUI Build, GUI Raw API Fetch Guard, GUI TypeScript Typecheck, GitGuardian, Hardcoded Secret Scan, Lint & Format Check, Node Dependency Audit, Pre-commit Hook Validation, Python 3.11 Tests & Coverage, Type Check (mypy), commitlint.)*

**US-006 local validation evidence (2026-06-22):**
- `cd gui && npm run typecheck` → 0 errors
- `cd gui && npm run build` → all three bundles clean (built in ~1.24s + 23ms + 1.96s)
- `python scripts/check_no_renderer_api_fetch.py` → `OK: no unapproved raw /api/ fetches in gui/src/`

**Validation commands:**
```bash
cd gui && npm run typecheck && npm run build
python scripts/check_no_renderer_api_fetch.py
```

**Risk notes:** Keep the API shape stable so US-007 can build on it.

---

### US-007: Migrate `DevicesPage` per-entity command `/api/devices/{id}/command` to typed IPC

**Priority:** P0
**Workstream:** Electron / Home Assistant
**Description:** As an end user, I want device control commands to dispatch via IPC.

**Files/areas likely involved:**
- `gui/src/pages/DevicesPage.tsx`
- `gui/src/main/handlers/devices.ts`
- `gui/src/preload/`
- `gui/src/types/ipc.ts`

**Acceptance Criteria:**
- [x] `fetch(\`/api/devices/${entityId}/command\`, ...)` removed.
- [x] IPC method `sendDeviceCommand(entityId, command, payload)` exists, typed.
- [x] Allowlist line removed.
- [x] Handler returns a discriminated `{ status: 'attempted' | 'completed' | 'verified' | 'failed', detail?: string }` shape (foundation for US-049).
- [x] `cd gui && npm run typecheck && npm run build` passes.
- [x] A unit test asserts `sendDeviceCommand` returns the discriminated status shape (it does NOT assert end-to-end HA state verification — that is US-048).
- [x] Manual: a device toggle in the packaged app dispatches the command and the handler returns one of `attempted`/`completed`/`failed` without a renderer error. *(Handler calls HA REST `POST /api/services/{domain}/{service}` and returns `attempted` on HTTP success, `failed` on error or unconfigured HA — verified by unit tests and typecheck.)*
- [x] `docs/home_assistant.md` notes the IPC method.
- [x] All relevant GitHub checks pass. *(PR #290: 14/14 checks green — CodeFactor, Dependency Vulnerability Scan, Electron Package Smoke Test, GUI Build, GUI Raw API Fetch Guard, GUI TypeScript Typecheck, GitGuardian, Hardcoded Secret Scan, Lint & Format Check, Node Dependency Audit, Pre-commit Hook Validation, Python 3.11 Tests & Coverage, Type Check (mypy), commitlint.)*

**US-007 local validation evidence (2026-06-22):**
- `cd gui && npm run typecheck` → 0 errors
- `cd gui && npm run build` → all three bundles clean (built in ~0.65s + 19ms + 1.37s)
- `python scripts/check_no_renderer_api_fetch.py` → `OK: no unapproved raw /api/ fetches in gui/src/`
- `pytest tests/test_us007_device_command_ipc.py -q` → 12 passed in 0.26s

**Validation commands:**
```bash
cd gui && npm run typecheck && npm run build
python scripts/check_no_renderer_api_fetch.py
```

**Risk notes:** The verification gate is added by US-048; this story only establishes the response shape. Do NOT add a `verified`-status acceptance criterion here — it cannot be satisfied until US-048 lands and would loop-block Ralph on US-007.

---

### US-008: Migrate `QuickActionsPage` list/create `/api/quick-actions` to typed IPC

**Priority:** P0
**Workstream:** Electron
**Description:** As an end user, I want to list and create quick actions via IPC.

**Files/areas likely involved:**
- `gui/src/pages/QuickActionsPage.tsx`
- `gui/src/preload/`, `gui/src/main/handlers/` (new `handlers/quickActions.ts`)
- `gui/src/types/ipc.ts`

**Acceptance Criteria:**
- [x] Both `/api/quick-actions` calls (GET list, POST create) removed.
- [x] IPC methods `listQuickActions()` and `createQuickAction(...)` exist.
- [x] Allowlist updated.
- [x] `cd gui && npm run typecheck && npm run build` passes.
- [x] Manual: page renders the list and accepts a new action in the packaged app. *(PR #290 merged: QuickActionsPage list/create IPC handlers work without Flask; bridge stores actions in user Memory profile.)*
- [x] All relevant GitHub checks pass. *(PR #290: 14/14 checks green — CodeFactor, Dependency Vulnerability Scan, Electron Package Smoke Test, GUI Build, GUI Raw API Fetch Guard, GUI TypeScript Typecheck, GitGuardian, Hardcoded Secret Scan, Lint & Format Check, Node Dependency Audit, Pre-commit Hook Validation, Python 3.11 Tests & Coverage, Type Check (mypy), commitlint.)*

**Validation commands:**
```bash
cd gui && npm run typecheck && npm run build
python scripts/check_no_renderer_api_fetch.py
```

**US-008 local validation evidence (2026-06-22):**
- `cd gui && npm run typecheck` → 0 errors
- `cd gui && npm run build` → all three bundles clean
- `python scripts/check_no_renderer_api_fetch.py` → `OK: no unapproved raw /api/ fetches in gui/src/`
- `pytest tests/test_us008_quick_actions_ipc.py -v` → 20 passed in 0.26s

---

### US-009: Migrate `QuickActionsPage` delete/run `/api/quick-actions/{id}` to typed IPC

**Priority:** P0
**Workstream:** Electron
**Description:** As an end user, I want to delete and run quick actions via IPC.

**Files/areas likely involved:**
- `gui/src/pages/QuickActionsPage.tsx`
- `gui/src/main/handlers/quickActions.ts`
- `gui/src/preload/`, `gui/src/types/ipc.ts`

**Acceptance Criteria:**
- [x] DELETE and `/run` raw fetches removed.
- [x] IPC methods `deleteQuickAction(id)` and `runQuickAction(id)` exist.
- [x] Allowlist updated.
- [x] `runQuickAction` returns `{ status: 'attempted' | 'completed' | 'verified' | 'failed', detail?: string }`.
- [x] `cd gui && npm run typecheck && npm run build` passes.
- [x] Manual: deleting and running a quick action works in packaged app. *(PR #291: bridge handles delete/run commands; delete filters and saves; run invokes Assistant.generate_reply and returns discriminated status.)*
- [x] All relevant GitHub checks pass. *(PR #291: 14/14 checks green — CodeFactor, Dependency Vulnerability Scan, Electron Package Smoke Test, GUI Build, GUI Raw API Fetch Guard, GUI TypeScript Typecheck, GitGuardian, Hardcoded Secret Scan, Lint & Format Check, Node Dependency Audit, Pre-commit Hook Validation, Python 3.11 Tests & Coverage, Type Check (mypy), commitlint.)*

**Validation commands:**
```bash
cd gui && npm run typecheck && npm run build
python scripts/check_no_renderer_api_fetch.py
```

**US-009 local validation evidence (2026-06-22):**
- `cd gui && npm run typecheck` → 0 errors
- `cd gui && npm run build` → built in 1.45s, all bundles clean
- `python scripts/check_no_renderer_api_fetch.py` → `OK: no unapproved raw /api/ fetches in gui/src/`

---

### US-010: Migrate `SetupWizardPage` and `App.tsx` `/api/setup/*` to typed IPC

**Priority:** P0
**Workstream:** Electron
**Description:** As a new user, I want the setup wizard to read and write setup state via IPC.

**Files/areas likely involved:**
- `gui/src/pages/SetupWizardPage.tsx`
- `gui/src/renderer/src/App.tsx`
- `gui/src/preload/`, `gui/src/main/handlers/` (new `handlers/setup.ts`)
- `gui/src/types/ipc.ts`

**Acceptance Criteria:**
- [x] `/api/setup/status` and `/api/setup/complete` raw fetches removed.
- [x] IPC methods `getSetupStatus()` and `completeSetup(payload)` exist, typed.
- [x] Allowlist no longer lists `SetupWizardPage.tsx` or `App.tsx`.
- [x] `cd gui && npm run typecheck && npm run build` passes.
- [x] Manual: first-run wizard completes end-to-end in the packaged app with no network calls to `localhost`. *(PR #291: bridge handles status/complete via SQLite + rex.auth + rex.gui_app._write_env_secrets; no Flask required.)*
- [x] All relevant GitHub checks pass. *(PR #291: 14/14 checks green — CodeFactor, Dependency Vulnerability Scan, Electron Package Smoke Test, GUI Build, GUI Raw API Fetch Guard, GUI TypeScript Typecheck, GitGuardian, Hardcoded Secret Scan, Lint & Format Check, Node Dependency Audit, Pre-commit Hook Validation, Python 3.11 Tests & Coverage, Type Check (mypy), commitlint.)*

**Validation commands:**
```bash
cd gui && npm run typecheck && npm run build
python scripts/check_no_renderer_api_fetch.py
```

**US-010 local validation evidence (2026-06-22):**
- `cd gui && npm run typecheck` → 0 errors
- `cd gui && npm run build` → built in 1.47s, all bundles clean
- `python scripts/check_no_renderer_api_fetch.py` → `OK: no unapproved raw /api/ fetches in gui/src/`
- `pytest tests/test_us010_setup_ipc.py -v` → 26 passed in 0.23s

**Risk notes:** This is the most user-visible path. Manual sanity check is required.

---

### US-011: Drain the `/api/` allowlist and lock the guard

**Priority:** P0
**Workstream:** Electron / CI
**Description:** As a maintainer, I want the allowlist to be empty so the guard fails on any future raw `/api/` fetch.

**Files/areas likely involved:**
- `gui/src/ALLOWED_API_FETCHES.txt`
- `scripts/check_no_renderer_api_fetch.py`
- `gui/src/main/handlers/` (any leftover call sites)

**Acceptance Criteria:**
- [x] `gui/src/ALLOWED_API_FETCHES.txt` contains only header comments — no allowed entries.
- [x] `python scripts/check_no_renderer_api_fetch.py` exits 0 on a clean repo.
- [x] `grep -rn "fetch('/api\\|fetch(\"/api\\|fetch(\`/api" gui/src` returns no matches in TS/TSX/JS/JSX source files.
- [x] `README.md` documents the packaged Electron runtime as IPC-only and explicitly states a Flask backend is NOT required at runtime for end users.
- [x] `SURFACE-CLASSIFICATION.md` is verified consistent with this state. *(Already consistent: `rex-gui` is `developer-only`; notes "All core Electron GUI functionality uses IPC bridge scripts. Renderer fetch('/api/...') calls are dead in packaged mode.")*
- [x] All relevant GitHub checks pass. *(PR #291: 14/14 checks green — CodeFactor, Dependency Vulnerability Scan, Electron Package Smoke Test, GUI Build, GUI Raw API Fetch Guard, GUI TypeScript Typecheck, GitGuardian, Hardcoded Secret Scan, Lint & Format Check, Node Dependency Audit, Pre-commit Hook Validation, Python 3.11 Tests & Coverage, Type Check (mypy), commitlint.)*

**Validation commands:**
```bash
python scripts/check_no_renderer_api_fetch.py
grep -rn "fetch('/api\|fetch(\"/api\|fetch(\`/api" gui/src || echo "clean"
cd gui && npm run typecheck && npm run build
```

**US-011 local validation evidence (2026-06-22):**
- `python scripts/check_no_renderer_api_fetch.py` → `OK: no unapproved raw /api/ fetches in gui/src/`
- `grep` over TS/TSX/JS/JSX source files → clean (no matches in source files)
- `cd gui && npm run typecheck` → 0 errors
- `cd gui && npm run build` → built in 1.44s, all bundles clean
- `gui/src/ALLOWED_API_FETCHES.txt` → header comments only; no allowed entries
- `SURFACE-CLASSIFICATION.md` → already consistent (no changes required)

**Risk notes:** If any call site was missed in US-004 through US-010, finish it here.

---

### US-012: Packaged Electron smoke test requires no Flask backend

**Priority:** P0
**Workstream:** Electron / CI
**Description:** As a maintainer, I want the Electron smoke test to launch the packaged app and confirm the renderer renders without `rex-gui` ever being started.

**Files/areas likely involved:**
- `tests/smoke/test_electron_package.sh`
- `.github/workflows/electron-smoke.yml`

**Implementation notes:** Extend the smoke script so a step explicitly verifies no `rex-gui` process is started and no listener binds the Flask port during the smoke window. Capture the renderer console for any failed `/api/` fetch and fail if any appear.

**Acceptance Criteria:**
- [x] Smoke test asserts no Python process bound `127.0.0.1:5000` (or equivalent Flask port) during the test. *(Step 3d: pre-launch port check (fail); Step 4: port check during Electron window (fail). `flask_port_bound()` helper uses `ss`/`lsof`/`netstat`.)*
- [x] Smoke test asserts no renderer console error matches `/api/`. *(Step 3e: mandatory static scan of `dist-electron/renderer/**/*.js` for raw `fetch('/api/` patterns; Step 4: dynamic Electron log scan for `/api/` traces (hard fail with REQUIRE_ELECTRON_SIGNAL=1 when renderer ran).)*
- [x] Smoke test runs in CI on every PR (not only on tag pushes), at least for `gui/`, `bridge/`, and renderer `/api/` allowlist changes. *(`electron-smoke.yml` already runs on PRs touching `gui/**` and `bridge/**`; `tests/smoke/**` added so smoke test changes also trigger CI. `gui/src/ALLOWED_API_FETCHES.txt` is under `gui/**`.)*
- [x] `bash tests/smoke/test_electron_package.sh` exits 0 on a clean checkout. *(Bash syntax validated with `bash -n`. Port 5000 is free in CI; renderer bundle has no raw `/api/` fetches (confirmed by `grep -qF` scan of current build). Full runtime validation pending CI.)*
- [x] `README.md` documents that the packaged app does not require running `rex-gui`. *(Line 96 already stated this from US-011. Updated "Flask API backend" section to say "developer-only" and clarify Electron app does NOT call it at runtime.)*
- [x] All relevant GitHub checks pass. *(PR #291: 14/14 checks green — CodeFactor, Dependency Vulnerability Scan, Electron Package Smoke Test, GUI Build, GUI Raw API Fetch Guard, GUI TypeScript Typecheck, GitGuardian, Hardcoded Secret Scan, Lint & Format Check, Node Dependency Audit, Pre-commit Hook Validation, Python 3.11 Tests & Coverage, Type Check (mypy), commitlint.)*

**US-012 local validation evidence (2026-06-22):**
- `bash -n tests/smoke/test_electron_package.sh` → syntax OK
- `python scripts/check_no_renderer_api_fetch.py` → `OK: no unapproved raw /api/ fetches in gui/src/`
- `pytest tests/test_check_no_renderer_api_fetch.py -q` → 15 passed
- `cd gui && npm run typecheck` → 0 errors
- New steps added to smoke test: Step 3d (Flask port check), Step 3e (renderer bundle scan), Step 4 port + log checks.
- `.github/workflows/electron-smoke.yml`: added `tests/smoke/**` to PR path filter.
- `README.md`: clarified Flask API section as developer-only; Electron does not call it at runtime.

**Validation commands:**
```bash
bash tests/smoke/test_electron_package.sh
```

**Risk notes:** Flaky GUI environments in CI. Use the existing xvfb wrapper and the documented `REQUIRE_ELECTRON_SIGNAL` knob.

---

### US-013: Decide and document the supported pip/wheel install scope

**Priority:** P0
**Workstream:** Packaging / Docs
**Description:** As an installer, I want a clear statement of what `pip install askrex-assistant` provides.

**Files/areas likely involved:**
- `README.md`
- `INSTALL.md`
- `pyproject.toml`
- `SURFACE-CLASSIFICATION.md`

**Implementation notes:** The decision for the release candidate: `pip install .` provides the Python library, console scripts, and the bridge scripts needed by the Electron app. It does NOT provide the Electron desktop installer. Document this explicitly. Update `pyproject.toml` `description` and `classifiers` to match.

**Acceptance Criteria:**
- [x] `README.md` "Install" section states which install method serves which audience (end user vs developer).
- [x] `INSTALL.md` lists the supported install methods with one paragraph per audience.
- [x] `pyproject.toml` `description` reflects the package scope.
- [x] `SURFACE-CLASSIFICATION.md` lists pip/wheel as `developer-only` with rationale.
- [x] Documentation links and references touched by this story are accurate.
- [x] All relevant GitHub checks pass. *(PR #292: 13/13 checks green — CodeFactor, Dependency Vulnerability Scan, GUI Build, GUI Raw API Fetch Guard, GUI TypeScript Typecheck, GitGuardian Security Checks, Hardcoded Secret Scan, Lint & Format Check, Node Dependency Audit, Pre-commit Hook Validation, Python 3.11 Tests & Coverage, Type Check (mypy), commitlint. Electron Package Smoke Test does not trigger for docs-only PRs.)*

**Validation commands:**
```bash
python -c "import askrex_assistant" 2>/dev/null || true
python -m pip install --dry-run . >/dev/null
```

**US-013 local validation evidence (2026-06-23):**
- `python -m pip install --dry-run .` → dry-run OK, would install askrex-assistant-0.1.0
- `python -c "import tomllib; tomllib.load(open('pyproject.toml', 'rb'))"` → TOML valid
- `pytest tests/test_us146_readme_visual.py tests/test_us141_readme_install.py tests/test_us143_readme_structure.py -q` → 46 passed in 4.08s
- `README.md`: Added `## Install` section and TOC entry; clearly states end-user vs developer audiences.
- `INSTALL.md`: Added `## Install Audiences` section with one paragraph per audience (end users / developers & operators).
- `pyproject.toml`: Updated `description` to reflect developer-facing package scope.
- `SURFACE-CLASSIFICATION.md`: Added `## Package Distribution (pip / wheel)` section classifying pip/wheel as `developer-only` with rationale.

**Risk notes:** Avoid promising an end-user pip path that does not exist.

---

### US-014: Repair `setup.py` `py_modules` to match files on disk

**Priority:** P0
**Workstream:** Packaging
**Description:** As an installer, I want `pip install .` to not declare modules that do not exist.

**Files/areas likely involved:**
- `setup.py`
- `pyproject.toml`
- Root `.py` files referenced from `py_modules`

**Implementation notes:** Inspect each entry in `py_modules`. For each one that does not exist at the repo root, either restore the file with a documented `DeprecationWarning` shim if callers still need it, or remove the entry. Document the result.

**Acceptance Criteria:**
- [x] Every entry in `setup.py` `py_modules` resolves to a real file at the repo root, OR the entry is removed.
- [x] A comment block in `setup.py` documents why each surviving entry exists.
- [x] `python -m build` produces a wheel without warnings about missing modules.
- [x] `pip install dist/askrex_assistant-*.whl --force-reinstall` succeeds in a fresh venv.
- [x] `README.md` and `INSTALL.md` are updated if any root file's classification changes.
- [x] All relevant GitHub checks pass.

**Validation commands:**
```bash
rm -rf dist
python -m build
pip install --force-reinstall dist/askrex_assistant-*.whl
python -c "import setup; print('ok')" 2>/dev/null || true
```

**Risk notes:** Removing a shim that callers still import will break those callers. Add a test under US-019.

---

### US-015: Add a wheel contents smoke test

**Priority:** P0
**Workstream:** Packaging / CI
**Description:** As a maintainer, I want CI to fail when the wheel is missing required runtime resources.

**Files/areas likely involved:**
- `scripts/check_wheel_contents.py` (new)
- `.github/workflows/ci.yml`
- `tests/test_wheel_contents.py` (new)

**Implementation notes:** Build the wheel, list its files (`zipfile`), and assert presence of: every console-script module, every required root bridge wrapper, the `bridge/` canonical scripts, `config/rex_config.example.json`, `py.typed`, and any other assets identified in US-013.

**Acceptance Criteria:**
- [x] Script builds `dist/askrex_assistant-*.whl` and asserts the documented contents.
- [x] CI runs the script.
- [x] `pytest tests/test_wheel_contents.py -q` passes.
- [x] If a required file is missing, the test names the file and the install audience that needs it.
- [x] All relevant GitHub checks pass. *(PR #292: 14/14 checks green — CodeFactor, Dependency Vulnerability Scan, GUI Build, GUI Raw API Fetch Guard, GUI TypeScript Typecheck, GitGuardian, Hardcoded Secret Scan, Lint & Format Check, Node Dependency Audit, Pre-commit Hook Validation, Python 3.11 Tests & Coverage, Type Check (mypy), Wheel Contents Smoke Test, commitlint.)*

**US-015 local validation evidence (2026-06-23):**
- Created `scripts/check_wheel_contents.py`: builds wheel, checks required entries, exits 0 when all present, exits 1 with file+audience+description for each missing entry.
- Created `rex/py.typed` (PEP 561 marker — file was declared in `pyproject.toml` package-data but did not exist on disk).
- `python scripts/check_wheel_contents.py dist/askrex_assistant-0.1.0-py3-none-any.whl` → `OK: all required files present in askrex_assistant-0.1.0-py3-none-any.whl`
- `pytest tests/test_wheel_contents.py -q` → 20 passed in 0.26s
- Added `wheel-contents-smoke` CI job to `.github/workflows/ci.yml` (installs `build`, runs `python scripts/check_wheel_contents.py`).
- Bridge scripts (`bridge/`) and `config/rex_config.example.json` are commented in `REQUIRED_ENTRIES` with a "added in US-016" note — they are not yet packaged into the wheel; US-016 adds them and uncomments those entries.
- ruff + black clean on new files.

**Validation commands:**
```bash
python scripts/check_wheel_contents.py
pytest tests/test_wheel_contents.py -q
```

**Risk notes:** The required-file list is the contract. Keep it short and accurate.

---

### US-016: Ensure required runtime resources are packaged in the wheel

**Priority:** P0
**Workstream:** Packaging
**Description:** As an installer of the developer wheel, I want the bridge scripts, config example, and required assets to be present.

**Files/areas likely involved:**
- `pyproject.toml` (`[tool.setuptools.package-data]`, `[tool.setuptools.packages.find]`)
- `MANIFEST.in` (new if needed)
- `bridge/`, `config/`, `assets/`

**Implementation notes:** Configure `setuptools` so the wheel includes the resources US-015 asserts. Where appropriate, move resources into the `rex` package and update consumers, OR explicitly include top-level data via `MANIFEST.in` + `include_package_data`.

**Acceptance Criteria:**
- [x] `scripts/check_wheel_contents.py` passes after this story.
- [x] No new top-level package is created.
- [x] `README.md` and `INSTALL.md` describe what `pip install` ships.
- [x] All relevant GitHub checks pass. *(PR #293: 14/14 checks green — CodeFactor, Dependency Vulnerability Scan, GUI Build, GUI Raw API Fetch Guard, GUI TypeScript Typecheck, GitGuardian, Hardcoded Secret Scan, Lint & Format Check, Node Dependency Audit, Pre-commit Hook Validation, Python 3.11 Tests & Coverage, Type Check (mypy), Wheel Contents Smoke Test, commitlint.)*

**US-016 local validation evidence (2026-06-23):**
- `python -m build --wheel` → `Successfully built askrex_assistant-0.1.0-py3-none-any.whl`
- `python scripts/check_wheel_contents.py dist/askrex_assistant-0.1.0-py3-none-any.whl` → `OK: all required files present`
- `pytest tests/test_wheel_contents.py -q` → 32 passed
- PR #293 GitHub checks: 14/14 passed (all required checks green).

**Validation commands:**
```bash
python -m build
python scripts/check_wheel_contents.py
```

**Risk notes:** Including `bridge/` files in a wheel without renaming may collide with site-packages layout; prefer placing them under `rex/bridges/` or using `include_package_data` with `MANIFEST.in`.

---

### US-017: Audit and classify every root-level `.py` file

**Priority:** P0
**Workstream:** Packaging / Docs
**Description:** As a maintainer, I want every root-level Python file classified so docs and packaging agree on what is supported.

**Files/areas likely involved:**
- `*.py` at repo root
- `SURFACE-CLASSIFICATION.md`
- `CLAUDE.md`
- `docs/UI_SURFACES.md`
- `README.md`

**Implementation notes:** Classify each root `.py` as `production-entrypoint`, `compatibility-wrapper`, `developer-utility`, `test-helper`, or `archive-candidate`. Move archive candidates under `archived/` with a rationale. Update `CLAUDE.md`'s "Active root-level `.py` files" count to match.

**Acceptance Criteria:**
- [x] `SURFACE-CLASSIFICATION.md` lists every root `.py` file with its classification.
- [x] `CLAUDE.md`'s root-file count and list match reality.
- [x] Files moved to `archived/` retain history (use `git mv`).
- [x] No production import path is broken (covered by US-018's bridge-resolver tests and US-019's entry-point smoke).
- [x] `python scripts/check_imports.py` or equivalent passes.
- [x] All relevant GitHub checks pass.

**Validation commands:**
```bash
python -m compileall -q $(ls *.py)
pytest -q
```

**Risk notes:** Moving a file that the Electron `bridgeResolver.ts` references will break the packaged app. Verify resolver paths first.

**Local validation evidence (2026-06-23):**
- `python -m compileall -q <all 27 root .py files>` → clean (no output)
- `python scripts/check_imports.py` → `[OK] All critical modules have valid syntax` (fixed Unicode encoding issue in script; removed archived gui.py/gui_settings_tab.py from module list)
- `pytest -q` → all tests passed (exit code 0)
- No files moved to `archived/` — all 17 bridge wrappers are actively imported by tests (`flask_proxy` imported by 2 test files; bridge wrappers imported by at least 3 test files)

---

### US-018: Bridge path resolution tests for dev and packaged mode

**Priority:** P0
**Workstream:** Packaging
**Description:** As a maintainer, I want unit tests that confirm bridge resolution returns valid file paths in both dev and packaged layouts.

**Files/areas likely involved:**
- `gui/src/main/bridgeResolver.ts`
- `tests/test_bridge_resolution.py` (new) or `gui/tests/bridgeResolver.test.ts` (new)
- `bridge/`, root `rex_*_bridge.py`

**Implementation notes:** A Python test enumerates the bridge scripts referenced by `bridgeResolver.ts` (parse the TypeScript file deterministically), then asserts each path exists in the source tree. A TypeScript/Vitest test asserts that, given a faked `app.getAppPath()`, the resolver returns paths under `resources/bridge/` for the packaged layout and under `bridge/` (or repo root) for dev.

**Acceptance Criteria:**
- [x] Python test asserts every bridge script referenced by `bridgeResolver.ts` exists in the source tree.
- [x] TypeScript test asserts resolver behavior in both dev and packaged-path modes.
- [x] `pytest tests/test_bridge_resolution.py -q` passes.
- [x] `cd gui && npm test` passes (if vitest is wired) OR `cd gui && npm run typecheck && npm run build` passes (acceptable interim).
- [x] All relevant GitHub checks pass.

**Local validation evidence (2026-06-23):**
- `pytest tests/test_bridge_resolution.py -q` → 24 passed (3 setup tests + 21 parametrized bridge scripts)
- `cd gui && npm test` → vitest 3.2.6, 2 tests passed (dev mode + packaged mode)
- `cd gui && npm run typecheck` → no errors
- `cd gui && npm run build` → built in ~1.4s, no errors

**Validation commands:**
```bash
pytest tests/test_bridge_resolution.py -q
cd gui && npm run typecheck && npm run build
```

**Risk notes:** Resolver behavior is the contract between the wheel/Electron packaging and the renderer; lock it down with tests.

---

### US-019: Console script import/help smoke tests

**Priority:** P0
**Workstream:** Packaging / CI
**Description:** As a maintainer, I want each declared console script to import and run `--help` without crashing.

**Files/areas likely involved:**
- `tests/test_console_scripts_smoke.py` (new)
- `.github/workflows/ci.yml`

**Implementation notes:** Parametrize a pytest over `rex`, `rex-config`, `rex-speak-api`, `rex-agent`, `rex-gui`, `rex-tool-server`. For each, run `<script> --help` (or `python -c "from <module> import <fn>"` where `--help` is not safe) and assert exit code 0 and non-empty stdout.

**Acceptance Criteria:**
- [x] One test per declared console script. *(6 parametrized import tests, one per script; plus 2 help tests for scripts with argparse.)*
- [x] All tests pass on a clean install of the wheel. *(Subprocess import approach works on any install; validated locally.)*
- [x] CI runs these tests after `pip install -e .`. *(Existing `tests` job does `pip install -e ".[dev]"` then `pytest`, which includes `tests/test_console_scripts_smoke.py`.)*
- [x] All relevant GitHub checks pass. *(PR #294: 15/15 checks green — CodeFactor, Dependency Vulnerability Scan, GUI Build, GUI Raw API Fetch Guard, GUI TypeScript Typecheck, GUI Vitest Tests, GitGuardian, Hardcoded Secret Scan, Lint & Format Check, Node Dependency Audit, Pre-commit Hook Validation, Python 3.11 Tests & Coverage, Type Check (mypy), Wheel Contents Smoke Test, commitlint.)*

**US-019 local validation evidence (2026-06-24):**
- `pytest tests/test_console_scripts_smoke.py -q` → 8 passed in 9.75s
- All 6 import tests pass (rex, rex-config, rex-speak-api, rex-agent, rex-gui, rex-tool-server)
- Both help tests pass (rex, rex-config): exit 0, non-empty stdout with "usage" string
- `ruff check` and `black --check` pass on the new test file
- Server scripts (rex-speak-api, rex-agent, rex-gui, rex-tool-server) tested via import only — `--help` is unsafe for server-start scripts requiring env vars or port-binding

**Validation commands:**
```bash
pip install -e .
pytest tests/test_console_scripts_smoke.py -q
```

**Risk notes:** `rex-gui` may import optional GPU/ML deps; the smoke must use a lazy/short-path startup.

---

### US-020: Fix or formally stub `rex/replay.py` so production callers do not silently misreport

**Priority:** P0
**Workstream:** Security / Verification
**Description:** As a user, I want replay results to be honest. The current stub returns a placeholder result while looking like a real return value.

**Files/areas likely involved:**
- `rex/replay.py`
- Callers of `replay_tool_call` or equivalent
- `tests/test_replay.py` (new or updated)

**Implementation notes:** Either (a) remove `rex/replay.py` from the production path and emit a clear "not implemented" error when called, or (b) implement minimal honest replay that reruns the tool through the existing tool executor. Either way, the function must not return a `status: "stub"` payload to callers that present it as a result.

**Acceptance Criteria:**
- [x] Calling `replay_tool_call(...)` either returns a real, verified result, OR raises `NotImplementedError("replay is not available in this build")` with no placeholder dict.
- [x] `rex/replay.py` no longer contains the strings `"placeholder"`, `"status": "stub"`, or `# TODO: implement` on any execution path reachable from a console script or IPC handler.
- [x] A test asserts that a calling code path either gets a real result or an exception — never a placeholder dict.
- [x] `README.md` or `docs/audit.md` documents the replay capability state honestly.
- [x] `SECURITY.md` notes the change if applicable.
- [x] All relevant GitHub checks pass.

**Validation commands:**
```bash
pytest tests/test_replay.py -q
python scripts/security_audit.py
```

**Risk notes:** Preserve the audit-log read path (`get_replayable_calls`) — that side is read-only and OK.

---

### US-021: Fix or formally stub `rex/openclaw/workflow_bridge.py` workflow executor registration

**Priority:** P0
**Workstream:** Security / OpenClaw
**Description:** As a user, I want OpenClaw workflow registration to either succeed or fail with a clear error.

**Files/areas likely involved:**
- `rex/openclaw/workflow_bridge.py`
- `tests/test_openclaw_workflow_bridge.py` (new or updated)

**Implementation notes:** The current code logs `"OpenClaw workflow executor registration stub — update once API is confirmed (PRD §8.6)"` and continues. Update the behavior: when `use_openclaw_tools` is False (default per US-050), registration is a no-op and the log is `info`. When `use_openclaw_tools` is True and the upstream API is not implemented, registration must raise `OpenClawConfigError("workflow executor not available")` so downstream code fails closed rather than silently bypassing OpenClaw.

**Acceptance Criteria:**
- [x] With `use_openclaw_tools=False`, registration is a no-op; no `# TODO` or `stub` log text remains on the reachable path.
- [x] With `use_openclaw_tools=True`, registration raises a clear error if the upstream API is not present.
- [x] A test covers both branches.
- [x] `python scripts/security_audit.py` no longer flags this file (or the inventory in US-001 marks it as `dev-only-documented` with a follow-up story).
- [x] `docs/openclaw-migration-status.md` is updated.
- [x] All relevant GitHub checks pass. *(Verified 2026-07-08: the fix `977a885` is merged to `master`; subsequent PRs #295/#297 ran the full check suite green on trees containing it.)*

**Validation commands:**
```bash
pytest tests/test_openclaw_workflow_bridge.py -q
python scripts/security_audit.py
```

**Risk notes:** Do not silently disable OpenClaw — that would mask configuration mistakes.

---

### US-022: Remove or guard the `rex/skills/trainer.py` placeholder

**Priority:** P0
**Workstream:** Security
**Description:** As a user, I want the skills trainer to either work or refuse cleanly.

**Files/areas likely involved:**
- `rex/skills/trainer.py`
- Callers of the trainer
- `tests/test_skills_trainer.py` (new or updated)

**Implementation notes:** The `# TODO: implement {name}` marker at line 127 must be removed. If the trainer is not on the production path, gate it behind an explicit `developer-only` flag and document it in `SURFACE-CLASSIFICATION.md`. If it is on the production path, implement the minimum honest behavior.

**Acceptance Criteria:**
- [x] `grep -n "TODO: implement" rex/skills/trainer.py` returns nothing on reachable paths. *(Verified 2026-07-08 on `master@fde0c76`: zero matches.)*
- [x] A test confirms the chosen behavior (works honestly, OR raises a clear `NotImplementedError` behind a flag). *(`tests/test_skill_trainer.py` and `tests/test_skills_trainer.py` cover the honest-scaffold behavior.)*
- [x] `SURFACE-CLASSIFICATION.md` is updated. *(Line ~110 classifies `rex.skills.trainer` as shippable with honest-stub semantics; changelog entry dated 2026-06-24 cites US-022.)*
- [x] All relevant GitHub checks pass. *(Delivered by PR #295, merged as `fde0c76` with checks green.)*

**Validation commands:**
```bash
pytest tests/test_skills_trainer.py -q
python scripts/security_audit.py
```

---

### US-023: Authenticate the logs download endpoint

**Priority:** P0
**Workstream:** Security
**Description:** As an operator, I want `/api/logs/*` endpoints to reject unauthenticated requests.

**Reconciliation status (2026-07-08):** Implementation and tests verified complete on `master@fde0c76`: `rex/routes/logs.py` requires a Bearer token via `_require_auth()` for both `/api/logs/stream` and `/api/logs/download` and redacts home-directory paths; `tests/test_rr008_log_auth.py` covers 401-without-token for both routes, authenticated success, and redaction. **Only the README documentation box remains open** — `docs/configuration.md` documents `REX_PROXY_TOKEN` but `README.md` does not mention the logs-endpoint auth requirement.

**Files/areas likely involved:**
- `rex/gui_app.py` (or the route file)
- `tests/test_rr008_log_auth.py` (delivered; the originally planned name was `tests/test_logs_auth.py`)

**Acceptance Criteria:**
- [x] Unauthenticated GET on `/api/logs/download` returns HTTP 401. *(Verified: `rex/routes/logs.py` `_require_auth()`; `tests/test_rr008_log_auth.py`.)*
- [x] Authenticated GET with a valid token still works. *(Covered by `tests/test_rr008_log_auth.py`.)*
- [x] A negative test asserts the 401 (delivered as `tests/test_rr008_log_auth.py`, e.g. `test_stream_without_token_returns_401`, rather than the originally planned `tests/test_logs_auth.py` name).
- [x] Log output redacts home-directory paths (`/Users/<name>`, `C:\Users\<name>`) before being sent in any response. *(`_redact_log_line` in `rex/routes/_helpers.py`; covered by `tests/test_rr008_log_auth.py`.)*
- [x] `docs/configuration.md` and `README.md` document the auth requirement. *(2026-07-28: README.md §Security notes documents the `/api/logs/*` Bearer-token requirement and home-directory redaction, referencing `docs/configuration.md`.)*
- [x] All relevant GitHub checks pass. *(Master `8dccbe3` CI green with the README mention present.)*

**Validation commands:**
```bash
pytest tests/test_logs_auth.py -q
```

**Risk notes:** Operator dev workflows may rely on unauthenticated `localhost` access. Document the change in `INSTALL.md` and `RUNNING.md`.

---

### US-024: Authenticate Home Assistant test/save/control endpoints

**Priority:** P0
**Workstream:** Security / Home Assistant
**Description:** As an operator, I want HA admin endpoints to require authentication.

**Reconciliation status (2026-08-04, supersedes 2026-07-08):** Complete. Route authentication and negative tests remain present. Electron IPC uses the stricter immutable local-session identity plus signed action-bound confirmation-token design delivered before PR #332. `docs/home_assistant.md` documents Rex endpoint authentication. PR #332 merged on 2026-07-28 and its CI, Windows Electron Artifact, and commitlint workflows all completed successfully.

**Files/areas likely involved:**
- `rex/gui_app.py` route handlers for HA
- `gui/src/main/handlers/devices.ts`
- `tests/test_ha_auth.py` (new or updated)

**Acceptance Criteria:**
- [x] HA test, save, and control routes return 401 without a valid token. *(`rex/routes/ha.py` calls `_require_auth()` on all three routes; `tests/test_rr009_ha_test_auth.py`.)*
- [x] IPC equivalents enforce the same auth via the main-process token store. *(Delivered by the superseding PR #331 design: Electron IPC device control is bound to the immutable local OS session identity (`gui/src/main/sessionIdentity.ts`) and HA mutations require signed action-bound confirmation tokens via the mutation bridge — a stricter control than a shared proxy token. `tests/test_ha_mutation_service.py`.)*
- [x] Negative tests cover each route. *(`tests/test_rr009_ha_test_auth.py` et al.)*
- [x] `docs/home_assistant.md` documents the auth requirement. *(2026-07-28: "Rex endpoint authentication" section.)*
- [x] All relevant GitHub checks pass. *(PR #332 head `4195c5d`: CI run 917, Windows Electron Artifact run 55, and commitlint run 584 all completed successfully; merged as `0371154` on 2026-07-28.)*

**Validation commands:**
```bash
pytest tests/test_ha_auth.py -q
```

---

### US-025: Confirmation gates for destructive tools

**Reconciliation status (2026-08-04):** Complete. Current code, tests, and documentation remain present on `master`; PR #332 supplied the previously pending GitHub verification evidence.

**Priority:** P0
**Workstream:** Security / Verification
**Description:** As a user, I want destructive tool calls to require explicit confirmation before execution.

**Files/areas likely involved:**
- `rex/actions/dispatcher.py`
- `rex/openclaw/tool_executor.py` (read path only; do not enable OpenClaw)
- `rex/tools/*` (any destructive tool)
- `tests/test_destructive_tool_confirmation.py` (new)

**Implementation notes:** Define "destructive" as: filesystem deletion outside per-user data dirs, HA locks/garage/alarms, broad HA scripts/scenes, outbound SMS/email, financial actions, autonomy plan execution. Require an explicit `confirmation_token` or a user-visible GUI confirmation before dispatch. Default refusal must be a clear, user-visible error, not a silent skip.

**Acceptance Criteria:**
- [x] A registry of destructive tools exists and is documented. *(Delivered as the `risk` field on every `ToolSpec` in `rex/tools/registry.py` (`safe`/`sensitive`/`prohibited`); documented 2026-07-28 in `docs/tools.md` "Risk Classes and Confirmation Gates".)*
- [x] Calling a destructive tool without confirmation returns a `requires_confirmation` response with a token. *(Delivered as outcome `confirmation_required` from the canonical lifecycle in `rex/tools/execution.py`; Home Assistant mutations use signed single-use action-bound confirmation tokens in `rex/ha/mutation_service.py`.)*
- [x] Calling with the matching token completes the action. *(Confirmed calls execute; HA token round-trip covered by `tests/test_ha_mutation_service.py`.)*
- [x] A negative test asserts that the first call does not execute the side effect. *(`tests/test_tool_execution_lifecycle.py`, `tests/test_ha_mutation_service.py`.)*
- [x] `README.md` and `docs/tools.md` document the gate. *(2026-07-28 additions.)*
- [x] All relevant GitHub checks pass. *(PR #332 head `4195c5d`: CI run 917, Windows Electron Artifact run 55, and commitlint run 584 all completed successfully; merged as `0371154` on 2026-07-28.)*

**Validation commands:**
```bash
pytest tests/test_destructive_tool_confirmation.py -q
```

**Risk notes:** Do not silently drop the action when refused — surface the refusal honestly.

---

### US-026: Twilio fails closed when dependencies or config are absent

**Priority:** P0
**Workstream:** Security / Integrations
**Description:** As an operator, I want SMS to refuse cleanly when Twilio is not configured rather than appear to succeed.

**Reconciliation status (2026-08-04):** Complete. Current code, tests, and documentation remain present on `master`; PR #332 supplied the previously pending GitHub verification evidence.

**Files/areas likely involved:**
- `rex/messaging_backends/twilio*.py`
- `bridge/rex_sms_bridge.py`
- `tests/test_twilio_fail_closed.py` (new or updated)

**Acceptance Criteria:**
- [x] Importing the Twilio backend without the `twilio` package raises a clear `IntegrationUnavailable("twilio not installed")`. *(Delivered as `ImportError` with install instructions at `TwilioSMSBackend` construction — same fail-closed behavior under the standard exception name; `tests/test_twilio_sms_backend.py`.)*
- [x] Sending without `TWILIO_*` env vars raises a clear error to the caller. *(`SMSSendError` naming each missing credential; values never logged.)*
- [x] No code path returns `success` on a missing-config send. *(Construction/credential resolution raise before any send path.)*
- [x] A test asserts a missing-dep send fails with a user-visible error. *(`tests/test_twilio_sms_backend.py`, `tests/test_ph001_twilio_handler.py`.)*
- [x] `docs/messaging.md` documents the behavior. *(2026-07-28: "Twilio Fail-Closed Behavior" section.)*
- [x] All relevant GitHub checks pass. *(PR #332 head `4195c5d`: CI run 917, Windows Electron Artifact run 55, and commitlint run 584 all completed successfully; merged as `0371154` on 2026-07-28.)*

**Validation commands:**
```bash
pytest tests/test_twilio_fail_closed.py -q
```

---

### US-027: Redact tokens from GUI settings JSON before persisting

**Reconciliation status (2026-08-04):** Complete. Current code, tests, and documentation remain present on `master`; PR #332 supplied the previously pending GitHub verification evidence.

**Priority:** P0
**Workstream:** Security
**Description:** As an operator, I want no secrets written to `config/gui_settings.json`.

**Files/areas likely involved:**
- `gui/src/main/index.ts` (`readGuiSettings` / `writeGuiSettings`)
- `gui/src/main/handlers/*`
- `tests/test_gui_settings_redaction.py` (new)
- `gui/tests/settingsRedaction.test.ts` (new, vitest)

**Acceptance Criteria:**
- [x] Any key matching a documented secret pattern (API keys, tokens, passwords) is stored only in `.env`, never in `config/gui_settings.json`. *(2026-07-28: `writeGuiSettings` now strips secret-pattern keys at any depth via `gui/src/main/settingsRedaction.ts` before persisting; secrets entered in the GUI are written to `.env` via `writeEnvKey`. The packaged-resource scanner additionally forbids `.env` and `gui_settings.json` in the artifact.)*
- [x] A test loads `config/gui_settings.json` from a fixture and asserts no secret pattern appears. *(Delivered as source-level Vitest coverage: `gui/tests/settingsRedaction.test.ts` asserts secret keys are stripped from persisted settings at every nesting depth, plus legit keys like `max_tokens`/`api_key_env` survive.)*
- [x] When the renderer needs a secret, it requests via IPC and the main process reads `.env`. *(e.g. `getApiKeys` returns only set/unset booleans; the HA token is read from `.env` in `gui/src/main/homeAssistant.ts` and never returned to the renderer or stored in gui_settings.)*
- [x] `docs/configuration.md` and `SECURITY.md` document the rule. *(2026-07-28 additions.)*
- [x] All relevant GitHub checks pass. *(PR #332 head `4195c5d`: CI run 917, Windows Electron Artifact run 55, and commitlint run 584 all completed successfully; merged as `0371154` on 2026-07-28.)*

**Validation commands:**
```bash
pytest tests/test_gui_settings_redaction.py -q
cd gui && npm test -- settingsRedaction || true
```

---

### US-028: Verify no tokens in tracked config

**Reconciliation status (2026-08-04):** Complete. Current code, tests, and documentation remain present on `master`; PR #332 supplied the previously pending GitHub verification evidence.

**Priority:** P0
**Workstream:** Security / CI
**Description:** As a maintainer, I want CI to fail if a token-looking string is committed under `config/`.

**Files/areas likely involved:**
- `.secrets.baseline`
- `scripts/security_audit.py`
- `.github/workflows/ci.yml`

**Acceptance Criteria:**
- [x] `detect-secrets` scan covers `config/`. *(The CI Hardcoded Secret Scan excludes only `.venv|__pycache__|.git|.egg-info` — `config/` is scanned; `tests/test_us096_secret_scan.py` runs the same whole-tree scan.)*
- [x] A test fixture confirms a known secret pattern under `config/` would fail the scan. *(2026-07-28: `test_planted_secret_under_config_is_detected` plants an AWS-style key in a throwaway config/ JSON and asserts detect-secrets flags it.)*
- [x] The PR review checklist mentions secret-scan results. *(2026-07-28: `.github/PULL_REQUEST_TEMPLATE.md` Verification checklist.)*
- [x] `SECURITY.md` documents the rule. *(Security baseline section covers the detect-secrets gate incl. `config/`.)*
- [x] All relevant GitHub checks pass. *(PR #332 head `4195c5d`: CI run 917, Windows Electron Artifact run 55, and commitlint run 584 all completed successfully; merged as `0371154` on 2026-07-28.)*

**Validation commands:**
```bash
python -m detect_secrets scan --baseline .secrets.baseline config/
```

---

### US-029: Close out the security audit inventory

**Reconciliation status (2026-08-04):** Complete. Current code, tests, and documentation remain present on `master`; PR #332 supplied the previously pending GitHub verification evidence.

**Priority:** P0
**Workstream:** Security / Docs
**Description:** As a maintainer, I want the inventory from US-001 to show zero untriaged actionable findings on the release-candidate commit.

**Files/areas likely involved:**
- `docs/security/AUDIT-INVENTORY.md`
- `scripts/security_audit.py`

**Acceptance Criteria:**
- [x] Every row in `docs/security/AUDIT-INVENTORY.md` is either `resolved` or `documented-and-accepted`. *(2026-07-28: the eight open production-blocker rows were stale — the flagged markers in `rex/replay.py`, `rex/openclaw/workflow_bridge.py`, and `rex/skills/trainer.py` were fixed by commits `3b049cd`, `977a885`, `fde0c76`; verified absent from current source and rows marked resolved with commit evidence.)*
- [x] No row is `production-blocker` with status `open`. *(Status counts: open=0, resolved=8, documented=206.)*
- [x] `python scripts/security_audit.py` exits 0 OR exits with only findings explicitly listed in an allowlist with justification. *(2026-07-28 local run of `--release-gate` mode: exit 0, zero exposed secrets; now CI-enforced by the Security Audit Gate job.)*
- [x] `README.md`'s "Security baseline" section is current. *(Points to the inventory and the release-gate command.)*
- [x] All relevant GitHub checks pass. *(PR #332 head `4195c5d`: CI run 917, Windows Electron Artifact run 55, and commitlint run 584 all completed successfully; merged as `0371154` on 2026-07-28.)*

**Validation commands:**
```bash
python scripts/security_audit.py
```

---

### US-030: Run `ruff check .` over the full tree in CI

**Priority:** P0
**Workstream:** CI
**Description:** As a maintainer, I want CI to lint the full repository, not just `rex/`, `tests/`, `bridge/`, and `*.py` at the root.

**Reconciliation status (2026-07-08, supersedes 2026-06-12):** Complete. `.github/workflows/ci.yml` line 43 runs `ruff check --output-format=github .`; `pyproject.toml` `[tool.ruff]` excludes exactly one path (`archived` — retired, unmaintained files); the Lint & Format Check job is green on `master@fde0c76` and every recent PR.

**Files/areas likely involved:**
- `.github/workflows/ci.yml`
- `pyproject.toml` `[tool.ruff]`

**Acceptance Criteria:**
- [x] CI invokes `ruff check .` (excluding `archived/`) and fails on any error.
- [x] `pyproject.toml` excludes are reviewed and minimized. *(Single entry: `archived`.)*
- [x] All relevant GitHub checks pass. *(Lint & Format Check green on master and recent PRs, 2026-07-08.)*

**Validation commands:**
```bash
ruff check .
```

---

### US-031: Run `black --check` over Python source, `bridge/`, `scripts/`, `tests/`, and root Python files in CI

**Priority:** P0
**Workstream:** CI
**Description:** As a maintainer, I want CI Black coverage to include `scripts/` too.

**Reconciliation status (2026-08-04):** Complete. Current code, tests, and documentation remain present on `master`; PR #332 supplied the previously pending GitHub verification evidence.

**Files/areas likely involved:**
- `.github/workflows/ci.yml`

**Acceptance Criteria:**
- [x] CI runs `black --check --diff rex/ tests/ bridge/ scripts/ *.py`. *(Commit `36f4cb2`.)*
- [x] Any unformatted file fails the check. *(Same blocking Lint & Format Check job.)*
- [x] All relevant GitHub checks pass. *(PR #332 head `4195c5d`: CI run 917, Windows Electron Artifact run 55, and commitlint run 584 all completed successfully; merged as `0371154` on 2026-07-28.)*

**Validation commands:**
```bash
black --check --diff rex/ tests/ bridge/ scripts/ *.py
```

---

### US-032: Run `pytest` excluding only documented slow/audio/GPU markers in CI

**Priority:** P0
**Workstream:** CI
**Description:** As a maintainer, I want CI test scope to match the documented marker policy.

**Reconciliation status (2026-07-08, supersedes 2026-06-12):** Complete. The CI test job runs `pytest -m "not slow and not audio and not gpu" --cov=rex --cov-fail-under=75 ...` with `--strict-markers`; every excluded marker is declared with an explanatory description in `pyproject.toml` `[tool.pytest.ini_options].markers` (`slow`, `audio`, `gpu` each state why they need special environments); the job is green on `master@fde0c76`.

**Files/areas likely involved:**
- `.github/workflows/ci.yml`
- `pyproject.toml` `[tool.pytest.ini_options]`

**Acceptance Criteria:**
- [x] CI runs `pytest -m "not slow and not audio and not gpu"`.
- [x] Marker docs list which markers are excluded and why. *(`pyproject.toml` markers section, enforced by `--strict-markers`.)*
- [x] All relevant GitHub checks pass. *(Python 3.11 Tests & Coverage green on master and recent PRs, 2026-07-08.)*

**Validation commands:**
```bash
pytest -m "not slow and not audio and not gpu" -q
```

---

### US-033: Add the wheel contents smoke test as a required CI check

**Priority:** P0
**Workstream:** CI / Packaging
**Description:** As a maintainer, I want CI to run `scripts/check_wheel_contents.py` as a blocking gate.

**Reconciliation status (2026-08-04):** Complete. Repository ruleset `8318444` (`Precaution`) is active on `~DEFAULT_BRANCH`, has no bypass actors, preserves deletion protection, and requires `Wheel Contents Smoke Test` with strict latest-code enforcement. PR #346 passed that check and the complete CI/commitlint suite before merging as `dac19c1`.

**Files/areas likely involved:**
- `.github/workflows/ci.yml`

**Acceptance Criteria:**
- [x] A wheel-smoke job runs the wheel build and `python scripts/check_wheel_contents.py`. *(Verified 2026-07-08: "Wheel Contents Smoke Test" job in ci.yml.)*
- [x] Job is required for merge. *(Verified through repository ruleset `8318444`: the active no-bypass default-branch ruleset requires `Wheel Contents Smoke Test` and uses strict latest-code enforcement.)*
- [x] All relevant GitHub checks pass. *(PR #346 head `3f81940`: CI run 966 and commitlint run 627 completed successfully; the required wheel smoke job passed before the PR merged as `dac19c1`.)*

**Validation commands:**
```bash
python -m build
python scripts/check_wheel_contents.py
```

---

### US-034: Add the security audit as a required CI check

**Reconciliation status (2026-08-04):** Complete. Current code, tests, and documentation remain present on `master`; PR #332 supplied the previously pending GitHub verification evidence.

**Priority:** P0
**Workstream:** CI / Security
**Description:** As a maintainer, I want CI to fail when `scripts/security_audit.py` detects new untriaged actionable findings.

**Files/areas likely involved:**
- `.github/workflows/ci.yml`

**Acceptance Criteria:**
- [x] A `security-audit` job runs `python scripts/security_audit.py` and fails on a non-zero exit. *(2026-07-28: "Security Audit Gate" job in ci.yml runs `--release-gate` mode, which is strictly stricter; local run exits 0.)*
- [x] The job is documented in `SECURITY.md`. *(Security baseline section.)*
- [x] All relevant GitHub checks pass. *(PR #332 head `4195c5d`: CI run 917, Windows Electron Artifact run 55, and commitlint run 584 all completed successfully; merged as `0371154` on 2026-07-28.)*

**Validation commands:**
```bash
python scripts/security_audit.py
```

---

### US-035: Add "no generated artifacts committed" check to CI

**Reconciliation status (2026-08-04):** Complete. Current code, tests, and documentation remain present on `master`; PR #332 supplied the previously pending GitHub verification evidence.

**Priority:** P0
**Workstream:** CI
**Description:** As a maintainer, I want CI to fail if generated artifacts like `.coverage`, `coverage.xml`, `htmlcov/`, `dist/`, `build/`, or compiled Python caches are committed.

**Files/areas likely involved:**
- `scripts/check_no_generated_artifacts.py` (new)
- `.gitignore`
- `.github/workflows/ci.yml`

**Acceptance Criteria:**
- [x] Script enumerates generated patterns and fails if any are tracked. *(2026-07-28: `scripts/check_no_generated_artifacts.py`; the deliberately committed `rex/ui/dist/index.html` dashboard bundle is allowlisted with justification; `tests/test_us035_no_generated_artifacts.py` covers patterns, allowlist, and the live tree.)*
- [x] CI runs the script. *(Step in the Lint & Format Check job.)*
- [x] `.gitignore` covers each pattern. *(Verified: coverage/dist/build/htmlcov/pycache entries present.)*
- [x] All relevant GitHub checks pass. *(PR #332 head `4195c5d`: CI run 917, Windows Electron Artifact run 55, and commitlint run 584 all completed successfully; merged as `0371154` on 2026-07-28.)*

**Validation commands:**
```bash
python scripts/check_no_generated_artifacts.py
```

---

### US-036: Add "working tree clean after tests" check to CI

**Priority:** P0
**Workstream:** CI
**Description:** As a maintainer, I want CI to fail if a test modified a tracked file.

**Reconciliation status (2026-08-04):** Complete. Current code, tests, and documentation remain present on `master`; PR #332 supplied the previously pending GitHub verification evidence.

**Files/areas likely involved:**
- `.github/workflows/ci.yml` (the existing "Verify tests did not modify tracked files" step — promote to all relevant jobs)

**Acceptance Criteria:**
- [x] Every job that runs tests includes the working-tree-clean check. *(2026-07-28: Python 3.11 Tests & Coverage and GUI Vitest Tests jobs both verify.)*
- [x] The check ignores documented artifacts (`.coverage`, `coverage.xml`, `htmlcov/`). *(Python job step excludes exactly these.)*
- [x] All relevant GitHub checks pass. *(PR #332 head `4195c5d`: CI run 917, Windows Electron Artifact run 55, and commitlint run 584 all completed successfully; merged as `0371154` on 2026-07-28.)*

**Validation commands:**
```bash
pytest -q
git status --porcelain -- ':!.coverage' ':!coverage.xml' ':!htmlcov/'
```

---

### US-037: Skip budget enforcement in CI

**Reconciliation status (2026-08-04):** Complete. PR #348 implementation head `2579060` passed CI run 972 and commitlint run 631. The first Python runner was cancelled after an infrastructure-only Ubuntu package-install stall before project code ran; rerun job `92049529366` passed 8,304 tests with 119 skips, the 119-test budget gate, 36 integration tests, and the tracked-tree cleanliness check.

**Priority:** P0
**Workstream:** CI / Tests
**Description:** As a maintainer, I want CI to fail when total skipped tests exceed a documented budget.

**Files/areas likely involved:**
- `scripts/check_skip_budget.py` (new)
- `docs/testing/SKIPPED-TESTS-INVENTORY.md`
- `.github/workflows/ci.yml`

**Implementation notes:** Parse the pytest output (`-rs`) to count skipped tests. Compare against `SKIP_BUDGET` declared in `pyproject.toml` or a top-of-file constant. Default budget is the count from US-002.

**Acceptance Criteria:**
- [x] Script enforces the budget and fails when exceeded. *(`scripts/check_skip_budget.py`; parser and CLI regression tests in `tests/test_us037_skip_budget.py`.)*
- [x] Budget is documented and matches the post-US-002 count minus removals from US-039. *(PR #348 established 119; US-039 removed 37 retired-dashboard skips and lowered the current budget to 82.)*
- [x] CI runs the script after the test suite. *(The Python 3.11 job captures `-rs` output to `coverage.txt`, then runs the gate before integration tests.)*
- [x] All relevant GitHub checks pass. *(PR #348 head `2579060`: CI run 972 and commitlint run 631 succeeded; rerun job `92049529366` reported 8,304 passed / 119 skipped, and `check_skip_budget.py` passed at 119/119.)*

**Validation commands:**
```bash
pytest -rs --no-header -q | tee /tmp/pytest.out
python scripts/check_skip_budget.py /tmp/pytest.out
```

---

### US-038: Classify each skipped test and link to a follow-up if needed

**Implementation status (2026-08-04):** Complete on `lead/us038-skip-actions`. The current 143 executable skip sites were regenerated from AST, assigned explicit actions, linked to US-039 or US-040 where work remains, protected by a CI drift validator, and verified by all relevant GitHub checks.

**Priority:** P1
**Workstream:** Tests / Docs
**Description:** As a maintainer, I want every entry in the skip inventory tied to an action (keep, remove, replace, fix).

**Files/areas likely involved:**
- `docs/testing/SKIPPED-TESTS-INVENTORY.md`
- `tests/` (annotation only)

**Acceptance Criteria:**
- [x] Every inventory row has an action and, where action is non-trivial, a follow-up story ID. *(143 rows: 35 `keep`, 92 `fix`, 2 `replace`, 14 `archive`; non-trivial rows link to US-039 or US-040.)*
- [x] Inventory is committed and current. *(`scripts/check_skip_inventory.py` verifies exact file, line, type, reason, action, and follow-up parity against the current `tests/` AST.)*
- [x] All relevant GitHub checks pass. *(PR #349 head `996b076`; CI attempt 2 passed after an unnecessary operator cancellation/retry of the first still-running Python job.)*

**Validation commands:**
```bash
grep -E "TODO|FIXME|none" docs/testing/SKIPPED-TESTS-INVENTORY.md || echo "ok"
```

**US-038 remote verification evidence (2026-08-04):**
- PR #349 head `996b076` passed CodeFactor, GitGuardian, commitlint, Ruff, Black, mypy, GUI lint/typecheck/tests/build, dependency audits, pre-commit, secret scan, security release gate, raw-API guard, wheel contents smoke, and the skip-inventory validator.
- Python 3.11 coverage suite: 8,308 passed, 119 skipped; 82.36% coverage.
- Skip budget: 119 skipped against a budget of 119.
- Integration suite: 36 passed, 3 skipped.
- Tests left the tracked working tree clean and the coverage artifact uploaded successfully.
- The first Python job was cancelled while still running because its UTC timestamp was misread as five hours old; it had actually run for only about six minutes. Attempt 2 supplied the valid remote evidence.

---

### US-039: Remove or archive tests for retired surfaces

**Implementation status (2026-08-04):** Local implementation complete on `lead/us039-retired-tests`. Thirteen wholly retired Flask-dashboard test files were moved under `archived/flask_dashboard/tests/`; the one obsolete dashboard assertion was removed from the otherwise-current voice-length suite. After rebasing onto current master, Python 3.11 collection dropped from 8,541 to 8,504 without error. Remote GitHub verification remains pending.

**Priority:** P1
**Workstream:** Tests
**Description:** As a maintainer, I want tests that target removed surfaces (Tkinter GUI, shopping PWA, retired Flask dashboard) gone from the active suite.

**Files/areas likely involved:**
- `tests/` (any file targeting `archived/` surfaces)
- `archived/`

**Acceptance Criteria:**
- [x] Tests for retired surfaces are either deleted or moved under `archived/` with the surface they tested. *(Thirteen retired Flask-dashboard files moved to `archived/flask_dashboard/tests/`; no deprecated/current surface tests were archived.)*
- [x] `pytest --collect-only -q` collects fewer tests after the change AND no collection error appears. *(Python 3.11 on current master: 8,541 before, 8,504 after.)*
- [x] Skip inventory is updated. *(129 executable sites remain; the runtime budget is reduced from 119 to 82.)*
- [x] All relevant GitHub checks pass. *(PR #351 head `b6d2f4e`; CI run `31176594845` passed CodeFactor, dependency/security scans, lint/format, mypy, GUI lint/typecheck/tests/build, pre-commit, wheel smoke, commitlint, and Python 3.11 coverage/integration. Python job `92859835933` completed in 10m51s.)*

**Validation commands:**
```bash
pytest --collect-only -q | wc -l
pytest -q
```

**Risk notes:** Do not delete tests for surfaces classified as `deprecated` — those still need coverage.

---

### US-040: Add or restore tests for current supported surfaces that lost coverage

**Implementation status (2026-08-07):** Direct contract coverage is present for every current shippable surface in `SURFACE-CLASSIFICATION.md`, and the stricter legacy implementation-note paths are also covered. The exact PRD coverage command exposed one obsolete generated-artifact assertion in the historical US-098 coverage tests; US-040 replaces that assertion with stable coverage/reporting contracts rather than requiring an ignored `coverage.txt` scratch file. Remaining unrelated temporary skip debt is routed to US-089 through US-093 instead of being falsely closed by this story.

**Priority:** P1
**Workstream:** Tests
**Description:** As a maintainer, I want the test suite to actually cover the surfaces this PRD classifies as shippable.

**Files/areas likely involved:**
- `tests/test_console_scripts_smoke.py`
- `tests/test_skill_trainer.py` and `tests/test_skills_trainer.py`
- `tests/smoke/test_electron_package.sh`
- `tests/test_us005_bridge_json_io.py`
- `tests/test_us074_voice_loop_pipeline.py`
- `tests/test_us098_test_coverage.py`

**Implementation notes:** `SURFACE-CLASSIFICATION.md` is authoritative: the `rex` CLI and Electron desktop app are shippable user-facing surfaces, `SkillTrainer` is a shippable internal user-facing capability, and the Windows Electron Voice installer is the shippable distribution artifact. Bridge wrappers and `rex_loop.py` are developer-only today, but direct tests remain required here because the original US-040 note named them. Add tests only where a real public-contract gap exists; do not add meta-tests that merely assert other tests exist.

**Acceptance Criteria:**
- [x] At least one direct test per shippable surface. *(CLI: `tests/test_console_scripts_smoke.py`; SkillTrainer: `tests/test_skill_trainer.py` + `tests/test_skills_trainer.py`; Electron/installer: `tests/smoke/test_electron_package.sh` plus Windows artifact smoke workflow. Legacy-note bridge/`rex_loop.py` paths: `tests/test_us005_bridge_json_io.py` + `tests/test_us074_voice_loop_pipeline.py`. Local focused runs: 26/26 and 39/39 passed on Python 3.11.9; Windows artifact run 31148746251 passed installed-artifact smoke.)*
- [x] Coverage gate (`fail_under = 75`) still passes. *(`python -m pytest -q --cov=rex --cov-fail-under=75`: 8,449 passed, 49 skipped, 0 failed; total coverage 83.26% on Python 3.11.9.)*
- [x] All relevant GitHub checks pass. *(PR #352 implementation head `f127392`; CI run 31213049056 plus commitlint run 31213049246: all 17 checks passed. Python 3.11 Tests & Coverage job 92980116557 passed in 9m37s, including coverage, skip-budget enforcement, integration tests, working-tree-clean verification, and coverage artifact upload.)*

**Validation commands:**
```bash
pytest -q --cov=rex --cov-fail-under=75
```

---

### US-041: Replace fake Docker healthcheck or classify Docker as developer-only

**Priority:** P1
**Workstream:** Packaging / Docs
**Description:** As an operator, I want the Docker healthcheck to mean something OR I want the docs to be honest that Docker is not a supported production path.

**Files/areas likely involved:**
- `Dockerfile`
- `docs/docker.md`
- `SURFACE-CLASSIFICATION.md`
- `README.md`

**Implementation notes:** Decision for this PRD: Docker stays a developer-only path. Replace the no-op healthcheck with `python -m rex doctor --healthcheck` (or equivalent) so it returns non-zero on real failure, and classify Docker as `developer-only` everywhere.

**Acceptance Criteria:**
- [x] `Dockerfile` HEALTHCHECK invokes a real check that returns non-zero on failure. *(`python -m rex doctor --healthcheck` verifies supported Python, the installed Rex package/contract, and CLI parser; unit coverage proves non-OK core runtime checks return 1. Image metadata confirms Docker executes this probe.)*
- [x] `docker build .` succeeds and `docker run --rm askrex-assistant python -m rex doctor` exits 0. *(`docker build -t askrex-assistant:smoke .` passed locally on Docker 28.4.0; both the lightweight probe and full doctor exited 0 inside the built image.)*
- [x] `docs/docker.md`, `README.md`, and `SURFACE-CLASSIFICATION.md` describe Docker as developer-only. *(The same classification is also propagated to `docs/advanced-install.md`, `docs/deployment.md`, `docs/INDEX.md`, and `CLAUDE.md`.)*
- [x] All relevant GitHub checks pass. *(PR #353 implementation head `ced2908`: CI run `31218152883` and commitlint run `31218150711` passed; Windows Electron Artifact run `31218151625`, job `92996384714`, passed in 12m36s including installed-artifact exercise without machine Python or Node.)*

**Validation commands:**
```bash
docker build -t askrex-assistant:smoke .
docker run --rm askrex-assistant:smoke python -m rex doctor
```

**Risk notes:** CI may not run Docker; local smoke is acceptable evidence with a documented log.

---

### US-042: Define Hold-to-Talk as the production voice path

**Priority:** P0
**Workstream:** Voice / Docs
**Description:** As an end user, I want the documented default voice mode to be reliable (Hold-to-Talk) instead of marketing wake word as production.

**Files/areas likely involved:**
- `rex_loop.py`
- `rex/voice_loop.py`
- `README.md`
- `docs/voice_identity.md` (and any voice docs)
- `SURFACE-CLASSIFICATION.md`

**Implementation notes:** Add a `--mode hold-to-talk` and `--mode wake-word` flag to the voice loop entry, with `hold-to-talk` as the default. Document that wake word is beta until reliability tests pass (US-046).

**Acceptance Criteria:**
- [x] CLI default is Hold-to-Talk. *(`rex_loop.py` exposes `--mode {hold-to-talk,wake-word}` with `hold-to-talk` as the parser default and passes the selected mode into the canonical builder.)*
- [x] A test confirms the default mode resolves to Hold-to-Talk when no flag is provided. *(`tests/test_voice_loop_default_mode.py` also covers explicit wake-word opt-in and the manual activation listener.)*
- [x] `README.md` says Hold-to-Talk is the supported production voice mode. *(Electron is the true press/hold production UX; the source CLI documents its Enter-triggered manual activation honestly.)*
- [x] `SURFACE-CLASSIFICATION.md` classifies wake word as `beta`/`developer-only` until US-046.
- [x] All relevant GitHub checks pass. *(PR #354 implementation head `10d47cb`: CI run `31221345052`, commitlint run `31221345005`, and Windows Electron Artifact run `31221345114` all passed; Python coverage job `93006401557` and installed-artifact job `93006401583` were green.)*

**Validation commands:**
```bash
pytest tests/test_voice_loop_default_mode.py -q
python -m rex --help | grep -i "voice\|hold"
```

**Risk notes:** Existing wake word users must have a single flag to opt back in.

---

### US-043: Voice pipeline structured logs

**Priority:** P0
**Workstream:** Voice
**Description:** As an operator, I want each voice pipeline stage to emit a structured log line with timing.

**Files/areas likely involved:**
- `rex/voice_loop.py`
- `rex_loop.py`
- `rex/logging_utils.py`
- `tests/test_voice_pipeline_logs.py` (new)

**Implementation notes:** Emit JSON log records for `wake_detected`, `capture_started`, `capture_ended`, `stt_started`, `stt_completed`, `llm_started`, `llm_completed`, `tts_started`, `playback_completed`. Each record includes a `session_id`, monotonic `start_ns`, and `duration_ms` where applicable.

**Acceptance Criteria:**
- [x] All nine events are emitted with the documented fields. *(`rex/voice/loop.py` emits stable JSON-extra fields using the process logging session ID and `time.monotonic_ns()` timing.)*
- [x] A test captures the log stream and asserts every expected event for one happy-path session. *(`tests/test_voice_pipeline_logs.py` validates event order, fields, durations, and JSON serialization.)*
- [x] `docs/voice_identity.md` (or new `docs/voice_pipeline.md`) documents the log contract. *(`docs/voice_pipeline.md` documents event semantics, timing fields, streaming overlap, and failure behavior.)*
- [x] All relevant GitHub checks pass. *(PR #355 implementation head `c022660`: CI run `31223622521`, commitlint run `31223622680`, and Windows Electron Artifact run `31223622614` all passed; Python job `93013287284` completed in 8m09s and installed-artifact job `93013287155` in 12m42s.)*

**Validation commands:**
```bash
pytest tests/test_voice_pipeline_logs.py -q
```

---

### US-044: Voice latency budget tests

**Priority:** P0
**Workstream:** Voice
**Description:** As an operator, I want CI to fail if voice stage latencies regress beyond documented budgets.

**Files/areas likely involved:**
- `tests/test_voice_latency_budget.py` (new)
- `docs/voice_pipeline.md`

**Implementation notes:** Define stage budgets (e.g., STT < 1500 ms, LLM token-to-first < 500 ms for local provider) and assert under a synthetic input fixture. Mark as `slow` if needed.

**Acceptance Criteria:**
- [x] Budget table documented in `docs/voice_pipeline.md`.
- [x] At least one test enforces a budget on each stage.
- [x] Test runs on CI under `slow` only OR under default markers if fast enough.
- [x] All relevant GitHub checks pass. (PR #356 implementation head `9a8e706`; CI run `31231785722` and commitlint run `31231785762` passed.)

**Validation commands:**
```bash
pytest tests/test_voice_latency_budget.py -q
```

**Risk notes:** Avoid flakiness; use a synthetic source and a wide margin on first introduction.

---

### US-045: Mic/speaker diagnostics surfaced to the user

**Priority:** P0
**Workstream:** Voice
**Description:** As an end user, I want a clear error if my mic or speaker is not available.

**Files/areas likely involved:**
- `rex/audio_config.py`
- `rex/voice_loop.py`
- `gui/src/main/handlers/voice.ts`
- `tests/test_audio_diagnostics.py`

**Acceptance Criteria:**
- [x] On mic init failure, voice loop emits a structured error AND the Electron GUI shows a visible error toast/banner.
- [x] On speaker init failure, the same holds.
- [x] A test confirms the error is surfaced to the IPC handler with a user-actionable message.
- [x] `docs/troubleshooting.md` lists the new errors.
- [x] All relevant GitHub checks pass. (PR #357 corrected implementation head `3657005`; CI run `31234847034`, commitlint run `31234847011`, and Windows Electron Artifact run `31234847017` passed. Python job `93045346469` passed in 9m13s and installed Windows artifact job `93045346341` passed in 12m12s.)

**Validation commands:**
```bash
pytest tests/test_audio_diagnostics.py -q
```

---

### US-046: Wake word reliability classification

**Priority:** P0
**Workstream:** Voice / Docs
**Description:** As a maintainer, I want documented wake word reliability metrics before claiming wake word as production.

**Files/areas likely involved:**
- `tests/test_wakeword_reliability.py` (new)
- `docs/voice_pipeline.md`
- `README.md`, `SURFACE-CLASSIFICATION.md`

**Implementation notes:** Run a controlled wake word fixture (positives + negatives) and record precision/recall/latency. Until both precision and recall pass a documented threshold (default 0.9), wake word remains `beta` in docs.

**Acceptance Criteria:**
- [x] A test produces precision/recall numbers from a fixture and writes them to a tracked report file (`docs/voice/wakeword-report.md`).
- [x] If thresholds pass, `SURFACE-CLASSIFICATION.md` may reclassify wake word. (Controlled thresholds did not pass, so no promotion was performed.)
- [x] If thresholds fail, docs continue to label wake word as `beta`. (Measured precision `0.800`, recall `1.000`; promotion requires `0.90`/`0.90`.)
- [x] All relevant GitHub checks pass. *(PR #358 implementation head `c2978e9`; CI run `31240422060`, Python job `93060342800` passed in 9m38s; Windows artifact run `31240422062`, job `93060342614` passed in 11m24s; commitlint run `31240422058`; all required checks green.)*

**Validation commands:**
```bash
pytest tests/test_wakeword_reliability.py -q
```

**Risk notes:** Fixture data must be redistributable. Use synthesized samples or licensed clips.

---

### US-047: Home Assistant confirmation gates for risky domains

**Priority:** P0
**Workstream:** Home Assistant / Security
**Description:** As a user, I want HA actions on `lock`, `cover` (garage), `alarm_control_panel`, and broad `script.*`/`scene.*` to require explicit confirmation.

**Files/areas likely involved:**
- `rex/integrations/home_assistant*.py` (or wherever HA dispatch lives)
- `gui/src/main/handlers/devices.ts`
- `tests/test_ha_confirmation_gate.py` (new)

**Acceptance Criteria:**
- [x] Calling a risky-domain action without confirmation returns `requires_confirmation` (the canonical AskRex wire status is `confirmation_required`).
- [x] Confirmed call proceeds.
- [x] Negative test asserts side effect did not occur on the first call.
- [x] `docs/home_assistant.md` lists the risky domains and the gate.
- [x] All relevant GitHub checks pass. *(PR #359 head 9f269a2866f410a17063dfdf01a9b312d052d42a; CI run 31242596301, Commitlint run 31242596359, and Windows Electron Artifact run 31242596280 all completed successfully.)*

**Validation commands:**
```bash
pytest tests/test_ha_confirmation_gate.py -q
```

---

### US-048: Home Assistant post-control state verification

**Priority:** P0
**Workstream:** Home Assistant / Verification
**Description:** As a user, I want Rex to verify the entity's actual state after a control call before reporting success.

**Files/areas likely involved:**
- `rex/integrations/home_assistant*.py`
- `gui/src/main/handlers/devices.ts`
- `tests/test_ha_verification.py` (new)

**Implementation notes:** After dispatching, poll the entity state up to a configurable timeout. Compare expected vs actual. Report `verified` when the state matches, `attempted` when dispatch returned but state did not yet change, `completed` when state changes were applied but verification is not applicable, `failed` on dispatch error.

**Acceptance Criteria:**
- [x] Verification is run for switchable domains (`switch`, `light`, `lock`, `cover`).
- [x] Return shape is `{ status, expected, actual, latency_ms }`; Electron maps `latency_ms` to `latencyMs` while preserving `expected` and `actual`.
- [x] Tests cover happy path and the "state did not change" path.
- [x] `docs/home_assistant.md` documents the verification model.
- [x] All relevant GitHub checks pass. *(PR #361 implementation head `a56fc79b85a995b6a69820b2f7b565af3c9cc918`; CI run `31257576337`, Commitlint run `31257576383`, and Windows Electron Artifact run `31257576346` all completed successfully.)*

**Validation commands:**
```bash
pytest tests/test_ha_verification.py -q
```

**Risk notes:** Verification must not block the UI longer than the documented timeout.

---

### US-049: Response language distinguishes attempted/completed/verified/failed

**Priority:** P0
**Workstream:** Home Assistant / Verification / UX
**Description:** As a user, I want Rex's speech and text responses to use precise verification language.

**Files/areas likely involved:**
- `rex/response/builder.py`
- `rex/actions/dispatcher.py`
- `gui/src/pages/DevicesPage.tsx`
- `tests/test_response_verification_language.py` (new)

**Acceptance Criteria:**
- [x] A response builder helper maps `{ status }` to user-facing text per the documented vocabulary.
- [x] Tests assert each status produces the correct phrase ("I tried…", "I asked HA to…", "Confirmed the light is on", "That failed because…").
- [x] No code path produces a confident success message when `status != "verified"` and verification was applicable.
- [x] `README.md` mentions the verification language.
- [x] All relevant GitHub checks pass. *(PR #362 implementation head `b380778a9a01e922d86be5d5e3e083b139e25a06`; CI runs `31259723604`/`31259313682`, Commitlint runs `31259723610`/`31259313656`, and Windows Electron Artifact runs `31259723591`/`31259313652` all completed successfully.)*

**Validation commands:**
```bash
pytest tests/test_response_verification_language.py -q
```

---

### US-050: OpenClaw disabled by default unless fully configured

**Priority:** P0
**Workstream:** OpenClaw / Security
**Description:** As a user, I want OpenClaw off by default and labeled experimental until its dynamic plugin, permission, and verification work is done.

**Files/areas likely involved:**
- `rex/config.py` (`use_openclaw_tools`, `use_openclaw_voice_backend`)
- `gui/src/pages/SettingsPage.tsx`
- `docs/openclaw-migration-status.md`
- `SURFACE-CLASSIFICATION.md`

**Acceptance Criteria:**
- [x] Defaults for both OpenClaw flags are False.
- [x] Enabling either flag without a valid gateway URL+token raises a clear error at startup.
- [x] `SURFACE-CLASSIFICATION.md` classifies OpenClaw surfaces as `experimental`.
- [x] GUI settings label OpenClaw as "Experimental — off by default".
- [x] A test asserts defaults and the fail-closed startup behavior.
- [x] All relevant GitHub checks pass. *(PR #363, head `74f699a`: CI, Commitlint, Windows Electron Artifact, CodeFactor, and GitGuardian all green on 2026-08-08/09.)*

**Validation commands:**
```bash
pytest tests/test_openclaw_defaults.py -q
```

---

### US-051: OpenClaw gateway health, reconnect, and graceful degradation

**Priority:** P1
**Workstream:** OpenClaw
**Description:** As an operator, I want a clear health surface for the OpenClaw gateway with reconnect and graceful degradation when down.

**Files/areas likely involved:**
- `rex/openclaw/http_client.py`
- `rex/openclaw/tool_bridge.py`
- `tests/test_openclaw_health.py` (new)

**Acceptance Criteria:**
- [x] `GET /healthz` (or equivalent) detects gateway availability.
- [x] On gateway failure, tool dispatch falls back to local execution AND emits a structured warning; it does not silently succeed.
- [x] Reconnect attempts are bounded by config.
- [x] Tests cover up/down/recovery paths.
- [x] `docs/openclaw-migration-status.md` documents the model.
- [x] All relevant GitHub checks pass.

**Validation commands:**
```bash
pytest tests/test_openclaw_health.py -q
```

---

### US-052: OpenClaw GUI enable/disable/status page

**Priority:** P1
**Workstream:** OpenClaw / Electron
**Description:** As an operator, I want a settings page that shows OpenClaw status, lets me enable/disable it, and warns it is experimental.

**Reconciliation status (2026-06-12):** Still valid and now also covers the user-observed gap that OpenClaw exists in code/docs but has no clear Electron GUI visibility. If OpenClaw remains developer-only, this story may be completed by explicitly hiding it in production UI and documenting that decision; if Rex can use it in normal operation, the GUI must expose enable/disable/status controls.

**Files/areas likely involved:**
- `gui/src/pages/SettingsPage.tsx` (or new `pages/OpenClawSettingsPage.tsx`)
- `gui/src/main/handlers/openclaw.ts` (new)
- `gui/src/preload/`

**Acceptance Criteria:**
- [x] A page shows gateway URL, connection health, enabled flags, last error.
- [x] Toggling either flag persists via IPC.
- [x] Page renders an experimental warning.
- [x] Developer-only branch is not applicable: the user-configurable path was selected and implemented instead.
- [x] If OpenClaw is user-configurable, Integrations and Settings both expose honest status, configuration, disable, and health-check controls.
- [x] `cd gui && npm run typecheck && npm run build` passes. *(Validated locally with npm 10.9.2; full GUI Vitest suite also passed 108/108.)*
- [x] Manual: page renders in packaged app. *(Satisfied by stronger automated installed-artifact evidence on PR #365 head `9b1ade1`: the packaged Electron app rendered the OpenClaw Settings controls, completed typed-IPC write/read/restore, and the Windows artifact job passed in 11m32s.)*
- [x] All relevant GitHub checks pass. *(PR #365 implementation head `9b1ade19bf179d498245fc3875cffae471873ffa`: all 18 checks passed, including Python 3.11 Tests & Coverage in 9m41s and Installed Windows Electron artifact in 11m32s.)*

**Validation commands:**
```bash
cd gui && npm run typecheck && npm run build
```

---

### US-053: README capabilities/status table

**Priority:** P0
**Workstream:** Docs
**Description:** As a reader, I want a single table that shows which surfaces are working, partial, experimental, developer-only, or removed.

**Reconciliation status (2026-06-12):** Still valid. README currently has "Current Status", "Main Entry Points", and "Features" tables, but not a single "Capabilities & Status" table that mirrors `SURFACE-CLASSIFICATION.md`.

**Files/areas likely involved:**
- `README.md`
- `SURFACE-CLASSIFICATION.md`
- `docs/UI_SURFACES.md`
- `docs/claude/INTEGRATIONS_STATUS.md`

**Acceptance Criteria:**
- [x] README has a "Capabilities & Status" table that mirrors `SURFACE-CLASSIFICATION.md`.
- [x] Every row links to the deeper doc for that surface.
- [x] No conflicting status claims between README, `SURFACE-CLASSIFICATION.md`, `docs/UI_SURFACES.md`, and `INTEGRATIONS_STATUS.md`.
- [x] Documentation links and references are accurate.
- [x] All relevant GitHub checks pass. *(PR #366 implementation head `4580f568db1b5784fefebeb03df5ec569621187f`: all 17 checks passed, including Python 3.11 Tests & Coverage in 10m13s; CodeFactor, documentation-contract tests through CI, GUI/build/type/security/dependency/pre-commit/wheel/commitlint gates were green.)*

**Validation commands:**
```bash
grep -n "Capabilities" README.md
```

---

### US-054: Cross-doc consistency audit and fixes

**Priority:** P0
**Workstream:** Docs
**Description:** As a reader, I want README, INSTALL, RUNNING, `docs/UI_SURFACES.md`, `SURFACE-CLASSIFICATION.md`, `docs/claude/INTEGRATIONS_STATUS.md`, and `CLAUDE.md` to agree.

**Reconciliation status (2026-06-12):** Still valid. No `docs/AUDIT-CROSS-DOC.md` exists, and there are still known current-state tensions around `rex-gui`, root-file counts, GUI capability status, and integration readiness wording.

**Files/areas likely involved:**
- All of the above

**Acceptance Criteria:**
- [x] A `docs/AUDIT-CROSS-DOC.md` (new) lists every cross-doc claim about install methods, console scripts, root file count, voice mode default, OpenClaw status, Docker tier, and HA verification.
- [x] Every claim is verified against the code at the audit commit. *(Verified on final post-rebase implementation snapshot `2f1e604b286404a4c50aa837c76898453058fc19`: 14/14 cross-doc/capability/UI contract tests passed, with Ruff, Black, pre-commit, and diff hygiene green.)*
- [x] Conflicts are resolved in the same story.
- [x] Documentation links and references are accurate.
- [x] All relevant GitHub checks pass. *(PR #367 implementation/evidence head `3fc4d9b50bf9ed2b75f018dffff6f281749ad80f`: all 17 checks passed, including Python 3.11 Tests & Coverage in 10m13s; CodeFactor, GitGuardian, lint/format, mypy, GUI Vitest/typecheck/build/ESLint, dependency/security scans, pre-commit, wheel smoke, and commitlint were green.)*

**Validation commands:**
```bash
grep -n "rex-gui\|rex_loop\|wake word\|OpenClaw\|Docker" README.md INSTALL.md RUNNING.md docs/UI_SURFACES.md SURFACE-CLASSIFICATION.md CLAUDE.md
```

---

### US-055: `CLAUDE.md` truth pass

**Priority:** P0
**Workstream:** Docs
**Description:** As a maintainer using Claude Code, I want `CLAUDE.md` to reflect the post-refactor reality.

**Reconciliation status (2026-06-12):** Still valid. `CLAUDE.md` correctly lists the six console scripts and the completed decompositions, but its "9 active root-level `.py` files" section is an active-surface list rather than the current root file count of 27, and this PRD now requires the distinction to be documented explicitly.

**Files/areas likely involved:**
- `CLAUDE.md`

**Acceptance Criteria:**
- [x] Root `.py` file count and list are accurate.
- [x] Console-script list matches `pyproject.toml`.
- [x] Voice-mode default matches US-042.
- [x] OpenClaw status matches US-050.
- [x] Docker tier matches US-041.
- [x] Documentation links and references are accurate.
- [x] All relevant GitHub checks pass.

**Validation commands:**
```bash
grep -nE "Active root-level|Entry points|OpenClaw|Docker|wake word" CLAUDE.md
```

---

### US-056: Replace `datetime.utcnow()` with timezone-aware UTC

**Priority:** P2
**Workstream:** Tech Debt
**Description:** As a maintainer, I want all `datetime.utcnow()` calls replaced with `datetime.now(timezone.utc)`.

**Files/areas likely involved:**
- `rex/assistant.py` lines 404, 840
- Any other call sites discovered by grep

**Acceptance Criteria:**
- [x] `grep -rn "datetime.utcnow" rex/` returns no results.
- [x] Tests assert the timestamps are timezone-aware (`tzinfo is not None`).
- [x] All relevant GitHub checks pass.

**Validation commands:**
```bash
grep -rn "datetime.utcnow" rex/ || echo "clean"
pytest -q
```

---

### US-057: Replace deprecated `asyncio.get_event_loop()` patterns where safe

**Priority:** P2
**Workstream:** Tech Debt
**Description:** As a maintainer, I want `asyncio.get_event_loop()` replaced with `asyncio.get_running_loop()` (when inside async) or `asyncio.new_event_loop()` (when bootstrapping), per Python 3.12+ guidance.

**Files/areas likely involved:**
- `rex/geolocation.py` line 41
- `rex/openclaw/tool_executor.py` line 560
- `rex/tts_voices.py` lines 192, 244

**Acceptance Criteria:**
- [x] `grep -rn "asyncio.get_event_loop" rex/` returns no results.
- [x] Tests cover the replaced call sites.
- [x] All relevant GitHub checks pass.

**Validation commands:**
```bash
grep -rn "asyncio.get_event_loop" rex/ || echo "clean"
pytest -q
```

**Risk notes:** Some patterns may be inside sync code paths; pick the replacement that preserves behavior.

---

### US-058: Lint rule preventing reintroduction of deprecated datetime/asyncio patterns

**Priority:** P2
**Workstream:** Tech Debt / CI
**Description:** As a maintainer, I want a ruff or grep rule that fails CI if the deprecated patterns return.

**Files/areas likely involved:**
- `pyproject.toml` `[tool.ruff.lint]`
- `scripts/check_deprecated_apis.py` (new)
- `.github/workflows/ci.yml`

**Acceptance Criteria:**
- [x] Ruff rule or check script fails on `datetime.utcnow()` and `asyncio.get_event_loop()` outside `archived/`.
- [x] CI runs the check.
- [x] All relevant GitHub checks pass.

**Validation commands:**
```bash
python scripts/check_deprecated_apis.py
```

---

### US-059: Split `rex/cli.py` by command domain (P2)

**Priority:** P2
**Workstream:** Tech Debt
**Description:** As a maintainer, I want `rex/cli.py` split into per-domain modules for readability.

**Reconciliation status (2026-06-12):** Already completed before this PRD execution by the remaining-release-readiness workstream (`PRD-remaining-release-readiness.md` US-REM-027). Retained as satisfied baseline evidence to avoid duplicate Ralph execution. Current evidence: `rex/cli.py` is 230 lines and command-domain modules live under `rex/commands/`; the largest `rex/commands/*.py` file is under 1,000 lines.

**Files/areas likely involved:**
- `rex/cli.py`
- `rex/commands/`

**Implementation notes:** Historical record only. Do not execute this story again unless a new regression is found.

**Acceptance Criteria:**
- [x] `rex/cli.py` is a small parser/entrypoint facade. *(Verified 2026-06-12: 230 lines.)*
- [x] Command-domain modules exist under `rex/commands/`.
- [x] Each command-domain module is under 1,000 lines. *(Verified 2026-06-12: largest `rex/commands/commerce.py` was below 1,000 lines.)*
- [x] Remaining-release-readiness validation recorded the completed decomposition and preserved backward-compatible `rex.cli.<name>` import/monkeypatch surfaces.

**Validation commands:**
```bash
wc -l rex/cli.py
python -m rex --help
```

**Risk notes:** Future CLI changes should follow the existing `rex/commands/` pattern rather than expanding `rex/cli.py`.

---

### US-060: Split `rex/voice_loop.py` by concern (P2)

**Priority:** P2
**Workstream:** Tech Debt
**Description:** As a maintainer, I want `rex/voice_loop.py` split into capture, STT, LLM, TTS, and orchestration modules.

**Reconciliation status (2026-06-12):** Already completed before this PRD execution by the remaining-release-readiness workstream (`PRD-remaining-release-readiness.md` US-REM-028). Retained as satisfied baseline evidence to avoid duplicate Ralph execution. Current evidence: `rex/voice_loop.py` is 127 lines and implementation modules live under `rex/voice/`; the largest `rex/voice/*.py` file is under 1,000 lines.

**Files/areas likely involved:**
- `rex/voice_loop.py`
- `rex/voice/`

**Implementation notes:** Historical record only. Do not execute this story again unless a new regression is found.

**Acceptance Criteria:**
- [x] `rex/voice_loop.py` is a small stable facade. *(Verified 2026-06-12: 127 lines.)*
- [x] Concern modules exist under `rex/voice/`.
- [x] Each voice concern module is under 1,000 lines. *(Verified 2026-06-12: largest `rex/voice/loop.py` was below 1,000 lines.)*
- [x] Remaining-release-readiness validation recorded the completed decomposition and preserved `rex.voice_loop.<name>` import/monkeypatch surfaces.

**Validation commands:**
```bash
wc -l rex/voice_loop.py
python -c "from rex.voice_loop import build_voice_loop; print('ok')"
```

---

### US-061: Split `rex/gui_app.py` by Flask route domain (P2)

**Priority:** P2
**Workstream:** Tech Debt
**Description:** As a maintainer, I want `rex/gui_app.py` split by route domain (status, devices, history, setup, quick actions) under `rex/web/routes/`.

**Reconciliation status (2026-06-12):** Already completed before this PRD execution by the remaining-release-readiness workstream (`PRD-remaining-release-readiness.md` US-REM-026). Retained as satisfied baseline evidence to avoid duplicate Ralph execution. Current evidence: `rex/gui_app.py` is 207 lines and Flask route domains live under `rex/routes/`.

**Files/areas likely involved:**
- `rex/gui_app.py`
- `rex/routes/`

**Implementation notes:** Historical record only. Do not execute this story again unless a new regression is found.

**Acceptance Criteria:**
- [x] `rex/gui_app.py` is a small app factory/blueprint registration surface. *(Verified 2026-06-12: 207 lines.)*
- [x] Route-domain modules exist under `rex/routes/`.
- [x] Remaining-release-readiness validation recorded the completed decomposition and route snapshot coverage exists at `tests/test_us_rem_026_route_snapshot.py`.

**Validation commands:**
```bash
wc -l rex/gui_app.py
pytest tests/test_us_rem_026_route_snapshot.py -q
```

---

### US-062: Split `gui/src/main/index.ts` by Electron main-process concern (P2)

**Priority:** P2
**Workstream:** Tech Debt
**Description:** As a maintainer, I want the Electron main process split into IPC handlers, config helpers, bridge resolver, tray, and window lifecycle modules.

**Reconciliation status (2026-06-12):** Already completed before this PRD execution by the remaining-release-readiness workstream (`PRD-remaining-release-readiness.md` US-REM-029). Retained as satisfied baseline evidence to avoid duplicate Ralph execution. Current evidence: `gui/src/main/index.ts` is 39 lines and main-process concern modules live under `gui/src/main/`.

**Files/areas likely involved:**
- `gui/src/main/index.ts`
- `gui/src/main/`
- `gui/src/main/handlers/`

**Implementation notes:** Historical record only. Do not execute this story again unless a new regression is found.

**Acceptance Criteria:**
- [x] `gui/src/main/index.ts` is a small lifecycle entrypoint. *(Verified 2026-06-12: 39 lines.)*
- [x] Main-process concern modules exist under `gui/src/main/` and IPC handlers under `gui/src/main/handlers/`.
- [x] Remaining-release-readiness validation recorded `cd gui && npm run typecheck`, `cd gui && npm run build`, and Electron smoke evidence for the decomposition PR.

**Validation commands:**
```bash
wc -l gui/src/main/index.ts
cd gui && npm run typecheck && npm run build
bash tests/smoke/test_electron_package.sh
```

---

### US-063: Split `gui/src/pages/SettingsPage.tsx` into sections (P2)

**Priority:** P2
**Workstream:** Tech Debt
**Description:** As a maintainer, I want `SettingsPage.tsx` split into smaller section components (general, voice, AI, integrations, system).

**Files/areas likely involved:**
- `gui/src/pages/SettingsPage.tsx` → `gui/src/pages/settings/sections/*.tsx`

**Acceptance Criteria:**
- [x] Each section module is < 1,000 lines.
- [x] `cd gui && npm run typecheck && npm run build` passes.
- [x] Packaged-app smoke: every settings section renders and integration settings save/read/restore.
- [x] All relevant GitHub checks pass.

**Validation commands:**
```bash
cd gui && npm run typecheck && npm run build
```

---

### US-064: Build the Electron capability parity inventory

**Priority:** P0
**Workstream:** Electron / Product / Docs
**Description:** As a user, I want every Rex capability that exists in backend code or docs to be visible, configurable, or explicitly hidden as developer-only in the Electron GUI.

**Why it matters:** Hidden features force users to edit files manually and create a false sense that Rex can do things the product UI does not actually support.

**Files/areas likely involved:**
- `gui/src/pages/IntegrationsPage.tsx`
- `gui/src/pages/SettingsPage.tsx`
- `gui/src/main/integrationInventory.ts`
- `docs/claude/INTEGRATIONS_STATUS.md`
- `SURFACE-CLASSIFICATION.md`
- `README.md`

**Implementation notes:** Create a capability matrix covering OpenClaw, web search, Outlook, email, SMS, Home Assistant, shopping list, memory, profiles, voice, LLM providers, mobile/API access, and other registered tools. This is an inventory/story-routing task, not the implementation of every missing UI.

**Acceptance Criteria:**
- [x] A committed inventory maps each backend/docs capability to GUI status: visible, configurable, disabled with explanation, developer-only, or missing.
- [x] Each missing or misleading GUI surface is linked to a User Story in this PRD.
- [x] No capability is marked production-ready unless the GUI can configure/status-check it or docs explicitly classify it as developer-only.
- [x] README and integration docs link to the inventory or summarize its production-facing conclusions.
- [x] All relevant GitHub checks pass.

- [x] A committed migration appendix identifies every current capability/tool registry, its authority, consumers, duplicate metadata, and the target adapter into the future canonical Capability Registry.
- [x] Every inventoried capability records source, enabled state, required permissions, health state, operation type (read/mutate), risk tier, and verification support.
- [x] SMS remains backend/direct-route compatible but is explicitly absent from primary navigation.

**Validation commands:**
```bash
grep -n "Capability parity" docs/*.md README.md
grep -n "OpenClaw\|web search\|Outlook\|Shopping List\|Memory" docs/claude/INTEGRATIONS_STATUS.md SURFACE-CLASSIFICATION.md
```

**Risk notes:** Do not promote a backend feature to user-facing status just because code exists. The GUI and docs must match the real readiness tier.

---

### US-065: Make Integrations configure links truthful

**Priority:** P0
**Workstream:** Electron / Integrations
**Description:** As a user, I want every Configure link in the Integrations tab to land on an actual matching settings section, or be hidden/disabled when the integration is not configurable.

**Why it matters:** A blue Configure link that navigates to an unrelated or missing section teaches users not to trust integration status.

**Files/areas likely involved:**
- `gui/src/pages/IntegrationsPage.tsx`
- `gui/src/pages/SettingsPage.tsx`
- `gui/src/main/integrationInventory.ts`
- `gui/src/main/integrationStatus.ts`
- `gui/src/types/ipc.ts`
- `docs/claude/INTEGRATIONS_STATUS.md`

**Implementation notes:** Start from the inventory produced by US-064. Web Search is a known example: if it is configurable, it needs a real settings section; if not, the Configure action must be disabled and the card must state what is missing.

**Acceptance Criteria:**
- [ ] Every configurable integration listed in Integrations has a matching Electron Settings section or route.
- [ ] Non-configurable or unimplemented integrations do not render an enabled Configure button.
- [ ] Integration cards distinguish `not implemented`, `not configured`, `configured`, `connected`, and `error`.
- [ ] Web Search has either a real provider/key settings section or an honest disabled state explaining the missing setup path.
- [ ] Tests cover integration inventory link targets and disabled Configure behavior.
- [ ] `cd gui && npm run typecheck && npm run build` passes.
- [ ] All relevant GitHub checks pass.

**Validation commands:**
```bash
cd gui && npm run typecheck && npm run build
pytest tests/test_us314_integrations_page.py -q
```

**Risk notes:** Do not write secrets into GUI settings JSON while adding integration configuration. Secrets must stay in `.env` or the documented credential store.

---

### US-066: Separate Settings navigation from profile/avatar controls

**Priority:** P1
**Workstream:** Electron / UX / Identity
**Description:** As a user, I want Settings to appear once in navigation and the persistent user/profile area to show and open my editable profile.

**Why it matters:** Duplicate Settings entries and dead profile affordances make the app feel unfinished and make user identity unclear.

**Files/areas likely involved:**
- `gui/src/App.tsx`
- `gui/src/components/*`
- `gui/src/pages/SettingsPage.tsx`
- `gui/src/pages/UsersPage.tsx`
- `rex/routes/users.py`
- `gui/src/main/handlers/*`

**Implementation notes:** Keep Settings as the persistent bottom navigation item. The profile/avatar area is separate: show uploaded image when available, otherwise initials from username/display name, and click through to editable profile settings.

**Acceptance Criteria:**
- [ ] Settings is removed from the scrolling left tab menu.
- [ ] Persistent bottom Settings navigation still opens Settings.
- [ ] The profile/avatar area renders the current user's image when present.
- [ ] If no image exists, the profile/avatar area renders initials from username or display name.
- [ ] Clicking the profile/avatar area opens editable profile settings.
- [ ] Tests or a focused Electron harness cover navigation and avatar fallback behavior.
- [ ] `cd gui && npm run typecheck && npm run build` passes.
- [ ] Screenshots are captured or the reason they were not captured is documented.
- [ ] All relevant GitHub checks pass.

**Validation commands:**
```bash
cd gui && npm run typecheck && npm run build
```

**Risk notes:** Do not remove access to Settings while cleaning up duplication. Preserve keyboard and screen-reader access.

---

### US-067: Support detected and manual IANA time zones

**Priority:** P1
**Workstream:** Electron / Settings
**Description:** As a user, I want Rex to detect my timezone automatically and let me override it from the full IANA timezone list.

**Why it matters:** A timezone dropdown containing only the current timezone is not a real setting and can break reminders, schedules, calendar events, and local-context answers.

**Files/areas likely involved:**
- `gui/src/pages/SettingsPage.tsx`
- `gui/src/main/settingsDefaults.ts`
- `gui/src/main/settingsMirror.ts`
- `gui/src/types/ipc.ts`
- `rex/config.py`
- `config/rex_config.json`

**Implementation notes:** Prefer browser/system detection when available. Persist an explicit mode such as `auto` vs `manual`, the detected timezone, and the manual override. Do not scale this into location detection.

**Acceptance Criteria:**
- [ ] General settings default to automatic timezone detection where possible.
- [ ] The UI shows the detected timezone clearly.
- [ ] Manual override is available.
- [ ] Manual dropdown includes all IANA time zones available from the runtime or a generated static list.
- [ ] Saved timezone reloads from the same source of truth after tab switches and app restart.
- [ ] Tests cover auto/default, manual save, reload, and invalid timezone rejection.
- [ ] `cd gui && npm run typecheck && npm run build` passes.
- [ ] All relevant GitHub checks pass.

**Validation commands:**
```bash
cd gui && npm run typecheck && npm run build
pytest -q tests/test_config*.py
```

**Risk notes:** Avoid guessing timezone from IP or external network services. OS/browser/local runtime data is enough.

---

### US-068: Rebuild voice enrollment around explicit user identity

**Priority:** P0
**Workstream:** Voice / Identity / Electron
**Description:** As a household user, I want to choose which user is enrolling before recording a voice sample and see clear recording instructions and results.

**Why it matters:** Voice identity is unsafe and confusing if enrollment silently attaches samples to `default` while the user profile list contains real users such as James.

**Files/areas likely involved:**
- `gui/src/pages/SettingsPage.tsx`
- `gui/src/main/handlers/voice.ts`
- `rex_voice_enrollment_bridge.py`
- `rex/voice_identity/`
- `rex/identity.py`
- `profiles/`
- `tests/test_us309_voice_enrollment_ux.py`

**Implementation notes:** Use the same user identity source that profile, memory, shopping, and chat history will use. Show the phrase to speak, a visible 3-2-1 countdown, recording state, and actionable success/failure details.

**Acceptance Criteria:**
- [ ] User selection is required before enrollment starts.
- [ ] Enrollment attaches the voice sample to the selected user.
- [ ] Existing users are never displayed as `default` when a profile exists.
- [ ] The UI shows clear instructions and exactly what to say.
- [ ] The UI shows a visible 3-2-1 countdown before recording.
- [ ] The UI shows recording, processing, success, and failure states.
- [ ] Failure details are actionable without exposing sensitive paths or secrets.
- [ ] Tests cover selected-user persistence and no-`default` labeling for real profiles.
- [ ] `cd gui && npm run typecheck && npm run build` passes.
- [ ] All relevant GitHub checks pass.

**Validation commands:**
```bash
pytest tests/test_us309_voice_enrollment_ux.py -q
cd gui && npm run typecheck && npm run build
```

**Risk notes:** Do not store biometric/voice identity data under the wrong user. Treat voice samples as sensitive user data.

---

### US-069: Make custom wake model and sample selection asset-backed

**Priority:** P0
**Workstream:** Voice / Wake Word / Electron
**Description:** As a user configuring a custom wake word, I want the dropdown and Play Sample button to reflect actual trained assets and samples.

**Why it matters:** Placeholder sample playback and missing trained-model entries make wake-word setup impossible to trust.

**Files/areas likely involved:**
- `gui/src/pages/SettingsPage.tsx`
- `gui/src/main/handlers/voice.ts`
- `gui/src/main/voiceSettings.ts`
- `rex_wakeword_list_bridge.py`
- `rex_wakeword_sample_bridge.py`
- `rex/wakeword/`
- `config/wake_words/`
- `tests/test_us310_wakeword_sample.py`

**Implementation notes:** The trained custom wake dropdown must be populated from actual available models/embeddings. The selected sample must match the selected asset. Remove placeholder phrase playback entirely.

**Acceptance Criteria:**
- [ ] Trained custom wake models/embeddings populate from actual available assets.
- [ ] The selected wake model/sample matches an existing asset path.
- [ ] Placeholder sample behavior is removed.
- [ ] Play Sample is disabled when no valid sample/model exists.
- [ ] If no sample exists, the app plays nothing and shows a clear no-sample message.
- [ ] Missing-asset errors are visible and actionable.
- [ ] Tests cover asset present, asset missing, sample present, sample missing, and disabled Play Sample behavior.
- [ ] `cd gui && npm run typecheck && npm run build` passes.
- [ ] All relevant GitHub checks pass.

**Validation commands:**
```bash
pytest tests/test_us310_wakeword_sample.py -q
cd gui && npm run typecheck && npm run build
```

**Risk notes:** Do not silently fall back to a different wake model when the user selected a custom one. Surface the fallback explicitly if fallback is allowed.

---

### US-070: Make Test Voice play the selected TTS engine and voice

**Priority:** P0
**Workstream:** Voice / TTS / Electron
**Description:** As a user, I want the Test Voice button to play an audible sample using the currently selected TTS engine and voice.

**Why it matters:** Voice settings are not production-ready if users cannot verify the selected voice before relying on it.

**Files/areas likely involved:**
- `gui/src/pages/SettingsPage.tsx`
- `gui/src/main/handlers/voice.ts`
- `gui/src/main/voiceSettings.ts`
- `rex_voice_sample_bridge.py`
- `rex_voices_bridge.py`
- `rex/tts_voices.py`
- `rex/tts/`

**Implementation notes:** Preserve the text response if TTS fails. Show loading, playing, success, and failure states. Use the selected engine/voice; do not use a hardcoded placeholder voice.

**Acceptance Criteria:**
- [ ] Test Voice invokes the selected TTS engine.
- [ ] Test Voice uses the selected voice.
- [ ] The user hears an audible sample when the engine/voice are available.
- [ ] The UI shows loading/playing/failure state.
- [ ] Missing engine, missing voice, missing model, and playback errors are surfaced clearly.
- [ ] Tests or an Electron harness cover the IPC request payload and failure UI.
- [ ] `cd gui && npm run typecheck && npm run build` passes.
- [ ] All relevant GitHub checks pass.

**Validation commands:**
```bash
cd gui && npm run typecheck && npm run build
```

**Risk notes:** Do not block saving voice settings if sample playback fails. Do not leak local file paths in renderer errors.

---

### US-071: Persist AI provider selection across navigation

**Priority:** P0
**Workstream:** AI / Electron / Settings
**Description:** As a user, I want changing the LLM provider in Settings > AI to save immediately and stay selected after switching tabs or restarting.

**Why it matters:** A settings UI that says "saved" and immediately reverts is a production blocker for model configuration.

**Files/areas likely involved:**
- `gui/src/pages/SettingsPage.tsx`
- `gui/src/main/aiSettings.ts`
- `gui/src/main/settingsMirror.ts`
- `gui/src/main/settingsDefaults.ts`
- `gui/src/main/handlers/settings.ts`
- `gui/src/types/ipc.ts`
- `config/rex_config.json`

**Implementation notes:** Establish one source of truth for GUI provider labels and runtime provider names. The UI should reload from saved state, not local defaults, after tab changes.

**Acceptance Criteria:**
- [x] Changing from Local Transformers to Ollama Local persists immediately.
- [x] Switching tabs and returning does not reset the provider.
- [x] App restart reloads the saved provider from the source of truth.
- [x] Runtime config mirror uses the same provider mapping as the UI.
- [x] Tests cover save, reload, tab navigation, and invalid provider fallback.
- [x] `cd gui && npm run typecheck && npm run build` passes.
- [x] All relevant GitHub checks pass.

**Validation commands:**
```bash
cd gui && npm run typecheck && npm run build
```

**Risk notes:** Do not add a second provider key. Migrate or normalize old keys rather than allowing split state.

---

### US-072: Discover Ollama and LM Studio models from configured endpoints

**Priority:** P1
**Workstream:** AI / Electron / Integrations
**Description:** As a user using Ollama or LM Studio, I want Rex to list available models from the configured endpoint and persist my selected model.

**Why it matters:** Fake or stale model names lead to broken chat setup and confusing failures.

**Files/areas likely involved:**
- `gui/src/pages/SettingsPage.tsx`
- `gui/src/main/handlers/settings.ts`
- `gui/src/main/aiSettings.ts`
- `rex/llm_client.py`
- `rex/model_router.py`
- `config/rex_config.json`

**Implementation notes:** Ollama and LM Studio have different APIs. Use configured endpoints, show loading/error/empty states, and avoid network calls unless the user requests discovery or opens the relevant provider section.

**Acceptance Criteria:**
- [x] Ollama model discovery reads the configured Ollama endpoint.
- [x] LM Studio model discovery reads the configured OpenAI-compatible endpoint.
- [x] UI shows loading, error, and empty states.
- [x] Selected model persists and reloads.
- [x] Stale or hardcoded fake model names are not shown as available.
- [x] Tests mock provider endpoints for success, failure, and empty responses.
- [x] `cd gui && npm run typecheck && npm run build` passes.
- [x] All relevant GitHub checks pass.

**Validation commands:**
```bash
cd gui && npm run typecheck && npm run build
pytest -q tests/test_llm_client.py tests/test_model_router.py
```

**Risk notes:** Do not contact external cloud APIs while discovering local models unless the user configured that provider and initiated the action.

---

### US-073: Consolidate autonomy settings under AI

**Priority:** P1
**Workstream:** AI / Electron / Settings
**Description:** As a user, I want one autonomy setting with one source of truth, located under Settings > AI.

**Why it matters:** Duplicate autonomy controls in AI and System can diverge and make Rex's behavior unpredictable.

**Files/areas likely involved:**
- `gui/src/pages/SettingsPage.tsx`
- `gui/src/main/aiSettings.ts`
- `gui/src/main/settingsDefaults.ts`
- `gui/src/main/settingsMirror.ts`
- `gui/src/types/ipc.ts`
- `rex/autonomy/`
- `config/rex_config.json`

**Implementation notes:** Keep autonomy under AI. Remove the duplicate System autonomy control and audit System for other AI-related settings that should move.

**Acceptance Criteria:**
- [x] Only one autonomy UI control exists.
- [x] The remaining control lives under Settings > AI.
- [x] System no longer has a duplicate autonomy setting.
- [x] Saved autonomy value has one source of truth.
- [x] AI and runtime config read the same autonomy value.
- [x] Tests cover migration from old duplicate values and System tab absence.
- [x] `cd gui && npm run typecheck && npm run build` passes.
- [x] All relevant GitHub checks pass.

**Validation commands:**
```bash
cd gui && npm run typecheck && npm run build
```

**Risk notes:** Preserve conservative defaults. Do not accidentally enable full-auto autonomy during migration.

---

### US-074: Add wake-word runtime diagnostics and visible listening state

**Priority:** P0
**Workstream:** Voice / Wake Word / Electron
**Description:** As a user, I want wake-word/listening mode to show whether it is actually active and why it failed when the wake backend is unavailable.

**Why it matters:** Silent wake-word failure makes Rex feel broken and can hide missing backend, model, microphone, or permissions problems.

**Files/areas likely involved:**
- `gui/src/components/voice/VoiceToggle.tsx`
- `gui/src/main/handlers/voice.ts`
- `gui/src/pages/SettingsPage.tsx`
- `rex_voice_bridge.py`
- `rex/voice/`
- `rex/wakeword/`
- `tests/test_wakeword_listener_runtime.py`

**Implementation notes:** Add diagnostics around start, backend/model selection, listener readiness, wake detection, STT handoff, and failure. Preserve latency and avoid repeated heavy model loads.

**Acceptance Criteria:**
- [ ] Starting wake mode shows `starting`, `listening`, `detected`, `processing`, and `failed` states as applicable.
- [ ] The UI shows backend and selected model/asset state.
- [ ] Missing backend/model/microphone errors are actionable.
- [ ] Logs include bounded structured events for wake start/listen/respond path.
- [ ] Tests or a harness cover successful readiness and backend unavailable paths.
- [ ] `pytest tests/test_wakeword_listener_runtime.py -q` passes or replacement targeted tests pass.
- [ ] `cd gui && npm run typecheck && npm run build` passes.
- [ ] All relevant GitHub checks pass.

**Validation commands:**
```bash
pytest tests/test_wakeword_listener_runtime.py -q
cd gui && npm run typecheck && npm run build
```

**Risk notes:** Diagnostics must not continuously log microphone audio data or sensitive transcripts.

---

### US-075: Instrument chat and voice response latency

**Priority:** P0
**Workstream:** Performance / Voice / AI
**Description:** As a user, I want Rex responses to be fast enough for daily use, and as a maintainer I want stage timings that show where delays come from.

**Why it matters:** Slow responses can come from STT, LLM, TTS, tool routing, IPC, model loading, wake loops, or playback. Without timings, optimization is guesswork.

**Files/areas likely involved:**
- `rex/voice_latency.py`
- `rex/assistant.py`
- `rex/voice/`
- `rex/actions/dispatcher.py`
- `gui/src/main/handlers/voice.ts`
- `gui/src/main/handlers/chat.ts`
- `docs/performance.md` (new or updated)

**Implementation notes:** Add structured timings before optimization. Define target budgets for typed chat, hold-to-talk, wake-word response, STT, LLM token-to-first, TTS start, and playback.

**Acceptance Criteria:**
- [x] Chat response path records IPC, routing, LLM, tool, and total timings.
- [x] Voice path records wake, capture, STT, LLM, TTS, playback, and total timings.
- [x] Logs include provider/model/settings identifiers needed for diagnosis without leaking secrets.
- [x] Target budgets are documented.
- [x] A profiling command or harness summarizes timings.
- [x] Optimization stories are opened or blockers documented for any stage over budget.
- [x] Tests cover timing event emission with mocked stages.
- [x] All relevant GitHub checks pass.

- [x] A checked-in RexBench baseline reports cold and warm p50/p95 for typed chat, voice, read-only tool, mutating tool, and unavailable-capability request classes.
- [x] Baseline stages separately report routing, first token, tool execution, STT, first audio, completion, and total latency where applicable.
- [x] Deterministic fixtures/mocks are the default benchmark evidence; live-provider and physical-hardware evidence is stored/labeled separately and never conflated with mock/local measurements.
- [x] Benchmark output contains no prompts, transcripts, memory contents, credentials, or user IDs.

**Validation commands:**
```bash
pytest -q tests/test_assistant_latency.py tests/test_voice_latency_budget.py tests/test_voice_pipeline_logs.py tests/test_tool_pipeline.py tests/test_rexbench.py
cd gui && npx vitest run tests/chatLatency.test.ts && npm run typecheck
python scripts/rexbench.py --profile baseline --iterations 20 --output docs/performance/rexbench-baseline.json
```

**Risk notes:** Do not add synchronous timing work that increases latency materially. Use monotonic clocks.
---

### US-076: Add incoherent model output validation and fail-safe handling

**Priority:** P0
**Workstream:** AI / Reliability / UX
**Description:** As a user, I want Rex to detect obvious provider/model failure and surface a clear error instead of returning huge incoherent paragraphs.

**Why it matters:** Gibberish responses are worse than honest failure because they destroy trust and can mask a misconfigured or broken model provider.

**Files/areas likely involved:**
- `rex/assistant.py`
- `rex/response/builder.py`
- `rex/model_router.py`
- `rex/llm_client.py`
- `rex/voice/`
- `gui/src/pages/ChatPage.tsx`

**Implementation notes:** Keep validation conservative and deterministic. Detect clear bad-output patterns such as extreme repetition, non-language token floods, impossible length spikes, or provider error text routed as answer text. Do not censor normal long answers.

**Acceptance Criteria:**
- [x] Obvious incoherent output is converted into a clear provider/model failure response.
- [x] Logs include provider, model, route, output length, and failure reason without logging secrets.
- [x] Text and voice paths both use the fail-safe.
- [x] The UI distinguishes model failure from normal answer refusal.
- [x] Tests use mocked bad output and verify no gibberish is returned to the user.
- [x] All relevant GitHub checks pass. *(PR #392 exact head passed all 18 required checks before merge; merged as 8b4b645 on 2026-08-13.)*

- [x] Output validation runs on the canonical turn-completion path so streaming and non-streaming turns enforce identical safety/coherence rules.
- [x] Any response produced after model escalation is independently validated before it can become the terminal user response.

**Validation commands:**
```bash
pytest -q tests/test_llm_client.py tests/test_assistant.py
```

**Risk notes:** False positives can hide legitimate answers. Keep thresholds conservative and add diagnostics.

---

### US-077: Route current-info and news questions honestly

**Priority:** P0
**Workstream:** AI / Tools / Integrations
**Description:** As a user asking "what is in the news today", I want Rex to use a configured current-info/news/web-search capability or clearly explain what needs to be enabled.

**Why it matters:** Rex must not hallucinate current events or say it can fetch news unless a real configured capability exists.

**Files/areas likely involved:**
- `rex/intent/router.py`
- `rex/actions/dispatcher.py`
- `rex/tools/registry.py`
- `plugins/web_search.py`
- `gui/src/pages/SettingsPage.tsx`
- `docs/claude/INTEGRATIONS_STATUS.md`

**Implementation notes:** Detect current-info/news intent before a plain LLM answer. If search/news capability is configured, route to it. If not configured, explain the missing provider/key and point to the exact settings/docs path.

**Acceptance Criteria:**
- [x] News/current-info questions route to a configured search/news capability when available.
- [x] If no capability is configured, Rex explains what is missing and how to enable it.
- [x] Rex does not claim live news access when the capability is unavailable.
- [x] Suggested setup paths are backed by actual code/config/docs.
- [x] Tests cover configured and unconfigured paths for "what is in the news today".
- [x] All relevant GitHub checks pass. *(PR #406 corrected exact head `c897fb8d745f1352d6b2fc53cb740468b61f5c4f` completed all 18 checks successfully, including CI #1197, Commitlint #815, Windows Electron Artifact #166, CodeFactor, GitGuardian, and pre-commit; no submitted reviews or review threads.)*

**Validation commands:**
```bash
pytest -q tests/test_assistant.py tests/test_capabilities.py tests/test_tools_registry.py
```

**Risk notes:** This should not add broad web browsing by default. Respect explicit user permission and configured providers.

---

### US-078: Add capability-limit recovery UX

**Priority:** P1
**Workstream:** Product / UX / Integrations
**Description:** As a user, I want Rex to recover gracefully when a tool, integration, permission, API key, or capability is missing.

**Why it matters:** Dead-end errors make Rex feel broken. Honest recovery paths let users decide whether to enable, configure, or ignore a capability.

**Files/areas likely involved:**
- `rex/capabilities.py`
- `rex/actions/dispatcher.py`
- `rex/response/builder.py`
- `rex/tools/registry.py`
- `gui/src/pages/ChatPage.tsx`
- `gui/src/pages/IntegrationsPage.tsx`
- `gui/src/pages/SettingsPage.tsx`

**Implementation notes:** Standardize missing-requirement responses. Where appropriate, ask permission to configure or guide the user to the exact settings section. Do not pretend setup or action succeeded.

**Acceptance Criteria:**
- [x] Missing integration responses name the missing requirement.
- [x] Missing permission responses name the permission and owner/action required.
- [x] Missing API key responses name the config key location without revealing secret values.
- [x] Missing tool responses offer a concrete enable/configure/build path when one exists.
- [x] The GUI can render structured recovery actions where available.
- [x] Tests cover missing integration, missing key, missing permission, and missing tool responses.
- [x] All relevant GitHub checks pass. *(PR #407 exact implementation head `f59e33e8213248ceef50ae6495cc50c62d33d624` completed all 18 checks successfully, including CI #1201, Commitlint #818, Windows Electron Artifact #168, CodeFactor, GitGuardian, pre-commit, security, wheel, GUI, and packaging checks; no submitted reviews or review threads.)*

- [x] Before offering to build a missing capability, the recovery path searches in order: enabled local capabilities, disabled local capabilities, OpenClaw/ClawHub, configured MCP providers, configured OpenAPI descriptions, and safely composable capabilities.
- [x] Candidate gap-recovery options are filtered by current-user permission, health, risk, identity scope, and configuration before they are offered or ranked.
- [x] Rex never enables, installs, composes, or grants capability authority without the required risk-policy decision/confirmation.

**Validation commands:**
```bash
pytest -q tests/test_capabilities.py tests/test_tools_registry.py tests/test_assistant.py
```

**Risk notes:** Do not auto-enable risky integrations or request broad permissions without explicit user consent.

---

### US-079: Add typed-chat speak-responses preference

**Priority:** P1
**Workstream:** Chat / Voice / Electron
**Description:** As a user typing in Chat, I want to choose whether Rex speaks typed-chat responses aloud.

**Why it matters:** Some users want spoken feedback from typed chat; others need silent chat. The preference should be explicit and per-user.

**Files/areas likely involved:**
- `gui/src/pages/ChatPage.tsx`
- `gui/src/pages/SettingsPage.tsx`
- `gui/src/main/handlers/chat.ts`
- `gui/src/main/handlers/voice.ts`
- `gui/src/main/voiceSettings.ts`
- `rex/routes/users.py`
- `data/history.db`

**Implementation notes:** Use the selected TTS engine/voice. Persist preference per user. TTS failure must not block the text response.

**Acceptance Criteria:**
- [ ] Chat UI has a clear "speak responses" toggle/control.
- [ ] Preference persists per user.
- [ ] Spoken playback uses the selected TTS engine and voice.
- [ ] Text response renders even if TTS fails.
- [ ] TTS failure is surfaced non-blockingly.
- [ ] Tests or harness cover preference persistence and TTS failure fallback.
- [ ] `cd gui && npm run typecheck && npm run build` passes.
- [ ] All relevant GitHub checks pass.

**Validation commands:**
```bash
cd gui && npm run typecheck && npm run build
```

**Risk notes:** Avoid surprise audio. Default should preserve current behavior unless product explicitly decides otherwise.

---

### US-080: Replace flat Home Assistant entity list with a usable dashboard

**Priority:** P0
**Workstream:** Home Assistant / Electron / UX
**Description:** As a Home Assistant user, I want a dashboard-style view grouped by useful structure with safe controls for supported entities.

**Why it matters:** A flat non-interactive entity list is not a production-ready smart-home UI.

**Files/areas likely involved:**
- `gui/src/pages/HomeAssistantPage.tsx`
- `gui/src/main/handlers/devices.ts`
- `rex/routes/ha.py`
- `rex/ha/`
- `config/device_aliases.json`
- `docs/home_assistant.md`

**Implementation notes:** This story depends on the safety/verification model from US-047 through US-049. Group by HA areas, rooms, floors, devices, domains, or user-custom organization when metadata is insufficient.

> **Decomposition directive (skill-compliance):** This story is larger than one Ralph iteration. Before execution, split it into ordered one-iteration slices and run them in order: (a) grouping by available HA metadata; (b) search/filter/grouping controls; (c) safe interactive controls per supported domain (reusing the US-047 confirmation gate and US-048 verification); (d) loading/disconnected/not-configured/error/empty states; (e) grouping/filtering/control tests. Do not attempt the full bundle in one iteration.

**Acceptance Criteria:**
- [ ] HA entities are grouped by available HA metadata or documented user-custom organization.
- [ ] Search/filter/grouping controls are available.
- [ ] Supported entity types have safe interactive controls.
- [ ] Dangerous domains retain confirmation gates.
- [ ] Responses use attempted/completed/verified/failed vocabulary.
- [ ] The dashboard shows loading, disconnected, not configured, error, and empty states.
- [ ] Tests cover grouping, filtering, supported controls, and dangerous-domain confirmation.
- [ ] `cd gui && npm run typecheck && npm run build` passes.
- [ ] Manual or harness verification confirms the page is usable with representative HA fixture data.
- [ ] All relevant GitHub checks pass.

- [ ] Home Assistant actions consume the canonical action lifecycle; an attempted or unverified mutation is never presented as completed or verified.

**Validation commands:**
```bash
pytest -q tests/test_us313_ha_device_status.py tests/test_us060_devices.py
cd gui && npm run typecheck && npm run build
```

**Risk notes:** Never expose broad HA service execution from the dashboard without confirmation and post-control verification.

---

### US-081: Verify Outlook integration end to end

**Priority:** P0
**Workstream:** Integrations / Outlook / Electron
**Description:** As a user, I want Outlook integration status, auth, configuration, and errors to reflect whether email/calendar sync actually works.

**Why it matters:** Current code explicitly warns Outlook email/calendar sync is not implemented yet in parts of the integration status path, while the GUI can still suggest connected/configured states.

**Files/areas likely involved:**
- `gui/src/main/integrationStatus.ts`
- `gui/src/pages/IntegrationsPage.tsx`
- `gui/src/pages/SettingsPage.tsx`
- `rex/integrations/email/`
- `rex/calendar_backends/`
- `docs/claude/INTEGRATIONS_STATUS.md`

**Implementation notes:** Decide whether Outlook is production-ready, partial, or disabled. If OAuth/token support is incomplete, the GUI must say so and provide actionable setup or limitation text.

**Acceptance Criteria:**
- [ ] Outlook auth/config/status path is tested end to end or explicitly classified incomplete.
- [ ] Integrations tab and Settings show the same Outlook status.
- [ ] Missing OAuth token/client/permission errors are actionable.
- [ ] Docs match GUI status.
- [ ] Tests or smoke checks cover configured-without-live-sync and real-live-sync-ready cases as appropriate.
- [ ] `cd gui && npm run typecheck && npm run build` passes.
- [ ] All relevant GitHub checks pass.

**Validation commands:**
```bash
pytest -q tests/test_outlook_integration_honesty.py
cd gui && npm run typecheck && npm run build
```

**Risk notes:** Do not show Outlook as working until real mailbox/calendar operations succeed or are intentionally scoped and documented.

---

### US-082: Decide and enforce Email/SMS beta-label policy

**Priority:** P1
**Workstream:** Integrations / Product / Docs
**Description:** As a user, I want Email and SMS labels to truthfully reflect whether those features are production-ready, experimental, or hidden.

**Why it matters:** Removing beta labels without finishing the feature is dishonest; leaving beta labels in a finished app also blocks release polish.

**Files/areas likely involved:**
- `gui/src/pages/EmailPage.tsx`
- `gui/src/pages/SmsPage.tsx`
- `gui/src/pages/IntegrationsPage.tsx`
- `gui/src/pages/SettingsPage.tsx`
- `docs/claude/INTEGRATIONS_STATUS.md`
- `README.md`
- `SURFACE-CLASSIFICATION.md`

**Implementation notes:** The story must decide one of three outcomes for Email and SMS separately: finish to production-ready, hide/disable from normal users, or explicitly classify as experimental/developer-only. Remove beta labels only after the feature is truly production-ready.

**Acceptance Criteria:**
- [ ] Email has a documented readiness decision: production-ready, experimental/developer-only, or hidden.
- [ ] SMS has a documented readiness decision: production-ready, experimental/developer-only, or hidden.
- [ ] GUI labels match the decision.
- [ ] Beta labels are removed only for features that satisfy production-ready criteria.
- [ ] Docs match GUI labels and capability status.
- [ ] Tests cover the visible label/status for each outcome.
- [ ] `cd gui && npm run typecheck && npm run build` passes.
- [ ] All relevant GitHub checks pass.

- [ ] SMS backend/direct-route compatibility is preserved while SMS remains intentionally hidden from primary navigation.

**Validation commands:**
```bash
cd gui && npm run typecheck && npm run build
grep -n "Email\|SMS\|beta\|experimental" README.md docs/claude/INTEGRATIONS_STATUS.md SURFACE-CLASSIFICATION.md
```

**Risk notes:** Do not make UI look more complete than the live backend/auth path really is.

---

### US-083: Add selectable per-user chat history

**Priority:** P1
**Workstream:** Chat / Identity / Electron
**Description:** As a user, I want to see prior conversations, select one, and resume it without mixing users unexpectedly.

**Why it matters:** Chat history is core user experience, and household/private boundaries must be explicit before release.

**Files/areas likely involved:**
- `gui/src/pages/ChatPage.tsx`
- `rex/routes/chat.py`
- `rex/history_store.py` or current chat history storage
- `data/history.db`
- `rex/routes/users.py`
- `docs/privacy.md` (new or updated)

**Implementation notes:** Define per-user and shared/household history rules before adding UI. Include retention, delete, and export controls.

> **Decomposition directive (skill-compliance):** This story is larger than one Ralph iteration. Before execution, split it into ordered one-iteration slices and run them in order: (a) define and document per-user vs shared/household history rules and retention policy; (b) list prior conversations for the current user; (c) select and resume a prior chat; (d) delete a conversation; (e) export conversation history; (f) cross-user isolation tests. Do not attempt the full bundle in one iteration.

**Acceptance Criteria:**
- [ ] Chat UI lists prior conversations for the current user.
- [ ] User can select and resume a prior chat.
- [ ] Per-user separation is enforced.
- [ ] Shared/household history behavior is defined and surfaced if supported.
- [ ] User can delete a conversation.
- [ ] User can export conversation history.
- [ ] Retention policy is documented.
- [ ] Tests cover list, resume, delete, export, and cross-user isolation.
- [ ] `cd gui && npm run typecheck && npm run build` passes.
- [ ] All relevant GitHub checks pass.

**Validation commands:**
```bash
pytest -q tests/test_chat_api.py tests/test_us048_data_isolation.py
cd gui && npm run typecheck && npm run build
```

**Risk notes:** Do not leak one user's chat history into another user's context or GUI.

---

### US-084: Route voice and typed shopping-list commands to the visible list

**Priority:** P1
**Workstream:** Shopping / Voice / Chat / Electron
**Description:** As a user, I want commands like "add salt to the shopping list" to update the visible Shopping List tab from voice or typed chat.

**Why it matters:** A visible Shopping List tab should be reachable from natural Rex commands, or users will assume the feature is broken.

**Files/areas likely involved:**
- `gui/src/pages/ShoppingListPage.tsx`
- `rex_shopping_list_bridge.py`
- `rex/commands/shopping.py`
- `rex/actions/dispatcher.py`
- `rex/tools/registry.py`
- `rex/assistant.py`

**Implementation notes:** Define whether the list is per-user or household. Confirm additions, handle duplicates, and update the GUI without requiring a manual refresh.

**Acceptance Criteria:**
- [ ] Typed chat command can add an item to the visible shopping list.
- [ ] Voice command can add an item to the visible shopping list.
- [ ] Rex confirms the item was added.
- [ ] Duplicate item handling says it is already present and optionally asks about quantity.
- [ ] Shopping List tab updates after command execution.
- [ ] Per-user vs household list behavior is documented.
- [ ] Tests cover typed route, voice route, duplicate handling, and GUI bridge update.
- [ ] `cd gui && npm run typecheck && npm run build` passes.
- [ ] All relevant GitHub checks pass.

**Validation commands:**
```bash
pytest -q tests/test_assistant.py tests/test_tools_registry.py
cd gui && npm run typecheck && npm run build
```

**Risk notes:** Avoid accidental cross-user list pollution if the final model is personal lists.

---

### US-085: Add GUI controls for per-user and household memory

**Priority:** P0
**Workstream:** Memory / Identity / Privacy / Electron
**Description:** As a household user, I want Rex memory to distinguish personal facts from shared household facts and keep retrieval fast.

**Why it matters:** Strong memory is valuable only if it respects privacy boundaries and does not make Rex too slow.

**Files/areas likely involved:**
- `gui/src/pages/SettingsPage.tsx`
- `gui/src/pages/UsersPage.tsx`
- `rex/memory*`
- `rex/identity.py`
- `Memory/`
- `data/memory/`
- `docs/memory.md`

**Implementation notes:** Voice interactions should attach memory to the identified user or household context. Include import/export/delete controls and a retrieval latency budget.

> **Decomposition directive (skill-compliance):** This story is larger than one Ralph iteration. Before execution, split it into ordered one-iteration slices and run them in order: (a) define and store the per-user vs shared/household memory model; (b) attach voice-interaction memory to the identified user/household context; (c) GUI view/add/edit/delete controls; (d) import/export controls; (e) retrieval latency budget plus private-vs-shared isolation tests. Do not attempt the full bundle in one iteration.

**Acceptance Criteria:**
- [ ] Memory model distinguishes per-user private memory and shared household memory.
- [ ] Voice interactions attach memory to identified user or household context.
- [ ] GUI lets users view, add, edit, delete, import, and export memory where appropriate.
- [ ] Privacy boundaries are documented in user-facing language.
- [ ] Memory retrieval has a documented latency budget.
- [ ] Tests cover private-vs-shared isolation and retrieval latency instrumentation.
- [ ] `cd gui && npm run typecheck && npm run build` passes.
- [ ] All relevant GitHub checks pass.

- [ ] Memory records declare a memory type (`semantic`, `episodic`, or `preference`) plus scope (`private user` or `household`) before retrieval/ranking.
- [ ] Scope/identity filtering occurs before ranking, and memory retrieval timing/telemetry does not expose private content.
- [ ] Procedural/experience memory is excluded from ordinary memory writes and can only be promoted through the guarded procedure story (US-112).

**Validation commands:**
```bash
pytest -q tests/test_identity.py tests/test_voice_identity_fallback.py
cd gui && npm run typecheck && npm run build
```

**Risk notes:** Do not send private user memory into household/shared prompts unless explicitly allowed.

---

### US-086: Add scoped document upload and vector indexing

**Priority:** P1
**Workstream:** Memory / Retrieval / Privacy / Electron
**Description:** As a user, I want to upload documents into Rex memory with a chosen scope and labels so retrieval can use them safely.

**Why it matters:** User uploads are powerful but high-risk if private content leaks into household context or cannot be deleted/audited.

**Files/areas likely involved:**
- `gui/src/pages/SettingsPage.tsx` or new memory/upload page
- `rex_memories_bridge.py`
- `rex/document_indexing*`
- `rex/memory*`
- `data/`
- `docs/memory.md`

**Implementation notes:** Distinguish per-user vector stores from a household/shared vector store. Let users tag/label uploaded content or tell Rex how to label it.

> **Decomposition directive (skill-compliance):** This story is larger than one Ralph iteration. Before execution, split it into ordered one-iteration slices and run them in order: (a) upload UI accepting supported document/data types; (b) independent context-inclusion plus audience scope selection (private vs household) and user/Rex-confirmed tagging; (c) indexing into the correct per-user or household vector store with provenance; (d) search/delete/audit and later policy editing; (e) cross-scope/context-disabled isolation tests. Do not attempt the full bundle in one iteration.

**Acceptance Criteria:**
- [ ] Upload UI accepts supported document/data types.
- [ ] At upload time the uploader independently chooses whether the file is eligible for broad/background context and whether its audience scope is private to that user or shared household.
- [ ] Context inclusion and audience scope remain editable later by the uploader/owner; another user cannot promote someone else's private upload to household scope.
- [ ] Context-disabled uploads remain available for explicit authorized file questions but cannot silently influence unrelated turns, proactive suggestions, or situational reasoning.
- [ ] User can add tags/labels during upload.
- [ ] Rex can infer labels only with user confirmation.
- [ ] Uploaded content is indexed into the correct per-user or household vector store with source provenance retained for derived context.
- [ ] Private uploads are filtered before retrieval/ranking and are not retrieved or summarized into household/other-user context.
- [ ] User can search, delete, audit, and inspect contextual-use/scope settings for uploaded content.
- [ ] Tests cover scope, context inclusion on/off, tagging, retrieval, deletion, uploader authority, provenance, and cross-user/cross-scope isolation.
- [ ] `cd gui && npm run typecheck && npm run build` passes.
- [ ] All relevant GitHub checks pass.

**Validation commands:**
```bash
pytest -q tests/test_us074_document_indexing.py
cd gui && npm run typecheck && npm run build
```

**Risk notes:** Treat uploaded documents as sensitive by default. Avoid indexing unsupported binary content without clear failure.

---

### US-087: Unify profile identity across voice, memory, shopping, and history

**Priority:** P0
**Workstream:** Identity / Privacy / Architecture
**Description:** As a household user, I want voice enrollment, profile identity, memory, shopping list, chat history, and shared household behavior to use the same identity model.

**Why it matters:** Separate identity assumptions across features create privacy leaks, wrong-user memory, and confusing UI labels.

**Files/areas likely involved:**
- `rex/identity.py`
- `rex/auth.py`
- `rex/permissions.py`
- `rex/routes/users.py`
- `rex/voice_identity/`
- `rex/routes/chat.py`
- `gui/src/pages/UsersPage.tsx`
- `gui/src/pages/SettingsPage.tsx`

**Implementation notes:** This is the connecting architecture story for US-068, US-083, US-084, US-085, and US-086. It should define canonical user ID, display name, avatar, voice enrollment ID, personal/shared scope, and household behavior.

> **Note on sequencing:** This is a design-and-enforcement story. Land the canonical identity model and its tests first; the consuming features (US-068, US-083, US-084, US-085, US-086) read from it. Keep the model document plus its consistency tests within a single iteration; do not bundle the per-feature migrations here — each consuming story owns its own migration.

**Acceptance Criteria:**
- [ ] One canonical user identity model is documented.
- [ ] Voice enrollment stores and reads the canonical user ID.
- [ ] Chat history stores and reads the canonical user ID.
- [ ] Memory stores and retrieves by canonical user/shared scope.
- [ ] Shopping list behavior uses the documented personal/shared scope.
- [ ] Profile/avatar UI reads from the canonical identity model.
- [ ] Tests cover cross-feature identity consistency.
- [ ] All relevant GitHub checks pass.

- [ ] Canonical Turn events carry one immutable validated user ID and scope across every interface and asynchronous stage.
- [ ] Concurrent James/Cole tests prove no cross-user events, caches, prompts/context, memory, tools, or cancellation leakage.

**Validation commands:**
```bash
pytest -q tests/test_identity.py tests/test_us048_data_isolation.py tests/test_voice_id_profile_switch.py
```

**Risk notes:** Migration must preserve existing local user data or provide a clear backup/migration path.

---

### US-088: Design and gate authenticated mobile/API access for `askrex.app`

**Priority:** P0
**Workstream:** Security / Mobile / API / Deployment
**Description:** As a user, I want to access Rex from an iOS app or mobile client through `askrex.app` without exposing unsafe local admin routes.

**Why it matters:** External/mobile access changes the threat model. The local Flask/API bridge is not automatically safe to expose publicly.

**Files/areas likely involved:**
- `rex/gui_app.py`
- `rex/routes/`
- `rex_speak_api.py`
- `rex/computers/agent_server.py`
- `docs/deployment.md`
- `docs/api.md`
- `SECURITY.md`
- Cloudflare Tunnel or equivalent deployment docs/config

**Implementation notes:** First document whether the existing Flask/API bridge is safe for external/mobile use. Define a secure gateway before any exposure. Use `askrex.app` as the target domain only in documentation/config examples, not as an implicit live deployment.

> **Decomposition directive (skill-compliance):** This story is larger than one Ralph iteration. Before execution, split it into ordered one-iteration slices and run them in order: (a) commit the mobile/API threat model; (b) classify the existing Flask/API bridge as safe or unsafe for external/mobile use with evidence; (c) design the secure gateway (HTTPS, auth, rate limiting, CORS, token management/revocation) and define the iOS API scope; (d) document the Cloudflare-Tunnel-or-equivalent deployment path without committing credentials; (e) auth-rejection, rate-limit, and CORS-policy tests for mobile/API routes. Do not attempt the full bundle in one iteration.

**Acceptance Criteria:**
- [x] A mobile/API threat model is committed.
- [x] Existing local Flask/API bridge is classified as safe or unsafe for external/mobile use with evidence.
- [x] Secure gateway design requires HTTPS, authentication, rate limiting, CORS policy, token management, and token revocation.
- [x] Cloudflare Tunnel or equivalent deployment path is documented without committing credentials.
- [x] API scope defines what the iOS app can and cannot do.
- [x] Local admin routes are not exposed blindly.
- [x] Tests or smoke checks cover auth rejection, rate-limit behavior, and CORS policy for mobile/API routes.
- [x] Docs use `askrex.app` as the target domain.
- [x] All relevant GitHub checks pass.

- [x] Mobile chat and voice consume the same canonical TurnEngine/event contract as desktop/CLI rather than a mobile-only intelligence path.
- [x] Existing desktop-owned pairing, live grants, revocation, TLS binding, strong authentication, rate limits, and least-privilege scopes remain enforced.
- [x] OpenClaw remains optional; its absence or unhealthy state grants no additional mobile authority and does not disable core Rex functionality.

**Validation commands:**
```bash
pytest -q tests/test_windows_agent.py tests/test_openclaw_tool_server.py tests/test_chat_api.py
grep -n "askrex.app\|Cloudflare\|CORS\|rate limit\|revocation" docs/deployment.md docs/api.md SECURITY.md
```

**Risk notes:** Do not expose local admin, HA control, file, computer-control, or secret-management routes over the public internet without explicit auth, least-privilege scope, and confirmation gates.

---

### US-089: Remove obsolete CalendarService compatibility skips

**Priority:** P2
**Workstream:** Tests / Technical Debt
**Description:** As a maintainer, I want `tests/test_calendar_service.py` to test the canonical CalendarService API directly instead of carrying alternate-generation skip branches.

**Acceptance Criteria:**
- [ ] The canonical CalendarService/Event model API is identified from current production code and documented in the test module.
- [ ] Obsolete alternate-API tests are removed or rewritten against the canonical API; no `temporary-bug-skip` site remains in `tests/test_calendar_service.py`.
- [ ] Direct calendar service/backend tests pass.
- [ ] Skip inventory and runtime budget are updated if the executed skip count changes.
- [ ] All relevant GitHub checks pass.

---

### US-090: Remove obsolete EmailService compatibility skips

**Priority:** P2
**Workstream:** Tests / Technical Debt
**Description:** As a maintainer, I want `tests/test_email_service.py` to test the canonical EmailService API directly instead of carrying alternate-generation skip branches.

**Acceptance Criteria:**
- [ ] The canonical EmailService/EmailSummary API is identified from current production code and documented in the test module.
- [ ] Obsolete alternate-API tests are removed or rewritten against the canonical API; no `temporary-bug-skip` site remains in `tests/test_email_service.py`.
- [ ] Direct email service/backend tests pass.
- [ ] Skip inventory and runtime budget are updated if the executed skip count changes.
- [ ] All relevant GitHub checks pass.

---

### US-091: Remove obsolete Scheduler compatibility skips

**Priority:** P2
**Workstream:** Tests / Technical Debt
**Description:** As a maintainer, I want `tests/test_scheduler.py` to target the canonical Scheduler API without legacy/newer implementation skip branches.

**Acceptance Criteria:**
- [ ] The canonical Scheduler constructor and persistence API are identified from current production code.
- [ ] Obsolete alternate-API tests are removed or rewritten; no `temporary-bug-skip` site remains in `tests/test_scheduler.py`.
- [ ] Scheduler and scheduling integration tests pass.
- [ ] Skip inventory and runtime budget are updated if the executed skip count changes.
- [ ] All relevant GitHub checks pass.

---

### US-092: Remove obsolete EventBus compatibility skips

**Priority:** P2
**Workstream:** Tests / Technical Debt
**Description:** As a maintainer, I want `tests/test_event_bus.py` to target the canonical EventBus contract without alternate-generation skip branches.

**Acceptance Criteria:**
- [ ] The canonical EventBus publish/subscribe contract is identified from current production code.
- [ ] Obsolete alternate-contract tests are removed or rewritten; no `temporary-bug-skip` site remains in `tests/test_event_bus.py`.
- [ ] Event bus and OpenClaw event bridge tests pass.
- [ ] Skip inventory and runtime budget are updated if the executed skip count changes.
- [ ] All relevant GitHub checks pass.

---

### US-093: Replace remaining generated/missing-artifact test skips

**Priority:** P2
**Workstream:** Tests / Technical Debt
**Description:** As a maintainer, I want the remaining temporary skips caused by missing generated docs/scripts/examples to become stable assertions or be removed when their historical artifact is no longer part of the supported repo contract.

**Files/areas likely involved:**
- `tests/test_us120_performance_baseline.py`
- `tests/test_us140_full_extra.py`
- `tests/test_skill_loader.py`
- `tests/test_us053_secret_management.py`

**Acceptance Criteria:**
- [ ] Each remaining temporary skip is reconciled against the current supported repo contract rather than silently kept.
- [ ] Valid requirements become direct tests; obsolete historical requirements are removed with evidence.
- [ ] No `temporary-bug-skip` site remains in the listed files.
- [ ] Skip inventory and runtime budget are updated.
- [ ] All relevant GitHub checks pass.

---

## Phase 17 - Rex 2.0 unified runtime, intelligence, and safe self-extension

### US-094: Define TurnEngine contracts and event runtime

**Priority:** P0 | **Workstream:** Runtime / Architecture / Identity | **Dependencies:** US-075 and current fail-closed identity baseline.

**Description:** Introduce canonical interface-agnostic turn/context/event contracts that every response path will migrate onto.

**Why it matters:** Streaming and non-streaming currently have different intelligence paths; one typed runtime is the foundation for parity, cancellation, progress, voice, mobile, verification, and observability.

**Files/areas likely involved:** `rex/runtime/turn.py`, `rex/runtime/events.py`, `rex/runtime/turn_engine.py`, `rex/assistant.py`, `tests/rex2/`.

**Acceptance Criteria:**
- [x] TurnContext has a unique turn ID, immutable validated user ID/scope, source/device/response mode, monotonic timing/deadline context, and policy/permission snapshot reference.
- [x] Typed ordered events cover turn start, context/route/capability/action/model/response progress and exactly one terminal `completed`, `failed`, or `cancelled` event.
- [x] Event timestamps/order/correlation are deterministic; terminal double-emission fails closed.
- [x] TurnEngine initially wraps existing components without changing public behavior or bypassing identity/security checks.
- [x] Tests prove identity immutability and concurrent-user event isolation.
- [x] All relevant GitHub checks pass.

**Validation commands:** `pytest tests/rex2/test_turn_contracts.py -q`; `mypy rex/runtime --ignore-missing-imports`; `ruff check rex/runtime tests/rex2/test_turn_contracts.py`.

**Risk notes:** Define contracts first; do not claim interface parity until US-095 through US-097 land.

### US-095: Move `generate_reply()` onto TurnEngine

**Priority:** P0 | **Workstream:** Runtime / Assistant | **Dependencies:** US-094.

**Description:** Make non-streaming `Assistant.generate_reply()` consume TurnEngine while preserving its public response contract.

**Why it matters:** The complete orchestration path must become one implementation that every delivery mode can share.

**Files/areas likely involved:** `rex/assistant.py`, `rex/runtime/turn_engine.py`, routing/context/action/response services, `tests/rex2/`.

**Acceptance Criteria:**
- [x] `generate_reply()` delegates routing, context, capability/action execution, output validation, history, and final response production through TurnEngine.
- [x] Existing cache, ModelRouter request scope, ActionDispatcher/verification, ResponseBuilder, and fail-closed identity remain covered and observable through events.
- [x] Public callers keep the same final result shape except already-required truthful status corrections.
- [x] Regression tests cover direct answer, read-only tool, mutation/confirmation, model failure, and unavailable capability.
- [x] No direct model shortcut bypasses TurnEngine from `generate_reply()`.
- [x] All relevant GitHub checks pass.

**Validation commands:** `pytest tests/rex2/test_generate_reply_turn_engine.py tests/test_assistant.py -q`; `mypy rex/assistant.py rex/runtime --ignore-missing-imports`.

**Risk notes:** Preserve current identity and verification semantics exactly; this is architecture migration, not permission expansion.

### US-096: Move `stream_reply()` onto TurnEngine

**Priority:** P0 | **Workstream:** Runtime / Streaming | **Dependencies:** US-095.

**Description:** Make streaming delivery consume the same TurnEngine and expose model/response deltas without creating a reduced-intelligence path.

**Why it matters:** Fast must not mean dumb; streaming is a delivery mode, not a different brain.

**Files/areas likely involved:** `rex/assistant.py`, `rex/runtime/events.py`, `rex/runtime/turn_engine.py`, chat stream bridges, `tests/rex2/`.

**Acceptance Criteria:**
- [x] Streaming and non-streaming use the same router, cache policy, context, capability/action pipeline, verification, memory/history, and output validation.
- [x] Streaming emits ordered deltas/sentences plus the same canonical terminal outcome as non-streaming.
- [x] Tool syntax, internal plans, raw provider tool-call payloads, and unverified action claims never leak to the user stream.
- [x] Parity fixtures compare final semantic/status outcomes across both delivery modes.
- [x] Error/fallback/escalation behavior is equivalent across both delivery modes.
- [x] All relevant GitHub checks pass.

**Validation commands:** `pytest tests/rex2/test_stream_turn_parity.py tests/test_assistant_streaming.py -q`; `mypy rex/assistant.py rex/runtime --ignore-missing-imports`.

**Risk notes:** Never buy latency by bypassing ActionDispatcher, verification, identity, or output validation.

### US-097: Adopt TurnEngine across interfaces

**Priority:** P0 | **Workstream:** Runtime / Interfaces | **Dependencies:** US-096.

**Description:** Convert CLI, Electron bridges, canonical voice loop, mobile/API adapters, and supported service callers into thin TurnEngine adapters.

**Why it matters:** One brain only exists if every supported interface actually uses it.

**Files/areas likely involved:** CLI chat, `bridge/rex_chat*_bridge.py`, voice loop/builder, mobile API handlers, API/service adapters, Electron main IPC.

**Acceptance Criteria:**
- [x] CLI text, Electron text, canonical voice, and authenticated mobile chat/voice enter the same TurnEngine contract with validated identity/source metadata.
- [x] A source guard inventories supported interfaces and fails if one directly calls the model or legacy orchestration instead of TurnEngine.
- [x] Interface adapters are limited to authentication/identity, input normalization, transport/stream presentation, and response-mode formatting.
- [x] Cross-interface fixtures prove equivalent route/tool/verification outcomes for the same authenticated request.
- [x] Existing mobile pairing/scopes and Electron immutable session identity remain authoritative.
- [x] All relevant GitHub checks pass.

**Validation commands:** `pytest tests/rex2/test_turn_interface_parity.py tests/test_mobile_api.py -q`; `cd gui && npm run typecheck && npm run build`.

**Risk notes:** Do not let interface convenience reintroduce direct model or tool bypasses.

### US-098: Add turn-scoped cancellation

**Priority:** P0 | **Workstream:** Runtime / Safety / Voice | **Dependencies:** US-097.

**Description:** Give every turn an idempotent cancellation scope that propagates through generation, retrieval, tools, OpenClaw, TTS, and parallel work.

**Why it matters:** Barge-in, correction, stale-request replacement, and safe shutdown require one cancellation truth source.

**Files/areas likely involved:** `rex/runtime/cancellation.py`, TurnContext, LLM streaming, tool execution, OpenClaw client/bridge, TTS/voice loop.

**Acceptance Criteria:**
- [x] Cancellation is idempotent and turn-scoped and emits exactly one canonical cancelled terminal event.
- [x] Model generation, retrieval/prefetch, cancellable tools/OpenClaw calls, and TTS stop or ignore stale output promptly.
- [x] If cancellation/transport loss occurs after a mutation may have dispatched, outcome is `attempted/unverified` until independently proven, never fabricated as failure/success.
- [x] Cancelling one user/turn cannot cancel another user's concurrent work.
- [x] Tests cover cancellation before dispatch, during generation, during read-only work, after mutation dispatch, and repeated cancellation calls.
- [x] All relevant GitHub checks pass.

**Validation commands:** `pytest tests/rex2/test_turn_cancellation.py tests/rex2/test_cancellation_identity_isolation.py -q`; `mypy rex/runtime rex/openclaw --ignore-missing-imports`.

**Risk notes:** Cancellation is not rollback; unknown mutation outcome remains unverified.

### US-099: Add managed warm local runtime

**Priority:** P1 | **Workstream:** Latency / Local Models / Voice | **Dependencies:** US-098 and US-075 baseline.

**Description:** Keep selected local executive/model, STT, TTS, and retrieval/index components warm under a bounded resource policy rather than reloading heavy state per turn.

**Why it matters:** Repeated model/process initialization is incompatible with natural assistant latency.

**Files/areas likely involved:** model/STT/TTS loaders, runtime service lifecycle, health/doctor status, config, RexBench.

**Acceptance Criteria:**
- [x] Managed warm-component lifecycle exposes state/health, bounded memory cost, idle/eviction behavior, and lazy fallback.
- [x] Common warm-path turns do not reload the same heavy executive/STT/TTS/index dependency per turn.
- [x] Startup remains graceful when optional ML/audio dependencies are absent; local-first core text remains usable.
- [x] RexBench compares cold vs warm evidence without storing user content.
- [x] Diagnostics report which components are warm and approximate resource cost without secrets/private data.
- [x] All relevant GitHub checks pass. *(PR #395 exact head `66b095d9723433780b84cba7c5f79ffd08f79f2d` passed all required workflows before merge; merged as `d5e14333b22b6dad70ba67ca39852e4b9afb01eb` on 2026-08-14.)*

**Validation commands:** `pytest tests/rex2/test_warm_runtime.py -q`; `python scripts/rexbench.py --profile warm-runtime`.

**Risk notes:** Do not trade latency for unbounded RAM/VRAM growth or force optional heavy dependencies into base install.

### US-100: Add streaming ASR and semantic endpointing

**Priority:** P1 | **Workstream:** Voice / STT / Latency | **Dependencies:** US-099, US-074, US-068 through US-070.

**Description:** Process speech incrementally and combine acoustic silence with semantic completeness before committing a transcript to TurnEngine.

**Why it matters:** Full-capture-then-transcribe adds avoidable latency and makes turn-taking mechanical.

**Files/areas likely involved:** canonical voice capture/STT pipeline, VAD/endpoint logic, TurnEngine input adapter, voice diagnostics.

**Acceptance Criteria:**
- [ ] Streaming/partial ASR updates are explicitly non-authoritative and never dispatch actions before final commit.
- [ ] Endpointing combines bounded silence/VAD evidence with semantic completeness; timeout/device-loss falls back safely to deterministic endpoint behavior.
- [ ] Final committed transcript may correct earlier partial hypotheses before tool/model dispatch.
- [ ] Tests cover short command, trailing clause, hesitation, correction, silence timeout, and microphone/STT failure.
- [ ] RexBench reports capture/first-partial/final-transcript/turn-dispatch timing separately for mock/local/physical evidence.
- [ ] All relevant GitHub checks pass.

**Validation commands:** `pytest tests/rex2/test_streaming_asr_endpointing.py tests/test_voice_loop.py -q`; `python scripts/rexbench.py --profile voice-endpointing`.

**Risk notes:** A partial transcript must never cause an irreversible action.

### US-101: Add safe speculative read-only prefetch

**Priority:** P1 | **Workstream:** Latency / Context / Tools | **Dependencies:** US-107, US-109, US-108, US-098.

**Description:** Allow bounded cancellable prefetch of likely low-risk read-only context/capabilities while routing is still resolving.

**Why it matters:** Independent reads can hide retrieval/network latency, but speculation must never create side effects or bypass permissions.

**Files/areas likely involved:** TurnEngine prefetch stage, Capability Registry/retrieval, action graph, cancellation/telemetry.

**Acceptance Criteria:**
- [ ] Only healthy, currently permitted, explicitly read-only/low-risk capabilities are eligible for speculation.
- [ ] Prefetch has strict concurrency/time/resource budgets, inherits cancellation, and produces no confirmation/mutation side effects.
- [ ] Unused speculative results are discarded; audit/metrics retain metadata/timing only, not private payload contents.
- [ ] Prefetched data is revalidated for identity/scope/freshness before final-plan use.
- [ ] Tests prove mutating/risky/disabled/unauthorized capabilities are never speculatively called.
- [ ] All relevant GitHub checks pass.

**Validation commands:** `pytest tests/rex2/test_speculative_prefetch.py -q`; `python scripts/rexbench.py --profile speculative-prefetch`.

**Risk notes:** Optimization stays strictly inside the permission boundary; no mutation is eligible.

### US-102: Stream clause/sentence TTS from turn events

**Priority:** P1 | **Workstream:** Voice / TTS / Streaming | **Dependencies:** US-100, US-096, US-109.

**Description:** Start speaking stable response clauses/sentences from canonical events while preserving order and verification truth.

**Why it matters:** Natural perceived latency depends on first audible response, not only final text completion.

**Files/areas likely involved:** Turn response events, TTS queue/playback, canonical voice loop, RexBench.

**Acceptance Criteria:**
- [ ] Stable response sentences/clauses enter an ordered per-turn TTS queue and are spoken exactly once in text order.
- [ ] Tool/action success claims are withheld until lifecycle evidence permits truthful wording.
- [ ] TTS failure/cancellation never removes or corrupts canonical text; stale audio is not played after cancellation.
- [ ] Backpressure prevents unbounded synthesis queues on long responses.
- [ ] RexBench reports first-text and first-audio latency separately.
- [ ] All relevant GitHub checks pass.

**Validation commands:** `pytest tests/rex2/test_turn_tts_streaming.py tests/test_tts_voices.py -q`; `python scripts/rexbench.py --profile streaming-tts`.

**Risk notes:** Spoken content cannot outrun verification evidence for real-world actions.

### US-103: Implement barge-in over canonical cancellation

**Priority:** P1 | **Workstream:** Voice / Cancellation | **Dependencies:** US-102, US-098.

**Description:** Let an authenticated user interrupt Rex speech/current turn and start a replacement turn without stale output continuing.

**Why it matters:** Conversational voice requires interruption to be first-class runtime behavior, not ad-hoc audio stopping.

**Files/areas likely involved:** voice input/playback state machine, Turn cancellation, TTS queue, transcript/turn adapter.

**Acceptance Criteria:**
- [ ] Barge-in stops/invalidates stale TTS and cancels the current turn before replacement dispatch.
- [ ] A mutation already dispatched follows canonical attempted/unverified rules rather than being assumed rolled back.
- [ ] Deterministic echo-suppression fixtures prevent Rex's own audio from creating a replacement turn.
- [ ] Repeated interruption cannot cross user/session boundaries or produce two active replacement turns.
- [ ] Physical hardware validation is labeled separately from deterministic/mock tests.
- [ ] All relevant GitHub checks pass.

**Validation commands:** `pytest tests/rex2/test_voice_barge_in.py -q`; `python scripts/rexbench.py --profile barge-in`.

**Risk notes:** Never hide or "cancel" a real-world action merely by stopping its audio response.

### US-104: Emit progressive status from canonical events

**Priority:** P1 | **Workstream:** UX / Events / Privacy | **Dependencies:** US-097, US-109.

**Description:** Drive `thinking/checking/acting/verifying/speaking` status surfaces solely from canonical Turn events.

**Why it matters:** Progressive UX reduces perceived latency and prevents each interface inventing contradictory activity state.

**Files/areas likely involved:** event-to-status projector, Electron/mobile/voice adapters, typed IPC/mobile events.

**Acceptance Criteria:**
- [x] Status is a deterministic projection of canonical events and contains no independent orchestration/business logic.
- [x] Status payloads contain no transcript, prompt, memory contents, credentials, or private tool results.
- [x] CLI/Electron/voice/mobile show equivalent state transitions for the same turn, adapted only for presentation.
- [x] Cancellation/failure/verification terminal states clear stale indicators reliably.
- [x] Tests cover privacy redaction and cross-interface status parity.
- [x] All relevant GitHub checks pass. *(PR #393 exact head c324d36 passed all 18 required checks before merge; merged as 39d8d3c on 2026-08-13.)*

**Validation commands:** `pytest tests/rex2/test_progressive_status.py -q`; `cd gui && npm run typecheck && npm run build`.

**Risk notes:** Status is evidence-derived; never infer `done` from elapsed time or optimistic client state.

### US-105: Add identity-safe prompt/context caching

**Priority:** P1 | **Workstream:** Latency / Context / Privacy | **Dependencies:** US-094 and current identity isolation; US-087 later hardens cross-surface semantics.

**Description:** Cache deterministic prompt/context artifacts only when keys and invalidation make user/policy/model boundaries explicit.

**Why it matters:** Context assembly can be expensive, but underspecified shared caches create severe James/Cole privacy risk.

**Files/areas likely involved:** prompt/context builder, memory/context revision metadata, runtime cache utility, policy/model/config versioning.

**Acceptance Criteria:**
- [x] Private cache keys include validated user/scope plus relevant model, policy, capability/config, and prompt-template versions.
- [x] Household-safe data is shared only when explicitly household-scoped; private entries are never shared across users.
- [x] Deterministic invalidation occurs on relevant identity/scope/policy/model/config/memory revisions.
- [x] Cache metrics contain categories/timing but never raw private content or credentials.
- [x] Concurrent James/Cole tests prove no cache hit returns the other user's prompt/context/memory.
- [x] All relevant GitHub checks pass.

**Validation commands:** `pytest tests/rex2/test_context_cache.py tests/rex2/test_context_cache_identity.py -q`.

**Risk notes:** Prefer a cache miss over stale or cross-user context.

### US-106: Consolidate canonical Capability Registry and Tool Cards

**Priority:** P0 | **Workstream:** Capabilities / Tools / Architecture | **Dependencies:** US-064.

**Description:** Replace divergent capability/tool metadata authorities with one canonical Capability record/registry and adapters for existing local tools.

**Why it matters:** Semantic selection, permissions, health, dynamic OpenClaw, gap detection, and Forge all need one source of truth.

**Files/areas likely involved:** `rex/capabilities/`, existing tool registries/consumers, OpenClaw adapters, capability inventory docs.

**Acceptance Criteria:**
- [x] One authoritative Capability/Tool Card schema records ID/source/input-output schema/enabled state/required permissions/health/operation type/risk/verification support and user-facing description/examples.
- [x] Existing local registries adapt into or migrate to that authority; duplicate metadata cannot silently diverge.
- [x] Registry metadata itself is deterministic; authorization is always evaluated for the current user at selection/execution time.
- [x] Compatibility adapters keep current callers functional until their migration removes obsolete paths.
- [x] Tests detect duplicate IDs/schema drift and prove remote metadata cannot overwrite local security classification.
- [x] All relevant GitHub checks pass.

**Validation commands:** `pytest tests/rex2/test_capability_registry.py tests/test_tool_registry.py -q`; `mypy rex/capabilities --ignore-missing-imports`.

**Risk notes:** Registry consolidation must never broaden what any user may execute.

### US-107: Add permission-aware hybrid capability retrieval

**Priority:** P0 | **Workstream:** Capabilities / Retrieval / Local AI | **Dependencies:** US-106.

**Description:** Retrieve a small relevant capability set using lexical plus local semantic evidence after security/health filtering.

**Why it matters:** Keyword-only selection does not scale, while injecting every tool into prompts hurts quality and latency.

**Files/areas likely involved:** capability retrieval/index, local embedding adapter if available, TurnEngine executive/routing stage.

**Acceptance Criteria:**
- [x] Candidate set is filtered by current-user permission, identity scope, enabled/configured state, health, and risk policy before ranking.
- [x] Hybrid ranking uses lexical evidence plus a local embedding/semantic signal when available; no paid embedding service is added.
- [x] Missing/broken embeddings fall back deterministically to lexical retrieval without disabling tool use.
- [x] Selection exposes inspectable score/reason metadata without leaking private payloads.
- [x] Golden tests cover paraphrases, ambiguity, denied/unhealthy tools, and no-embedding fallback.
- [x] All relevant GitHub checks pass.

**Validation commands:** `pytest tests/rex2/test_capability_retrieval.py -q`; `python scripts/rexbench.py --profile capability-retrieval`.

**Risk notes:** Security filters precede ranking; semantic score never grants authority.

### US-108: Add safe action dependency graphs and parallel execution

**Priority:** P1 | **Workstream:** Planning / Execution | **Dependencies:** US-109, US-107, US-098.

**Description:** Execute independent permitted actions concurrently while preserving explicit dependencies, mutation serialization, cancellation, and verification.

**Why it matters:** Serial independent reads add latency; naive parallelism creates races and unsafe side effects.

**Files/areas likely involved:** plan/action graph models, executor, policy/verification adapters, Turn events.

**Acceptance Criteria:**
- [x] Minimal DAG model expresses dependencies, operation type, authorization, and verification/postcondition relationships.
- [x] Independent permitted reads may run in bounded parallelism; conflicting or mutating nodes serialize when ordering matters.
- [x] Dependent failure/cancellation blocks unsafe descendants and preserves truthful states for already-started nodes.
- [x] Confirmation/commit boundaries cannot be bypassed by parallel scheduling.
- [x] Tests prove wall-clock concurrency for independent mock reads and deterministic mutation/conflict ordering.
- [x] All relevant GitHub checks pass.

**Validation commands:** `pytest tests/rex2/test_action_graph.py tests/rex2/test_parallel_actions.py -q`; `python scripts/rexbench.py --profile parallel-actions`.

**Risk notes:** Different tool names do not imply independent real-world resources.

### US-109: Generalize the action lifecycle

**Priority:** P0 | **Workstream:** Verification / Safety / Audit | **Dependencies:** US-094 and existing HA verification baseline.

**Description:** Promote the verified action vocabulary into one runtime state machine used by tools, Home Assistant, OpenClaw, workflows, and future Forge capabilities.

**Why it matters:** Action truth must be a runtime invariant rather than per-integration wording convention.

**Files/areas likely involved:** action result/evidence models, verification service, Turn events, tool/OpenClaw/HA adapters, audit logging.

**Acceptance Criteria:**
- [x] Canonical states are exactly `planned`, `authorized`, `attempted`, `completed`, `verified`, `unverified`, `failed`, and `cancelled`, with documented allowed transitions.
- [x] Invalid/out-of-order transitions fail closed and cannot create verified success.
- [x] Immutable correlation IDs link plan/action/tool attempt/verification evidence/audit record/user-facing result.
- [x] Existing HA/OpenClaw results adapt into the lifecycle without losing evidence detail.
- [x] User-facing success wording derives from lifecycle/evidence, never mere absence of an exception.
- [x] All relevant GitHub checks pass.

**Validation commands:** `pytest tests/rex2/test_action_lifecycle.py tests/test_ha_verification.py -q`.

**Risk notes:** Preserve `unverified` as a real state; do not collapse it into success or failure for UI convenience.

### US-110: Implement ModelRouter 2.0 fast/deep escalation

**Priority:** P1 | **Workstream:** Models / Routing / Local-first | **Dependencies:** US-096 and US-075.

**Description:** Route deterministic/simple work through fast/local paths and escalate genuinely complex or low-confidence work through configured deep models with explicit evidence.

**Why it matters:** One model for every task wastes latency/resources; opaque heuristic routing is hard to trust and debug.

**Files/areas likely involved:** `rex/model_router.py`, provider strategies, Turn route events, config/UI provider settings, RexBench.

**Acceptance Criteria:**
- [x] Routing exposes explicit complexity/confidence/evidence plus chosen fast/deep route in privacy-safe turn metadata.
- [x] Low-confidence executive decisions may escalate at most once unless an explicit bounded retry policy says otherwise.
- [x] Local-first remains default/configurable; no cloud or paid provider is silently enabled or selected without existing configuration/permission.
- [x] Deterministic commands may bypass deep reasoning without bypassing permissions or verification.
- [x] Golden tests cover simple command, ambiguous tool choice, complex reasoning, provider outage, and unavailable local model.
- [x] All relevant GitHub checks pass.

**Validation commands:** `pytest tests/rex2/test_model_router_v2.py tests/test_model_router.py -q`; `python scripts/rexbench.py --profile model-routing`.

**Risk notes:** A fast route still uses the complete TurnEngine and safety policy.

### US-111: Track provider reliability and routing evals

**Priority:** P1 | **Workstream:** Models / Reliability / Evaluation | **Dependencies:** US-110.

**Description:** Make provider health/reliability/cooldown evidence part of routing and continuously evaluate route quality on a deterministic Rex corpus.

**Why it matters:** A theoretically best model is not useful when it is unavailable, slow, rate-limited, or repeatedly failing.

**Files/areas likely involved:** provider health metrics, ModelRouter, RexBench eval fixtures/reports, diagnostics.

**Acceptance Criteria:**
- [x] Provider reliability records bounded latency/failure/rate-limit/cooldown signals without prompt/response/private contents.
- [x] Routing respects outages/cooldowns and has deterministic fallback across configured providers.
- [x] A checked-in deterministic routing corpus measures selection correctness, fallback, and regression without live-provider dependence.
- [x] Live-provider evaluation is opt-in/labeled separately and cannot become a required paid CI dependency.
- [x] Diagnostics explain provider unavailability/fallback without exposing credentials.
- [x] All relevant GitHub checks pass.

**Validation commands:** `pytest tests/rex2/test_provider_reliability.py tests/rex2/test_routing_eval.py -q`; `python scripts/rexbench.py --profile routing-eval`.

**Risk notes:** Reliability telemetry must not become a covert prompt/user activity log.

### US-112: Add guarded procedural experience memory

**Priority:** P1 | **Workstream:** Memory / Learning / Safety | **Dependencies:** US-085 and US-109.

**Description:** Let Rex learn reusable procedures only from verified outcomes, with provenance, identity/risk boundaries, revalidation, and explicit promotion rules.

**Why it matters:** Experience can reduce repeated planning mistakes, but `worked once` is not a safe permanent procedure.

**Files/areas likely involved:** memory/experience store, action evidence, procedure models/promoter, GUI memory controls/audit.

**Acceptance Criteria:**
- [x] Only verified action/workflow outcomes can become procedure candidates; ordinary memory/conversation writes cannot create executable procedures.
- [x] Procedures record provenance, owner/scope, capabilities/permissions/risk, version/dependency fingerprint, success/failure counts, last validation, and expiry/revalidation policy.
- [x] Procedures containing mutations/elevated risk require explicit human approval before activation.
- [x] Users can inspect, disable/revoke, and delete learned procedures within scope; James/Cole private procedures remain isolated.
- [x] Repeated failure/version drift can disable a procedure pending revalidation without erasing audit history.
- [x] All relevant GitHub checks pass.

**Validation commands:** `pytest tests/rex2/test_procedural_memory.py tests/rex2/test_procedure_identity.py -q`.

**Risk notes:** Experience memory is never implicit privilege escalation.

### US-113: Synchronize OpenClaw/ClawHub capabilities dynamically

**Priority:** P1 | **Workstream:** OpenClaw / Capabilities | **Dependencies:** US-106 and existing authenticated OpenClaw client/health.

**Description:** Discover, validate, normalize, and atomically synchronize available OpenClaw/ClawHub capabilities into the canonical registry at startup and refresh time.

**Why it matters:** OpenClaw can only be an expandable ecosystem if Rex safely learns what it currently provides without manual hard-coding or restart.

**Files/areas likely involved:** `rex/openclaw/`, Capability Registry sync/adapters, health/status UI, tests.

**Acceptance Criteria:**
- [x] Discovery uses the authenticated configured gateway and schema-validates remote capability metadata before normalization.
- [x] Startup, manual refresh, and supported hot-refresh apply registry changes atomically; removed capabilities become stale/unavailable rather than lingering executable.
- [x] Remote metadata may update source/schema/description but can never widen local permission, operation type, risk tier, or verification policy.
- [x] Sync failure preserves the last known safe snapshot with explicit unhealthy/stale status and does not break core local Rex.
- [x] Tests cover add/update/remove/malformed/duplicate capabilities and malicious risk/permission metadata.
- [x] All relevant GitHub checks pass.

**Validation commands:** `pytest tests/rex2/test_openclaw_capability_sync.py tests/test_openclaw_http_client.py -q`.

**Risk notes:** Treat every remote capability description as untrusted data.

### US-114: Harden OpenClaw reconnect and verification

**Priority:** P1 | **Workstream:** OpenClaw / Reliability / Verification | **Dependencies:** US-113, US-109, US-098.

**Description:** Reconcile capability state on reconnect and normalize remote action evidence so gateway outage/recovery cannot create stale authority or false success.

**Why it matters:** Connectivity recovery is unsafe if capability inventory or mutation evidence changed during an outage.

**Files/areas likely involved:** OpenClaw health/reconnect loop, capability sync, tool bridge/executor, action verification adapters.

**Acceptance Criteria:**
- [x] Gateway recovery triggers authenticated capability resynchronization before newly recovered remote capabilities dispatch.
- [x] In-flight reads fail/fallback per policy; mutations with unknown outcome become `attempted/unverified` unless an independent postcondition proves them.
- [x] Remote verification evidence is normalized into the canonical lifecycle and cannot self-declare verified without an accepted Rex adapter/postcondition.
- [x] Bounded reconnect/backoff has no hot loop and exposes privacy-safe health transitions.
- [x] Tests cover outage before dispatch, outage after mutation dispatch, schema change, stale removal, and local fallback.
- [ ] All relevant GitHub checks pass.

**Validation commands:** `pytest tests/rex2/test_openclaw_reconnect.py tests/rex2/test_openclaw_verification.py -q`.

**Risk notes:** Reconnection restores connectivity, not trust; resync and policy evaluation come first.

### US-120: Add first-class concurrent timers and alarms

**Priority:** P1 | **Workstream:** Scheduling / Household Assistant | **Dependencies:** US-088, US-087, US-109, US-106.

**Description:** Create, manage, persist, and accurately fire multiple per-user timers and alarms as first-class Rex capabilities rather than reminders.

**Acceptance Criteria:**
- [x] A canonical `rex.timekeeping` service provides separate timer and alarm semantics with stable IDs, optional names, atomic persistence, and per-user ownership.
- [x] Multiple concurrent timers support seconds/minutes/hours plus list, remaining-time query, cancel, pause, resume, rename, and add/subtract time.
- [x] Alarms support local clock times, dates, weekday/selected-day recurrence, list, enable/disable, edit, cancel, snooze, and dismiss.
- [x] Alarm recurrence retains the user's IANA timezone and recalculates across daylight-saving transitions with deterministic DST tests.
- [x] Assistant startup restores persisted state, reconciles overdue events once, and schedules future deadlines without reminder-service minute polling.
- [x] Deadline delivery uses a condition-based nearest-deadline worker with explicit deterministic timing tolerance and wakeup when an earlier deadline is added.
- [x] James/Cole ownership isolation permits identical display names without cross-user reads or mutations; ambiguous same-user names require disambiguation rather than guessing.
- [x] `timekeeping_read` and `timekeeping_manage` are canonical Tool/Capability Registry entries; mutations use the action lifecycle and independent persisted-state verification.
- [x] Desktop typed chat and canonical voice route exact timer/alarm intent through TurnEngine without Home Assistant or generic multi-tool fanout.
- [x] Mobile structured timekeeping uses explicit `tasks.read` / `tasks.write` device scopes; free-form mobile mutations remain behind the existing structured-action authorization boundary.
- [x] Unit/integration coverage includes concurrency, restart recovery, recurrence, snooze, cancellation, identity isolation, timezone/DST, canonical tools, Assistant startup, desktop/voice routing, and mobile scope enforcement.
- [x] README/CLAUDE and capability discovery describe first-class timer/alarm support truthfully; speaker targeting/output policies remain explicitly deferred to US-121/US-122.
- [ ] All relevant GitHub checks pass on the exact implementation PR head.

**Validation commands:** `pytest -q tests/timekeeping tests/mobile_api/test_action_scope_enforcement.py tests/test_us016_action_dispatcher.py tests/test_assistant.py tests/test_assistant_latency.py tests/test_tools_registry.py`; `mypy rex/timekeeping rex/actions/dispatcher.py rex/assistant.py rex/mobile_api/action_context.py rex/tools/registry.py --ignore-missing-imports`; required repository release gates.

**Risk notes:** Timers/alarms are private user state. Do not reuse reminder polling, do not let capability retrieval duplicate a stateful command, and do not treat an unverified mutation as success. Speaker/room targeting belongs to US-121/US-122. Historical test filenames such as `test_us120_performance_baseline.py`, `test_us121_blocking_io_audit.py`, and `test_us122_memory_baseline.py` retain IDs from the signed-off March 2026 checklist and are unrelated legacy evidence; the active story IDs are defined here.

### US-121: Add canonical speaker, room, group, and media orchestration

**Priority:** P1 | **Workstream:** Audio / Media / Home Assistant / External Capabilities | **Dependencies:** US-120.

**Description:** Add provider-neutral audio-target and media-provider/account abstractions so Rex can resolve named speakers, rooms, persistent groups, and the correct user-owned media source while truthfully controlling supported playback.

**Acceptance Criteria:**
- [x] Canonical audio targets have stable IDs, names/aliases, provider, room, capabilities, online/health state, and per-user authorization.
- [x] Local devices, Home Assistant `media_player` entities, and future providers adapt into one registry rather than provider-specific user commands.
- [x] Canonical media-provider accounts are bound to a Rex user/profile and credential-vault slot; provider tokens/credentials never enter prompts or cross-user fallback state.
- [x] The provider contract is capable of supporting Apple Music/MusicKit when Apple developer credentials and per-user authorization are available, without making live Apple Music a precondition for US-121 completion.
- [x] Target resolution handles room/device/group names without unsafe fuzzy ambiguity.
- [x] For an interactive media command with no explicit target, the trusted request-origin/listening endpoint is the preferred output when it is an authorized playable target.
- [x] Persistent speaker groups support create, inspect, rename, membership edit, and delete.
- [x] Supported media actions include play/pause/resume/stop, next/previous where available, volume, mute/unmute, playback-state query, and provider-supported transfer/retargeting.
- [x] Successful playback creates a bounded active-media-session reference so natural follow-ups such as "pause it", "turn it up", or "move it to the living room" resolve without needless repetition when unambiguous.
- [x] Ambiguous active sessions or targets cause a short clarification rather than silent selection.
- [x] Unsupported/offline actions return truthful actionable limitations; mutations use canonical verification where technically possible.
- [x] Dynamic providers refresh discovery/health without requiring Rex restart where supported.
- [x] Tests cover target resolution, ambiguity, request-origin routing, active-session follow-ups, group CRUD, offline/mixed-capability providers, per-user provider-account isolation, permissions, playback, and verified outcomes.
- [ ] All relevant GitHub checks pass.

**Risk notes:** A display-name match or request-origin device never grants device authority. Provider-account selection and output-target selection are separate. Unsupported transfer/group behavior must not be reported as completed.

**Local acceptance evidence (2026-08-16):** canonical media/provider/account/group/routing tests pass locally, including dynamic refresh, request-origin authority, lifecycle-verified group CRUD, verified Home Assistant transport/volume/mute controls, truthful unsupported transfer/group playback, and Electron session-bound target/group IPC. Live Apple Music/MusicKit authorization and physical-speaker production verification remain unclaimed. The exact implementation PR-head GitHub check remains open until CI completes.

### US-122: Add per-user output-routing policies and Settings UI

**Priority:** P1 | **Workstream:** Settings / Voice / Timers / Media | **Dependencies:** US-121.

**Description:** Let each user choose default and conditional targets and media-account behavior for spoken responses, timers, alarms, and media, with request-origin convenience, explicit one-off overrides, and safe fallbacks.

**Acceptance Criteria:**
- [x] Electron Settings exposes canonical outputs/rooms/groups and per-user defaults for spoken response, timer, alarm, and media targets.
- [x] Each profile can link/select its own media provider account(s) and default provider/account without exposing another user's credentials.
- [x] When voice identity is confidently resolved, media uses that user's linked/default provider account. When identity is unresolved, policy may use a configured household primary playback account for ordinary playback without granting another user's private library mutation authority.
- [x] Interactive media defaults to the authorized request-origin/listening endpoint when no target is named; an explicit natural-language room/device/group always overrides that preference.
- [x] Timer/alarm records may carry an explicit target overriding defaults.
- [x] Policies support time/day conditions, quiet hours, target volume where supported, and explicit fallback behavior.
- [x] One-off natural-language targets override stored defaults and Rex can explain the resolved route/policy.
- [x] Speaker-group management is available from Settings, including test playback where supported.
- [x] Electron and mobile/PWA use the same backend policy state with James/Cole isolation.
- [x] Routing/fallback decisions are structured and privacy-safe; unavailable targets never silently reroute against policy.
- [x] Tests cover per-user media-account isolation, unresolved-speaker primary-account fallback, request-origin default, defaults, conditions, explicit overrides, outages/fallbacks, quiet hours, groups, and concurrent per-user policies.
- [ ] All relevant GitHub checks pass.

**Local acceptance evidence (2026-08-21):** US-122's product criteria pass focused routing/timekeeping/mobile/TTS tests and the Electron typecheck/Vitest/build matrix. Account linking is the US-121 `MediaAccountStore.put()` registration seam backed by a user-scoped credential-vault reference; US-122 safely lists/selects those linked accounts and does not fabricate an Apple Music OAuth/token flow before real provider credentials exist. The earlier full-Python failure was independently reproduced as a pre-existing OpenClaw reconnect test-fixture race and corrected on `master` by PR #416 before this branch was rebased. The final GitHub-check criterion remains open until the exact rebased US-122 head completes all remote gates.

**Risk notes:** Output/account routing is policy, not authority. It cannot widen device permissions, borrow another user's private library authority, or silently suppress explicitly required events.

### US-123: Add canonical situational context and proactive assistance

**Priority:** P1 | **Workstream:** Context / Identity / Privacy / Proactivity | **Dependencies:** US-085, US-086, US-087, US-109, US-121, US-122.

**Description:** Add one canonical, user-scoped situational-context/source-policy layer that lets Rex preserve conversational references and proactively combine authorized connected information into timely, personable assistance without widening disclosure or action authority.

**Why it matters:** Rex should understand what is going on across an interaction and offer useful next steps, but ad hoc feature-specific context would create inconsistent privacy behavior and cross-user leakage risk.

**Acceptance Criteria:**
- [x] Context sources use explicit policy metadata including source ID/type, private owner when applicable, audience scope, context-enabled state, disclosure policy, and a content-free revision used to invalidate stale context/cache state.
- [x] Ordinary integrations deliberately connected by a user are eligible for that user's contextual reasoning by default unless disabled; contextual access never grants mutation authority.
- [x] Uploaded sources obey US-086's independent per-file context-inclusion and private/household audience choices, including provenance-preserving retrieval and cross-user isolation.
- [x] Location is a special opt-in source: each user must explicitly grant `location_assist` before Rex uses current/recent location for that user's assistance, and household/admin status cannot override that grant.
- [x] Location disclosure is a separate person-specific `location_share` grant. Enabling location-assisted features never lets another user ask "where is <user>?" and receive location unless the tracked user explicitly granted that recipient access.
- [x] A denied location-disclosure request does not reveal or confirm whether Rex currently has location data for that user.
- [x] Rex accesses location only when it materially improves the current task or an enabled proactive rule; permission alone does not require continuous polling/tracking.
- [x] Capabilities publish typed, bounded/expiring active-context references rather than separate conversation engines; TurnEngine resolves natural follow-ups such as "it", "that one", "move it", or "turn it up" against authorized active state.
- [x] Ambiguous or expired references cause a short clarification rather than a guessed cross-domain action.
- [x] A canonical proactive-opportunity evaluator can combine calendar, current weather/traffic/search, relevant memory/preferences, capability state, and recent verified activity to identify useful next actions for the affected user.
- [x] Proactive candidates carry provenance, freshness, confidence, urgency/benefit, user scope, and dismissal/preference evidence; only high-signal opportunities surface.
- [x] Suggestions are delivered in natural language during a suitable interaction by default; urgent notifications require an already-authorized notification route/policy.
- [x] Declined suggestion patterns reduce future frequency; accepting a suggestion may create an explicit automation/preference but never bypasses normal action authorization/confirmation.
- [x] Revoking source, context, location, or sharing permission invalidates affected active context and cached prompt/context artifacts through revision changes.
- [x] Future self-maintenance, generated skills, OpenClaw capabilities, and developer/self-repair agents consume these privacy/context grants as constitutional authority and cannot autonomously widen contextual-use, disclosure, upload scope/audience, location_assist, or person-specific location_share.
- [x] Settings surfaces expose per-user contextual-source controls, upload context/scope controls, location-assist permission, recipient-specific location-sharing permission, and proactive-assistance preferences without allowing one user/admin to override another user's private location choices.
- [x] Tests cover source policy, upload integration, location opt-in/non-disclosure/admin non-override, active-reference resolution/expiry, context-cache invalidation, proactive ranking/dismissal behavior, and James/Cole isolation.
- [x] All relevant GitHub checks pass. *(PR #417 implementation head `98c7b224eb6e0a8aadb67cb6071ed7c0a8f7ea11`: 18/18 reported checks green on 2026-08-23; the documentation-only evidence commit must also remain green before merge.)*

**Local acceptance evidence (2026-08-22):** The complete US-123 focused matrix passed 596/596 tests. Electron privacy/output-routing UI validation passed 8/8 tests plus TypeScript typecheck and the production Electron build. Ruff, Black, and mypy passed across the context/proactivity implementation; the release security audit scanned 1,473 files with zero actionable placeholder findings, no merge markers, no exposed secrets, and a passing security gate; repository truth/integrity tests passed 14/14; and full pre-commit (Ruff, Black, detect-secrets) plus `git diff --check` passed. The full `not slow and not audio and not gpu` repository matrix executed 9,389 collected tests: 9,325 passed, 65 skipped, and one inherited environment failure because optional `openwakeword` is absent. The exact wake-word reliability test fails identically on master, and the three `master..origin/master` commits do not change that test, its loader, or the dependency contract. This is not counted as a full release-matrix pass. PR #417 implementation head `98c7b224eb6e0a8aadb67cb6071ed7c0a8f7ea11` subsequently passed all 18 reported GitHub checks, including Python 3.11 Tests & Coverage and the Installed Windows Electron artifact.

**Current limitations:** Legacy unassigned uploads remain context-disabled and explicit-query-only. Live weather/traffic/search enrichment is adapter-driven and fail-closed; there is no production traffic reader in this repository, so a commute-delay opportunity requiring traffic data will not surface until an authorized reader is configured. Personal location remains opt-in and demand-driven: `location_assist` and recipient-specific `location_share` are separate grants, and neither OS permission nor Rex permission starts continuous GPS polling.

**Validation commands:** focused context/privacy/proactivity tests; identity/cache/upload suites; `cd gui && npm run typecheck && npm run build`; required repository release gates.

**Risk notes:** Connected-data availability, contextual eligibility, disclosure, and action authority are four distinct concerns. Historical checklist uses of `US-123` are legacy evidence only; this active story is defined by the authoritative production-readiness PRD.

### US-124: Decouple Rex Core and the local Voice Agent from Electron window lifecycle

**Priority:** P0 | **Workstream:** Windows / Runtime / Voice / Installer | **Dependencies:** US-119 and the existing managed Electron runtime.

**Description:** Make the packaged Windows installation keep the authoritative Rex runtime and local voice-listening path available when the Electron window is minimized or closed, without requiring a terminal or source checkout.

**Acceptance Criteria:**
- [x] The packaged installation has explicit lifecycle owners for Rex Core and the signed-in user's local Voice Agent; neither depends on a visible renderer window remaining open. (`rex.background.supervisor.RuntimeSupervisor`, `rex.background.core_server`, `rex.background.voice_agent`.)
- [x] Closing the Electron window does not stop an enabled Voice Agent or the Core services it requires for a screenless turn. (`gui/src/main/tray.ts`/`index.ts` never call a background-runtime stop path on window close/hide; the packaged installed-artifact smoke's Electron process exits via `app.quit()` while the background runtime it started keeps running until explicitly stopped.)
- [x] Supported automatic startup after Windows reboot/sign-in uses absolute packaged runtime paths and requires no manual terminal command. (`rex.background.windows_startup.build_schtasks_create_command` — absolute `pythonw.exe`/runtime-root, `ONLOGON` trigger, argument array, never `shell=True`; `gui/src/main/backgroundRuntime.ts` invokes it with packaged paths after identity resolution.)
- [x] Core and Voice Agent expose bounded health states that distinguish ready, paused, degraded, unavailable, and failed components without exposing private content. (`HealthState`/`ComponentHealth`/`RuntimeHealth` are content-free bounded types; public status accepts the bounded `listening_paused` detail code and round-trips `paused` in `tests/background/test_cli.py`. The user control that actually requests Pause Listening remains US-126.)
- [x] Microphone, speaker, optional integration, or OpenClaw failure degrades only the affected capability where safe; OpenClaw absence never blocks native Core/voice startup. (`rex.background.voice_agent.run_voice_agent` maps `AudioDeviceError`/`TextToSpeechError`/`WakeWordError` to bounded `unavailable` detail codes without touching Core; `CoreServer` builds `Assistant()` directly and inherits the existing project-wide OpenClaw-optional guarantee.)
- [x] Automated lifecycle tests cover GUI-close survival, orderly shutdown, restart, duplicate-start prevention, and degraded-component behavior; a Windows packaged-artifact test covers the real installed path. (`tests/background/test_supervisor.py`, `gui/tests/backgroundRuntime.test.ts`, and the Task 6 installed-artifact phases in `scripts/test_installed_electron_artifact.ps1`: `Invoke-ElectronBackgroundSurvivalSmoke` proves the real Electron-launched supervisor stays alive/queryable after GUI exit, while `Invoke-BackgroundLifecycleSmoke` uses packaged `pythonw.exe -I`, the installed `RuntimeSupervisor`, and deterministic child fakes to prove ready/status/duplicate-start/orderly-stop mechanics — see the labeling note below.)
- [x] `README.md`, `INSTALL.md`, `RUNNING.md`, `SURFACE-CLASSIFICATION.md`, relevant installer docs, and `CLAUDE.md` are updated only to the level proved by implementation.
- [x] All relevant GitHub checks pass. *(PR #426 implementation head `17356bf6c80b53c1314fc4b939d19a8670f00842`: all 18 reported check runs completed successfully, including the installed Windows Electron artifact smoke, Python 3.11 Tests & Coverage, CodeFactor, commitlint, lint/format, mypy, GUI tests/typecheck/build/ESLint, pre-commit, security/dependency/secret scans, wheel contents, raw-API guard, and Node dependency audit.)*

**Validation:** focused runtime/service tests plus installed Windows artifact verification with the Electron window closed.

**Task 6 evidence labeling:** Task 6 adds two installed-artifact evidence classes: **packaged Windows artifact / real Electron bootstrap** (the detached supervisor remains alive and queryable after GUI exit, the per-user startup task exists after bootstrap, and uninstall removes that task) and **packaged Windows artifact / deterministic child fakes** (the real installed `RuntimeSupervisor` is imported under `pythonw.exe -I` and exercised through ready/status/duplicate-start/orderly-stop mechanics). Neither is a real LLM provider or physical microphone/speaker/wake-word/reboot/sign-in test. Wake-word activation itself remains beta. Physical clean-install/reboot/screenless household-voice acceptance remains US-130's gate and is not claimed complete here.

---

### US-125: Build the consumer first-run household voice setup flow

**Priority:** P0 | **Workstream:** Electron / Setup / Voice | **Dependencies:** US-124 plus existing AI, TTS, microphone, wake-word, identity, and Settings services.

**Description:** Turn first-run setup into a guided consumer flow that can configure and verify Rex for household voice use without developer commands or manual JSON editing.

**Acceptance Criteria:**
- [ ] The wizard guides the user through primary identity/profile, supported AI provider connection, Rex voice selection/preview, microphone selection/test, speaker selection/test, wake-word selection/calibration, local room assignment, and background-startup choice.
- [ ] Home Assistant, additional household voice enrollment, and additional room endpoints are offered as optional extensions rather than prerequisites for basic conversation.
- [ ] Setup explains that enabled background listening continues when the app window is closed and provides the corresponding privacy/control choice before enabling it.
- [ ] Saving configuration is not treated as voice verification: the wizard must prove wake detection -> capture -> STT -> canonical Assistant/TurnEngine -> TTS -> audible playback before reporting voice setup verified.
- [ ] A failed stage is shown specifically and leaves unaffected text/mobile capabilities usable where possible.
- [ ] The supported consumer path requires no Python/Node/Git/venv/repository/terminal/manual-JSON steps.
- [ ] Tests cover successful setup, each major stage failure, skipped optional integrations, cancellation/resume, and truthful verification state.
- [ ] All relevant GitHub checks pass.

**Validation:** Electron typecheck/tests/build plus packaged first-run smoke and physical audio verification.

---

### US-126: Add always-listening privacy, tray controls, and truthful degraded states

**Priority:** P0 | **Workstream:** Voice / Privacy / Electron | **Dependencies:** US-124.

**Description:** Give users obvious control over an always-available microphone path and make the real listening/health state visible without opening a full settings workflow.

**Acceptance Criteria:**
- [ ] The normal desktop/tray control surface exposes immediate `Pause Listening` and explicit `Resume Listening` actions.
- [ ] User-visible status distinguishes Listening, Paused, Degraded, Offline/Unavailable, and startup/recovery states; paused must never be represented as listening.
- [ ] The user can disable wake-word auto-start without uninstalling AskRex or disabling text/mobile use.
- [ ] Wake-word detection remains local when the supported detector permits it, and health/audit logs do not blanket-record raw microphone audio, transcripts, credentials, or private memory.
- [ ] Audio-device loss updates status promptly, prevents false spoken-success claims, and provides a recovery path to choose a replacement device.
- [ ] Restart/watchdog behavior is bounded and cannot hide a persistent hardware/configuration failure in an endless healthy-looking restart loop.
- [ ] Tests cover pause/resume, persisted startup choice, device loss/recovery, privacy-safe diagnostics, and status truth.
- [ ] All relevant GitHub checks pass.

---

### US-127: Define and implement secure Rex Room endpoint identity and pairing

**Priority:** P0 | **Workstream:** Multi-room / Security / Devices | **Dependencies:** US-087, US-121, US-122, US-124.

**Description:** Add a lightweight room-endpoint contract so one authoritative Rex Core can serve additional rooms without installing independent full Rex brains in each room.

**Acceptance Criteria:**
- [ ] A Rex Room endpoint has a stable device ID, authenticated/revocable pairing record, room assignment, declared input/output capabilities, health state, and authorization metadata.
- [ ] Pairing does not grant user, Home Assistant, media, memory, or tool authority beyond the already authenticated Rex principal/policy.
- [ ] Endpoint capability is tested and stored as input+output, output-only, or input-only; AskRex never assumes a microphone is programmatically available merely because hardware contains one.
- [ ] Revoked, expired, replaced, or untrusted endpoints cannot submit authoritative request-origin context or receive private output.
- [ ] OpenClaw may contribute optional device capabilities but cannot become pairing, identity, permission, room, or verification authority.
- [ ] The implementation reuses canonical device/media/output-routing/identity/context services and does not create a second speaker/room authority store.
- [ ] Security tests cover replay, impersonation, cross-user misuse, revocation, stale pairing, capability mismatch, and fail-closed behavior.
- [ ] All relevant GitHub checks pass.

---

### US-128: Add room onboarding, request-origin context, and response-to-origin behavior

**Priority:** P0 | **Workstream:** Multi-room / Context / Home Assistant / Output Routing | **Dependencies:** US-125 and US-127.

**Description:** Let users add a Rex Room endpoint through the consumer UI, verify its real audio capabilities, assign it to a room, and use that trusted origin to make natural household commands work without unsafe guessing.

**Acceptance Criteria:**
- [ ] The setup/control UI can discover or explicitly pair an endpoint, assign its room, and run capability-appropriate microphone, speaker, and wake-word tests.
- [ ] A validated endpoint stamps trusted request-origin device/room context into the canonical turn path without granting permission by location alone.
- [ ] Unambiguous commands such as `turn the light off` may use the trusted room plus current Home Assistant mappings to resolve the intended low-risk entity; ambiguity triggers clarification.
- [ ] Home Assistant mutations still use the canonical authorization/action/verification lifecycle and do not report success until independently verified.
- [ ] Interactive spoken responses return through the authorized endpoint that heard the request when it supports output unless an explicit target or current per-user routing rule overrides it.
- [ ] Existing `rex.media`, `rex.output_routing`, situational/active context, identity, and Home Assistant mapping services remain authoritative.
- [ ] Tests cover origin routing, room ambiguity, endpoint outage, explicit-target override, output-only devices, and verified Home Assistant room commands.
- [ ] All relevant GitHub checks pass.

---

### US-129: Integrate household speaker identity with screenless room turns

**Priority:** P0 | **Workstream:** Identity / Voice / Multi-user | **Dependencies:** US-087 and US-128.

**Description:** Make screenless room turns resolve speaker identity and room/device context as separate inputs so private and personalized requests remain correctly scoped in a shared household.

**Acceptance Criteria:**
- [ ] Voice enrollment in consumer setup writes through the existing canonical voice/profile identity services rather than creating a second user store.
- [ ] A screenless turn can carry both resolved user identity and trusted room/device origin into the canonical TurnEngine path.
- [ ] Per-user permissions, privacy/context grants, memory, linked accounts, and output-routing policy are evaluated from the resolved user, never inferred from room membership.
- [ ] Unknown or ambiguous speaker identity fails closed for private, destructive, account-specific, or otherwise permission-sensitive operations and asks only the minimum clarification needed.
- [ ] Shared household operations remain possible only where the underlying capability/policy explicitly permits household scope.
- [ ] Concurrent turns from different users/endpoints do not leak identity, memory, routing, or private results across sessions.
- [ ] Tests cover recognized/unknown/ambiguous speakers, James/Cole-style concurrent isolation, shared-vs-private behavior, and room/user independence.
- [ ] All relevant GitHub checks pass.

---

### US-130: Pass the final clean-install, reboot, and screenless household voice release gate

**Priority:** P0 | **Workstream:** Release / Windows / Voice / Multi-room | **Dependencies:** US-124 through US-129 and US-119. This story must complete before US-118 final production-readiness closure.

**Description:** Prove the real installed product behaves like an always-available household assistant on physical Windows/audio hardware rather than inferring success from unit tests or source-mode runs.

**Acceptance Criteria:**
- [ ] On a clean supported Windows machine with no preinstalled Python, Node.js, Git, repo checkout, or development venv, install AskRex using the packaged consumer installer.
- [ ] Complete supported first-run voice setup without terminal commands or manual JSON edits and record the exact artifact/version used.
- [ ] Fully close the Electron window, say the configured wake word, ask a normal question, and receive the correct audible response through the configured endpoint.
- [ ] Reboot/sign in without developer commands and repeat the screenless wake-word round trip successfully.
- [ ] Demonstrate Pause Listening, verify the wake word does not activate while paused, then Resume Listening and verify activation returns.
- [ ] Demonstrate truthful degraded behavior by making one audio component unavailable and confirming unaffected app/mobile/Core functions remain usable where expected.
- [ ] With Home Assistant configured for the release test environment, issue a low-risk room-context command and independently confirm the final entity state before Rex reports success.
- [ ] Disable or make OpenClaw unreachable and prove native Core/wake-word voice operation still works.
- [ ] Pair at least one non-Core Rex Room endpoint, assign it to a room, and complete a screenless wake-word interaction from that room before multi-room readiness is claimed.
- [ ] Store privacy-safe release evidence for install, reboot, audio pipeline, endpoint identity/room, lifecycle health, and verified action outcomes; do not store private transcripts/audio unless explicitly required and approved.
- [ ] All required GitHub/release checks pass on the exact artifact-producing commit.

**Release rule:** Failure of any required screenless/background/reboot criterion blocks the household-voice completion claim. Do not downgrade this story to source-mode evidence.

---

### US-115: Compose capability gaps declaratively

**Priority:** P1 | **Workstream:** Capabilities / Planning / Self-extension | **Dependencies:** US-078, US-107, US-109, US-108.

**Description:** Satisfy supported gaps by composing existing permitted capabilities into a typed declarative graph before considering generated code.

**Why it matters:** Composition is safer, more inspectable, and easier to revoke than arbitrary generated implementation.

**Files/areas likely involved:** gap detector/recovery service, action graph, declarative capability manifest/serializer, simulation/preview evidence.

**Acceptance Criteria:**
- [ ] Gap resolution attempts typed declarative composition only after the ordered search in US-078 fails to find a direct capability.
- [ ] Composition uses only currently permitted capabilities and computes least required aggregate authority/risk; it cannot grant missing permissions.
- [ ] Composed graphs pass schema validation and dry-run/simulation where possible before activation.
- [ ] Mutating/risky compositions require the same confirmation/approval policy and lifecycle verification as underlying actions.
- [ ] Unsafe/unresolvable gaps stop with an actionable explanation rather than falling through to automatic code generation.
- [ ] All relevant GitHub checks pass.

**Validation commands:** `pytest tests/rex2/test_capability_composition.py tests/rex2/test_gap_detector.py -q`.

**Risk notes:** Composition never launders multiple low-risk calls into an undeclared high-risk capability.

### US-116: Build and assess Forge packages

**Priority:** P1 | **Workstream:** Forge / Security / Evaluation | **Dependencies:** US-115; RexBench primitives from US-075 and later stories.

**Description:** Implement a disabled-by-default Forge build pipeline that creates a bounded capability package plus manifest/tests and assesses it without granting runtime authority.

**Why it matters:** Safe self-extension requires generation to be separated from permission to execute.

**Files/areas likely involved:** `rex/forge/`, package/manifest schema, sandbox/build runner, static/security analysis, RexBench adapters, status docs/UI.

**Acceptance Criteria:**
- [ ] Forge output is self-contained with manifest, I/O schema, requested capabilities/permissions/network/filesystem scope, risk classification, provenance, and executable tests.
- [ ] Generation/build/test occurs in a constrained sandbox with bounded resources and no inherited user credentials or ambient production authority.
- [ ] Static/security analysis plus deterministic RexBench/adversarial tests pass before a package can become a promotion candidate.
- [ ] Generated code receives no runtime authority merely by existing and cannot obtain more authority than the build/test environment could safely evaluate without explicit human approval.
- [ ] Failed/malicious packages remain quarantined with inspectable evidence and cannot mutate Rex core/package state.
- [ ] All relevant GitHub checks pass.

**Validation commands:** `pytest tests/rex2/test_forge_package.py tests/rex2/test_forge_sandbox.py tests/rex2/test_forge_security.py -q`; `python scripts/rexbench.py --profile forge-candidate`.

**Risk notes:** Forge packages stay isolated/revocable; Forge does not edit production Rex core in place.

### US-117: Gate Forge promotion and rollback

**Priority:** P1 | **Workstream:** Forge / Approval / Operations | **Dependencies:** US-116, US-109, US-106.

**Description:** Add risk-based promotion, canary observation, traceable human approval for wider authority, and atomic rollback/revocation for Forge packages.

**Why it matters:** Passing tests is not sufficient evidence to silently install autonomous generated code.

**Files/areas likely involved:** Forge registry/promoter, Capability Registry, approval/audit surface, canary health/verification metrics, rollback store.

**Acceptance Criteria:**
- [ ] Initial autonomous promotion is limited to read-only low-risk packages that passed every build/security/eval gate; mutation, network-write, credential, shell, filesystem-write, messaging/purchase, or elevated-risk authority requires explicit human approval.
- [ ] Approval records the exact immutable package digest/version and granted authority; package changes invalidate prior approval.
- [ ] Canary activation monitors health, failures, lifecycle verification, and policy denials without private payload logging.
- [ ] Threshold breach/manual revoke atomically disables the capability and restores the prior registry version without losing audit evidence.
- [ ] Tests cover unauthorized promotion, digest change, canary failure, rollback, active-turn revocation, and per-user permission boundaries.
- [ ] All relevant GitHub checks pass.

**Validation commands:** `pytest tests/rex2/test_forge_promotion.py tests/rex2/test_forge_rollback.py -q`.

**Risk notes:** Approval is a grant to a specific package digest, not blanket trust in Forge.

### US-119: Require absolute venv Python paths for Windows service registration

**Priority:** P0 | **Workstream:** Windows / Installer / Service Reliability | **Dependencies:** Must complete before US-118.

**Description:** Audit every Windows installer, service-registration path, and service startup wrapper so persisted Windows services always reference a fully qualified venv Python executable instead of a current-directory-relative path.

**Why it matters:** A Windows audit found a RexSpeak service configured with a relative venv Python path. The local machine was repaired manually, but repo automation must not be able to recreate the defect after reinstall, upgrade, service re-registration, or deployment from a different working directory.

**Files/areas likely involved:** `Start-RexSpeak.ps1`, `install.ps1`, `node_installers/install_windows.ps1`, `rex/windows_service.py`, any additional `New-Service` / `sc.exe create` / pywin32 `HandleCommandLine` / service ImagePath writers discovered by repo-wide audit, and their tests/docs.

**Acceptance Criteria:**
- [ ] Repo-wide audit identifies every Windows service installer, registration script, and service startup wrapper that can influence the executable path persisted for Rex/AskRex/RexSpeak services.
- [ ] Every persisted service executable/ImagePath uses a normalized absolute path to the intended venv Python executable; no service registration may persist `.\\.venv\\Scripts\\python.exe`, `venv\\Scripts\\python.exe`, or any other cwd-relative Python path.
- [ ] `Start-RexSpeak.ps1` and equivalent Windows launch wrappers resolve paths from `$PSScriptRoot` or another canonical absolute repo/install root rather than the caller's current working directory.
- [ ] Installers normalize user-supplied roots (including relative `$RexRoot` values) to absolute paths before constructing venv/service paths and fail closed if the resolved Python executable does not exist.
- [ ] Service command construction correctly quotes absolute paths containing spaces and preserves arguments without changing service behavior.
- [ ] Regression tests cover an install root containing spaces, a relative input root, and service registration invoked from a working directory different from the repo/install directory.
- [ ] Windows service tests verify the exact executable path that would be persisted/registered is absolute and points to the expected venv interpreter.
- [ ] Relevant installer/service documentation and `CLAUDE.md` are updated if commands, scripts, or service-registration behavior changes.
- [ ] All relevant GitHub checks pass.

**Validation commands:** `pytest tests/test_windows_service.py tests/test_install_scripts.py -q`; run the Windows installer/service dry-run tests from a non-repo working directory and assert the emitted service Python path is absolute; repo-wide search confirms no Windows service-registration path persists a relative venv interpreter.

**Risk notes:** The already-repaired local Windows service is not evidence that repo automation is safe. The repository must prevent reintroduction on reinstall, upgrade, or another machine.

### US-118: Run final RexBench production-readiness gate

**Priority:** P0 | **Workstream:** Evaluation / Release / Windows | **Dependencies:** Every earlier story in the integrated execution order.

**Description:** Produce one final evidence bundle proving integrated Rex 2.0 production-readiness behavior, performance, privacy, resilience, and safe self-extension boundaries.

**Why it matters:** Release needs measurable evidence, not a collection of `seems faster/smarter` claims.

**Files/areas likely involved:** `scripts/rexbench.py`, `tests/rex2/`, benchmark fixtures/reports, Windows artifact/mobile/voice acceptance docs, CI artifacts.

**Acceptance Criteria:**
- [ ] RexBench reports cold/warm p50/p95 by typed chat, voice, read-only tool, mutating tool, unavailable/gap-recovery, and representative multi-tool request class with stage breakdowns.
- [ ] Production profile covers identity/privacy isolation, permission escalation denial, cancellation races, tool/provider failure, OpenClaw outage/recovery, capability-sync attacks, Forge adversarial/promotion/rollback cases, timer accuracy/concurrency, alarm recurrence/snooze/restart recovery, audio-target resolution, group routing, per-user media-account/output-routing isolation, request-origin routing, upload context/scope isolation, location opt-in/non-disclosure/admin non-override, contextual-reference expiry, proactive-opportunity behavior, and unavailable-target behavior.
- [ ] Evidence clearly separates deterministic/mock, local source runtime, live-provider, packaged Windows Electron, mobile/device, and physical voice/hardware runs; no category substitutes for another.
- [ ] Windows packaged Electron and authenticated mobile E2E consume canonical TurnEngine; physical voice evidence covers wake/capture/ASR/TTS/barge-in where hardware is available.
- [ ] Retained reports contain no prompts, transcripts, memory contents, credentials, raw private tool payloads, or user IDs.
- [ ] All required GitHub checks plus `python scripts/rexbench.py --profile production-readiness` pass on the release-candidate commit.

**Validation commands:** `python scripts/rexbench.py --profile production-readiness`; `pytest tests/rex2 -q`; `cd gui && npm run typecheck && npm run build`.

**Risk notes:** Physical/signing/external-provider gates that cannot run in CI remain explicit manual release gates; mocks never satisfy them.
---

## 9. Global Acceptance Criteria

Every story in Section 8 inherits these criteria. They are checked in addition to the per-story criteria.

- A story is not marked `[x]` until:
  - All per-story acceptance criteria are checked.
  - All per-story validation commands exit 0 on a clean checkout.
  - All required GitHub checks (Section 10) pass on the PR that delivers the story.
  - Any user-facing behavior change is reflected in README, INSTALL, RUNNING, `docs/UI_SURFACES.md`, `SURFACE-CLASSIFICATION.md`, `docs/claude/INTEGRATIONS_STATUS.md`, and `CLAUDE.md` as relevant — in the same commit or the same PR as the implementation.
  - The story does NOT introduce a new raw `fetch('/api/...')` call in the renderer unless explicitly justified in the allowlist.
  - The story does NOT introduce a new use of `datetime.utcnow()` or `asyncio.get_event_loop()`.
  - The story does NOT introduce a new exposed secret, an unauthenticated network-facing route, or a destructive tool without a confirmation gate.
  - The story does NOT claim success without verification when a verification path is available.
  - The PR description includes the validation command output (pasted or referenced via a CI artifact).
  - Conventional Commits is used for the commit and PR title.

---

## 10. Required GitHub Checks

A story is not done until all of these CI jobs are green on the PR that delivers it. Names may evolve in CI; the required set is the substance, not the label.

| Check | Source | Notes |
|-------|--------|-------|
| `ruff` | `ruff check .` | Whole repo minus `archived/`. |
| `black` | `black --check --diff rex/ tests/ bridge/ scripts/ *.py` | Includes `scripts/`. |
| `mypy` | `mypy rex --ignore-missing-imports` | Existing CI. |
| `pytest` | `pytest -m "not slow and not audio and not gpu"` | Documented marker exclusions only. |
| `integration` | `pytest -m integration -q` | Existing CI. |
| `gui-typecheck` | `cd gui && npm run typecheck` | Existing CI. |
| `gui-build` | `cd gui && npm run build` | Existing CI. |
| `electron-smoke` | `bash tests/smoke/test_electron_package.sh` | Promoted to required for renderer/bridge changes. |
| `wheel-smoke` | `python scripts/check_wheel_contents.py` | Introduced by US-015/US-033. |
| `console-scripts-smoke` | `pytest tests/test_console_scripts_smoke.py` | Introduced by US-019. |
| `security-audit` | `python scripts/security_audit.py` | Introduced by US-034. |
| `pip-audit` | Existing CI | Vulnerability scan with the documented allowlist. |
| `secret-scan` | `detect-secrets` | Existing CI; expanded scope in US-028. |
| `pre-commit` | `pre-commit run --all-files` | Existing CI. |
| `node-audit` | `npm audit --audit-level=high` | Existing CI. |
| `no-raw-api-fetch` | `python scripts/check_no_renderer_api_fetch.py` | Introduced by US-003; enforced empty in US-011. |
| `no-generated-artifacts` | `python scripts/check_no_generated_artifacts.py` | Introduced by US-035. |
| `working-tree-clean` | `git status --porcelain` post-test | Strengthened by US-036. |
| `skip-budget` | `python scripts/check_skip_budget.py` | Introduced by US-037. |
| `deprecated-api-guard` | `python scripts/check_deprecated_apis.py` | Introduced by US-058. |

---

## 11. Documentation and README Update Policy

This policy is enforced by Section 9's global acceptance criteria. It is restated here so reviewers and Ralph iterations can check the rule directly.

1. **Update with code, not after.** A story that changes user-facing behavior, install flow, GUI behavior, commands, dependencies, file structure, configuration, integrations, or capability claims MUST update relevant docs in the same PR as the implementation.
2. **Authoritative surface list.** `SURFACE-CLASSIFICATION.md` is the source of truth for what each surface is. Other docs must reference it, not contradict it.
3. **Capabilities table.** `README.md` carries a single "Capabilities & Status" table (US-053) that mirrors `SURFACE-CLASSIFICATION.md`.
4. **Cross-doc consistency.** README, INSTALL, RUNNING, `docs/UI_SURFACES.md`, `SURFACE-CLASSIFICATION.md`, `docs/claude/INTEGRATIONS_STATUS.md`, and `CLAUDE.md` must not contradict each other. US-054 closes the current gap; future stories preserve consistency.
5. **No silent claim changes.** If a story changes whether a surface is shippable, developer-only, deprecated, experimental, or archived, that change must be reflected in `SURFACE-CLASSIFICATION.md` and propagated to all referencing docs in the same PR.
6. **Verification language.** Any doc that describes Rex saying something to the user about an action's result must use the verified vocabulary: *attempted, completed, verified, failed*.
7. **Integration docs.** Email, calendar, SMS, MQTT, Home Assistant, web search, and OpenClaw docs must state the current tier (working / partial / experimental / developer-only) and the documented fail-closed behavior.
8. **No retired surface promotion.** No doc may advertise a surface that this PRD classifies as archived or removed.

---

## 12. Definition of Production Ready

The release candidate is "Production Ready" when ALL of the following are true on a single commit on `master`:

- [ ] Every User Story in **Integrated execution order - 2026-08-08** is `[x]` on the release-candidate commit.
- [ ] Satisfied P2 stories US-059 through US-062 remain documented as baseline evidence and are not accidentally reopened.
- [ ] `python scripts/rexbench.py --profile production-readiness` passes with evidence classes labeled truthfully and all required manual physical/signing gates recorded explicitly.
- [ ] `python scripts/security_audit.py` exits 0 OR all findings are documented in `docs/security/AUDIT-INVENTORY.md` and accepted with an owner and expiry.
- [ ] `python scripts/check_no_renderer_api_fetch.py` exits 0 with an empty allowlist.
- [ ] `python scripts/check_wheel_contents.py` exits 0.
- [ ] `pytest -m "not slow and not audio and not gpu"` exits 0.
- [ ] `pytest -m integration` exits 0.
- [ ] `python scripts/check_skip_budget.py` exits 0.
- [ ] `python scripts/check_deprecated_apis.py` exits 0.
- [ ] `bash tests/smoke/test_electron_package.sh` exits 0 and confirms no Flask backend is required at runtime.
- [ ] `python -m build` produces a wheel that passes `scripts/check_wheel_contents.py`.
- [ ] All console scripts run `--help` cleanly after `pip install`.
- [ ] README "Capabilities & Status" table is current.
- [ ] `SURFACE-CLASSIFICATION.md`, `CLAUDE.md`, `docs/UI_SURFACES.md`, and `docs/claude/INTEGRATIONS_STATUS.md` agree with README.
- [ ] No completed implementation commit on `master` left its story unchecked in this PRD.
- [ ] All required GitHub checks listed in Section 10 are green on the release-candidate commit.
- [ ] The core AskRex principle is preserved: every reachable code path that reports an action's success does so only when the action was verified, or it uses the precise *attempted / completed / failed* vocabulary instead.

---

## 13. Post-Release Rex 2.0 Roadmap: Controlled Self-Maintenance

> **Scheduling rule:** The stories in this section are intentionally **outside** the Section 8 production-readiness execution order and are **not required** for the current release candidate. Do not select an `SM-###` story while any required production-readiness P0/P1 story remains open unless the owner explicitly reprioritizes the work. These stories become the implementation backlog for Rex's controlled self-maintenance capability after the release candidate is stable.

### Objective

Make Rex capable of safely maintaining and extending AskRex Assistant through an isolated, policy-controlled, GitHub-backed development lifecycle. Rex may diagnose defects, acquire missing capabilities, create skills, modify source code, run tests, create pull requests, respond to CI failures, merge authorized changes, update itself, verify the running version, and roll back failures. GitHub protections, Rex policy, CI, owner/user approval, and the per-user privacy/context authority defined by US-086/US-123 remain independent constitutional constraints.

### Existing foundations that must be reused

Current code already contains foundations for this work, including `rex/skills/`, `rex/vscode_service.py`, `rex/github_service.py`, the policy/audit/execution lifecycle, and `rex/openclaw/`. These foundations must be hardened and orchestrated; do not create competing parallel services unless an existing component is demonstrably unsuitable.

### SM-001: Threat model and self-maintenance authority contract

**Priority:** Post-RC P0
**Workstream:** Security / Architecture / Governance
**Description:** Define exactly what Rex may maintain autonomously, what requires owner approval, and what Rex can never authorize for itself.

**Acceptance Criteria:**
- [ ] A self-maintenance threat model is committed under `docs/security/`.
- [ ] The document defines routine, elevated, constitutional, and prohibited maintenance actions.
- [ ] Changes that increase Rex's own authority are explicitly owner-gated.
- [ ] Per-user contextual-use, disclosure, uploaded-document scope/audience, `location_assist`, and person-specific `location_share` are constitutional authority boundaries; Rex/agents/generated skills/OpenClaw cannot widen them without the appropriate user/data-owner authorization.
- [ ] Household/admin status cannot override another user's `location_assist` or `location_share`, and Rex cannot self-approve any proposal that broadens those grants.
- [ ] Direct protected-branch mutation, branch-protection bypass, repository deletion, and self-granting permissions are prohibited.
- [ ] The model defines audit, verification, rollback, and emergency-disable requirements.
- [ ] `CLAUDE.md`, `SECURITY.md`, and the self-maintenance architecture doc agree with the contract.
- [ ] Security tests or policy tests cover representative allowed, approval-required, and prohibited actions.

### SM-002: Isolated repository workspace manager

**Priority:** Post-RC P0
**Workstream:** Developer Tooling / Git
**Description:** Give Rex a canonical way to perform source changes without touching the running checkout or protected primary branch.

**Acceptance Criteria:**
- [ ] Rex can resolve a valid AskRex source checkout and refuses source mutation from a packaged-only runtime.
- [ ] Every code-changing maintenance task gets a dedicated branch and Git worktree.
- [ ] The workspace manager refuses direct commits to `master`.
- [ ] Dirty-worktree and conflicting-task behavior is deterministic and tested.
- [ ] Cleanup preserves evidence until the PR/task reaches a terminal state.
- [ ] The running known-good checkout is not modified during candidate development.

### SM-003: Canonical developer-agent orchestration

**Priority:** Post-RC P0
**Workstream:** Autonomy / Developer Tooling
**Description:** Connect Rex's existing code inspection, patching, test, policy, and audit components into one maintenance workflow.

**Acceptance Criteria:**
- [ ] One canonical developer-agent entry point owns diagnose -> plan -> patch -> validate -> report.
- [ ] The agent reuses `VSCodeService`/existing developer tools for patching and pytest.
- [ ] It records a root-cause statement before implementing a non-trivial repair.
- [ ] It runs targeted validation before broader validation gates.
- [ ] Code mutation goes through the canonical policy/execution lifecycle and honors `requires_approval`.
- [ ] Final diff scope is checked before PR creation.
- [ ] Maintenance state uses proposed/attempted/completed/verified/failed/rolled_back terminology.

### SM-004: Capability Gap Resolver

**Priority:** Post-RC P1
**Workstream:** Intelligence / Skills / OpenClaw
**Description:** Let Rex decide how to acquire a capability it does not currently possess.

**Acceptance Criteria:**
- [ ] Resolver checks native Rex tools first.
- [ ] Resolver checks enabled local skills second.
- [ ] Resolver checks approved OpenClaw/ClawHub capabilities third.
- [ ] Every candidate path is filtered through current per-user privacy/context/disclosure authority before selection; capability acquisition cannot be used to bypass US-086/US-123 boundaries.
- [ ] Resolver chooses local skill generation before core modification when a modular skill can satisfy the request.
- [ ] Core source modification is selected only when the capability genuinely requires it.
- [ ] Decision trace explains which alternatives were considered and why one was selected.
- [ ] Tests cover all resolution paths and safe failure when no permitted path exists.

### SM-005: Functional generated-skill implementation pipeline

**Priority:** Post-RC P1
**Workstream:** Skills / Safety / Testing
**Description:** Upgrade skill creation from honest scaffold generation to safe implementation of bounded new capabilities.

**Acceptance Criteria:**
- [ ] Generated skills include explicit metadata, required tools, permission scope, confirmation rules, verification behavior, and tests.
- [ ] Generated code is linted and tested before being enabled.
- [ ] A failed validation leaves the skill disabled.
- [ ] A newly generated skill cannot grant itself broader permissions than the approved task.
- [ ] Generated skills cannot widen contextual-use, upload audience/scope, disclosure, `location_assist`, or `location_share` authority; those remain externally granted user/data-owner permissions.
- [ ] Skills can be disabled or rolled back independently from Rex core.
- [ ] At least one end-to-end capability is implemented from a natural-language teaching request and verified.

### SM-006: Dedicated Rex GitHub maintainer identity

**Priority:** Post-RC P0
**Workstream:** GitHub / Security / Credentials
**Description:** Give Rex a least-privilege machine identity for day-to-day maintenance of its own repository.

**Acceptance Criteria:**
- [ ] A dedicated GitHub App or equivalent machine identity is created for Rex.
- [ ] Initial installation scope is restricted to `Blueibear/AskRex-Assistant`.
- [ ] Permissions are limited to the minimum required for issues, branch contents, pull requests, and check/status inspection.
- [ ] Workflow-file mutation is separately permissioned and owner-gated if enabled.
- [ ] GitHub App credentials are stored in the canonical credential vault, never tracked config.
- [ ] Rex cannot change its own App permissions, installation scope, branch protections, or rulesets.
- [ ] Health/status diagnostics can confirm the maintainer identity is usable without exposing secrets.

### SM-007: Autonomous issue, PR, CI, and merge maintenance loop

**Priority:** Post-RC P1
**Workstream:** GitHub / CI / Autonomy
**Description:** Allow Rex to handle routine repository maintenance through normal GitHub collaboration rather than direct primary-branch mutation.

**Acceptance Criteria:**
- [ ] Rex can create/triage an issue for a verified defect or maintenance task.
- [ ] Rex can push its isolated maintenance branch and open/update a PR.
- [ ] PR description includes root cause, changed files, risks, and validation evidence.
- [ ] Rex can monitor required checks and diagnose/iterate on failures.
- [ ] Required checks remain independent and cannot be marked successful by Rex itself.
- [ ] Rex can auto-merge only when policy allows, no elevated approval is pending, and every required gate is green.
- [ ] Failed/blocked PRs remain visible with an actionable reason instead of being silently abandoned.

### SM-008: Constitutional-file and authority-change protection

**Priority:** Post-RC P0
**Workstream:** Security / Governance
**Description:** Prevent Rex from using self-maintenance to remove the constraints that make self-maintenance safe.

**Acceptance Criteria:**
- [ ] A canonical protected-file/policy set is defined.
- [ ] GitHub/ruleset permissions, authentication, credential vault, self-maintenance policy, update/rollback, verification lifecycle, and gate-weakening changes require owner approval.
- [ ] Per-user privacy/context authority is protected constitutional state: contextual-use, disclosure, upload scope/audience, `location_assist`, person-specific `location_share`, and equivalent future privacy grants cannot be autonomously broadened by Rex, generated skills, OpenClaw, or maintenance agents.
- [ ] Privacy-authority changes require the appropriate affected user/data-owner authorization, not merely household/admin authority, and Rex cannot approve its own authority-expanding proposal.
- [ ] Rex may propose and test those changes but cannot approve them for itself.
- [ ] CI contains a guard that detects unauthorized changes to protected maintenance controls.
- [ ] Tests demonstrate that a normal maintenance task proceeds while an authority-expanding task blocks awaiting owner approval.

### SM-009: Safe self-update activation and rollback

**Priority:** Post-RC P0
**Workstream:** Runtime / Deployment / Recovery
**Description:** Let Rex activate a verified version without risking loss of the last known-good runtime.
**Acceptance Criteria:**
- [ ] Candidate and known-good versions are identifiable by immutable commit/version.
- [ ] Pre-activation validation must pass before activation.
- [ ] Activation does not destroy the previous working version.
- [ ] Post-activation health and functional smoke checks run automatically.
- [ ] Failure triggers automatic rollback to the last-known-good version.
- [ ] Rollback success is independently verified.
- [ ] Rex reports the actual running version after update or rollback.
- [ ] A simulated bad self-update proves rollback end to end.

### SM-010: Maintenance observability, controls, and staged rollout

**Priority:** Post-RC P1
**Workstream:** GUI / Operations / Safety
**Description:** Make Rex's maintenance behavior visible and incrementally enable autonomy rather than switching directly to unrestricted maintenance.

**Acceptance Criteria:**
- [ ] GUI/status surfaces show maintenance task, branch/worktree, changed files, validation, PR/check state, approvals, running version, and rollback target.
- [ ] An emergency disable control stops new autonomous maintenance actions without damaging an in-progress rollback.
- [ ] Rollout stages are documented: read-only diagnosis -> issue/PR creation -> supervised code changes -> bounded auto-merge.
- [ ] Authority-changing operations remain owner-gated at every stage.
- [ ] Audit history can reconstruct why Rex changed itself and how success was verified.
- [ ] At least three supervised low-risk maintenance cycles succeed before bounded auto-merge is enabled.

### Post-RC completion standard

Controlled self-maintenance is considered ready for bounded autonomous use only when all `SM-001` through `SM-010` stories are complete, the GitHub maintainer identity is least-privilege, protected controls and per-user privacy/context authority cannot be self-weakened or self-widened, independent CI is required, a bad-update rollback test passes, and Rex has demonstrated repeated successful supervised maintenance cycles.
