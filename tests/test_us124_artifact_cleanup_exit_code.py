from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_cleanup_resets_ignored_schtasks_exit_code() -> None:
    harness = (ROOT / "scripts/test_installed_electron_artifact.ps1").read_text(encoding="utf-8")
    start = harness.index("function Remove-SmokeBackgroundStartupTask")
    end = harness.index("function Invoke-Installer", start)
    cleanup = harness[start:end]

    assert "$global:LASTEXITCODE = 0" in cleanup
