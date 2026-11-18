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


def test_claude_cloud_mode_round_trips_and_requires_pause(tmp_path: Path) -> None:
    from scripts.dev_orchestrator.cli import set_claude_cloud_mode, set_observe_only

    root = tmp_path / "coordination"
    backend = tmp_path / "backend"
    mobile = tmp_path / "mobile"
    frozen = tmp_path / "rex-ai-pc-test"
    for path in (root, backend, mobile, frozen):
        path.mkdir()

    initialize_runtime(root, backend, mobile, frozen)
    updated = set_claude_cloud_mode(root, "preferred")

    assert updated.claude_cloud_mode == "preferred"
    assert load_config(root).claude_cloud_mode == "preferred"
    assert load_config(root).claude_cloud_timeout_minutes == 240

    set_observe_only(root, False)
    with pytest.raises(ValueError, match="pause the supervisor"):
        set_claude_cloud_mode(root, "fallback")


def test_deferred_task_prefixes_round_trip_in_config(tmp_path: Path) -> None:
    from dataclasses import replace

    from scripts.dev_orchestrator.cli import save_config

    root = tmp_path / "coordination"
    backend = tmp_path / "backend"
    mobile = tmp_path / "mobile"
    frozen = tmp_path / "rex-ai-pc-test"
    for candidate in (root, backend, mobile, frozen):
        candidate.mkdir()

    config = initialize_runtime(root, backend, mobile, frozen)
    save_config(replace(config, deferred_task_prefixes=("S35-",)))

    loaded = load_config(root)

    assert loaded.deferred_task_prefixes == ("S35-",)


def test_deferred_issue_ids_round_trip_in_config(tmp_path: Path) -> None:
    from dataclasses import replace

    from scripts.dev_orchestrator.cli import save_config

    root = tmp_path / "coordination"
    backend = tmp_path / "backend"
    mobile = tmp_path / "mobile"
    frozen = tmp_path / "rex-ai-pc-test"
    for candidate in (root, backend, mobile, frozen):
        candidate.mkdir()

    config = initialize_runtime(root, backend, mobile, frozen)
    save_config(replace(config, deferred_issue_ids=("STORY-S35-SPEECH-ROUTER",)))

    loaded = load_config(root)

    assert loaded.deferred_issue_ids == ("STORY-S35-SPEECH-ROUTER",)


def test_defer_issue_cli_requires_pause_and_releases_matching_blocked_task(
    tmp_path: Path, capsys
) -> None:
    from dataclasses import replace

    from scripts.dev_orchestrator.cli import main, save_config
    from scripts.dev_orchestrator.storage import AtomicJsonStore
    from scripts.dev_orchestrator.types import TaskItem, WorkerState, WorkerStatus

    root = tmp_path / "coordination"
    backend = tmp_path / "backend"
    mobile = tmp_path / "mobile"
    frozen = tmp_path / "rex-ai-pc-test"
    for candidate in (root, backend, mobile, frozen):
        candidate.mkdir()
    initialize_runtime(root, backend, mobile, frozen)
    (root / "issues").mkdir()
    issue = root / "issues" / "STORY-S35-SPEECH-ROUTER.md"
    issue.write_text(
        "# STORY-S35-SPEECH-ROUTER\n\nStatus: open\nOwner: both\n",
        encoding="utf-8",
    )
    AtomicJsonStore(root / "state" / "backend.json").write(
        WorkerState(
            role="backend",
            status=WorkerStatus.BLOCKED_USER,
            task=TaskItem("STORY-S35-SPEECH-ROUTER", "deferred work"),
            blocked_reason="auth required",
            blocker_kind="auth",
            resume_status=WorkerStatus.IMPLEMENTING,
            claude_session_id="keep-claude",
        ).to_dict()
    )

    assert (
        main(
            [
                "defer-issue",
                "--coordination-root",
                str(root),
                "--issue-id",
                "STORY-S35-SPEECH-ROUTER",
                "--clear-task-id",
                "STORY-S35-SPEECH-ROUTER",
            ]
        )
        == 0
    )
    capsys.readouterr()

    config = load_config(root)
    state = AtomicJsonStore(root / "state" / "backend.json").read()
    assert config.deferred_issue_ids == ("STORY-S35-SPEECH-ROUTER",)
    assert state["status"] == "idle"
    assert state["task"] is None
    assert state["blocked_reason"] == ""
    assert state["claude_session_id"] == "keep-claude"
    original = issue.read_text(encoding="utf-8")

    assert (
        main(
            [
                "resume-issue",
                "--coordination-root",
                str(root),
                "--issue-id",
                "STORY-S35-SPEECH-ROUTER",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert load_config(root).deferred_issue_ids == ()
    assert issue.read_text(encoding="utf-8") == original

    save_config(replace(load_config(root), observe_only=False))
    with pytest.raises(ValueError, match="pause"):
        main(
            [
                "defer-issue",
                "--coordination-root",
                str(root),
                "--issue-id",
                "STORY-S35-SPEECH-ROUTER",
            ]
        )


def test_openai_policy_defaults_are_fail_closed_and_secret_free(tmp_path: Path) -> None:
    import json
    from decimal import Decimal

    from scripts.dev_orchestrator.cli import _config_payload

    root = tmp_path / "coordination"
    backend = tmp_path / "backend"
    mobile = tmp_path / "mobile"
    frozen = tmp_path / "rex-ai-pc-test"
    for candidate in (root, backend, mobile, frozen):
        candidate.mkdir()

    config = initialize_runtime(root, backend, mobile, frozen)
    payload = _config_payload(config)

    assert config.openai_worker_enabled is False
    assert config.openai_monthly_budget_usd == Decimal("30.00")
    assert config.openai_astra_enabled is True
    assert config.openai_project_hard_limit_confirmed is False
    assert payload["openai_monthly_budget_usd"] == "30.00"
    assert "api_key" not in json.dumps(payload).lower()


def test_openai_policy_config_round_trips_without_a_credential(tmp_path: Path) -> None:
    import json
    from dataclasses import replace
    from decimal import Decimal

    from scripts.dev_orchestrator.cli import _config_payload, save_config

    root = tmp_path / "coordination"
    backend = tmp_path / "backend"
    mobile = tmp_path / "mobile"
    frozen = tmp_path / "rex-ai-pc-test"
    for candidate in (root, backend, mobile, frozen):
        candidate.mkdir()

    config = initialize_runtime(root, backend, mobile, frozen)
    configured = replace(
        config,
        openai_worker_enabled=True,
        openai_monthly_budget_usd=Decimal("30.00"),
        openai_project_id="proj-ralph",
        openai_project_hard_limit_confirmed=True,
        openai_timeout_seconds=90,
        openai_max_input_chars=100_000,
        openai_max_output_tokens=3_000,
        openai_max_calls_per_cycle=3,
    )
    save_config(configured)

    loaded = load_config(root)
    payload = _config_payload(loaded)
    openai_fields = (
        "openai_worker_enabled",
        "openai_review_model",
        "openai_escalation_model",
        "openai_planning_model",
        "openai_astra_model",
        "openai_astra_enabled",
        "openai_monthly_budget_usd",
        "openai_project_id",
        "openai_project_hard_limit_confirmed",
        "openai_timeout_seconds",
        "openai_max_input_chars",
        "openai_max_output_tokens",
        "openai_max_calls_per_cycle",
        "openai_max_astra_calls_per_escalation",
    )
    for field_name in openai_fields:
        assert getattr(loaded, field_name) == getattr(configured, field_name)
    assert "api_key" not in json.dumps(payload).lower()


def test_confirm_openai_project_limit_records_prerequisite_without_enabling_worker(
    tmp_path: Path, capsys
) -> None:
    from scripts.dev_orchestrator.cli import main

    root = tmp_path / "coordination"
    backend = tmp_path / "backend"
    mobile = tmp_path / "mobile"
    frozen = tmp_path / "rex-ai-pc-test"
    for candidate in (root, backend, mobile, frozen):
        candidate.mkdir()
    initialize_runtime(root, backend, mobile, frozen)

    assert (
        main(
            [
                "confirm-openai-project-limit",
                "--coordination-root",
                str(root),
                "--project-id",
                "proj-ralph",
                "--monthly-usd",
                "30.00",
            ]
        )
        == 0
    )
    capsys.readouterr()
    loaded = load_config(root)
    assert loaded.openai_project_id == "proj-ralph"
    assert loaded.openai_project_hard_limit_confirmed is True
    assert loaded.openai_worker_enabled is False

    with pytest.raises(ValueError):
        main(
            [
                "confirm-openai-project-limit",
                "--coordination-root",
                str(root),
                "--project-id",
                "proj-ralph",
                "--monthly-usd",
                "29.99",
            ]
        )


def test_status_exposes_privacy_safe_openai_budget_summary(tmp_path: Path) -> None:
    import json

    from scripts.dev_orchestrator.openai_budget import OpenAIBudgetLedger

    root = tmp_path / "coordination"
    backend = tmp_path / "backend"
    mobile = tmp_path / "mobile"
    frozen = tmp_path / "rex-ai-pc-test"
    for candidate in (root, backend, mobile, frozen):
        candidate.mkdir()
    config = initialize_runtime(root, backend, mobile, frozen)
    reservation = OpenAIBudgetLedger(
        root, monthly_cap_usd=config.openai_monthly_budget_usd
    ).reserve(
        model="gpt-5.6-terra",
        input_token_ceiling=1_000,
        output_token_ceiling=100,
    )

    status = json.loads(render_status(config))
    assert status["openai_worker_enabled"] is False
    assert status["openai_project_hard_limit_confirmed"] is False
    assert status["openai_routing_policy"] == "chatgpt_subscription_first_api_fallback"
    budget = status["openai_api_budget"]
    assert budget["cap_usd"] == "30.00"
    assert budget["spent_or_reserved_usd"] == str(reservation.reserved_usd)
    assert budget["remaining_usd"] == str(
        config.openai_monthly_budget_usd - reservation.reserved_usd
    )
    assert set(budget) == {
        "month",
        "cap_usd",
        "spent_or_reserved_usd",
        "remaining_usd",
    }


def test_config_round_trips_iteration_validation_metadata(tmp_path: Path) -> None:
    from dataclasses import replace

    from scripts.dev_orchestrator.cli import save_config

    root = tmp_path / "coordination"
    backend = tmp_path / "backend"
    mobile = tmp_path / "mobile"
    frozen = tmp_path / "rex-ai-pc-test"
    for path in (root, backend, mobile, frozen):
        path.mkdir()
    config = initialize_runtime(root, backend, mobile, frozen)
    metadata = {
        "iteration_validation": {
            "enabled": True,
            "backend": {
                "gates": [{"name": "focused", "command": ["py", "-3.11", "-m", "pytest", "-q"]}]
            },
        }
    }

    save_config(replace(config, metadata=metadata))

    assert load_config(root).metadata == metadata


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
    for command in (
        "status",
        "cycle",
        "run",
        "activate",
        "pause",
        "enable-openai-worker",
        "disable-openai-worker",
    ):
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
    record_confirmed_resets(root, 0)
    assert load_config(root).banked_resets_remaining == 0
    record_confirmed_resets(root, 1)
    assert load_config(root).banked_resets_remaining == 1


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
    assert sorted(calls) == ["backend", "mobile"]


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


def test_openai_worker_enable_requires_project_cap_and_vault_credential(tmp_path: Path) -> None:
    from decimal import Decimal
    from types import SimpleNamespace

    from scripts.dev_orchestrator.cli import (
        record_openai_project_limit_confirmation,
        set_openai_worker_enabled,
    )

    root = tmp_path / "coordination"
    backend = tmp_path / "backend"
    mobile = tmp_path / "mobile"
    frozen = tmp_path / "frozen"
    for path in (root, backend, mobile, frozen):
        path.mkdir()
    initialize_runtime(root, backend, mobile, frozen)

    class Manager:
        def __init__(self, credential):
            self.credential = credential

        def get_credential(self, service):
            assert service == "openai"
            return self.credential

    vault_credential = SimpleNamespace(token="test-secret-token", source="vault")
    with pytest.raises(ValueError, match="hard limit"):
        set_openai_worker_enabled(root, True, credential_manager=Manager(vault_credential))

    record_openai_project_limit_confirmation(root, "proj-test", Decimal("30.00"))
    with pytest.raises(ValueError, match="credential"):
        set_openai_worker_enabled(root, True, credential_manager=Manager(None))
    runtime_credential = SimpleNamespace(token="test-secret-token", source="runtime")
    with pytest.raises(ValueError, match="vault-backed"):
        set_openai_worker_enabled(root, True, credential_manager=Manager(runtime_credential))
    expired_credential = SimpleNamespace(
        token="test-secret-token", source="vault", is_expired=lambda: True
    )
    with pytest.raises(ValueError, match="usable"):
        set_openai_worker_enabled(root, True, credential_manager=Manager(expired_credential))

    enabled = set_openai_worker_enabled(root, True, credential_manager=Manager(vault_credential))
    assert enabled.openai_worker_enabled is True
    config_text = (root / "orchestrator-config.json").read_text(encoding="utf-8")
    assert "test-secret-token" not in config_text

    disabled = set_openai_worker_enabled(root, False, credential_manager=Manager(None))
    assert disabled.openai_worker_enabled is False


def test_build_active_invoker_wires_openai_router_only_when_enabled(
    tmp_path: Path, monkeypatch
) -> None:
    from dataclasses import replace
    from types import SimpleNamespace

    from scripts.dev_orchestrator import cli as cli_module

    root = tmp_path / "coordination"
    backend = tmp_path / "backend"
    mobile = tmp_path / "mobile"
    frozen = tmp_path / "frozen"
    for path in (root, backend, mobile, frozen):
        path.mkdir()
    base = initialize_runtime(root, backend, mobile, frozen)
    config = replace(
        base,
        observe_only=False,
        openai_worker_enabled=True,
        openai_project_id="proj-test",
        openai_project_hard_limit_confirmed=True,
    )
    credential = SimpleNamespace(token="test-secret-token", source="vault")

    class Manager:
        def get_credential(self, service):
            assert service == "openai"
            return credential

    active = cli_module._build_active_invoker(config, credential_manager=Manager())

    from scripts.dev_orchestrator.provider_routing import ProviderRoutingInvoker

    assert isinstance(active, ProviderRoutingInvoker)
    assert active.openai_worker is not None
    assert active.cli_invoker.__class__.__name__ == "CliAgentInvoker"

    class MissingCredential:
        def get_credential(self, service):
            assert service == "openai"
            return None

    with pytest.raises(ValueError, match="vault-backed"):
        cli_module._build_active_invoker(config, credential_manager=MissingCredential())

    disabled = replace(config, openai_worker_enabled=False)

    class MustNotReadCredential:
        def get_credential(self, service):
            raise AssertionError(f"credential lookup not expected for {service}")

    cli_only = cli_module._build_active_invoker(
        disabled, credential_manager=MustNotReadCredential()
    )
    assert cli_only.__class__.__name__ == "CliAgentInvoker"


def test_active_cycle_uses_default_invoker_builder(tmp_path: Path, monkeypatch) -> None:
    from dataclasses import replace

    from scripts.dev_orchestrator import cli as cli_module
    from scripts.dev_orchestrator.supervisor import Supervisor
    from scripts.dev_orchestrator.types import AgentResult, TaskItem

    root = tmp_path / "coordination"
    backend = tmp_path / "backend"
    mobile = tmp_path / "mobile"
    frozen = tmp_path / "frozen"
    for path in (root, backend, mobile, frozen):
        path.mkdir()
    _git_repo(backend)
    _git_repo(mobile)
    base = initialize_runtime(root, backend, mobile, frozen)
    _handoff(base)
    config = replace(base, observe_only=False)
    calls = []

    class Invoker:
        def implement(self, *args):
            return AgentResult("continue", "ok", "continue")

        def review(self, *args):
            raise AssertionError("unexpected review")

        def lead(self, *args, **kwargs):
            return AgentResult("done", "none", "")

    invoker = Invoker()
    monkeypatch.setattr(
        cli_module, "_build_active_invoker", lambda value: calls.append(value) or invoker
    )
    Supervisor(config, invoker).enqueue("backend", TaskItem("B-BUILDER", "work"))

    cli_module.run_cycle(config)

    assert calls == [config]


def test_configure_claude_oauth_token_persists_only_opaque_ref(tmp_path: Path) -> None:
    from rex.credential_vault import InMemoryCredentialVault
    from rex.credentials import CredentialManager
    from scripts.dev_orchestrator.cli import configure_claude_oauth_token, initialize_runtime

    root = tmp_path / "coordination"
    backend = tmp_path / "backend"
    mobile = tmp_path / "mobile"
    frozen = tmp_path / "frozen"
    for path in (root, backend, mobile, frozen):
        path.mkdir()
    initialize_runtime(root, backend, mobile, frozen)
    vault = InMemoryCredentialVault()
    manager = CredentialManager(
        credential_mapping={"claude_code_oauth": "CLAUDE_CODE_OAUTH_TOKEN"},
        vault=vault,
    )
    # Fixed synthetic vault fixture, never an account credential.
    secret = "synthetic-claude-oauth-value"  # pragma: allowlist secret

    config = configure_claude_oauth_token(root, secret, credential_manager=manager)

    assert config.claude_oauth_credential_ref
    text = (root / "orchestrator-config.json").read_text(encoding="utf-8")
    assert secret not in text
    assert (
        vault.get_secret(
            config.claude_oauth_credential_ref,
            integration="claude_code",
            account=None,
            slot="oauth_token",
        )
        == secret
    )


def test_configure_claude_oauth_cli_reads_secret_via_getpass(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    from rex.credential_vault import InMemoryCredentialVault
    from rex.credentials import CredentialManager
    from scripts.dev_orchestrator import cli as cli_module

    root = tmp_path / "coordination"
    backend = tmp_path / "backend"
    mobile = tmp_path / "mobile"
    frozen = tmp_path / "frozen"
    for path in (root, backend, mobile, frozen):
        path.mkdir()
    cli_module.initialize_runtime(root, backend, mobile, frozen)
    vault = InMemoryCredentialVault()
    manager = CredentialManager(
        credential_mapping={"claude_code_oauth": "CLAUDE_CODE_OAUTH_TOKEN"},
        vault=vault,
    )
    monkeypatch.setattr(cli_module, "CredentialManager", lambda *args, **kwargs: manager)
    monkeypatch.setattr("getpass.getpass", lambda _prompt: "interactive-secret-token")

    assert cli_module.main(["configure-claude-oauth", "--coordination-root", str(root)]) == 0
    output = capsys.readouterr().out
    assert "interactive-secret-token" not in output
    assert cli_module.load_config(root).claude_oauth_credential_ref


def test_active_invoker_wires_vault_claude_oauth_resolver(tmp_path: Path, monkeypatch) -> None:
    from dataclasses import replace

    from scripts.dev_orchestrator import cli as cli_module

    root = tmp_path / "coordination"
    backend = tmp_path / "backend"
    mobile = tmp_path / "mobile"
    frozen = tmp_path / "frozen"
    for path in (root, backend, mobile, frozen):
        path.mkdir()
    config = cli_module.initialize_runtime(root, backend, mobile, frozen)
    config = replace(config, claude_oauth_credential_ref="credref_abcdefghijklmnop")
    monkeypatch.setattr(cli_module, "_vault_claude_oauth_token", lambda _config: "vault-token")

    invoker = cli_module._build_active_invoker(config)

    assert invoker._claude_oauth_resolver is not None
    assert invoker._claude_oauth_resolver() == "vault-token"


def _scheduler_runtime(tmp_path: Path):
    root = tmp_path / "coordination"
    backend = tmp_path / "backend"
    mobile = tmp_path / "mobile"
    frozen = tmp_path / "rex-ai-pc-test"
    for path in (root, backend, mobile, frozen):
        path.mkdir()
    _git_repo(backend)
    _git_repo(mobile)
    config = initialize_runtime(root, backend, mobile, frozen)
    _handoff(config)
    return set_observe_only(root, False)


def test_run_loop_continues_mobile_while_backend_is_busy(tmp_path: Path) -> None:
    import threading

    from scripts.dev_orchestrator.cli import run_loop
    from scripts.dev_orchestrator.supervisor import Supervisor
    from scripts.dev_orchestrator.types import AgentResult, TaskItem

    config = _scheduler_runtime(tmp_path)
    backend_started = threading.Event()
    mobile_started = threading.Event()
    mobile_repeated = threading.Event()
    release_backend = threading.Event()
    stop = threading.Event()
    guard = threading.Lock()
    active = {"backend": 0, "mobile": 0}
    maximum_active = {"backend": 0, "mobile": 0}
    calls = {"backend": 0, "mobile": 0}
    failures = []

    class Invoker:
        def implement(self, role, *_args):
            with guard:
                active[role] += 1
                maximum_active[role] = max(maximum_active[role], active[role])
                calls[role] += 1
                role_calls = calls[role]
            try:
                if role == "backend":
                    backend_started.set()
                    if not release_backend.wait(10):
                        raise RuntimeError("test did not release backend")
                else:
                    mobile_started.set()
                    if role_calls == 2:
                        mobile_repeated.set()
                return AgentResult("continue", "bounded fake work", "continue")
            finally:
                with guard:
                    active[role] -= 1

        def lead(self, *_args, **_kwargs):
            raise AssertionError("test tasks are already queued")

    invoker = Invoker()
    supervisor = Supervisor(config, invoker)
    for role in ("backend", "mobile"):
        supervisor.enqueue(role, TaskItem(role + "-work", "bounded test task"))

    def run():
        try:
            run_loop(config, invoker=invoker, max_cycles=40, sleep_fn=lambda _: stop.wait(0.02))
        except BaseException as exc:
            failures.append(exc)

    controller = threading.Thread(target=run)
    controller.start()
    try:
        assert backend_started.wait(5)
        assert mobile_started.wait(5)
        assert mobile_repeated.wait(3), "mobile waited for the unrelated backend task"
        assert not release_backend.is_set()
        assert maximum_active == {"backend": 1, "mobile": 1}
        assert calls["backend"] == 1
    finally:
        set_observe_only(config.coordination_root, True)
        release_backend.set()
        stop.set()
        controller.join(10)
    assert not controller.is_alive()
    assert not failures


def test_run_loop_pause_prevents_new_role_dispatches(tmp_path: Path) -> None:
    import threading

    from scripts.dev_orchestrator.cli import run_loop
    from scripts.dev_orchestrator.supervisor import Supervisor
    from scripts.dev_orchestrator.types import AgentResult, TaskItem

    config = _scheduler_runtime(tmp_path)
    started = {role: threading.Event() for role in ("backend", "mobile")}
    release = threading.Event()
    calls = []
    sleeps = 0

    class Invoker:
        def implement(self, role, *_args):
            calls.append(role)
            started[role].set()
            if not release.wait(10):
                raise RuntimeError("test did not release workers")
            return AgentResult("continue", "bounded fake work", "continue")

        def lead(self, *_args, **_kwargs):
            raise AssertionError("test tasks are already queued")

    invoker = Invoker()
    supervisor = Supervisor(config, invoker)
    for role in ("backend", "mobile"):
        supervisor.enqueue(role, TaskItem(role + "-work", "bounded test task"))

    def pause_after_dispatch(_seconds):
        nonlocal sleeps
        sleeps += 1
        assert started["backend"].wait(5)
        assert started["mobile"].wait(5)
        set_observe_only(config.coordination_root, True)
        release.set()

    try:
        run_loop(config, invoker=invoker, max_cycles=4, sleep_fn=pause_after_dispatch)
    finally:
        release.set()
    assert sleeps == 3
    assert sorted(calls) == ["backend", "mobile"]


def test_run_loop_drains_workers_before_releasing_supervisor_lock(
    tmp_path: Path, monkeypatch
) -> None:
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from scripts.dev_orchestrator import cli as cli_module
    from scripts.dev_orchestrator.cli import run_loop
    from scripts.dev_orchestrator.supervisor import Supervisor
    from scripts.dev_orchestrator.types import AgentResult, TaskItem

    config = _scheduler_runtime(tmp_path)
    started = threading.Event()
    release = threading.Event()
    stopping = threading.Event()
    failures = []
    calls = []

    def one_worker(**kwargs):
        kwargs["max_workers"] = 1
        return ThreadPoolExecutor(**kwargs)

    monkeypatch.setattr(cli_module, "ThreadPoolExecutor", one_worker)

    class Invoker:
        def implement(self, role, *_args):
            calls.append(role)
            if role == "backend":
                started.set()
                if not release.wait(10):
                    raise RuntimeError("test did not release backend")
            return AgentResult("continue", "bounded fake work", "continue")

        def lead(self, *_args, **_kwargs):
            raise AssertionError("test tasks are already queued")

    invoker = Invoker()
    supervisor = Supervisor(config, invoker)
    for role in ("backend", "mobile"):
        supervisor.enqueue(role, TaskItem(role + "-work", "bounded test task"))

    def fail_poll(_seconds):
        assert started.wait(5)
        stopping.set()
        raise RuntimeError("injected scheduler failure")

    def run():
        try:
            run_loop(config, invoker=invoker, sleep_fn=fail_poll)
        except BaseException as exc:
            failures.append(exc)

    controller = threading.Thread(target=run)
    controller.start()
    try:
        assert started.wait(5)
        assert stopping.wait(3), "scheduler could not inspect state during active work"
        with pytest.raises(RuntimeError, match="already running"):
            with SupervisorLock(config.coordination_root / "supervisor.lock"):
                pass
        assert controller.is_alive()
    finally:
        release.set()
        controller.join(10)
    assert not controller.is_alive()
    assert len(failures) == 1
    assert str(failures[0]) == "injected scheduler failure"
    assert calls == ["backend"], "queued mobile work started after scheduler failure"
    with SupervisorLock(config.coordination_root / "supervisor.lock"):
        pass


def test_run_loop_propagates_role_start_failure_on_final_poll(tmp_path: Path, monkeypatch) -> None:
    from scripts.dev_orchestrator import cli as cli_module

    config = _scheduler_runtime(tmp_path)

    def unavailable_role(*_args, **_kwargs):
        raise RuntimeError("role launch could not validate its lease")

    monkeypatch.setattr(cli_module, "_run_role_cycle", unavailable_role)
    with pytest.raises(RuntimeError, match="could not validate its lease"):
        cli_module.run_loop(config, max_cycles=1)


def test_role_failure_preserves_unconsumed_durable_result(tmp_path: Path, monkeypatch) -> None:
    from scripts.dev_orchestrator.storage import AtomicJsonStore
    from scripts.dev_orchestrator.supervisor import Supervisor

    config = _scheduler_runtime(tmp_path)
    supervisor = Supervisor(config, object())
    store = AtomicJsonStore(config.coordination_root / "handoff" / "backend.json")
    record = store.read()
    pending = {"invocation_id": "pending-before-failure", "phase": "implement"}
    record["pending_result"] = pending
    store.write(record)

    def fail_after_receiving_result(role):
        supervisor._result_context.role = role
        supervisor._result_context.invocation_id = pending["invocation_id"]
        raise RuntimeError("result application failed")

    monkeypatch.setattr(supervisor, "_run_role", fail_after_receiving_result)
    supervisor.run_role("backend")

    assert store.read().get("pending_result") == pending
    state = supervisor.load_state("backend")
    assert state.status.value == "blocked_system"
    assert state.last_result_invocation_id == ""
