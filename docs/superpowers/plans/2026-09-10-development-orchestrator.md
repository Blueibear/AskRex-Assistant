# AskRex Development Orchestrator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and locally install a safe, deterministic supervisor that drives independent backend and mobile Ralph-style development loops using Claude Code, Codex Terra/Sol, and event-driven Astra.

**Architecture:** Versioned orchestration code lives under `scripts/dev_orchestrator/` in AskRex-Assistant. Mutable state, queues, alerts, logs, and runtime config live under the external `askrex-coordination` directory. A long-running Python supervisor owns state transitions; a 30-minute Windows watchdog only restarts a stale/dead supervisor.

**Tech Stack:** Python 3.11 standard library, Claude Code 2.1.238+, Codex CLI 0.153.4+, Windows Task Scheduler, existing AskRex coordination protocol.

**Spec:** `docs/superpowers/specs/2026-09-10-development-orchestrator-design.md`

## Global Constraints

- Never modify `C:\Users\james\rex-ai-test\rex-ai-pc-test`.
- Never clean/reset either active development worktree.
- Explicitly select a model on every model invocation.
- Routine Codex review: `gpt-5.6-terra`; escalation: `gpt-5.6-sol`; lead: `gpt-6-astra`.
- Claude implementation: `sonnet`; escalation: `opus`.
- Never use dangerous approval/sandbox bypass flags.
- Banked reset consumption remains a human-confirmed gate; configured starting count is 3.
- Roll out in observe-only mode until current browser workers hand off.

---
### Task 1: Durable config, state, queues, and result schemas

**Files:**
- Create: `scripts/dev_orchestrator/__init__.py`
- Create: `scripts/dev_orchestrator/types.py`
- Create: `scripts/dev_orchestrator/storage.py`
- Create: `scripts/dev_orchestrator/schema.py`
- Test: `tests/scripts/test_dev_orchestrator_storage.py`

**Interfaces:**
- Produces `OrchestratorConfig`, `WorkerState`, `TaskItem`, `AgentResult`, `AtomicJsonStore`, and `validate_agent_result()`.

- [ ] Write failing tests proving atomic JSON persistence, allowed state/outcome vocabularies, default reset count `3`, and frozen-worktree rejection.
- [ ] Run `pytest -q tests/scripts/test_dev_orchestrator_storage.py` and confirm RED.
- [ ] Implement only standard-library dataclasses/enums and atomic replace-based JSON storage.
- [ ] Re-run the focused tests and confirm GREEN.
- [ ] Commit the task.
### Task 2: Safe model routing and CLI command construction

**Files:**
- Create: `scripts/dev_orchestrator/routing.py`
- Create: `scripts/dev_orchestrator/runner.py`
- Test: `tests/scripts/test_dev_orchestrator_routing.py`

**Interfaces:**
- Consumes `WorkerState` and `TaskItem`.
- Produces `select_implementer_model()`, `select_reviewer_model()`, `build_claude_command()`, `build_codex_command()`, and usage-limit classification.

- [ ] Write failing tests asserting routine/retry model choices and that every Codex command contains `-m` with Terra/Sol/Astra as appropriate.
- [ ] Assert no command contains `dangerously-bypass`, Claude `bypassPermissions`, or a path equal to the frozen worktree.
- [ ] Assert Claude routine commands use `sonnet`, escalation uses `opus`, and structured-output flags are present.
- [ ] Implement subprocess execution with captured stdout/stderr, timeout, and explicit cwd.
- [ ] Parse structured JSON results and classify usage-limit/overload/auth failures without treating them as task success.
- [ ] Run focused tests and commit.
### Task 3: Coordination scanner and Ralph workstream state machine

**Files:**
- Create: `scripts/dev_orchestrator/coordination.py`
- Create: `scripts/dev_orchestrator/supervisor.py`
- Test: `tests/scripts/test_dev_orchestrator_supervisor.py`

**Interfaces:**
- Consumes coordination root, worker config, queue/state stores, and an injected agent runner.
- Produces `Supervisor.run_cycle()` and per-role transitions.

- [ ] Write failing tests for independent backend/mobile progress, observe-only mode, mailbox context, implementation→review→changes→implementation loops, review pass→task completion, queue-empty→Astra planning, human blockers, and repeated-failure escalation.
- [ ] Implement bounded coordination summaries that include protocol, role file, owned open issues, and mailbox filenames/content without secrets expansion.
- [ ] Implement one-task-per-role durable queues and state transitions with per-task iteration/failure counters.
- [ ] Make Astra selection event-driven only: queue empty, adjudication threshold, or final-completion check.
- [ ] Make one blocked workstream leave the other runnable.
- [ ] Run focused tests and commit.
### Task 4: Usage-reset policy and user alerts

**Files:**
- Create: `scripts/dev_orchestrator/usage.py`
- Create: `scripts/dev_orchestrator/alerts.py`
- Test: `tests/scripts/test_dev_orchestrator_usage.py`

**Interfaces:**
- Produces `UsageBudget`, `handle_usage_limit()`, and file-backed `AlertSink`.

- [ ] Write failing tests for starting reset count 3, preserving the final reset by default, no decrement before confirmation, independent Claude fallback, and exact `blocked_user` alert content.
- [ ] Implement reset recommendation/accounting without any unattended Settings click or purchase action.
- [ ] Persist alerts atomically under `coordination\alerts` and de-duplicate identical active blockers.
- [ ] Run focused tests and commit.

### Task 5: CLI, heartbeat, and Windows watchdog

**Files:**
- Create: `scripts/dev_orchestrator/cli.py`
- Create: `scripts/dev_orchestrator/windows_watchdog.ps1`
- Test: `tests/scripts/test_dev_orchestrator_cli.py`

- [ ] Write failing tests for `cycle`, `run`, `status`, `init`, and observe-only behavior.
- [ ] Implement a single-instance lock, atomic supervisor heartbeat, bounded polling loop, clean shutdown, and status output.
- [ ] Implement watchdog logic that only restarts a missing/stale supervisor and never runs models itself.
- [ ] Add an `init` command that creates runtime directories/config without overwriting existing coordination files.
- [ ] Run focused tests and commit.
### Task 6: Protocol integration, rollout, and verification

**Files:**
- Modify: `CLAUDE.md` Cross-Instance Coordination section
- Create: `docs/claude/DEVELOPMENT_ORCHESTRATOR.md`
- Runtime-only: `C:\Users\james\rex-ai-test\askrex-coordination\AGENT_SUPERVISOR.md`
- Runtime-only: coordination `mailbox\supervisor`, `state`, `queues`, `alerts`, and `logs`

- [ ] Update backend agent instructions to recognize supervisor-owned state without weakening existing ownership/security rules.
- [ ] Initialize live coordination runtime in observe-only mode with explicit backend/mobile paths and model routes.
- [ ] Send backend/mobile mailbox handoff messages: finish the current checkpoint, acknowledge supervisor handoff, then do not start another browser-driven task.
- [ ] Run all orchestrator tests, Ruff/Black on new Python files, `git diff --check`, and inspect the complete diff.
- [ ] Run a fake-runner dry cycle proving both workstreams can progress independently without paid model calls.
- [ ] Register the 30-minute Windows watchdog only after the supervisor command passes dry-run verification; keep model execution observe-only until worker handoff is acknowledged.
- [ ] Commit and report activation status truthfully.