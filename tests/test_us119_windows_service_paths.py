from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVICE_REGISTRATION_MARKERS = (
    "New-Service",
    "sc.exe create",
    "rex.windows_service install",
    "HandleCommandLine(",
)
RELATIVE_VENV_PYTHON_FRAGMENTS = (
    ".\\.venv\\scripts\\python.exe",
    "venv\\scripts\\python.exe",
)


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_rexspeak_launcher_is_rooted_at_script_location() -> None:
    script = _read("Start-RexSpeak.ps1")

    assert "$PSScriptRoot" in script
    assert "[System.IO.Path]::GetFullPath" in script
    assert "Resolve-Path ." not in script
    assert ".\\.venv\\Scripts\\python.exe" not in script
    assert "Test-Path -LiteralPath $VenvPython" in script
    assert "& $VenvPython -m waitress" in script


def test_rexspeak_launcher_requires_an_operator_supplied_api_key() -> None:
    script = _read("Start-RexSpeak.ps1")

    assert "[string]::IsNullOrWhiteSpace($env:REX_SPEAK_API_KEY)" in script
    assert "REX_SPEAK_API_KEY must be set" in script
    assert "YOUR-SECRET-HERE" not in script


def test_source_installer_roots_repo_paths_at_its_own_location() -> None:
    script = _read("install.ps1")

    assert "$PSScriptRoot" in script
    assert "$RepoDir = [System.IO.Path]::GetFullPath($PSScriptRoot)" in script
    assert '$VenvPython = Join-Path $VenvDir "Scripts\\python.exe"' in script


def test_lean_node_installer_normalizes_root_before_service_paths() -> None:
    script = _read("node_installers/install_windows.ps1")

    normalization = "$RexRoot = [System.IO.Path]::GetFullPath($RexRoot)"
    assert normalization in script
    assert script.index(normalization) < script.index("Test-Path $RexRoot")
    assert '$python = Join-Path $RexRoot "venv\\Scripts\\python.exe"' in script
    assert '$pip = Join-Path $RexRoot "venv\\Scripts\\pip.exe"' in script
    assert "Test-Path -LiteralPath $python" in script
    assert 'Write-Host "[DRY RUN] & `"$python`" -m rex.windows_service install"' in script
    assert 'Write-Host "[DRY RUN] & `"$python`" -m rex.windows_service start"' in script


def test_service_registration_sources_do_not_embed_relative_venv_python() -> None:
    source_suffixes = {".ps1", ".py", ".cmd", ".bat"}
    offenders: list[str] = []

    for path in REPO_ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in source_suffixes:
            continue
        relative = path.relative_to(REPO_ROOT)
        if any(part in {".git", ".worktrees", "archived"} for part in relative.parts):
            continue
        if relative.parts and relative.parts[0] == "tests":
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if not any(marker in text for marker in SERVICE_REGISTRATION_MARKERS):
            continue
        for line in text.splitlines():
            lowered = line.lower()
            if "join-path" in lowered:
                continue
            if any(fragment in lowered for fragment in RELATIVE_VENV_PYTHON_FRAGMENTS):
                offenders.append(str(relative))
                break

    assert offenders == []


def test_service_python_path_helper_normalizes_and_requires_existing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from rex.windows_service_paths import normalize_existing_executable

    install_root = tmp_path / "Rex Root With Spaces"
    executable = install_root / "venv" / "Scripts" / "python.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"test")

    monkeypatch.chdir(tmp_path)
    relative = executable.relative_to(tmp_path)
    resolved = normalize_existing_executable(relative)

    assert resolved == executable.resolve()
    assert resolved.is_absolute()

    with pytest.raises(FileNotFoundError):
        normalize_existing_executable(tmp_path / "missing" / "python.exe")


def test_windows_service_normalizes_child_interpreter_before_launch() -> None:
    service = _read("rex/windows_service.py")

    assert "from rex.windows_service_paths import normalize_existing_executable" in service
    assert "service_python = normalize_existing_executable(sys.executable)" in service
    assert "str(service_python)," in service


if os.name == "nt":

    def test_lean_node_dry_run_emits_absolute_quoted_registration_from_other_cwd(
        tmp_path: Path,
    ) -> None:
        powershell = shutil.which("powershell") or shutil.which("pwsh")
        assert powershell is not None, "PowerShell is required for Windows service-path validation"

        script = REPO_ROOT / "node_installers" / "install_windows.ps1"
        relative_root = Path("nested") / "Rex Root With Spaces"
        expected_root = (tmp_path / relative_root).resolve()
        expected_python = expected_root / "venv" / "Scripts" / "python.exe"

        result = subprocess.run(
            [
                powershell,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
                "-RexRoot",
                str(relative_root),
                "-DryRun",
            ],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 0, result.stderr
        expected_install = f'[DRY RUN] & "{expected_python}" -m rex.windows_service install'
        expected_start = f'[DRY RUN] & "{expected_python}" -m rex.windows_service start'
        assert expected_install in result.stdout
        assert expected_start in result.stdout
