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

## Remaining findings and required next phase

The authoritative inventory identifies non-certifi alert instances, including
critical/high findings. Their complete package/advisory/path classification is
unknown in this scratch checkout except for the supplied aggregate and should
be handled in the queued compatible batches. No alert has been marked closed.

Required supervisor checks on the exact candidate revision:

1. Regenerate/validate the Pipenv lock using the authorized resolver, then
   confirm the lock metadata and both dependency groups match the Pipfile.
2. Resolve the managed runtime requirements with hash enforcement and perform
   ordinary HTTPS certificate validation without disabling certificate checks.
3. Run the exact-revision repository gates and independent review before any
   publication, merge, build, or deployment claim.
