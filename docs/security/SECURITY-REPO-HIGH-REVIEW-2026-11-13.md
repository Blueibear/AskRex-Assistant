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
remaining high paths that local source inspection showed still select a
vulnerable lock version. They are not suppressed or treated as resolved.

## Remaining implementation batch

| Group | Local selection | Affected advisory IDs | Exposure condition | Owner | Required next action |
| --- | --- | --- | --- | --- | --- |
| Transformers | Pipfile.lock `default`: `5.5.0`; source/profile declarations: `5.22.2` | `GHSA-29pf-2h5f-8g72`, `GHSA-x9r9-c232-4q39`, `GHSA-xrqw-3rrv-vx5w` | A model or Voice profile loads attacker-controlled model/tokenizer content through the locked dependency. | backend | Regenerate the complete Python 3.11 Pipenv lock, retain resolver-generated hashes, then run focused model, TTS, Voice, and packaged Voice-profile checks. |
| urllib3 | Pipfile.lock `default`: `2.7.0`; `develop`: `2.6.3`; Pipfile and packaged runtime: `2.8.0` | `GHSA-8988-9cw3-xx77`, `GHSA-vxq7-64xx-v4gw` | An installed runtime handles an affected HTTP response through the stale locked graph. | backend | Regenerate the complete Python 3.11 Pipenv lock, then run an install and normal HTTPS certificate-validation checks without disabling verification. |
| msgpack | Pipfile.lock `develop`: `1.1.2`; Pipfile development constraint: `>=1.2.3` | `GHSA-6v7p-g79w-8964`, `GHSA-j586-36cw-2gc2` | A development or CI environment deserializes attacker-controlled MessagePack input through the stale development graph. | backend | Regenerate the complete Python 3.11 Pipenv lock and run the repository CI/security gates. |
| filelock | Pipfile.lock `develop`: `3.24.3`; the separate pip-compile evidence resolves `4.2.10`, but is not the Pipenv lock | `GHSA-v4x8-3wr5-3wq8` | A development or CI Pipenv environment reaches file locking on Windows through the stale development graph. | backend | Regenerate the complete Python 3.11 Pipenv lock selecting at least `4.2.1`, then run the repository CI/security gates. |

The Python lock is a generated security input. No lock record or artifact hash
was hand-written from package metadata, and no audit override or suppression
was added.

## Other high-path reconciliation

The local candidate already selects the reported fixed versions for these
high groups: `cryptography` `50.0.0`, Pillow `13.0.0`, PyJWT `2.15.0`,
Electron `42.11.18`, top-level Undici `7.30.1`, nested node-gyp Undici
`6.29.1`, Vite `6.4.5`, and source-map-js `1.2.2`. This is only a
source/lock selection observation. The supervisor must perform clean
exact-candidate install, audit, GUI test, typecheck, Electron build/package,
and remote alert recheck before any alert-closure, merge, build, or deploy
claim.

## Worker validation and handoff

Source inspection was performed in this disposable offline checkout. A
resolver attempt used `uvx --offline --from pipenv pipenv lock --python 3.11`.
It could not start a managed Python installation because the isolation denies
access to `C:\\Users\\james\\AppData\\Roaming\\uv\\python`; the Windows
Python launcher is likewise access-denied. Therefore no generated lock,
install, Voice/model, bridge, Electron build/package, auth, HTTPS, or
repository security gate is claimed from this worker invocation.

The deterministic supervisor must run the Python 3.11 Pipenv resolver in its
authorized environment, validate the resulting exact candidate with the
required auth, image, Voice/model, bridge, install, HTTPS, Electron packaging,
and security/test gates, refresh the remote alert inventory, and obtain a
new independent review. The independent reviewer should verify this document,
the complete generated lock diff, and the exact-revision validation receipt.
