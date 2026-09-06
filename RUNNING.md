# How to Run AskRex Assistant

End users launch the installed AskRex Windows app; its managed runtime needs no machine Python or Node. Use an activated Python 3.11 environment for source/developer Python commands in this guide.

Windows:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

macOS / Linux:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
```

## Core Runtime Modes

| Mode | Command | Purpose |
|---|---|---|
| Text chat | `rex` or `python -m rex` | Interactive CLI chat |
| Diagnostics | `rex doctor` or `python -m rex doctor` | Environment, dependency, and config checks |
| Voice loop | `python rex_loop.py` | Developer-only source voice loop; defaults to Hold-to-Talk; `--mode wake-word` is beta opt-in |
| Python web dashboard | `rex-gui` | Developer-only browser/API surface at `http://127.0.0.1:8765/ui/` |
| Electron desktop GUI | Installed AskRex app or `cd gui && npm.cmd run dev` | Primary user-facing surface; source command is development-only |
| TTS API | `rex-speak-api` | Local speech synthesis service on port 5005 |
| OpenClaw tool server | `rex-tool-server` | Experimental, off by default; HTTP tool adapter service on port 18790 |
| Windows computer agent | `rex-agent` | Optional remote PC control agent |

## Background Rex Runtime (Windows, packaged)

On packaged Windows installs, Electron bootstraps a persistent, Electron-independent
background runtime (`rex.background`) after identity is resolved: a loopback-only Rex
Core process and a signed-in-user Voice Agent process, each supervised with bounded
restarts and content-free health. Closing or quitting the Electron window does not stop
an already-running background runtime; automatic startup after Windows reboot/sign-in is
registered via a single absolute-path Task Scheduler entry, with no manual terminal
command required. The Windows uninstaller requests a bounded orderly background stop and
removes that startup task before deleting the installed managed runtime.

Developer/operator inspection and control:

```powershell
python -m rex background status --runtime-root <ASKREX_RUNTIME_DIR>
python -m rex background stop --runtime-root <ASKREX_RUNTIME_DIR>
```

`status` prints machine-readable, content-free JSON (`ready`/`paused`/`degraded`/
`unavailable`/`failed`/`starting`/`stopped` per component); it never includes
transcripts, prompts, memory, credentials, or tool results. This lifecycle is proven by focused tests (`tests/background/`) and two installed-artifact checks: the real Electron-launched supervisor remains alive/queryable after the GUI exits, and a separate packaged `pythonw.exe -I` harness imports the installed `RuntimeSupervisor` and exercises ready/status/duplicate-start/orderly-stop mechanics with self-contained deterministic child fakes instead of a real LLM provider or physical microphone/speaker hardware. Wake-word activation itself remains
beta, and the full clean-install/reboot/screenless household-voice acceptance gate is
tracked separately by US-130 — it is not proven by this lifecycle evidence alone.

## Runtime data locations

Packaged Electron bridges write only beneath Electron's `userData` directory.
Shared state resolves to `data/household/`; private state resolves to
`data/users/<validated-user-id>/`. Developer source runs use the repository as
the runtime root unless `ASKREX_RUNTIME_DIR` is set. `REX_DATA_DIR` remains a
compatibility override for the exact shared-data root.

Use `python scripts/migrate_runtime_data.py --user <user-id>` for a dry-run of
legacy `data/` and `~/.rex` moves. Add `--apply` only after reviewing the plan.

The old Tkinter launchers are archived under `archived/tkinter_gui/` and are not supported.

## Text Chat

```bash
rex
# or
python -m rex
```

Useful related commands:

```bash
rex --help
rex version --verbose
rex tools -v
rex usage
```

## Voice Loop

```bash
python rex_loop.py
```

Optional flags:

```bash
python rex_loop.py --user james
python rex_loop.py --enable-plugin web_search
```

Voice mode requires the ML/audio dependency stack:

```bash
pip install -r requirements-cpu.txt
# or, for validated Windows CUDA 12.4:
pip install -r requirements-gpu-cu124.txt
```

In the Electron app, Hold to Talk is the supported path: record, STT, streamed response, TTS, selected-output playback, cancel/barge-in, replay, device-loss fallback, and repeated turns. Wake-word mode is beta until hardware reliability is proven.

## Python Web Dashboard

This developer-only surface is not required by the Electron app.

```bash
rex-gui
```

By default this starts a local Flask app on `127.0.0.1:8765` and opens:

```text
http://127.0.0.1:8765/ui/
```

Override the port:

```bash
REX_GUI_PORT=9000 rex-gui
```

PowerShell:

```powershell
$env:REX_GUI_PORT=9000; rex-gui
```

## Electron Desktop GUI

Development:

```powershell
cd gui
npm.cmd install
npm.cmd run dev
```

Build and preview:

```powershell
cd gui
npm.cmd run typecheck
npm.cmd run build
npm.cmd run preview
```

Development Electron resolves canonical bridge scripts from `bridge/`. Packaged
Windows Electron resolves those scripts from `resources/bridge/` and uses only
its managed `resources/python/python.exe`; it does not use machine Python or a
source checkout.

Build the managed Voice installer from `gui/` with `npm.cmd run dist`. The
Voice profile includes CPU Whisper/Torch and bundled FFmpeg, so the installer
is substantially larger than the Core runtime. CUDA/GPU acceleration and XTTS
voice cloning remain optional external profiles rather than installer claims.

For Electron-only verification harnesses, run `npm.cmd run build` in `gui/` before using `gui/tmp_verify_*.cjs` so `gui/dist-electron/main/index.js` matches the TypeScript sources.

## TTS API

```bash
set REX_SPEAK_API_KEY=example-key  # pragma: allowlist secret
rex-speak-api
```

PowerShell:

```powershell
$env:REX_SPEAK_API_KEY="example-key  # pragma: allowlist secret"
rex-speak-api
```

Default URL:

```text
http://127.0.0.1:5005
```

Example request:

```bash
curl -X POST http://127.0.0.1:5005/speak \
  -H "Content-Type: application/json" \
  -H "X-API-Key: example-key  # pragma: allowlist secret" \
  -d '{"text":"Hello from AskRex","user":"default"}' \
  --output speech.wav
```

## OpenClaw Tool Server

```bash
set REX_TOOL_API_KEY=example-key  # pragma: allowlist secret
rex-tool-server
```

Default URL:

```text
http://127.0.0.1:18790
```

Health check:

```bash
curl http://127.0.0.1:18790/health/live
```

Example tool call:

```bash
curl -X POST http://127.0.0.1:18790/rex/tools/time_now \
  -H "Content-Type: application/json" \
  -H "X-API-Key: example-key  # pragma: allowlist secret" \
  -d '{"args":{"location":"Dallas, TX"},"context":{}}'
```

## Diagnostics

```bash
python -m rex doctor
```

The doctor command checks Python version, package importability, config files, API keys, audio devices, FFmpeg, LLM service reachability, wake word config, STT/TTS dependencies, and GPU availability. Use `rex doctor --release-gate` for actionable release blockers and `rex integrations` for the evidence-based integration inventory. Credentials alone are reported as configured, not connected or authenticated.

## Common Problems

If `python -m rex` reports unsupported Python 3.12+, activate the `.venv` or call Python 3.11 explicitly:

```powershell
py -3.11 -m rex doctor
```

If wake-word or voice features fail, install the CPU/GPU ML requirements and confirm FFmpeg is available:

```bash
pip install -r requirements-cpu.txt
ffmpeg -version
python -m rex doctor
```

If `rex-gui` opens a placeholder page, build the web UI under `rex/ui/` or use the Electron GUI under `gui/`.
