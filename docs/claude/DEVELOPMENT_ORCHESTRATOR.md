# AskRex Development Orchestrator

## Purpose

The development orchestrator keeps the backend/desktop and mobile AskRex workstreams moving without requiring repeated browser-chat continuation prompts. It is an operator/development tool, not part of the shipped AskRex runtime.

The orchestrator combines:

- deterministic Python supervision and durable state;
- Claude Code implementation loops;
- independent Codex review using GPT-5.6 Terra/Sol;
- event-driven GPT-6 Astra lead-engineer decisions;
- the shared cross-instance coordination protocol;
- a model-free Windows watchdog.

The authoritative design is `docs/superpowers/specs/2026-09-10-development-orchestrator-design.md`.

## Repository ownership

Backend/desktop and mobile remain separate workstreams. The supervisor may route work and read shared coordination state, but it does not grant one worker permission to edit the other repository.

Never configure `C:\Users\james\rex-ai-test\rex-ai-pc-test` as a development worker. That worktree remains frozen for live acceptance testing.

## Model routing

Every invocation selects its model explicitly. Do not rely on the user's global Codex default.

| Role | Routine | Escalation |
|---|---|---|
| Claude implementation | `sonnet` | `opus` |
| Codex independent review | `gpt-5.6-terra` | `gpt-5.6-sol` |
| Lead/adjudication | `gpt-6-astra` | n/a |

Astra is event-driven. Use it when a queue needs a next-task decision, repeated failures need adjudication, or final completion needs lead-level judgment. It must not poll unchanged state merely to keep a heartbeat alive.

## Ralph-style lifecycle

Each repo has one durable current task and one state: `idle`, `planning`, `implementing`, `reviewing`, `blocked_user`, `blocked_system`, or `done`.

Implementation may return `continue` or `ready_for_review`. Independent review must return `pass` before the task is cleared. `changes_required` returns review feedback to the implementer. Repeated failures escalate models and eventually invoke Astra for adjudication.

A blocker affects only its workstream. Backend may continue while mobile awaits an iPhone test, and mobile may continue while backend awaits a Windows acceptance step.

## Human gates and banked resets

Physical device tests, credentials/login, account or billing changes, security-authority changes, and banked Work/Codex reset consumption are human gates.

The supervisor records three banked resets initially and reserves the final reset by default. A usage limit may create a `blocked_user` reset recommendation, but the tool never clicks ChatGPT Settings, purchases usage, or decrements the count before confirmation.

After James confirms the platform's remaining reset count with `confirm-resets`, only workers blocked specifically by `usage_limit` are released. They resume their exact prior phase; a reviewer blocked by usage returns to `reviewing`, not implementation. Unrelated physical/account blockers remain stopped.

## CLI

Run from the AskRex-Assistant checkout that contains `scripts/dev_orchestrator`:

```powershell
py -3.11 -m scripts.dev_orchestrator.cli status --coordination-root C:\Users\james\rex-ai-test\askrex-coordination
py -3.11 -m scripts.dev_orchestrator.cli pause --coordination-root C:\Users\james\rex-ai-test\askrex-coordination
py -3.11 -m scripts.dev_orchestrator.cli activate --coordination-root C:\Users\james\rex-ai-test\askrex-coordination
py -3.11 -m scripts.dev_orchestrator.cli confirm-resets --coordination-root C:\Users\james\rex-ai-test\askrex-coordination --remaining 2
```

Initialize a new coordination runtime only with explicit roots:

```powershell
py -3.11 -m scripts.dev_orchestrator.cli init `
  --coordination-root C:\Users\james\rex-ai-test\askrex-coordination `
  --backend-root C:\Users\james\rex-ai-test\rex-ai-us126-final `
  --mobile-root C:\Users\james\rex-ai-test\askrex-mobile-sdk57 `
  --frozen-worktree C:\Users\james\rex-ai-test\rex-ai-pc-test
```

`init` is non-destructive and defaults to observe-only. `cycle` performs one supervision cycle. `run` holds the single-instance lock, maintains a background heartbeat, reloads config every cycle, and continues until stopped.

## Watchdog

`scripts/dev_orchestrator/windows_watchdog.ps1` checks only the supervisor heartbeat. It must contain no Codex, Claude, or model invocation logic. Windows Task Scheduler may run it every 30 minutes to restart a missing/stale supervisor.

The supervisor itself performs frequent deterministic checks. A background heartbeat remains fresh while a long Claude/Codex subprocess is running, so the watchdog must not mistake legitimate model work for a dead supervisor.

## Rollout rule

Never let browser-based coding workers and CLI orchestrator workers write the same worktree concurrently. Initialize and test in observe-only mode, request an explicit handoff from the current browser workers, then activate the CLI supervisor only after both workstreams have stopped browser-driven implementation.
