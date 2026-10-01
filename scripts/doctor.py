"""Rex doctor – quick diagnostics for the local environment."""

from __future__ import annotations

import os
import platform
import shutil
import sys
from importlib import import_module

# Add repo root to path and load .env before accessing any environment variables
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)
from utils.env_loader import load as _load_env  # noqa: E402

_load_env()

CheckResult = tuple[str, bool, str]


def _check_python() -> CheckResult:
    major, minor = sys.version_info[:2]
    if major > 3 or (major == 3 and minor >= 10):
        return ("Python version", True, f"{major}.{minor} detected")
    return (
        "Python version",
        False,
        f"{major}.{minor} detected – Rex targets Python 3.10 or newer.",
    )


def _check_binary(name: str, install_hint: str) -> CheckResult:
    path = shutil.which(name)
    if path:
        return (name, True, f"{name} available at {path}")
    return (
        name,
        False,
        f"{name} executable not found. Install via {install_hint} and ensure it is on PATH.",
    )


def _check_torch_cuda() -> CheckResult:
    try:
        import torch
    except Exception:
        return (
            "PyTorch",
            False,
            "torch is not installed. Install CPU build via "
            "`pip install torch==2.2.1 --index-url https://download.pytorch.org/whl/cpu` "
            "or follow README GPU instructions.",
        )

    if torch.cuda.is_available():
        device = torch.cuda.get_device_name(0)
        return ("CUDA", True, f"CUDA available: {device}")
    return (
        "CUDA",
        True,
        "CUDA not detected – running in CPU mode (expected for non-GPU hosts).",
    )


def _check_env_vars() -> list[CheckResult]:
    checks: list[CheckResult] = []
    required = {
        "REX_SPEAK_API_KEY": "Required for /speak TTS endpoint authentication.",  # pragma: allowlist secret -- environment-variable name
    }
    for key, why in required.items():
        if os.getenv(key):
            checks.append((key, True, "set"))
        else:
            checks.append((key, False, f"not set ({why})"))

    optional = {
        "OPENAI_API_KEY": "Needed for OpenAI-backed chat responses.",  # pragma: allowlist secret -- environment-variable name
        "REX_LLM_MODEL": "Overrides default local LLM.",
    }
    for key, why in optional.items():
        if os.getenv(key):
            checks.append((key, True, "set"))
        else:
            checks.append((key, True, f"not set (optional – {why})"))
    return checks


def _check_rate_limiter() -> CheckResult:
    storage = os.getenv("REX_SPEAK_STORAGE_URI") or os.getenv("FLASK_LIMITER_STORAGE_URI")
    if storage and not storage.startswith("memory://"):
        return ("Rate limiter storage", True, f"configured ({storage})")
    return (
        "Rate limiter storage",
        True,
        "using in-memory backend; configure REX_SPEAK_STORAGE_URI (e.g. redis://localhost:6379/0) "
        "when running multiple workers.",
    )


def _check_inbound_webhook() -> list[CheckResult]:
    """Check inbound SMS webhook readiness.

    Loads the runtime config and validates that, when inbound is enabled,
    the required auth token credential is resolvable.  Never prints secrets.
    """
    checks: list[CheckResult] = []

    try:
        inbound_store = import_module("rex.messaging_backends.inbound_store")
        load_inbound_store_config = inbound_store.load_inbound_store_config
        from rex.config_manager import load_config
    except ImportError:
        # Messaging backends not installed or importable — skip silently
        return checks

    try:
        raw_config = load_config()
    except Exception:
        checks.append(
            (
                "Inbound SMS webhook",
                True,
                "config not loaded (messaging section may be absent); skipped",
            )
        )
        return checks

    inbound_cfg = load_inbound_store_config(raw_config)

    if not inbound_cfg.enabled:
        checks.append(
            (
                "Inbound SMS webhook",
                True,
                "disabled in config (messaging.inbound.enabled = false)",
            )
        )
        return checks

    # Enabled — verify auth token is resolvable
    try:
        from rex.credentials import get_credential_manager

        cred_mgr = get_credential_manager()
        token = cred_mgr.get_token(inbound_cfg.auth_token_ref)
    except Exception:
        token = None

    if token:
        checks.append(
            (
                "Inbound SMS webhook",
                True,
                f"enabled; auth token resolved (ref: {inbound_cfg.auth_token_ref})",
            )
        )
    else:
        checks.append(
            (
                "Inbound SMS webhook",
                False,
                f"enabled but auth token not found (ref: {inbound_cfg.auth_token_ref}). "
                "Set the credential via .env or config/credentials.json.",
            )
        )

    return checks


def run_checks() -> list[CheckResult]:
    results: list[CheckResult] = []
    results.append(_check_python())
    results.append(_check_binary("ffmpeg", "https://ffmpeg.org/download.html"))
    results.append(_check_torch_cuda())
    results.extend(_check_env_vars())
    results.append(_check_rate_limiter())
    results.extend(_check_inbound_webhook())
    return results


def _format_result(result: CheckResult) -> str:
    label, ok, message = result
    status = "PASS" if ok else "WARN"
    return f"[{status}] {label}: {message}"


def main() -> int:
    print("Rex Doctor\n==========")
    print(f"Platform: {platform.system()} {platform.release()} ({platform.machine()})\n")

    results = run_checks()
    warnings = sum(0 if ok else 1 for _, ok, _ in results)
    for result in results:
        print(_format_result(result))

    print("\nSummary:", "all good." if warnings == 0 else f"{warnings} warning(s) detected.")
    print("See README for installation and configuration guidance.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
