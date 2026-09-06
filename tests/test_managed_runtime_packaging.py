from __future__ import annotations

import json
import subprocess
import tomllib
from pathlib import Path

import pytest

from scripts import verify_electron_package_contents as verifier
from scripts.verify_electron_package_contents import (
    REQUIRED_BRIDGES,
    REQUIRED_VOICE_DIST_INFO_PREFIXES,
    verify,
)

ROOT = Path(__file__).resolve().parents[1]


def _successful_runtime_probe(_: Path) -> list[str]:
    return []


def packaged_resources(tmp_path: Path) -> Path:
    resources = tmp_path / "resources"
    (resources / "python" / "Lib" / "site-packages" / "rex").mkdir(parents=True)
    (resources / "python" / "python.exe").write_bytes(b"test")
    (resources / "python" / "pythonw.exe").write_bytes(b"test")
    (resources / "python" / "ASKREX_RUNTIME.json").write_text(
        json.dumps({"python_version": "3.11.9"}), encoding="utf-8"
    )
    (resources / "python" / "Lib" / "site-packages" / "rex" / "credential_vault.py").write_text(
        "# provider\n", encoding="utf-8"
    )
    background_pkg = resources / "python" / "Lib" / "site-packages" / "rex" / "background"
    background_pkg.mkdir()
    (background_pkg / "__init__.py").write_text("", encoding="utf-8")
    site_packages = resources / "python" / "Lib" / "site-packages"
    for prefix in REQUIRED_VOICE_DIST_INFO_PREFIXES:
        (site_packages / f"{prefix}1.0.0.dist-info").mkdir()
    win32 = resources / "python" / "Lib" / "site-packages" / "win32"
    (win32 / "lib").mkdir(parents=True)
    for relative in ("win32crypt.pyd", "win32security.pyd", "win32api.pyd"):
        (win32 / relative).write_bytes(b"test")
    (win32 / "lib" / "ntsecuritycon.py").write_text("# test\n", encoding="utf-8")
    bridge = resources / "bridge"
    bridge.mkdir()
    for name in REQUIRED_BRIDGES:
        (bridge / name).write_text("# bridge\n", encoding="utf-8")
    config = resources / "config"
    config.mkdir()
    for name in (
        "autonomy.json",
        "rex_config.example.json",
        "rex_config.schema.json",
        "triage_rules.json",
    ):
        (config / name).write_text("{}", encoding="utf-8")
    return resources


def test_packaged_resources_accept_complete_resource_structure_with_runtime_probe(
    tmp_path: Path,
) -> None:
    assert verify(packaged_resources(tmp_path), runtime_probe=_successful_runtime_probe) == []


def test_packaged_resources_reject_flask_and_user_configuration(tmp_path: Path) -> None:
    resources = packaged_resources(tmp_path)
    (resources / "python" / "Lib" / "site-packages" / "flask").mkdir()
    (resources / "config" / "rex_config.json").write_text("{}", encoding="utf-8")
    errors = verify(resources, runtime_probe=_successful_runtime_probe)
    assert any("Flask" in error for error in errors)
    assert any("rex_config.json" in error for error in errors)


def test_packaged_resources_require_vault_bridge_and_provider(tmp_path: Path) -> None:
    resources = packaged_resources(tmp_path)
    (resources / "bridge" / "rex_credential_vault_bridge.py").unlink()
    (resources / "python" / "Lib" / "site-packages" / "rex" / "credential_vault.py").unlink()
    errors = verify(resources, runtime_probe=_successful_runtime_probe)
    assert any("rex_credential_vault_bridge.py" in error for error in errors)
    assert any("credential vault provider" in error for error in errors)


def test_packaged_resources_reject_plaintext_credential_artifacts(tmp_path: Path) -> None:
    resources = packaged_resources(tmp_path)
    (resources / ".env").write_text("OPENAI_API_KEY=forbidden\n", encoding="utf-8")
    (resources / "fake_credentials.json").write_text("{}", encoding="utf-8")
    errors = verify(resources, runtime_probe=_successful_runtime_probe)
    assert any(".env" in error for error in errors)
    assert any("fake_credentials.json" in error for error in errors)


def test_packaged_resources_require_pythonw_for_background_runtime(tmp_path: Path) -> None:
    resources = packaged_resources(tmp_path)
    (resources / "python" / "pythonw.exe").unlink()
    errors = verify(resources, runtime_probe=_successful_runtime_probe)
    assert any("pythonw.exe" in error for error in errors)


def test_packaged_resources_require_background_runtime_package(tmp_path: Path) -> None:
    resources = packaged_resources(tmp_path)
    background_pkg = resources / "python" / "Lib" / "site-packages" / "rex" / "background"
    (background_pkg / "__init__.py").unlink()
    background_pkg.rmdir()
    errors = verify(resources, runtime_probe=_successful_runtime_probe)
    assert any("rex.background" in error for error in errors)


def test_voice_verifier_covers_every_pinned_voice_requirement() -> None:
    requirements = (ROOT / "requirements-electron-voice.txt").read_text(encoding="utf-8")
    expected = {
        f"{line.split('==', 1)[0].replace('-', '_')}-"
        for raw in requirements.splitlines()
        if (line := raw.strip()) and not line.startswith("#")
    }
    assert set(REQUIRED_VOICE_DIST_INFO_PREFIXES) == expected


@pytest.mark.parametrize("prefix", REQUIRED_VOICE_DIST_INFO_PREFIXES)
def test_packaged_resources_require_every_voice_runtime_dependency(
    tmp_path: Path, prefix: str
) -> None:
    resources = packaged_resources(tmp_path)
    site_packages = resources / "python" / "Lib" / "site-packages"
    for entry in site_packages.glob(f"{prefix}*.dist-info"):
        entry.rmdir()
    errors = verify(resources, runtime_probe=_successful_runtime_probe)
    dependency = prefix.rstrip("-")
    assert any(dependency in error for error in errors)


def test_electron_packaging_never_falls_back_to_machine_python() -> None:
    resolver = (ROOT / "gui/src/main/bridgeResolver.ts").read_text(encoding="utf-8")
    packaged_block = resolver.split("if (app.isPackaged)", 1)[1].split(
        "const bundledVenvPython", 1
    )[0]
    assert "resourcesPath, 'python', 'python.exe'" in packaged_block
    assert "return 'python'" not in packaged_block


def test_runtime_builder_is_pinned_and_package_filters_private_config() -> None:
    builder = (ROOT / "scripts/build_managed_python_runtime.ps1").read_text(encoding="utf-8")
    voice_requirements = (ROOT / "requirements-electron-voice.txt").read_text(encoding="utf-8")
    package = json.loads((ROOT / "gui/package.json").read_text(encoding="utf-8"))
    assert "3.11.9" in builder
    runtime_sha256 = "009D6BF7E3B2DDCA3D784FA09F90FE54336D5B60F0E0F305C37F400BF83CFD3B"  # pragma: allowlist secret
    assert runtime_sha256 in builder
    assert "torch==2.12.1" in voice_requirements
    assert "torch.__version__.split('+', 1)[0] == '2.12.1'" in builder
    resources = package["build"]["extraResources"]
    config_rule = next(item for item in resources if item["to"] == "config")
    assert "rex_config.json" not in config_rule["filter"]
    assert next(item for item in resources if item["to"] == "python")["from"] == "runtime/python"


def test_managed_wheel_includes_voice_runtime_utils_package() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    include = config["tool"]["setuptools"]["packages"]["find"]["include"]
    assert "utils*" in include


def test_packaged_bridges_do_not_require_a_source_checkout() -> None:
    for bridge_name in REQUIRED_BRIDGES:
        source = (ROOT / "bridge" / bridge_name).read_text(encoding="utf-8")
        assert "repo_root()" not in source, bridge_name


def test_packaged_resources_reject_non_executable_managed_runtime(tmp_path: Path) -> None:
    errors = verify(packaged_resources(tmp_path))
    assert any("runtime import probe" in error for error in errors)


def test_runtime_probe_uses_absolute_isolated_managed_python_and_complete_import_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resources = packaged_resources(tmp_path)
    python_exe = resources / "python" / "python.exe"
    observed: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        observed["command"] = command
        observed["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(verifier.subprocess, "run", fake_run)
    assert verifier.probe_managed_runtime(python_exe) == []

    command = observed["command"]
    assert isinstance(command, list)
    assert Path(command[0]).is_absolute()
    assert Path(command[0]) == python_exe.resolve()
    assert command[1:3] == ["-I", "-c"]
    probe_code = command[3]
    for module_name in (
        "rex.background.supervisor",
        "numpy",
        "sounddevice",
        "soundfile",
        "torch",
        "whisper",
        "imageio_ffmpeg",
    ):
        assert module_name in probe_code
    kwargs = observed["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["shell"] is False
    assert kwargs["check"] is False
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True


def test_runtime_probe_reports_bounded_module_name_without_raw_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resources = packaged_resources(tmp_path)
    python_exe = resources / "python" / "python.exe"

    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command,
            7,
            stdout="torch\n",
            stderr="private provider text must not escape",
        )

    monkeypatch.setattr(verifier.subprocess, "run", fake_run)
    errors = verifier.probe_managed_runtime(python_exe)
    assert errors == ["managed runtime import failed: torch"]
    assert all("private provider" not in error for error in errors)
