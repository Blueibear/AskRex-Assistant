# Dependency Management Guide

## Canonical Runtime Matrix

This table is the single authoritative release matrix. Supported profiles use
exact, tested package sets. A version change must update this table, the
profile regression, and the profile-specific install/import evidence together.

| Profile | torch | torchvision | torchaudio | Notes |
|-----------|----------------|-------|
| CPU / Docker / `[ml]` extra | `2.7.1` | `0.22.1` | `2.7.1` | Published CPU set; Python 3.11 only |
| CUDA 12.4 | `2.6.0+cu124` | `0.21.0+cu124` | `2.6.0+cu124` | Install through the PyTorch CUDA 12.4 index; do not install `[ml]` over it |
| Managed Electron Voice | `2.7.1` | `0.22.1` | `2.7.1` | Bundles the complete matching CPU set so packaged upgrades cannot resolve incompatible companions |

**Transformers policy | `>=5.5.0,<6.0`**: Python 3.11 only, with
`rex/compat/transformers_shims.py` preserving Coqui TTS 0.22.0's legacy
`BeamSearchScorer` import. `Pipfile.lock` resolves `5.5.0`. The release audit
has no ignore for CVE-2026-1839 or PYSEC-2025-217.

The managed-runtime builder imports the shipped voice stack and checks its
versions. The repository regression cross-checks these exact sets against the
requirements profiles, extras, Dockerfile, builder, and this table. Those
checks prevent policy drift, but are not compatibility evidence by themselves.

For each release candidate, create an isolated Python 3.11 environment from
the exact revision, install and resolve the selected profile, and retain the
command output from the matching runtime verifier:

```bash
python scripts/verify_dependency_profile.py --profile cpu
python scripts/verify_dependency_profile.py --profile cuda124
```

The verifier records the resolved `transformers`, `torch`, `torchvision`, and
`torchaudio` versions and exercises imports, the Coqui compatibility shim, a
Torch tensor operation, a torchvision transform, and torchaudio resampling.
It fails if the resolved distributions differ from the declared profile. The
CUDA verifier is dependency/runtime compatibility evidence only; it does not
claim GPU hardware, packaged Windows, or end-to-end model acceptance.

## Overview

AskRex Assistant splits dependencies by install target so CPU-only installs and CI do not pull CUDA/GPU wheels. Base installs come from `pyproject.toml`, while optional ML/audio stacks live in split requirements files.

## Files

- **`requirements.txt`**: Pointer file with guidance (not used for installs)
- **`requirements-cpu.txt`**: CPU-only ML + audio stack (no CUDA index)
- **`requirements-gpu-cu124.txt`**: CUDA 12.4 GPU stack (Windows RTX 3060)
- **`requirements-gpu.txt`**: CUDA 11.8 GPU stack (Linux)
- **`requirements-dev.txt`**: Dev tooling via extras
- **`requirements.in`**: Top-level dependencies with semver ranges (optional)
- **`Pipfile`**: Pipenv dependency specification for Dependabot scanning (core/web/API packages only — see note below)
- **`Pipfile.lock`**: Pipenv lockfile with all transitive dependencies (for Dependabot)

**Note:** `Pipfile.lock` enables GitHub Dependabot to automatically detect and propose updates for vulnerable dependencies.

## Pipfile / Pipfile.lock — Dependabot Scope

The `Pipfile` and `Pipfile.lock` exist **primarily so Dependabot can scan for vulnerabilities**. They must be lockable on a clean Linux system (no CUDA, no extra index URLs).

**What is in Pipfile:** Core web/API packages, pure-Python packages, and security-critical dependencies (flask, requests, cryptography, pillow, urllib3, etc.) that can be resolved from PyPI on stock Linux.

**What is NOT in Pipfile:** Heavy ML packages that prevent clean locking on Dependabot's environment:

| Package | Why excluded | Where it lives |
|---|---|---|
| `torch`, `torchvision`, `torchaudio` | Standard PyPI Linux wheel pulls in triton + all NVIDIA CUDA packages (~300 MB), making `pipenv lock` fail on clean systems | `requirements-cpu.txt`, `requirements-gpu*.txt` |
| `openai-whisper` | Depends on numba + triton, which require native toolchain (LLVM) and pull CUDA libs | `requirements-cpu.txt` |
| `openwakeword` | Depends on onnxruntime, which requires numpy ≥ 2.x, conflicting with numpy 1.x pin | `requirements-cpu.txt` |
| `TTS` (Coqui) | Depends on torch (see above), has many complex transitive deps, and is only validated here on Python 3.11 | `requirements-cpu.txt` |

> **Rule:** Any package that causes `pipenv lock` to fail on a clean Debian/Ubuntu Linux environment without CUDA must not be added to `Pipfile`. Keep those in the split requirements files instead.

## Updating Dependencies

### Manual Updates

1. **Edit the relevant requirements file** with new version numbers
2. **Test thoroughly:**
   ```bash
   pip install .
   pip install -r requirements-cpu.txt
   pip install -e ".[dev]"
   python -m compileall .
   pytest
   python -c "import rex; from rex.llm_client import *"
   ```
3. **Commit changes** with explanation of what was updated and why

### Security Updates

When GitHub Dependabot alerts you to vulnerabilities:

1. Check the **recommended version** in the Dependabot alert
2. Update the relevant requirements file to that version or higher
3. **Test for breaking changes** (see Testing section below)
4. Review security advisory to understand the risk
5. Commit with reference to CVE/GHSA ID

## Important Constraints

### PyTorch (CPU-only builds)

Rex uses CPU-only PyTorch builds for compatibility:

```bash
pip install -r requirements-cpu.txt
```

**Version constraints:**
- `torch`, `torchvision`, `torchaudio` must be compatible versions
- Check [PyTorch version compatibility](https://pytorch.org/get-started/previous-versions/)
- These packages are NOT in Pipfile because the standard PyPI Linux wheel includes CUDA dependencies that break `pipenv lock` on clean systems

### Transformers + TTS Compatibility

**Issue:** Hugging Face Transformers 4.38+ moved `BeamSearchScorer` from top-level exports to internal modules. Coqui TTS still expects it at top level.

**Solution:** Compatibility shim in `rex/compat/transformers_shims.py`

- Call `ensure_transformers_compatibility()` before importing libraries that require it
- Allows upgrading to transformers 4.48+ for security fixes
- No changes needed to TTS library code

**Testing the shim:**
```bash
python -c "from transformers import BeamSearchScorer; print('OK')"
```

## Testing After Updates

### Minimal Smoke Test
```bash
python -c "import rex; from rex.llm_client import *; print('Imports OK')"
```

### Full Test Suite
```bash
python -m compileall .  # Check all Python files compile
pytest                  # Run unit tests
rex-gui                 # Test Flask API / experimental browser dashboard starts
```

### Integration Test
1. Start the assistant
2. Test wake word detection
3. Test speech-to-text
4. Test LLM response generation
5. Test text-to-speech output

## Security Best Practices

1. **Subscribe to Dependabot alerts** in GitHub repository settings
2. **Review changelogs** before major version bumps
3. **Test breaking changes** in development environment first
4. **Pin transitive dependencies** if they cause issues
5. **Document compatibility constraints** in this file

## Known Compatibility Issues

### Resolved

✅ **transformers BeamSearchScorer** - Shim in `rex/compat/transformers_shims.py`

✅ **Dependabot lock failure** - Removed heavy ML packages (torch, openai-whisper, openwakeword, TTS) from Pipfile; they remain in requirements-cpu.txt and pyproject.toml [ml] extras.

### Active Constraints

- **torch**: Must use CPU builds (no CUDA). Not in Pipfile due to CUDA transitive deps on Linux PyPI.
- **TTS**: Requires transformers compatibility shim
- **Python**: Repository install paths are intentionally constrained to Python 3.11 until broader compatibility is validated end-to-end.
- **numpy**: Keep <2.0 in requirements-cpu.txt for compatibility with numba/TTS/other scientific packages
- **speechbrain / resemblyzer**: Voice identity optional extras only (`pip install '.[voice-id]'`). Not in Pipfile or default dependencies.

## Optional Extras Policy

Heavy ML and audio dependencies are split into optional extras in `pyproject.toml` so the base install and `Pipfile.lock` remain lightweight and lockable.

| Extra group | Packages | Use case |
|-------------|----------|----------|
| `audio` | numpy, sounddevice, soundfile, simpleaudio | Audio I/O for voice loop |
| `ml` | torch, torchvision, torchaudio, transformers, TTS, whisper, openwakeword | Full ML stack |
| `voice-id` | speechbrain, resemblyzer | Speaker recognition embeddings |
| `sms` | twilio | Twilio SMS backend |
| `dev` | pytest, pytest-cov, pytest-asyncio, build, black, ruff, mypy | Development tools |

**Install command for voice identity extras:**

```bash
pip install '.[voice-id]'
```

**Rules:**
- Pipfile and Pipfile.lock must remain lockable on clean Linux without CUDA or heavy ML packages.
- Heavy deps belong in optional extras groups only, never in default `[project.dependencies]` or `[packages]` in Pipfile.
- Runtime code must guard imports of optional packages and fall back cleanly when they are missing.

## Dependabot Configuration

GitHub Dependabot requires exact pins (`==`) to function. It will:
- Detect outdated dependencies
- Propose version bumps via pull requests
- Flag security vulnerabilities

**Pipfile / Pipfile.lock must remain lockable** on a clean Linux system without CUDA. Test with:

```bash
pip install pipenv
pipenv lock --clear
```

**To enable Dependabot:**
1. Ensure the split requirements files use exact pins where needed
2. Enable Dependabot in repository settings
3. Review and merge Dependabot PRs after testing

## Version History

| Date       | Transformers | Torch | TTS   | Notes                          |
|------------|--------------|-------|-------|--------------------------------|
| 2026-02-20 | 4.57.3 (Pipfile) | 2.6.0 (cpu only) | 0.22.0 (req files) | Remove ML packages from Pipfile to fix Dependabot lock; bump cryptography→44.0.2, pillow→11.2.1 |
| 2026-01-09 | 4.57.3       | 2.6.0 | 0.22.0| Pin CUDA 12.4, fix torchvision constraint |
| 2026-01-09 | 4.57.3       | 2.8.0 | 0.22.0| 15 CVEs fixed, Pipfile.lock added |
| 2026-01-08 | 4.57.3       | 2.9.1 | 0.22.0| Security fixes, shim added     |
| 2025-12-XX | 4.37.2       | 2.7.1 | 0.18.0| Pre-security audit             |

## Questions?

See [README_STABILIZATION.md](README_STABILIZATION.md) for configuration details or open an issue on GitHub.


## Using Pipenv (Optional)

Pipenv provides an alternative dependency management workflow with lockfile support. The `Pipfile.lock` is primarily used by GitHub Dependabot to track updates.

### Installation with Pipenv

```bash
# Install pipenv
pip install pipenv

# Install dependencies from Pipfile
pipenv install

# Install dev dependencies
pipenv install --dev

# Activate virtual environment
pipenv shell

# Run commands in pipenv environment
pipenv run rex-gui
```

### Updating Dependencies with Pipenv

```bash
# Update a specific package
pipenv update flask

# Update all packages
pipenv update

# Lock dependencies after manual edit to Pipfile
pipenv lock --clear

# Generate requirements from Pipfile.lock (core packages only)
pipenv requirements > /tmp/pipenv-core.txt
```

### Keeping Pipfile and split requirements in Sync

**Primary workflow:** Edit the split requirements files directly for dependency updates.

The Pipfile covers only the **core/web/API subset** of packages (no torch, openai-whisper, etc.). For the full ML stack, use `requirements-cpu.txt`.

```bash
# After editing Pipfile, regenerate the lockfile
pipenv lock --clear

# Run tests to verify nothing broke
pytest -q
```

**Note:** `requirements.txt` is a pointer file. Pipfile/Pipfile.lock exist primarily to enable Dependabot.
