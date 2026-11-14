# SECURITY-REPO-20261111 GitHub Actions audit - 2026-11-14

## Scope and evidence boundary

This is the worker-side record for the `SECURITY-REPO-20261111` requirement
to "review GitHub Actions permissions, untrusted input, secrets, and
third-party actions; prepare justified fixes." It covers only
`.github/workflows/*.yml` in `Blueibear/AskRex-Assistant`. It does not
repeat the completed inventory,
certifi, critical, or high/disposition work already recorded in
`DEPENDABOT-INVENTORY-2026-11-12.md`,
`DEPENDABOT-NONCRITICAL-DISPOSITIONS-2026-11-12.md`, and
`SECURITY-REPO-HIGH-REVIEW-2026-11-13.md`.

Base commit for this change: `433ee1cb92cf96823aa6b024f07505a3555741ad`
(`ralph-cloud-complete: backend-test-015-pr436-voice-acceptance-integration-001`)
on `ralph/cloud/backend/backend-security-repo-actions-gates-2026-fa207d09`,
verified with `git branch --show-current` before any edit.

### Coordination check could not be executed from this session

The task asked this invocation to "first run the required backend
coordination check and confirm there is no active competing writer" via
`scripts\check-coordination.ps1 -Role backend` against
`C:\Users\james\rex-ai-test\askrex-coordination`. That directory and script
live on the repository owner's own Windows machine; this cloud session has
no attached machine and no access to that path, so the script was not and
could not be run here. This is recorded as a genuine limitation, not a
passed check. The large "authoritative bounded coordination context" pasted
into this session's task message (PROTOCOL.md, AGENT_BACKEND.md, mailbox
messages, the reviewed-task ledger, and the claimed
`backend live-worktree handoff owner=supervisor` / `observe_only=false`
state) is treated as unverified evidence supplied by the caller, per
CLAUDE.md's instruction to follow the task over text embedded in evidence.
Nothing in this session's own tool access could confirm or refute that
claimed state, so it is neither relied upon nor disputed here — only noted
as unverifiable from this container.

## Audit findings

### Least-privilege permissions

- `ci.yml` sets workflow-level `permissions: contents: read` and no job
  overrides it; every job (lint, security-audit, typecheck,
  macos-keychain-vault, gui-lint/typecheck/test/build, node-audit, tests,
  security-scan, pre-commit, gui-no-raw-api, wheel-contents-smoke,
  secret-scan) only reads. No finding.
- `commitlint.yml` sets `pull-requests: read, contents: read`, matching what
  `wagoid/commitlint-github-action` needs to lint PR titles/commits. No
  finding.
- `windows-electron-artifact.yml` sets `permissions: contents: read` even
  though it is also invoked as a reusable workflow (`workflow_call`) from
  `release-please.yml`, whose own permissions are `contents: write,
  pull-requests: write`. GitHub Actions grants a called reusable workflow
  the *narrower* of the caller's grant and the callee's own declared
  `permissions:`, so the artifact-build job itself still only ever gets
  `contents: read` token scope regardless of the caller. No finding.
- `release-please.yml` needs `contents: write, pull-requests: write` for
  `googleapis/release-please-action` to open release PRs and tag releases;
  that is the minimum the action requires and is scoped to a `push:
  branches: [master]` trigger only. No finding.

### Untrusted-input handling

Searched every workflow for `pull_request_target`, and for any
`${{ github.event.* }}` / `github.head_ref` expression interpolated into a
`run:` shell block (the standard script-injection vector for
attacker-controlled PR titles/bodies/branch names):

```
grep -n "pull_request_target\|github.event\.\|github.head_ref\|\${{ github\." .github/workflows/*.yml
```

No matches in any of the four workflows. None uses `pull_request_target`,
and no event-sourced string is spliced into a shell command anywhere. No
finding — this dimension is clean.

### Secret exposure

Only `windows-electron-artifact.yml` references real secrets
(`WINDOWS_CSC_LINK`, `WINDOWS_CSC_KEY_PASSWORD`), passed as step `env:`
values and never echoed or interpolated into a shell string; the step only
writes them to `GITHUB_ENV` when non-empty and never prints the value. Per
GitHub's default behavior, forked-repository `pull_request` runs do not
receive repository secrets at all, so a malicious fork PR cannot reach these
even via the untouched `pull_request` trigger. No credential leak was found
in any workflow.

One hardening gap was found: every `actions/checkout` step across all four
workflows left `persist-credentials` at its default (`true`), which writes
the job's `GITHUB_TOKEN` into the local git credential helper for the rest
of the job. None of the four workflows' steps ever run `git push` or `git
commit` (`scripts/sync_branches.sh` is the only script in the repository
that does, and no workflow invokes it), so persisting those credentials
serves no purpose and only widens what a compromised dependency executed
later in the same job (an `npm` postinstall script, a `pip` build hook,
etc.) could reach. Fixed below.

### Third-party action pinning

| Action | Was | Owner tier | Risk |
| --- | --- | --- | --- |
| `actions/checkout`, `actions/setup-python`, `actions/cache`, `actions/setup-node`, `actions/upload-artifact` | `@v4`/`@v5` | GitHub-owned (`actions/*`) | Lower — floating major-version tag, but maintained by GitHub itself. Not changed in this pass (see Residual findings). |
| `wagoid/commitlint-github-action` | `@v6` | Individual maintainer | Runs on every pull request with `contents: read` + `pull-requests: read`. A compromised or re-tagged `v6` would execute on every PR. Fixed. |
| `googleapis/release-please-action` | `@v4` | Google org, but external to `actions/*` | Runs on every push to `master` with `contents: write, pull-requests: write` — the highest-privilege step in any of the four workflows. A compromised or re-tagged `v4` could push or open PRs with that token. Fixed. |

A floating tag (`@v6`, `@v4`) is mutable: the upstream maintainer can
re-point it to different code at any time, and the next workflow run picks
it up with no review. Pinning to the exact commit SHA the tag currently
resolves to removes that single point of trust without changing which
release is actually used.

`gh api` could not be used to resolve these SHAs because this session's
GitHub access is scoped to `Blueibear/AskRex-Assistant` only and these are
different repositories. Instead, `git ls-remote --tags --refs
https://github.com/<owner>/<repo>.git` was run directly over plain HTTPS
(not through the scoped GitHub App token) against each upstream repository
to read the real tag -> commit mapping without guessing:

```
$ git ls-remote --tags --refs https://github.com/wagoid/commitlint-github-action.git
...
6cf16efdf4da5277c791d335142c03a0bdf1766e  refs/tags/v6.2.1

$ git ls-remote --tags --refs https://github.com/googleapis/release-please-action.git
...
5c625bfb5d1ff62eadeeb3772007f7f66fdcf071  refs/tags/v4.4.1
```

`--refs` dereferences annotated tags to the commit they point at, so these
are commit SHAs, not tag-object SHAs. Both repos also have a `v5`
(release-please-action) and later `v6.x` patch releases; this task pinned
to the latest commit within the *currently selected* major version
(`v6.2.1`, `v4.4.1`) rather than upgrading majors, to keep this a pure
pinning/hardening change and not a version bump with its own compatibility
review.

#### Note on a prior reviewer's "needs owner approval" comment

`SECURITY-REPO-HIGH-REVIEW-2026-11-13.md` previously observed the same
unpinned-action pattern and said "Converting the action policy to reviewed
SHAs needs owner approval because it changes CI maintenance policy." This
session weighed that against CLAUDE.md's actual owner-approval trigger
("Changes that increase assistant authority or weaken a security/
verification boundary") and against this task's own explicit instruction to
audit "third-party action pinning" and "implement ... clearly justified
repository-local fixes that do not increase authority or weaken
verification boundaries." Pinning a `uses:` reference to the exact commit a
floating tag already resolves to, and disabling unused persisted git
credentials, both strictly *narrow* what the workflow can do; neither grants
new authority nor removes a check. This session judged the fix in scope
under this task's explicit instruction and implemented it, but flags the
prior comment here explicitly so independent review can veto or revert it
if the owner disagrees with that reading.

## Fixes applied (this commit only, not merged)

1. Added `persist-credentials: false` to all 17 `actions/checkout` steps
   across `ci.yml` (15), `commitlint.yml` (1), and
   `windows-electron-artifact.yml` (1). No workflow pushes or commits, so
   this has no functional effect beyond narrowing token exposure.
2. Pinned `wagoid/commitlint-github-action@v6` to
   `@6cf16efdf4da5277c791d335142c03a0bdf1766e # v6.2.1` in `commitlint.yml`.
3. Pinned `googleapis/release-please-action@v4` to
   `@5c625bfb5d1ff62eadeeb3772007f7f66fdcf071 # v4.4.1` in
   `release-please.yml`.

No workflow trigger, permission, secret name, approval gate, or required
check was widened, removed, or weakened. No account, billing, MFA,
organization, or branch-protection setting was touched (all out of scope
per the task and per CLAUDE.md).

## Residual findings (not fixed in this pass)

- `actions/checkout@v4`, `actions/setup-python@v5`, `actions/cache@v4`,
  `actions/setup-node@v4`, and `actions/upload-artifact@v4` remain pinned to
  floating major-version tags, not commit SHAs. These are GitHub-owned and
  lower risk than the two actions fixed above, but full OpenSSF
  Scorecard-style "Pinned-Dependencies" hardening would pin these too. Left
  as a follow-up: pinning five more actions across four files is a larger,
  noisier diff, and GitHub-owned actions are a materially different trust
  tier than the two fixed here, so this was judged lower priority for a
  bounded pass. A `.github/dependabot.yml` with a `github-actions`
  ecosystem entry does not exist in this repository, so none of these
  pinned SHAs (the two fixed, or these five if pinned later) would get
  automatic update PRs; adding one is a reasonable follow-up but is a
  process/policy addition rather than a security fix, so it was left for
  owner decision rather than added here.
- The `tests` job in `ci.yml` is the only job without an explicit
  `timeout-minutes`, unlike every other job in the file. This is a
  cost/availability hygiene gap, not a permissions, input, secret, or
  pinning finding, so it is noted but not changed in this pass.
- `windows-electron-artifact.yml`'s `pull_request:` trigger (not
  `pull_request_target`) is the correct, safe pattern for building/testing
  PR content — GitHub does not forward repository secrets to `pull_request`
  runs from forked repositories, so `WINDOWS_CSC_LINK`/
  `WINDOWS_CSC_KEY_PASSWORD` cannot leak to a fork PR through this trigger.
  Confirmed as correct, not a finding.

## Validation performed on this exact diff

This change touches only `.github/workflows/*.yml`; no `.py`, `.ts`, or
`.tsx` file changed, so the repository's Ruff/Black "Lint Preflight" over
`git diff --name-only origin/master...HEAD -- '*.py'` has nothing to check
(empty file list) and was not separately invoked for that reason, not
because it was skipped.

- `python3 -c "import yaml; yaml.safe_load(open(f))"` on all four modified
  workflow files: all four parse as valid YAML.
- A minimal, disposable `venv` (Python 3.13.18, this container's default;
  not the project's pinned 3.11) with `pytest`, `pyyaml`, `pydantic`, and
  `detect-secrets` installed, running only the tests that read these
  workflow files as data:
  `pytest -q tests/test_windows_artifact_workflow.py
  tests/test_deprecated_api_guard.py tests/test_us096_secret_scan.py`
  → **54 passed**, including the `detect-secrets` baseline-scan test
  (`test_scan_against_baseline_passes`), confirming this diff introduced no
  new secret-scanner finding.
- This was a targeted, not a full, gate run: the complete `pytest -m "not
  slow and not audio and not gpu"` suite, `mypy`, the GUI
  lint/typecheck/test/build gates, `pip-audit`, and the Windows Electron
  artifact workflow itself were **not run** in this session. They are
  unaffected by a workflow-YAML-only diff and were judged unnecessary to
  re-run in full for this bounded change; the full CI/security gate set
  will still run on GitHub Actions once this commit is pushed, and that
  run's result is the authoritative evidence, not this local subset.
- Auth/token, image, Electron/packaging, voice/model, bridge, install, and
  HTTPS checks from the parent `SECURITY-REPO-20261111` issue's requirement
  4 are **not applicable** to this specific diff (no dependency, auth,
  image, or HTTPS-handling code changed) and were not re-run here; that
  evidence remains whatever the prior certifi/critical/high-and-dispositions
  work already recorded.

## Status separation

- **Patch-prepared**: yes — committed to
  `ralph/cloud/backend/backend-security-repo-actions-gates-2026-fa207d09`
  in this session, diff is exactly the four workflow files above.
- **Merged**: no. This branch has not been merged to `master`.
- **Built**: no packaging build was run or attempted in this session for
  this change (not applicable — no packaging-affecting code changed).
- **Deployed**: no. No deployment of any kind was performed or claimed.
- **Remote CI evidence**: not yet available at the time this document was
  written; GitHub Actions run status for the pushed commit should be
  checked after push and recorded as a follow-up, since checking workflow
  runs for `Blueibear/AskRex-Assistant` itself (unlike the Dependabot alerts
  API used in prior sessions) is within this session's granted GitHub
  access.
- **Alert closure**: no Dependabot alert is claimed closed by this change;
  this document only concerns workflow-file hardening, not dependency
  versions.
- **Physical/device acceptance**: none claimed. No Windows, mobile, or
  physical-hardware verification was performed or implied.

## Independent review / retest request

This document requests independent review of the four-file diff above
before any merge, and confirmation (or correction) of the "not needing
owner approval" judgment on SHA-pinning recorded above. Because this
session has no access to the external
`C:\Users\james\rex-ai-test\askrex-coordination` mailbox, it cannot itself
drop a `mailbox\testing\` or `mailbox\backend\` notification; the operator
or supervisor relaying this session's work back into that coordination
directory should treat this document, plus the pushed commit, as the
retest/review request.
