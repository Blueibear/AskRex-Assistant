"""CLI contract tests for the persistent Rex background runtime."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from rex.background import cli as background_cli
from rex.background.paths import BackgroundPaths
from rex.background.types import ComponentHealth, HealthState, RuntimeHealth


def _health() -> RuntimeHealth:
    observed_at = time.time()
    return RuntimeHealth(
        core=ComponentHealth("core", HealthState.READY, None, observed_at, 111),
        voice_agent=ComponentHealth(
            "voice_agent", HealthState.DEGRADED, "core_unavailable", observed_at, 222
        ),
        supervisor_pid=os.getpid(),
        observed_at=observed_at,
    )


def test_status_emits_machine_readable_content_free_json(tmp_path: Path, capsys) -> None:
    paths = BackgroundPaths.from_runtime_root(tmp_path)
    paths.state_dir.mkdir(parents=True)
    health = _health()
    paths.health_file.write_text(json.dumps(health.to_dict()) + "\n", encoding="utf-8")

    assert background_cli.main(["status", "--runtime-root", str(tmp_path)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == health.to_dict()
    serialized = json.dumps(payload).lower()
    for forbidden in ("transcript", "prompt", "user_id", "credential", "tool_result"):
        assert forbidden not in serialized


def test_missing_status_is_truthful_unavailable(tmp_path: Path, capsys) -> None:
    assert background_cli.main(["status", "--runtime-root", str(tmp_path)]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["core"]["state"] == "unavailable"
    assert payload["voice_agent"]["state"] == "unavailable"
    assert payload["core"]["detail_code"] == "health_unavailable"


def test_stop_requests_orderly_supervisor_shutdown(tmp_path: Path, capsys) -> None:
    paths = BackgroundPaths.from_runtime_root(tmp_path)
    assert background_cli.main(["stop", "--runtime-root", str(tmp_path)]) == 0
    assert paths.stop_file.exists()
    assert json.loads(capsys.readouterr().out) == {"ok": True, "requested": True}


def test_supervisor_child_commands_use_absolute_python(tmp_path: Path) -> None:
    paths = BackgroundPaths.from_runtime_root(tmp_path)
    supervisor = background_cli.build_supervisor(paths, user_id="james")
    assert Path(supervisor._core.spec.argv[0]).is_absolute()
    assert Path(supervisor._voice.spec.argv[0]).is_absolute()
    assert supervisor._core.spec.argv[1:4] == ("-m", "rex.background.cli", "core")


def test_top_level_cli_registers_background_status(tmp_path: Path) -> None:
    from rex.cli import create_parser

    args = create_parser().parse_args(["background", "status", "--runtime-root", str(tmp_path)])
    assert args.command == "background"
    assert args.background_command == "status"
    assert args.runtime_root == str(tmp_path)
    assert callable(args.func)


def test_status_rejects_nested_private_fields(tmp_path: Path, capsys) -> None:
    paths = BackgroundPaths.from_runtime_root(tmp_path)
    paths.state_dir.mkdir(parents=True)
    payload = _health().to_dict()
    payload["core"]["transcript"] = "private words"
    paths.health_file.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    assert background_cli.main(["status", "--runtime-root", str(tmp_path)]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["core"]["state"] == "unavailable"
    assert "private words" not in json.dumps(result).lower()


def test_status_rejects_dead_supervisor_snapshot(tmp_path: Path, capsys, monkeypatch) -> None:
    paths = BackgroundPaths.from_runtime_root(tmp_path)
    paths.state_dir.mkdir(parents=True)
    payload = _health().to_dict()
    payload["supervisor_pid"] = 777
    paths.health_file.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    monkeypatch.setattr(background_cli, "_pid_is_alive", lambda pid: False, raising=False)

    assert background_cli.main(["status", "--runtime-root", str(tmp_path)]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["core"]["state"] == "unavailable"
    assert result["voice_agent"]["state"] == "unavailable"


def test_status_rejects_unbounded_detail_code_content(tmp_path: Path, capsys) -> None:
    paths = BackgroundPaths.from_runtime_root(tmp_path)
    paths.state_dir.mkdir(parents=True)
    payload = _health().to_dict()
    payload["core"]["detail_code"] = "secret transcript words"
    paths.health_file.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    assert background_cli.main(["status", "--runtime-root", str(tmp_path)]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["core"]["state"] == "unavailable"
    assert "secret transcript words" not in json.dumps(result).lower()


def test_status_accepts_bounded_restart_backoff_detail_code(tmp_path: Path, capsys) -> None:
    paths = BackgroundPaths.from_runtime_root(tmp_path)
    paths.state_dir.mkdir(parents=True)
    payload = _health().to_dict()
    payload["core"]["state"] = "starting"
    payload["core"]["detail_code"] = "restart_backoff"
    payload["core"]["pid"] = None
    paths.health_file.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    assert background_cli.main(["status", "--runtime-root", str(tmp_path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["core"]["state"] == "starting"
    assert result["core"]["detail_code"] == "restart_backoff"


def test_status_rejects_stale_health_snapshot(tmp_path: Path, capsys) -> None:
    paths = BackgroundPaths.from_runtime_root(tmp_path)
    paths.state_dir.mkdir(parents=True)
    payload = _health().to_dict()
    payload["observed_at"] = time.time() - 60.0
    payload["core"]["observed_at"] = payload["observed_at"]
    payload["voice_agent"]["observed_at"] = payload["observed_at"]
    paths.health_file.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    assert background_cli.main(["status", "--runtime-root", str(tmp_path)]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["core"]["state"] == "unavailable"
    assert result["voice_agent"]["state"] == "unavailable"


def test_status_accepts_bounded_stop_failed_detail_code(tmp_path: Path, capsys) -> None:
    paths = BackgroundPaths.from_runtime_root(tmp_path)
    paths.state_dir.mkdir(parents=True)
    payload = _health().to_dict()
    payload["voice_agent"]["state"] = "failed"
    payload["voice_agent"]["detail_code"] = "stop_failed"
    payload["voice_agent"]["pid"] = None
    paths.health_file.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    assert background_cli.main(["status", "--runtime-root", str(tmp_path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["voice_agent"]["state"] == "failed"
    assert result["voice_agent"]["detail_code"] == "stop_failed"


def test_status_rejects_nan_aggregate_timestamp(tmp_path: Path, capsys) -> None:
    paths = BackgroundPaths.from_runtime_root(tmp_path)
    paths.state_dir.mkdir(parents=True)
    payload = _health().to_dict()
    payload["observed_at"] = float("nan")
    paths.health_file.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    assert background_cli.main(["status", "--runtime-root", str(tmp_path)]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["core"]["state"] == "unavailable"


def test_status_rejects_nan_component_timestamp(tmp_path: Path, capsys) -> None:
    paths = BackgroundPaths.from_runtime_root(tmp_path)
    paths.state_dir.mkdir(parents=True)
    payload = _health().to_dict()
    payload["voice_agent"]["observed_at"] = float("nan")
    paths.health_file.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    assert background_cli.main(["status", "--runtime-root", str(tmp_path)]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["voice_agent"]["state"] == "unavailable"


def test_status_rejects_oversized_supervisor_pid_without_raising(tmp_path: Path, capsys) -> None:
    paths = BackgroundPaths.from_runtime_root(tmp_path)
    paths.state_dir.mkdir(parents=True)
    payload = _health().to_dict()
    payload["supervisor_pid"] = 2**200
    paths.health_file.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    assert background_cli.main(["status", "--runtime-root", str(tmp_path)]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["core"]["state"] == "unavailable"
    assert result["voice_agent"]["state"] == "unavailable"


def test_cli_import_does_not_preload_voice_runtime() -> None:
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import rex.background.cli; raise SystemExit(int('rex.voice_loop' in sys.modules))",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_packaged_supervisor_configures_runtime_environment_before_build(
    monkeypatch, tmp_path: Path
) -> None:
    captured: dict[str, str | None] = {}

    class _Runtime:
        def run(self) -> None:
            return None

    def _build(_paths, **_kwargs):
        import os

        for key in (
            "ASKREX_PACKAGED",
            "ASKREX_RUNTIME_DIR",
            "ASKREX_CONFIG_PATH",
            "ASKREX_ENV_PATH",
            "ASKREX_PROFILES_DIR",
            "REX_DATA_DIR",
            "ASKREX_HOUSEHOLD_DATA_DIR",
            "ASKREX_USERS_DATA_DIR",
            "ASKREX_MEMORY_DIR",
            "REX_ALLOW_PLAINTEXT_CREDENTIAL_FALLBACK",
        ):
            captured[key] = os.environ.get(key)
        return _Runtime()

    monkeypatch.setenv("REX_ALLOW_PLAINTEXT_CREDENTIAL_FALLBACK", "1")
    monkeypatch.setattr(background_cli, "build_supervisor", _build)
    root = tmp_path.resolve()

    assert (
        background_cli.main(
            ["supervisor", "--runtime-root", str(root), "--user", "james", "--packaged"]
        )
        == 0
    )
    assert captured == {
        "ASKREX_PACKAGED": "1",
        "ASKREX_RUNTIME_DIR": str(root),
        "ASKREX_CONFIG_PATH": str(root / "config" / "rex_config.json"),
        "ASKREX_ENV_PATH": str(root / ".env"),
        "ASKREX_PROFILES_DIR": str(root / "profiles"),
        "REX_DATA_DIR": str(root / "data"),
        "ASKREX_HOUSEHOLD_DATA_DIR": str(root / "data" / "household"),
        "ASKREX_USERS_DATA_DIR": str(root / "data" / "users"),
        "ASKREX_MEMORY_DIR": str(root / "Memory"),
        "REX_ALLOW_PLAINTEXT_CREDENTIAL_FALLBACK": None,
    }


def test_cli_import_does_not_preload_runtime_config() -> None:
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import rex.background.cli; raise SystemExit(int('rex.config' in sys.modules))",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_status_accepts_bounded_listening_paused_detail_code(tmp_path: Path, capsys) -> None:
    paths = BackgroundPaths.from_runtime_root(tmp_path)
    paths.state_dir.mkdir(parents=True)
    payload = _health().to_dict()
    payload["voice_agent"]["state"] = "paused"
    payload["voice_agent"]["detail_code"] = "listening_paused"
    paths.health_file.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    assert background_cli.main(["status", "--runtime-root", str(tmp_path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["voice_agent"]["state"] == "paused"
    assert result["voice_agent"]["detail_code"] == "listening_paused"


def test_stop_wait_option_waits_for_supervisor_release(tmp_path: Path, capsys, monkeypatch) -> None:
    observed: dict[str, float] = {}

    def fake_wait(_paths, timeout_seconds: float) -> bool:
        observed["timeout"] = timeout_seconds
        return True

    monkeypatch.setattr(background_cli, "_wait_for_supervisor_stop", fake_wait, raising=False)
    assert (
        background_cli.main(["stop", "--runtime-root", str(tmp_path), "--wait-seconds", "15"]) == 0
    )
    assert observed == {"timeout": 15.0}
    assert json.loads(capsys.readouterr().out) == {"ok": True, "requested": True}


def test_stop_wait_timeout_is_bounded_failure(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.setattr(
        background_cli, "_wait_for_supervisor_stop", lambda _paths, _timeout: False, raising=False
    )
    assert (
        background_cli.main(["stop", "--runtime-root", str(tmp_path), "--wait-seconds", "1"]) == 1
    )
    assert json.loads(capsys.readouterr().out) == {"ok": False, "detail_code": "stop_timeout"}
