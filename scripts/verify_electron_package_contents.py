"""Fail closed when the packaged Electron resources are incomplete or contain user data."""

from __future__ import annotations

import argparse
import json
import subprocess
from collections.abc import Callable
from pathlib import Path

REQUIRED_BRIDGES = {
    "rex_calendar_bridge.py",
    "rex_chat_bridge.py",
    "rex_chat_stream_bridge.py",
    "rex_credential_vault_bridge.py",
    "rex_email_bridge.py",
    "rex_file_extract_bridge.py",
    "rex_ha_mutation_bridge.py",
    "rex_history_bridge.py",
    "rex_identity_bridge.py",
    "rex_memories_bridge.py",
    "rex_pairing_bridge.py",
    "rex_reminders_bridge.py",
    "rex_shopping_list_bridge.py",
    "rex_setup_bridge.py",
    "rex_sms_bridge.py",
    "rex_speaker_bridge.py",
    "rex_stt_bridge.py",
    "rex_tasks_bridge.py",
    "rex_voice_bridge.py",
    "rex_voice_enrollment_bridge.py",
    "rex_voice_sample_bridge.py",
    "rex_voice_upload_bridge.py",
    "rex_voices_bridge.py",
    "rex_wakeword_list_bridge.py",
    "rex_wakeword_sample_bridge.py",
    "rex_wakeword_train_bridge.py",
}
ALLOWED_CONFIG = {
    "autonomy.json",
    "rex_config.example.json",
    "rex_config.schema.json",
    "triage_rules.json",
}
FORBIDDEN_NAMES = {
    ".env",
    "credentials.json",
    "fake_credentials.json",
    "gui_settings.json",
    "plaintext_credentials.json",
    "rex_config.json",
    "users.json",
    "session.json",
    "node.exe",
}
FORBIDDEN_PARTS = {"memory", "profiles", "logs", "transcripts"}
REQUIRED_VOICE_DIST_INFO_PREFIXES = (
    "numpy-",
    "sounddevice-",
    "soundfile-",
    "torch-",
    "openai_whisper-",
    "imageio_ffmpeg-",
)
REQUIRED_RUNTIME_IMPORTS = (
    "rex.background.supervisor",
    "numpy",
    "sounddevice",
    "soundfile",
    "torch",
    "whisper",
    "imageio_ffmpeg",
)
RuntimeProbe = Callable[[Path], list[str]]


def probe_managed_runtime(python_exe: Path) -> list[str]:
    """Prove the installed managed runtime can import its background/Voice stack."""

    modules_literal = repr(REQUIRED_RUNTIME_IMPORTS)
    probe_code = (
        "import importlib\n"
        f"modules={modules_literal}\n"
        "for name in modules:\n"
        "    try:\n"
        "        importlib.import_module(name)\n"
        "    except Exception:\n"
        "        print(name)\n"
        "        raise SystemExit(7)\n"
    )
    try:
        result = subprocess.run(
            [str(python_exe.resolve()), "-I", "-c", probe_code],
            cwd=python_exe.resolve().parent,
            shell=False,
            check=False,
            capture_output=True,
            text=True,
            timeout=60.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ["managed runtime import probe could not execute"]

    if result.returncode == 0:
        return []

    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    failed_module = lines[-1] if lines else ""
    if failed_module in REQUIRED_RUNTIME_IMPORTS:
        return [f"managed runtime import failed: {failed_module}"]
    return ["managed runtime import probe failed"]


def verify(resources: Path, *, runtime_probe: RuntimeProbe | None = None) -> list[str]:
    errors: list[str] = []
    python_exe = resources / "python" / "python.exe"
    pythonw_exe = resources / "python" / "pythonw.exe"
    metadata_path = resources / "python" / "ASKREX_RUNTIME.json"
    if not python_exe.is_file():
        errors.append("managed python/python.exe is missing")
    if not pythonw_exe.is_file():
        errors.append(
            "managed python/pythonw.exe is missing (required for the windowless"
            " background runtime supervisor)"
        )
    if not metadata_path.is_file():
        errors.append("managed runtime metadata is missing")
    else:
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"managed runtime metadata is invalid: {exc}")
        else:
            if not str(metadata.get("python_version", "")).startswith("3.11."):
                errors.append("managed runtime is not Python 3.11")

    bridge_dir = resources / "bridge"
    actual_bridges = {path.name for path in bridge_dir.glob("*.py")}
    for missing in sorted(REQUIRED_BRIDGES - actual_bridges):
        errors.append(f"missing bridge: {missing}")

    config_dir = resources / "config"
    actual_config = {path.name for path in config_dir.iterdir()} if config_dir.is_dir() else set()
    unexpected_config = actual_config - ALLOWED_CONFIG
    if unexpected_config:
        errors.append(f"unexpected config files: {sorted(unexpected_config)}")

    for path in resources.rglob("*"):
        relative_parts = {part.casefold() for part in path.relative_to(resources).parts}
        if path.name.casefold() in FORBIDDEN_NAMES:
            errors.append(f"forbidden packaged file: {path.relative_to(resources)}")
        if relative_parts & FORBIDDEN_PARTS:
            errors.append(f"forbidden personal-data path: {path.relative_to(resources)}")

    site_packages = resources / "python" / "Lib" / "site-packages"
    if (site_packages / "flask").exists() or list(site_packages.glob("Flask-*.dist-info")):
        errors.append("Flask is present in the Electron runtime")
    if not (site_packages / "rex").is_dir():
        errors.append("installed AskRex package is missing from managed runtime")
    elif not (site_packages / "rex" / "credential_vault.py").is_file():
        errors.append("credential vault provider is missing from managed runtime")
    background_pkg = site_packages / "rex" / "background"
    if not (background_pkg / "__init__.py").is_file():
        errors.append("rex.background package is missing from managed runtime")
    for prefix in REQUIRED_VOICE_DIST_INFO_PREFIXES:
        if not list(site_packages.glob(f"{prefix}*.dist-info")):
            errors.append(f"managed Voice runtime is missing dependency: {prefix.rstrip('-')}")
    if python_exe.is_file():
        probe = runtime_probe or probe_managed_runtime
        errors.extend(probe(python_exe))
    for relative in (
        Path("win32/win32crypt.pyd"),
        Path("win32/win32security.pyd"),
        Path("win32/win32api.pyd"),
        Path("win32/lib/ntsecuritycon.py"),
    ):
        if not (site_packages / relative).is_file():
            errors.append(f"credential vault Windows dependency is missing: {relative}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("resources", type=Path)
    args = parser.parse_args()
    errors = verify(args.resources.resolve())
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(f"Electron resource verification passed: {args.resources.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
