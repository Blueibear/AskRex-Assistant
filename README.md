# AskRex Assistant

<p align="center">
  <img src="assets/brand/primary-horizontal.png" alt="AskRex Assistant - local-first voice AI" width="400" />
</p>

<p align="center">
  <img src="https://github.com/Blueibear/AskRex-Assistant/actions/workflows/ci.yml/badge.svg" alt="CI status" />
  <img src="https://img.shields.io/badge/python-3.11-blue" alt="Python 3.11" />
  <img src="https://img.shields.io/badge/license-MIT-green" alt="License: MIT" />
  <a href="https://www.buymeacoffee.com/Blueibear" target="_blank"><img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" alt="Buy Me A Coffee" style="height: 28px !important;width: 120px !important;" ></a>
</p>

AskRex Assistant is a local-first AI assistant for text chat, voice interaction, desktop control surfaces, and credential-gated home/productivity integrations. It runs from the Python `rex` package, with optional local ML dependencies for Whisper, openWakeWord, and XTTS, plus optional cloud/local LLM routing through OpenAI-compatible providers and Ollama.

AskRex is a release-candidate implementation under validation, not a signed public production release. The Windows artifact has passed local clean-install, launch, managed-runtime, reinstall, and uninstall automation, but release signing, GitHub CI, and service/hardware checks listed below remain gates. Hold to Talk is the supported voice path; wake word is beta. See [INTEGRATIONS_STATUS.md](INTEGRATIONS_STATUS.md) for integration truth.

> **Advanced / Developer**: For CLI text mode, voice loop, GPU/CUDA setup, and backend service configuration, see the [Advanced / Developer](#advanced--developer) section below. For GPU/CUDA setup and additional install variants, see [docs/advanced-install.md](docs/advanced-install.md).

## Table of Contents

- [Getting Started](#getting-started)
- [Install](#install)
- [Current Status](#current-status)
- [Working Now](#working-now)
- [Known Limitations / External Verification](#known-limitations--external-verification)
- [Implemented Privacy Boundary](#implemented-privacy-boundary)
- [Main Entry Points](#main-entry-points)
- [Features](#features)
- [Requirements](#requirements)
- [Configuration](#configuration)
- [GUI Usage](#gui-usage)
- [Advanced / Developer](#advanced--developer)
- [Development](#development)
- [Documentation](#documentation)
- [Security](#security)

## Getting Started

The supported user-facing interface is the **packaged Windows Electron desktop app**. Its managed Voice runtime does not require machine Python, Node.js, a source checkout, or a neighboring virtual environment. The source setup below is for developers; Python 3.11 and Node.js/npm are required only to build or run from source.

Python 3.12 and newer are intentionally rejected by the current installers and runtime checks because the validated ML/TTS dependency path is Python 3.11-only.

1. Clone the repository.

   ```bash
   git clone https://github.com/Blueibear/AskRex-Assistant.git
   cd AskRex-Assistant
   ```

2. Run the install script for your platform.

   Windows PowerShell:

   ```powershell
   py -3.11 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install --upgrade pip setuptools wheel
   Copy-Item config\rex_config.example.json config\rex_config.json -ErrorAction SilentlyContinue
   .\install.ps1
   ```

   macOS/Linux shell:

   ```bash
   python3.11 -m venv .venv
   source .venv/bin/activate
   python -m pip install --upgrade pip setuptools wheel
   cp -n config/rex_config.example.json config/rex_config.json
   bash install.sh
   ```

3. Configure LM Studio for local chat completions.

   In LM Studio, load a chat model, start the local OpenAI-compatible server, and confirm the server URL is `http://localhost:1234/v1`. AskRex expects the LM Studio server to be reachable on `localhost:1234` when you choose that local provider path.

4. Run Rex in the Electron desktop app.

   Windows PowerShell:

   ```powershell
   cd gui
   npm.cmd install
   npm.cmd run dev
   ```

   macOS/Linux shell:

   ```bash
   cd gui
   npm install
   npm run dev
   ```

   The packaged Electron app communicates with Python via IPC bridge scripts. A Flask backend (`rex-gui`) is **not required** at runtime for end users — the Electron app is IPC-only. Run `rex-gui` separately only for developer/operator web-dashboard access.

5. Verify it works.

   ```bash
   rex doctor
   ```

   Then send a short chat message in the Electron app and confirm Rex replies.

## Install

This section states which install method serves which audience. See [INSTALL.md](INSTALL.md) for detailed per-audience instructions.

**End users — packaged Windows Electron installer:** The built release artifact contains its own managed Python 3.11 Voice runtime, installed AskRex wheel, bridge scripts, and FFmpeg. It does not require a separate `pip install`, Node.js, or machine Python. The current local release candidate is unsigned; public release signing remains an external release requirement.

**Developers and operators — `pip install .` from source:** `pip install .` installs the Python library (`rex` package), all six console scripts (`rex`, `rex-gui`, `rex-config`, `rex-speak-api`, `rex-agent`, `rex-tool-server`), the IPC bridge scripts (`bridge/rex_*.py` → `{sys.prefix}/bridge/`) that the Electron app spawns at runtime, and the config example (`config/rex_config.example.json` → `{sys.prefix}/config/`). This is the correct install path for developers, contributors, and operators who need CLI access, voice loop, TTS API, or direct Python library access. It does **not** provide the Electron desktop installer. See the [Advanced / Developer](#advanced--developer) section and [INSTALL.md](INSTALL.md) for the full developer setup.

The `askrex-assistant` PyPI package, when published, targets the **developer/operator** audience. It is not an end-user install artifact.

## Current Status

This README reflects the current milestone after recent live testing and repair work. The Electron GUI is the current primary user-facing interface. The repo has usable CLI, chat, GUI, Home Assistant, and voice paths, with several areas still being stabilized.

## Capabilities & Status

This is the high-level release/status view. Surface classifications mirror [SURFACE-CLASSIFICATION.md](SURFACE-CLASSIFICATION.md); integration evidence states mirror [INTEGRATIONS_STATUS.md](INTEGRATIONS_STATUS.md). The complete UI inventory is [docs/UI_SURFACES.md](docs/UI_SURFACES.md), and the backend-to-Electron capability parity and migration inventory is [docs/CAPABILITY-PARITY.md](docs/CAPABILITY-PARITY.md). Lower-level compatibility wrappers remain enumerated only in the detailed surface inventory.

| Capability / surface | Release status | Current contract | Details |
|---|---|---|---|
| Electron desktop GUI | `shippable` | Primary packaged end-user interface | [UI surfaces](docs/UI_SURFACES.md) |
| Background Rex runtime (Windows) | implemented, packaged-artifact tested | Electron-independent Core + signed-in-user Voice Agent supervisor; ONLOGON startup and GUI-close survival are automated, while physical wake/mic/reboot acceptance remains US-130 | [Running](RUNNING.md#background-rex-runtime-windows-packaged) |
| CLI (`rex`) | `shippable` | Canonical text/command interface | [Architecture / CLI](docs/ARCHITECTURE.md#cli-command-tree) |
| Source voice loop / wake word | `developer-only` / beta wake word | Source runner is advanced/developer-only; packaged Electron Hold-to-Talk is the supported end-user voice path | [Voice pipeline](docs/voice_pipeline.md) |
| Flask/API (`rex-gui`) | `developer-only` | Local API plus incomplete experimental browser dashboard; not required by Electron | [UI surfaces](docs/UI_SURFACES.md) |
| Config CLI (`rex-config`) | `developer-only` | Operator/developer configuration utility | [Configuration](docs/configuration.md) |
| TTS API (`rex-speak-api`) | `developer-only` | Optional standalone authenticated TTS service | [API reference](docs/api.md) |
| Windows agent (`rex-agent`) | `developer-only` | Optional permission-gated remote computer-control service | [Computer control](docs/computers.md) |
| OpenClaw tool server / gateway | `experimental` | Off by default; health check can prove gateway reachability, not authentication or tool capability | [OpenClaw status](docs/openclaw-migration-status.md) |
| Mobile API backend | `developer-only` backend; mobile pre-release | Authenticated companion backend; physical-device/LAN release validation remains outstanding | [Mobile API setup](docs/mobile/MOBILE_API_SETUP_WINDOWS.md) |
| Docker image | `developer-only` | Developer/operator smoke and service path, not an end-user artifact | [Docker](docs/docker.md) |
| Home Assistant | Supported, credential-gated | Reads require live auth; writes use policy/confirmation and independent verification evidence | [Home Assistant](docs/home_assistant.md) |
| Timers & alarms | Locally verified; CI pending | First-class per-user concurrent timers and persistent one-shot/recurring alarms; restart recovery, snooze, pause/resume, DST-aware recurrence, canonical verified mutations, explicit output targets, and resolved-route explanation are implemented. | [Timer/alarm design](docs/superpowers/specs/2026-08-15-timers-alarms-media-routing.md) |
| Media / audio targets | Locally verified; CI pending | Canonical per-user targets cover Home Assistant media players, cached LAN speakers, rooms, request-origin preference, and persistent groups. HA playback supports verified play/pause/resume/stop/next/previous/volume/mute controls where state is readable. Persistent groups are addressable/configurable but advertise no direct playback controls until a verified group adapter exists. Apple Music is metadata/adapter contract only; physical-provider production verification remains outstanding. | [Media routing design](docs/superpowers/specs/2026-08-15-timers-alarms-media-routing.md) |
| Per-user output routing | Locally verified; CI pending | Electron Settings and the authenticated mobile settings backend share user-scoped spoken/timer/alarm/media defaults, conditional day/time rules, quiet hours, target volume, explicit fail/ask/named fallbacks, media-account selection, request-origin media preference, privacy-safe route auditing, speaker-group management, and supported test playback. Routing policy never grants target or cross-user media authority. | [Output routing design](docs/superpowers/specs/2026-08-15-timers-alarms-media-routing.md#us-122-output-routing-policies-and-settings-ui) |
| Situational context / privacy / proactivity | Locally verified; CI pending | Canonical per-user source policy, upload context/scope, opt-in location assistance, recipient-specific location sharing, bounded conversational references, and high-signal proactive opportunity evaluation are implemented. Electron Context & Privacy Settings and authenticated mobile settings use the same owner-bound service; admin/device/OpenClaw/self-maintenance status cannot widen another user's grants. | [Context/privacy design](docs/superpowers/specs/2026-08-16-situational-context-media-privacy-design.md) |
| Email | Partial | IMAP/SMTP paths exist; GUI sending remains draft-only | [Email](docs/email.md) |
| Calendar | Partial / read-only | ICS reads exist; provider writes/OAuth remain unavailable | [Calendar](docs/calendar.md) |
| SMS / Phone | Experimental | Twilio-backed capability; live delivery/calling remains externally verified | [Messaging](docs/messaging.md) |
| Legacy Tkinter / Shopping PWA | `archived` | Retained only under `archived/`; neither is a supported release surface | [UI surfaces](docs/UI_SURFACES.md) |

No surface is currently classified `removed`; historical/low-level entries remain in the canonical surface inventory.

## Working Now

- Core CLI help and doctor paths work: `rex --help`, `rex doctor`, and `python -m rex doctor`.
- Basic text chat works in the CLI and GUI.
- First-class timers and alarms work through the canonical chat/voice tool path: multiple per-user timers, pause/resume/adjust/rename/cancel, one-shot and recurring alarms, snooze/dismiss, DST-aware recurrence, restart recovery, explicit output destinations, and route explanation such as "where will my morning alarm play?".
- Canonical media routing resolves authorized device/room/group names without fuzzy guessing, prefers the trusted request-origin target when appropriate, refreshes dynamic target state without a Rex restart where supported, and routes mutations through verified lifecycle evidence. Home Assistant is the currently verified control adapter; LAN Sonos/Bose targets are discovery-visible but do not claim direct media controls through this adapter.
- Per-user output-routing policy is available in Electron Settings and the authenticated mobile settings backend. Spoken responses, timers, alarms, and media can use user-owned defaults, conditional rules, quiet hours, target volume, and explicit fail/ask/named fallback behavior; speaker-group editing and supported test playback reuse the canonical media target/group layer.
- Context & Privacy Settings now expose owner-bound connected-source context use, per-upload private/household scope and future-context inclusion, private `location_assist`, separate recipient-specific `location_share`, and proactive-assistance preference. Turning proactive assistance off short-circuits before Rex reads proactive calendar/memory/upload/location/current-info context.
- Bounded active-context references preserve natural follow-ups such as media/timekeeping “it”/“that one” resolution when unambiguous; expired or ambiguous references fail closed or clarify instead of guessing across users/domains.
- The Electron GUI launches and the main shell is stable.
- GUI pages load for Tasks, Reminders, Settings, Users, Integrations, Email, Calendar, and Home Assistant.
- The Home Assistant page loads and lists entities after the recent GUI/backend consistency fixes.
- The Home Assistant status stays authenticated in the UI after a successful live API test; saved credentials alone remain configured-only.
- GUI Chat shows a visible pending/thinking state while Rex is preparing a reply.
- Voice Hold to Talk records, transcribes, streams a Rex reply, synthesizes and plays it on the configured output device, supports cancel/barge-in and replay, and can be used repeatedly.
- Wake-word mode is wired but remains beta; it is not part of the release-verified voice contract. The controlled US-046 acoustic fixture measured precision `0.800` and recall `1.000`, below the `0.90`/`0.90` promotion threshold; see [the wake-word reliability report](docs/voice/wakeword-report.md).
- Custom wake backends are wired: built-in openWakeWord fallback, `custom_embedding` as an interim path, and `custom_onnx` as the long-term target when a real asset is present.
- Day/date phrasing coverage in chat has improved for common variants.

## Known Limitations / External Verification

- Wake-word mode is beta and still requires microphone/device reliability and latency validation.
- The repo does not ship a real `Hey Rex` custom wake asset. A valid asset is still required for `custom_onnx` or `custom_embedding` to become active.
- Outlook email and calendar are labeled unavailable because Graph OAuth is not implemented.
- Some pages and integrations are still in active stabilization; do not assume every registered integration is production-ready.
- The locally built Windows installer is unsigned. Signing remains a public-release gate.
- Microphone input, audible selected-output TTS, barge-in, live Home Assistant transitions, physical speaker/media playback paths, and provider delivery/write paths require environment-specific validation. Apple Music/MusicKit has no live credential or authorization verification in this repository.
- Proactive weather/traffic/search enrichment is adapter-driven and fail-closed: if a trustworthy current-info reader is not configured, Rex does not invent the missing fact or surface a candidate that requires it. The repository does not currently provide a production traffic reader, so commute-delay suggestions require an authorized current-info adapter before they can operate live.
- Personal location remains opt-in and demand-driven. Granting OS location permission or Rex `location_assist` does not start continuous GPS polling; mobile OS sensor/notification permissions should be requested just in time for an intentional feature use and remain separate from Rex-side contextual/disclosure grants.
- In the lean shared Python 3.11 developer environment, the full `not slow and not audio and not gpu` release matrix currently has one inherited wake-word reliability failure because optional `openwakeword` is not installed (9,325 passed, 65 skipped, 1 failed). The exact failure reproduces on master; it is not a US-123 regression or a claim that wake-word mode is release-ready.

## Implemented Privacy Boundary

Electron resolves one validated session identity in the main process. Chat, history, memories, tasks, reminders, shopping, email/calendar/SMS reads, voice identity, corrections, and document uploads receive that identity explicitly and fail closed when it is missing or invalid. Existing unowned Electron data is quarantined by `scripts/migrate_electron_data_ownership.py`; data is shared only when an owning store explicitly marks it shared. See [docs/security/ELECTRON_IDENTITY_AND_DATA.md](docs/security/ELECTRON_IDENTITY_AND_DATA.md).

Context/privacy mutations additionally pass through `ContextPrivacyService`, which requires the affected user/data owner at the service boundary. Electron session identity, mobile `settings.write`, household/admin status, OpenClaw, generated skills, and future self-maintenance/developer agents cannot widen another user's contextual-source, upload-audience, location-assistance, location-sharing, or proactive-assistance settings. Location assistance and person-specific location disclosure are deliberately separate grants; denied disclosure does not reveal whether current location data exists.

## Main Entry Points

| Surface | Command | Status |
|---|---|---|
| **Electron desktop app** | Installed AskRex app; source development: `cd gui; npm.cmd run dev` | **Shippable** primary user-facing interface; Windows artifact locally validated but unsigned |
| CLI text chat | `rex` or `python -m rex` | **Shippable** canonical CLI text interface |
| Diagnostics | `rex doctor` or `python -m rex doctor` | Working |
| Voice loop | `python rex_loop.py` | **Developer-only** source path; defaults to manual Hold-to-Talk. `--mode wake-word` opts into beta wake-word mode. |
| Python/Flask local API | `rex-gui` | **Developer-only** compatibility/API surface; not spawned or required by Electron |
| TTS API | `rex-speak-api` | **Developer-only** standalone authenticated TTS service |
| OpenClaw tool server | `rex-tool-server` | **Experimental**, off by default; gateway health test proves reachability only |
| Windows computer agent | `rex-agent` | **Developer-only** optional remote PC control service |
| Runtime config CLI | `rex-config` | **Developer-only** configuration/migration utility |

The Electron app under `gui/` is the current primary GUI. `rex-gui` remains useful as a local Flask/API service and for compatibility testing, but its browser dashboard at `/ui/` is incomplete and should not be treated as the main user interface. The legacy Tkinter launchers (`gui.py` and its entry point) are archived — moved to `archived/tkinter_gui/` and no longer maintained. See [SURFACE-CLASSIFICATION.md](SURFACE-CLASSIFICATION.md).

## Features

| Area | Current repo state |
|---|---|
| Text chat | Basic CLI and GUI chat work through `rex.assistant.Assistant` and the configured LLM provider. Common day/date phrasing coverage has improved; provider quality still depends on model/configuration. |
| Voice pipeline | Hold-to-Talk is the supported production path: the packaged Electron app provides real press/hold recording, Whisper STT, streamed response, TTS, selected-device playback, cancel/barge-in, replay, device-loss fallback, and repeated turns. The source `rex_loop.py` defaults to an explicit Enter-to-talk trigger and accepts `--mode wake-word` for the beta wake detector. |
| Custom wake support | Built-in openWakeWord remains the safe fallback path. `custom_embedding` is usable as an interim path, and `custom_onnx` is the target path for a real `Hey Rex` wake model. The repo does not ship that custom asset by default. |
| LLM providers | Local Transformers, OpenAI-compatible API settings, and Ollama routing are supported by config. Local model output quality varies by model and prompt path. |
| Configuration | Runtime settings live in `config/rex_config.json`; secrets live in the OS-backed credential vault; profiles live in `profiles/`. |
| GUIs | The Electron/React desktop GUI (`gui/`) is the current primary interface. The Electron shell is stable in current testing; Tasks, Reminders, Settings, Users, Integrations, Email, Calendar, and Home Assistant pages load. The Python/Flask `rex-gui` surface still serves local API routes and an experimental `/ui/` browser dashboard, but that browser UI is incomplete and not recommended as the primary interface. |
| Home Assistant | The GUI lists entities and routes mutations through one policy service. Sensitive lock/alarm/cover actions require action-bound confirmation, and Rex says an action is confirmed only after observing the requested state; otherwise it reports attempted-but-unverified, denied, or failed. Live-device verification remains environment-dependent. |
| Email and calendar integrations | Credential presence is labeled configured-only. Outlook Graph OAuth is unavailable. The GUI can display inbox/calendar bridge results and create email drafts, but GUI email sending is unavailable; copy the draft into a mail client. |
| CLI integrations | `rex email`, `rex calendar`, `rex msg`, `rex notify`, `rex gh`, `rex code`, `rex pc`, `rex wp`, `rex wc`, `rex ha`, `rex shopping`, `rex usage`, and more are registered. Readiness varies by backend credentials and test coverage. |
| Tool execution | Tool registry, policy checks, audit logging, and OpenClaw-facing HTTP tool server are implemented. Local tool execution currently covers time, weather, and web search; the HTTP tool server exposes a broader adapter set. |
| Memory and private data | Electron private stores are session-identity scoped and tested with two users. Shared data requires explicit shared ownership; legacy unowned data is quarantined for deliberate migration. |
| Notifications | Priority routing, quiet hours, digest/escalation logic, CLI commands, and Electron notification UI plumbing are present. Provider delivery remains externally verified. |
| WordPress/WooCommerce | WordPress health checks and WooCommerce order/product reads are implemented; WooCommerce order status and coupon writes are approval-gated. |
| OpenClaw | Optional experimental HTTP gateway/client adapters and a standalone Rex tool server are present; feature flags under `openclaw` control gateway-backed paths. URL/token are configuration evidence; the GUI health check can prove gateway reachability, while authentication and tool capability remain unproven. |

Home Assistant result wording mirrors the verification evidence. `attempted_unverified` uses **"I tried..."**, `completed` uses **"I asked HA to..."** without claiming state proof, `verified` alone may use **"Confirmed..."**, and a definite dispatch failure uses **"That failed because..."**. See [docs/home_assistant.md](docs/home_assistant.md) for the status and evidence contract.

## Requirements

| Component | Requirement |
|---|---|
| Python | Bundled for the Windows end-user artifact; 3.11 only for source/developer workflows |
| OS | Windows 10/11, macOS, or Linux |
| FFmpeg | Required for parts of the audio/TTS stack |
| Audio hardware | Microphone and speakers for voice mode |
| Node.js/npm | Build/development only; not required by the installed app |
| GPU | Optional NVIDIA CUDA path via `requirements-gpu*.txt` |

On Windows, use `py -3.11 ...` or activate the repo `.venv` before running commands. A system default `python` that points at 3.12+ will be rejected.

> **Note**: Source voice workflows may require optional audio dependencies. The packaged Voice runtime carries its validated CPU audio/ML stack; do not modify the managed runtime in place.

## Configuration

AskRex uses three configuration layers:

1. `config/rex_config.json` for non-secret runtime settings such as audio, wake word, models, UI, integrations, and feature flags.
2. The Windows DPAPI-backed credential vault for API keys, tokens, passwords, and authentication secrets. Config stores only contextual opaque references.
3. `profiles/<name>.json` for profile-level capabilities and runtime overrides.

The canonical wake word config key is `wakeword`. The legacy `wake_word` key is still migrated at runtime but logs a warning.

Custom wake asset defaults now follow:

- `config/wake_words/hey_rex/model.onnx` for `custom_onnx`
- `config/wake_words/hey_rex/embedding.pt` for `custom_embedding`

The repo wiring is in place for a real custom `Hey Rex` wake path, but a valid asset file is still required before that path becomes active.

Useful config commands:

```bash
rex-config show
rex-config migrate-legacy-env --dry-run
rex-config migrate-legacy-env
```

See [CONFIGURATION.md](CONFIGURATION.md), [docs/configuration.md](docs/configuration.md), and [docs/environment-variables.md](docs/environment-variables.md).

For Home Assistant, see [Home Assistant setup](docs/home-assistant-setup.md).

## GUI Usage

See [docs/usage.md](docs/usage.md) for the full usage guide including voice mode, autonomous workflows, and integrations.

### Electron desktop GUI - Windows setup

Use Windows PowerShell:

```powershell
cd gui
npm.cmd install
npm.cmd run dev

## Build and preview the compiled Electron app:
npm.cmd run build
npm.cmd run preview
```

### Electron desktop GUI - macOS setup

Use Terminal:

```bash
cd gui
npm install
npm run dev

npm run build
npm run preview
```

### Flask API backend (`rex-gui`) — developer-only

`rex-gui` starts the Flask server at `http://127.0.0.1:<gui_port>`. It is a **developer-only
backend service** — the packaged Electron app does **not** call it at runtime. All Electron GUI
functionality is backed by IPC bridge scripts (see Quick Start above and
[docs/UI_SURFACES.md](docs/UI_SURFACES.md)).

```bash
rex-gui
## Flask API listens on http://127.0.0.1:8765/ (or configured gui_port)
## Use only for API route testing or the experimental /ui/ browser dashboard
```

Use `rex-gui` only for operator/developer tasks: inspecting API routes, testing bridge scripts
outside Electron, or verifying the Flask layer independently. End users do not need to run it.

## Advanced / Developer

The following runtime paths are for developers, advanced users, and contributors. They are not the primary user-facing path.

### CLI Text Mode

After setting up the Python environment (see [Getting Started](#getting-started) step 2), run:

```bash
rex doctor
rex
```

Configure LM Studio for local model access at `localhost:1234`, or configure an OpenAI-compatible provider in `config/rex_config.json`.

For GPU/CUDA setup and additional install variants, see [docs/advanced-install.md](docs/advanced-install.md). Additional install scripts (`install_full.sh`, `install_lean.sh`, `setup.sh`) are in `scripts/install/`.

### Voice Loop

```bash
python rex_loop.py
```

Wake word → STT → LLM → TTS. Wake-word reliability still requires tuning.

### Common Commands

After activating the virtual environment:

```bash
rex --help
rex doctor
rex tools -v
rex usage
rex memory stats
rex kb search "query"
rex plan "check weather in Dallas"
rex approvals
rex wc orders list --site myshop
rex wc coupons create --site myshop --code SAVE10 --amount 10 --type percent
rex ha tts test --message "Hello from Rex"
```

### TTS API

The TTS API (`rex-speak-api`) requires an API key. Generate one with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Windows PowerShell:

```powershell
$env:REX_SPEAK_API_KEY = "<YOUR_API_KEY>"
$env:REX_ALLOW_PLAINTEXT_CREDENTIAL_FALLBACK = "1"
rex-speak-api  # explicit unpackaged legacy/operator mode; packaged builds reject this
```

macOS/Linux shell:

```bash
export REX_SPEAK_API_KEY=<YOUR_API_KEY>
export REX_ALLOW_PLAINTEXT_CREDENTIAL_FALLBACK=1
rex-speak-api  # explicit unpackaged legacy/operator mode; no production fallback
```

Request format with curl, using macOS/Linux shell syntax or Git Bash/WSL on Windows:

```bash
curl -X POST http://127.0.0.1:5005/speak \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <YOUR_API_KEY>" \
  -d '{"text":"Hello from AskRex","user":"default"}' \
  --output speech.wav
```

### OpenClaw / Rex Tool Server

The OpenClaw tool server (`rex-tool-server`) requires an API key. Generate one with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Windows PowerShell:

```powershell
$env:REX_TOOL_API_KEY = "<YOUR_API_KEY>"
$env:REX_ALLOW_PLAINTEXT_CREDENTIAL_FALLBACK = "1"
rex-tool-server  # explicit unpackaged legacy/operator mode; packaged builds reject this
```

macOS/Linux shell:

```bash
export REX_TOOL_API_KEY=<YOUR_API_KEY>
export REX_ALLOW_PLAINTEXT_CREDENTIAL_FALLBACK=1
rex-tool-server  # explicit unpackaged legacy/operator mode; no production fallback
```

Health endpoints with curl, using macOS/Linux shell syntax or Git Bash/WSL on Windows:

```bash
curl http://127.0.0.1:5005/health/live
curl http://127.0.0.1:18790/health/live
```

## Docker

Docker is a **developer-only** smoke-test/operator path, not an end-user or supported production deployment. The Windows Electron Voice installer is the supported end-user artifact. See [docs/docker.md](docs/docker.md) for the developer container workflow and its real liveness check.

## Memory

AskRex stores per-user voice and conversation profiles under `Memory/`. See [docs/memory.md](docs/memory.md) for profile format and voice cloning notes.

## Troubleshooting

Common issues: missing ffmpeg, CUDA driver mismatches, wake word not triggering. See [docs/troubleshooting.md](docs/troubleshooting.md) for solutions.

## Development

Python checks, after activating the virtual environment on any platform:

```bash
pip install -e ".[dev,test]"
pytest
ruff check .
black --check .
mypy .
```

Electron checks on Windows PowerShell:

```powershell
cd gui
npm.cmd run typecheck
npm.cmd run build
```

Electron checks on macOS/Linux:

```bash
cd gui
npm run typecheck
npm run build
```

The current coverage threshold in `pyproject.toml` is 75 percent. Test markers include `unit`, `integration`, `slow`, `audio`, `gpu`, `network`, `asyncio`, `anyio`, and `smoke`. The skipped-test inventory is maintained in [docs/testing/SKIPPED-TESTS-INVENTORY.md](docs/testing/SKIPPED-TESTS-INVENTORY.md).

Renderer IPC policy — raw `fetch('/api/...')` calls in `gui/src/` are blocked by CI:

- `scripts/check_no_renderer_api_fetch.py` — guard script (run via `python scripts/check_no_renderer_api_fetch.py`)
- `gui/src/ALLOWED_API_FETCHES.txt` — allowlist of exempted call sites; all renderer `/api/` fetches have been migrated to IPC (US-003 through US-010); the allowlist is now empty and any new raw fetch will fail CI

## Documentation

Start with [docs/INDEX.md](docs/INDEX.md). High-value active docs:

- [INSTALL.md](INSTALL.md) - installation guide
- [RUNNING.md](RUNNING.md) - runtime commands
- [docs/usage.md](docs/usage.md) - user-facing usage guide
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) - architecture overview
- [docs/UI_SURFACES.md](docs/UI_SURFACES.md) - current UI surface inventory
- [INTEGRATIONS_STATUS.md](INTEGRATIONS_STATUS.md) - canonical integration state and support contract
- [docs/audits/AUDIT-REMEDIATION-2026-07-22.md](docs/audits/AUDIT-REMEDIATION-2026-07-22.md) - current A–K remediation evidence ledger
- [docs/tools.md](docs/tools.md) - tool registry and execution notes
- [docs/api.md](docs/api.md) - HTTP services reference
- [docs/troubleshooting.md](docs/troubleshooting.md) - common failures

Files under `docs/archive/` are historical development records and may intentionally describe old plans or superseded states.

## Security

- Keep secrets in the OS-backed credential vault; do not put them in `.env`, JSON config, documentation, logs, or packaged resources. Plaintext environment reads exist only as an explicit unpackaged legacy/operator mode.
- `rex-speak-api` requires `REX_SPEAK_API_KEY`.
- `rex-tool-server` requires `REX_TOOL_API_KEY` for tool invocation.
- The GUI backend's log endpoints (`/api/logs/stream`, `/api/logs/download`) require a `REX_PROXY_TOKEN` Bearer token and redact home-directory paths from served log content. See [docs/configuration.md](docs/configuration.md).
- Approval-gated high-risk actions, such as WooCommerce writes and remote PC commands, require explicit confirmation flows.
- Canonical tools declare a risk class (`safe` / `sensitive` / `prohibited`) in the tool registry; `sensitive` tools return `confirmation_required` instead of executing until explicitly confirmed, and `prohibited` tools are always denied. Home Assistant mutations additionally use signed, single-use, action-bound confirmation tokens. See [docs/tools.md](docs/tools.md).

### Security baseline

- [docs/security/AUDIT-INVENTORY.md](docs/security/AUDIT-INVENTORY.md) - current `scripts/security_audit.py` triage inventory
- Release gate: `python scripts/security_audit.py --release-gate`

Security references:

- [docs/security/SECURITY_ADVISORY.md](docs/security/SECURITY_ADVISORY.md)
- [docs/security/SECURITY_AUDIT_2026-01-08.md](docs/security/SECURITY_AUDIT_2026-01-08.md)
- [docs/security/VULNERABILITY-SCAN.md](docs/security/VULNERABILITY-SCAN.md)
- [docs/security/SECRET-SCAN.md](docs/security/SECRET-SCAN.md)

## Contributing

Use short-lived branches from `master`, open PRs back to `master`, and follow Conventional Commits. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

Released under the MIT License. See [LICENSE](LICENSE).
