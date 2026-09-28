"""Environment diagnostics for Rex AI Assistant.

This module provides the `rex doctor` command functionality, checking:
- Python version compatibility
- Configuration file presence and readability
- Required environment variables
- External dependencies (binaries on PATH)
- Basic security checks

Usage:
    from rex.doctor import run_diagnostics
    exit_code = run_diagnostics(verbose=True)
"""

from __future__ import annotations

import os
import shutil
import socket
import stat
import sys
from dataclasses import dataclass, field
from enum import Enum
from importlib.util import find_spec
from pathlib import Path

from rex.audio.speaker_discovery import get_speaker_discovery
from rex.compat.python_compat import (
    SUPPORTED_VERSION_LABEL,
    WINDOWS_GPU_INSTALL_LABEL,
    is_supported_python,
    unsupported_python_message,
)


class Status(Enum):
    """Status levels for diagnostic checks."""

    OK = "ok"
    WARNING = "warn"
    ERROR = "error"
    INFO = "info"


@dataclass
class CheckResult:
    """Result of a single diagnostic check."""

    name: str
    status: Status
    message: str
    details: str = ""
    release_blocking: bool = False


@dataclass
class DiagnosticsReport:
    """Collection of all diagnostic check results."""

    results: list[CheckResult] = field(default_factory=list)

    def add(self, result: CheckResult) -> None:
        """Add a check result to the report."""
        self.results.append(result)

    def has_errors(self) -> bool:
        """Return True if any check has error status."""
        return any(r.status == Status.ERROR for r in self.results)

    def has_warnings(self) -> bool:
        """Return True if any check has warning status."""
        return any(r.status == Status.WARNING for r in self.results)

    @property
    def error_count(self) -> int:
        """Count of checks with error status."""
        return sum(1 for r in self.results if r.status == Status.ERROR)

    @property
    def warning_count(self) -> int:
        """Count of checks with warning status."""
        return sum(1 for r in self.results if r.status == Status.WARNING)

    @property
    def release_blocker_count(self) -> int:
        """Count actionable warnings that block a release gate."""
        return sum(
            1
            for result in self.results
            if result.status == Status.WARNING and result.release_blocking
        )


def _status_symbol(status: Status) -> str:
    """Return a distinct, truthful label for each status level."""
    return {
        Status.OK: "[PASS]",
        Status.INFO: "[INFO]",
        Status.WARNING: "[WARN]",
        Status.ERROR: "[ERROR]",
    }[status]


def _find_project_root() -> Path | None:
    """Find the project root by looking for pyproject.toml or .git."""
    # Start from current working directory
    current = Path.cwd()

    # Walk up the directory tree
    for parent in [current] + list(current.parents):
        if (parent / "pyproject.toml").exists():
            return parent
        if (parent / ".git").exists():
            return parent
        if (parent / "config" / "rex_config.example.json").exists():
            return parent

    return None


def check_python_version() -> CheckResult:
    """Check if Python version is supported."""
    version = sys.version_info
    version_str = f"{version.major}.{version.minor}.{version.micro}"

    if not is_supported_python(version):
        return CheckResult(
            name="Python Version",
            status=Status.ERROR,
            message=f"Python {version_str} is not supported",
            details=(
                unsupported_python_message(version, install_target=WINDOWS_GPU_INSTALL_LABEL)
                + f" The supported runtime window is {SUPPORTED_VERSION_LABEL}."
            ),
        )
    else:
        return CheckResult(
            name="Python Version",
            status=Status.OK,
            message=f"Python {version_str} ({SUPPORTED_VERSION_LABEL})",
        )


def check_config_file(root: Path | None) -> CheckResult:
    """Check for presence and readability of rex_config.json."""
    if root is None:
        return CheckResult(
            name="Config File",
            status=Status.WARNING,
            message="Could not determine project root",
            details="Run rex doctor from the project directory or ensure pyproject.toml exists.",
        )

    config_path = root / "config" / "rex_config.json"
    example_path = root / "config" / "rex_config.example.json"

    if not config_path.exists():
        if example_path.exists():
            return CheckResult(
                name="Config File",
                status=Status.WARNING,
                message="rex_config.json not found (example exists)",
                details=f"Copy {example_path} to {config_path} and customize it.",
                release_blocking=True,
            )
        return CheckResult(
            name="Config File",
            status=Status.ERROR,
            message="rex_config.json not found",
            details=f"Expected config file at: {config_path}",
        )

    # Check readability
    try:
        with open(config_path) as f:
            import json

            json.load(f)
        return CheckResult(
            name="Config File",
            status=Status.OK,
            message=f"Found and readable: {config_path.name}",
        )
    except json.JSONDecodeError as e:
        return CheckResult(
            name="Config File",
            status=Status.ERROR,
            message="rex_config.json has invalid JSON",
            details=str(e),
        )
    except PermissionError:
        return CheckResult(
            name="Config File",
            status=Status.ERROR,
            message="Cannot read rex_config.json (permission denied)",
            details=f"Check file permissions on {config_path}",
        )


def check_env_file(root: Path | None) -> CheckResult:
    """Check for presence of .env file."""
    if root is None:
        return CheckResult(
            name="Environment File",
            status=Status.INFO,
            message="Could not determine project root",
        )

    env_path = root / ".env"
    example_path = root / ".env.example"

    if not env_path.exists():
        if example_path.exists():
            return CheckResult(
                name="Environment File",
                status=Status.WARNING,
                message=".env not found (example exists)",
                details=f"Copy {example_path} to {env_path} and add your API keys.",
            )
        return CheckResult(
            name="Environment File",
            status=Status.WARNING,
            message=".env file not found",
            details="Create a .env file with your API keys. See .env.example for template.",
        )

    return CheckResult(
        name="Environment File",
        status=Status.OK,
        message=f"Found: {env_path.name}",
    )


def check_credential_vault_backend() -> CheckResult:
    """Report whether this platform has a production OS-backed credential vault."""
    from rex.credential_vault import VaultUnavailableError, get_credential_vault

    try:
        vault = get_credential_vault()
    except VaultUnavailableError as exc:
        return CheckResult(
            name="Credential Vault",
            status=Status.WARNING,
            message="Production credential vault unavailable",
            details=str(exc),
        )

    backend_name = type(vault).__name__
    if sys.platform == "darwin":
        message = "macOS Keychain credential vault available"
    elif sys.platform == "win32":
        message = "Windows DPAPI credential vault available"
    else:
        message = f"OS-backed credential vault available ({backend_name})"
    return CheckResult(
        name="Credential Vault",
        status=Status.OK,
        message=message,
    )


def check_environment_variables() -> CheckResult:
    """Check for legacy plaintext API-key environment variables."""
    # List of common API key environment variables
    # We don't hardcode specific key names, just check common patterns
    api_key_patterns = [
        "OPENAI_API_KEY",
        "OLLAMA_API_KEY",
        "BRAVE_API_KEY",
        "SERPAPI_KEY",
        "GOOGLE_API_KEY",
        "HA_TOKEN",
    ]

    missing = []
    present = []

    for var in api_key_patterns:
        value = os.environ.get(var)
        if value and value.strip():
            present.append(var)
        else:
            missing.append(var)

    if not present:
        return CheckResult(
            name="Legacy API Key Environment",
            status=Status.INFO,
            message="No plaintext API-key environment variables configured",
            details=(
                "This is expected when credentials are stored in the OS-backed vault. "
                f"Legacy environment values absent: {', '.join(missing[:3])}..."
            ),
        )

    if missing:
        return CheckResult(
            name="API Keys",
            status=Status.INFO,
            message=f"{len(present)} API key(s) configured",
            details=f"Configured: {', '.join(present)}",
        )

    return CheckResult(
        name="API Keys",
        status=Status.OK,
        message=f"All {len(present)} common API keys configured",
    )


def check_binary(name: str, purpose: str) -> CheckResult:
    """Check if a binary is available on PATH."""
    path = shutil.which(name)
    if path:
        return CheckResult(
            name=f"Binary: {name}",
            status=Status.OK,
            message=f"Found: {path}",
        )
    return CheckResult(
        name=f"Binary: {name}",
        status=Status.WARNING,
        message=f"'{name}' not found on PATH",
        details=f"Required for: {purpose}. Install it or ensure it's in your PATH.",
    )


def check_external_dependencies() -> list[CheckResult]:
    """Check for external binary dependencies (git and other tools).

    FFmpeg has its own dedicated check (``check_ffmpeg_for_tts``) that reports
    whether FFmpeg is required based on the active TTS provider.
    """
    dependencies = [
        ("git", "version control"),
    ]

    results = []
    for name, purpose in dependencies:
        results.append(check_binary(name, purpose))

    return results


def check_config_permissions(root: Path | None) -> CheckResult:
    """Check for security issues with config file permissions."""
    if root is None:
        return CheckResult(
            name="Config Permissions",
            status=Status.INFO,
            message="Could not determine project root",
        )

    # Check .env file permissions (should not be world-readable)
    env_path = root / ".env"
    if env_path.exists():
        if os.name == "nt":
            return CheckResult(
                name="Config Permissions",
                status=Status.INFO,
                message="Windows ACL permissions require an explicit access review",
                details=(
                    f"Review access to {env_path} with 'icacls' and ensure only the "
                    "current user and trusted administrators can read it."
                ),
            )
        try:
            mode = env_path.stat().st_mode
            # Check if world-readable (others have read permission)
            if mode & stat.S_IROTH:
                return CheckResult(
                    name="Config Permissions",
                    status=Status.WARNING,
                    message=".env file is world-readable",
                    details=(
                        f"Consider restricting permissions: chmod 600 {env_path}\n"
                        "This file may contain sensitive API keys."
                    ),
                    release_blocking=True,
                )
        except OSError:
            pass

    # Check for secrets in config files
    config_path = root / "config" / "rex_config.json"
    if config_path.exists():
        try:
            content = config_path.read_text().lower()
            sensitive_patterns = ["api_key", "secret", "password", "token"]
            found_patterns = [p for p in sensitive_patterns if p in content]
            if found_patterns:
                # This is likely fine since config should have non-sensitive settings
                pass
        except OSError:
            pass

    return CheckResult(
        name="Config Permissions",
        status=Status.OK,
        message="Configuration permissions look reasonable",
    )


def check_package_installation() -> CheckResult:
    """Check if Rex is properly installed as a package."""
    try:
        import rex  # noqa: F401
        from rex.contracts import CONTRACT_VERSION

        return CheckResult(
            name="Package Installation",
            status=Status.OK,
            message=f"rex package installed (contracts v{CONTRACT_VERSION})",
        )
    except ImportError as e:
        return CheckResult(
            name="Package Installation",
            status=Status.WARNING,
            message="rex package not fully installed",
            details=f"Import error: {e}. Run 'pip install -e .' to install.",
        )


def check_core_dependencies() -> list[CheckResult]:
    """Check if core Python dependencies are installed."""
    results = []

    deps = [
        ("torch", "PyTorch (ML framework)"),
        ("transformers", "Transformers (NLP models)"),
        ("whisper", "Whisper (speech recognition)"),
        ("TTS", "Coqui TTS (text-to-speech)"),
        ("openwakeword", "OpenWakeWord (wake word detection)"),
        ("flask", "Flask (web framework)"),
        ("pydantic", "Pydantic (data validation)"),
        ("dotenv", "python-dotenv (environment loading)"),
    ]

    for module, description in deps:
        try:
            __import__(module)
            results.append(
                CheckResult(
                    name=f"Dependency: {module}",
                    status=Status.OK,
                    message=f"Installed: {description}",
                )
            )
        except ImportError:
            results.append(
                CheckResult(
                    name=f"Dependency: {module}",
                    status=Status.WARNING,
                    message=f"Not installed: {description}",
                    details=f"Run 'pip install {module}' or 'pip install -e .' to install.",
                )
            )

    return results


def check_gpu_availability() -> CheckResult:
    """Check if GPU is available for ML inference."""
    try:
        import torch

        if torch.cuda.is_available():
            device_count = torch.cuda.device_count()
            device_name = torch.cuda.get_device_name(0) if device_count > 0 else "Unknown"
            return CheckResult(
                name="GPU Availability",
                status=Status.OK,
                message=f"CUDA available: {device_name}",
                details=f"{device_count} GPU(s) detected",
            )
        else:
            return CheckResult(
                name="GPU Availability",
                status=Status.INFO,
                message="No CUDA GPU available (CPU mode)",
                details="ML inference will run on CPU. This is fine but slower.",
            )
    except ImportError:
        return CheckResult(
            name="GPU Availability",
            status=Status.INFO,
            message="PyTorch not installed (GPU check skipped)",
        )
    except Exception as e:
        return CheckResult(
            name="GPU Availability",
            status=Status.WARNING,
            message="Could not check GPU availability",
            details=str(e),
        )


def check_audio_input_device() -> CheckResult:
    """Check if an audio input device (microphone) is available."""
    if os.getenv("ASKREX_DEVELOPER_CONTAINER", "").strip().lower() in {"1", "true", "yes", "on"}:
        return CheckResult(
            name="Audio Input",
            status=Status.INFO,
            message="Developer-only Docker image: host audio input is outside the supported container contract",
        )
    try:
        import sounddevice as sd

        devices = sd.query_devices()
        input_devices = [d for d in devices if d["max_input_channels"] > 0]
        if not input_devices:
            return CheckResult(
                name="Audio Input",
                status=Status.ERROR,
                message="No audio input devices detected",
                details="Connect a microphone and check system audio settings.",
            )
        default = sd.query_devices(kind="input")
        available_names = ", ".join(str(device["name"]) for device in input_devices)
        return CheckResult(
            name="Audio Input",
            status=Status.OK,
            message=f"Input device found: {default['name']} (available: {available_names})",
        )
    except ImportError:
        return CheckResult(
            name="Audio Input",
            status=Status.ERROR,
            message="sounddevice not installed",
            details="Run 'pip install sounddevice' or 'pip install .[full]' to enable audio input.",
        )
    except Exception as e:
        return CheckResult(
            name="Audio Input",
            status=Status.ERROR,
            message=f"Audio input check failed: {e}",
            details="Ensure a microphone is connected and audio drivers are installed.",
        )


def check_audio_output_device() -> CheckResult:
    """Check if an audio output device (speaker/headphones) is available."""
    if os.getenv("ASKREX_DEVELOPER_CONTAINER", "").strip().lower() in {"1", "true", "yes", "on"}:
        return CheckResult(
            name="Audio Output",
            status=Status.INFO,
            message="Developer-only Docker image: host audio output is outside the supported container contract",
        )
    try:
        import sounddevice as sd

        devices = sd.query_devices()
        output_devices = [d for d in devices if d["max_output_channels"] > 0]
        if not output_devices:
            return CheckResult(
                name="Audio Output",
                status=Status.ERROR,
                message="No audio output devices detected",
                details="Connect speakers or headphones and check system audio settings.",
            )
        default = sd.query_devices(kind="output")
        return CheckResult(
            name="Audio Output",
            status=Status.OK,
            message=f"Output device found: {default['name']}",
        )
    except ImportError:
        return CheckResult(
            name="Audio Output",
            status=Status.ERROR,
            message="sounddevice not installed",
            details="Run 'pip install sounddevice' or 'pip install .[full]' to enable audio output.",
        )
    except Exception as e:
        return CheckResult(
            name="Audio Output",
            status=Status.ERROR,
            message=f"Audio output check failed: {e}",
            details="Ensure speakers or headphones are connected and audio drivers are installed.",
        )


def check_lm_studio_reachability(
    host: str = "127.0.0.1", port: int = 1234, timeout: float = 3.0
) -> CheckResult:
    """Check if LM Studio local API server is reachable."""

    try:
        with socket.create_connection((host, port), timeout=timeout):
            return CheckResult(
                name="LM Studio",
                status=Status.OK,
                message=f"LM Studio reachable at {host}:{port}",
            )
    except ConnectionRefusedError:
        return CheckResult(
            name="LM Studio",
            status=Status.WARNING,
            message=f"LM Studio not running (connection refused on {host}:{port})",
            details=f"Start LM Studio and enable the local API server on port {port} for local LLM support.",
        )
    except TimeoutError:
        return CheckResult(
            name="LM Studio",
            status=Status.WARNING,
            message=f"LM Studio not reachable (timed out after {timeout}s on {host}:{port})",
            details=f"Ensure LM Studio is running and listening on {host}:{port}.",
        )
    except OSError as e:
        return CheckResult(
            name="LM Studio",
            status=Status.WARNING,
            message=f"LM Studio not reachable: {e}",
            details=f"Ensure LM Studio is running and listening on {host}:{port}.",
        )


def check_stt_backend() -> CheckResult:
    """Check which STT backend (whisper or faster-whisper) is installed.

    Reports OK when at least one backend is available, ERROR when neither is
    found so that ``rex doctor`` surfaces the gap before the voice loop starts.
    """
    if os.getenv("ASKREX_DEVELOPER_CONTAINER", "").strip().lower() in {"1", "true", "yes", "on"}:
        return CheckResult(
            name="STT Backend",
            status=Status.INFO,
            message="Developer-only Docker image: voice STT is outside the supported container contract",
        )
    has_whisper = find_spec("whisper") is not None
    has_faster_whisper = find_spec("faster_whisper") is not None

    if has_whisper and has_faster_whisper:
        return CheckResult(
            name="STT Backend",
            status=Status.OK,
            message="Both openai-whisper and faster-whisper are installed",
            details="Rex will use openai-whisper by default.",
        )
    if has_whisper:
        return CheckResult(
            name="STT Backend",
            status=Status.OK,
            message="STT backend: openai-whisper",
        )
    if has_faster_whisper:
        return CheckResult(
            name="STT Backend",
            status=Status.OK,
            message="STT backend: faster-whisper",
        )
    return CheckResult(
        name="STT Backend",
        status=Status.ERROR,
        message="No STT backend installed (whisper or faster-whisper required)",
        details=(
            "Install one of:\n" "  pip install openai-whisper\n" "  pip install faster-whisper"
        ),
    )


def check_warm_runtime() -> CheckResult:
    """Report content-free state for managed heavyweight local components."""
    from rex.runtime.warm import WarmState, get_global_warm_runtime

    snapshot = get_global_warm_runtime().snapshot()
    components = snapshot["components"]
    warm_count = sum(1 for item in components if item["state"] == WarmState.WARM)
    degraded_count = sum(
        1 for item in components if item["state"] in {WarmState.DEGRADED, WarmState.ERROR}
    )
    status = Status.WARNING if degraded_count else (Status.OK if warm_count else Status.INFO)
    details = "\n".join(
        f"- {item['name']}: {item['state']}, ~{item['estimated_cost_mb']:g} MB, "
        f"loads={item['load_count']}"
        for item in components
    )
    return CheckResult(
        name="Warm Runtime",
        status=status,
        message=(
            f"{warm_count} warm component(s), ~{snapshot['estimated_cost_mb']:g} MB retained / "
            f"{snapshot['max_cost_mb']:g} MB cache-accounting ceiling"
        ),
        details=details,
    )


def check_stt_warmup(stt: object | None = None) -> CheckResult:
    """Check the STT model warm-up status.

    When called with a live ``SpeechToText`` instance the result reflects
    whether the background-loaded model has finished loading.  When called
    without an instance (e.g. from a standalone ``rex doctor`` run) it falls
    back to checking whether the ``whisper`` package is importable.
    """
    if stt is not None:
        is_loaded = getattr(stt, "is_loaded", lambda: False)()
        load_error: str | None = getattr(stt, "_load_error", None)
        if load_error is not None:
            return CheckResult(
                name="STT model",
                status=Status.ERROR,
                message=f"STT model: load failed — {load_error}",
            )
        if is_loaded:
            return CheckResult(
                name="STT model",
                status=Status.OK,
                message="STT model: loaded",
            )
        return CheckResult(
            name="STT model",
            status=Status.INFO,
            message="STT model: loading...",
        )

    # Standalone path: check whisper availability
    try:
        if find_spec("whisper") is not None:
            return CheckResult(
                name="STT model",
                status=Status.OK,
                message="STT model: whisper available (warm-up enabled at startup)",
            )
        return CheckResult(
            name="STT model",
            status=Status.WARNING,
            message="STT model: whisper not installed",
            details="Install whisper: pip install openai-whisper",
        )
    except Exception as exc:
        return CheckResult(
            name="STT model",
            status=Status.ERROR,
            message=f"STT model check failed: {exc}",
        )


def check_smart_speakers() -> CheckResult:
    """Check for discovered Sonos and Bose speakers on the local network."""
    service = get_speaker_discovery()
    speakers = service.discover_now()
    if not speakers:
        return CheckResult(
            name="Smart Speakers",
            status=Status.INFO,
            message="No Sonos or Bose speakers discovered",
            details="Discovery runs in the background at startup. Ensure speakers are on the same LAN.",
        )

    details = "\n".join(
        f"- {speaker.provider.title()}: {speaker.name} ({speaker.model}) @ {speaker.ip}"
        for speaker in speakers
    )
    return CheckResult(
        name="Smart Speakers",
        status=Status.OK,
        message=f"{len(speakers)} smart speaker(s) discovered",
        details=details,
    )


def check_config_types(root: Path | None) -> CheckResult:
    """Load rex_config.json and validate field types via build_app_config.

    Returns WARNING if any numeric fields carry string-typed values, and
    ERROR if any field holds an invalid (non-parseable) value.
    """
    if root is None:
        return CheckResult(
            name="Config Types",
            status=Status.INFO,
            message="Could not determine project root (skipped)",
        )

    config_path = root / "config" / "rex_config.json"
    if not config_path.exists():
        return CheckResult(
            name="Config Types",
            status=Status.INFO,
            message="rex_config.json not found (skipped)",
        )

    import json
    import logging

    try:
        with open(config_path) as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        return CheckResult(
            name="Config Types",
            status=Status.ERROR,
            message=f"Cannot read config for type check: {exc}",
        )

    warnings_found: list[str] = []

    class _WarningCatcher(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            if record.levelno >= logging.WARNING and "coercing" in record.getMessage():
                warnings_found.append(record.getMessage())

    logger = logging.getLogger("rex.config")
    handler = _WarningCatcher()
    logger.addHandler(handler)
    try:
        from rex.assistant_errors import ConfigurationError
        from rex.config import build_app_config

        build_app_config(raw)
    except ConfigurationError as exc:
        return CheckResult(
            name="Config Types",
            status=Status.ERROR,
            message=f"Config type validation failed: {exc}",
            details="Fix the value in rex_config.json and run rex doctor again.",
        )
    except (ValueError, TypeError) as exc:
        return CheckResult(
            name="Config Types",
            status=Status.ERROR,
            message=f"Config has invalid value: {exc}",
        )
    except Exception as exc:
        return CheckResult(
            name="Config Types",
            status=Status.WARNING,
            message=f"Config type check could not complete: {exc}",
            release_blocking=True,
        )
    finally:
        logger.removeHandler(handler)

    if warnings_found:
        details = "\n".join(f"- {w}" for w in warnings_found)
        details += "\nFix these fields in rex_config.json to use JSON numbers (not strings)."
        return CheckResult(
            name="Config Types",
            status=Status.WARNING,
            message=f"{len(warnings_found)} field(s) have string-typed numeric values",
            details=details,
            release_blocking=True,
        )

    return CheckResult(
        name="Config Types",
        status=Status.OK,
        message="All config fields have correct types",
    )


def check_ffmpeg_for_tts() -> CheckResult:
    """Check FFmpeg availability relative to the active TTS backend.

    FFmpeg is required only when XTTS (Coqui TTS) is the active provider because
    torio (a Coqui dependency) attempts to load FFmpeg extension libraries at
    import time.  For edge-tts and pyttsx3, FFmpeg is not needed.
    """
    # Determine the active TTS provider from config (default: "xtts")
    tts_provider = "xtts"
    try:
        from rex.config import load_config

        cfg = load_config()
        tts_provider = getattr(cfg, "tts_provider", "xtts") or "xtts"
    except Exception:
        pass  # Fall through with the default

    ffmpeg_path = shutil.which("ffmpeg")
    xtts_providers = {"xtts", "coqui"}
    requires_ffmpeg = tts_provider.lower() in xtts_providers

    if ffmpeg_path:
        return CheckResult(
            name="FFmpeg (TTS)",
            status=Status.OK,
            message=f"FFmpeg found: {ffmpeg_path}",
            details=f"Active TTS provider: {tts_provider}",
        )

    if requires_ffmpeg:
        return CheckResult(
            name="FFmpeg (TTS)",
            status=Status.WARNING,
            message=f"FFmpeg not found — required for active TTS provider '{tts_provider}'",
            details=(
                "Install FFmpeg and ensure it is on your PATH.\n"
                "Windows: https://ffmpeg.org/download.html\n"
                "macOS:   brew install ffmpeg\n"
                "Linux:   sudo apt install ffmpeg\n"
                "Rex will attempt to fall back to edge-tts if XTTS fails to load."
            ),
        )

    return CheckResult(
        name="FFmpeg (TTS)",
        status=Status.INFO,
        message=f"FFmpeg not found — not required for active TTS provider '{tts_provider}'",
        details="Audio capture and playback use sounddevice and do not require FFmpeg.",
    )


def check_xtts_transformers_compat() -> CheckResult:
    """Check XTTS + transformers version compatibility status."""
    if find_spec("TTS") is None:
        return CheckResult(
            name="XTTS Compatibility",
            status=Status.INFO,
            message="Coqui TTS not installed (XTTS check skipped)",
        )

    if find_spec("transformers") is None:
        return CheckResult(
            name="XTTS Compatibility",
            status=Status.WARNING,
            message="transformers not installed; XTTS may not work",
            details="Install transformers: pip install 'transformers>=4.38.0,<5.0'",
        )

    try:
        import transformers

        transformers_version = getattr(transformers, "__version__", "unknown")

        if hasattr(transformers, "BeamSearchScorer"):
            return CheckResult(
                name="XTTS Compatibility",
                status=Status.OK,
                message=f"transformers {transformers_version}: compatible (BeamSearchScorer available)",
            )

        # BeamSearchScorer missing — try the shim
        from rex.compat import ensure_transformers_compatibility

        ensure_transformers_compatibility()

        if hasattr(transformers, "BeamSearchScorer"):
            return CheckResult(
                name="XTTS Compatibility",
                status=Status.OK,
                message=(
                    f"transformers {transformers_version}: compatible"
                    " (compatibility shim applied for BeamSearchScorer)"
                ),
            )

        return CheckResult(
            name="XTTS Compatibility",
            status=Status.WARNING,
            message=f"transformers {transformers_version}: BeamSearchScorer missing after shim",
            details="XTTS may fail to load. TTS will fall back to edge-tts automatically.",
        )
    except Exception as exc:
        return CheckResult(
            name="XTTS Compatibility",
            status=Status.WARNING,
            message=f"XTTS compatibility check failed: {exc}",
        )


def check_wakeword_config() -> CheckResult:
    """Check wake word configuration: keyword presence and model file existence."""
    try:
        from .config import load_config
        from .wakeword.utils import (
            load_wakeword_model_with_metadata,
            resolve_custom_wakeword_embedding_path,
            resolve_custom_wakeword_model_path,
        )

        config = load_config(reload=True)
    except Exception as exc:
        return CheckResult(
            name="Wake Word Config",
            status=Status.WARNING,
            message=f"Could not load config: {exc}",
        )

    keyword = (getattr(config, "wakeword", "hey_rex") or "").strip()
    raw_backend = getattr(config, "wakeword_backend", "openwakeword")
    backend = raw_backend.strip() if isinstance(raw_backend, str) else "openwakeword"
    if not keyword:
        return CheckResult(
            name="Wake Word Config",
            status=Status.ERROR,
            message="Wake word keyword is empty",
            details="Set wakeword.wakeword in rex_config.json (e.g. 'hey_rex').",
        )

    explicit_model_path = getattr(config, "wakeword_model_path", None)
    if isinstance(explicit_model_path, str) and explicit_model_path.strip():
        resolved_model = Path(explicit_model_path)
        if not resolved_model.is_file():
            return CheckResult(
                name="Wake Word Config",
                status=Status.ERROR,
                message=f"Wake word model file not found: {resolved_model}",
                details="Set wakeword.model_path to an existing custom ONNX file.",
            )
        if backend != "custom_onnx":
            return CheckResult(
                name="Wake Word Config",
                status=Status.OK,
                message=(
                    f"backend={backend}, keyword='{keyword}', " f"model='{resolved_model.name}'"
                ),
            )

    if backend == "custom_onnx":
        resolved = resolve_custom_wakeword_model_path(
            keyword,
            explicit_model_path,
        )
        if not resolved.is_file():
            return CheckResult(
                name="Wake Word Config",
                status=Status.ERROR,
                message=f"Wake word model file not found: {resolved}",
                details=("Set wakeword.model_path or place a custom ONNX file at " f"{resolved}."),
            )
        try:
            _, selection = load_wakeword_model_with_metadata(
                keyword=keyword,
                model_path=str(resolved),
                backend="custom_onnx",
                fallback_to_builtin=False,
                fallback_keyword=getattr(config, "wakeword_fallback_keyword", "hey jarvis"),
            )
        except Exception as exc:
            return CheckResult(
                name="Wake Word Config",
                status=Status.ERROR,
                message=f"Wake word model failed validation: {resolved}",
                details=str(exc),
            )
        return CheckResult(
            name="Wake Word Config",
            status=Status.OK,
            message=f"backend=custom_onnx, phrase='{keyword}', model='{resolved.name}'",
            details=f"Validated custom ONNX asset. Active backend: {selection.active_backend}.",
        )

    if backend == "custom_embedding":
        resolved = resolve_custom_wakeword_embedding_path(
            keyword,
            getattr(config, "wakeword_embedding_path", None),
        )
        if not resolved.is_file():
            return CheckResult(
                name="Wake Word Config",
                status=Status.ERROR,
                message=f"Wake word embedding file not found: {resolved}",
                details=(
                    "Set wakeword.embedding_path or place a trained embedding at " f"{resolved}."
                ),
            )
        try:
            _, selection = load_wakeword_model_with_metadata(
                keyword=keyword,
                embedding_path=str(resolved),
                backend="custom_embedding",
                fallback_to_builtin=False,
                fallback_keyword=getattr(config, "wakeword_fallback_keyword", "hey jarvis"),
            )
        except Exception as exc:
            return CheckResult(
                name="Wake Word Config",
                status=Status.ERROR,
                message=f"Wake word embedding failed validation: {resolved}",
                details=str(exc),
            )
        return CheckResult(
            name="Wake Word Config",
            status=Status.OK,
            message=f"backend=custom_embedding, phrase='{keyword}', embedding='{resolved.name}'",
            details=f"Validated custom embedding asset. Active backend: {selection.active_backend}.",
        )

    return CheckResult(
        name="Wake Word Config",
        status=Status.OK,
        message=f"backend=openwakeword, keyword='{keyword}'",
    )


def _current_whisper_language() -> str:
    try:
        from .config import load_config

        config = load_config(reload=True)
        if config.whisper_language is None:
            return "auto-detect"
        return config.whisper_language
    except Exception:
        return "unavailable"


def _print_debug_info() -> None:
    """Print extra low-level diagnostic info when --debug is active."""
    import logging
    import os

    print("=== DEBUG INFO ===")

    # Log level
    root_level = logging.getLevelName(logging.getLogger().getEffectiveLevel())
    print(f"  Root log level : {root_level}")

    # REX_DEBUG env var
    print(f"  REX_DEBUG      : {os.getenv('REX_DEBUG', '(not set)')}")

    # Config values (redact secrets)
    try:
        from rex.config import load_config

        cfg = load_config()
        secret_fields = {
            "openai_api_key",
            "anthropic_api_key",
            "brave_api_key",
            "speak_api_key",
            "ollama_api_key",
        }
        print("  Config fields  :")
        for field_name, value in vars(cfg).items():
            if field_name in secret_fields:
                display = "***" if value else "(not set)"
            else:
                display = repr(value)
            print(f"    {field_name} = {display}")
    except Exception as exc:
        print(f"  Config load failed: {exc}")

    # Loaded integrations (providers that are configured)
    try:
        from rex.config import load_config

        cfg = load_config()
        integrations = []
        if cfg.openai_api_key:
            integrations.append("openai")
        if cfg.anthropic_api_key:
            integrations.append("anthropic")
        if cfg.brave_api_key:
            integrations.append("brave-search")
        if getattr(cfg, "ha_base_url", None):
            integrations.append("home-assistant")
        print(f"  Configured integrations: {', '.join(integrations) or '(none)'}")
        print(f"  LLM provider   : {cfg.llm_provider}")
        print(f"  LLM model      : {cfg.llm_model}")
        print(f"  TTS provider   : {cfg.tts_provider}")
    except Exception:
        pass

    print("==================")
    print()


def check_integration_states() -> CheckResult:
    """Report configured integration evidence without probing or overclaiming it."""
    try:
        from rex.config import load_config
        from rex.integration_state import build_integration_inventory

        integrations = build_integration_inventory(load_config())
        details = "\n".join(
            f"{item.name}: {item.state.value}" + (f" - {item.detail}" if item.detail else "")
            for item in integrations
        )
        configured = sum(item.configured for item in integrations)
        return CheckResult(
            name="Integration readiness",
            status=Status.INFO,
            message=f"{configured} configured; live state requires explicit provider tests",
            details=details,
        )
    except Exception as exc:
        return CheckResult(
            name="Integration readiness",
            status=Status.WARNING,
            message="Could not determine integration readiness",
            details=str(exc),
        )


def run_healthcheck() -> int:
    """Run a lightweight process-level liveness probe.

    This intentionally avoids hardware, model, network, and integration checks so
    container health polling does not load the voice stack every 30 seconds.  Full
    readiness remains the responsibility of ``rex doctor`` / ``--release-gate``.
    The liveness probe fails closed if the supported Python runtime, installed Rex
    package/contract, or CLI parser cannot be loaded.
    """
    checks = [check_python_version(), check_package_installation()]
    for result in checks:
        print(f"{result.name}: {result.status.value} - {result.message}")
        if result.status != Status.OK:
            return 1

    try:
        from rex.cli import create_parser

        parser = create_parser()
        parser.parse_args(["version"])
    except Exception as exc:
        print(f"CLI Runtime: error - {exc}")
        return 1

    print("CLI Runtime: ok")
    return 0


def run_diagnostics(
    verbose: bool = False,
    debug: bool = False,
    release_gate: bool = False,
) -> int:
    """Run all diagnostic checks and print results.

    Args:
        verbose: If True, show detailed information for all checks.
        debug: If True, print additional low-level diagnostic info.
        release_gate: If True, actionable warnings also produce a nonzero exit.

    Returns:
        Exit code: 0 if no errors, 1 if errors found.
    """
    print("Rex Doctor - Environment Diagnostics")
    print("=" * 40)
    print()

    if release_gate:
        print("Release gate: enabled (actionable warnings are blocking)")
        print()

    if debug:
        _print_debug_info()

    report = DiagnosticsReport()
    project_root = _find_project_root()

    if project_root:
        print(f"Project root: {project_root}")
    else:
        print("Project root: (not found)")
    print(f"Whisper language: {_current_whisper_language()}")
    print()

    # Core checks
    report.add(check_python_version())
    report.add(check_package_installation())
    report.add(check_config_file(project_root))
    report.add(check_config_types(project_root))
    report.add(check_env_file(project_root))
    report.add(check_credential_vault_backend())
    report.add(check_environment_variables())
    report.add(check_config_permissions(project_root))

    # Required packages (always shown)
    for result in check_core_dependencies():
        report.add(result)

    # Audio devices
    report.add(check_audio_input_device())
    report.add(check_audio_output_device())
    report.add(check_smart_speakers())

    # LM Studio reachability
    report.add(check_lm_studio_reachability())

    # External dependencies (git and other binaries)
    for result in check_external_dependencies():
        report.add(result)

    # FFmpeg — report status relative to the active TTS backend
    report.add(check_ffmpeg_for_tts())

    # Wake word config
    report.add(check_wakeword_config())

    # STT backend availability
    report.add(check_stt_backend())

    # STT warm-up
    report.add(check_stt_warmup())

    # Managed heavyweight runtime state
    report.add(check_warm_runtime())

    # GPU check
    report.add(check_gpu_availability())

    # XTTS + transformers compatibility
    report.add(check_xtts_transformers_compat())
    report.add(check_integration_states())

    # Print results
    for result in report.results:
        symbol = _status_symbol(result.status)
        print(f"{symbol:8s} {result.name}: {result.message}")
        show_details = bool(
            result.details
            and (verbose or result.status == Status.ERROR or result.name == "Smart Speakers")
        )
        if show_details:
            for line in result.details.split("\n"):
                print(f"         {line}")

    # Summary
    print()
    print("-" * 40)

    if report.has_errors():
        print(f"Rex is NOT ready to use. Fix the {report.error_count} ERROR item(s) above.")
        return 1
    if release_gate and report.release_blocker_count:
        print(
            "Rex is NOT release-ready. Fix the "
            f"{report.release_blocker_count} actionable WARN item(s) above."
        )
        return 1
    if report.has_warnings():
        print(f"Rex is ready to use with {report.warning_count} warning(s).")
    else:
        print("Rex is ready to use.")
    return 0


if __name__ == "__main__":
    sys.exit(run_diagnostics(verbose=True))
