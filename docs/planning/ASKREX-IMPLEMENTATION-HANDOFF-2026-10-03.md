# Post-Phase-0 AskRex implementation handoff

Status: implementation guide to be reconciled into `PRD-production-readiness.md` **after** the active PR #433 CI repair; not a competing authoritative story queue. Ralph may not change the current task merely because this file exists.

## Scheduling contract

`PRD-production-readiness.md` and its 2026-08-08 integrated execution order remain the only release-readiness authority; the July audit ledger and September/October test evidence supplement it. A task is one complete user story with tested acceptance and exact-HEAD required CI. Check the active PRD for already-completed structural TurnEngine, mobile auth, Windows service and first-run work; do not reimplement checked stories or reopen physical acceptance from a unit test. Use each role's shared-coordination mailbox and issue ownership. Never make backend/mobile contract changes without an acknowledged cross-repo handoff.

## Phase 1 — Current release-gate CI repair

Keep `backend-ci-release-gate-repair-001` and PR #433 as the backend's active task until independently reviewed, its actual Ruff/mypy/GUI/security/pre-commit/CodeFactor findings resolved, and the exact PR-head required CI is green. Classify Detect Secrets findings individually; genuine secrets require remediation, not an ignored gate. Preserve previous SECURITY-HA-MACOS and TEST-012 implementation. Physical TEST-012/013/014 remain testing-owned.

## Phase 2 — Truthful feature-evidence inventory

Reconcile the independent October review with the July audit ledger, canonical integration status, current code/tests, and new installed-artifact evidence. For each core capability record source revision, implementation, focused tests, exact-head CI, live external-provider result, physical device outcome and truthful UI state. Sample mobile data, notification fixtures, Twilio send-versus-delivery and Home Assistant confirmation are **investigation targets**, not already-proven regressions. Create bounded owner-specific issues only for still-reproducible gaps; preserve existing issue histories and use canonical status vocabulary.

## Phase 3 — Minimum dependable Windows assistant

First controlled hands-on Windows gate: install/start, chat and durable history, shared identity and private memory, supported hold-to-talk, model availability/fallback, Home Assistant read and confirmed low-risk mutations, timers/reminders, basic notifications and truthful timeout/denied/unverified reporting. Validate long sessions, restart, microphone loss, disconnected HA, model outage and interrupted external actions. Do not imply the complete release gate has passed.

## Phase 4 — Complete remaining integrated PRD stories

Follow the remaining integrated execution order: safe prefetch, voice diagnostics/enrollment/wake assets/TTS testing and streaming, truthful desktop settings/navigation/integrations, usable HA panel, Outlook and email/SMS scope, canonical per-user history/shopping/memory/documents, procedural experience only after verified actions, shared speaker/room routing and identity. Keep provider authorization, per-user scoping and canonical action verification intact.

## Phase 5 — Mobile parity after independent budget/credential clearance

Keep implemented gateway pairing, token revocation, HTTPS, cross-surface TurnEngine, and per-user grants. Replace any confirmed fake UI success/data with disabled or accurate live states, then deliver actual mobile notifications, approvals, tasks, workflows, audit and settings one tested story at a time. Test on physical iPhone only with explicit owner participation. Do not clear the current unrelated API-budget blocker or infer consent to pay.

## Phase 6 — Always-on household voice

Complete US-126 (pause/resume/privacy and truthful degraded status), US-127 (authenticated revocable room endpoints), US-128 (trusted room origin and response routing), US-129 (speaker identity separate from room authority), and US-130 (physical clean-install/reboot/screenless/audio/HA/room acceptance). Existing US-124 and US-125 are implemented and locally/CI tested but still require their explicit end-to-end hardware evidence.

## Phase 7 — Advanced capabilities and streaming music

Follow the approved self-extension and optional integration work: OpenClaw, remote computer/browser/developer capabilities, real-provider email/calendar/messaging, media and speaker providers, Forge's simulation/approval/canary/rollback and authorization boundaries. Do not block the minimum Windows milestone on optional integrations. Media-specific provider gaps and Apple Music's official versus optional third-party routes are defined in `docs/planning/MEDIA-SERVICES-PROVIDER-ROADMAP-2026-10-03.md`. Current US-121/122 are media infrastructure, **not** evidence that Apple Music or Spotify account linking and playback are live.

## Phase 8 — Final release gate

US-130 physical household-voice evidence; US-131 supported macOS package and real Keychain/first-launch evidence; final US-118 RexBench across accuracy, outages, privacy, action verification, latency, device routing, genuine live providers, security, CI and signed artifact policy. Owner-gated actions remain owner-gated. Publication requires final documented approval, not merely all checkboxes marked by an agent.

## Delivery protocol for Ralph

At each task: read `CLAUDE.md` and current shared protocol, validate the real owner and prerequisites, add failing focused regressions before behavioral changes, implement only one bounded story, run required gates in the isolated validator, preserve evidence and safe patch artifacts, update the PRD/progress ledger in the same product commit, obtain an independent review, then require exact-head GitHub checks. A failed validator returns to implementation; external hardware/credentials/authority requests must stop with a truthful blocker. Notify James on phase change, failure, a performed repair, a human decision, or verified readiness—not unchanged routine status.
