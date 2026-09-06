from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from rex.background import windows_startup


def test_create_command_requires_absolute_packaged_paths(tmp_path: Path) -> None:
    runtime_root = tmp_path / "Ask Rex Data"
    pythonw = tmp_path / "Ask Rex" / "pythonw.exe"
    command = windows_startup.build_schtasks_create_command(
        "AskRex Background Runtime",
        pythonw.resolve(),
        runtime_root.resolve(),
        "james",
        run_as_user="DESKTOP\\james",
    )

    assert command[0].lower().endswith("schtasks.exe")
    assert "/Create" in command
    assert command[command.index("/SC") + 1] == "ONLOGON"
    assert command[command.index("/RU") + 1] == "DESKTOP\\james"
    assert "/IT" in command
    assert command[command.index("/RL") + 1] == "LIMITED"
    assert "/F" in command


def test_create_command_quotes_action_as_data_with_spaces() -> None:
    pythonw = Path(r"C:\Program Files\Ask Rex\pythonw.exe")
    runtime_root = Path(r"C:\Users\James\Rex Data")
    command = windows_startup.build_schtasks_create_command(
        "AskRex Background Runtime",
        pythonw,
        runtime_root,
        "james.user",
        run_as_user="DESKTOP\\James User",
    )

    action = command[command.index("/TR") + 1]
    assert str(pythonw) in action
    assert "-m rex.background.cli supervisor" in action
    assert str(runtime_root) in action
    assert "-u james.user" in action
    assert "-p" in action
    assert "--runtime-root" not in action
    assert command.count("/TR") == 1


def test_create_command_requires_explicit_authoritative_run_as_principal() -> None:
    with pytest.raises(ValueError, match="run-as user"):
        windows_startup.build_schtasks_create_command(
            "AskRex Background Runtime",
            Path(r"C:\AskRex\pythonw.exe"),
            Path(r"C:\Users\James\AskRex"),
            "james",
        )


def test_create_command_stays_within_schtasks_action_limit_for_production_shaped_paths() -> None:
    pythonw = Path(
        r"C:\Users\abcdefghijklmnopqrst\AppData\Local\Programs\AskRex\resources\python\pythonw.exe"
    )
    runtime_root = Path(r"C:\Users\abcdefghijklmnopqrst\AppData\Roaming\AskRex")
    user_id = "a" * 64
    command = windows_startup.build_schtasks_create_command(
        "AskRex Background Runtime",
        pythonw,
        runtime_root,
        user_id,
        run_as_user=r"CONTOSO\abcdefghijklmnopqrst",  # pragma: allowlist secret
    )

    action = command[command.index("/TR") + 1]
    assert len(action) <= windows_startup.SCHTASKS_TASK_ACTION_MAX_CHARS


def test_supervisor_compact_scheduler_aliases_remain_equivalent(tmp_path: Path) -> None:
    from rex.background import cli as background_cli

    root = tmp_path.resolve()
    args = background_cli.create_parser().parse_args(
        ["supervisor", "-r", str(root), "-u", "james", "-p"]
    )
    assert args.runtime_root == str(root)
    assert args.user == "james"
    assert args.packaged is True


@pytest.mark.parametrize(
    ("pythonw", "runtime_root"),
    [(Path("pythonw.exe"), Path("C:/AskRex")), (Path("C:/AskRex/pythonw.exe"), Path("runtime"))],
)
def test_create_command_rejects_relative_paths(pythonw: Path, runtime_root: Path) -> None:
    with pytest.raises(ValueError, match="absolute"):
        windows_startup.build_schtasks_create_command("AskRex", pythonw, runtime_root, "james")


def test_install_startup_uses_argument_array_without_shell(monkeypatch, tmp_path: Path) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(command, **kwargs):
        calls.append((list(command), dict(kwargs)))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(windows_startup, "_is_windows", lambda: True)
    monkeypatch.setattr(windows_startup.subprocess, "run", fake_run)
    pythonw = (tmp_path / "python" / "pythonw.exe").resolve()
    runtime_root = (tmp_path / "runtime root").resolve()

    windows_startup.install_startup(
        "AskRex Background Runtime",
        pythonw,
        runtime_root,
        "james",
        run_as_user="DESKTOP\\james",
    )

    assert len(calls) == 1
    command, kwargs = calls[0]
    assert command[0].lower().endswith("schtasks.exe")
    assert kwargs["shell"] is False
    assert kwargs["check"] is False
    assert kwargs["timeout"] == windows_startup.SCHTASKS_TIMEOUT_SECONDS


def test_install_startup_is_idempotent_repair_via_force(monkeypatch, tmp_path: Path) -> None:
    commands: list[list[str]] = []

    def fake_run(command, **_kwargs):
        commands.append(list(command))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(windows_startup, "_is_windows", lambda: True)
    monkeypatch.setattr(windows_startup.subprocess, "run", fake_run)
    pythonw = (tmp_path / "new install" / "pythonw.exe").resolve()
    runtime_root = (tmp_path / "user data").resolve()

    for _ in range(2):
        windows_startup.install_startup(
            "AskRex Background Runtime",
            pythonw,
            runtime_root,
            "james",
            run_as_user="DESKTOP\\james",
        )

    assert len(commands) == 2
    assert all("/F" in command for command in commands)
    assert commands[0] == commands[1]


def test_query_and_remove_startup_use_schtasks_arrays(monkeypatch) -> None:
    commands: list[list[str]] = []

    def fake_run(command, **_kwargs):
        commands.append(list(command))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(windows_startup, "_is_windows", lambda: True)
    monkeypatch.setattr(windows_startup.subprocess, "run", fake_run)

    assert windows_startup.query_startup("AskRex Background Runtime") is True
    windows_startup.remove_startup("AskRex Background Runtime")

    assert commands[0][1:] == ["/Query", "/TN", "AskRex Background Runtime"]
    assert commands[1][1:] == ["/Delete", "/TN", "AskRex Background Runtime", "/F"]


def test_mutating_startup_fails_closed_off_windows(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(windows_startup, "_is_windows", lambda: False)
    with pytest.raises(OSError, match="Windows"):
        windows_startup.install_startup(
            "AskRex",
            (tmp_path / "pythonw.exe").resolve(),
            tmp_path.resolve(),
            "james",
            run_as_user="DESKTOP\\james",
        )


def test_install_startup_surfaces_scheduler_failure(monkeypatch, tmp_path: Path) -> None:
    def fake_run(command, **_kwargs):
        return subprocess.CompletedProcess(command, 5, stdout="", stderr="access denied")

    monkeypatch.setattr(windows_startup, "_is_windows", lambda: True)
    monkeypatch.setattr(windows_startup.subprocess, "run", fake_run)

    with pytest.raises(windows_startup.StartupTaskError, match="create"):
        windows_startup.install_startup(
            "AskRex",
            (tmp_path / "pythonw.exe").resolve(),
            tmp_path.resolve(),
            "james",
            run_as_user="DESKTOP\\james",
        )


def test_query_missing_task_returns_false(monkeypatch) -> None:
    monkeypatch.setattr(windows_startup, "_is_windows", lambda: True)
    monkeypatch.setattr(
        windows_startup.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 1, stdout="", stderr=""),
    )
    assert windows_startup.query_startup("AskRex Background Runtime") is False


def test_internal_cli_registers_startup_commands(monkeypatch, tmp_path: Path) -> None:
    from rex.background import cli as background_cli

    recorded: list[tuple[Path, Path, str, str | None]] = []

    def fake_install(_task, pythonw, root, user, *, run_as_user=None):
        recorded.append((Path(pythonw), Path(root), user, run_as_user))

    monkeypatch.setattr(background_cli, "install_startup", fake_install, raising=False)
    pythonw = (tmp_path / "python" / "pythonw.exe").resolve()
    root = (tmp_path / "user data").resolve()
    result = background_cli.main(
        [
            "install-startup",
            "--runtime-root",
            str(root),
            "--pythonw-path",
            str(pythonw),
            "--user",
            "james",
            "--packaged",
            "--run-as-user",
            "DESKTOP\\james",
        ]
    )
    assert result == 0
    assert recorded == [(pythonw, root, "james", "DESKTOP\\james")]


def test_internal_cli_maps_startup_validation_failure_to_bounded_error(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    from rex.background import cli as background_cli

    def fail_install(*_args, **_kwargs):
        raise ValueError("private path or principal detail must not escape")

    monkeypatch.setattr(background_cli, "install_startup", fail_install)
    result = background_cli.main(
        [
            "install-startup",
            "--runtime-root",
            str(tmp_path.resolve()),
            "--pythonw-path",
            str((tmp_path / "pythonw.exe").resolve()),
            "--user",
            "james",
            "--run-as-user",
            "CONTOSO\\james",
        ]
    )
    assert result == 1
    output = capsys.readouterr().out
    assert "startup_registration_failed" in output
    assert "private path" not in output


def test_internal_cli_removes_startup_task(monkeypatch, tmp_path: Path) -> None:
    from rex.background import cli as background_cli

    removed: list[str] = []
    monkeypatch.setattr(
        background_cli,
        "remove_startup",
        lambda task_name: removed.append(task_name),
        raising=False,
    )

    result = background_cli.main(
        [
            "remove-startup",
            "--runtime-root",
            str(tmp_path.resolve()),
            "--task-name",
            "AskRex Background Runtime",
        ]
    )

    assert result == 0
    assert removed == ["AskRex Background Runtime"]


def test_packaged_supervisor_cli_sets_canonical_runtime_environment(
    monkeypatch, tmp_path: Path
) -> None:
    from rex.background import cli as background_cli

    root = (tmp_path / "Electron User Data").resolve()
    captured: list[Path] = []

    class _Runtime:
        def run(self) -> None:
            assert os.environ["ASKREX_RUNTIME_DIR"] == str(root)
            assert os.environ["ASKREX_CONFIG_PATH"] == str(root / "config" / "rex_config.json")
            assert os.environ["ASKREX_PROFILES_DIR"] == str(root / "profiles")
            assert os.environ["ASKREX_USERS_DATA_DIR"] == str(root / "data" / "users")
            assert os.environ["ASKREX_MEMORY_DIR"] == str(root / "Memory")
            assert os.environ["ASKREX_PACKAGED"] == "1"
            assert "REX_ALLOW_PLAINTEXT_CREDENTIAL_FALLBACK" not in os.environ

    def fake_build(paths, **_kwargs):
        captured.append(paths.runtime_root)
        return _Runtime()

    monkeypatch.setattr(background_cli, "build_supervisor", fake_build)
    monkeypatch.setattr(background_cli, "_install_stop_signal_handlers", lambda _paths: None)
    monkeypatch.setenv("REX_ALLOW_PLAINTEXT_CREDENTIAL_FALLBACK", "1")
    for name in (
        "ASKREX_RUNTIME_DIR",
        "ASKREX_CONFIG_PATH",
        "ASKREX_ENV_PATH",
        "ASKREX_PROFILES_DIR",
        "REX_DATA_DIR",
        "ASKREX_HOUSEHOLD_DATA_DIR",
        "ASKREX_USERS_DATA_DIR",
        "ASKREX_MEMORY_DIR",
        "ASKREX_PACKAGED",
    ):
        monkeypatch.delenv(name, raising=False)

    assert (
        background_cli.main(
            ["supervisor", "--runtime-root", str(root), "--user", "james", "--packaged"]
        )
        == 0
    )
    assert captured == [root]
    assert "ASKREX_RUNTIME_DIR" not in os.environ
    assert "ASKREX_PACKAGED" not in os.environ
    assert os.environ["REX_ALLOW_PLAINTEXT_CREDENTIAL_FALLBACK"] == "1"


def test_background_main_restores_process_environment(monkeypatch, tmp_path: Path) -> None:
    from rex.background import cli as background_cli

    root = (tmp_path / "runtime").resolve()
    monkeypatch.setenv("ASKREX_RUNTIME_DIR", "C:/original-runtime")
    monkeypatch.delenv("ASKREX_PACKAGED", raising=False)
    monkeypatch.setattr(background_cli, "_read_status", lambda _paths: ({"ok": True}, 0))

    class _Runtime:
        def run(self) -> None:
            assert os.environ["ASKREX_RUNTIME_DIR"] == str(root)
            assert os.environ["ASKREX_PACKAGED"] == "1"

    monkeypatch.setattr(background_cli, "build_supervisor", lambda *_args, **_kwargs: _Runtime())
    monkeypatch.setattr(background_cli, "_install_stop_signal_handlers", lambda _paths: None)
    assert (
        background_cli.main(
            ["supervisor", "--runtime-root", str(root), "--user", "james", "--packaged"]
        )
        == 0
    )
    assert os.environ["ASKREX_RUNTIME_DIR"] == "C:/original-runtime"
    assert "ASKREX_PACKAGED" not in os.environ
