"""Behavior tests for the deterministic Core/Voice Agent fake used by the
packaged installed-artifact lifecycle smoke
(``scripts/background_lifecycle_fake_child.py``).

These run the fixture as a real subprocess against the dev interpreter to
prove its file-protocol behavior before it is relied on from the Windows
installed-artifact PowerShell harness against the packaged managed runtime.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from rex.background.paths import BackgroundPaths

FIXTURE = Path(__file__).resolve().parents[2] / "scripts" / "background_lifecycle_fake_child.py"
PYTHON = getattr(sys, "_base_executable", sys.executable)


def _wait_for(predicate, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def test_fake_core_writes_valid_endpoint_and_exits_on_stop_file(tmp_path: Path) -> None:
    paths = BackgroundPaths.from_runtime_root(tmp_path)
    process = subprocess.Popen([PYTHON, str(FIXTURE), "core", str(tmp_path)])
    try:
        assert _wait_for(lambda: paths.core_endpoint_file.exists())
        payload = json.loads(paths.core_endpoint_file.read_text(encoding="utf-8"))
        assert payload["host"] == "127.0.0.1"
        assert payload["pid"] == process.pid
        assert isinstance(payload["token"], str) and len(payload["token"]) >= 32

        paths.state_dir.mkdir(parents=True, exist_ok=True)
        paths.stop_file.touch()
        assert process.wait(timeout=10.0) == 0
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5.0)


def test_fake_voice_agent_publishes_ready_heartbeat_and_exits_on_stop_file(
    tmp_path: Path,
) -> None:
    paths = BackgroundPaths.from_runtime_root(tmp_path)
    process = subprocess.Popen([PYTHON, str(FIXTURE), "voice_agent", str(tmp_path)])
    try:
        assert _wait_for(lambda: paths.voice_agent_health_file.exists())
        payload = json.loads(paths.voice_agent_health_file.read_text(encoding="utf-8"))
        assert payload["component"] == "voice_agent"
        assert payload["state"] == "ready"
        assert payload["pid"] == process.pid

        first_observed_at = payload["observed_at"]

        def _heartbeat_advanced() -> bool:
            current = json.loads(paths.voice_agent_health_file.read_text(encoding="utf-8"))
            return current["observed_at"] > first_observed_at

        assert _wait_for(_heartbeat_advanced, timeout=5.0)

        paths.state_dir.mkdir(parents=True, exist_ok=True)
        paths.stop_file.touch()
        assert process.wait(timeout=10.0) == 0
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5.0)


def test_fake_child_rejects_unknown_role(tmp_path: Path) -> None:
    result = subprocess.run(
        [PYTHON, str(FIXTURE), "not_a_role", str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=10.0,
        check=False,
    )
    assert result.returncode == 2


def test_fake_child_rejects_missing_arguments() -> None:
    result = subprocess.run(
        [PYTHON, str(FIXTURE)],
        capture_output=True,
        text=True,
        timeout=10.0,
        check=False,
    )
    assert result.returncode == 2
