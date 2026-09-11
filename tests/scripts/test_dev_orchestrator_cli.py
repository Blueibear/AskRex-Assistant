import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scripts.dev_orchestrator.cli import (
    heartbeat_is_stale,
    initialize_runtime,
    load_config,
    record_confirmed_resets,
    render_status,
    set_observe_only,
)
from scripts.dev_orchestrator.lifecycle import SupervisorLock, write_heartbeat


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
    write_heartbeat(path, now=now - timedelta(seconds=30), pid=123)
    assert heartbeat_is_stale(path, now=now, max_age_seconds=60) is False
    assert heartbeat_is_stale(path, now=now, max_age_seconds=20) is True
    assert heartbeat_is_stale(tmp_path / "missing.json", now=now, max_age_seconds=60) is True


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
    assert "claude" not in lowered
    assert "gpt-" not in lowered


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
    config = replace(initialize_runtime(root, backend, mobile, frozen), observe_only=False)

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
    initialize_runtime(root, backend, mobile, frozen)

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
        first = json.loads(path.read_text(encoding="utf-8"))["timestamp"]
        wall_time.sleep(0.04)
        second = json.loads(path.read_text(encoding="utf-8"))["timestamp"]
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
