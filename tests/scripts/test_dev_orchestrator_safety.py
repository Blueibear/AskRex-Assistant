import subprocess
from pathlib import Path

import pytest

from scripts.dev_orchestrator.cli import initialize_runtime
from scripts.dev_orchestrator.handoff import (
    HandoffRequired,
    acknowledge_handoff,
    validate_handoffs,
)
from scripts.dev_orchestrator.lifecycle import write_heartbeat
from scripts.dev_orchestrator.paths import validate_runtime_paths


def _git_repo(path: Path) -> None:
    mobile = "mobile" in path.name.lower()
    base = "main" if mobile else "master"
    origin = (
        "https://github.com/Blueibear/AskRex.git"
        if mobile
        else "https://github.com/Blueibear/AskRex-Assistant.git"
    )
    source = path.with_name(path.name + "-source")
    source.mkdir()
    subprocess.run(["git", "init", "-q", "-b", base], cwd=source, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=source, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=source, check=True)
    (source / "README.md").write_text("ready\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=source, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=source, check=True)
    subprocess.run(["git", "remote", "add", "origin", origin], cwd=source, check=True)
    branch = f"worker/{path.name}"
    subprocess.run(
        ["git", "worktree", "add", "-q", "-b", branch, str(path)], cwd=source, check=True
    )
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=path, text=True).strip()
    subprocess.run(["git", "update-ref", f"refs/remotes/origin/{base}", head], cwd=path, check=True)
    subprocess.run(
        ["git", "update-ref", f"refs/remotes/origin/{branch}", head], cwd=path, check=True
    )
    subprocess.run(
        ["git", "branch", "--set-upstream-to", f"origin/{branch}"],
        cwd=path,
        check=True,
        stdout=subprocess.DEVNULL,
    )


def test_runtime_paths_reject_any_overlap(tmp_path: Path) -> None:
    backend = tmp_path / "backend"
    mobile = tmp_path / "mobile"
    frozen = tmp_path / "frozen"
    coord = tmp_path / "coord"
    for path in (backend, mobile, frozen, coord):
        path.mkdir()
    validate_runtime_paths(coord, backend, mobile, frozen)

    with pytest.raises(ValueError, match="overlap"):
        validate_runtime_paths(backend / "coord", backend, mobile, frozen)
    with pytest.raises(ValueError, match="overlap"):
        validate_runtime_paths(coord, frozen / "backend", mobile, frozen)
    with pytest.raises(ValueError, match="overlap"):
        validate_runtime_paths(coord, backend, backend / "mobile", frozen)
    with pytest.raises(ValueError, match="overlap"):
        validate_runtime_paths(coord, backend, backend, frozen)


def test_active_mode_requires_clean_head_bound_handoffs(tmp_path: Path) -> None:
    root = tmp_path / "coord"
    backend = tmp_path / "backend"
    mobile = tmp_path / "mobile"
    frozen = tmp_path / "frozen"
    root.mkdir()
    frozen.mkdir()
    _git_repo(backend)
    _git_repo(mobile)
    config = initialize_runtime(root, backend, mobile, frozen)

    with pytest.raises(HandoffRequired):
        validate_handoffs(config)

    acknowledge_handoff(config, "backend", source="test")
    acknowledge_handoff(config, "mobile", source="test")
    validate_handoffs(config)

    (backend / "README.md").write_text("changed\n", encoding="utf-8")
    with pytest.raises(HandoffRequired, match="backend"):
        validate_handoffs(config)


def test_handoff_refuses_dirty_worktree(tmp_path: Path) -> None:
    root = tmp_path / "coord"
    backend = tmp_path / "backend"
    mobile = tmp_path / "mobile"
    frozen = tmp_path / "frozen"
    root.mkdir()
    frozen.mkdir()
    _git_repo(backend)
    _git_repo(mobile)
    config = initialize_runtime(root, backend, mobile, frozen)
    (backend / "dirty.txt").write_text("dirty\n", encoding="utf-8")

    with pytest.raises(HandoffRequired, match="clean"):
        acknowledge_handoff(config, "backend", source="test")


def test_cli_cycle_uses_single_instance_lock(tmp_path: Path) -> None:
    from scripts.dev_orchestrator.cli import run_locked_cycle
    from scripts.dev_orchestrator.lifecycle import SupervisorLock

    root = tmp_path / "coord"
    root.mkdir()
    backend = tmp_path / "backend"
    backend.mkdir()
    mobile = tmp_path / "mobile"
    mobile.mkdir()
    frozen = tmp_path / "frozen"
    frozen.mkdir()
    config = initialize_runtime(root, backend, mobile, frozen)
    with SupervisorLock(root / "supervisor.lock"):
        with pytest.raises(RuntimeError, match="already running"):
            run_locked_cycle(config)


def test_watchdog_never_kills_live_supervisor_or_active_agent() -> None:
    script = (
        Path("scripts/dev_orchestrator/windows_watchdog.ps1").read_text(encoding="utf-8").lower()
    )

    assert "active-agents" in script
    assert "stop-process" not in script
    assert "taskkill" not in script
    assert "get-process" in script


def test_claude_does_not_receive_coordination_write_access(tmp_path: Path) -> None:
    from scripts.dev_orchestrator.runner import CliAgentInvoker, ProcessResult
    from scripts.dev_orchestrator.types import TaskItem, WorkerState

    repo = tmp_path / "backend"
    mobile = tmp_path / "mobile"
    coord = tmp_path / "coord"
    coord.mkdir()
    frozen = tmp_path / "frozen"
    frozen.mkdir()
    _git_repo(repo)
    _git_repo(mobile)
    cfg = initialize_runtime(coord, repo, mobile, frozen)
    acknowledge_handoff(cfg, "backend", source="test")
    acknowledge_handoff(cfg, "mobile", source="test")
    seen = []

    def execute(command, cwd, **kwargs):
        seen.append(command)
        return ProcessResult(
            0,
            '{"outcome":"continue","summary":"ok","next_action":"next","needs_user":false,"blocker_reason":""}',
            "",
        )

    invoker = CliAgentInvoker(cfg, execute=execute, allow_test_executor=True)
    invoker.implement(
        "backend",
        WorkerState(role="backend"),
        TaskItem("B-1", "Work"),
        "BOUNDED COORDINATION CONTEXT",
        "sonnet",
    )

    command = seen[0]
    assert "--add-dir" not in command
    assert str(coord) not in command[:-1]
    assert "BOUNDED COORDINATION CONTEXT" in command[-1]


def test_run_command_tracks_active_child_process(tmp_path: Path) -> None:
    import sys

    from scripts.dev_orchestrator.runner import run_command

    marker = tmp_path / "active-agents" / "backend.json"
    code = "import pathlib,sys,time; time.sleep(0.05); " "print(pathlib.Path(sys.argv[1]).exists())"
    result = run_command(
        [sys.executable, "-c", code, str(marker)],
        tmp_path,
        timeout_seconds=5,
        activity_file=marker,
        activity_metadata={"role": "backend", "provider": "claude"},
    )
    assert result.returncode == 0
    assert "True" in result.stdout
    assert not marker.exists()


def test_invoker_rejects_direct_overlapping_config(tmp_path: Path) -> None:
    from scripts.dev_orchestrator.runner import CliAgentInvoker
    from scripts.dev_orchestrator.types import OrchestratorConfig

    frozen = tmp_path / "frozen"
    frozen.mkdir()
    backend = frozen / "nested"
    backend.mkdir()
    mobile = tmp_path / "mobile"
    mobile.mkdir()
    coord = tmp_path / "coord"
    coord.mkdir()
    config = OrchestratorConfig(coord, backend, mobile, frozen, observe_only=False)

    with pytest.raises(ValueError, match="overlap"):
        CliAgentInvoker(config)


def test_structured_coordination_requests_are_validated_and_applied(tmp_path: Path) -> None:
    from scripts.dev_orchestrator.coordination import apply_agent_updates
    from scripts.dev_orchestrator.schema import validate_agent_result

    root = tmp_path / "coord"
    root.mkdir()
    (root / "mailbox" / "mobile").mkdir(parents=True)
    (root / "issues").mkdir()
    issue = root / "issues" / "TEST-009.md"
    issue.write_text(
        "# TEST-009\n\nStatus: open\nOwner: backend\n\n## Retest\nPending.\n",
        encoding="utf-8",
    )
    result = validate_agent_result(
        {
            "outcome": "pass",
            "summary": "ready",
            "next_action": "retest",
            "needs_user": False,
            "blocker_reason": "",
            "invocation_id": "safety-test-invocation",
            "coordination_messages": [
                {
                    "to": "mobile",
                    "priority": "high",
                    "related": "TEST-009",
                    "needs_response": True,
                    "body": "Contract is ready.",
                }
            ],
            "issue_updates": [
                {
                    "issue_id": "TEST-009",
                    "status": "fixed-needs-retest",
                    "note": "Implementation and independent review passed.",
                }
            ],
        }
    )

    apply_agent_updates(root, "backend", result, allow_issue_updates=True, task_id="TEST-009")

    messages = list((root / "mailbox" / "mobile").glob("*.md"))
    assert len(messages) == 1
    text = messages[0].read_text(encoding="utf-8")
    assert "From: backend" in text
    assert "Contract is ready." in text
    updated = issue.read_text(encoding="utf-8")
    assert "Status: open" in updated
    retests = list((root / "mailbox" / "testing").glob("RETEST-*.md"))
    assert len(retests) == 1
    retest_text = retests[0].read_text(encoding="utf-8")
    assert "Implementation and independent review passed." in retest_text


def test_development_coordination_schema_can_never_request_verified(tmp_path: Path) -> None:
    from scripts.dev_orchestrator.schema import validate_agent_result

    with pytest.raises(ValueError, match="schema"):
        validate_agent_result(
            {
                "outcome": "pass",
                "summary": "x",
                "next_action": "x",
                "needs_user": False,
                "blocker_reason": "",
                "issue_updates": [{"issue_id": "TEST-001", "status": "verified", "note": "no"}],
            }
        )


def test_completion_gate_classifies_github_auth_as_user_blocker(
    tmp_path: Path, monkeypatch
) -> None:
    import subprocess as subprocess_module

    from scripts.dev_orchestrator import completion
    from scripts.dev_orchestrator.completion import evaluate_completion
    from scripts.dev_orchestrator.types import OrchestratorConfig

    repo = tmp_path / "backend"
    _git_repo(repo)
    coord = tmp_path / "coord"
    coord.mkdir()
    (coord / "issues").mkdir()
    config = OrchestratorConfig(coordination_root=coord, backend_root=repo)
    from scripts.dev_orchestrator.handoff import acknowledge_handoff

    (coord / "handoff").mkdir(exist_ok=True)
    acknowledge_handoff(config, "backend", source="test")
    real_run = completion._run

    def fake_run(path, *args):
        if args[:3] == ("gh", "pr", "view"):
            return subprocess_module.CompletedProcess(
                args, 1, "", "HTTP 401: Requires authentication"
            )
        return real_run(path, *args)

    monkeypatch.setattr(completion.shutil, "which", lambda _name: "gh.exe")
    monkeypatch.setattr(completion, "_run", fake_run)
    gate = evaluate_completion(config, "backend")

    assert gate.ready is False
    assert gate.needs_user is True
    assert any("authentication" in reason.lower() for reason in gate.reasons)


def test_run_command_writes_launching_marker_before_process_spawn(
    tmp_path: Path, monkeypatch
) -> None:
    import json

    from scripts.dev_orchestrator import runner

    marker = tmp_path / "active-agents" / "backend.json"
    observed = {}

    def fail_spawn(*_args, **_kwargs):
        observed.update(json.loads(marker.read_text(encoding="utf-8")))
        raise OSError("simulated spawn failure")

    monkeypatch.setattr(runner.subprocess, "Popen", fail_spawn)
    with pytest.raises(OSError, match="simulated spawn failure"):
        runner.run_command(
            ["fake-agent"],
            tmp_path,
            activity_file=marker,
            activity_metadata={"role": "backend", "provider": "claude"},
        )
    assert observed["status"] == "launching"
    assert observed["pid"] is None
    assert not marker.exists()


def test_injected_executor_still_enforces_commit_provenance(tmp_path: Path) -> None:
    import json
    import subprocess as sp

    from scripts.dev_orchestrator.runner import CliAgentInvoker, ProcessResult
    from scripts.dev_orchestrator.types import TaskItem, WorkerState

    root = tmp_path / "coord"
    root.mkdir()
    frozen = tmp_path / "frozen"
    frozen.mkdir()
    backend = tmp_path / "backend"
    mobile = tmp_path / "mobile"
    _git_repo(backend)
    _git_repo(mobile)
    cfg = initialize_runtime(root, backend, mobile, frozen)
    acknowledge_handoff(cfg, "backend", source="test")
    acknowledge_handoff(cfg, "mobile", source="test")
    before = sp.check_output(["git", "rev-parse", "HEAD"], cwd=backend, text=True).strip()

    def execute(_command, cwd, **_kwargs):
        (cwd / "unsafe.txt").write_text("x\n", encoding="utf-8")
        sp.run(["git", "add", "unsafe.txt"], cwd=cwd, check=True)
        sp.run(["git", "commit", "-qm", "unsafe injected commit"], cwd=cwd, check=True)
        return ProcessResult(
            0,
            json.dumps(
                {
                    "outcome": "continue",
                    "summary": "x",
                    "next_action": "x",
                    "needs_user": False,
                    "blocker_reason": "",
                }
            ),
            "",
        )

    result = CliAgentInvoker(cfg, execute=execute, allow_test_executor=True).implement(
        "backend", WorkerState("backend"), TaskItem("B-X", "x"), "ctx", "sonnet"
    )
    after = sp.check_output(["git", "rev-parse", "HEAD"], cwd=backend, text=True).strip()
    assert result.outcome == "continue"
    assert after == before
    assert not (backend / "unsafe.txt").exists()


def test_watchdog_reconciles_provably_dead_agent_marker(tmp_path: Path) -> None:
    import json
    import os
    import shutil
    import subprocess as sp
    import sys
    from datetime import UTC, datetime

    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if pwsh is None:
        pytest.skip("PowerShell is unavailable")
    coord = tmp_path / "coord"
    active = coord / "active-agents"
    active.mkdir(parents=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    write_heartbeat(
        coord / "supervisor-heartbeat.json",
        now=datetime.now(UTC),
        pid=os.getpid(),
    )
    marker = active / "backend.json"
    marker.write_text(
        json.dumps(
            {
                "status": "active",
                "pid": 2147483000,
                "invocation_id": "dead-test",
                "process_started_at": datetime.now(UTC).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    script = Path("scripts/dev_orchestrator/windows_watchdog.ps1").resolve()
    result = sp.run(
        [
            pwsh,
            "-NoProfile",
            "-File",
            str(script),
            "-CoordinationRoot",
            str(coord),
            "-PythonExe",
            sys.executable,
            "-RepoRoot",
            str(repo),
            "-MaxAgeSeconds",
            "0",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert not marker.exists()
    assert "Removed stale active-agent marker" in result.stdout


def test_watchdog_rejects_future_dated_heartbeat(tmp_path: Path) -> None:
    import os
    import shutil
    import subprocess as sp
    import sys
    from datetime import UTC, datetime, timedelta

    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if pwsh is None:
        pytest.skip("PowerShell is unavailable")
    coord = tmp_path / "coord-future-heartbeat"
    coord.mkdir()
    repo = tmp_path / "watchdog-repo"
    repo.mkdir()
    write_heartbeat(
        coord / "supervisor-heartbeat.json",
        now=datetime.now(UTC) + timedelta(hours=1),
        pid=os.getpid(),
    )
    script = Path("scripts/dev_orchestrator/windows_watchdog.ps1").resolve()
    result = sp.run(
        [
            pwsh,
            "-NoProfile",
            "-File",
            str(script),
            "-CoordinationRoot",
            str(coord),
            "-PythonExe",
            sys.executable,
            "-RepoRoot",
            str(repo),
            "-MaxAgeSeconds",
            "0",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert "still matches heartbeat process identity despite stale heartbeat" in result.stdout


@pytest.mark.parametrize("bad_pid_kind", ["string", "float", "boolean"])
def test_watchdog_rejects_non_integer_heartbeat_pid(tmp_path: Path, bad_pid_kind: str) -> None:
    import json
    import os
    import shutil
    import subprocess as sp
    import sys

    from scripts.dev_orchestrator.lifecycle import write_heartbeat

    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if pwsh is None:
        pytest.skip("PowerShell is unavailable")
    coord = tmp_path / "coord-bad-heartbeat-pid"
    active = coord / "active-agents"
    active.mkdir(parents=True)
    repo = tmp_path / "watchdog-repo"
    repo.mkdir()
    heartbeat_path = coord / "supervisor-heartbeat.json"
    write_heartbeat(heartbeat_path)
    payload = json.loads(heartbeat_path.read_text(encoding="utf-8"))
    if bad_pid_kind == "string":
        payload["pid"] = str(os.getpid())
    elif bad_pid_kind == "float":
        payload["pid"] = float(os.getpid())
    else:
        payload["pid"] = True
    heartbeat_path.write_text(json.dumps(payload), encoding="utf-8")
    (active / "backend.json").write_text(
        json.dumps({"status": "delegated", "pid": os.getpid(), "invocation_id": ""}),
        encoding="utf-8",
    )
    script = Path("scripts/dev_orchestrator/windows_watchdog.ps1").resolve()
    result = sp.run(
        [
            pwsh,
            "-NoProfile",
            "-File",
            str(script),
            "-CoordinationRoot",
            str(coord),
            "-PythonExe",
            sys.executable,
            "-RepoRoot",
            str(repo),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 3, result.stdout + result.stderr
    assert "Delegated executor marker" in result.stdout


def test_watchdog_requires_exact_heartbeat_process_filetime(tmp_path: Path) -> None:
    import json
    import os
    import shutil
    import subprocess as sp
    import sys

    from scripts.dev_orchestrator.lifecycle import write_heartbeat

    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if pwsh is None:
        pytest.skip("PowerShell is unavailable")
    coord = tmp_path / "coord-filetime-mismatch"
    active = coord / "active-agents"
    active.mkdir(parents=True)
    repo = tmp_path / "watchdog-repo"
    repo.mkdir()
    heartbeat_path = coord / "supervisor-heartbeat.json"
    write_heartbeat(heartbeat_path)
    payload = json.loads(heartbeat_path.read_text(encoding="utf-8"))
    payload["process_started_filetime"] += 10_000_000
    heartbeat_path.write_text(json.dumps(payload), encoding="utf-8")
    (active / "backend.json").write_text(
        json.dumps({"status": "delegated", "pid": os.getpid(), "invocation_id": ""}),
        encoding="utf-8",
    )
    script = Path("scripts/dev_orchestrator/windows_watchdog.ps1").resolve()
    result = sp.run(
        [
            pwsh,
            "-NoProfile",
            "-File",
            str(script),
            "-CoordinationRoot",
            str(coord),
            "-PythonExe",
            sys.executable,
            "-RepoRoot",
            str(repo),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 3, result.stdout + result.stderr
    assert "Delegated executor marker" in result.stdout


def test_claude_uses_repo_scoped_file_tools_without_shell_or_web(tmp_path: Path) -> None:
    from scripts.dev_orchestrator.runner import build_claude_command

    command = build_claude_command(tmp_path / "repo", "work", "sonnet")
    tools = command[command.index("--tools") + 1]
    assert tools == "Read,Write,Edit,Glob,Grep"
    assert "--safe-mode" in command
    assert command[command.index("--permission-mode") + 1] == "acceptEdits"
    assert "Bash" not in tools
    assert "Web" not in tools
    assert "--add-dir" not in command


def test_injected_executor_file_edits_are_rejected(tmp_path: Path) -> None:
    import json
    import subprocess as sp

    from scripts.dev_orchestrator.runner import CliAgentInvoker, ProcessResult
    from scripts.dev_orchestrator.types import TaskItem, WorkerState

    root = tmp_path / "coord"
    root.mkdir()
    frozen = tmp_path / "frozen"
    frozen.mkdir()
    backend = tmp_path / "backend"
    mobile = tmp_path / "mobile"
    _git_repo(backend)
    _git_repo(mobile)
    cfg = initialize_runtime(root, backend, mobile, frozen)
    acknowledge_handoff(cfg, "backend", source="test")
    before = sp.check_output(["git", "rev-parse", "HEAD"], cwd=backend, text=True).strip()

    def execute(_command, cwd, **_kwargs):
        (cwd / "unsafe.txt").write_text("unsafe\n", encoding="utf-8")
        return ProcessResult(
            0,
            json.dumps(
                {
                    "outcome": "continue",
                    "summary": "edited",
                    "next_action": "review",
                    "needs_user": False,
                    "blocker_reason": "",
                }
            ),
            "",
        )

    result = CliAgentInvoker(cfg, execute=execute, allow_test_executor=True).implement(
        "backend", WorkerState("backend"), TaskItem("B-SAFE", "edit only"), "ctx", "sonnet"
    )
    after = sp.check_output(["git", "rev-parse", "HEAD"], cwd=backend, text=True).strip()
    assert result.outcome == "continue"
    assert after == before
    assert not (backend / "unsafe.txt").exists()
    assert sp.check_output(["git", "status", "--porcelain"], cwd=backend, text=True).strip() == ""


def _watchdog_fixture(tmp_path: Path, marker_payload: dict, docker_script: str | None = None):
    import json
    import os
    import shutil
    import subprocess as sp
    import sys
    from datetime import UTC, datetime

    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if pwsh is None:
        pytest.skip("PowerShell is unavailable")
    coord = tmp_path / "coord-watchdog"
    active = coord / "active-agents"
    active.mkdir(parents=True)
    repo = tmp_path / "watchdog-repo"
    repo.mkdir()
    write_heartbeat(
        coord / "supervisor-heartbeat.json",
        now=datetime.now(UTC),
        pid=os.getpid(),
    )
    marker = active / "backend.json"
    marker.write_text(json.dumps(marker_payload), encoding="utf-8")
    env = os.environ.copy()
    log = tmp_path / "docker-calls.log"
    if docker_script is not None:
        bindir = tmp_path / "fake-docker-bin"
        bindir.mkdir()
        (bindir / "docker.cmd").write_text(docker_script, encoding="utf-8")
        env["PATH"] = str(bindir) + os.pathsep + env.get("PATH", "")
        env["FAKE_DOCKER_LOG"] = str(log)
    script = Path("scripts/dev_orchestrator/windows_watchdog.ps1").resolve()
    result = sp.run(
        [
            pwsh,
            "-NoProfile",
            "-File",
            str(script),
            "-CoordinationRoot",
            str(coord),
            "-PythonExe",
            sys.executable,
            "-RepoRoot",
            str(repo),
            "-MaxAgeSeconds",
            "0",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    return result, marker, log


def test_watchdog_removes_orphaned_container_before_restart(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    docker = """@echo off
echo %*>>\"%FAKE_DOCKER_LOG%\"
if \"%1\"==\"info\" exit /b 0
if \"%1\"==\"container\" if \"%2\"==\"inspect\" (echo true& exit /b 0)
if \"%1\"==\"rm\" if \"%2\"==\"-f\" exit /b 0
exit /b 1
"""
    result, marker, log = _watchdog_fixture(
        tmp_path,
        {
            "status": "active",
            "pid": 2147483000,
            "invocation_id": "orphan",
            "container_name": "askrex-backend-orphan",
            "process_started_at": datetime.now(UTC).isoformat(),
        },
        docker,
    )
    assert result.returncode == 2
    assert not marker.exists()
    assert "Removed orphaned Claude container" in result.stdout
    calls = log.read_text(encoding="utf-8")
    assert "info --format" in calls
    assert "container inspect" in calls
    assert "rm -f askrex-backend-orphan" in calls


def test_watchdog_fails_closed_when_docker_state_unavailable(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    docker = """@echo off
echo %*>>\"%FAKE_DOCKER_LOG%\"
exit /b 1
"""
    result, marker, _ = _watchdog_fixture(
        tmp_path,
        {
            "status": "active",
            "pid": 2147483000,
            "invocation_id": "docker-down",
            "container_name": "askrex-backend-down",
            "process_started_at": datetime.now(UTC).isoformat(),
        },
        docker,
    )
    assert result.returncode == 3
    assert marker.exists()
    assert "refusing automatic restart" in result.stdout


def test_watchdog_fails_closed_when_orphan_cleanup_fails(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    docker = """@echo off
echo %*>>\"%FAKE_DOCKER_LOG%\"
if \"%1\"==\"info\" exit /b 0
if \"%1\"==\"container\" if \"%2\"==\"inspect\" (echo true& exit /b 0)
if \"%1\"==\"rm\" if \"%2\"==\"-f\" exit /b 1
exit /b 1
"""
    result, marker, _ = _watchdog_fixture(
        tmp_path,
        {
            "status": "active",
            "pid": 2147483000,
            "invocation_id": "cleanup-fail",
            "container_name": "askrex-backend-fail",
            "process_started_at": datetime.now(UTC).isoformat(),
        },
        docker,
    )
    assert result.returncode == 3
    assert marker.exists()
    assert "refusing automatic restart" in result.stdout


def test_watchdog_reconciles_pid_reuse_identity_mismatch(tmp_path: Path) -> None:
    import os

    result, marker, _ = _watchdog_fixture(
        tmp_path,
        {
            "status": "active",
            "pid": os.getpid(),
            "invocation_id": "",
            "process_started_at": "2000-01-01T00:00:00+00:00",
        },
    )
    assert result.returncode == 2
    assert not marker.exists()
    assert "PID identity no longer matches" in result.stdout


def test_docker_cleanup_fails_closed_when_daemon_unavailable(monkeypatch) -> None:
    import subprocess as sp

    from scripts.dev_orchestrator import runner

    def fake_run(args, **_kwargs):
        if args[:3] == ["docker", "rm", "-f"]:
            return sp.CompletedProcess(args, 1, "", "daemon unavailable")
        if args[:3] == ["docker", "container", "inspect"]:
            return sp.CompletedProcess(args, 1, "", "error during connect: daemon unavailable")
        raise AssertionError(args)

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    assert runner._remove_docker_container("askrex-backend-deadbeef") is False


def test_watchdog_removes_stale_scratch_clone(tmp_path: Path) -> None:
    import json
    import os
    import shutil
    import subprocess as sp
    import sys
    import tempfile
    from datetime import UTC, datetime

    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if pwsh is None:
        pytest.skip("PowerShell is unavailable")
    coord = tmp_path / "coord"
    active = coord / "active-agents"
    active.mkdir(parents=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    scratch_root = Path(tempfile.mkdtemp(prefix="askrex-backend-watchdog-"))
    scratch = scratch_root / "repo"
    scratch.mkdir()
    sp.run(["git", "init", "-q"], cwd=scratch, check=True)
    sp.run(["git", "config", "user.email", "test@example.com"], cwd=scratch, check=True)
    sp.run(["git", "config", "user.name", "Test"], cwd=scratch, check=True)
    (scratch / "leftover.txt").write_text("x\n", encoding="utf-8")
    sp.run(["git", "add", "leftover.txt"], cwd=scratch, check=True)
    sp.run(["git", "commit", "-qm", "scratch"], cwd=scratch, check=True)
    pre_head = sp.check_output(["git", "rev-parse", "HEAD"], cwd=scratch, text=True).strip()
    from scripts.dev_orchestrator import runner

    scratch_nonce = runner._write_scratch_owner(
        scratch, role="backend", invocation_id="dead-scratch", pre_head=pre_head
    )
    write_heartbeat(
        coord / "supervisor-heartbeat.json",
        now=datetime.now(UTC),
        pid=os.getpid(),
    )
    marker = active / "backend.json"
    marker.write_text(
        json.dumps(
            {
                "status": "active",
                "pid": 2147483000,
                "invocation_id": "dead-scratch",
                "role": "backend",
                "pre_head": pre_head,
                "scratch_nonce": scratch_nonce,
                "process_started_at": datetime.now(UTC).isoformat(),
                "scratch_path": str(scratch.resolve()),
            }
        ),
        encoding="utf-8",
    )
    script = Path("scripts/dev_orchestrator/windows_watchdog.ps1").resolve()
    result = sp.run(
        [
            pwsh,
            "-NoProfile",
            "-File",
            str(script),
            "-CoordinationRoot",
            str(coord),
            "-PythonExe",
            sys.executable,
            "-RepoRoot",
            str(repo),
            "-MaxAgeSeconds",
            "0",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2, result.stdout
    assert not marker.exists()
    assert not scratch_root.exists()
    assert "Removed stale scratch clone" in result.stdout


def test_watchdog_removes_stale_dedicated_codex_scratch_clone(tmp_path: Path) -> None:
    import json
    import os
    import shutil
    import subprocess as sp
    import sys
    from datetime import UTC, datetime

    from scripts.dev_orchestrator import runner

    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if pwsh is None:
        pytest.skip("PowerShell is unavailable")
    coord = tmp_path / "coord"
    active = coord / "active-agents"
    active.mkdir(parents=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    scratch_root = coord.parent / ".askrex-agent-scratch" / "askrex-backend-codex-watchdog"
    scratch = scratch_root / "repo"
    scratch.mkdir(parents=True)
    sp.run(["git", "init", "-q"], cwd=scratch, check=True)
    sp.run(["git", "config", "user.email", "test@example.com"], cwd=scratch, check=True)
    sp.run(["git", "config", "user.name", "Test"], cwd=scratch, check=True)
    (scratch / "leftover.txt").write_text("x\n", encoding="utf-8")
    sp.run(["git", "add", "leftover.txt"], cwd=scratch, check=True)
    sp.run(["git", "commit", "-qm", "scratch"], cwd=scratch, check=True)
    pre_head = sp.check_output(["git", "rev-parse", "HEAD"], cwd=scratch, text=True).strip()
    scratch_nonce = runner._write_scratch_owner(
        scratch, role="backend", invocation_id="dead-codex-scratch", pre_head=pre_head
    )
    write_heartbeat(
        coord / "supervisor-heartbeat.json",
        now=datetime.now(UTC),
        pid=os.getpid(),
    )
    marker = active / "backend.json"
    marker.write_text(
        json.dumps(
            {
                "status": "active",
                "pid": 2147483000,
                "invocation_id": "dead-codex-scratch",
                "role": "backend",
                "pre_head": pre_head,
                "scratch_nonce": scratch_nonce,
                "process_started_at": datetime.now(UTC).isoformat(),
                "scratch_path": str(scratch.resolve()),
            }
        ),
        encoding="utf-8",
    )
    script = Path("scripts/dev_orchestrator/windows_watchdog.ps1").resolve()
    result = sp.run(
        [
            pwsh,
            "-NoProfile",
            "-File",
            str(script),
            "-CoordinationRoot",
            str(coord),
            "-PythonExe",
            sys.executable,
            "-RepoRoot",
            str(repo),
            "-MaxAgeSeconds",
            "0",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert not marker.exists()
    assert not scratch_root.exists()
    assert "Removed stale scratch clone" in result.stdout


def test_watchdog_refuses_unowned_scratch_clone(tmp_path: Path) -> None:
    import json
    import os
    import shutil
    import subprocess as sp
    import sys
    import tempfile
    from datetime import UTC, datetime

    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if pwsh is None:
        pytest.skip("PowerShell is unavailable")
    coord = tmp_path / "coord"
    active = coord / "active-agents"
    active.mkdir(parents=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    scratch_root = Path(tempfile.mkdtemp(prefix="askrex-backend-unowned-"))
    scratch = scratch_root / "repo"
    scratch.mkdir()
    write_heartbeat(
        coord / "supervisor-heartbeat.json",
        now=datetime.now(UTC),
        pid=os.getpid(),
    )
    marker = active / "backend.json"
    marker.write_text(
        json.dumps(
            {
                "status": "active",
                "pid": 2147483000,
                "invocation_id": "forged-scratch",
                "process_started_at": datetime.now(UTC).isoformat(),
                "scratch_path": str(scratch),
                "scratch_nonce": "not-owned",
            }
        ),
        encoding="utf-8",
    )
    script = Path("scripts/dev_orchestrator/windows_watchdog.ps1").resolve()
    result = sp.run(
        [
            pwsh,
            "-NoProfile",
            "-File",
            str(script),
            "-CoordinationRoot",
            str(coord),
            "-PythonExe",
            sys.executable,
            "-RepoRoot",
            str(repo),
            "-MaxAgeSeconds",
            "0",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 3
    assert marker.exists()
    assert scratch_root.exists()
    assert "refusing automatic restart" in result.stdout


def test_watchdog_fails_closed_when_container_inspect_is_ambiguous(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    docker = """@echo off
echo %*>>\"%FAKE_DOCKER_LOG%\"
if \"%1\"==\"info\" exit /b 0
if \"%1\"==\"container\" if \"%2\"==\"inspect\" (echo transport failure 1>&2& exit /b 1)
exit /b 1
"""
    result, marker, _ = _watchdog_fixture(
        tmp_path,
        {
            "status": "active",
            "pid": 2147483000,
            "invocation_id": "inspect-unknown",
            "container_name": "askrex-backend-unknown",
            "process_started_at": datetime.now(UTC).isoformat(),
        },
        docker,
    )
    assert result.returncode == 3
    assert marker.exists()
    assert "refusing automatic restart" in result.stdout


def test_watchdog_refuses_scratch_with_descendant_reparse_point(tmp_path: Path) -> None:
    import json
    import os
    import shutil
    import subprocess as sp
    import sys
    import tempfile
    from datetime import UTC, datetime

    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if pwsh is None:
        pytest.skip("PowerShell is unavailable")
    coord = tmp_path / "coord-reparse"
    active = coord / "active-agents"
    active.mkdir(parents=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    source = tmp_path / "source-reparse"
    source.mkdir()
    sp.run(["git", "init", "-q"], cwd=source, check=True)
    sp.run(["git", "config", "user.email", "test@example.com"], cwd=source, check=True)
    sp.run(["git", "config", "user.name", "Test"], cwd=source, check=True)
    (source / "README.md").write_text("ready\n", encoding="utf-8")
    sp.run(["git", "add", "README.md"], cwd=source, check=True)
    sp.run(["git", "commit", "-qm", "init"], cwd=source, check=True)
    scratch_root = Path(tempfile.mkdtemp(prefix="askrex-backend-reparse-"))
    scratch = scratch_root / "repo"
    sp.run(["git", "clone", "-q", str(source), str(scratch)], check=True)
    head = sp.check_output(["git", "rev-parse", "HEAD"], cwd=scratch, text=True).strip()
    nonce = "owned-reparse-test"
    (scratch_root / ".askrex-scratch-owner.json").write_text(
        json.dumps(
            {
                "nonce": nonce,
                "scratch_path": str(scratch.resolve()),
                "invocation_id": "descendant-reparse",
                "role": "backend",
                "pre_head": head,
            }
        ),
        encoding="utf-8",
    )
    outside = tmp_path / "outside-target"
    outside.mkdir()
    junction = scratch / "escape"
    made = sp.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
        capture_output=True,
        text=True,
        check=False,
    )
    if made.returncode != 0:
        pytest.skip(f"junction creation unavailable: {made.stderr or made.stdout}")
    write_heartbeat(
        coord / "supervisor-heartbeat.json",
        now=datetime.now(UTC),
        pid=os.getpid(),
    )
    marker = active / "backend.json"
    marker.write_text(
        json.dumps(
            {
                "status": "active",
                "pid": 2147483000,
                "invocation_id": "descendant-reparse",
                "role": "backend",
                "pre_head": head,
                "process_started_at": datetime.now(UTC).isoformat(),
                "scratch_path": str(scratch.resolve()),
                "scratch_nonce": nonce,
            }
        ),
        encoding="utf-8",
    )
    script = Path("scripts/dev_orchestrator/windows_watchdog.ps1").resolve()
    result = sp.run(
        [
            pwsh,
            "-NoProfile",
            "-File",
            str(script),
            "-CoordinationRoot",
            str(coord),
            "-PythonExe",
            sys.executable,
            "-RepoRoot",
            str(repo),
            "-MaxAgeSeconds",
            "0",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 3
    assert marker.exists()
    assert scratch_root.exists()
    assert junction.exists()
    assert outside.exists()
    assert "refusing automatic restart" in result.stdout


def test_custom_claude_executor_cannot_advance_closed_over_live_repo(tmp_path: Path) -> None:
    import json
    import subprocess as sp

    from scripts.dev_orchestrator.handoff import HandoffRequired
    from scripts.dev_orchestrator.runner import CliAgentInvoker, ProcessResult
    from scripts.dev_orchestrator.types import TaskItem, WorkerState

    root = tmp_path / "coord"
    backend = tmp_path / "backend"
    mobile = tmp_path / "mobile"
    frozen = tmp_path / "frozen"
    root.mkdir()
    frozen.mkdir()
    _git_repo(backend)
    _git_repo(mobile)
    cfg = initialize_runtime(root, backend, mobile, frozen)
    acknowledge_handoff(cfg, "backend", source="test")
    before = sp.check_output(["git", "rev-parse", "HEAD"], cwd=backend, text=True).strip()

    def execute(command, _cwd, **_kwargs):
        invocation_id = (
            command[-1].split("AskRex-Orchestrator-Invocation-ID: ", 1)[1].splitlines()[0]
        )
        (backend / "ambient.txt").write_text("ambient write\n", encoding="utf-8")
        sp.run(["git", "add", "ambient.txt"], cwd=backend, check=True)
        sp.run(
            ["git", "commit", "-qm", f"ambient\n\nAskRex-Orchestrator-Invocation: {invocation_id}"],
            cwd=backend,
            check=True,
        )
        return ProcessResult(
            0,
            json.dumps(
                {
                    "outcome": "continue",
                    "summary": "x",
                    "next_action": "x",
                    "needs_user": False,
                    "blocker_reason": "",
                }
            ),
            "",
        )

    with pytest.raises(HandoffRequired, match="custom executor modified the leased repository"):
        CliAgentInvoker(cfg, execute=execute, allow_test_executor=True).implement(
            "backend", WorkerState("backend"), TaskItem("B-AMBIENT", "ambient"), "ctx", "sonnet"
        )
    assert sp.check_output(["git", "rev-parse", "HEAD"], cwd=backend, text=True).strip() != before


def test_custom_executor_detects_live_repo_ref_mutation(tmp_path: Path) -> None:
    import json
    import subprocess as sp

    from scripts.dev_orchestrator.handoff import HandoffRequired
    from scripts.dev_orchestrator.runner import CliAgentInvoker, ProcessResult
    from scripts.dev_orchestrator.types import TaskItem, WorkerState

    root = tmp_path / "coord"
    backend = tmp_path / "backend"
    mobile = tmp_path / "mobile"
    frozen = tmp_path / "frozen"
    root.mkdir()
    frozen.mkdir()
    _git_repo(backend)
    _git_repo(mobile)
    cfg = initialize_runtime(root, backend, mobile, frozen)
    acknowledge_handoff(cfg, "backend", source="test")

    def execute(_command, _cwd, **_kwargs):
        sp.run(
            ["git", "update-ref", "refs/heads/ambient-hidden-ref", "HEAD"],
            cwd=backend,
            check=True,
        )
        return ProcessResult(
            0,
            json.dumps(
                {
                    "outcome": "continue",
                    "summary": "x",
                    "next_action": "x",
                    "needs_user": False,
                    "blocker_reason": "",
                }
            ),
            "",
        )

    with pytest.raises(HandoffRequired, match="custom executor modified the leased repository"):
        CliAgentInvoker(cfg, execute=execute, allow_test_executor=True).implement(
            "backend", WorkerState("backend"), TaskItem("B-REF", "ambient ref"), "ctx", "sonnet"
        )


def test_custom_executor_detects_modify_then_restore_live_file(tmp_path: Path) -> None:
    import json

    from scripts.dev_orchestrator.handoff import HandoffRequired
    from scripts.dev_orchestrator.runner import CliAgentInvoker, ProcessResult
    from scripts.dev_orchestrator.types import TaskItem, WorkerState

    root = tmp_path / "coord"
    backend = tmp_path / "backend"
    mobile = tmp_path / "mobile"
    frozen = tmp_path / "frozen"
    root.mkdir()
    frozen.mkdir()
    _git_repo(backend)
    _git_repo(mobile)
    cfg = initialize_runtime(root, backend, mobile, frozen)
    acknowledge_handoff(cfg, "backend", source="test")
    target = backend / "README.md"
    original = target.read_text(encoding="utf-8")

    def execute(_command, _cwd, **_kwargs):
        target.write_text("temporary ambient mutation\n", encoding="utf-8")
        target.write_text(original, encoding="utf-8")
        return ProcessResult(
            0,
            json.dumps(
                {
                    "outcome": "continue",
                    "summary": "x",
                    "next_action": "x",
                    "needs_user": False,
                    "blocker_reason": "",
                }
            ),
            "",
        )

    with pytest.raises(HandoffRequired, match="custom executor modified the leased repository"):
        CliAgentInvoker(cfg, execute=execute, allow_test_executor=True).implement(
            "backend",
            WorkerState("backend"),
            TaskItem("B-RESTORE", "ambient restore"),
            "ctx",
            "sonnet",
        )
    assert target.read_text(encoding="utf-8") == original


def test_custom_executor_detects_scratch_origin_push_to_live_repo(tmp_path: Path) -> None:
    import json
    import subprocess as sp

    from scripts.dev_orchestrator.runner import CliAgentInvoker, ProcessResult
    from scripts.dev_orchestrator.types import TaskItem, WorkerState

    root = tmp_path / "coord"
    root.mkdir()
    frozen = tmp_path / "frozen"
    frozen.mkdir()
    backend = tmp_path / "backend"
    mobile = tmp_path / "mobile"
    _git_repo(backend)
    _git_repo(mobile)
    cfg = initialize_runtime(root, backend, mobile, frozen)
    acknowledge_handoff(cfg, "backend", source="test")
    payload = json.dumps(
        {
            "outcome": "continue",
            "summary": "ok",
            "next_action": "continue",
            "needs_user": False,
            "blocker_reason": "",
        }
    )

    def execute(_command, cwd, **_kwargs):
        pushed = sp.run(
            ["git", "push", "origin", "HEAD:refs/heads/ambient-from-scratch"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
        assert pushed.returncode == 0, pushed.stdout + pushed.stderr
        return ProcessResult(0, payload, "")

    with pytest.raises(HandoffRequired, match="custom executor modified the leased repository"):
        CliAgentInvoker(cfg, execute=execute, allow_test_executor=True).implement(
            "backend", WorkerState("backend"), TaskItem("B-PUSH", "x"), "ctx", "sonnet"
        )


def test_custom_executor_detects_live_repo_mutation_when_callback_raises(tmp_path: Path) -> None:
    import subprocess as sp

    from scripts.dev_orchestrator.runner import CliAgentInvoker
    from scripts.dev_orchestrator.types import TaskItem, WorkerState

    root = tmp_path / "coord"
    root.mkdir()
    frozen = tmp_path / "frozen"
    frozen.mkdir()
    backend = tmp_path / "backend"
    mobile = tmp_path / "mobile"
    _git_repo(backend)
    _git_repo(mobile)
    cfg = initialize_runtime(root, backend, mobile, frozen)
    acknowledge_handoff(cfg, "backend", source="test")

    def execute(_command, _cwd, **_kwargs):
        sp.run(
            ["git", "update-ref", "refs/heads/ambient-exception", "HEAD"], cwd=backend, check=True
        )
        raise RuntimeError("callback failed after ambient mutation")

    with pytest.raises(HandoffRequired, match="custom executor modified the leased repository"):
        CliAgentInvoker(cfg, execute=execute, allow_test_executor=True).implement(
            "backend", WorkerState("backend"), TaskItem("B-EX", "x"), "ctx", "sonnet"
        )


def test_custom_executor_terminates_delayed_child_before_final_seal(tmp_path: Path) -> None:
    import json
    import subprocess as sp
    import sys
    import time

    from scripts.dev_orchestrator.runner import CliAgentInvoker, ProcessResult
    from scripts.dev_orchestrator.types import TaskItem, WorkerState

    root = tmp_path / "coord"
    root.mkdir()
    frozen = tmp_path / "frozen"
    frozen.mkdir()
    backend = tmp_path / "backend"
    mobile = tmp_path / "mobile"
    _git_repo(backend)
    _git_repo(mobile)
    cfg = initialize_runtime(root, backend, mobile, frozen)
    acknowledge_handoff(cfg, "backend", source="test")
    payload = json.dumps(
        {
            "outcome": "continue",
            "summary": "ok",
            "next_action": "continue",
            "needs_user": False,
            "blocker_reason": "",
        }
    )

    child_code = (
        "import subprocess,sys,time; "
        "time.sleep(0.25); "
        "subprocess.run(['git','update-ref','refs/heads/ambient-delayed','HEAD'], cwd=sys.argv[1], check=True)"
    )

    def execute(_command, _cwd, **_kwargs):
        sp.Popen([sys.executable, "-c", child_code, str(backend)])
        return ProcessResult(0, payload, "")

    result = CliAgentInvoker(cfg, execute=execute, allow_test_executor=True).implement(
        "backend", WorkerState("backend"), TaskItem("B-DELAY", "x"), "ctx", "sonnet"
    )
    assert result.outcome == "continue"
    time.sleep(0.4)
    ref = sp.run(
        ["git", "show-ref", "--verify", "--quiet", "refs/heads/ambient-delayed"],
        cwd=backend,
        check=False,
    )
    assert ref.returncode != 0


def test_tree_state_rejects_windows_directory_junction(tmp_path: Path) -> None:
    import os
    import subprocess as sp

    if os.name != "nt":
        pytest.skip("Windows junction regression")
    from scripts.dev_orchestrator import runner

    root = tmp_path / "sealed"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    junction = root / "escape"
    made = sp.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
        capture_output=True,
        text=True,
        check=False,
    )
    if made.returncode != 0:
        pytest.skip(f"junction creation unavailable: {made.stderr or made.stdout}")
    with pytest.raises(HandoffRequired, match="reparse"):
        runner._tree_state(root)


def test_custom_executor_rejects_non_temporary_runtime_config() -> None:
    from scripts.dev_orchestrator.runner import CliAgentInvoker, ProcessResult
    from scripts.dev_orchestrator.types import OrchestratorConfig

    config = OrchestratorConfig(coordination_root=Path.cwd())

    with pytest.raises(ValueError, match="temporary test runtime"):
        CliAgentInvoker(
            config,
            execute=lambda *_args, **_kwargs: ProcessResult(0, "{}", ""),
            allow_test_executor=True,
        )


def test_watchdog_binds_quarantine_to_same_filesystem_identity() -> None:
    script = Path("scripts/dev_orchestrator/windows_watchdog.ps1").read_text(encoding="utf-8")

    source_identity = script.find("$sourceIdentity = Get-StableDirectoryIdentity $parent")
    rename = script.find("[System.IO.Directory]::Move($parent, $quarantine)")
    quarantine_identity = script.find(
        "$quarantineIdentity = Get-StableDirectoryIdentity $quarantine", rename
    )
    compare = script.find("$quarantineIdentity -ne $sourceIdentity", quarantine_identity)
    delete = script.find("Remove-TreeWithoutFollowingReparse $quarantine", compare)

    assert "GetFileInformationByHandle" in script
    assert source_identity >= 0
    assert rename > source_identity
    assert quarantine_identity > rename
    assert compare > quarantine_identity
    assert delete > compare


def test_watchdog_dedicated_scratch_boundary_is_role_scoped() -> None:
    script = Path("scripts/dev_orchestrator/windows_watchdog.ps1").read_text(encoding="utf-8")

    assert ".askrex-agent-scratch" in script
    assert 'StartsWith("askrex-$Role-"' in script
    assert "Dedicated scratch boundary is a reparse point." in script


def test_watchdog_quarantines_scratch_before_destructive_delete() -> None:
    script = Path("scripts/dev_orchestrator/windows_watchdog.ps1").read_text(encoding="utf-8")

    rename = script.find("[System.IO.Directory]::Move($parent, $quarantine)")
    reverify = script.find("Assert-NoReparseTree $quarantine", rename)
    delete = script.find("Remove-TreeWithoutFollowingReparse $quarantine", reverify)

    assert rename >= 0
    assert reverify > rename
    assert delete > reverify
    assert "Remove-Item -LiteralPath $parent -Recurse" not in script


def test_watchdog_preserves_postprocessing_scratch_after_agent_exit(tmp_path: Path) -> None:
    import json
    import os
    import shutil
    import subprocess as sp
    import sys
    import tempfile
    from datetime import UTC, datetime

    from scripts.dev_orchestrator import runner

    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if pwsh is None:
        pytest.skip("PowerShell is unavailable")
    coord = tmp_path / "coord-postprocessing"
    active = coord / "active-agents"
    active.mkdir(parents=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    scratch_root = Path(tempfile.mkdtemp(prefix="askrex-backend-postprocessing-"))
    scratch = scratch_root / "repo"
    scratch.mkdir()
    sp.run(["git", "init", "-q"], cwd=scratch, check=True)
    sp.run(["git", "config", "user.email", "test@example.com"], cwd=scratch, check=True)
    sp.run(["git", "config", "user.name", "Test"], cwd=scratch, check=True)
    (scratch / "base.txt").write_text("base\n", encoding="utf-8")
    sp.run(["git", "add", "base.txt"], cwd=scratch, check=True)
    sp.run(["git", "commit", "-qm", "base"], cwd=scratch, check=True)
    pre_head = sp.check_output(["git", "rev-parse", "HEAD"], cwd=scratch, text=True).strip()
    nonce = runner._write_scratch_owner(
        scratch, role="backend", invocation_id="postprocess-test", pre_head=pre_head
    )
    (scratch / "completed.txt").write_text("valuable\n", encoding="utf-8")
    write_heartbeat(coord / "supervisor-heartbeat.json", now=datetime.now(UTC), pid=os.getpid())
    marker = active / "backend.json"
    marker.write_text(
        json.dumps(
            {
                "status": "postprocessing",
                "pid": 2147483000,
                "invocation_id": "postprocess-test",
                "role": "backend",
                "pre_head": pre_head,
                "scratch_nonce": nonce,
                "process_started_at": datetime.now(UTC).isoformat(),
                "scratch_path": str(scratch.resolve()),
            }
        ),
        encoding="utf-8",
    )
    script = Path("scripts/dev_orchestrator/windows_watchdog.ps1").resolve()
    result = sp.run(
        [
            pwsh,
            "-NoProfile",
            "-File",
            str(script),
            "-CoordinationRoot",
            str(coord),
            "-PythonExe",
            sys.executable,
            "-RepoRoot",
            str(repo),
            "-MaxAgeSeconds",
            "0",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 3, result.stdout + result.stderr
    assert marker.exists()
    assert scratch_root.exists()
    assert (scratch / "completed.txt").read_text(encoding="utf-8") == "valuable\n"
    assert "postprocessing" in result.stdout.lower()
