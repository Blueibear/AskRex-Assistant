from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_windows_artifact_workflow_exercises_installed_release() -> None:
    workflow = (ROOT / ".github/workflows/windows-electron-artifact.yml").read_text(
        encoding="utf-8"
    )
    for required in (
        "runs-on: windows-latest",
        'python-version: "3.11.9"',
        'node-version: "20.19.1"',
        "Validate PowerShell artifact scripts",
        "npm ci",
        "python -m build --wheel",
        "npm run dist",
        "verify_electron_package_contents.py",
        "test_installed_electron_artifact.ps1",
        "scripts/background_lifecycle_harness.py",
        "scripts/background_lifecycle_fake_child.py",
        "askrex-windows-smoke-diagnostics",
    ):
        assert required in workflow


def test_signing_activates_only_when_certificate_secret_exists() -> None:
    """Authenticode signing is conditional on the certificate secret.

    Without WINDOWS_CSC_LINK the workflow must build the same unsigned
    artifact as before; with it, electron-builder receives CSC_LINK and the
    verification step fails closed if the produced installer is not Valid.
    """
    workflow = (ROOT / ".github/workflows/windows-electron-artifact.yml").read_text(
        encoding="utf-8"
    )
    for required in (
        "secrets.WINDOWS_CSC_LINK",
        "secrets.WINDOWS_CSC_KEY_PASSWORD",
        "IsNullOrWhiteSpace($env:WINDOWS_CSC_LINK)",
        "CSC_LINK=$env:WINDOWS_CSC_LINK",
        "Get-AuthenticodeSignature",
        "Verify Authenticode signature truthfully",
    ):
        assert required in workflow
    # The conditional export must come before the build so electron-builder
    # sees CSC_LINK, and verification must come after the build.
    assert workflow.index("CSC_LINK=$env:WINDOWS_CSC_LINK") < workflow.index("npm run dist")
    assert workflow.index("npm run dist") < workflow.index("Get-AuthenticodeSignature")
    # Signed builds must be timestamped so signatures outlive the cert.
    package_json = (ROOT / "gui/package.json").read_text(encoding="utf-8")
    assert "rfc3161TimeStampServer" in package_json


def test_release_automation_is_gated_by_windows_artifact() -> None:
    workflow = (ROOT / ".github/workflows/release-please.yml").read_text(encoding="utf-8")
    assert "uses: ./.github/workflows/windows-electron-artifact.yml" in workflow
    assert "needs: windows-electron-artifact" in workflow


def test_installed_artifact_harness_retries_identity_with_diagnostics() -> None:
    harness = (ROOT / "scripts/test_installed_electron_artifact.ps1").read_text(encoding="utf-8")
    assert "$attempt -le 3" in harness
    assert "failed after 3 attempts" in harness
    assert "$identityResult.Stdout.Trim()" in harness
    assert "$identityResult.Stderr.Trim()" in harness
    assert "Write-SmokeDiagnostics 'failure'" in harness


def test_installed_artifact_harness_uses_utf8_file_backed_stdin() -> None:
    harness = (ROOT / "scripts/test_installed_electron_artifact.ps1").read_text(encoding="utf-8")
    assert "System.Text.UTF8Encoding($false)" in harness
    assert "System.IO.File]::WriteAllText" in harness
    assert "RedirectStandardInput = $stdinPath" in harness
    assert "Start-Process @startProcessArguments" in harness
    assert "StandardInputEncoding" not in harness
    assert "StandardInput.BaseStream" not in harness


def test_installed_artifact_harness_uses_clean_reinstall_lifecycle() -> None:
    harness = (ROOT / "scripts/test_installed_electron_artifact.ps1").read_text(encoding="utf-8")
    phases = (
        "initial-install",
        "first-uninstall",
        "reinstall",
        "final-uninstall",
    )
    positions = [harness.index(phase) for phase in phases]
    assert positions == sorted(positions)
    assert "function Invoke-Uninstaller" in harness
    assert "[string]$InstallRoot" in harness
    assert "$uninstallCopyRoot = Join-Path ([System.IO.Path]::GetTempPath())" in harness
    assert "Copy-Item -LiteralPath $UninstallerPath -Destination $uninstallCopy -Force" in harness
    assert "Start-Process -FilePath $uninstallCopy" in harness
    assert """-ArgumentList @('/S', '/currentuser', "_?=$InstallRoot")""" in harness
    assert "Remove-Item -LiteralPath $uninstallCopyRoot -Recurse -Force" in harness
    assert "Start-Process -FilePath $UninstallerPath" not in harness
    assert harness.count("Invoke-Uninstaller $uninstaller $installPath") == 2
    assert "function Assert-Uninstalled" in harness


def test_installed_artifact_harness_pins_build_python_before_path_isolation() -> None:
    harness = (ROOT / "scripts/test_installed_electron_artifact.ps1").read_text(encoding="utf-8")
    resolve_position = harness.index("$buildPythonPath = (Get-Command $BuildPython")
    isolate_position = harness.index("$env:PATH = Join-Path $env:SystemRoot 'System32'")
    assert resolve_position < isolate_position
    assert harness.count("& $buildPythonPath") == 2


def test_installed_artifact_harness_does_not_turn_cleanup_locks_into_product_failure() -> None:
    harness = (ROOT / "scripts/test_installed_electron_artifact.ps1").read_text(encoding="utf-8")
    assert "function Remove-SmokeTestRoot" in harness
    assert "$attempt -le 10" in harness
    assert 'Write-Warning "Could not fully remove temporary smoke directory' in harness
    assert "Remove-SmokeTestRoot $testRoot" in harness


def test_background_lifecycle_fixtures_exist() -> None:
    assert (ROOT / "scripts/background_lifecycle_fake_child.py").is_file()
    assert (ROOT / "scripts/background_lifecycle_harness.py").is_file()


def test_background_lifecycle_fake_child_is_self_contained() -> None:
    fixture = (ROOT / "scripts/background_lifecycle_fake_child.py").read_text(encoding="utf-8")
    assert "from rex." not in fixture
    assert "import rex" not in fixture


def test_background_lifecycle_has_no_arbitrary_product_script_override() -> None:
    production_cli = (ROOT / "rex/background/cli.py").read_text(encoding="utf-8")
    assert "ASKREX_BACKGROUND_FAKE_CHILD_SCRIPT" not in production_cli


def test_installed_artifact_harness_proves_electron_background_survival() -> None:
    harness = (ROOT / "scripts/test_installed_electron_artifact.ps1").read_text(encoding="utf-8")
    for required in (
        "function Invoke-ElectronBackgroundSurvivalSmoke",
        "electron-background-survival",
        "detached supervisor status is unavailable after GUI exit",
        "detached supervisor is not alive after GUI exit",
        "remains live for uninstall verification",
    ):
        assert required in harness


def test_installed_artifact_harness_runs_deterministic_packaged_lifecycle_smoke() -> None:
    harness = (ROOT / "scripts/test_installed_electron_artifact.ps1").read_text(encoding="utf-8")
    for required in (
        "function Invoke-BackgroundLifecycleSmoke",
        "background-lifecycle-smoke",
        "background_lifecycle_harness.py",
        "background_lifecycle_fake_child.py",
        "packaged Windows artifact / deterministic child fakes",
        "python\\pythonw.exe",
        "'-I', '-m', 'rex.background.cli'",
    ):
        assert required in harness


def test_background_lifecycle_smoke_runs_after_electron_survival_and_before_uninstall() -> None:
    harness = (ROOT / "scripts/test_installed_electron_artifact.ps1").read_text(encoding="utf-8")
    positions = [
        harness.index("electron-ipc-smoke"),
        harness.index("Invoke-ElectronBackgroundSurvivalSmoke -RuntimeRoot"),
        harness.index("Invoke-BackgroundLifecycleSmoke -Resources"),
        harness.index("first-uninstall"),
    ]
    assert positions == sorted(positions)


def test_background_lifecycle_smoke_isolates_managed_runtime_from_checkout() -> None:
    harness = (ROOT / "scripts/test_installed_electron_artifact.ps1").read_text(encoding="utf-8")
    assert "import pathlib,sys,rex.background" in harness
    assert "rex.background.__file__" in harness
    assert "managed rex.background import escaped installed resources" in harness


def test_background_lifecycle_smoke_copies_fixtures_into_runtime_root() -> None:
    harness = (ROOT / "scripts/test_installed_electron_artifact.ps1").read_text(encoding="utf-8")
    assert "$harnessScript = Join-Path $RuntimeRoot 'background_lifecycle_harness.py'" in harness
    assert (
        "$fakeChildScript = Join-Path $RuntimeRoot 'background_lifecycle_fake_child.py'" in harness
    )
    assert "Copy-Item -LiteralPath $harnessSource -Destination $harnessScript -Force" in harness
    assert "Copy-Item -LiteralPath $fakeChildSource -Destination $fakeChildScript -Force" in harness


def test_background_lifecycle_smoke_proves_required_us124_task6_evidence() -> None:
    harness = (ROOT / "scripts/test_installed_electron_artifact.ps1").read_text(encoding="utf-8")
    for required in (
        "duplicate supervisor start was not rejected",
        "-ne 2",
        "fake-child supervisor never reached ready status",
        "status became unreadable after duplicate-start probe",
        "detached supervisor exited unexpectedly",
        "did not exit after an orderly stop request",
        "status still reports live after orderly stop",
    ):
        assert required in harness
    assert "$runtimePythonW = Join-Path $Resources 'python\\pythonw.exe'" in harness
    assert "$supervisorProcess = Start-Process -FilePath $runtimePythonW" in harness


def test_electron_background_survival_requires_gui_exit_before_probe() -> None:
    harness = (ROOT / "scripts/test_installed_electron_artifact.ps1").read_text(encoding="utf-8")
    assert "if (-not $app.WaitForExit(15000))" in harness
    assert "Electron process did not exit before background survival verification" in harness


def test_background_lifecycle_smoke_exercises_spaced_runtime_paths_with_safe_pythonw_argv() -> None:
    harness = (ROOT / "scripts/test_installed_electron_artifact.ps1").read_text(encoding="utf-8")
    assert "$lifecycleRuntimeRoot = Join-Path $testRoot 'Lifecycle Runtime With Spaces'" in harness
    assert (
        "Invoke-BackgroundLifecycleSmoke -Resources $resources -RuntimeRoot $lifecycleRuntimeRoot"
        in harness
    )
    assert (
        '$harnessArgumentString = "-I `"$harnessScript`" `"$RuntimeRoot`" `"$fakeChildScript`""'
        in harness
    )
    assert "Start-Process -FilePath $runtimePythonW -ArgumentList $harnessArgumentString" in harness
    assert "Start-Process -FilePath $runtimePythonW -ArgumentList $harnessArgs" not in harness


def test_nsis_uninstaller_stops_background_runtime_and_removes_startup_task() -> None:
    package = json.loads((ROOT / "gui/package.json").read_text(encoding="utf-8"))
    assert package["build"]["nsis"]["include"] == "nsis/installer.nsh"
    include = (ROOT / "gui/nsis/installer.nsh").read_text(encoding="utf-8")
    for required in (
        "!macro customUnInstall",
        "resources\\python\\python.exe",
        "rex.background.cli stop",
        "--wait-seconds 15",
        "AskRex Background Runtime",
        "schtasks.exe",
        "/End",
        "/Delete",
        "/F",
    ):
        assert required in include


def test_installed_artifact_harness_proves_startup_task_removed_by_uninstall() -> None:
    harness = (ROOT / "scripts/test_installed_electron_artifact.ps1").read_text(encoding="utf-8")
    assert "function Assert-BackgroundStartupTaskPresent" in harness
    assert "function Assert-BackgroundStartupTaskAbsent" in harness
    present = harness.index("    Assert-BackgroundStartupTaskPresent")
    first_uninstall = harness.index("Set-SmokePhase 'first-uninstall'")
    absent = harness.index("    Assert-BackgroundStartupTaskAbsent", present + 1)
    assert present < first_uninstall < absent


def test_uninstaller_uses_current_electron_userdata_name() -> None:
    package = json.loads((ROOT / "gui/package.json").read_text(encoding="utf-8"))
    include = (ROOT / "gui/nsis/installer.nsh").read_text(encoding="utf-8")
    assert f'$APPDATA\\{package["name"]}' in include
    main_sources = "\n".join(
        path.read_text(encoding="utf-8") for path in (ROOT / "gui/src/main").glob("*.ts")
    )
    assert "app.setName(" not in main_sources


def test_artifact_smoke_startup_paths_fit_windows_task_action_limit() -> None:
    harness = (ROOT / "scripts/test_installed_electron_artifact.ps1").read_text(encoding="utf-8")
    assert "('arx-' + [guid]::NewGuid().ToString('N').Substring(0, 8))" in harness

    from rex.background.windows_startup import build_schtasks_create_command

    base = Path(r"C:\Users\runneradmin\AppData\Local\Temp\arx-12345678")
    pythonw = base / "AskRex" / "resources" / "python" / "pythonw.exe"
    runtime = base / "Runtime"
    command = build_schtasks_create_command(
        "AskRex Background Runtime",
        pythonw,
        runtime,
        "artifact-ci-user",
        run_as_user=r"RUNNER\runneradmin",
    )
    action = command[command.index("/TR") + 1]
    assert len(action) <= 262


def test_nsis_uninstaller_fails_closed_when_startup_task_removal_cannot_be_confirmed() -> None:
    include = (ROOT / "gui/nsis/installer.nsh").read_text(encoding="utf-8")
    delete_at = include.index('/Delete /TN "AskRex Background Runtime" /F')
    failure_check = include.index("${If} $0 != 0", delete_at)
    verify_at = include.index("Schedule.Service", failure_check)
    abort_at = include.index(
        'Abort "AskRex could not confirm removal of the background startup task."', verify_at
    )
    assert delete_at < failure_check < verify_at < abort_at
    assert "($$_.Exception.HResult -band 0xFFFF) -eq 2" in include


def test_artifact_smoke_task_state_query_fails_closed_on_scheduler_errors() -> None:
    harness = (ROOT / "scripts/test_installed_electron_artifact.ps1").read_text(encoding="utf-8")
    assert "function Get-BackgroundStartupTaskState" in harness
    assert "Schedule.Service" in harness
    assert "($_.Exception.HResult -band 0xFFFF) -eq 2" in harness
    assert "return 'absent'" in harness
    assert "return 'present'" in harness
    assert "Could not query the AskRex background startup task." in harness
    assert "return $LASTEXITCODE -eq 0" not in harness


def test_real_electron_supervisor_remains_live_until_nsis_uninstall() -> None:
    harness = (ROOT / "scripts/test_installed_electron_artifact.ps1").read_text(encoding="utf-8")
    assert "$runtimeRoot = Join-Path $appData 'rex-gui'" in harness
    assert "$survivingSupervisorPid = Invoke-ElectronBackgroundSurvivalSmoke" in harness
    survival = harness[harness.index("function Invoke-ElectronBackgroundSurvivalSmoke") :]
    survival = survival[: survival.index("function Invoke-BackgroundLifecycleSmoke")]
    assert "rex.background.cli stop" not in survival
    first_uninstall = harness.index("Set-SmokePhase 'first-uninstall'")
    prior = harness[:first_uninstall]
    assert (
        "Stop-InstalledProcesses $installPath"
        not in prior[prior.index("Set-SmokePhase 'electron-background-survival'") :]
    )
    after = harness[first_uninstall:]
    assert "Get-Process -Id $survivingSupervisorPid" in after
    assert "background supervisor survived uninstall" in after.lower()


def test_artifact_smoke_task_cleanup_cannot_mask_primary_failure() -> None:
    harness = (ROOT / "scripts/test_installed_electron_artifact.ps1").read_text(encoding="utf-8")
    start = harness.index("function Remove-SmokeBackgroundStartupTask")
    end = harness.index("function Invoke-Installer", start)
    cleanup = harness[start:end]
    assert "try {\n        & $schtasks /End" in cleanup
    assert "try {\n        & $schtasks /Delete" in cleanup
    assert cleanup.count("catch {") >= 2
