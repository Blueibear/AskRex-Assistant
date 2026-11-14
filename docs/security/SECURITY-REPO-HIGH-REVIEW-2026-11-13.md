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
