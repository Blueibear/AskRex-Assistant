# SECURITY-REPO-20261111 GitHub Actions workflow review and final evidence - 2026-11-14

## Scope and evidence boundary

This record covers item 5 of `SECURITY-REPO-20261111` only: reviewing the
four tracked GitHub Actions workflows in `Blueibear/AskRex-Assistant` for
excessive permissions, untrusted input, secret exposure, and third-party
action pinning, and preparing minimal justified fixes. Repository only.
Account-wide tokens, MFA, sessions, and organization settings were not
inspected or changed. No merge, build, deployment, or physical-device
acceptance is claimed anywhere in this document.

This worker ran in a disposable cloud container on branch
`ralph/cloud/backend/backend-security-repo-workflows-final-ev-cdd1f3dd`,
starting from HEAD `aa1f43f73aecba79adaa31bd45ddb0f07dea5907` (the prior
`backend-security-repo-high-and-dispositions-20261111` completion commit,
already recorded in the reviewed-completed-task ledger). The workflow files
reviewed are:

- `.github/workflows/ci.yml`
- `.github/workflows/commitlint.yml`
- `.github/workflows/release-please.yml`
- `.github/workflows/windows-electron-artifact.yml`

No other `.github` tooling exists in this repository (no `dependabot.yml`
custom config, no other workflow directories).

## Method

1. Manual line-by-line review of all four files for `pull_request_target`,
   `workflow_run`, direct `${{ github.event.* }}` interpolation into `run:`
   blocks (script-injection risk), `${{ secrets.* }}` usage patterns, and
   `uses:` pinning style.
2. Independent automated review with `zizmor` 1.32.0 (installed from PyPI
   in this container; MIT-licensed, purpose-built GitHub Actions security
   auditor). Online audits (`ref-confusion`) could not run because this
   container's GitHub access is scoped to this repository only and
   unauthenticated `git-upload-pack` to `github.com` for other
   repositories returned `401`; `--no-online-audits` was used for the
   offline rule set, which does not need that access.
3. `git ls-remote --tags` against the two third-party action repositories
   (`wagoid/commitlint-github-action`, `googleapis/release-please-action`)
   to resolve the exact commit each moving major-version tag currently
   points to, for SHA pinning.
4. Repository regression test `tests/test_us057_ci_pipeline.py` and a
   direct `detect-secrets` scan of the edited files, to confirm the fix
   does not break existing CI-content assertions or introduce a
   high-entropy/keyword false positive.

## Findings

### 1. Excessive permissions

`ci.yml`, `commitlint.yml`, and `windows-electron-artifact.yml` already
declared minimal top-level `permissions:` (`contents: read`, plus
`pull-requests: read` for commitlint) with no job needing anything
broader. No change was needed in those three files.

`release-please.yml` declared `contents: write` and `pull-requests: write`
at the **workflow** level, which `zizmor`'s `excessive-permissions` audit
flagged at `High` confidence as overly broad: that permission block was
inherited by both jobs, including `windows-electron-artifact`, which only
invokes a reusable workflow and needs no write access at all. The called
workflow's own `permissions: contents: read` already capped the effective
token for that job (GitHub Actions takes the minimum of caller and callee
permissions for a `uses:`-called reusable workflow), so this was not an
active over-grant, but it was not *expressed* least-privilege and would
become one if that reusable call were ever changed to accept
`secrets: inherit` or lose its own restrictive block.

**Fix applied:** moved `permissions:` from workflow level to job level in
`release-please.yml`: the `windows-electron-artifact` job now explicitly
declares `contents: read`, and only the `release-please` job (which
actually creates release PRs/tags) keeps `contents: write` /
`pull-requests: write`. This is a pure scoping change with no functional
effect on either job's current behavior.

### 2. Untrusted input

No workflow uses `pull_request_target` or `workflow_run` (the two trigger
types that hand an elevated token or secrets to code built from untrusted
input). No workflow interpolates `${{ github.event.* }}` (PR title, issue
body, commit message, etc.) directly into a `run:` shell block — the
classic GitHub Actions script-injection vector. `windows-electron-artifact.yml`
runs on `pull_request` (the safe, token-restricted, no-fork-secrets
variant), scoped further by `paths:` filters, plus `push: tags: ["v*"]`,
`workflow_dispatch`, and `workflow_call`. No finding here; no change made.

### 3. Secret exposure

Only `windows-electron-artifact.yml` references secrets
(`WINDOWS_CSC_LINK`, `WINDOWS_CSC_KEY_PASSWORD`), and only through `env:`,
never inlined into a `${{ secrets.* }}` expression inside a `run:` string —
the pattern that risks the raw value landing in shell history or being
echoed by a misbehaving script. The step already degrades safely (an
unsigned artifact, truthfully reported) when the secret is absent, which
is also how it behaves for any fork-originated `pull_request` run, since
GitHub does not forward repository secrets (other than `GITHUB_TOKEN`) to
workflows triggered by a fork's `pull_request` event. No change made here.

`zizmor`'s `artipacked` rule (confidence: Low/Medium, 17 instances) flagged
every `actions/checkout` step across all four files for not setting
`persist-credentials: false`. By default `actions/checkout` writes the
job's `GITHUB_TOKEN` into the runner's local `.git/config` so later `git`
commands in the same job can use it; if nothing in the job needs that (no
`git push`, `git commit`, or token-authenticated `git` remote call), the
credential sitting in a file for the rest of the job is unnecessary
exposure — particularly for jobs in this repository that go on to run
third-party build tooling (`npm ci`, `pip install -e .[dev]`,
`electron-builder`) or upload artifacts (`coverage-report`,
`askrex-windows-installer`, `askrex-windows-smoke-diagnostics`), any of
which could in principle read or repackage that file.

A `grep` across all four workflow files for `git push`, `git commit`,
`git remote`, confirmed **no** job performs any of those operations, so no
job depends on the persisted credential.

**Fix applied:** added `persist-credentials: false` to every
`actions/checkout` step in all four workflow files (15 steps). This was
applied with `zizmor --fix=unsafe-only` (zizmor's own safe/unsafe fix
split treats this one as needing human confirmation that nothing depends
on the persisted token, which the `grep` above provided) and the full
resulting diff was reviewed by hand before being kept.

**Tooling defect found and corrected:** the same `--fix=unsafe-only` run
corrupted the unrelated reusable-workflow reference in
`release-please.yml` line 13, rewriting
`uses: ./.github/workflows/windows-electron-artifact.yml` to
`uses: $/.github/workflows/windows-electron-artifact.yml` (an invalid
reference that would have broken that job). This was caught by the
post-fix `git diff` review required by this task's validation step, not
by zizmor itself, and was corrected by hand. This is recorded as a
caution against trusting `zizmor --fix` output without a line-by-line diff
review; it is **not** evidence that this fix category is unsafe to apply
once reviewed, and the committed state has been re-validated (YAML parse,
`zizmor` re-run, see "Validation" below).

### 4. Third-party action pinning

Every step in `ci.yml` and `windows-electron-artifact.yml` uses an
`actions/*`-owned action (`checkout`, `setup-python`, `setup-node`,
`cache`, `upload-artifact`) pinned to a moving major-version tag (`@v4`,
`@v5`), not an immutable commit SHA. The prior review
(`docs/security/SECURITY-REPO-HIGH-REVIEW-2026-11-13.md`) already recorded
this as a documented supply-chain hardening follow-up requiring owner
approval, because converting the entire first-party action surface to SHA
pins is a blanket CI maintenance-policy change (it changes how every
future action version bump is reviewed and applied, repo-wide) rather than
a narrowly bounded fix, and `zizmor`'s own `unpinned-uses` rule flags it
under a "blanket policy" that assumes SHA pinning everywhere — that is a
policy choice, not a specific vulnerability in this repository's current
configuration. This review does not change that disposition: it remains
open, pending explicit owner sign-off, and is **not** closed by this task.

Two actions are genuinely third-party (not published by GitHub's
`actions` org), which is the specific risk this task's language calls
out — a maintainer-controlled tag that could be force-moved to point at
different code without the repository owner's action:

- `wagoid/commitlint-github-action@v6` (used only in `commitlint.yml`,
  which runs with `pull-requests: read` / `contents: read` and no
  secrets).
- `googleapis/release-please-action@v4` (used only in `release-please.yml`,
  which runs with `contents: write` / `pull-requests: write` on push to
  `master` — the higher-value target, since a compromised tag here would
  run with write authority against the default branch).

**Fix applied:** both were resolved via `git ls-remote --tags` to the
exact commit each tag currently points to and pinned to that commit SHA,
with the human-readable version kept as a trailing comment (the standard,
Dependabot-compatible pattern):

| Action | Was | Now |
| --- | --- | --- |
| `wagoid/commitlint-github-action` | `@v6` | `@b948419dd99f3fd78a6548d48f94e3df7f6bf3ed # v6.2.1` |
| `googleapis/release-please-action` | `@v4` | `@5c625bfb5d1ff62eadeeb3772007f7f66fdcf071 # v4.4.1` |

This is strictly a hardening change (it removes the ability for either
upstream maintainer to silently change what code runs under this
repository's existing permissions) and does not increase this
repository's or any agent's authority, so it does not trigger the
CLAUDE.md owner-approval gate for authority-increasing or
verification-weakening changes. It also does not touch the first-party
`actions/*` blanket-pinning policy question above, which remains
deliberately out of scope pending owner decision.

### Deferred finding: cache-poisoning risk on the Windows artifact workflow

`zizmor`'s `cache-poisoning` rule (confidence: Low) flagged
`windows-electron-artifact.yml`'s `actions/setup-node@v4` (`cache: npm`)
and `actions/setup-python@v5` (`cache: pip`) steps: the workflow both
accepts `pull_request` triggers and publishes runtime artifacts
(`askrex-windows-installer`), and dependency-manager caches populated
during a `pull_request` run can in principle be restored into a later,
more-trusted run (e.g. the `push: tags: ["v*"]` release path) if GitHub's
cache-key scoping ever allowed that cross-pollination. This is a real,
named class of GitHub Actions risk, but the concrete fix (disabling the
built-in package-manager cache on this job, or restricting cache scope)
is a build-time/performance trade-off on an already 60-minute-budgeted
Windows runner job, not a one-line hardening change, and changing it
without being able to execute the real `windows-latest` workflow from this
sandbox would be validating a CI reliability change on faith. Per the
same owner-approval boundary as the blanket SHA-pinning item above, this
is recorded as an open finding for an explicit owner/CI-maintainer
decision, not applied here.

## Validation performed

- `python3 -c "import yaml; ..."` — all four edited workflow files parse
  as valid YAML after every change (checked after each edit round).
- `zizmor --no-online-audits .github/workflows/` before and after:
  `excessive-permissions` findings 2 → 0; `artipacked` findings 17 → 0;
  third-party `unpinned-uses` findings for the two pinned actions
  resolved (first-party `actions/*` `unpinned-uses` findings, 42,
  intentionally remain — see above); `cache-poisoning` findings unchanged
  at 2 (intentionally deferred — see above).
- `pytest -q tests/test_us057_ci_pipeline.py` — 9 passed. This is the only
  repository test that asserts on workflow file content; none of its
  substring assertions (`pull_request`, `master`, `lint`, `ruff`, `black`,
  `typecheck`/`mypy`, `pytest`, `--cov`) were touched by this change.
- `python -m detect_secrets scan` against the four edited files directly —
  zero results (the added commit-SHA strings and comments do not trip the
  hardcoded-secret heuristics).
- Ran the exact CI secret-scan command
  (`detect_secrets scan --exclude-files ... --baseline .secrets.baseline`)
  to confirm exit 0 (no new secrets beyond baseline). It rewrote
  `.secrets.baseline` with Windows-vs-Linux path-separator normalization
  and a new `generated_at` timestamp — unrelated to any file this task
  touched (no baseline entry references any of the four workflow files,
  before or after). That unrelated drift was reverted with
  `git checkout -- .secrets.baseline` to keep this change scoped to the
  workflow review; it is not evidence of anything this task introduced.
- No GitHub Actions run was triggered from this container. This record
  does not claim CI passed on the pushed commit — only that the files are
  syntactically valid and pass the one existing content-assertion test.
  The supervisor/CI must observe an actual run of all four workflows
  (especially `windows-electron-artifact.yml`, which needs a
  `windows-latest` runner this sandbox does not have) on the exact pushed
  revision before treating this as proven in CI.

## Dependabot inventory recheck (current revision)

The authenticated Dependabot Alerts API
(`GET /repos/Blueibear/AskRex-Assistant/dependabot/alerts`) returned
`403 Resource not accessible by integration` for this session's GitHub
App installation token. Per the task's own instruction, this is recorded
as **unknown, not zero** — this worker could not refresh the open-alert
count and does not claim any count, closed or open, from this container.
The most recent authenticated count on record is the owner-operator's
mailbox report of 321 open default-branch alerts at
`2026-11-14T05:01:26Z` (22 critical, 152 high, 125 medium, 22 low) against
`master`, which is a different ref than this branch and does not reflect
this branch's unmerged fixes.

As supplementary, session-accessible evidence, the repository currently
has 21 open Dependabot dependency-bump PRs against `master`
(`gh api repos/Blueibear/AskRex-Assistant/issues`, state=open, filtered to
`build(deps`/`chore(deps` titles): #451, #450, #449, #448, #447, #446,
#445, #444, #441, #440, #439, #438, #437, #432, #431, #430, #423, #420,
#327, #288, #233. Cross-checking this branch's current `Pipfile` /
`pyproject.toml` / `requirements-electron-runtime.txt` against those PR
targets:

| PR | Package | PR target | This branch's current pin |
| --- | --- | --- | --- |
| #450 | certifi | 2026.10.18 | `2026.10.23` (already ahead) |
| #449 | Pillow | 13.0.0 | `13.0.0` (already matches) |
| #441 | urllib3 | 2.8.0 | `2.8.0` (already matches) |
| #440 | PyJWT | 2.15.0 | `>=2.15.0` floor / `2.15.0` runtime pin (already matches) |
| #437 | cryptography | 50.0.0 | `50.0.0` (already matches) |
| #423 | transformers | 5.10.1 | `5.22.2` (already ahead) |
| #448 | setuptools | 84.1.0 | `78.1.1` (**not yet addressed — remaining finding, outside this task's bounded GitHub Actions scope**) |

The remaining open PRs (#451 soupsieve, #447 Vite in `rex/ui`, #446
postcss-selector-parser/Tailwind, #445 source-map-js, #444 Werkzeug, #439
Electron, #438 Undici, #432/#431 Vitest, #430 react-router-dom, #420 pip,
#327 Torch, #288 msgpack, #233 pytest) were not individually re-diffed
against this branch in this session; the prior
`docs/security/DEPENDABOT-NONCRITICAL-DISPOSITIONS-2026-11-12.md` and
`docs/security/SECURITY-REPO-HIGH-REVIEW-2026-11-13.md` records remain the
dated source for that package-by-package reconciliation and are not
superseded by this document. None of these open PRs were merged, closed,
or otherwise modified by this task — modifying Dependabot PRs is dependency
remediation, not the GitHub Actions workflow review this task is scoped
to, and the setuptools gap in particular is called out above as a genuine
remaining item rather than silently left out.

## Prior campaign patch audit on this exact revision

This branch's HEAD before this task's commits is
`aa1f43f73aecba79adaa31bd45ddb0f07dea5907`, which the reviewed-completed-task
ledger records as the `backend-security-repo-high-and-dispositions-20261111`
completion commit. Spot-checking the package floors that task's own
dated records (`SECURITY-REPO-HIGH-REVIEW-2026-11-13.md`) claim were
fixed, directly against this exact revision's source files (not from the
dated doc's prose), confirms: `transformers==5.22.2` (Pipfile), `Pillow==13.0.0`
(Pipfile and `requirements-electron-runtime.txt`), `cryptography==50.0.0`
(Pipfile), `urllib3==2.8.0` (Pipfile), `certifi==2026.10.23` (Pipfile),
`PyJWT>=2.15.0` (pyproject.toml) / `PyJWT==2.15.0`
(`requirements-electron-runtime.txt`) are all present as declared in the
tracked source on this exact revision. This is a source-declaration
check only; it does not re-run the exact-candidate resolver, install,
packaging, or auth/HTTPS/TTS gates those prior tasks already recorded —
those remain that task's own evidence, not re-produced here, and this
document does not claim they were re-verified in this session.

## Packaged-runtime, hardware, and credential limits of this session

- This container has no `windows-latest` runner, no physical Windows
  machine, no Electron GUI display, and no microphone/audio hardware. The
  Windows Electron Artifact workflow (`windows-electron-artifact.yml`)
  could not be executed end-to-end here; only its YAML was validated for
  syntax and reviewed for security properties.
- `WINDOWS_CSC_LINK` / `WINDOWS_CSC_KEY_PASSWORD` (Authenticode signing
  secrets) are GitHub Actions repository secrets this container has no
  access to and did not need for this task (no signing was exercised).
- The GitHub App installation backing this session's `gh`/MCP GitHub
  access is scoped to `Blueibear/AskRex-Assistant` only and lacks
  Dependabot-alerts read scope (confirmed by the `403` above); this
  worker could not independently confirm or refresh default-branch alert
  counts, close any alert, merge any PR, or trigger any workflow run.
- No physical-device or Windows acceptance claim is made anywhere in this
  document.

## Status: patch-prepared vs merged vs built vs deployed

- **Patch-prepared:** yes, on this commit, on branch
  `ralph/cloud/backend/backend-security-repo-workflows-final-ev-cdd1f3dd`.
  The four workflow edits above are complete and locally validated as
  described.
- **Merged:** no. This branch has not been merged to `master`.
- **Built:** no CI run of these exact edited workflow files has been
  observed from this session. The next push to this branch, or a PR
  opened from it, will be the first time `ci.yml` (which runs on
  `push: branches: [master, claude/**]` — note this branch name does not
  match either `master` or `claude/**`, so a direct push here does not by
  itself trigger `ci.yml`; a PR against `master` would) and
  `commitlint.yml` actually execute against this change. The supervisor
  must observe and record that run's result separately; this document
  does not claim it.
- **Deployed:** no. Nothing here touches a deployed/packaged artifact.

## Remaining findings and required next action

1. First-party `actions/*` actions remain pinned to moving major-version
   tags across `ci.yml` and `windows-electron-artifact.yml`. Owner
   decision required before adopting a blanket SHA-pinning maintenance
   policy (carried forward unchanged from the 2026-11-13 review).
2. `cache-poisoning` (Low confidence, `zizmor`) on
   `windows-electron-artifact.yml`'s npm/pip caches given its
   `pull_request` + artifact-publishing combination. Owner/CI-maintainer
   decision required on whether to disable or scope that caching; not
   fixed here pending that call and pending real Windows-runner evidence
   this sandbox cannot produce.
3. `setuptools` is pinned to `78.1.1` in `Pipfile` while Dependabot PR
   #448 proposes `84.1.0`. This is a dependency-floor item, not a GitHub
   Actions workflow item, and is explicitly out of this task's bounded
   scope; it is recorded here so it is not lost, for whichever task
   resumes the dependency-remediation line of `SECURITY-REPO-20261111`.
4. The authenticated Dependabot alert count for this exact revision
   remains unknown to this worker (API returned 403). The supervisor, or
   a session with broader GitHub access, must refresh it before any
   alert-closure claim.

## Independent review requested

This task requests independent review of the four-file diff described
above before this branch is treated as ready to merge. Per CLAUDE.md and
this task's own instructions, this worker does not merge branches, close
GitHub issues or PRs, or claim deployment, and has not located a
dedicated GitHub Issue for `SECURITY-REPO-20261111` in this repository
(a targeted `gh api search/issues` query for that string returned zero
results) — the tracking artifact referenced by this task's coordination
context appears to be the external Windows coordination directory's
`issues/SECURITY-REPO-20261111.md` file, which this cloud container
cannot read or write. Closing that tracking record, if and when its full
acceptance evidence (including the items in "Remaining findings" above)
is complete, remains the supervisor/owner's action, not this worker's.
