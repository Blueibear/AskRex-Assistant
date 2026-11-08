from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
GUI_DIR = REPO_ROOT / "gui"
VERIFY_SCRIPT = GUI_DIR / "tmp_verify_chat_stream.cjs"
ELECTRON_CMD = GUI_DIR / "node_modules" / ".bin" / "electron.cmd"
MAIN_BUNDLE = GUI_DIR / "dist-electron" / "main" / "index.js"


def _run_electron_verification(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run Electron without allowing a timed-out child tree to wedge pytest."""
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    with (
        tempfile.TemporaryFile(mode="w+", encoding="utf-8") as stdout_file,
        tempfile.TemporaryFile(mode="w+", encoding="utf-8") as stderr_file,
    ):
        try:
            process = subprocess.Popen(
                [str(ELECTRON_CMD), str(VERIFY_SCRIPT)],
                cwd=str(GUI_DIR),
                stdout=stdout_file,
                stderr=stderr_file,
                text=True,
                env=env,
                creationflags=creationflags,
            )
        except OSError as exc:
            pytest.skip(f"Electron verification unavailable: {exc}")

        timed_out = False
        try:
            returncode = process.wait(timeout=120)
        except subprocess.TimeoutExpired:
            timed_out = True
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            returncode = process.wait(timeout=15)

        stdout_file.seek(0)
        stderr_file.seek(0)
        stdout = stdout_file.read()
        stderr = stderr_file.read()

    if timed_out:
        pytest.fail(
            "Electron verification timed out after 120 seconds.\n"
            f"stdout: {stdout[:1000]}\n"
            f"stderr: {stderr[:1000]}"
        )
    return subprocess.CompletedProcess(
        [str(ELECTRON_CMD), str(VERIFY_SCRIPT)], returncode, stdout, stderr
    )


@pytest.mark.skipif(
    not ELECTRON_CMD.exists() or not MAIN_BUNDLE.exists(),
    reason="Electron binary or built main bundle not available",
)
def test_chat_streaming_works_in_electron(tmp_path: Path) -> None:
    """Electron chat page renders a typed streamed reply incrementally."""
    assert VERIFY_SCRIPT.exists(), f"Missing Electron verification script: {VERIFY_SCRIPT}"
    assert ELECTRON_CMD.exists(), f"Missing Electron binary: {ELECTRON_CMD}"
    assert MAIN_BUNDLE.exists(), f"Missing Electron main bundle: {MAIN_BUNDLE}"

    # The main process derives the development bridge runtime root from
    # ``app.getAppPath()`` rather than Electron's userData path.  Give this
    # harness a complete disposable root so it cannot restore a profile left
    # behind by another Electron test.
    runtime_root = tmp_path / "runtime"
    shutil.copytree(REPO_ROOT / "bridge", runtime_root / "bridge")
    profile_dir = runtime_root / "Memory" / "electron-test-user"
    profile_dir.mkdir(parents=True)
    (profile_dir / "core.json").write_text(
        json.dumps(
            {
                "name": "Electron Test User",
                "role": "",
                "user": "electron-test-user",
                "preferences": {},
            }
        ),
        encoding="utf-8",
    )

    # Regression sentinel: unrelated known users must not affect the
    # isolated runtime or be changed by the Electron process.
    external_runtime = tmp_path / "external-runtime"
    sentinel = external_runtime / "Memory" / "sentinel-user" / "core.json"
    sentinel.parent.mkdir(parents=True)
    sentinel_contents = '{"sentinel":"must remain unchanged"}\n'
    sentinel.write_text(sentinel_contents, encoding="utf-8")
    for user_id in ("external-user-one", "external-user-two"):
        external_profile = external_runtime / "Memory" / user_id
        external_profile.mkdir(parents=True)
        (external_profile / "core.json").write_text("{}\n", encoding="utf-8")

    app_data = tmp_path / "appdata"
    session_dir = app_data / "rex-ai"
    session_dir.mkdir(parents=True)
    (session_dir / "session.json").write_text(
        json.dumps({"active_user": "electron-test-user"}), encoding="utf-8"
    )
    env = os.environ.copy()
    env["LOCALAPPDATA"] = str(app_data)
    env["ASKREX_CHAT_STREAM_RUNTIME_ROOT"] = str(runtime_root)
    # The copied bridges deliberately use the current source tree; neither
    # real profiles nor configuration are copied into the fixture runtime.
    env["PYTHONPATH"] = str(REPO_ROOT)
    env["PATH"] = f"{Path(sys.executable).parent}{os.pathsep}{env.get('PATH', '')}"

    result = _run_electron_verification(env)

    assert sentinel.read_text(encoding="utf-8") == sentinel_contents

    stdout_lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert stdout_lines, f"Electron verification produced no stdout. stderr: {result.stderr[:500]}"

    payload = json.loads(stdout_lines[-1])
    assert result.returncode == 0 and payload.get("ok") is True, (
        "Electron chat verification failed.\n"
        f"stdout: {result.stdout[:1000]}\n"
        f"stderr: {result.stderr[:1000]}"
    )

    verification = payload["result"]
    assert verification["sawUserMessage"] is True
    assert verification["sawFirstPartial"] is True
    assert verification["sawSecondPartial"] is True
    assert verification["sawFinal"] is True
