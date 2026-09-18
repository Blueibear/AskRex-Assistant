# AskRex Development Orchestrator

## Purpose

The development orchestrator keeps the backend/desktop and mobile AskRex workstreams moving without requiring repeated browser-chat continuation prompts. It is an operator/development tool, not part of the shipped AskRex runtime.

The orchestrator combines:

- deterministic Python supervision and durable state;
- Claude Code implementation loops with Codex as the implementation fallback;
- read-only OpenAI API Terra/Sol reasoning with matching Codex fallbacks;
- emergency-only GPT-6 Astra adjudication after the Sol chain remains explicitly unresolved;
- the shared cross-instance coordination protocol;
- a model-free Windows watchdog.

The authoritative design is `docs/superpowers/specs/2026-09-10-development-orchestrator-design.md`.

## Repository ownership

Backend/desktop and mobile remain separate workstreams. The supervisor may route work and read shared coordination state, but it does not grant one worker permission to edit the other repository.

Never configure `C:\Users\james\rex-ai-test\rex-ai-pc-test` as a development worker. That worktree remains frozen for live acceptance testing.

## Model routing

Every invocation selects its provider/model explicitly. Do not rely on the user's global Codex default. OpenAI API routing is disabled by default.

| Phase | Primary | Fallback |
|---|---|---|
| Implementation | Claude `sonnet` / escalated `opus` | Codex implementation |
| Routine review | OpenAI API `gpt-5.6-terra` | Codex `gpt-5.6-terra` |
| Escalated review | OpenAI API `gpt-5.6-sol` | Codex `gpt-5.6-sol` |
| Review after Codex implementation | OpenAI API `gpt-5.6-sol` | Codex `gpt-5.6-sol` |
| Planning | OpenAI API `gpt-5.6-sol` | Codex `gpt-5.6-sol` |
| Adjudication | OpenAI API Sol, then Codex Sol | API `gpt-6-astra` only after a valid unresolved `failed` result and all emergency gates |
| `FINAL-VERIFY-*` | Codex `gpt-5.6-sol` only | none |

Astra is not a routine planner, reviewer, implementer, or final verifier. It may be attempted at most once for a revision-bound task escalation episode. API/Codex transport unavailability alone never justifies Astra; the lower-cost Sol chain must produce a valid explicitly unresolved result first.

## Ralph-style lifecycle

Each repo has one durable current task and one state: `idle`, `planning`, `implementing`, `reviewing`, `blocked_user`, `blocked_system`, or `done`.

Implementation may return `continue` or `ready_for_review`. A published `ready_for_review` checkpoint must pass the supervisor's configured deterministic local validation gates before the workstream may enter independent review. Normal gate failures return the exact gate, command, exit code, and captured output to implementation feedback; validation-infrastructure failures park as `blocked_system` while preserving the durable implementation result so recovery retries validation without reinvoking the worker. Independent review must return `pass` before the task is cleared. `changes_required` returns review feedback to the implementer. Repeated failures escalate within the configured routing policy. Astra is considered only for adjudication after the API Sol and Codex Sol chain returns a valid explicitly unresolved `failed` result and the emergency threshold/episode/budget gates pass.

A blocker affects only its workstream. Backend may continue while mobile awaits an iPhone test, and mobile may continue while backend awaits a Windows acceptance step. Backend and mobile role cycles execute independently so one long model invocation does not serialize the other workstream.

## Coordination authority

Workers receive a bounded read-only snapshot of protocol, role mailbox, and owned issues. They do not receive filesystem write access to the authoritative coordination root. Mailbox messages and owned issue transitions are returned as structured result fields; the deterministic supervisor validates recipient, ownership, and status before writing them. Development may request `fixed-needs-retest` but never writes the canonical `TEST-*.md` status. The supervisor emits an atomic retest request to `mailbox/testing`; the testing role alone updates canonical live-test status and verification.

Activation requires an exclusive nonce-based lease for both development worktrees. The supervisor creates a request bound to a browser-session correlation identifier. The trusted local Windows operator verifies that the browser worker checkpointed and stopped writing, then records `operator-ack-handoff`. The session identifier is correlation evidence, not cryptographic authentication. Lease acceptance records the operator identity, waits through a quiescence interval, and revalidates the linked non-protected worktree, expected GitHub repository, exact `origin/<current-branch>` upstream, branch, HEAD, and cleanliness. Supervisor and per-role control-plane lock pathnames persist; ownership is an OS/kernel-held exclusive file lock and is never reclaimed by PID inspection or pathname deletion. Claude commits must include the invocation trailer supplied by the supervisor, and every post-invocation commit is verified against that provenance before the lease HEAD advances. Every production model invocation uses a disposable scratch clone instead of the leased live worktree. Claude runs inside the pinned `askrex-claude-code:2.1.238` Docker sandbox as a non-root user with Linux capabilities dropped, `no-new-privileges`, and no Bash tool. Production model prompts are streamed over stdin rather than embedded in Windows process argv, preventing large coordination/review/validation context from exceeding `CreateProcess` command-line limits. Claude is the preferred implementer; if and only if Claude returns a classified usage/session limit, implementation falls back to Codex in a writable disposable scratch clone under the same lease, provenance, result-binding, and checkpoint publication gates. The Codex fallback prompt permits only workspace-local repository/terminal tools inside that scratch clone; it does not inherit Claude's no-shell instruction and still forbids Web/network/MCP access and Git commits. Codex review/lead commands remain read-only with both cwd and `-C` rewritten to their scratch clone; OpenAI API reasoning remains tool-free, read-only, and same-HEAD. On Windows, Codex CLI invocations use the `windows.sandbox="elevated"` backend because the unelevated backend reproducibly suffers intermittent child-process initialization failures (`0xC0000142`); the authority boundary remains the disposable scratch clone plus Codex `workspace-write` for implementation/read-only for review and lead, with out-of-workspace writes denied. A checkpoint implemented by Codex is independently reviewed by a fresh GPT-5.6 Sol review invocation. If both Claude and Codex are usage-limited, the workstream pauses through the existing usage-limit blocker instead of retrying continuously. All agent output must pass the canonical Draft 2020-12 JSON Schema before any state change, coordination write, or repository publication. Successful Codex review/lead and OpenAI reasoning output is schema-validated before lease advancement. Every production Claude result, including blocked or failed outcomes that carry coordination messages, must also echo the exact active role, task ID, and unpredictable invocation ID before any live side effect. For successful implementation outcomes only, the supervisor creates the invocation-provenance commit inside the scratch clone, revalidates the live HEAD/clean state, imports that immutable direct-child commit, and fast-forwards the live worktree while holding the per-role lease lock. Custom executors are test-only, require every configured runtime path to live beneath the system temporary root, receive only scratch clones, must leave their configured repository unchanged, and never publish or advance leased repositories. Custom executors are trusted test fixtures only, require explicit test opt-in and temporary runtime paths, and are not a security boundary for hostile callback code. Repository-state seals and child-process cleanup provide defense-in-depth against accidental ambient mutation, including hidden refs, scratch-origin pushes, modify-then-restore writes, exception paths, and Windows reparse traversal; untrusted or adversarial callbacks must never be supplied through this hook. Coordination mailbox publication is a recoverable transaction: a durable manifest precedes visibility, destinations are confined beneath `mailbox/`, partial writes roll back, abandoned manifests recover under the publication lock, and supervisor startup performs recovery before normal work. Launcher image inspection plus watchdog container inspection/cleanup distinguish present, absent, and unknown Docker state; images are built only when definitively absent, and daemon or inspect ambiguity fails closed. Each production scratch has a random ownership nonce stored in an ownership manifest and activity marker; watchdog deletion verifies nonce, canonical path, role, invocation ID, pre-HEAD, Git top-level/HEAD, and rejects the root or any descendant reparse point, captures a stable Windows filesystem identity immediately before quarantine, atomically renames the verified scratch parent into a private quarantine path, requires the same filesystem identity after rename, revalidates provenance and reparse safety there, and deletes it with a non-reparse-following primitive; ambiguous rename/revalidation/deletion fails closed and retains quarantine.

## Human gates and banked resets

Physical device tests, credentials/login, account or billing changes, security-authority changes, and banked Work/Codex reset consumption are human gates. OpenAI API billing/budget/rate-limit state is separate from ChatGPT/Codex reset accounting and never consumes or recommends a banked reset.

The supervisor records three banked resets initially and reserves the final reset by default. A usage limit may create a `blocked_user` reset recommendation, but the tool never clicks ChatGPT Settings, purchases usage, or decrements the count before confirmation.

After James confirms the platform's remaining reset count with `confirm-resets`, only workers blocked specifically by `usage_limit` are released. `confirm-acceptance` releases only revision-bound acceptance blockers. `resume-human --blocker-kind github_auth|auth|human` releases only the explicitly matching blocker after the operator has completed that external action. Every release restores the saved phase; unrelated blockers remain stopped.

## CLI

Run from the AskRex-Assistant checkout that contains `scripts/dev_orchestrator`:

```powershell
py -3.11 -m scripts.dev_orchestrator.cli status --coordination-root C:\Users\james\rex-ai-test\askrex-coordination
py -3.11 -m scripts.dev_orchestrator.cli pause --coordination-root C:\Users\james\rex-ai-test\askrex-coordination
py -3.11 -m scripts.dev_orchestrator.cli defer-issue --coordination-root C:\Users\james\rex-ai-test\askrex-coordination --issue-id STORY-S35-SPEECH-ROUTER --clear-task-id S35-BACKEND-CANONICAL-CONTRACT-001 --clear-task-id S35-MOBILE
py -3.11 -m scripts.dev_orchestrator.cli resume-issue --coordination-root C:\Users\james\rex-ai-test\askrex-coordination --issue-id STORY-S35-SPEECH-ROUTER
py -3.11 -m scripts.dev_orchestrator.cli activate --coordination-root C:\Users\james\rex-ai-test\askrex-coordination
py -3.11 -m scripts.dev_orchestrator.cli confirm-openai-project-limit --coordination-root C:\Users\james\rex-ai-test\askrex-coordination --project-id DEDICATED_PROJECT_ID --monthly-usd 30.00
py -3.11 -m scripts.dev_orchestrator.cli enable-openai-worker --coordination-root C:\Users\james\rex-ai-test\askrex-coordination
py -3.11 -m scripts.dev_orchestrator.cli disable-openai-worker --coordination-root C:\Users\james\rex-ai-test\askrex-coordination
py -3.11 -m scripts.dev_orchestrator.cli request-handoff --coordination-root C:\Users\james\rex-ai-test\askrex-coordination --role backend --worker-session <browser-session-id>
py -3.11 -m scripts.dev_orchestrator.cli operator-ack-handoff --coordination-root C:\Users\james\rex-ai-test\askrex-coordination --role backend --nonce <nonce> --worker-session <browser-session-id>
py -3.11 -m scripts.dev_orchestrator.cli accept-handoff --coordination-root C:\Users\james\rex-ai-test\askrex-coordination --role backend --nonce <nonce>
py -3.11 -m scripts.dev_orchestrator.cli set-reset-policy --coordination-root C:\Users\james\rex-ai-test\askrex-coordination --reserve-last-reset false
py -3.11 -m scripts.dev_orchestrator.cli confirm-resets --coordination-root C:\Users\james\rex-ai-test\askrex-coordination --remaining 2
py -3.11 -m scripts.dev_orchestrator.cli confirm-acceptance --coordination-root C:\Users\james\rex-ai-test\askrex-coordination --role backend --kind documentation --evidence "docs verified for current leased HEAD"
py -3.11 -m scripts.dev_orchestrator.cli confirm-acceptance --coordination-root C:\Users\james\rex-ai-test\askrex-coordination --role backend --kind physical --evidence "<evidence>"
py -3.11 -m scripts.dev_orchestrator.cli resume-human --coordination-root C:\Users\james\rex-ai-test\askrex-coordination --role backend --blocker-kind github_auth
```

Initialize a new coordination runtime only with explicit roots:

```powershell
py -3.11 -m scripts.dev_orchestrator.cli init `
  --coordination-root C:\Users\james\rex-ai-test\askrex-coordination `
  --backend-root C:\Users\james\rex-ai-test\rex-ai-us126-final `
  --mobile-root C:\Users\james\rex-ai-test\askrex-mobile-sdk57 `
  --frozen-worktree C:\Users\james\rex-ai-test\rex-ai-pc-test
```

`init` is non-destructive and defaults to observe-only. `cycle` performs one supervision cycle. `run` holds the single-instance lock, maintains a background heartbeat, reloads config every cycle, and continues until stopped. Normal `activate` changes only observe-only/runtime state; it does **not** enable paid OpenAI API routing. Owner scheduling overrides use `defer-issue` / `resume-issue` only while the supervisor is paused. Deferral is scheduling metadata, not issue closure or verification: the canonical issue file remains unchanged and visible to coordination/testing. `--clear-task-id` releases only explicitly named current task IDs; Ralph never infers that relationship from prompt text or a loose story-number substring.

OpenAI API activation is a separate operator sequence: create/use a dedicated OpenAI project; configure an enforced **$30.00/month** project spend limit in OpenAI; ensure the household credential vault contains the OpenAI credential; record the project ID and cap confirmation with `confirm-openai-project-limit`; then run `enable-openai-worker`. The enable command accepts no credential value, requires a vault-backed credential, and stores only configuration state. Removing the vault credential later causes active API construction/send to fail closed. `disable-openai-worker` requires no credential and is safe at any time. Ralph also enforces a separate local **$30.00 per UTC month** reservation ledger before every paid call.

## Watchdog

`scripts/dev_orchestrator/windows_watchdog.ps1` checks only deterministic heartbeat/process state. A heartbeat is healthy only when it has the exact contract, a strictly typed positive integer PID, a timezone-bearing timestamp within bounded future skew, and the exact raw Windows process-creation FILETIME matching the currently live PID; malformed, future-dated, or PID-reused heartbeats are stale. It contains no Codex, Claude, or model invocation logic. Windows Task Scheduler may run it every 30 minutes. The watchdog never kills a live supervisor. For Claude markers it checks the uniquely named Docker container as well as the client PID; if the client died but the scratch-only container remains, it force-removes that orphan container before clearing the marker. If Docker state is unavailable or ambiguous it refuses restart. It starts a replacement only when heartbeat/process/container evidence proves no model child may still be running.

The supervisor itself performs frequent deterministic checks. A background heartbeat remains fresh while a long Claude/Codex subprocess is running, so the watchdog must not mistake legitimate model work for a dead supervisor.

## Rollout rule

Never let browser-based coding workers and CLI orchestrator workers write the same worktree concurrently. Initialize and test in observe-only mode. For each role use `request-handoff`, have the trusted local operator verify the active browser worker checkpointed/stopped, record `operator-ack-handoff` with the request nonce and browser-session correlation ID, then `accept-handoff` after the quiescence recheck. Activation remains forbidden until both exclusive leases validate. Any external writer invalidates the lease and blocks further mutation.

## Completion gate

Planner/adjudicator `done` claims are advisory only, and Astra may not mark an active task done before independent review. Each repository has an explicit completion manifest. Before a workstream becomes `done`, the deterministic gate requires no non-deferred owned open or `fixed-needs-retest` issues, a clean leased worktree, revision-bound documentation-truth acceptance, recorded physical acceptance, recorded cross-repo contract acknowledgment, a CI workflow, an OPEN pull request at the exact current HEAD and expected base branch, every mandatory named CI check present with conclusion SUCCESS, and every local release-quality command passing. An owner-deferred issue remains open canonical work; it is excluded only from the currently authorized campaign scheduling/completion calculation until explicitly resumed. A final independent **Codex GPT-5.6 Sol** review runs only after those deterministic gates pass and always bypasses the OpenAI API worker, even when paid routing is enabled and budget remains. Missing GitHub authentication is a `blocked_user` credential boundary, not a coding task. `SKIPPED` or `NEUTRAL` never satisfies a required check.
