"""Tests for rex doctor command."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from rex.doctor import (
    CheckResult,
    DiagnosticsReport,
    Status,
    check_binary,
    check_config_file,
    check_config_permissions,
    check_credential_vault_backend,
    check_env_file,
    check_environment_variables,
    check_gpu_availability,
    check_package_installation,
    check_python_version,
    run_diagnostics,
)


class TestCheckResult:
    """Tests for CheckResult dataclass."""

    def test_check_result_creation(self):
        """Test creating a CheckResult."""
        result = CheckResult(
            name="Test Check",
            status=Status.OK,
            message="All good",
            details="Extra info",
        )
        assert result.name == "Test Check"
        assert result.status == Status.OK
        assert result.message == "All good"
        assert result.details == "Extra info"

    def test_check_result_default_details(self):
        """Test CheckResult with default empty details."""
        result = CheckResult(
            name="Test",
            status=Status.WARNING,
            message="Warning message",
        )
        assert result.details == ""


class TestDiagnosticsReport:
    """Tests for DiagnosticsReport."""

    def test_empty_report(self):
        """Test empty report has no errors or warnings."""
        report = DiagnosticsReport()
        assert not report.has_errors()
        assert not report.has_warnings()
        assert report.error_count == 0
        assert report.warning_count == 0

    def test_report_with_ok_results(self):
        """Test report with only OK results."""
        report = DiagnosticsReport()
        report.add(CheckResult("Check 1", Status.OK, "Good"))
        report.add(CheckResult("Check 2", Status.OK, "Also good"))
        assert not report.has_errors()
        assert not report.has_warnings()

    def test_report_with_warnings(self):
        """Test report detects warnings."""
        report = DiagnosticsReport()
        report.add(CheckResult("Check 1", Status.OK, "Good"))
        report.add(CheckResult("Check 2", Status.WARNING, "Minor issue"))
        assert not report.has_errors()
        assert report.has_warnings()
        assert report.warning_count == 1

    def test_report_with_errors(self):
        """Test report detects errors."""
        report = DiagnosticsReport()
        report.add(CheckResult("Check 1", Status.OK, "Good"))
        report.add(CheckResult("Check 2", Status.ERROR, "Critical issue"))
        assert report.has_errors()
        assert report.error_count == 1


class MockVersionInfo:
    """Mock for sys.version_info with named attributes."""

    def __init__(self, major: int, minor: int, micro: int):
        self.major = major
        self.minor = minor
        self.micro = micro

    def __lt__(self, other):
        return (self.major, self.minor, self.micro) < other

    def __le__(self, other):
        return (self.major, self.minor, self.micro) <= other

    def __gt__(self, other):
        return (self.major, self.minor, self.micro) > other

    def __ge__(self, other):
        return (self.major, self.minor, self.micro) >= other


class TestPythonVersionCheck:
    """Tests for Python version checking."""

    def test_current_version_passes(self):
        """Test that current Python version passes check."""
        result = check_python_version()
        # Current version should at least be OK or WARNING (not ERROR)
        assert result.status in (Status.OK, Status.WARNING)
        assert "Python" in result.name

    def test_old_version_fails(self):
        """Test that Python 3.8 fails check."""
        with patch("rex.doctor.sys.version_info", MockVersionInfo(3, 8, 0)):
            result = check_python_version()
            assert result.status == Status.ERROR
            assert "3.8" in result.message

    def test_python_39_warning(self):
        """Test that Python 3.9 is not supported (policy: 3.11 only)."""
        with patch("rex.doctor.sys.version_info", MockVersionInfo(3, 9, 0)):
            result = check_python_version()
            assert result.status == Status.ERROR
            assert "3.9" in result.message

    def test_python_311_ok(self):
        """Test that Python 3.11 is OK."""
        with patch("rex.doctor.sys.version_info", MockVersionInfo(3, 11, 0)):
            result = check_python_version()
            assert result.status == Status.OK


class TestConfigFileCheck:
    """Tests for config file checking."""

    def test_config_not_found(self):
        """Test when config file doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = check_config_file(Path(tmpdir))
            assert result.status in (Status.WARNING, Status.ERROR)

    def test_config_found(self):
        """Test when config file exists and is valid."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "config"
            config_dir.mkdir()
            config_file = config_dir / "rex_config.json"
            config_file.write_text('{"test": true}')

            result = check_config_file(Path(tmpdir))
            assert result.status == Status.OK

    def test_config_invalid_json(self):
        """Test when config file has invalid JSON."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "config"
            config_dir.mkdir()
            config_file = config_dir / "rex_config.json"
            config_file.write_text("invalid json {{{")

            result = check_config_file(Path(tmpdir))
            assert result.status == Status.ERROR
            assert "invalid" in result.message.lower()

    def test_example_exists_but_not_config(self):
        """Test when only example config exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "config"
            config_dir.mkdir()
            example_file = config_dir / "rex_config.example.json"
            example_file.write_text('{"example": true}')

            result = check_config_file(Path(tmpdir))
            assert result.status == Status.WARNING
            assert "example" in result.message.lower()

    def test_none_root(self):
        """Test when root is None."""
        result = check_config_file(None)
        assert result.status == Status.WARNING


class TestEnvFileCheck:
    """Tests for .env file checking."""

    def test_env_not_found(self):
        """Test when .env file doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = check_env_file(Path(tmpdir))
            assert result.status == Status.WARNING

    def test_env_found(self):
        """Test when .env file exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir) / ".env"
            env_file.write_text("TEST_VAR=value")

            result = check_env_file(Path(tmpdir))
            assert result.status == Status.OK

    def test_example_exists_but_not_env(self):
        """Test when only .env.example exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            example_file = Path(tmpdir) / ".env.example"
            example_file.write_text("EXAMPLE_VAR=value")

            result = check_env_file(Path(tmpdir))
            assert result.status == Status.WARNING
            assert "example" in result.message.lower()


class TestCredentialVaultBackendCheck:
    """Tests for platform credential-vault diagnostics."""

    def test_macos_keychain_backend_reported_available(self):
        with (
            patch("rex.credential_vault.get_credential_vault", return_value=object()),
            patch("rex.doctor.sys.platform", "darwin"),
        ):
            result = check_credential_vault_backend()
        assert result.status == Status.OK
        assert "macos keychain" in result.message.lower()

    def test_unavailable_vault_is_actionable_warning(self):
        from rex.credential_vault import VaultUnavailableError

        with patch(
            "rex.credential_vault.get_credential_vault",
            side_effect=VaultUnavailableError("unsupported platform"),
        ):
            result = check_credential_vault_backend()
        assert result.status == Status.WARNING
        assert "unsupported platform" in result.details


class TestEnvironmentVariablesCheck:
    """Tests for environment variables checking."""

    def test_no_api_keys(self):
        """Test when no API keys are set."""
        with patch.dict(os.environ, {}, clear=True):
            result = check_environment_variables()
            assert result.status == Status.INFO
            assert "plaintext" in result.message.lower()

    def test_some_api_keys(self):
        """Test when some API keys are set."""
        with patch.dict(
            os.environ,
            {"OPENAI_API_KEY": "sk-test123"},
            clear=True,
        ):
            result = check_environment_variables()
            assert result.status in (Status.OK, Status.INFO)

    def test_all_api_keys(self):
        """Test when all common API keys are set."""
        env_vars = {
            "OPENAI_API_KEY": "sk-test",
            "OLLAMA_API_KEY": "ollama-test",
            "BRAVE_API_KEY": "brave-test",
            "SERPAPI_KEY": "serp-test",
            "GOOGLE_API_KEY": "google-test",
            "HA_TOKEN": "ha-test",
        }
        with patch.dict(os.environ, env_vars, clear=True):
            result = check_environment_variables()
            assert result.status == Status.OK


class TestBinaryCheck:
    """Tests for binary dependency checking."""

    def test_python_binary_found(self):
        """Test checking for Python binary (should always exist)."""
        result = check_binary("python3", "testing")
        # On some systems it might be python instead of python3
        if result.status != Status.OK:
            result = check_binary("python", "testing")
        assert result.status == Status.OK

    def test_nonexistent_binary(self):
        """Test checking for nonexistent binary."""
        result = check_binary("definitely_not_a_real_binary_12345", "testing")
        assert result.status == Status.WARNING


class TestConfigPermissionsCheck:
    """Tests for config permissions checking."""

    def test_none_root(self):
        """Test when root is None."""
        result = check_config_permissions(None)
        assert result.status == Status.INFO

    def test_no_env_file(self):
        """Test when .env doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = check_config_permissions(Path(tmpdir))
            assert result.status == Status.OK


class TestPackageInstallationCheck:
    """Tests for package installation checking."""

    def test_rex_installed(self):
        """Test that rex package is detected as installed."""
        result = check_package_installation()
        # Should be OK or WARNING depending on installation state
        assert result.status in (Status.OK, Status.WARNING)


class TestGpuAvailabilityCheck:
    """Tests for GPU availability checking."""

    def test_gpu_check_runs(self):
        """Test that GPU check runs without error."""
        result = check_gpu_availability()
        # Should not error, just report available/unavailable/skipped
        assert result.status in (Status.OK, Status.INFO, Status.WARNING)


class TestRunDiagnostics:
    """Tests for the main run_diagnostics function."""

    def test_run_diagnostics_returns_int(self):
        """Test that run_diagnostics returns an integer exit code."""
        exit_code = run_diagnostics(verbose=False)
        assert isinstance(exit_code, int)
        assert exit_code in (0, 1)

    def test_run_diagnostics_verbose(self):
        """Test that verbose mode works."""
        exit_code = run_diagnostics(verbose=True)
        assert isinstance(exit_code, int)

    def test_run_diagnostics_includes_smart_speaker_details(self, monkeypatch, capsys):
        """Smart speaker listings are printed even without verbose mode."""
        ok = CheckResult(name="OK", status=Status.OK, message="ok")
        smart = CheckResult(
            name="Smart Speakers",
            status=Status.OK,
            message="1 smart speaker(s) discovered",
            details="- Sonos: Office (Era 100) @ 192.168.1.40",
        )

        monkeypatch.setattr("rex.doctor._find_project_root", lambda: None)
        monkeypatch.setattr("rex.doctor.check_python_version", lambda: ok)
        monkeypatch.setattr("rex.doctor.check_package_installation", lambda: ok)
        monkeypatch.setattr("rex.doctor.check_config_file", lambda root: ok)
        monkeypatch.setattr("rex.doctor.check_env_file", lambda root: ok)
        monkeypatch.setattr("rex.doctor.check_environment_variables", lambda: ok)
        monkeypatch.setattr("rex.doctor.check_config_permissions", lambda root: ok)
        monkeypatch.setattr("rex.doctor.check_core_dependencies", lambda: [ok])
        monkeypatch.setattr("rex.doctor.check_audio_input_device", lambda: ok)
        monkeypatch.setattr("rex.doctor.check_audio_output_device", lambda: ok)
        monkeypatch.setattr("rex.doctor.check_smart_speakers", lambda: smart)
        monkeypatch.setattr("rex.doctor.check_lm_studio_reachability", lambda: ok)
        monkeypatch.setattr("rex.doctor.check_external_dependencies", lambda: [ok])
        monkeypatch.setattr("rex.doctor.check_gpu_availability", lambda: ok)
        monkeypatch.setattr("rex.doctor.check_config_types", lambda root: ok)
        monkeypatch.setattr("rex.doctor.check_ffmpeg_for_tts", lambda: ok)
        monkeypatch.setattr("rex.doctor.check_wakeword_config", lambda: ok)
        monkeypatch.setattr("rex.doctor.check_stt_backend", lambda: ok)
        monkeypatch.setattr("rex.doctor.check_stt_warmup", lambda: ok)
        monkeypatch.setattr("rex.doctor.check_xtts_transformers_compat", lambda: ok)

        assert run_diagnostics(verbose=False) == 0
        output = capsys.readouterr().out

        assert "Smart Speakers" in output
        assert "Office (Era 100)" in output


class TestDoctorCLI:
    """Integration tests for rex doctor CLI."""

    def test_doctor_via_module(self):
        """Test running rex doctor via python -m rex doctor."""
        result = subprocess.run(
            [sys.executable, "-m", "rex", "doctor"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        # Check that it ran and produced output
        assert "Rex Doctor" in result.stdout or "Rex Doctor" in result.stderr
        # Should exit with 0 or 1
        assert result.returncode in (0, 1)

    def test_doctor_verbose(self):
        """Test running rex doctor with verbose flag."""
        result = subprocess.run(
            [sys.executable, "-m", "rex", "doctor", "-v"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert "Rex Doctor" in result.stdout
        # Verbose should show more output
        assert result.returncode in (0, 1)

    def test_doctor_module_direct(self):
        """Test running doctor.py directly as module."""
        result = subprocess.run(
            [sys.executable, "-m", "rex.doctor"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert "Rex Doctor" in result.stdout
        assert result.returncode in (0, 1)


class TestDoctorWithMockEnvironment:
    """Tests with mocked environment for predictable results."""

    def test_all_checks_pass(self):
        """Test scenario where all checks pass."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)

            # Create valid config structure
            config_dir = tmppath / "config"
            config_dir.mkdir()
            (config_dir / "rex_config.json").write_text('{"test": true}')
            (tmppath / ".env").write_text("TEST=value")
            (tmppath / "pyproject.toml").write_text("[project]")

            # Mock environment with API keys
            env_vars = {
                "OPENAI_API_KEY": "sk-test",
                "BRAVE_API_KEY": "brave-test",
            }

            with patch.dict(os.environ, env_vars):
                # The actual test
                result = check_config_file(tmppath)
                assert result.status == Status.OK

                result = check_env_file(tmppath)
                assert result.status == Status.OK

    def test_missing_config_detected(self):
        """Test that missing config is properly detected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = check_config_file(Path(tmpdir))
            assert result.status in (Status.WARNING, Status.ERROR)
