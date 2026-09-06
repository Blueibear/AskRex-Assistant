# AskRex Assistant Installation Guide

This guide covers the end-user artifact and source/developer install paths. The managed Windows artifact bundles Python 3.11; source workflows must use a Python 3.11 virtual environment.

## Install Audiences

Different audiences use different install methods. Read the paragraph for your use case before proceeding.

**End users (packaged Windows Electron installer):** The release artifact bundles a managed Python 3.11 runtime, the installed AskRex wheel, canonical bridge scripts, Whisper/Torch CPU dependencies, and FFmpeg. It does not require machine Python, Node.js, a source checkout, or a neighboring `.venv`. The locally validated build is currently unsigned; do not describe a public download as signed until release signing is configured and verified.

After the signed-in identity is resolved, the packaged Windows app registers one per-user ONLOGON Task Scheduler entry using the absolute managed pythonw.exe and runtime-root paths, then bootstraps the same background supervisor detached from the Electron window. Closing or quitting the UI does not stop that runtime. Uninstall requests a bounded orderly background stop and removes the AskRex startup task before deleting the managed runtime; installed-artifact automation verifies startup-task creation/removal as well as supervisor lifecycle and GUI-exit survival. Physical microphone/wake-word and clean reboot/sign-in acceptance remain the separate US-130 release gate.

**Developers and operators (`pip install .` from source):** `pip install .` installs:
- The `rex` Python package library
- All six console scripts (`rex`, `rex-gui`, `rex-config`, `rex-speak-api`, `rex-agent`, `rex-tool-server`)
- The IPC bridge scripts (`bridge/rex_*.py`) installed to `{sys.prefix}/bridge/` — these are the Python scripts the Electron app spawns at runtime
- The config example (`config/rex_config.example.json`) installed to `{sys.prefix}/config/`

This is the correct path for contributors, developers, and operators who need CLI text mode, the voice loop, TTS API, direct Python library access, or a developer Electron app (`npm run dev`). It does **not** provide the Electron desktop installer.

## System Requirements

| Component | Requirement |
|---|---|
| OS | Windows 10/11, macOS, or Linux |
| Python | Bundled for end users; 3.11 (`>=3.11,<3.12`) for source/developer workflows |
| Node.js/npm | Build/development only; not required by the installed app |
| Disk | Several GB if installing ML/TTS models |
| Audio | Microphone and speakers for voice mode |
| FFmpeg | Required for parts of the audio/TTS stack |
| GPU | Optional NVIDIA CUDA path via the GPU requirements files |

Python 3.12, 3.13, and 3.14 are not supported by the validated ML/TTS dependency path and are rejected by the app.

## Source / Developer Quick Start

End users run the packaged Windows installer. The following steps clone the repository and run the Electron app in development mode.

Windows PowerShell:

```powershell
git clone https://github.com/Blueibear/AskRex-Assistant.git
cd AskRex-Assistant

py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip setuptools wheel
pip install .

Copy-Item config\rex_config.example.json config\rex_config.json -ErrorAction SilentlyContinue
Copy-Item .env.example .env -ErrorAction SilentlyContinue

cd gui
npm.cmd install
npm.cmd run dev
```

macOS / Linux:

```bash
git clone https://github.com/Blueibear/AskRex-Assistant.git
cd AskRex-Assistant

python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
pip install .

cp -n config/rex_config.example.json config/rex_config.json
cp -n .env.example .env

cd gui
npm install
npm run dev
```

To verify the Python install separately:

```bash
python -m rex doctor
```

## Advanced / Developer: CLI and Python Direct Mode

For developers who want the CLI or direct Python access without the Electron app:

Windows PowerShell:

```powershell
python -m rex doctor
python -m rex
```

macOS / Linux:

```bash
python -m rex doctor
python -m rex
```

This starts the interactive CLI text chat. Activate the `.venv` first (see Quick Start steps above).

## Platform Install Scripts

The root directory contains two install scripts for the primary paths:

| OS | Script | Purpose |
|---|---|---|
| Windows | `install.ps1` | PowerShell post-pip setup (FFmpeg check, config copy) |
| macOS/Linux | `install.sh` | Bash post-pip setup (FFmpeg check, config copy) |

Additional install variants are in `scripts/install/`:
- `install_full.sh` — full-stack install including all optional dependencies
- `install_lean.sh` — minimal install for lightweight deployments
- `setup.sh` — system-level dependency setup (apt-get, espeak, ffmpeg)
- `install.py` — Python-based interactive installer

## Install Options

Base install:

```bash
pip install .
```

This installs the Flask/config/security/runtime dependencies plus lightweight TTS backends, but not the heavy ML/audio stack.

CPU ML/audio stack:

```bash
pip install -r requirements-cpu.txt
```

Validated Windows CUDA 12.4 stack:

```bash
pip install -r requirements-gpu-cu124.txt
```

Alternative CUDA 11.8 stack:

```bash
pip install -r requirements-gpu.txt
```

Editable development install:

```bash
pip install -e ".[dev,test]"
```

Full extra from package metadata:

```bash
pip install -e ".[full]"
```

Use the split requirements files for GPU installs because CUDA PyTorch wheels require the PyTorch extra index URL, which cannot be expressed reliably as a normal optional extra.

## Configuration Files

Create these files from the checked-in examples if they do not already exist:

```bash
cp config/rex_config.example.json config/rex_config.json
cp .env.example .env
```

On Windows:

```powershell
Copy-Item config\rex_config.example.json config\rex_config.json -ErrorAction SilentlyContinue
Copy-Item .env.example .env -ErrorAction SilentlyContinue
```

Use:

- `config/rex_config.json` for non-secret runtime settings: audio, wake word, models, profiles, integrations, UI flags, tool settings.
- `.env` for secrets: API keys, tokens, and shared service keys.
- `profiles/*.json` for profile capabilities and runtime overrides.

### Existing runtime data

AskRex now separates shared state under `data/household/` from private state
under `data/users/<user-id>/`. Before upgrading an existing installation,
preview the migration plan:

```bash
python scripts/migrate_runtime_data.py --user <user-id>
```

After reviewing every source and destination, apply it with `--apply`. The tool
creates adjacent backups, retains the original files, is safe to rerun, and
refuses to overwrite conflicting targets.

Useful commands:

```bash
rex-config show
rex-config migrate-legacy-env --dry-run
rex-config migrate-legacy-env
```

## Startup Modes

| Mode | Command | Notes |
|---|---|---|
| **Electron desktop app** | Packaged Windows installer; source development: `cd gui; npm.cmd run dev` | **Shippable installer; source command is development-only** - packaged end-user runtime needs no machine Python/Node; source development does |
| Text chat (CLI) | `rex` or `python -m rex` | Developer / advanced — default interactive CLI |
| Diagnostics | `rex doctor` | Environment and dependency checks |
| Voice loop | `python rex_loop.py` | **Developer-only** - defaults to Hold-to-Talk; `--mode wake-word` is beta opt-in |
| Flask API/dashboard | `rex-gui` | Developer-only compatibility surface; Electron does not spawn or require it |
| TTS API | `rex-speak-api` | Developer / advanced — requires `REX_SPEAK_API_KEY`; default port 5005 |
| OpenClaw tool server | `rex-tool-server` | **Experimental** - off by default; requires explicit gateway configuration and `REX_TOOL_API_KEY` for the Rex tool-server surface |
| Windows computer agent | `rex-agent` | Developer / advanced — optional remote PC control agent |

The Tkinter launchers are archived under `archived/tkinter_gui/` and are not supported runtime paths.

Override the Python web dashboard port:

```bash
REX_GUI_PORT=9000 rex-gui
```

PowerShell:

```powershell
$env:REX_GUI_PORT=9000; rex-gui
```

## Electron GUI Setup

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

Build the Windows Voice installer with `npm.cmd run dist`. `predist` first constructs the managed Python 3.11 runtime, installs the AskRex wheel and pinned Voice dependencies, then builds Electron. The produced installer is currently unsigned. `scripts/verify_electron_package_contents.py` and `scripts/test_installed_electron_artifact.ps1` are the artifact-level validation paths.

For Electron-only verification harnesses, build first so `gui/dist-electron/main/index.js` matches the TypeScript sources.

## Platform Notes

Linux:

```bash
sudo apt-get update
sudo apt-get install -y ffmpeg libsndfile1 libasound2-dev portaudio19-dev python3-dev python3-venv
```

macOS:

```bash
brew install ffmpeg portaudio python@3.11
```

Windows:

- Install Python 3.11 and use `py -3.11`.
- Install FFmpeg and ensure `ffmpeg.exe` is on `PATH`.
- Native Windows is supported; WSL2 is also usable for CLI/server flows.
- `simpleaudio` is optional and disabled on Windows because it has known build issues.

## Running Tests

```bash
pip install -e ".[dev,test]"
pytest
pytest -m "not slow and not audio and not gpu"
pytest --cov=rex --cov-report=html
```

Electron checks:

```powershell
cd gui
npm.cmd run typecheck
npm.cmd run build
```

## Troubleshooting

If `python -m rex ...` rejects your interpreter, confirm the active Python:

```bash
python --version
```

On Windows, prefer:

```powershell
py -3.11 -m rex doctor
```

Run diagnostics:

```bash
python -m rex doctor
```

Common fixes:

- Missing FFmpeg: install FFmpeg and reopen the terminal so `PATH` updates.
- Missing ML dependencies: install `requirements-cpu.txt` or the matching GPU requirements file.
- OpenAI/OpenRouter/Ollama/model failures: check `config/rex_config.json` model settings and the credential-vault status shown in Settings.
- TTS API startup failure: set `REX_SPEAK_API_KEY`.
- Tool server 401s: set `REX_TOOL_API_KEY`.

## Uninstall

For the packaged Windows app, uninstall **AskRex** from Windows Installed Apps. The artifact harness verifies uninstall removes the application files. User data is retained intentionally; delete it separately only after confirming it is no longer needed.

For a developer/operator wheel install:

```bash
pip uninstall askrex-assistant
```

Repository virtual environments and runtime-data directories are separate from the wheel uninstall. Remove only explicit paths you have reviewed and backed up; memories, profiles, transcripts, and logs may contain private user data.
