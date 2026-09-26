from __future__ import annotations

import json
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

    monkeypatch.setattr(claude_cloud.shutil, "which", fake_which)
    monkeypatch.setattr(claude_cloud, "__import__", __import__, raising=False)

    assert claude_cloud._claude_executable().endswith("claude.cmd")
    if __import__("os").name == "nt":
        assert seen[0] == "claude.cmd"


def test_parse_cloud_launch_metadata_accepts_current_cli_output() -> None:
    output = (
        "\x1b[?25hCreated cloud session: Review task\r\n"
        "View: https://claude.ai/code/session_01K7cJz4TRbzvetbp5kkrnUL?from=cli&m=0\r\n"
        "Resume with: claude --teleport session_01K7cJz4TRbzvetbp5kkrnUL\r\n"
    )

    session_id, url = claude_cloud._parse_cloud_launch_metadata(output)

    assert session_id == "session_01K7cJz4TRbzvetbp5kkrnUL"
    assert url.startswith("https://claude.ai/code/session_01K7cJz4TRbzvetbp5kkrnUL")


def test_cloud_prompt_requires_final_completion_commit_and_bounded_context() -> None:
    prompt = claude_cloud._cloud_prompt(
        TaskItem("ROADMAP-123", "Implement it", "review feedback"),
        role="backend",
        branch="ralph/cloud/backend/roadmap-123-abcd1234",
        context="validated coordination evidence",
    )

    assert "do not merge" in prompt
    assert "do not claim physical-device verification" in prompt
    assert "ralph-cloud-complete: ROADMAP-123" in prompt
    assert "review feedback" in prompt
    assert "Authoritative bounded coordination context supplied by Ralph" in prompt
    assert "validated coordination evidence" in prompt


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
        assert "bounded launch context" in command[2]
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
        context="bounded launch context",
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


def test_adopt_publishes_bound_pending_result_and_clears_session(
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
        invocation_id="inv-cloud-123",
    )
    claude_cloud._session_store(config, "backend").write(session.to_dict())
    monkeypatch.setattr(
        claude_cloud,
        "poll_cloud_session",
        lambda *_args: claude_cloud.ClaudeCloudPoll(
            "ready_for_review", "b" * 40, "ralph-cloud-complete: US-086"
        ),
    )
    monkeypatch.setattr(claude_cloud, "validate_handoff", lambda *_args: None)
    merged = False

    def fake_git(repo: Path, *args: str, timeout: int = 60) -> str:
        nonlocal merged
        if args == ("status", "--porcelain"):
            return ""
        if args == ("rev-parse", "HEAD"):
            return "b" * 40 if merged else "a" * 40
        if args[:2] == ("merge", "--ff-only"):
            merged = True
            return ""
        raise AssertionError(args)

    monkeypatch.setattr(claude_cloud, "_git_text", fake_git)
    captured: dict[str, object] = {}

    def fake_advance(config, role, **kwargs):
        captured.update(kwargs)
        return tmp_path / "handoff.json"

    monkeypatch.setattr(claude_cloud, "advance_handoff", fake_advance)

    result = adopt_cloud_session(config, "backend")

    assert result.outcome == "ready_for_review"
    assert result.invocation_id == "inv-cloud-123"
    assert captured["provider"] == "claude_cloud"
    pending = captured["pending_result"]
    assert isinstance(pending, dict)
    assert pending["task_id"] == "US-086"
    assert pending["result"]["outcome"] == "ready_for_review"
    assert read_cloud_session(config, "backend") is None


def test_handoff_accepts_only_bound_cloud_completion_commit(tmp_path: Path) -> None:
    import subprocess

    from scripts.dev_orchestrator.cli import initialize_runtime
    from scripts.dev_orchestrator.handoff import (
        acknowledge_handoff,
        advance_handoff,
        read_pending_result,
    )
    from tests.scripts.test_dev_orchestrator_safety import _git_repo

    root = tmp_path / "coord"
    root.mkdir()
    backend = tmp_path / "backend"
    mobile = tmp_path / "mobile"
    frozen = tmp_path / "frozen"
    frozen.mkdir()
    _git_repo(backend)
    _git_repo(mobile)
    config = initialize_runtime(root, backend, mobile, frozen)
    acknowledge_handoff(config, "backend", source="test")
    pre_head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=backend, text=True).strip()
    (backend / "cloud.txt").write_text("complete\n", encoding="utf-8")
    subprocess.run(["git", "add", "cloud.txt"], cwd=backend, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "ralph-cloud-complete: US-CLOUD"],
        cwd=backend,
        check=True,
    )
    post_head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=backend, text=True
    ).strip()
    pending = {
        "phase": "implement",
        "role": "backend",
        "task_id": "US-CLOUD",
        "invocation_id": "cloud-invocation",
        "pre_head": pre_head,
        "post_head": post_head,
        "result": {
            "outcome": "ready_for_review",
            "summary": "cloud complete",
            "next_action": "review",
            "needs_user": False,
            "blocker_reason": "",
            "task_id": "US-CLOUD",
            "task_prompt": "",
            "role": "backend",
            "invocation_id": "cloud-invocation",
            "coordination_messages": [],
            "issue_updates": [],
        },
    }

    advance_handoff(
        config,
        "backend",
        pre_head=pre_head,
        post_head=post_head,
        invocation_id="cloud-invocation",
        provider="claude_cloud",
        pending_result=pending,
    )

    assert read_pending_result(config, "backend")["task_id"] == "US-CLOUD"
