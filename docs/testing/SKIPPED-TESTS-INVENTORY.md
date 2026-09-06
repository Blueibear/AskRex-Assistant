# Skipped Tests Inventory

Updated for `US-040` on 2026-08-07 after replacing the generated-coverage-artifact skips and routing the remaining temporary compatibility/test-artifact debt to dedicated follow-up stories.

## CI Runtime Skip Budget

- **Budget:** 82 executed skipped tests in the primary Linux/Python 3.11 CI suite.
- **Command scope:** `pytest -m "not slow and not audio and not gpu" -rs` with the existing coverage options.
- **Evidence:** PR #348 established the 119-skip baseline; US-039 archived 37 retired-dashboard tests, so the enforced budget is now 82. Final Linux CI confirmation is required before US-039 closes.
- **Enforcement:** `python scripts/check_skip_budget.py coverage.txt` runs immediately after the primary suite and fails if the executed skip count exceeds 82 or the pytest summary cannot be parsed.
- **Maintenance rule:** when a skipped test is removed, lower `SKIP_BUDGET` in the same PR. Any increase requires an updated inventory entry and explicit rationale.

The runtime count is intentionally separate from the executable source-site count below. A single skip marker can skip multiple parameterized tests, and platform or dependency guards may not execute on every runner.

## Validation Snapshot

- `python scripts/check_skip_inventory.py`: passed; 129 executable skip sites match the inventory exactly by file, line, type, and reason.
- Python 3.11 US-040 full coverage validation collected 8,498 tests and completed with 8,449 passed, 49 skipped, 0 failed; total coverage was 83.26%.
- Every remaining row has one explicit action: `keep` or `fix`; all `archive` and `replace` actions from US-039/US-040 are complete.
- Permanent guards retain a written rationale; all non-trivial actions link to a non-circular follow-up story.
- US-039 removed 14 retired-surface skip sites; US-040 removes two generated-artifact skip sites. The remaining 129 sites cover supported code, dedicated follow-up repair work, and legitimate platform/dependency guards, including the two permanent Windows Job Object contract guards added by US-124.

## Classification and Action Summary

| Classification | Count | Action | Follow-up |
|---|---:|---|---|
| `optional-dep-skip` | 22 | `keep` | permanent: optional dependency/tool/environment guard |
| `platform-skip` | 15 | `keep` | permanent: platform/runtime-specific guard |
| `temporary-bug-skip` | 26 | `fix` | US-089 |
| `temporary-bug-skip` | 25 | `fix` | US-090 |
| `temporary-bug-skip` | 22 | `fix` | US-091 |
| `temporary-bug-skip` | 13 | `fix` | US-092 |
| `temporary-bug-skip` | 6 | `fix` | US-093 |

## Inventory

| File | Line | Skip type | Skip reason text | Classification | Action | Follow-up |
|---|---:|---|---|---|---|---|
| `tests/conftest.py` | 221 | `skip` | No async test runner installed (anyio or pytest-asyncio required) | `optional-dep-skip` | `keep` | permanent: optional dependency/tool/environment guard |
| `tests/test_audio_device_selection.py` | 9 | `pytest.skip` | f"sounddevice unavailable: {exc}" | `optional-dep-skip` | `keep` | permanent: optional dependency/tool/environment guard |
| `tests/test_calendar_service.py` | 131 | `skipif` | CalendarService event-bus style API not available in this build. | `temporary-bug-skip` | `fix` | `US-089` |
| `tests/test_calendar_service.py` | 185 | `skipif` | CalendarEvent is not a pydantic-style model in this build. | `temporary-bug-skip` | `fix` | `US-089` |
| `tests/test_calendar_service.py` | 212 | `skipif` | CalendarEvent.overlaps_with not available in this build. | `temporary-bug-skip` | `fix` | `US-089` |
| `tests/test_calendar_service.py` | 242 | `skipif` | Mock-file calendar service tests apply to the newer implementation only. | `temporary-bug-skip` | `fix` | `US-089` |
| `tests/test_calendar_service.py` | 252 | `skipif` | Mock-file calendar service tests apply to the newer implementation only. | `temporary-bug-skip` | `fix` | `US-089` |
| `tests/test_calendar_service.py` | 262 | `skipif` | Mock-file calendar service tests apply to the newer implementation only. | `temporary-bug-skip` | `fix` | `US-089` |
| `tests/test_calendar_service.py` | 271 | `skipif` | Mock-file calendar service tests apply to the newer implementation only. | `temporary-bug-skip` | `fix` | `US-089` |
| `tests/test_calendar_service.py` | 288 | `skipif` | Mock-file calendar service tests apply to the newer implementation only. | `temporary-bug-skip` | `fix` | `US-089` |
| `tests/test_calendar_service.py` | 300 | `skipif` | Mock-file calendar service tests apply to the newer implementation only. | `temporary-bug-skip` | `fix` | `US-089` |
| `tests/test_calendar_service.py` | 310 | `skipif` | Mock-file calendar service tests apply to the newer implementation only. | `temporary-bug-skip` | `fix` | `US-089` |
| `tests/test_calendar_service.py` | 338 | `skipif` | Mock-file calendar service tests apply to the newer implementation only. | `temporary-bug-skip` | `fix` | `US-089` |
| `tests/test_calendar_service.py` | 353 | `skipif` | Mock-file calendar service tests apply to the newer implementation only. | `temporary-bug-skip` | `fix` | `US-089` |
| `tests/test_calendar_service.py` | 368 | `skipif` | Mock-file calendar service tests apply to the newer implementation only. | `temporary-bug-skip` | `fix` | `US-089` |
| `tests/test_calendar_service.py` | 384 | `skipif` | Mock-file calendar service tests apply to the newer implementation only. | `temporary-bug-skip` | `fix` | `US-089` |
| `tests/test_calendar_service.py` | 394 | `skipif` | Mock-file calendar service tests apply to the newer implementation only. | `temporary-bug-skip` | `fix` | `US-089` |
| `tests/test_calendar_service.py` | 404 | `skipif` | Mock-file calendar service tests apply to the newer implementation only. | `temporary-bug-skip` | `fix` | `US-089` |
| `tests/test_calendar_service.py` | 423 | `skipif` | Mock-file calendar service tests apply to the newer implementation only. | `temporary-bug-skip` | `fix` | `US-089` |
| `tests/test_calendar_service.py` | 432 | `skipif` | Mock-file calendar service tests apply to the newer implementation only. | `temporary-bug-skip` | `fix` | `US-089` |
| `tests/test_calendar_service.py` | 441 | `skipif` | CalendarService.find_conflicts not available in this build. | `temporary-bug-skip` | `fix` | `US-089` |
| `tests/test_calendar_service.py` | 464 | `skipif` | CalendarService.find_conflicts not available in this build. | `temporary-bug-skip` | `fix` | `US-089` |
| `tests/test_calendar_service.py` | 486 | `skipif` | CalendarService.find_conflicts not available in this build. | `temporary-bug-skip` | `fix` | `US-089` |
| `tests/test_calendar_service.py` | 496 | `skipif` | CalendarService.get_upcoming_events not available in this build. | `temporary-bug-skip` | `fix` | `US-089` |
| `tests/test_calendar_service.py` | 507 | `skipif` | CalendarService.get_upcoming_events not available in this build. | `temporary-bug-skip` | `fix` | `US-089` |
| `tests/test_calendar_service.py` | 518 | `skipif` | CalendarService.get_all_events not available in this build. | `temporary-bug-skip` | `fix` | `US-089` |
| `tests/test_calendar_service.py` | 529 | `skipif` | Mock-file calendar service tests apply to the newer implementation only. | `temporary-bug-skip` | `fix` | `US-089` |
| `tests/test_calendar_service.py` | 553 | `skipif` | CalendarEvent serialization test applies to the newer implementation only. | `temporary-bug-skip` | `fix` | `US-089` |
| `tests/test_credential_vault.py` | 134 | `pytest.skip` | This assertion applies to non-Windows production | `platform-skip` | `keep` | permanent: platform/runtime-specific guard |
| `tests/test_credential_vault.py` | 139 | `skipif` | DPAPI vault is Windows-only | `platform-skip` | `keep` | permanent: platform/runtime-specific guard |
| `tests/test_email_account_isolation.py` | 67 | `skipif` | Real DPAPI isolation is Windows-only | `platform-skip` | `keep` | permanent: platform/runtime-specific guard |
| `tests/test_email_service.py` | 96 | `skipif` | EmailService event-bus style API not available in this build. | `temporary-bug-skip` | `fix` | `US-090` |
| `tests/test_email_service.py` | 187 | `skipif` | EmailSummary pydantic-style model not available in this build. | `temporary-bug-skip` | `fix` | `US-090` |
| `tests/test_email_service.py` | 212 | `skipif` | Mock-file email service tests apply to the newer implementation only. | `temporary-bug-skip` | `fix` | `US-090` |
| `tests/test_email_service.py` | 222 | `skipif` | Mock-file email service tests apply to the newer implementation only. | `temporary-bug-skip` | `fix` | `US-090` |
| `tests/test_email_service.py` | 232 | `skipif` | Mock-file email service tests apply to the newer implementation only. | `temporary-bug-skip` | `fix` | `US-090` |
| `tests/test_email_service.py` | 241 | `skipif` | Mock-file email service tests apply to the newer implementation only. | `temporary-bug-skip` | `fix` | `US-090` |
| `tests/test_email_service.py` | 253 | `skipif` | Mock-file email service tests apply to the newer implementation only. | `temporary-bug-skip` | `fix` | `US-090` |
| `tests/test_email_service.py` | 263 | `skipif` | Mock-file email service tests apply to the newer implementation only. | `temporary-bug-skip` | `fix` | `US-090` |
| `tests/test_email_service.py` | 273 | `skipif` | Mock-file email service tests apply to the newer implementation only. | `temporary-bug-skip` | `fix` | `US-090` |
| `tests/test_email_service.py` | 290 | `skipif` | Mock-file email service tests apply to the newer implementation only. | `temporary-bug-skip` | `fix` | `US-090` |
| `tests/test_email_service.py` | 299 | `skipif` | Mock-file email service tests apply to the newer implementation only. | `temporary-bug-skip` | `fix` | `US-090` |
| `tests/test_email_service.py` | 308 | `skipif` | Mock-file email service tests apply to the newer implementation only. | `temporary-bug-skip` | `fix` | `US-090` |
| `tests/test_email_service.py` | 326 | `skipif` | Mock-file email service tests apply to the newer implementation only. | `temporary-bug-skip` | `fix` | `US-090` |
| `tests/test_email_service.py` | 344 | `skipif` | Mock-file email service tests apply to the newer implementation only. | `temporary-bug-skip` | `fix` | `US-090` |
| `tests/test_email_service.py` | 362 | `skipif` | Mock-file email service tests apply to the newer implementation only. | `temporary-bug-skip` | `fix` | `US-090` |
| `tests/test_email_service.py` | 380 | `skipif` | Mock-file email service tests apply to the newer implementation only. | `temporary-bug-skip` | `fix` | `US-090` |
| `tests/test_email_service.py` | 399 | `skipif` | Mock-file email service tests apply to the newer implementation only. | `temporary-bug-skip` | `fix` | `US-090` |
| `tests/test_email_service.py` | 417 | `skipif` | Mock-file email service tests apply to the newer implementation only. | `temporary-bug-skip` | `fix` | `US-090` |
| `tests/test_email_service.py` | 430 | `skipif` | Mock-file email service tests apply to the newer implementation only. | `temporary-bug-skip` | `fix` | `US-090` |
| `tests/test_email_service.py` | 440 | `skipif` | Mock-file email service tests apply to the newer implementation only. | `temporary-bug-skip` | `fix` | `US-090` |
| `tests/test_email_service.py` | 450 | `skipif` | Mock-file email service tests apply to the newer implementation only. | `temporary-bug-skip` | `fix` | `US-090` |
| `tests/test_email_service.py` | 464 | `skipif` | Mock-file email service tests apply to the newer implementation only. | `temporary-bug-skip` | `fix` | `US-090` |
| `tests/test_email_service.py` | 474 | `skipif` | EmailSummary pydantic-style model not available in this build. | `temporary-bug-skip` | `fix` | `US-090` |
| `tests/test_email_service.py` | 498 | `skipif` | EmailSummary pydantic-style model not available in this build. | `temporary-bug-skip` | `fix` | `US-090` |
| `tests/test_email_service.py` | 528 | `skipif` | EmailSummary pydantic-style model not available in this build. | `temporary-bug-skip` | `fix` | `US-090` |
| `tests/test_event_bus.py` | 79 | `skipif` | Legacy EventBus.publish(event_type, payload) API not available in this build. | `temporary-bug-skip` | `fix` | `US-092` |
| `tests/test_event_bus.py` | 96 | `skipif` | Legacy EventBus.publish(event_type, payload) API not available in this build. | `temporary-bug-skip` | `fix` | `US-092` |
| `tests/test_event_bus.py` | 123 | `skipif` | Newer EventBus.publish(Event) API not available in this build. | `temporary-bug-skip` | `fix` | `US-092` |
| `tests/test_event_bus.py` | 137 | `skipif` | Newer EventBus.publish(Event) API not available in this build. | `temporary-bug-skip` | `fix` | `US-092` |
| `tests/test_event_bus.py` | 151 | `skipif` | Newer EventBus.publish(Event) API not available in this build. | `temporary-bug-skip` | `fix` | `US-092` |
| `tests/test_event_bus.py` | 161 | `skipif` | Newer EventBus.publish(Event) API not available in this build. | `temporary-bug-skip` | `fix` | `US-092` |
| `tests/test_event_bus.py` | 173 | `skipif` | Newer EventBus.publish(Event) API not available in this build. | `temporary-bug-skip` | `fix` | `US-092` |
| `tests/test_event_bus.py` | 189 | `skipif` | Newer EventBus.publish(Event) API not available in this build. | `temporary-bug-skip` | `fix` | `US-092` |
| `tests/test_event_bus.py` | 210 | `skipif` | Newer EventBus.publish(Event) API not available in this build. | `temporary-bug-skip` | `fix` | `US-092` |
| `tests/test_event_bus.py` | 233 | `skipif` | Newer EventBus.publish(Event) API not available in this build. | `temporary-bug-skip` | `fix` | `US-092` |
| `tests/test_event_bus.py` | 259 | `skipif` | Newer EventBus.publish(Event) API not available in this build. | `temporary-bug-skip` | `fix` | `US-092` |
| `tests/test_event_bus.py` | 274 | `skipif` | Legacy EventBus.publish(event_type, payload) API not available in this build. | `temporary-bug-skip` | `fix` | `US-092` |
| `tests/test_event_bus.py` | 293 | `skipif` | Newer EventBus.publish(Event) API not available in this build. | `temporary-bug-skip` | `fix` | `US-092` |
| `tests/background/test_supervisor.py` | 546 | `skipif` | Windows Job Object contract | `platform-skip` | `keep` | permanent: Windows process-containment contract |
| `tests/background/test_supervisor.py` | 681 | `skipif` | Windows Job Object contract | `platform-skip` | `keep` | permanent: Windows process-containment contract |
| `tests/test_install_scripts.py` | 15 | `pytest.skip` | bash not available | `optional-dep-skip` | `keep` | permanent: optional dependency/tool/environment guard |
| `tests/test_install_scripts.py` | 24 | `pytest.skip` | bash not usable on Windows | `platform-skip` | `keep` | permanent: platform/runtime-specific guard |
| `tests/test_openclaw_root_voice_loop_flag.py` | 19 | `skipif` | openwakeword is not installed | `optional-dep-skip` | `keep` | permanent: optional dependency/tool/environment guard |
| `tests/test_openclaw_root_voice_loop_text_mode.py` | 20 | `skipif` | openwakeword is not installed | `optional-dep-skip` | `keep` | permanent: optional dependency/tool/environment guard |
| `tests/test_scheduler.py` | 90 | `skipif` | Legacy Scheduler(storage_path, now_func) API not available in this build. | `temporary-bug-skip` | `fix` | `US-091` |
| `tests/test_scheduler.py` | 115 | `skipif` | Legacy Scheduler(storage_path, now_func) API not available in this build. | `temporary-bug-skip` | `fix` | `US-091` |
| `tests/test_scheduler.py` | 157 | `skipif` | Newer Scheduler(jobs_file=...) API not available in this build. | `temporary-bug-skip` | `fix` | `US-091` |
| `tests/test_scheduler.py` | 167 | `skipif` | Newer Scheduler(jobs_file=...) API not available in this build. | `temporary-bug-skip` | `fix` | `US-091` |
| `tests/test_scheduler.py` | 191 | `skipif` | Newer Scheduler(jobs_file=...) API not available in this build. | `temporary-bug-skip` | `fix` | `US-091` |
| `tests/test_scheduler.py` | 211 | `skipif` | Newer Scheduler(jobs_file=...) API not available in this build. | `temporary-bug-skip` | `fix` | `US-091` |
| `tests/test_scheduler.py` | 225 | `skipif` | Newer Scheduler(jobs_file=...) API not available in this build. | `temporary-bug-skip` | `fix` | `US-091` |
| `tests/test_scheduler.py` | 241 | `skipif` | Newer Scheduler(jobs_file=...) API not available in this build. | `temporary-bug-skip` | `fix` | `US-091` |
| `tests/test_scheduler.py` | 254 | `skipif` | Newer Scheduler(jobs_file=...) API not available in this build. | `temporary-bug-skip` | `fix` | `US-091` |
| `tests/test_scheduler.py` | 266 | `skipif` | Newer Scheduler(jobs_file=...) API not available in this build. | `temporary-bug-skip` | `fix` | `US-091` |
| `tests/test_scheduler.py` | 279 | `skipif` | Newer Scheduler(jobs_file=...) API not available in this build. | `temporary-bug-skip` | `fix` | `US-091` |
| `tests/test_scheduler.py` | 311 | `skipif` | Newer Scheduler(jobs_file=...) API not available in this build. | `temporary-bug-skip` | `fix` | `US-091` |
| `tests/test_scheduler.py` | 337 | `skipif` | Newer Scheduler(jobs_file=...) API not available in this build. | `temporary-bug-skip` | `fix` | `US-091` |
| `tests/test_scheduler.py` | 363 | `skipif` | Newer Scheduler(jobs_file=...) API not available in this build. | `temporary-bug-skip` | `fix` | `US-091` |
| `tests/test_scheduler.py` | 391 | `skipif` | Newer Scheduler(jobs_file=...) API not available in this build. | `temporary-bug-skip` | `fix` | `US-091` |
| `tests/test_scheduler.py` | 407 | `skipif` | Newer Scheduler(jobs_file=...) API not available in this build. | `temporary-bug-skip` | `fix` | `US-091` |
| `tests/test_scheduler.py` | 427 | `skipif` | Newer Scheduler(jobs_file=...) API not available in this build. | `temporary-bug-skip` | `fix` | `US-091` |
| `tests/test_scheduler.py` | 450 | `skipif` | Newer Scheduler(jobs_file=...) API not available in this build. | `temporary-bug-skip` | `fix` | `US-091` |
| `tests/test_scheduler.py` | 463 | `skipif` | Newer Scheduler(jobs_file=...) API not available in this build. | `temporary-bug-skip` | `fix` | `US-091` |
| `tests/test_scheduler.py` | 493 | `skipif` | Newer Scheduler(jobs_file=...) API not available in this build. | `temporary-bug-skip` | `fix` | `US-091` |
| `tests/test_scheduler.py` | 502 | `skipif` | Newer Scheduler(jobs_file=...) API not available in this build. | `temporary-bug-skip` | `fix` | `US-091` |
| `tests/test_scheduler.py` | 521 | `skipif` | Newer Scheduler(jobs_file=...) API not available in this build. | `temporary-bug-skip` | `fix` | `US-091` |
| `tests/test_skill_loader.py` | 213 | `pytest.skip` | example_weather_skill.py not found | `temporary-bug-skip` | `fix` | `US-093` |
| `tests/test_transformers_shim.py` | 30 | `pytest.skip` | f"transformers not installed or BeamSearchScorer unavailable: {e}" | `optional-dep-skip` | `keep` | permanent: optional dependency/tool/environment guard |
| `tests/test_transformers_shim.py` | 48 | `pytest.skip` | transformers not installed | `optional-dep-skip` | `keep` | permanent: optional dependency/tool/environment guard |
| `tests/test_transformers_shim.py` | 63 | `pytest.skip` | transformers not installed | `optional-dep-skip` | `keep` | permanent: optional dependency/tool/environment guard |
| `tests/test_us011_voice_deps.py` | 43 | `pytest.skip` | edge-tts not installed in this environment | `optional-dep-skip` | `keep` | permanent: optional dependency/tool/environment guard |
| `tests/test_us011_voice_deps.py` | 53 | `pytest.skip` | pyttsx3 not installed in this environment | `optional-dep-skip` | `keep` | permanent: optional dependency/tool/environment guard |
| `tests/test_us017_stt_backend.py` | 72 | `skipif` | numpy required for SpeechToText.transcribe | `optional-dep-skip` | `keep` | permanent: optional dependency/tool/environment guard |
| `tests/test_us027_device_approval.py` | 129 | `skipif` | rex.cli requires Python 3.11 | `platform-skip` | `keep` | permanent: platform/runtime-specific guard |
| `tests/test_us027_device_approval.py` | 161 | `skipif` | rex.cli requires Python 3.11 | `platform-skip` | `keep` | permanent: platform/runtime-specific guard |
| `tests/test_us027_device_approval.py` | 168 | `skipif` | rex.cli requires Python 3.11 | `platform-skip` | `keep` | permanent: platform/runtime-specific guard |
| `tests/test_us027_device_approval.py` | 180 | `skipif` | rex.cli requires Python 3.11 | `platform-skip` | `keep` | permanent: platform/runtime-specific guard |
| `tests/test_us027_device_approval.py` | 205 | `skipif` | rex.cli requires Python 3.11 | `platform-skip` | `keep` | permanent: platform/runtime-specific guard |
| `tests/test_us027_device_approval.py` | 230 | `skipif` | rex.cli requires Python 3.11 | `platform-skip` | `keep` | permanent: platform/runtime-specific guard |
| `tests/test_us030_clarification.py` | 184 | `pytest.skip` | HABridge not importable (requests not installed) | `optional-dep-skip` | `keep` | permanent: optional dependency/tool/environment guard |
| `tests/test_us030_clarification.py` | 204 | `pytest.skip` | HABridge not importable (requests not installed) | `optional-dep-skip` | `keep` | permanent: optional dependency/tool/environment guard |
| `tests/test_us033_acknowledgment.py` | 11 | `skipif` | numpy not installed | `optional-dep-skip` | `keep` | permanent: optional dependency/tool/environment guard |
| `tests/test_us034_progressive_response.py` | 22 | `skipif` | numpy not installed | `optional-dep-skip` | `keep` | permanent: optional dependency/tool/environment guard |
| `tests/test_us053_secret_management.py` | 125 | `pytest.skip` | .env.example not found | `temporary-bug-skip` | `fix` | `US-093` |
| `tests/test_us071_debug_mode.py` | 18 | `skipif` | rex.cli unavailable on this Python version | `platform-skip` | `keep` | permanent: platform/runtime-specific guard |
| `tests/test_us074_voice_loop_pipeline.py` | 33 | `skipif` | numpy not installed | `optional-dep-skip` | `keep` | permanent: optional dependency/tool/environment guard |
| `tests/test_us096_secret_scan.py` | 171 | `pytest.skip` | PyYAML not installed; skipping YAML parse test | `optional-dep-skip` | `keep` | permanent: optional dependency/tool/environment guard |
| `tests/test_us120_performance_baseline.py` | 180 | `pytest.skip` | docs/performance-baseline.md not yet created | `temporary-bug-skip` | `fix` | `US-093` |
| `tests/test_us120_performance_baseline.py` | 187 | `pytest.skip` | docs/performance-baseline.md not yet created | `temporary-bug-skip` | `fix` | `US-093` |
| `tests/test_us129_smoke.py` | 54 | `pytest.skip` | Local Rex instance not running — start with 'python flask_proxy.py' to exercise live-server smoke tests | `optional-dep-skip` | `keep` | permanent: optional dependency/tool/environment guard |
| `tests/test_us129_smoke.py` | 323 | `pytest.skip` | f"agent_server optional dependency not available: {exc}" | `optional-dep-skip` | `keep` | permanent: optional dependency/tool/environment guard |
| `tests/test_us139_install_scripts.py` | 139 | `skipif` | executable bit not relevant on Windows | `platform-skip` | `keep` | permanent: platform/runtime-specific guard |
| `tests/test_us140_full_extra.py` | 138 | `pytest.skip` | install.sh not present | `temporary-bug-skip` | `fix` | `US-093` |
| `tests/test_us140_full_extra.py` | 145 | `pytest.skip` | install.ps1 not present | `temporary-bug-skip` | `fix` | `US-093` |
| `tests/test_us304_chat_stream_electron_verification.py` | 67 | `skipif` | Electron binary or built main bundle not available | `optional-dep-skip` | `keep` | permanent: optional dependency/tool/environment guard |
| `tests/test_us304_chat_stream_electron_verification.py` | 36 | `pytest.skip` | f"Electron verification unavailable: {exc}" | `optional-dep-skip` | `keep` | permanent: optional dependency/tool/environment guard |
| `tests/test_voice_id_mvp.py` | 590 | `pytest.skip` | speechbrain is installed — cannot test missing-dep path | `optional-dep-skip` | `keep` | permanent: optional dependency/tool/environment guard |
| `tests/test_windows_service.py` | 10 | `pytest.skip` | rex.windows_service is Windows-only | `platform-skip` | `keep` | permanent: platform/runtime-specific guard |
