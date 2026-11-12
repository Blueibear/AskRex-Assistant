# Repository Dependency Inventory - 2026-11-12

Status: phase 1 inventory and the `certifi` remediation candidate only. This
record is not evidence that any GitHub alert is closed, that a pull request is
merged, or that a build or deployment occurred.

## Evidence boundary

The authorized operator collected the GitHub REST inventory on 2026-11-12 at
`2026-11-12T03:41:18.3040481-06:00`. It recorded `master` as
`a9abdd8c4319d81c603ac49251ff8fc4f042d959` and the active branch
`lead/us126-listening-privacy-tray` as
`007503b19ea711b6f5ab44035c27001351fbbf07`.

That inventory contained 311 open Dependabot alert instances: 22 critical,
147 high, 120 medium, and 22 low. It also recorded 27 open pull requests. The
full per-alert data and live branch/PR state are inaccessible from this
disposable, offline scratch checkout, so details beyond the bounded evidence
below are unknown rather than zero.

## Certifi classification and candidate

| Alert instances | Package/advisory | Manifest path | Path classification | Candidate |
| --- | --- | --- | --- | --- |
| #483 | `certifi` / `GHSA-6q48-6c7w-9gw2` | `Pipfile` | direct runtime declaration | `2026.10.23` |
| #484 | `certifi` / `GHSA-6q48-6c7w-9gw2` | `Pipfile.lock` | locked runtime and development resolver graph | `2026.10.23` |
| #502 | `certifi` / `GHSA-6q48-6c7w-9gw2` | `requirements-electron-runtime.txt` | packaged Electron Python runtime | `2026.10.23` |

GitHub's supplied vulnerable range is `>= 2019.03.09, < 2026.10.18`; the
first patched release is `2026.10.18`. This candidate uses the later current
PyPI release `2026.10.23`, with PyPI-published hashes:

- wheel: `sha256:e9b54de76e530bb6cf0ccd036787103acb58fd42ba930ea8bf948ddbaa2f61d1`
- source distribution: `sha256:a9895e6e2758b371e0545e321a3a8fd1fcfd9829ada8824a936dfb16aaaf36cd`

The prior pins were `2024.12.14` in the Pipenv declaration/lock and
`2026.7.22` in the packaged runtime. All were in the supplied vulnerable
range. Both `default` and `develop` lock sections now carry the matching
version and artifact hashes.

Existing open Dependabot PR #450,
`dependabot/pip/certifi-2026.10.18` at
`d2bbedf1b2e8fd6d7ffc26b5bb788f94d929a38a`, was reported to update only
`Pipfile` and `Pipfile.lock`. This candidate does not merge or overwrite that
branch and also updates the separately packaged runtime requirement.

## Execution evidence

Trusted operator execution used an isolated copy of exact revision
`e22c6aaf3bce22d4602de71d70c520ccddd23fd4`. The copied source inputs had
these SHA-256 values: `Pipfile`
`21EC1234044CD124CF469CFD66770BD857F2528BE8E05EBB216427C21D304970`,
`Pipfile.lock`
`3CFD6A03985993E18EB1B3730F05A2DE3DA6AFD4783BA82C0426456D6360FA8A`, and
`requirements-electron-runtime.txt`
`D3AC3AD4363D4FF8181DC5788DB59D2D56832273773B7D9ABD49C9A80CE564C7`.

Pipenv 2026.11.0 verified the exact copied Pipfile and lock successfully
(`Pipfile.lock is up-to-date.`). On a separate isolated copy, `pipenv lock`
with Python 3.11 resolved both dependency groups successfully and generated
the recorded Pipfile hash
`e758dc04e5ddee63a3b2432f9c365947026a400920515e64d3f7fb2d9b053c14`.
`uv pip compile` and `uv pip install -r` both succeeded for the exact copied
Electron runtime requirements in an isolated Python 3.11.9 environment.

In that installed profile, `requests.get` with default certificate
verification and `urllib.request.urlopen` using
`ssl.create_default_context(cafile=certifi.where())` each returned HTTP 200
from `https://pypi.org/pypi/certifi/json`, with certifi `2026.10.23`.
Certificate verification was not disabled. The bounded command record and
log SHA-256 values are in
`evidence/certifi-e22c6aa-exact-resolution-https-20261112.json`
(SHA-256 `AF806068D656D5665E252396ABF8FF019DD85628F290F3100B7061C423DBCCC1`).

This evidence demonstrates isolated dependency resolution and ordinary HTTPS
certificate validation only. It does not demonstrate installed Electron
packaging, Dependabot alert closure, pull-request merge, build, or deployment.

## Refreshed critical inventory and bounded remediation state

The trusted operator refreshed the GitHub Dependabot and official package
metadata inventory at 2026-11-12 05:10 CST. It reported 22 critical alert
instances. The groups below account for all 22 instances in that inventory;
the number is an alert-instance count, not an independent exploit-path count.
No alert has been marked closed.

| Instances | Package/advisory | Actual path | Current state |
| --- | --- | --- | --- |
| 3 | `certifi` / `GHSA-6q48-6c7w-9gw2` | Pipenv direct/lock and packaged Electron Python runtime | Fixed in the prior reviewed certifi phase at `2026.10.23`; its exact-revision resolver and HTTPS evidence is above. |
| 15 | `Pillow` / five current critical advisories | `Pipfile` direct runtime declaration, `Pipfile.lock` resolved runtime graph, and packaged Electron Python runtime requirements | The direct declaration, verified resolver lock, and packaged runtime pin now select `13.0.0`, the first fixed release for all five. Exact-revision review and release checks remain pending. |
| 2 | `PyJWT` / `GHSA-ffc3-869f-jxw9` | Project direct dependency range and packaged Electron Python runtime requirements | `2.14.0` is the first fixed release. The project range is prepared at `>=2.14.0` and the packaged runtime pin at `2.15.0`. PyJWT is absent from the current Pipenv graph, so no synthetic lock record was added. |
| 2 | `tinypool` / two current critical advisories | GUI development test path through Vitest in `gui/package-lock.json` | Vitest is updated to `4.1.11`; the explicit Tinypool override is removed, and the regenerated lock contains no Tinypool package. A clean isolated install and GUI tests passed; exact-revision review remains pending. |

The five Pillow advisories are `GHSA-332p-7hrc-92wf`,
`GHSA-5x8w-8jj5-q52h`, `GHSA-8g5g-8vmr-9782`,
`GHSA-v9pv-5gc8-w2m6`, and `GHSA-w889-685w-c8fh`. Each had three alert
instances in the 05:10 CST inventory and first fixed release `13.0.0`.
Tinypool's two advisories are `GHSA-5gmw-xhrv-c9v3` (first fixed `2.1.1`)
and `GHSA-85c8-ppgw-ccpr` (first fixed `2.1.2`).

The supplied critical inventory identifies no other critical package groups.
The listed historical high/medium/low packages require a separate refreshed
per-advisory inventory before they are changed; they are unknown here, not
treated as zero.

### Resolver and validation evidence

The trusted operator resolved the exact candidate Pipfile with Pipenv
2026.11.0 in isolation. The Pillow `13.0.0` entry has 83 artifact hashes and
the generated Pipfile metadata hash is
`4d32502e310473589bf6e33e9093f97a0a4006b31f13b686fb2156da5f6c6533`.
Only that entry and metadata hash were applied to the existing lock; all other
dependency records were preserved. `uvx --from pipenv pipenv verify` reported
`Pipfile.lock is up-to-date` on this candidate. The resolver entry and its
checksums are recorded in the coordination evidence for this task.

An isolated GUI copy regenerated `gui/package-lock.json` with Vitest 4.1.11
after removing the Tinypool override. `npm ci --offline` and `npm ls --all`
passed against the resulting lock; all 343 GUI tests and typecheck passed
after making the tray mock constructor-compatible. The isolated candidate
also passed GUI build and `npm audit --audit-level=high` (zero critical/high
findings at the time). These checks do not replace supervisor validation and
independent review on the committed revision.

### Final dependency and packaged-artifact checks

On clean source commit `c2d5d27cfe877ccbfd1e6357a84d0cccefe9f4a7`,
the trusted operator used a disposable clone and npm 11.19.1. `npm ci
--offline` passed on the committed lock after adding five resolver-generated
optional Electron package records; the same lock also passed npm 11.6.0
offline installation. On the exact clone, all 343 GUI tests, typecheck,
`npm run build`, and `npm audit --audit-level=high` passed. The audit reported
zero high/critical and 19 moderate findings at that time. The committed lock
SHA-256 is `AB3297820A1EC4D989267D52CE998CBEF6540BBC4BF4C808BC9F9BA0BC35FAB2`.

In that clone, `build_managed_python_runtime.ps1 -RuntimeProfile Core`
completed and its embedded Python import smoke passed. `electron-builder
--win --dir --publish never` then produced an unpacked Windows artifact
containing `AskRex.exe`, `resources/app.asar`, and Python 3.11.9. Running
the packaged `python.exe -I` imported Rex and reported Pillow 13.0.0,
PyJWT 2.15.0, and certifi 2026.10.23. The directory build was unsigned.
This verifies a core-profile packaged dependency path, not the default Voice
profile, an installed NSIS artifact, a launched GUI, deployment, or physical
Windows acceptance. The build, Electron directory build, and GUI test log
SHA-256 values are respectively
`9D021785815B6CE0AA1532BD77280D4FBFC2C91E6EE87367CD6DBEEAE9BFAFED`,
`48BADE1B5FAF1236E83AEE2EF6CF93DD3138451E48A296AA67D95D9A10977224`,
and `80B3D135A92B4E43839834D65D7E49A254881BB16CC651C01BAA2DE1FD1A70FB`.

Before any alert-closure or release claim, the supervisor must inspect the
exact-revision backend gates and independent review. Release acceptance also
requires the default Voice-profile package, installed artifact behavior, and
physical Windows checks, none of which this core-profile directory build
establishes. No merge, deployment, or alert closure is claimed.

Required supervisor checks on the exact candidate revision:

1. Verify the committed Pipenv lock metadata and both dependency groups match
   the exact candidate Pipfile.
2. Inspect the recorded isolated runtime resolution and ordinary HTTPS
   certificate-validation evidence for the exact dependency declarations.
3. Run the exact-revision repository gates and independent review before any
   publication, merge, build, or deployment claim.
