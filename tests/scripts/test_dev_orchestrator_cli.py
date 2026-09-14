import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scripts.dev_orchestrator.cli import (
    heartbeat_is_stale,
    initialize_runtime,
    load_config,
    record_confirmed_acceptance,
    record_confirmed_resets,
    render_status,
    set_observe_only,
)
from scripts.dev_orchestrator.lifecycle import SupervisorLock, read_heartbeat, write_heartbeat


def _git_repo(path: Path) -> None:

    mobile = "mobile" in path.name.lower()
    base = "main" if mobile else "master"
    origin = (
        "https://github.com/Blueibear/AskRex.git"
        if mobile
        else "https://github.com/Blueibear/AskRex-Assistant.git"
    )
    if path.exists():
        if any(path.iterdir()):
            raise AssertionError("test worktree path must be empty")
        path.rmdir()
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


def _handoff(config) -> None:
    from scripts.dev_orchestrator.handoff import acknowledge_handoff

    acknowledge_handoff(config, "backend", source="test")
    acknowledge_handoff(config, "mobile", source="test")


def test_init_creates_runtime_without_overwriting_protocol(tmp_path: Path) -> None:
    root = tmp_path / "coordination"
    root.mkdir()
    protocol = root / "PROTOCOL.md"
    protocol.write_text("authoritative\n", encoding="utf-8")
    backend = tmp_path / "backend"
    mobile = tmp_path / "mobile"
    frozen = tmp_path / "rex-ai-pc-test"
    for path in (backend, mobile, frozen):
        path.mkdir()

    initialize_runtime(root, backend, mobile, frozen)

    assert protocol.read_text(encoding="utf-8") == "authoritative\n"
    for name in ("state", "queues", "alerts", "logs", "mailbox/supervisor"):
        assert (root / name).is_dir()


def test_init_persists_observe_only_config_and_loads_it(tmp_path: Path) -> None:
    root = tmp_path / "coordination"
    backend = tmp_path / "backend"
    mobile = tmp_path / "mobile"
    frozen = tmp_path / "rex-ai-pc-test"
    for path in (root, backend, mobile, frozen):
        path.mkdir()
    initialize_runtime(root, backend, mobile, frozen)
    config = load_config(root)
    assert config.observe_only is True
    assert config.banked_resets_remaining == 3
    assert config.backend_root == backend
    assert config.mobile_root == mobile
    assert config.frozen_worktree == frozen


def test_heartbeat_staleness_is_deterministic(tmp_path: Path) -> None:
    path = tmp_path / "heartbeat.json"
    now = datetime(2026, 9, 11, tzinfo=UTC)
    write_heartbeat(
        path,
        now=now - timedelta(seconds=30),
        pid=123,
        process_started_filetime=123456789,
    )
    assert heartbeat_is_stale(path, now=now, max_age_seconds=60) is False
    assert heartbeat_is_stale(path, now=now, max_age_seconds=20) is True
    assert heartbeat_is_stale(tmp_path / "missing.json", now=now, max_age_seconds=60) is True


def test_future_or_malformed_heartbeat_is_stale(tmp_path: Path) -> None:
    path = tmp_path / "heartbeat.json"
    now = datetime(2026, 9, 12, 23, 0, tzinfo=UTC)
    valid_start = now - timedelta(minutes=5)

    path.write_text(
        json.dumps(
            {
                "pid": 123,
                "timestamp": (now + timedelta(hours=1)).isoformat(),
                "process_started_at": valid_start.isoformat(),
            }
        ),
        encoding="utf-8",
    )
    assert heartbeat_is_stale(path, now=now, max_age_seconds=180) is True

    malformed = (
        {"pid": 0, "timestamp": now.isoformat(), "process_started_at": valid_start.isoformat()},
        {
            "pid": 123,
            "timestamp": now.replace(tzinfo=None).isoformat(),
            "process_started_at": valid_start.isoformat(),
        },
        {"pid": 123, "timestamp": now.isoformat()},
        {
            "pid": 123,
            "timestamp": now.isoformat(),
            "process_started_at": valid_start.isoformat(),
            "unexpected": True,
        },
    )
    for payload in malformed:
        path.write_text(json.dumps(payload), encoding="utf-8")
        assert heartbeat_is_stale(path, now=now, max_age_seconds=180) is True


def test_read_heartbeat_retries_transient_windows_sharing_violation(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "heartbeat.json"
    write_heartbeat(path, pid=123)
    original = Path.read_text
    attempts = 0

    def flaky_read_text(target, *args, **kwargs):
        nonlocal attempts
        if target == path and attempts == 0:
            attempts += 1
            raise PermissionError(13, "sharing violation")
        return original(target, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", flaky_read_text)
    payload = read_heartbeat(path)
    assert payload is not None
    assert payload["pid"] == 123
    assert attempts == 1


def test_supervisor_lock_rejects_second_owner(tmp_path: Path) -> None:
    lock_path = tmp_path / "supervisor.lock"
    first = SupervisorLock(lock_path)
    second = SupervisorLock(lock_path)
    first.acquire()
    try:
        with pytest.raises(RuntimeError, match="already running"):
            second.acquire()
    finally:
        first.release()


def test_supervisor_lock_never_reclaims_stale_path_by_unlink(tmp_path: Path, monkeypatch) -> None:
    lock_path = tmp_path / "supervisor.lock"
    lock_path.write_text("stale-owner\n", encoding="ascii")
    original_unlink = Path.unlink

    def forbidden_unlink(target, *args, **kwargs):
        if target == lock_path:
            raise AssertionError("lock ownership must not depend on deleting the pathname")
        return original_unlink(target, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", forbidden_unlink)
    lock = SupervisorLock(lock_path)
    lock.acquire()
    lock.release()
    assert lock_path.exists()


def test_supervisor_lock_cross_process_ignores_mutable_pid_contents(tmp_path: Path) -> None:
    lock_path = tmp_path / "supervisor.lock"
    first = SupervisorLock(lock_path)
    child = """from pathlib import Path
import sys
from scripts.dev_orchestrator.lifecycle import SupervisorLock
lock = SupervisorLock(Path(sys.argv[1]))
try:
    lock.acquire()
except RuntimeError:
    raise SystemExit(23)
else:
    lock.release()
"""
    first.acquire()
    try:
        blocked = subprocess.run(
            [sys.executable, "-c", child, str(lock_path)],
            cwd=Path.cwd(),
            capture_output=True,
            text=True,
            check=False,
        )
        assert blocked.returncode == 23, blocked.stdout + blocked.stderr
    finally:
        first.release()

    acquired = subprocess.run(
        [sys.executable, "-c", child, str(lock_path)],
        cwd=Path.cwd(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert acquired.returncode == 0, acquired.stdout + acquired.stderr


def test_status_reports_both_workers_and_observe_only(tmp_path: Path) -> None:
    root = tmp_path / "coordination"
    backend = tmp_path / "backend"
    mobile = tmp_path / "mobile"
    frozen = tmp_path / "rex-ai-pc-test"
    for path in (root, backend, mobile, frozen):
        path.mkdir()
    initialize_runtime(root, backend, mobile, frozen)
    payload = json.loads(render_status(load_config(root)))
    assert payload["observe_only"] is True
    assert payload["banked_resets_remaining"] == 3
    assert set(payload["workers"]) == {"backend", "mobile"}


def test_run_loop_writes_heartbeat_and_observe_only_makes_no_model_calls(tmp_path: Path) -> None:
    root = tmp_path / "coordination"
    backend = tmp_path / "backend"
    mobile = tmp_path / "mobile"
    frozen = tmp_path / "rex-ai-pc-test"
    for path in (root, backend, mobile, frozen):
        path.mkdir()
    config = initialize_runtime(root, backend, mobile, frozen)
    from scripts.dev_orchestrator.cli import run_loop

    run_loop(config, max_cycles=2, sleep_fn=lambda _: None)
    heartbeat = json.loads((root / "supervisor-heartbeat.json").read_text(encoding="utf-8"))
    assert heartbeat["pid"] > 0
    assert heartbeat["timestamp"]


def test_parser_exposes_required_commands() -> None:
    from scripts.dev_orchestrator.cli import build_parser

    parser = build_parser()
    init_args = [
        "init",
        "--coordination-root",
        "C:/coord",
        "--backend-root",
        "C:/backend",
        "--mobile-root",
        "C:/mobile",
        "--frozen-worktree",
        "C:/frozen",
    ]
    assert parser.parse_args(init_args).command == "init"
    for command in ("status", "cycle", "run", "activate", "pause"):
        parsed = parser.parse_args([command, "--coordination-root", "C:/coord"])
        assert parsed.command == command
    parsed = parser.parse_args(
        ["confirm-resets", "--coordination-root", "C:/coord", "--remaining", "2"]
    )
    assert parsed.command == "confirm-resets"
    assert parsed.remaining == 2


def test_windows_watchdog_is_model_free() -> None:
    script = Path("scripts/dev_orchestrator/windows_watchdog.ps1").read_text(encoding="utf-8")
    lowered = script.lower()
    assert "supervisor-heartbeat.json" in script
    assert "codex" not in lowered
    assert "gpt-" not in lowered
    assert "start-process -filepath claude" not in lowered
    assert "& claude" not in lowered
    assert " claude -p" not in lowered


def test_active_cycle_uses_injected_invoker(tmp_path: Path) -> None:
    from dataclasses import replace

    from scripts.dev_orchestrator.cli import run_cycle
    from scripts.dev_orchestrator.types import AgentResult, TaskItem

    root = tmp_path / "coordination"
    backend = tmp_path / "backend"
    backend.mkdir()
    mobile = tmp_path / "mobile"
    mobile.mkdir()
    frozen = tmp_path / "rex-ai-pc-test"
    frozen.mkdir()
    root.mkdir()
    _git_repo(backend)
    _git_repo(mobile)
    base_config = initialize_runtime(root, backend, mobile, frozen)
    _handoff(base_config)
    config = replace(base_config, observe_only=False)

    class Invoker:
        def implement(self, *args):
            return AgentResult("continue", "ok", "continue")

        def review(self, *args):
            raise AssertionError("unexpected review")

        def lead(self, *args, **kwargs):
            return AgentResult("done", "none", "")

    from scripts.dev_orchestrator.supervisor import Supervisor

    Supervisor(config, Invoker()).enqueue("backend", TaskItem("B-1", "work"))
    run_cycle(config, invoker=Invoker())
    assert json.loads((root / "state" / "backend.json").read_text())["status"] == "implementing"


def test_pause_activate_and_confirmed_reset_update_config(tmp_path: Path) -> None:
    from scripts.dev_orchestrator.cli import record_confirmed_resets, set_observe_only

    root = tmp_path / "coordination"
    backend = tmp_path / "backend"
    backend.mkdir()
    mobile = tmp_path / "mobile"
    mobile.mkdir()
    frozen = tmp_path / "rex-ai-pc-test"
    frozen.mkdir()
    root.mkdir()
    initialize_runtime(root, backend, mobile, frozen)
    set_observe_only(root, False)
    assert load_config(root).observe_only is False
    set_observe_only(root, True)
    assert load_config(root).observe_only is True
    record_confirmed_resets(root, 2)
    assert load_config(root).banked_resets_remaining == 2


def test_main_status_activate_pause_and_confirm_resets(tmp_path: Path, capsys) -> None:
    from scripts.dev_orchestrator.cli import main

    root = tmp_path / "coordination"
    backend = tmp_path / "backend"
    backend.mkdir()
    mobile = tmp_path / "mobile"
    mobile.mkdir()
    frozen = tmp_path / "rex-ai-pc-test"
    frozen.mkdir()
    root.mkdir()
    config = initialize_runtime(root, backend, mobile, frozen)
    _git_repo(backend)
    _git_repo(mobile)
    _handoff(config)

    assert main(["status", "--coordination-root", str(root)]) == 0
    assert '"observe_only": true' in capsys.readouterr().out
    assert main(["activate", "--coordination-root", str(root)]) == 0
    assert load_config(root).observe_only is False
    assert main(["pause", "--coordination-root", str(root)]) == 0
    assert load_config(root).observe_only is True
    assert main(["confirm-resets", "--coordination-root", str(root), "--remaining", "2"]) == 0
    assert load_config(root).banked_resets_remaining == 2


def test_heartbeat_pump_stays_fresh_during_long_cycle(tmp_path: Path) -> None:
    import time as wall_time

    from scripts.dev_orchestrator.lifecycle import HeartbeatPump

    path = tmp_path / "heartbeat.json"
    with HeartbeatPump(path, interval_seconds=0.01):
        first_payload = read_heartbeat(path)
        assert first_payload is not None
        first = first_payload["timestamp"]
        wall_time.sleep(0.04)
        second_payload = read_heartbeat(path)
        assert second_payload is not None
        second = second_payload["timestamp"]
    assert second != first


def test_run_loop_reloads_config_so_activation_takes_effect(tmp_path: Path) -> None:
    from scripts.dev_orchestrator.cli import run_loop

    root = tmp_path / "coordination"
    backend = tmp_path / "backend"
    backend.mkdir()
    mobile = tmp_path / "mobile"
    mobile.mkdir()
    frozen = tmp_path / "rex-ai-pc-test"
    frozen.mkdir()
    root.mkdir()
    config = initialize_runtime(root, backend, mobile, frozen)
    _git_repo(backend)
    _git_repo(mobile)
    _handoff(config)
    calls = []

    class Invoker:
        def implement(self, *args):
            raise AssertionError("unexpected")

        def review(self, *args):
            raise AssertionError("unexpected")

        def lead(self, role, *args, **kwargs):
            calls.append(role)
            from scripts.dev_orchestrator.types import AgentResult

            return AgentResult("done", "none", "")

    def activate_after_first(_seconds):
        set_observe_only(root, False)

    run_loop(config, invoker=Invoker(), max_cycles=2, sleep_fn=activate_after_first)
    assert calls == ["backend", "mobile"]


def test_confirmed_reset_resumes_only_usage_blocked_workers(tmp_path: Path) -> None:
    root = tmp_path / "coordination"
    backend = tmp_path / "backend"
    backend.mkdir()
    mobile = tmp_path / "mobile"
    mobile.mkdir()
    frozen = tmp_path / "rex-ai-pc-test"
    frozen.mkdir()
    root.mkdir()
    initialize_runtime(root, backend, mobile, frozen)
    from scripts.dev_orchestrator.storage import AtomicJsonStore

    AtomicJsonStore(root / "state" / "backend.json").write(
        {
            "role": "backend",
            "status": "blocked_user",
            "task": {"task_id": "B-1", "prompt": "x", "feedback": ""},
            "blocker_kind": "usage_limit",
            "resume_status": "reviewing",
        }
    )
    AtomicJsonStore(root / "state" / "mobile.json").write(
        {
            "role": "mobile",
            "status": "blocked_user",
            "task": {"task_id": "M-1", "prompt": "y", "feedback": ""},
            "blocker_kind": "human",
            "resume_status": "implementing",
        }
    )

    record_confirmed_resets(root, 2)

    backend_state = AtomicJsonStore(root / "state" / "backend.json").read()
    mobile_state = AtomicJsonStore(root / "state" / "mobile.json").read()
    assert backend_state["status"] == "reviewing"
    assert backend_state["blocker_kind"] == ""
    assert mobile_state["status"] == "blocked_user"


def test_main_nonce_handoff_commands_create_supervisor_lease(tmp_path: Path, capsys) -> None:
    from scripts.dev_orchestrator.cli import main

    root = tmp_path / "coordination"
    backend = tmp_path / "backend"
    mobile = tmp_path / "mobile"
    frozen = tmp_path / "rex-ai-pc-test"
    for path in (root, frozen):
        path.mkdir()
    _git_repo(backend)
    _git_repo(mobile)
    initialize_runtime(root, backend, mobile, frozen)

    assert (
        main(
            [
                "request-handoff",
                "--coordination-root",
                str(root),
                "--role",
                "backend",
                "--worker-session",
                "backend-browser-session",
            ]
        )
        == 0
    )
    request_path = Path(capsys.readouterr().out.strip())
    nonce = json.loads(request_path.read_text(encoding="utf-8"))["nonce"]
    assert (
        main(
            [
                "operator-ack-handoff",
                "--coordination-root",
                str(root),
                "--role",
                "backend",
                "--nonce",
                nonce,
                "--worker-session",
                "backend-browser-session",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert (
        main(
            [
                "accept-handoff",
                "--coordination-root",
                str(root),
                "--role",
                "backend",
                "--nonce",
                nonce,
            ]
        )
        == 0
    )
    lease_path = Path(capsys.readouterr().out.strip())
    lease = json.loads(lease_path.read_text(encoding="utf-8"))
    assert lease["owner"] == "supervisor"
    assert lease["handoff_nonce"] == nonce


def test_main_reset_policy_command_can_release_final_reset_reserve(tmp_path: Path) -> None:
    from scripts.dev_orchestrator.cli import main

    root = tmp_path / "coordination"
    backend = tmp_path / "backend"
    mobile = tmp_path / "mobile"
    frozen = tmp_path / "rex-ai-pc-test"
    root.mkdir()
    frozen.mkdir()
    _git_repo(backend)
    _git_repo(mobile)
    config = initialize_runtime(root, backend, mobile, frozen)
    _handoff(config)

    assert (
        main(
            ["set-reset-policy", "--coordination-root", str(root), "--reserve-last-reset", "false"]
        )
        == 0
    )
    assert load_config(root).reserve_last_reset is False


def test_main_confirm_acceptance_records_human_evidence(tmp_path: Path) -> None:
    from scripts.dev_orchestrator.cli import main

    root = tmp_path / "coordination"
    backend = tmp_path / "backend"
    mobile = tmp_path / "mobile"
    frozen = tmp_path / "rex-ai-pc-test"
    root.mkdir()
    frozen.mkdir()
    _git_repo(backend)
    _git_repo(mobile)
    config = initialize_runtime(root, backend, mobile, frozen)
    _handoff(config)

    assert (
        main(
            [
                "confirm-acceptance",
                "--coordination-root",
                str(root),
                "--role",
                "mobile",
                "--kind",
                "physical",
                "--evidence",
                "iPhone acceptance passed",
            ]
        )
        == 0
    )
    payload = json.loads(
        (root / "completion" / "mobile-acceptance.json").read_text(encoding="utf-8")
    )
    assert payload["physical"]["confirmed"] is True
    assert payload["physical"]["evidence"] == "iPhone acceptance passed"


def test_confirmed_acceptance_resumes_only_acceptance_blocker(tmp_path: Path) -> None:
    from scripts.dev_orchestrator.storage import AtomicJsonStore

    root = tmp_path / "coordination"
    root.mkdir()
    (root / "PROTOCOL.md").write_text("protocol\n", encoding="utf-8")
    backend = tmp_path / "backend"
    mobile = tmp_path / "mobile"
    frozen = tmp_path / "rex-ai-pc-test"
    frozen.mkdir()
    _git_repo(backend)
    _git_repo(mobile)
    config = initialize_runtime(root, backend, mobile, frozen)
    _handoff(config)
    store = AtomicJsonStore(root / "state" / "backend.json")
    store.write(
        {
            "role": "backend",
            "status": "blocked_user",
            "task": None,
            "blocked_reason": "acceptance required",
            "blocker_kind": "acceptance",
            "resume_status": "planning",
        }
    )

    record_confirmed_acceptance(root, "backend", "physical", "Windows acceptance passed")
    state = store.read()
    assert state["status"] == "planning"
    assert state["blocker_kind"] == ""
    assert state["resume_status"] is None


def test_resume_human_blocker_requires_matching_kind(tmp_path: Path) -> None:
    from scripts.dev_orchestrator.cli import resume_confirmed_human_blocker
    from scripts.dev_orchestrator.storage import AtomicJsonStore

    root = tmp_path / "coordination"
    root.mkdir()
    store = AtomicJsonStore(root / "state" / "backend.json")
    store.write(
        {
            "role": "backend",
            "status": "blocked_user",
            "task": None,
            "blocked_reason": "login required",
            "blocker_kind": "github_auth",
            "resume_status": "planning",
        }
    )
    with pytest.raises(ValueError, match="blocker kind"):
        resume_confirmed_human_blocker(root, "backend", "human")
    resume_confirmed_human_blocker(root, "backend", "github_auth")
    state = store.read()
    assert state["status"] == "planning"
    assert state["blocker_kind"] == ""
