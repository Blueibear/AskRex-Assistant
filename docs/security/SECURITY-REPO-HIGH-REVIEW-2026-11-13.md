# SECURITY-REPO-20261111 high-advisory implementation review - 2026-11-13

## Purpose and evidence boundary

This is the worker-side review record for the remaining high-severity
Dependabot work. It uses the repository-local
`DEPENDABOT-OPEN-ALERTS-2026-11-12.json` snapshot, fetched at
`2026-11-13T01:42:57.4825330Z`, whose recorded raw-export SHA-256 is
`7E1E6F4F752B331FADE33B49550B728C39C2F0C90CD844084B2CD4FC31EC2EFB`.
The snapshot has 147 high alert instances. This offline review cannot refresh
GitHub, close alerts, establish merge status, or establish deployment status.

The exhaustive per-GHSA owner/exposure/next-action register remains
`DEPENDABOT-NONCRITICAL-DISPOSITIONS-2026-11-12.md`. This review records the
high paths pending when the dated export was captured, plus the current local
selection after the complete Pipenv lock regeneration. A local fixed selection
is not suppression and is not evidence of an alert closure, merge, build, or
deployment.

## Current local lock reconciliation

| Group | Local selection | Affected advisory IDs | Exposure condition | Owner | Required next action |
| --- | --- | --- | --- | --- | --- |
| Transformers | Pipfile.lock `default`: `5.22.2`; source/profile declarations: `5.22.2` | `GHSA-29pf-2h5f-8g72`, `GHSA-x9r9-c232-4q39`, `GHSA-xrqw-3rrv-vx5w` | A model or Voice profile loads attacker-controlled model/tokenizer content. | backend | Supervisor: exact-candidate model, TTS, Voice, and packaged Voice-profile checks; independent review; alert recheck. |
| urllib3 | Pipfile.lock `default` and `develop`: `2.8.0`; Pipfile and packaged runtime: `2.8.0` | `GHSA-8988-9cw3-xx77`, `GHSA-vxq7-64xx-v4gw` | An installed runtime handles an affected HTTP response. | backend | Supervisor: exact-candidate install and normal HTTPS validation; independent review; alert recheck. |
| msgpack | Pipfile.lock `develop`: `1.2.4`; Pipfile development constraint: `>=1.2.3` | `GHSA-6v7p-g79w-8964`, `GHSA-j586-36cw-2gc2` | A development or CI environment deserializes attacker-controlled MessagePack input. | backend | Supervisor: exact-candidate CI/security gates; independent review; alert recheck. |
| filelock | Pipfile.lock `default` and `develop`: `4.2.10`; Pipfile development constraint: `>=4.2.1` | `GHSA-v4x8-3wr5-3wq8` | A development or CI Pipenv environment reaches file locking on Windows. | backend | Supervisor: exact-candidate CI/security gates; independent review; alert recheck. |

The Python lock is a generated security input. No lock record or artifact hash
was hand-written from package metadata, and no audit override or suppression
was added.

## Lock-generation and policy evidence

The former source-only Transformers regression was insufficient: it allowed a
stale generated lock to pass while every source declaration selected `5.22.2`.
`tests/test_dependency_policy.py` now inspects the resolver-generated lock and
requires the fixed `urllib3`, `msgpack`, and `filelock` selections as well as
the source declarations. This makes a future source-only floor increase fail
when either Pipenv dependency graph remains vulnerable.

The trusted operator completed a Python 3.11 Pipenv 2026.11.0 resolver run
from the current Pipfile. `pipenv verify` passed, and the generated lock
selects the four versions shown above. No lock hash was hand-written and no
audit override or suppression was added. This is local patch evidence only;
exact-candidate validation and independent review remain required.

## Other high-path reconciliation

The local candidate already selects the reported fixed versions for these
high groups: `cryptography` `50.0.0`, Pillow `13.0.0`, PyJWT `2.15.0`,
Electron `42.11.18`, top-level Undici `7.30.1`, nested node-gyp Undici
`6.29.1`, Vite `6.4.5`, and source-map-js `1.2.2`. This is only a
source/lock selection observation. The supervisor must perform clean
exact-candidate install, audit, GUI test, typecheck, Electron build/package,
and remote alert recheck before any alert-closure, merge, build, or deploy
claim.

## Handoff status

This disposable offline checkout confirms the current tracked selections and
the regression that binds policy to them. It does not establish a merge, remote
alert closure, packaged build, deployment, installed artifact behavior, or
physical Windows acceptance. The deterministic supervisor must retain those
boundaries while validating the exact candidate and obtaining independent
review. The reviewer should inspect the complete generated lock diff, this
document, the policy test, and the exact-revision validation receipt.

## Reviewer-response: current evidence and GitHub Actions review

The dated noncritical register now explicitly says that its snapshot-time
`Unresolved` language is historical. Its current reconciliation covers all
medium/low package groups and calls out the still-open React Router and Torch
selections. It neither deletes dated evidence nor turns an advisory into an
audit exception.

### Workflow source review

This source-only review inspected `.github/workflows/ci.yml` and
`.github/workflows/windows-electron-artifact.yml`. Both declare top-level
`permissions: contents: read`; the Windows artifact workflow uses that
explicit permission, limits pull requests to `master`, and only reads signing
inputs at its signing step. When no certificate secret is available it reports
an unsigned artifact rather than claiming a signature.

The workflows reference maintained major-version actions (`checkout@v4`,
`setup-node@v4`, `setup-python@v5`, `cache@v4`, and `upload-artifact@v4`),
not immutable commit SHAs. That is a documented supply-chain hardening
follow-up, not evidence of compromise. Converting the action policy to
reviewed SHAs needs owner approval because it changes CI maintenance policy.
No workflow token, secret, permission, audit ignore, or verification gate was
widened or weakened by this task.

### Affected-path validation matrix and exact-candidate receipt requirements

| Path | Local evidence | Exact-candidate receipt required before acceptance |
| --- | --- | --- |
| Model, TTS, Voice | Transformers policy/lock selects `5.22.2`; the repository verifier checks declared versions, XTTS imports, Torch tensor operation, torchvision transform, and torchaudio resampling. | Resolve the default managed Voice profile from the candidate, run `python scripts/verify_dependency_profile.py --profile electron_voice`, then run focused model/TTS/Voice tests. Record the candidate revision, source/artifact SHA-256 values, commands, exit codes, and retained log hashes. |
| Auth and bridge | PyJWT floor/pin is `2.15.0`; bounded clean-clone evidence recorded 38 JWT/user-auth tests. | Run the focused JWT/user-auth and bridge suites from the candidate's installed environment. Record the candidate revision, installed PyJWT version, commands, exit codes, and log hashes. Prior clean-clone evidence is not final-head evidence. |
| HTTPS | Certifi record documents isolated Python 3.11 resolution and certificate-verified HTTPS; urllib3 selects `2.8.0`. | Install the candidate runtime profile and run the normal `requests` and `urllib` HTTPS probes with certificate verification enabled. Record the candidate revision, installed certifi/urllib3 versions, endpoint, commands, exit codes, and log hashes. |
| Electron/package | Current JS selections meet dated Electron/Undici/Vite/source-map-js floors; isolated GUI/core-package evidence is in the inventory. | From a clean candidate checkout, run install, audit, GUI tests, typecheck, build, Electron harness, default Voice-profile package build, package-content verification, and installed-artifact smoke. Record the candidate revision, lock and artifact SHA-256 values, commands, exit codes, and log hashes. No signing, release, or physical Windows acceptance is implied. |
| CI/security | Dependency policy binds generated Pipenv selections; workflow source review is above. | Run deterministic pytest, Ruff, diff, and dependency/security gates on the same candidate and obtain independent review. Record the candidate revision, commands, exit codes, and log hashes. Remote alert recheck remains required. |

An exact-candidate receipt is valid only when its stated revision and recorded
source hashes match the candidate that is independently reviewed. This scratch
checkout cannot create that receipt: it has no Python interpreter capable of
running the repository suite and no installed GUI dependency tree. It therefore
does not relabel prior isolated-copy results as final-candidate evidence. The
supervisor must execute and retain the required receipt after publishing this
patch to its candidate worktree.

### Status separation

This checkout supplies only source/lock/policy/documentation patch evidence.
It does not establish a commit, merge, remote alert closure, build,
deployment, release signing, installed-artifact behavior, or physical
acceptance. The supervisor owns exact-revision validation and the independent
reviewer owns review disposition.

## 2026-11-14 exact-candidate affected-path execution receipt

This section supplies the exact-candidate execution evidence the prior
section said was still missing. It was produced in this session's own
container (Python 3.11.18, Node 22.23.4/npm 10.9.9), not a disposable
pre-review scratch copy, by actually installing and running the suites below
against the exact commits named — not by trusting any externally-supplied
claim. It still does not establish a merge, remote alert closure, packaged
build artifact, deployment, release signing, or physical Windows acceptance.

Candidate revisions: `623571ecfa1531c95bcd365fd30426df2ec14329` (GUI
`minimatch` fix) then `d42a537905168cf3e40d94a8a9abf2208a82b176` (doctor
smoke-test mock fix), both on `claude/backend-security-repo-high-g3hdno`
(mirrored by fast-forward to
`ralph/cloud/backend/backend-security-repo-high-and-dispositi-ea5a451a`).

### Refreshed alert count

A `git push` of this work to GitHub on 2026-11-14 reported the live
default-branch (`master`) Dependabot count as **22 critical, 152 high, 125
moderate, 22 low (321 total)** — higher than the 2026-11-12 dated snapshot's
289 non-critical + 22 critical = 311, confirming the task instruction that
counts drift and must not be assumed current. This repository/session has no
tool that lists individual Dependabot alerts (the available GitHub MCP tools
and the permitted `gh api search/*` do not cover `dependabot/alerts`), so the
per-advisory detail could not be independently re-pulled here; the 2026-11-12
per-advisory register remains the best available detail and is corrected
below where it is independently verifiable as stale.

### Python gates (`.venv311`, Python 3.11.18)

- `pip install .` then `pip install -r requirements-dev.txt`: both exit 0.
- `pytest -q tests/test_dependency_policy.py`: **10/10 passed**.
- `python scripts/security_audit.py --release-gate`: **passed** (no merge
  markers, no exposed secrets, 0 actionable placeholder findings).
- `ruff check` and `black --check --diff` on every file this session
  changed: **clean**.
- `pip-audit --strict .` against the fully installed graph, including
  `requirements-electron-voice.txt` (torch 2.7.1/torchvision
  0.22.1/torchaudio 2.7.1/openai-whisper 20250625/openwakeword
  0.6.0/scipy 1.15.3/onnxruntime 1.22.0/scikit-learn 1.6.1) and
  `transformers==5.22.2`: **"No known vulnerabilities found."** `pip-audit`
  draws on PyPI/OSV advisory data, which is a different source and coverage
  than GitHub's GHSA/Dependabot feed, so this is corroborating evidence, not
  a replacement for a Dependabot recheck.
- `python scripts/verify_dependency_profile.py --profile electron_voice`:
  **passed** — `{'torch': '2.7.1', 'torchvision': '0.22.1', 'torchaudio':
  '2.7.1', 'openai-whisper': '20250625', 'openwakeword': '0.6.0', 'scipy':
  '1.15.3', 'onnxruntime': '1.22.0', 'scikit-learn': '1.6.1'}`, matching the
  pinned `requirements-electron-voice.txt` set exactly, with the torch
  tensor/torchvision transform/torchaudio resample smoke operations and the
  Whisper/openWakeWord/onnxruntime/scikit-learn/sounddevice/soundfile/
  imageio-ffmpeg imports all succeeding. (This container has no PortAudio
  system library by default; `libportaudio2` was installed from the
  distribution's own package repository to let `sounddevice` import, a
  container-local system-library install with no effect on the repository,
  secrets, or any other session.)
- HTTPS/certificate verification: `requests.get("https://pypi.org/...")`
  with default verification, and `urllib.request.urlopen` with
  `ssl.create_default_context(cafile=certifi.where())`, both returned
  HTTP 200 with urllib3 2.8.0 / certifi 2026.10.23 once the TLS trust
  anchor matched the actual path in use. This container routes all
  outbound HTTPS through a pinned local proxy (its own CA, not a public
  CA); a certifi-only trust store fails here for that sandbox-specific
  reason, not because of any urllib3/certifi defect — verification was
  never disabled, and a real end-user machine reaching PyPI's actual CA
  chain directly would not hit this proxy-specific case at all.
- `pytest -m "not slow and not audio and not gpu" -rs -q` (the CI-mirroring
  marker set), run twice:
  1. First pass (before any fix in this section): **2 failed, 9858 passed,
     63 skipped**. Both failures were root-caused, not left unexplained:
     - `tests/mobile_api/test_attachments.py::...test_ingests_private_attachment_and_returns_safe_provenance`
       failed only because this session's ad hoc `pip install .` resolved
       Werkzeug to 3.1.9 (pyproject.toml's floor is `werkzeug>=3.1.6`,
       unpinned) instead of the Pipenv-lock-pinned `3.1.6`
       (`Pipfile.lock` `default.werkzeug == 3.1.6`). Reinstalling
       `werkzeug==3.1.6` made all 29 tests in that file pass. This is a
       test-environment reproduction gap in a floor-only dependency range,
       not a regression in the exact Pipenv-locked candidate; it is noted
       here rather than silently fixed because tightening the floor is a
       dependency-policy decision this task has not been asked to make.
     - `tests/test_us327_smoke.py::TestDoctorSmoke::test_exits_zero_with_mocked_checks`
       failed because `_patch_all_checks()` never mocked
       `check_credential_vault_backend`, which `run_diagnostics()` does
       call. On any non-Windows/macOS platform `get_credential_vault()`
       raises `VaultUnavailableError` by the documented S4 fail-closed
       design, so that real check returns `Status.WARNING`, and the smoke
       test's "ready to use" / zero-warning assertion fails — on every
       Linux run, independent of any dependency change. This is a genuine,
       narrow pre-existing test gap (missing mock for a check added after
       the test was written), fixed in commit `d42a5379` by mocking it like
       every other check in that helper; verified 8/8 passed afterward.
  2. Second pass (werkzeug pinned back to the resolver-locked `3.1.6`,
     `test_us327_smoke.py` fix applied, transformers/electron-voice stack
     installed): **9863 passed, 60 skipped, 0 failed** in 513s. (The skip
     count drop from 63 to 60 is the three `test_transformers_shim.py`
     cases that previously skipped for lack of an installed `transformers`
     and now run and pass.)

### GUI gates (`gui/`, Node 22.23.4, npm 10.9.9)

Run at commit `623571ec` (the `minimatch` override) and reconfirmed
unaffected by the later test-only commit:

- `npm ci`: exit 0.
- `npm audit --audit-level=high`: exit 0, **0 critical/high** both before
  (16 moderate) and after (2 moderate) the `minimatch` override.
- `npm audit --json` before the override showed Undici, Vite, Electron,
  source-map-js, brace-expansion, fast-uri, and postcss-selector-parser are
  **no longer present as current `npm audit` findings at all** on this
  lock — only `minimatch` (transitive, via `electron-builder`'s
  `@electron/universal`/`app-builder-lib`, `GHSA-g3hv-75wg-m249`, range
  `>=7.0.0 <11.0.1`) and the already-tracked `react-router`/
  `react-router-dom` moderate pair remained.
- Added `"minimatch": "^11.0.1"` to `gui/package.json`'s existing
  `overrides` block (the same pattern already used there for `undici` and
  `brace-expansion`), regenerated the lock with
  `npm install --package-lock-only`, then reran `npm ci` from the new lock,
  `npm test -- --run` (**343/343 passed**), `npm run typecheck` (clean),
  `npm run lint` (clean, 2 pre-existing warnings), and `npm run build`
  (succeeded) — all green. `npm audit` dropped from 16 to 2 moderate
  findings; the remaining 2 are `react-router`/`react-router-dom`
  (`GHSA-wrjc-x8rr-h8h6`, `GHSA-337j-9hxr-rhxg`), which need a 6→7 major
  migration and were deliberately not attempted here (a breaking-change
  batch, not a small compatible one).

### Disposition correction

`DEPENDABOT-NONCRITICAL-DISPOSITIONS-2026-11-12.md` labeled all four
Transformers high-severity rows (`GHSA-29pf-2h5f-8g72`, `GHSA-fgcw-684q-jj6r`,
`GHSA-x9r9-c232-4q39`, `GHSA-xrqw-3rrv-vx5w`) "Unresolved compatibility
batch," but every one of those rows' own listed vulnerable ranges
(`< 5.3.0`, `< 5.5.0`, `>= 4.49.0, <= 5.8.1`, `< 5.10.0`) already excludes
the current `transformers==5.22.2` pin. That stale wording is corrected in
this session to match the file's own 2026-11-13 reconciliation table, which
already had this right. `torch` (`GHSA-rrmf-rvhw-rf47`, range `<= 2.12.1`)
was independently rechecked against the current `2.7.1` pin and remains
genuinely open — `2.7.1` falls inside that range — so its "Unresolved"
disposition is accurate and was left as-is; a torch upgrade needs a
coordinated `torch`/`torchvision`/`torchaudio` bump across every pinned
profile plus full Voice/model regression, which is out of scope for a small
compatible batch.

### Status separation (2026-11-14)

This is real local execution evidence against the exact commits named above,
gathered in this session's own container. It still does not establish a
remote Dependabot alert closure, a merge to `master`, a packaged build
artifact, a deployment, a release signature, or physical Windows acceptance
(US-130). Independent review of commits `623571ec` and `d42a5379` remains
required before any of those claims could be made.

## 2026-11-14 completion-commit correction and Electron-evidence blocker

`HEAD` at `647d66f` (`ralph-cloud-complete: ...`) is an **empty** commit with
no file changes. It was created immediately after the receipt above without
satisfying the matrix row this document itself states is still outstanding:
"From a clean candidate checkout, run install, audit, GUI tests, typecheck,
build, Electron harness, default Voice-profile package build,
package-content verification, and installed-artifact smoke." That row was
never executed, so the completion claim was premature. This is consistent
with independent reviewer feedback relayed into this session, which withheld
approval for the same reason. No new `ralph-cloud-complete` commit is made
in this session; the prior one should not be treated as a valid completion
signal.

### Why this worker cannot close that gap

This session's container is Linux-only (`uname -a`: `Linux ... x86_64 GNU/Linux`)
with no `powershell`/`pwsh` and no `wine` installed. Checked directly in
this container before writing this note:

- `gui/package.json`'s `predist` script is `npm run runtime:build && npm run
  build`, and `runtime:build` is `powershell.exe -NoProfile
  -ExecutionPolicy Bypass -File ../scripts/build_managed_python_runtime.ps1
  -Profile Voice`. `npm run dist` (`electron-builder --publish never`) runs
  `predist` first, so it fails immediately in this container for lack of
  `powershell.exe` before `electron-builder` ever runs, independent of any
  dependency-version fix made here.
- `scripts/test_installed_electron_artifact.ps1` is a PowerShell script that
  exercises Windows-specific installed-artifact behavior (Task Scheduler
  `ONLOGON` registration, DPAPI-backed credential vault, an installed NSIS
  binary). It requires an actual Windows host, which this container is not,
  and which `list_computer_folders` confirmed has no attached machine to
  connect to in this session.

These are the same Windows-only mechanisms CLAUDE.md itself documents
(managed Python Voice runtime build, NSIS installer, DPAPI vault, Windows
Task Scheduler background runtime, PowerShell installed-artifact smoke).
Producing this evidence needs a Windows-capable worker or the repository
owner's own Windows machine, not more effort inside this Linux container.

### Remote alert recheck is still unavailable to this session

Re-verified directly: `gh api repos/Blueibear/AskRex-Assistant/dependabot/alerts`
returns `403 Resource not accessible by integration`. This session's GitHub
access genuinely cannot list or recheck individual Dependabot alerts, matching
the prior finding in this document. Alert-instance counts quoted anywhere in
coordination material (including figures attributed to an "owner-operator")
are therefore unverifiable from inside this session and must not be relied on
as current.

### What remains true and unchanged

The dependency-selection evidence recorded earlier in this document (Python
gates, `pip-audit --strict`, GUI `npm test`/`typecheck`/`build`/`audit`,
dependency-policy tests, the `minimatch` override, and the Transformers/
urllib3/msgpack/filelock/cryptography/Pillow/PyJWT/Electron/Undici/Vite/
source-map-js reconciliation) is unaffected by this note and was not redone.
The deferred `react-router`/`react-router-dom` major-version bump and the
coordinated `torch`/`torchvision`/`torchaudio` upgrade remain open,
evidence-backed dispositions (breaking-change batches, correctly out of
scope for a small compatible batch), not suppressions.

### Disposition

Status: **blocked**, narrowly, on Electron packaging/package-content/
installed-artifact evidence and on remote Dependabot alert recheck, both of
which require capabilities this session does not have (a Windows host for
the former, Dependabot API access for the latter). Everything else this
task asked for that is achievable from a Linux worker with this session's
GitHub access has real, reproduced evidence above. Next action: a
Windows-capable session (or the repository owner, on their own Windows
machine) runs `npm run dist`, `scripts/verify_electron_package_contents.py`,
and `scripts/test_installed_electron_artifact.ps1` against this exact branch
head, and a repository owner with Dependabot API access re-pulls the current
alert list, before independent review can accept completion. Owner:
backend (packaging step), repository owner (Dependabot access). This
session does not claim merge, deployment, build, or physical-acceptance
status.

## Windows operator follow-up, 2026-11-14 UTC

The Linux worker's packaging and alert-access blockers above were resolved by
the authorized Windows operator without changing the dependency selections.
This follow-up supersedes the earlier session's blocked disposition for those
two evidence items; it does not claim alert closure, merge, deployment, or
physical-device acceptance.

On commit `3a9afecadfbafb024dd35a34d1e6a99c770ef8f3`, a clean Windows
`npm run dist` completed with exit 0. The installer was 450,052,540 bytes,
SHA-256 `6F6923EB274DB4E90BCE5DF647D324FDA905EB4717C435F04BC40F3E93763557`.
The package-content check exited 0, and the Electron harness passed (1 test).
The existing [Windows Electron Artifact workflow run](https://github.com/Blueibear/AskRex-Assistant/actions/runs/43559782518)
was explicitly dispatched on the same commit and completed successfully. It
passed the managed-runtime build, installer, package-content check, clean
first-run setup, and full installed-artifact smoke, including detached
background survival, uninstall, reinstall, and final uninstall. The uploaded
first-run and installed-artifact diagnostics both report `status: success`;
their SHA-256 hashes are `1AEF3F83C3F5E4C64590DC06CA4A29AC237A0B5D15AABE67D8899FA6CFC05A0E`
and `6E40ED3474430B4A93FADFF207DACCF1A4FE58DB615E5AB5CE9EA136EC29D5E5`.
The complete job-log SHA-256 is
`06F1006D25A3D751382055B5EBD30DE41CBCBF351D1271C3B9A0E19AB227EF9F`.
The local coordination receipt is
`evidence/windows-electron-1893009-20261113/RECEIPT.md`.

The installed smoke script was corrected to use the ID of the user it actually
created and to force the background-startup path it asserts. The managed
runtime import probe now distinguishes a timeout from a launch error and
allows 180 seconds for a cold installed runtime. These changes are in
`3a9afec`, and the workflow ran those committed scripts. The local desktop's
non-elevated token cannot create even a separate harmless ONLOGON task
(`Access is denied`); an elevated local run passed, and the Windows CI run
passed. Normal non-elevated background registration on this particular desktop
remains an observed limitation, not a claim about every Windows host.

The Windows operator also queried the authenticated Dependabot API at
`2026-11-14T05:01:26Z`: 321 open alert instances on the default branch,
22 critical, 152 high, 125 medium, and 22 low. These are current remote
counts, while the 147-high snapshot above is historical. The local branch
fixes cannot close alerts until merged and re-evaluated; the per-advisory
exposure/owner/next-action dispositions remain in the dated register.
Independent review of the final Ralph candidate and the supervisor's own
exact-revision gates are still required before this task can be accepted.
