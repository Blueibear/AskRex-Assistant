from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.dev_orchestrator import claude_cloud
from scripts.dev_orchestrator.claude_cloud import (
    ClaudeCloudError,
    ClaudeCloudSession,
    adopt_cloud_session,
    launch_cloud_session,
    poll_cloud_session,
    read_cloud_session,
)
from scripts.dev_orchestrator.types import OrchestratorConfig, TaskItem


def _config(tmp_path: Path) -> OrchestratorConfig:
    root = tmp_path / "coord"
    backend = tmp_path / "backend"
    mobile = tmp_path / "mobile"
    frozen = tmp_path / "frozen"
    for path in (root, backend, mobile, frozen):
        path.mkdir()
    return replace(
        OrchestratorConfig.default(root),
        backend_root=backend,
        mobile_root=mobile,
        frozen_worktree=frozen,
    )


def test_claude_executable_prefers_cmd_shim_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def fake_which(name: str):
        seen.append(name)
        return (
            r"C:\\Users\\james\\AppData\\Roaming\\npm\\claude.cmd" if name == "claude.cmd" else None
        )

    monkeypatch.setattr(claude_cloud.os, "name", "nt")
    monkeypatch.setattr(claude_cloud.shutil, "which", fake_which)

    assert claude_cloud._claude_executable().endswith("claude.cmd")
    assert seen[0] == "claude.cmd"


def test_claude_executable_uses_bare_name_off_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def fake_which(name: str):
        seen.append(name)
        return "/usr/bin/claude" if name == "claude" else None

    monkeypatch.setattr(claude_cloud.os, "name", "posix")
    monkeypatch.setattr(claude_cloud.shutil, "which", fake_which)

    assert claude_cloud._claude_executable() == "/usr/bin/claude"
    assert seen == ["claude"]


def test_parse_cloud_launch_metadata_accepts_current_cli_output() -> None:
    output = (
        "\x1b[?25hCreated cloud session: Review task\r\n"
        "View: https://claude.ai/code/session_01K7cJz4TRbzvetbp5kkrnUL?from=cli&m=0\r\n"
        "Resume with: claude --teleport session_01K7cJz4TRbzvetbp5kkrnUL\r\n"
    )

    session_id, url = claude_cloud._parse_cloud_launch_metadata(output)

    assert session_id == "session_01K7cJz4TRbzvetbp5kkrnUL"
    assert url.startswith("https://claude.ai/code/session_01K7cJz4TRbzvetbp5kkrnUL")


def test_launch_cloud_interactive_requires_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(claude_cloud.os, "name", "posix")

    with pytest.raises(ClaudeCloudError, match="Windows ConPTY"):
        claude_cloud._launch_cloud_interactive(
            coordination_root=tmp_path, worktree=tmp_path, prompt="do work"
        )


def test_launch_cloud_interactive_requires_pywinpty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(claude_cloud.os, "name", "nt")
    monkeypatch.delitem(sys.modules, "winpty", raising=False)

    with pytest.raises(ClaudeCloudError, match="pywinpty"):
        claude_cloud._launch_cloud_interactive(
            coordination_root=tmp_path, worktree=tmp_path, prompt="do work"
        )


class _FakePtyProcess:
    def __init__(self, chunks: list[str], *, exitstatus: int | None = 0) -> None:
        self._chunks = list(chunks)
        self.exitstatus = exitstatus
        self.fileobj = SimpleNamespace(settimeout=lambda timeout: None)
        self.terminated = False
        self.closed = False

    def read(self, size: int) -> str:
        del size
        if self._chunks:
            return self._chunks.pop(0)
        raise TimeoutError("no data available")

    def isalive(self) -> bool:
        return bool(self._chunks)

    def terminate(self, force: bool = False) -> None:
        del force
        self.terminated = True

    def close(self, force: bool = False) -> None:
        del force
        self.closed = True


def _install_fake_winpty(monkeypatch: pytest.MonkeyPatch, proc: _FakePtyProcess) -> None:
    fake_module = SimpleNamespace(PtyProcess=SimpleNamespace(spawn=lambda *a, **kw: proc))
    monkeypatch.setitem(sys.modules, "winpty", fake_module)


def test_launch_cloud_interactive_parses_successful_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(claude_cloud.os, "name", "nt")
    monkeypatch.setattr(claude_cloud, "_claude_executable", lambda: "claude.cmd")
    proc = _FakePtyProcess(
        [
            "Created cloud session: Review task\r\n",
            "View: https://claude.ai/code/session_01ABCDEFGHJKMNPQRSTVWXYZ\r\n",
            "Resume with: claude --teleport session_01ABCDEFGHJKMNPQRSTVWXYZ\r\n",
        ]
    )
    _install_fake_winpty(monkeypatch, proc)

    session_id, url = claude_cloud._launch_cloud_interactive(
        coordination_root=tmp_path, worktree=tmp_path, prompt="do work", timeout=5
    )

    assert session_id == "session_01ABCDEFGHJKMNPQRSTVWXYZ"
    assert url == "https://claude.ai/code/session_01ABCDEFGHJKMNPQRSTVWXYZ"
    assert proc.closed
    assert not proc.terminated


def test_launch_cloud_interactive_times_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(claude_cloud.os, "name", "nt")
    monkeypatch.setattr(claude_cloud, "_claude_executable", lambda: "claude.cmd")
    proc = _FakePtyProcess([])
    monkeypatch.setattr(proc, "isalive", lambda: True)
    _install_fake_winpty(monkeypatch, proc)

    with pytest.raises(ClaudeCloudError, match="timed out"):
        claude_cloud._launch_cloud_interactive(
            coordination_root=tmp_path, worktree=tmp_path, prompt="do work", timeout=0.2
        )

    assert proc.terminated


def test_launch_cloud_interactive_reports_cli_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(claude_cloud.os, "name", "nt")
    monkeypatch.setattr(claude_cloud, "_claude_executable", lambda: "claude.cmd")
    proc = _FakePtyProcess(
        ["Some banner\r\n", "Error: authentication failed\r\n"],
        exitstatus=1,
    )
    _install_fake_winpty(monkeypatch, proc)

    with pytest.raises(ClaudeCloudError, match="authentication failed"):
        claude_cloud._launch_cloud_interactive(
            coordination_root=tmp_path, worktree=tmp_path, prompt="do work", timeout=5
        )


def test_cloud_prompt_requires_final_completion_commit() -> None:
    prompt = claude_cloud._cloud_prompt(
        TaskItem("ROADMAP-123", "Implement it", "review feedback"),
        role="backend",
        branch="ralph/cloud/backend/roadmap-123-abcd1234",
    )

    assert "do not merge" in prompt
    assert "do not claim physical-device verification" in prompt
    assert "ralph-cloud-complete: ROADMAP-123" in prompt
    assert "review feedback" in prompt


def test_launch_persists_machine_readable_session_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    commands: list[tuple[str, ...]] = []

    def fake_git(repo: Path, *args: str, timeout: int = 60) -> str:
        commands.append(tuple(args))
        if args == ("status", "--porcelain"):
            return ""
        if args == ("remote", "get-url", "origin"):
            return "https://github.com/blueibear/askrex-assistant.git"
        if args == ("rev-parse", "HEAD"):
            return "a" * 40
        return ""

    monkeypatch.setattr(claude_cloud, "_git_text", fake_git)
    monkeypatch.setattr(
        claude_cloud,
        "_run",
        lambda command, **kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    monkeypatch.setattr(claude_cloud.tempfile, "mkdtemp", lambda **kwargs: str(tmp_path / "launch"))

    def fake_execute(command, *, cwd, timeout):
        assert command[0].lower().endswith(("claude", "claude.cmd"))
        assert command[1] == "--cloud"
        assert "--output-format" in command
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "ok": True,
                    "session_id": "session_abc123",
                    "url": "https://claude.ai/code/session_abc123",
                }
            ),
            stderr="",
        )

    record = launch_cloud_session(
        config,
        role="backend",
        task=TaskItem("US-086", "Implement attachments"),
        execute=fake_execute,
    )

    assert record.session_id == "session_abc123"
    assert record.branch.startswith("ralph/cloud/backend/us-086-")
    assert read_cloud_session(config, "backend") == record
    assert any(command[:3] == ("push", "--set-upstream", "origin") for command in commands)


@pytest.mark.parametrize(
    ("remote_head", "subject", "expected"),
    [
        ("a" * 40, "base", "running"),
        ("b" * 40, "work in progress", "working_changes_pushed"),
        ("b" * 40, "ralph-cloud-complete: US-086", "ready_for_review"),
    ],
)
def test_poll_classifies_remote_branch_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    remote_head: str,
    subject: str,
    expected: str,
) -> None:
    config = _config(tmp_path)
    claude_cloud._session_store(config, "backend").write(
        ClaudeCloudSession(
            role="backend",
            task_id="US-086",
            session_id="session_abc",
            url="https://claude.ai/code/session_abc",
            branch="ralph/cloud/backend/us-086-12345678",
            base_head="a" * 40,
            launched_at="2026-09-25T00:00:00+00:00",
        ).to_dict()
    )

    def fake_git(repo: Path, *args: str, timeout: int = 60) -> str:
        if args[:2] == ("fetch", "--quiet"):
            return ""
        if args[0:2] == ("rev-parse", "refs/remotes/origin/ralph/cloud/backend/us-086-12345678"):
            return remote_head
        if args[0:3] == (
            "show",
            "-s",
            "--format=%s",
        ):
            return subject
        raise AssertionError(args)

    monkeypatch.setattr(claude_cloud, "_git_text", fake_git)

    assert poll_cloud_session(config, "backend").status == expected


def test_adopt_refuses_if_local_worktree_moved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    session = ClaudeCloudSession(
        role="backend",
        task_id="US-086",
        session_id="session_abc",
        url="https://claude.ai/code/session_abc",
        branch="ralph/cloud/backend/us-086-12345678",
        base_head="a" * 40,
        launched_at="2026-09-25T00:00:00+00:00",
    )
    claude_cloud._session_store(config, "backend").write(session.to_dict())
    monkeypatch.setattr(
        claude_cloud,
        "poll_cloud_session",
        lambda config, role: claude_cloud.ClaudeCloudPoll(
            "ready_for_review", "b" * 40, "ralph-cloud-complete: US-086"
        ),
    )

    def fake_git(repo: Path, *args: str, timeout: int = 60) -> str:
        if args == ("status", "--porcelain"):
            return ""
        if args == ("rev-parse", "HEAD"):
            return "c" * 40
        raise AssertionError(args)

    monkeypatch.setattr(claude_cloud, "_git_text", fake_git)

    with pytest.raises(ClaudeCloudError, match="worktree moved"):
        adopt_cloud_session(config, "backend")
