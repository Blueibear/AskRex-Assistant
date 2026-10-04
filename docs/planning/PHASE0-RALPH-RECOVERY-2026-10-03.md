# Phase 0: Ralph orchestration reliability and recovery

Status: implementation on `fix/ralph-phase0-validation-recovery`; independent review, exact-head CI, and live unattended proof remain separate gates.
Owner: Ralph development infrastructure, not the backend/mobile product coding roles.
Authority: `CLAUDE.md`, `docs/claude/DEVELOPMENT_ORCHESTRATOR.md`, and the shared `askrex-coordination/PROTOCOL.md`. The product roadmap remains `PRD-production-readiness.md`.

## Diagnosed failure

The supervisor recorded a valid implementation result and advanced the leased backend HEAD. A subsequent trusted, deterministic pre-commit validation ran in the leased worktree, failed on Detect Secrets, and reformatted `scripts/rexbench.py` by adding a blank line. The next cycle's `validate_handoff` correctly rejected the resulting dirty leased checkout. Repeated manual patch/reverse/restart cycles masked this infrastructure defect without fixing it. This is **not** evidence that the Detect Secrets findings are false; the security gate must remain enabled.

## Recovery architecture

- All configured deterministic validation gates now execute without a shell in a fresh isolated, exact-HEAD Git clone outside the authoritative coordination directory and outside the leased product worktree.
- Clone the upstream remote refs from the leased repository so commands such as `git diff origin/master...HEAD` inspect the same revisions. Verify the live checkout starts clean and is unchanged after the gate sequence.
- When configured gates invoke npm/npx, install dependencies inside the disposable checkout using its checked-in `package-lock.json` (`npm ci --prefer-offline --no-audit --no-fund`) before running the unmodified gates. The live worktree's mutable `node_modules` is never reused. A missing lockfile or failed installation is an infrastructure blocker, not a passed test; any provisioning edits are preserved using the same recovery rules.
- A nonzero gate result returns the real exit code, command, and bounded output as implementation feedback. An apparently successful gate that edits the checkout is treated as **failed**, not green.
- A gate-generated tracked-file diff is saved as a SHA-256-addressed binary Git patch under the local `.askrex-validation-evidence` directory (sibling of coordination). The patch must pass reverse-apply verification before the disposable clone is removed. Duplicate identical edits reuse the existing digest-named patch. The isolated checkout remains retained from the start of preservation until a patch is durably written; disk or permissions errors never delete the only recoverable copy.
- If validation creates untracked files, changes its own Git HEAD, or cannot safely record the diff, retain the entire sandbox for operator inspection; do not silently delete uncertain work. Never copy raw patches, credentials, or private data into the shared coordination directory.
- On a timeout, save any validation edits and return a failed report. Never issue a passed revision-bound receipt for an incomplete or dirty validation.
- The long-running supervisor catches only recognized preflight `HandoffRequired` errors, records the affected non-human role as `blocked_system` with its original phase preserved, continues its heartbeat and bounded polling, and retries only after the complete lease check succeeds. It does not clear unrelated human/budget/acceptance blockers or silently adopt changed HEAD.
- Existing durable implementation receipts, nonce-bound leases, single-writer locks, protected branches, and independent review are unchanged.

## Phase 0 acceptance gate

1. Tests reproduce an exit-7 formatter change, an exit-0 validator that dirties its checkout, a timed-out formatter, and untracked artifacts. All preserve recoverable evidence, refuse a green receipt, and leave the live checkout untouched.
2. The isolated clone has the correct upstream Git reference and the normal revision-bound validation receipt still supports existing reviewer evidence.
3. Supervisor tests prove a stopped/invalid lease preserves its task, iteration, invocation ID, and heartbeat without invoking another coding agent. A mobile budget or other human blocker remains unchanged.
4. Run focused regression tests, complete orchestrator tests, Ruff, Black/format, and relevant mypy/security checks. Confirm the exact source revision and `git diff --check` are clean.
5. Commit the isolated Phase 0 branch, obtain an independent review, and verify required CI for its exact head before adopting it as the live supervisor's installed checkout.
6. Before restarting: confirm no supervisor or child agent is alive; retain an exact copy/hash of the iteration-57 `scripts/rexbench.py` patch, verify it against the live dirty worktree, and only then remove that precise worktree edit to restore the previously attested HEAD. Do not discard or move any other changed file, delete a lease lock, alter phase/iteration, or touch the frozen Windows worktree.
7. Start exactly one supervisor from the tested orchestrator revision. Verify a fresh process identity and advancing heartbeat across several cycles, correct recovery of the iteration-57 backend task, truthful failed Detect Secrets validation with **no** dirty leased worktree, and continuing iteration or an explicit recoverable blocker. Verify budget-blocked mobile state remains blocked.
8. Exercise a failure-injection restart against a throwaway test runtime, never the production handoff, to prove no duplicate worker invocation or lost durable result.

## Deployment and observability

Launch from the tested orchestrator checkout by setting the PowerShell working directory explicitly and running `py -3.11 -m scripts.dev_orchestrator.cli run --coordination-root <root>`. Run the source revision that includes the validation isolation and resilient supervisor loop; a source branch alone is not a deployed fix. Capture stdout/stderr outside the tracked repository. Use the existing Windows watchdog, but do not add a competing supervisor task. Monitor heartbeat process creation FILETIME, active-agent markers, backend state/iteration/receipt, and Git cleanliness. A source security failure is a development-task failure, not an infrastructure crash.

## Explicit exclusions

No direct backend product-source editing while its lease belongs to Ralph; no CI/security/approval bypass, automatic reset consumption, credentials, unapproved paid API spending, physical acceptance, or changes to `rex-ai-pc-test`. Human approval still governs expanded agent authority, publication, spending, and genuine security-risk acceptance.
