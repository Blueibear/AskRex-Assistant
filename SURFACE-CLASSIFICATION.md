# Surface Classification

This document classifies every runtime entry point and UI surface in the AskRex repository.
It is the authoritative reference for packaging, CI, docs, and support scope decisions.

Classifications are assigned at story US-REM-018. Any change to a classification must be made
here first, then propagated to docs, packaging config, and CI.

---

## Classification Definitions

| Classification | Meaning |
|----------------|---------|
| `shippable` | Supported, user-facing surface included in or required by a release artifact. Security fixes and regressions are P0. |
| `developer-only` | Supported for developer and operator use but not exposed to end users in the packaged app. Not in the packaged Electron installer. |
| `experimental` | Optional pre-release surface. Disabled by default and not part of the supported end-user contract until explicit promotion criteria pass. |
| `deprecated` | Still present for backward compatibility. Emits `DeprecationWarning` on use. Will be removed in a future release. No new features. Security fixes only. |
| `archived` | Not maintained. Entry points removed. Kept in `archived/` for reference. May be permanently deleted in a future major version. |
| `removed` | Deleted from the repo. Listed here as a historical record only. |

---

## Entry Points (`pyproject.toml [project.scripts]`)

| Entry Point | Module | Classification | Notes |
|-------------|--------|----------------|-------|
| `rex` | `rex.cli:main` | `shippable` | Primary CLI; first-class user-facing command. Text mode. |
| `rex-gui` | `rex.gui_app:main` | `developer-only` | Flask web dashboard / API server. NOT spawned by the packaged Electron app (US-REM-019 audit). All core Electron GUI functionality uses IPC bridge scripts. Renderer fetch('/api/...') calls are dead in packaged mode (file:// protocol). Run separately for developer/operator web dashboard access. |
| `rex-config` | `rex.config:cli` | `developer-only` | Config inspection and migration utility. Operator/developer use only. |
| `rex-speak-api` | `rex_speak_api:main` | `developer-only` | Standalone TTS API with auth and rate limiting. Backend service; not user-facing. |
| `rex-agent` | `rex.computers.agent_server:main` | `developer-only` | Optional remote PC control API. Not enabled by default; requires explicit configuration. |
| `rex-tool-server` | `rex.openclaw.tool_server:main` | `experimental` | OpenClaw tool adapter backend at `/rex/tools/{tool_name}`. Disabled by default; requires explicit gateway configuration and is not part of the supported end-user contract. |

The mobile API gateway is a CLI subcommand, not a `[project.scripts]` console script:

| Entry Point | Module | Classification | Notes |
|-------------|--------|----------------|-------|
| `python -m rex mobile-api` | `rex.mobile_api` | `developer-only` | Authenticated mobile API gateway for the companion AskRex mobile client (issue #323). Localhost by default, JWT-authenticated (`REX_JWT_SECRET`, fails closed), rate-limited. Backend service run by an operator; not part of the packaged Electron installer. Physical-device/LAN validation is outstanding, so the mobile surface itself is pre-release. |
| `rex background start\|status\|stop` | `rex.commands.background` -> `rex.background.cli` | `developer-only` | Operator/developer wrapper over the internal packaged background-runtime CLI (US-124). Ordinary end users never invoke this directly; Electron and the registered Windows Task Scheduler task invoke `python -m rex.background.cli` with absolute packaged paths instead. `status` is content-free JSON only. |
| `python -m rex.background.cli supervisor\|core\|voice-agent\|status\|stop\|install-startup\|remove-startup` | `rex.background.cli` | `developer-only` | Not a documented end-user command. Invoked only by packaged Electron (`gui/src/main/backgroundRuntime.ts`) and the per-user Windows Task Scheduler startup task it registers, always with absolute managed-runtime paths. Owns the Electron-independent Core + Voice Agent supervisor lifecycle; see `rex.background` in `CLAUDE.md`. |

---

## UI Surfaces

| Surface | Classification | Notes |
|---------|----------------|-------|
| `gui/` (React + Electron desktop app) | `shippable` | Primary user-facing release artifact. The supported install path for end users. |

Capability presence is tracked separately from surface classification. See [docs/CAPABILITY-PARITY.md](docs/CAPABILITY-PARITY.md) for whether each backend/docs capability is visible, configurable, disabled with explanation, developer-only, or missing in the Electron product, plus the owning follow-up story for each gap.
| `rex/ui/` (Vite/React developer dashboard) | `developer-only` | Confirmed developer-only by `package.json` description: "Developer-only surface. Not included in packaged Electron app." Served by `rex-gui` at `/ui/`. Not bundled in the packaged Electron installer. |

---

## Root-level Scripts and Entry-point Files

| File | Classification | Notes |
|------|----------------|-------|
| `rex_loop.py` | `developer-only` | Source voice-loop entry point. Defaults to manual Hold-to-Talk activation; `--mode wake-word` is the explicit beta opt-in. The packaged Electron Hold-to-Talk path is the supported end-user voice surface. |
| Wake-word voice activation | `beta` / `developer-only` | Available from `rex_loop.py --mode wake-word`. US-046 controlled acoustic evidence records precision `0.800` and recall `1.000`, so the `0.90`/`0.90` promotion threshold fails; see `docs/voice/wakeword-report.md`. |
| `rex_speak_api.py` | `developer-only` | Same function as `rex-speak-api` entry point. Root-level script kept for direct invocation (`python rex_speak_api.py`). |
| `wsgi.py` | `developer-only` | WSGI deployment entry point for `rex-gui`. Operator use for production deployments (e.g., gunicorn). |
| `sitecustomize.py` | `developer-only` | Windows UTF-8 encoding fix; applied automatically at interpreter start. Not a user-facing entry point. |
| `conftest.py` | `developer-only` | pytest root conftest with shared fixtures. Test infrastructure only. |
| `setup.py` | `deprecated` | Legacy setuptools stub. Packaging is handled by `pyproject.toml`. Present only for legacy tool compatibility. |
| `voice_loop.py` | `deprecated` | Root-level re-export shim for `AsyncRexAssistant` backward compatibility. Emits `DeprecationWarning` on import. Canonical implementation: `rex.voice_loop`. Scheduled for removal — see US-REM-020. |
| `llm_client.py` | `deprecated` | Root-level re-export shim for `rex.llm_client`. Emits `DeprecationWarning` on import. Canonical implementation: `rex.llm_client`. Scheduled for removal — see US-REM-020. |
| `config.py` | `deprecated` | Root-level re-export shim for `rex.config`. Emits `DeprecationWarning` on import. Canonical implementation: `rex.config`. Scheduled for removal — see US-REM-020. |
| `flask_proxy.py` | `deprecated` | Legacy Flask API and proxy application. Still present at repo root but not an entry point in `pyproject.toml`. Canonical replacement: `rex-gui` (`rex.gui_app:main`). An archived copy lives at `archived/compat_shims/flask_proxy.py`. Scheduled for removal in a future release. |
| `rex_chat_bridge.py` | `developer-only` | Compatibility wrapper — executes `bridge/rex_chat_bridge.py` in its namespace to preserve patchable helpers for test imports. Electron resolves bridge scripts from `bridge/` directly; this root wrapper is not used by the Electron app. Canonical implementation: `bridge/rex_chat_bridge.py`. |
| `rex_chat_stream_bridge.py` | `developer-only` | Compatibility wrapper — executes `bridge/rex_chat_stream_bridge.py`. Canonical implementation: `bridge/rex_chat_stream_bridge.py`. |
| `rex_file_extract_bridge.py` | `developer-only` | Compatibility wrapper — executes `bridge/rex_file_extract_bridge.py`. Canonical implementation: `bridge/rex_file_extract_bridge.py`. |
| `rex_memories_bridge.py` | `developer-only` | Compatibility wrapper — executes `bridge/rex_memories_bridge.py`. Canonical implementation: `bridge/rex_memories_bridge.py`. |
| `rex_reminders_bridge.py` | `developer-only` | Compatibility wrapper — executes `bridge/rex_reminders_bridge.py`. Canonical implementation: `bridge/rex_reminders_bridge.py`. |
| `rex_shopping_list_bridge.py` | `developer-only` | Compatibility wrapper — executes `bridge/rex_shopping_list_bridge.py`. Canonical implementation: `bridge/rex_shopping_list_bridge.py`. |
| `rex_speaker_bridge.py` | `developer-only` | Compatibility wrapper — executes `bridge/rex_speaker_bridge.py`. Canonical implementation: `bridge/rex_speaker_bridge.py`. |
| `rex_stt_bridge.py` | `developer-only` | Compatibility wrapper — executes `bridge/rex_stt_bridge.py`. Canonical implementation: `bridge/rex_stt_bridge.py`. |
| `rex_tasks_bridge.py` | `developer-only` | Compatibility wrapper — executes `bridge/rex_tasks_bridge.py`. Canonical implementation: `bridge/rex_tasks_bridge.py`. |
| `rex_voice_bridge.py` | `developer-only` | Compatibility wrapper — executes `bridge/rex_voice_bridge.py`. Canonical implementation: `bridge/rex_voice_bridge.py`. |
| `rex_voice_enrollment_bridge.py` | `developer-only` | Compatibility wrapper — executes `bridge/rex_voice_enrollment_bridge.py`. Canonical implementation: `bridge/rex_voice_enrollment_bridge.py`. |
| `rex_voice_sample_bridge.py` | `developer-only` | Compatibility wrapper — executes `bridge/rex_voice_sample_bridge.py`. Canonical implementation: `bridge/rex_voice_sample_bridge.py`. |
| `rex_voice_upload_bridge.py` | `developer-only` | Compatibility wrapper — executes `bridge/rex_voice_upload_bridge.py`. Canonical implementation: `bridge/rex_voice_upload_bridge.py`. |
| `rex_voices_bridge.py` | `developer-only` | Compatibility wrapper — executes `bridge/rex_voices_bridge.py`. Canonical implementation: `bridge/rex_voices_bridge.py`. |
| `rex_wakeword_list_bridge.py` | `developer-only` | Compatibility wrapper — executes `bridge/rex_wakeword_list_bridge.py`. Canonical implementation: `bridge/rex_wakeword_list_bridge.py`. |
| `rex_wakeword_sample_bridge.py` | `developer-only` | Compatibility wrapper — executes `bridge/rex_wakeword_sample_bridge.py`. Canonical implementation: `bridge/rex_wakeword_sample_bridge.py`. |
| `rex_wakeword_train_bridge.py` | `developer-only` | Compatibility wrapper — executes `bridge/rex_wakeword_train_bridge.py`. Canonical implementation: `bridge/rex_wakeword_train_bridge.py`. |

---

## Archived Content (`archived/`)

All content under `archived/` is classified as `archived`. None of these files are referenced by
any entry point, import, or startup path in the active codebase.

| Path | Original Purpose | Archived In | Replacement |
|------|-----------------|-------------|-------------|
| `archived/tkinter_gui/gui.py` | Tkinter desktop window (`AssistantGUI`) | US-006 | `gui/` (Electron app) |
| `archived/tkinter_gui/gui_settings_tab.py` | Tkinter settings panel widget | US-006 | `gui/` (Electron app) |
| `archived/tkinter_gui/run_gui.py` | Tkinter GUI launch script | US-006 | `gui/` (Electron app) |
| `archived/shopping_pwa/shopping_pwa.py` | Flask Blueprint for mobile shopping-list PWA | US-007 | Electron GUI shopping routes |
| `archived/shopping_pwa/test_sl004_shopping_pwa.py` | Test suite for shopping PWA surface | US-007 | N/A (archived with PWA) |
| `archived/compat_shims/patch_tts_torch_load.py` | One-shot PyTorch 2.6 TTS patch | US-020 | `rex/compat/transformers_shims.py` |
| `archived/compat_shims/patch_tts_transformers.py` | One-shot Transformers 4.38+ TTS patch | US-020 | `rex/compat/transformers_shims.py` |
| `archived/compat_shims/flask_proxy.py` | Legacy Flask API compatibility proxy | US-020 | `rex-gui` (`rex.gui_app:main`) |
| `archived/rex_assistant.py` | Legacy CLI entry point | US-021 | `rex.cli:main` |
| `archived/conversation_memory.py` | `ConversationMemory` class | US-021 | `rex.conversation_memory` |
| `archived/plugin_loader.py` | Dict-based plugin discovery | US-021 | `rex.plugin_loader` |
| `archived/audio_config.py` | Audio device CLI utilities | US-021 | `rex.audio_config` |
| `archived/memory_utils.py` | Re-export shim for `rex.memory_utils` | US-021 | `rex.memory_utils` |
| `archived/logging_utils.py` | Re-export shim for `rex.logging_utils` | US-023 | `rex.logging_utils` |
| `archived/assistant_errors.py` | Re-export shim for `rex.assistant_errors` | US-023 | `rex.assistant_errors` |

---

## Internal Python APIs (User-Facing Capabilities)

These modules implement user-facing capabilities invoked by the assistant pipeline. They are not entry points and are not user-invocable directly, but they affect user-visible behavior.

| Module | Classification | Notes |
|--------|----------------|-------|
| `rex.skills.trainer` (`SkillTrainer`) | `shippable` | Invoked by the assistant when a user says "teach yourself to…" or similar. Generates a Python skill scaffold in `plugins/skills/` and registers it in the skill registry. The generated script includes an honest stub that tells the user the skill is not yet implemented. Users can edit the generated file to add real behavior. |

---

## Package Distribution (pip / wheel)

| Artifact | Classification | Notes |
|----------|----------------|-------|
| `askrex-assistant` wheel (`pip install .` / `pip install askrex-assistant`) | `developer-only` | Installs the `rex` Python library, six console scripts, and canonical IPC bridge scripts. **Not** an end-user artifact. The Windows Electron build installs this wheel into its managed runtime; developers/operators use it directly for CLI and service workflows. |
| Locally built Docker image (`Dockerfile`) | `developer-only` | Operator/development smoke-test path only. Not an end-user artifact or supported production deployment. Uses `python -m rex doctor --healthcheck` for lightweight core-runtime liveness. |
| Windows Electron Voice installer | `shippable` | Primary end-user artifact. Bundles Electron, canonical bridges, managed Python 3.11, the AskRex wheel, CPU Whisper/Torch dependencies, and FFmpeg. Locally artifact-tested but currently unsigned; public release requires signing and blocking CI. |

---

## Summary Counts

| Classification | Count |
|----------------|-------|
| `shippable` | 2 |
| `developer-only` | 28 |
| `experimental` | 1 |
| `deprecated` | 5 |
| `archived` | 15 |
| `removed` | 0 |
| **Total** | **51** |

(Distribution artifacts are counted separately from the 51 entry points and UI surfaces. The Docker image and installer classifications do not change the total.)

---

## Change Log

| Date | Story | Change |
|------|-------|--------|
| 2026-06-01 | US-REM-018 | Initial classification of all surfaces |
| 2026-06-01 | US-REM-019 | rex-gui reclassified shippable → developer-only. Audit confirmed: packaged Electron app does not spawn rex-gui; IPC uses bridge scripts only; renderer /api/... calls are dead in packaged mode (file:// protocol). |
| 2026-06-07 | US-REM-025 | Added root-level flask_proxy.py as deprecated (count: deprecated 4→5, total 32→33). Updated docs to use archived (not deprecated) for gui.py/run_gui.py. Added developer-only labels across INSTRUCTION_MANUAL.md, ARCHITECTURE.md, COMMANDS_AND_ENTRYPOINTS.md, and API/deployment docs. |
| 2026-06-23 | US-013 | Added Package Distribution section classifying pip/wheel (askrex-assistant) as developer-only. |
| 2026-06-23 | US-017 | Classified all 17 root-level bridge compatibility wrappers as developer-only (count: developer-only 10→27, total 33→50). Updated CLAUDE.md root-file count from 9 to 27. No files moved to archived/ — all bridge wrappers are actively used for test-import compatibility. |
| 2026-06-24 | US-022 | Added Internal Python APIs section. Classified `rex.skills.trainer` (`SkillTrainer`) as shippable — invoked by assistant when user requests skill creation. |
| 2026-07-28 | #323 | Added `python -m rex mobile-api` (`rex.mobile_api`) as developer-only — the surface existed on master but was missing from this document (count: developer-only 28→29, total 50→51). |
| 2026-08-07 | US-041 | Classified the locally built Docker image as developer-only and documented the lightweight core-runtime healthcheck. |
