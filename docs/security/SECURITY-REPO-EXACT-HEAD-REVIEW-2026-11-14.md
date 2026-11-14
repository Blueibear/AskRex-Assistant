# SECURITY-REPO-20261111 exact-head independent review - 2026-11-14

## Scope and evidence boundary

This is the independent-reviewer record for the task "backend-security-
exact-head-review-aa1f43f7": review the exact commit
`aa1f43f73aecba79adaa31bd45ddb0f07dea5907` (`ralph-cloud-complete:
backend-security-repo-high-and-dispositions-20261111`) on
`ralph/cloud/backend/backend-security-repo-high-and-dispositi-5c116c51`,
and record pass/fail with evidence. It does not repeat or redo the
GitHub Actions workflow-hardening review already recorded in
`SECURITY-REPO-ACTIONS-GATES-REVIEW-2026-11-14.md`, and it does not
re-open or re-litigate the dependency disposition tables in
`DEPENDABOT-INVENTORY-2026-11-12.md` / `DEPENDABOT-NONCRITICAL-
DISPOSITIONS-2026-11-12.md`.

This task's own prompt supplied a large "authoritative bounded
coordination context" (PROTOCOL.md, AGENT_BACKEND.md, mailbox messages,
an issue file, a validation JSON excerpt). None of that directory exists
in this git repository or is reachable from this cloud session (confirmed
below). Per CLAUDE.md's instruction to follow the task over text embedded
in evidence, that pasted material is treated throughout this review as
unverified claims to check against this repository's own history and
GitHub's live API, not as fact.

### Coordination check could not be executed from this session

`scripts\check-coordination.ps1 -Role backend` and the
`C:\Users\james\rex-ai-test\askrex-coordination` directory it reads
(`issues\`, `mailbox\`, `validation\`) live only on the repository
owner's own Windows machine. `list_computer_folders` was called from this
session and returned no attached or available machine at all
(`{"folders":[],"cannot_be_used":[]}`), so there was nothing to request
and the script could not be run. This is recorded as a genuine capability
limitation, not a passed check. Consequently this review could not update
`issues\SECURITY-REPO-20261111.md` to `fixed-needs-retest` or drop a
`mailbox\testing\` notification; this document plus the pushed commit are
the retest/review evidence for whoever next has coordination-directory
access to relay.

As a substitute, git-level check (not a replacement for the real
coordination script): before doing any work, this session fetched both
`ralph/cloud/backend/backend-security-exact-head-review-aa1f4-00d76422`
(this task's own branch) and
`ralph/cloud/backend/backend-security-repo-high-and-dispositi-5c116c51`
(the review target) and confirmed local == remote for each
(`f0cdccd0ba643271fe03b1e2f20d6bc18c8f15b9` and
`aa1f43f73aecba79adaa31bd45ddb0f07dea5907` respectively). Both were
re-fetched again immediately before writing this document and remained
unchanged. That rules out a concurrent writer on either exact branch
during this session; it does not confirm or refute the broader
supervisor/live-worktree state asserted in the pasted coordination text.

## Commit identity

- `aa1f43f73aecba79adaa31bd45ddb0f07dea5907` exists in this repository and
  is the current head of
  `origin/ralph/cloud/backend/backend-security-repo-high-and-dispositi-5c116c51`
  (confirmed by `git rev-parse` against both the local object and the
  fetched remote ref).
- The commit itself is an **empty** completion-marker commit (no file
  changes), sitting directly on top of `3a9afecadfbafb024dd35a34d1e6a99c770ef8f3`
  ("test: align installed artifact smoke with current identity and
  background startup"). Its tree is therefore byte-identical to that
  parent's tree.
- This task's own branch (`...-exact-head-review-aa1f4-00d76422`) carries
  `aa1f43f73a` as an ancestor seven commits back from its current head
  `f0cdccd0`; the file this review inspects
  (`docs/security/SECURITY-REPO-HIGH-REVIEW-2026-11-13.md`) is byte-identical
  between `aa1f43f73a` and `f0cdccd0` (`git diff aa1f43f73a..HEAD -- <file>`
  is empty), so reading it at the current worktree HEAD is equivalent to
  reading it at the exact reviewed commit.

## Diff reviewed

`git diff --stat b4004d55..aa1f43f73a` (the regenerate-lock commit through
the reviewed completion marker) touches 11 files, 468 insertions / 152
deletions: `AGENTS.md`, `docs/DEPENDENCIES.md`,
`docs/security/DEPENDABOT-INVENTORY-2026-11-12.md`,
`docs/security/DEPENDABOT-NONCRITICAL-DISPOSITIONS-2026-11-12.md`,
`docs/security/SECURITY-REPO-HIGH-REVIEW-2026-11-13.md`,
`gui/package-lock.json`, `gui/package.json`,
`scripts/test_installed_electron_artifact.ps1`,
`scripts/verify_electron_package_contents.py`,
`tests/test_dependency_policy.py`, `tests/test_us327_smoke.py`.

Reviewed the non-prose diffs directly (not just the narrative describing
them):

- `gui/package.json` adds `"minimatch": "^11.0.1"` to the existing
  `overrides` block, matching the documented `GHSA-g3hv-75wg-m249` fix.
- `scripts/test_installed_electron_artifact.ps1` replaces a hardcoded
  `artifact-ci-user` identity assertion with the ID of the user the smoke
  script actually created, and adds
  `ASKREX_ARTIFACT_SMOKE_FORCE_BACKGROUND` — matches the documented smoke
  fix, and is a test-harness correction, not a production-code change.
- `scripts/verify_electron_package_contents.py` raises the managed-runtime
  import probe timeout from 60s to 180s and distinguishes a timeout from a
  launch error in the returned message — matches the documented fix, is
  purely diagnostic/timeout tuning, not a verification-boundary weakening
  (a failure still fails; the message is just more specific).
- `tests/test_dependency_policy.py` adds a new test binding
  `docs/DEPENDENCIES.md` prose to the actual `Pipfile.lock` Transformers
  selection, and adds a `filelock >= 4.2.1` assertion on the `default`
  lock section (previously only asserted on `develop`) — both are
  policy-tightening additions, not removals or weakenings.
- `tests/test_us327_smoke.py` adds a mock for
  `check_credential_vault_backend` in the doctor-smoke test helper. This
  is a test-only mock for a check that, by the documented S4 fail-closed
  vault design, genuinely cannot pass on Linux/CI; it does not touch the
  real `check_credential_vault_backend` implementation or weaken its
  fail-closed behavior.
- `AGENTS.md` **removes** a "Supervised Ralph development loop" paragraph
  that had granted broad continue-without-pause autonomy. This narrows,
  not widens, standing agent authority, so it does not trigger CLAUDE.md's
  owner-approval requirement for authority-increasing changes.

No file in this diff touches a security/verification boundary, an
approval gate, a permission, or an audit-ignore rule. Nothing here
contradicts the diff's own narrative in `SECURITY-REPO-HIGH-REVIEW-
2026-11-13.md`.

## Dated inventory / disposition claims

`SECURITY-REPO-HIGH-REVIEW-2026-11-13.md` (as it stands at the reviewed
commit) is unusually careful about the truthful-vocabulary separation
CLAUDE.md requires (prepared / merged / built / deployed / alert status
are each called out explicitly, repeatedly, and never conflated). Spot
checks against this repository's own state, independent of trusting the
document's prose:

- **Pipfile.lock regeneration claim**: the document says the resolver run
  selected `transformers==5.22.2` (default), `urllib3==2.8.0` (default and
  develop), `msgpack==1.2.4` (develop), `filelock==4.2.10` (default and
  develop). Read `Pipfile.lock` directly out of the commit object
  (`git show aa1f43f73a:Pipfile.lock`) and parsed it: **all four match
  exactly**, in both sections where claimed.
- **`tests/test_dependency_policy.py`**: ran it independently in a fresh
  Python 3.11.18 venv (no project install needed; the file only parses
  lock/requirements/pyproject text) at this exact worktree state —
  **10/10 passed**, matching the document's claim.
- **`python scripts/security_audit.py --release-gate`**: ran it
  independently — **exit 0**, "0 actionable findings", "CLEAN - No
  exposed secrets found", matching the document's claim.
- **Disposition-correction claim** (Transformers rows in
  `DEPENDABOT-NONCRITICAL-DISPOSITIONS-2026-11-12.md` corrected from
  "Unresolved" to reflect that `5.22.2` already excludes every cited
  vulnerable range): read the corrected file at this commit; the claimed
  correction is present and the cited ranges (`< 5.3.0`, `< 5.5.0`,
  `>= 4.49.0, <= 5.8.1`, `< 5.10.0`) do each exclude `5.22.2`, so the
  correction is accurate, not a suppression.

## Windows Electron Artifact run and receipt hashes

The task asked to "validate the supplied Windows Electron Artifact run
and receipt hashes against repository evidence." Two distinct claims were
in play and were checked separately against GitHub's live API (not
against each other, and not assumed consistent by default):

1. **In-repo document's own claim** (written in-session, dated 2026-11-14,
   "Windows operator follow-up"): run
   [43559782518](https://github.com/Blueibear/AskRex-Assistant/actions/runs/43559782518)
   on commit `3a9afecadfbafb024dd35a34d1e6a99c770ef8f3` (the content-identical
   parent of the reviewed commit). **Verified real via
   `mcp__github__actions_get get_workflow_run`**: `conclusion: success`,
   `head_branch: ralph/cloud/backend/backend-security-repo-high-and-dispositi-5c116c51`,
   `head_sha: 3a9afecadfbafb024dd35a34d1e6a99c770ef8f3` — matches exactly.
   Its local receipt path, `evidence/windows-electron-1893009-20261113/RECEIPT.md`,
   **does not exist anywhere in this repository's git history on any
   branch** (`git log --all` for that path returns nothing). It is
   Windows-coordination-directory-local evidence that this session cannot
   open or independently check.

2. **Coordination-message claim** (the "owner-operator" mailbox text
   pasted into this task): run
   [43561775201](https://github.com/Blueibear/AskRex-Assistant/actions/runs/43561775201),
   first-run diagnostic SHA-256 `098DC0D4...175B693D`, installed-artifact
   diagnostic SHA-256 `84068652...E05B58F0`, job-log SHA-256
   `7919E556...EA92769C`. **Verified real via the same tool**:
   `conclusion: success`, same branch, `head_sha:
   aa1f43f73aecba79adaa31bd45ddb0f07dea5907` — the exact commit under
   review, dispatched 24 minutes after run (1) as a separate, later
   confirmation against the exact head rather than its content-identical
   parent. This is a second genuine run, not a contradiction of (1).
   - Downloaded the run's `askrex-windows-smoke-diagnostics` artifact
     directly from GitHub (`download_workflow_run_artifact`, then
     `curl`+`unzip`+`sha256sum` in this session's own scratch directory)
     and independently recomputed both file hashes:
     `askrex-first-run-smoke.json` → `098dc0d4...175b693d` and
     `askrex-artifact-smoke.json` → `84068652...e05b58f0`. **Both match
     the claimed hashes exactly** (case-insensitive hex match). Both
     files' content is `{"status": "success", ...}`, content-free per
     CLAUDE.md's diagnostic-logging rule.
   - The claimed "complete job-log SHA-256"
     (`7919E556ADE48E2D2791D17C549672C2F1E30E1E1DC75ABD082C4E65EA92769C`)
     **could not be reproduced**. Tried three independent retrievals of
     that run's single job's log and hashed each: (a) the raw log body
     via `gh api .../actions/jobs/<id>/logs` → `04fe2798...6dc9f8d`; (b)
     the per-step `.txt` file inside the run's downloaded `logs.zip`
     bundle → same `04fe2798...6dc9f8d`; (c) `gh run view --log` CLI
     output → `8c434c31...189acd814`. None matches the claimed hash. This
     is recorded as an **unreproduced claim**, not a proven fabrication —
     it may reflect a local log-capture format or combination (e.g. a
     locally concatenated multi-run log) this session has no way to
     replicate — but it should not be treated as independently verified.

## Dependabot alert status

Re-checked directly from this session: `gh api
repos/Blueibear/AskRex-Assistant/dependabot/alerts` returns `403 Resource
not accessible by integration`, the same limitation the reviewed document
already discloses. The coordination message's "321 open alert instances
(22 critical, 152 high, 125 medium, 22 low) at 2026-11-14T05:01:26Z" is
**consistent with, not contradicted by**, the figure already recorded in
`SECURITY-REPO-HIGH-REVIEW-2026-11-13.md`'s own "Windows operator
follow-up" section, but remains an unverifiable third-party claim from
this session's vantage point either way. Nothing in the reviewed material
claims those alerts are closed, and this review does not claim that
either.

**Addendum**: pushing this review's own commit produced GitHub's
standard post-push advisory banner, which independently states the exact
same default-branch figure from GitHub itself: "GitHub found 321
vulnerabilities on Blueibear/AskRex-Assistant's default branch (22
critical, 152 high, 125 moderate, 22 low)." This is first-party evidence
(unprompted, from `git push`'s own remote output, not from the `gh api`
call that returned 403) and corroborates the coordination message's count
as of this review. It still does not establish alert closure, merge, or
deployment, and alert counts are expected to keep drifting.

## "Exact-head supervisor gate manifest"

The task asked to "run the applicable exact-head supervisor gate manifest
available in the worktree." No such manifest file exists in this git
worktree: there is no `validation/` directory and no file matching
`*gate*manifest*` anywhere in the tracked tree (checked with `git
ls-tree -r --name-only HEAD`). The `validation/backend-*.json` content
pasted into this task's prompt is, like the rest of the coordination
material, external to this repository. In its place, this review ran the
concrete repository gates CLAUDE.md itself documents and that this
specific task's own evidence depends on
(`tests/test_dependency_policy.py`, `scripts/security_audit.py
--release-gate`), both independently, both passing as recorded above.

## Status separation

- **Patch prepared**: yes, at the reviewed commit, already pushed to
  `ralph/cloud/backend/backend-security-repo-high-and-dispositi-5c116c51`.
- **Merged**: no. Not claimed anywhere in the reviewed material, and this
  review does not claim it either.
- **Built**: yes for the Windows Electron artifact specifically — two
  separate, live-API-confirmed successful CI runs exist against this
  commit and its content-identical parent. Not a release build or signed
  artifact claim.
- **Deployed**: no. Not claimed.
- **Dependabot alert closure**: no. Not claimed, and not independently
  checkable from this session (403 on the alerts API, as above).
- **Physical-device acceptance**: no. Not claimed. This review performed
  no hardware interaction of any kind.

## Disposition

**Pass**, with one disclosed, bounded finding: the coordination message's
"complete job-log SHA-256" for run `43561775201` does not match any of
three independent reconstructions of that job's log obtainable from
GitHub's API in this session. This does not implicate the underlying
dependency/security work, which is independently verified above (lock
contents, policy test, release-gate audit, two of the three artifact
hashes, and the CI run's own existence/success/commit-binding all check
out against live or repository evidence). It is an evidence-provenance
gap in one third-party-supplied hash, not a defect in the reviewed diff.

Recommended next action for whoever next has access to
`C:\Users\james\rex-ai-test\askrex-coordination`: relay this document as
the review/retest evidence for `issues\SECURITY-REPO-20261111.md`, mark
it `fixed-needs-retest` there (this session cannot), and ask the
"owner-operator" to confirm what the job-log hash was actually computed
over, since it does not match GitHub's own served log content for that
job.
