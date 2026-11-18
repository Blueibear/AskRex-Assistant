from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.dev_orchestrator import validation
from scripts.dev_orchestrator.types import OrchestratorConfig


def _config(tmp_path: Path, command: list[str]) -> OrchestratorConfig:
    repo = tmp_path / "backend"
    repo.mkdir()
    return OrchestratorConfig(
        coordination_root=tmp_path / "coordination",
        backend_root=repo,
        mobile_root=tmp_path / "mobile",
        metadata={
            "iteration_validation": {
                "backend": {"gates": [{"name": "diagnostic gate", "command": command}]}
            }
        },
    )


def _records(config: OrchestratorConfig) -> list[Path]:
    return list((config.coordination_root / "validation-failures").glob("*/manifest.json"))


def _assert_stream(root: Path, artifact: dict, expected: bytes) -> None:
    saved = (root / artifact["path"]).read_bytes()
    assert saved == expected
    assert artifact["bytes"] == len(saved)
    assert artifact["sha256"] == hashlib.sha256(saved).hexdigest()


def test_pytest_traceback_survives_long_warning_tail_with_complete_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(
        tmp_path,
        [sys.executable, "-m", "pytest", "-q", "--tb=long", "test_registration_failure.py"],
    )
    assert config.backend_root is not None
    (config.backend_root / "test_registration_failure.py").write_text(
        "import warnings\n\n"
        "def test_registration():\n"
        "    warnings.warn('NOISY_WARNING_TAIL_' + 'w' * 30000)\n"
        "    assert False, 'REGISTRATION_TRACEBACK_SENTINEL'\n",
        encoding="utf-8",
    )
    captured: list[subprocess.CompletedProcess] = []
    actual_run = subprocess.run

    def capture(command, **kwargs):
        result = actual_run(command, **kwargs)
        captured.append(result)
        return result

    monkeypatch.setattr(validation.subprocess, "run", capture)
    report = validation.run_iteration_validation(config, "backend", "B-TRACEBACK")

    assert report.passed is False
    assert report.system_error == ""
    assert report.receipt is None
    assert "REGISTRATION_TRACEBACK_SENTINEL" in report.feedback
    assert "test_registration" in report.feedback
    assert len(report.feedback) <= 5000
    assert "output truncated" in report.feedback
    assert not (config.coordination_root / "validation" / "backend-B-TRACEBACK.json").exists()

    [record] = _records(config)
    manifest = json.loads(record.read_text(encoding="utf-8"))
    [result] = captured
    assert isinstance(result.stdout, bytes)
    assert len(result.stdout) > 30000
    assert (
        manifest["command"]
        == config.metadata["iteration_validation"]["backend"]["gates"][0]["command"]
    )
    assert manifest["cwd"] == str(config.backend_root.resolve())
    assert manifest["exit_code"] == 1
    assert manifest["timed_out"] is False
    assert manifest["role"] == "backend"
    assert manifest["task_id"] == "B-TRACEBACK"
    _assert_stream(config.coordination_root, manifest["stdout"], result.stdout)
    _assert_stream(config.coordination_root, manifest["stderr"], result.stderr)
    assert record.relative_to(config.coordination_root).as_posix() in report.feedback
    assert hashlib.sha256(record.read_bytes()).hexdigest() in report.feedback
    assert manifest["stdout"]["sha256"] in report.feedback


def test_generic_failure_preserves_raw_bytes_and_distinct_attempts(tmp_path: Path) -> None:
    stdout = b"startup \xff\r\n"
    stderr = b"FATAL: BOOT_REJECTED\r\n"
    command = [
        sys.executable,
        "-c",
        f"import sys; sys.stdout.buffer.write({stdout!r}); "
        f"sys.stderr.buffer.write({stderr!r}); sys.exit(7)",
    ]
    config = _config(tmp_path, command)
    receipt = config.coordination_root / "validation" / "backend-B-RAW.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text('{"previous_success": true}', encoding="utf-8")

    first = validation.run_iteration_validation(config, "backend", "B-RAW")
    assert first.passed is False
    assert first.system_error == ""
    assert first.receipt is None
    assert "Exit code: 7" in first.feedback
    assert "BOOT_REJECTED" in first.feedback
    assert "startup \ufffd" in first.feedback
    assert not receipt.exists()
    [first_path] = _records(config)
    first_bytes = first_path.read_bytes()

    second = validation.run_iteration_validation(config, "backend", "B-RAW")
    assert second.passed is False
    assert second.system_error == ""
    records = _records(config)
    assert len(records) == 2
    assert first_path.read_bytes() == first_bytes
    for path in records:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        assert manifest["attempt_id"] == path.parent.name
        assert manifest["command"] == command
        assert manifest["exit_code"] == 7
        assert manifest["timed_out"] is False
        _assert_stream(config.coordination_root, manifest["stdout"], stdout)
        _assert_stream(config.coordination_root, manifest["stderr"], stderr)


@pytest.mark.parametrize("failed_artifact", ["stdout.log", "stderr.log", "manifest.json"])
def test_failure_artifact_write_error_remains_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed_artifact: str
) -> None:
    config = _config(
        tmp_path,
        [sys.executable, "-c", "import sys; print('REAL-GATE-FAILURE'); sys.exit(7)"],
    )
    receipt = config.coordination_root / "validation" / "backend-B-WRITE.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text('{"previous_success": true}', encoding="utf-8")
    actual_open = Path.open

    def denied(path, *args, **kwargs):
        if path.name == failed_artifact or (
            failed_artifact == "manifest.json" and path.name.startswith(".manifest.json.")
        ):
            raise PermissionError("failure evidence storage denied")
        return actual_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", denied)
    report = validation.run_iteration_validation(config, "backend", "B-WRITE")

    assert report.passed is False
    assert report.receipt is None
    assert "failure evidence" in report.system_error
    assert "7" in report.system_error
    assert not receipt.exists()
    assert not _records(config)
