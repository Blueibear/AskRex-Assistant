# Deterministic Implementation Validation Gate Plan

> **For agentic workers:** REQUIRED SUB-SKILL: use TDD and verification-before-completion for every behavior change.

**Goal:** Prevent Ralph from sending a published implementation checkpoint to independent review until trusted local validation commands pass.

**Architecture:** Keep the existing worker and review states. On `ready_for_review`, run deterministic gates from trusted orchestrator configuration against the leased repository before changing state to `reviewing`. A normal gate failure returns exact command/output to implementation feedback; validation infrastructure failure blocks the workstream as `blocked_system`.

**Tech Stack:** Python 3.11, pytest, subprocess without shell execution, existing `OrchestratorConfig`, lease/provenance checks.

**Spec:** `docs/claude/DEVELOPMENT_ORCHESTRATOR.md`

## Global Constraints

- Preserve the existing Claude→Codex implementation fallback and fresh Sol review rule.
- Do not modify the frozen `rex-ai-pc-test` worktree.
- Validation commands are trusted local configuration, never model-supplied shell text.
- Command cwd must remain inside the role repository.
- A crash before validation state is durable must replay the durable implementation result, not reinvoke the worker.
- Keep Ralph infrastructure bounded; no new workflow state or validation service.

### Task 1: Validation runner

**Files:** create `scripts/dev_orchestrator/validation.py`; test via supervisor tests.

- [ ] Add a red test proving a failed configured gate prevents `reviewing` and returns command/output to `TaskItem.feedback`.
- [ ] Add a red test proving a passing gate permits `reviewing`.
- [ ] Implement a no-shell subprocess runner with bounded output, timeout handling, repo-confined cwd, and built-in role defaults plus task-prefix overrides.
- [ ] Treat missing executables/configuration errors as system failures rather than worker failures.

### Task 2: Supervisor integration and durable replay

**Files:** modify `scripts/dev_orchestrator/supervisor.py`; test `tests/scripts/test_dev_orchestrator_supervisor.py`.

- [ ] Run validation only after `_accept_result()` has accepted/published `ready_for_review`.
- [ ] On gate failure, save `implementing` with exact validation feedback and increment iteration; never call review.
- [ ] On pass, preserve the existing transition to `reviewing`.
- [ ] Verify durable-result replay performs validation without reinvoking the worker.

### Task 3: Config persistence, docs, and live S35 proof

**Files:** modify `scripts/dev_orchestrator/cli.py`, `CLAUDE.md`, `docs/claude/DEVELOPMENT_ORCHESTRATOR.md`, and production coordination config.

- [ ] Preserve `metadata` through config save/load and add a red-green persistence test.
- [ ] Configure an S35-specific backend gate for speech/mobile API tests plus branch diff checking; retain broad built-in defaults for later tasks.
- [ ] Run the complete orchestrator suite and static gates, commit/push, restart supervisor/watchdog.
- [ ] Live-prove the known stale TTS assertion is caught before review, then allow Ralph to repair it and advance only after green validation.
