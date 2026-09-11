# AskRex Development Orchestrator Design

Date: 2026-09-10
Status: Approved design captured from the James/ChatGPT architecture discussion

## Goal

Build a local, durable lead-engineering system that keeps AskRex backend/desktop and mobile development progressing without requiring repeated "please continue" messages, while preserving repository ownership, independent review, human-only gates, and the frozen PC-test worktree.

## Core architecture

The orchestration runtime is a deterministic Python process. It owns process health, worker state, retry rules, mailbox checks, model routing, escalation, and Ralph-style iteration. Ordinary supervision consumes no model tokens.

Astra (`gpt-6-astra`) is the event-driven lead engineer, not the polling loop. It is invoked only when a new task must be selected, a repeated/ambiguous failure needs adjudication, cross-repo priorities conflict, or final release readiness needs higher-level judgment.

Claude Code is the primary implementer. Codex is the independent reviewer/verifier. Their results are structured and persisted outside model context so a fresh invocation can continue from durable repository and coordination state.
## Workstreams and ownership

Backend/desktop development root:
`C:\Users\james\rex-ai-test\rex-ai-us126-final`

Mobile development root:
`C:\Users\james\rex-ai-test\askrex-mobile-sdk57`

Frozen live-test root:
`C:\Users\james\rex-ai-test\rex-ai-pc-test`

The orchestrator may read the frozen worktree only when a verification workflow explicitly requires evidence. It must never modify, reset, clean, reinstall into, restart from, or use it as a development workspace.

Backend agents may not edit the mobile repository and mobile agents may not edit the backend repository unless ownership is explicitly reassigned through the shared coordination protocol.
## Shared coordination source of truth

The existing directory `C:\Users\james\rex-ai-test\askrex-coordination` remains authoritative for `PROTOCOL.md`, agent-role files, mailboxes, live-test issues, and cross-repo contracts.

The orchestrator adds only new, bounded operational areas:

- `mailbox\supervisor\` for worker-to-lead escalation.
- `state\backend.json` and `state\mobile.json` for machine-readable worker state.
- `queues\backend.json` and `queues\mobile.json` for durable task queues.
- `alerts\` for human-action requests and audit-visible notifications.
- `logs\` for content-bounded supervisor execution logs.

Coordination records must never contain credentials, auth tokens, private conversation contents, or secrets.
## Model routing

Routine Codex review uses `gpt-5.6-terra`. Difficult or repeated review failures escalate to `gpt-5.6-sol`. Lead-engineer decisions use `gpt-6-astra` explicitly so the user's global Codex default cannot accidentally route ordinary work to Astra.

Routine Claude Code implementation uses model alias `sonnet`. Repeated implementation failure escalates to `opus` for the current task only.

Astra is invoked only for lead events. It does not poll heartbeats or inspect unchanged state every cycle.

Every model invocation must use an explicit model selection and a bounded role prompt. No worker may inherit the global `gpt-6-astra` default for routine work.
## Ralph-style state machine

Each workstream has exactly one current task and one machine-readable state:
`idle`, `planning`, `implementing`, `reviewing`, `blocked_user`, `blocked_system`, or `done`.

A task cycles through implementation and independent review until the reviewer passes it, the task becomes a human/system blocker, or a bounded failure threshold triggers escalation. A completed task clears the worker task and causes Astra to select the next highest-priority actionable item from authoritative project sources.

Fresh model sessions are preferred between completed tasks to limit context/token growth. Session IDs may be retained within one task when the CLI returns them reliably, but correctness must rely on files, git history, tests, and coordination state rather than chat memory.

The loop is not an uncontrolled `while true`: subprocess timeouts, repeated-failure thresholds, stale-heartbeat detection, and human-only gates stop or escalate a workstream deterministically.
## Structured agent outcomes

Every implementer, reviewer, and lead invocation must emit a validated JSON result. At minimum it includes `outcome`, `summary`, `next_action`, `needs_user`, and `blocker_reason`.

Allowed implementation outcomes are `continue`, `ready_for_review`, `blocked_user`, `blocked_system`, and `failed`.
Allowed review outcomes are `pass`, `changes_required`, `blocked_user`, `blocked_system`, and `failed`.
Allowed lead outcomes are `assign`, `done`, `blocked_user`, `blocked_system`, and `failed`.

Prose alone is not authority. The supervisor validates the result schema before changing task state. Invalid output becomes a bounded system failure and may be retried/escalated; it is never treated as success.
## Usage and banked resets

The supervisor tracks a user-configured count of banked Work/Codex resets, initially `3`, only for planning and notification. It must not claim a reset was consumed unless the platform confirms that result.

There is no supported unattended reset API in the installed Codex CLI. Therefore reset consumption remains `blocked_user`: the supervisor records that a banked reset is recommended and tells James exactly why. It must not click Settings through brittle UI automation or decrement the stored count preemptively.

Before requesting a reset, the supervisor may continue useful work through the independent Claude Code allowance when task ownership and review policy permit it. The final remaining reset is reserved by default unless James overrides the policy.
## Safety and permissions

The supervisor never uses `--dangerously-bypass-approvals-and-sandbox` or Claude's bypass-permissions mode. Implementers use the installed CLIs' supported automatic/approval mechanisms; reviewers and Astra planning run read-only whenever mutation is unnecessary.

The supervisor never force-pushes, deletes branches, disables tests, weakens required checks, or changes repository/security authority to make a task pass. Existing `CLAUDE.md`, `AGENTS.md`, PRD, and coordination ownership rules remain higher-priority instructions for workers.

A human-only gate includes physical iPhone/Windows testing, credentials or login, account/billing actions, authority/security changes requiring owner approval, and banked-reset confirmation. Those states stop only the affected workstream; the other workstream may continue.
## Process lifecycle and watchdog

The primary supervisor is a long-running local process with a single-instance lock and atomic heartbeat. It performs inexpensive deterministic checks frequently and invokes models only when state transitions require them.

A Windows Task Scheduler watchdog runs every 30 minutes. Its job is only to verify that the supervisor heartbeat is fresh and restart the supervisor when it is absent/stale. The watchdog does not invoke AI itself.

Initial rollout must support observe-only mode. Activation requires explicit worker handoff so the new CLI agents never race the currently active browser workers in the same worktrees.

The existing hourly ChatGPT browser supervisor may remain enabled during rollout, but once the local orchestrator owns both workstreams it should not send browser continuations that could create concurrent writers.
## Completion policy

Astra may declare a workstream or the overall project `done` only after objective evidence shows the relevant authoritative backlog is exhausted and required validation gates are satisfied. A worker's self-reported completion is insufficient.

Project-level completion must account for backend tests/quality gates, mobile tests/type/lint/Expo checks, required CI, documentation truth, unresolved coordination issues, cross-repo contract acknowledgments, required real-device/Windows acceptance, and clean intentional working-tree state.

If a required physical acceptance cannot be automated, the system remains `blocked_user` rather than falsely reporting release readiness.

## Testing strategy

The orchestrator is standard-library Python and receives subprocess, clock, filesystem, and notifier dependencies through testable boundaries. Unit tests use fake runners and temporary directories; they must never invoke paid models or modify either real development worktree.

Tests cover explicit model overrides, queue/state transitions, review escalation, Astra gating, usage-limit handling, reset accounting, cross-workstream independence, frozen-worktree protection, stale-heartbeat watchdog behavior, malformed agent output, and observe-only rollout.