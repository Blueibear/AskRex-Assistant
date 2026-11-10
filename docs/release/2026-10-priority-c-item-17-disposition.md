# Priority C item 17 disposition

Date: 2026-11-10

This is a bounded source disposition for the October pre-release remediation.
It records what was inspected and deliberately does not claim a physical
Windows, CUDA, or release acceptance result.

## Dependency profile compatibility

The supported Python 3.11 profiles are now recorded as exact release sets in
`docs/DEPENDENCIES.md` and enforced by `tests/test_dependency_policy.py`.
The managed Electron Voice profile now carries the complete published CPU set:
torch 2.7.1, torchvision 0.22.1, and torchaudio 2.7.1. The managed-runtime
builder imports and version-checks that same set before packaging. CUDA 12.4
remains the separate published 2.6.0/0.21.0/2.6.0 set and must not install the
CPU `ml` extra over it.

The evidence is profile consistency and builder import/version checks. A
future upgrade still requires an isolated install, profile-specific runtime
smoke, audit, and applicable hardware acceptance before it can be called
compatible.

## Priority C item 17 review

| Item | Source finding | Disposition |
| --- | --- | --- |
| `cloud_fallback_cooldown_seconds` | `AppConfig` parses the value, but `Assistant` currently constructs `ModelRouter` without passing it, so the router always uses its default. | Defer a narrow wiring fix with a config-to-router regression. It changes runtime routing behavior and is not needed for the security release blocker. |
| Routing vocabulary | The established persisted router vocabulary (`local_preferred`, `cloud_only`, `local_only`) and the newer speech-policy vocabulary serve separate contracts. | No rename in this release. A migration must inventory persisted config, GUI labels, API payloads, and tests before aliases are changed. |
| Deprecated shim/junk files | `fix_tts.bat` still directs users to an obsolete Transformers 4.37 installation that conflicts with the supported 5.5 policy. | Do not execute it. Remove or replace it in a separately reviewed documentation/surface cleanup with a regression preventing obsolete install guidance. |
| Commitlint relaxations | `.commitlintrc.yml` still disables `subject-case`, `header-max-length`, and `body-max-line-length` for a completed OpenClaw migration. | Defer re-tightening to a dedicated history-aware CI change. Re-enabling it here could reject existing PR history without validating the exact action behavior. |

No item in this disposition weakens an audit, CI gate, security check, or
physical acceptance boundary.
