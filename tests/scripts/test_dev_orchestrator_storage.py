from __future__ import annotations

from pathlib import Path

import pytest

from scripts.dev_orchestrator.schema import validate_agent_result
from scripts.dev_orchestrator.storage import AtomicJsonStore
from scripts.dev_orchestrator.types import (
    AgentResult,
    OrchestratorConfig,
    WorkerState,
    WorkerStatus,
)


def test_config_defaults_to_three_banked_resets_and_observe_only(tmp_path: Path) -> None:
    config = OrchestratorConfig.default(tmp_path)

    assert config.banked_resets_remaining == 3
    assert config.reserve_last_reset is True
    assert config.observe_only is True


def test_atomic_store_round_trips_worker_state(tmp_path: Path) -> None:
    store = AtomicJsonStore(tmp_path / "state.json")
    state = WorkerState(role="backend", status=WorkerStatus.IMPLEMENTING)

    store.write(state.to_dict())

    assert store.read() == state.to_dict()
    assert not (tmp_path / "state.json.tmp").exists()


def test_config_rejects_frozen_worktree_as_worker_root(tmp_path: Path) -> None:
    frozen = tmp_path / "rex-ai-pc-test"
    config = OrchestratorConfig.default(tmp_path)

    with pytest.raises(ValueError, match="frozen"):
        config.with_worker_root("backend", frozen, frozen_worktree=frozen)


def test_agent_result_validation_rejects_unknown_outcome() -> None:
    with pytest.raises(ValueError, match="schema"):
        validate_agent_result({"outcome": "magic", "summary": "nope", "next_action": ""})


def test_agent_result_validation_accepts_structured_result() -> None:
    result = validate_agent_result(
        {
            "outcome": "continue",
            "summary": "Tests are green; one refactor remains.",
            "next_action": "Finish the refactor.",
            "needs_user": False,
            "blocker_reason": "",
        }
    )

    assert isinstance(result, AgentResult)
    assert result.outcome == "continue"
    assert result.needs_user is False


def test_atomic_store_retries_windows_replace_sharing_violation(
    tmp_path: Path, monkeypatch
) -> None:
    import os

    from scripts.dev_orchestrator import storage as storage_module

    store = AtomicJsonStore(tmp_path / "state.json")
    real_replace = os.replace
    calls = 0

    def flaky_replace(source, destination):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise PermissionError(5, "sharing violation")
        return real_replace(source, destination)

    monkeypatch.setattr(storage_module.os, "replace", flaky_replace)
    store.write({"status": "working"})

    assert calls == 2
    assert store.read() == {"status": "working"}


def test_agent_result_rejects_needs_user_with_success_outcome() -> None:
    with pytest.raises(ValueError, match="schema"):
        validate_agent_result(
            {
                "outcome": "pass",
                "summary": "looks good",
                "next_action": "",
                "needs_user": True,
                "blocker_reason": "needs hardware confirmation",
            }
        )


def test_blocked_user_requires_needs_user_and_reason() -> None:
    with pytest.raises(ValueError, match="schema"):
        validate_agent_result(
            {
                "outcome": "blocked_user",
                "summary": "blocked",
                "next_action": "confirm",
                "needs_user": False,
                "blocker_reason": "",
            }
        )


def test_control_plane_lock_serializes_concurrent_mutation(tmp_path: Path) -> None:
    import threading
    import time

    from scripts.dev_orchestrator.lifecycle import ControlPlaneLock

    path = tmp_path / "control-plane.lock"
    entered = threading.Event()
    finished = threading.Event()

    def contender() -> None:
        entered.set()
        with ControlPlaneLock(path):
            finished.set()

    with ControlPlaneLock(path):
        thread = threading.Thread(target=contender)
        thread.start()
        assert entered.wait(timeout=1)
        time.sleep(0.05)
        assert finished.is_set() is False
    thread.join(timeout=2)
    assert finished.is_set() is True
    assert path.exists()
    with ControlPlaneLock(path):
        assert path.exists()
    assert path.exists()


def test_agent_result_requires_declared_json_schema_fields() -> None:
    with pytest.raises(ValueError, match="schema"):
        validate_agent_result({"outcome": "continue", "summary": "x", "next_action": "x"})


def test_agent_result_rejects_additional_properties() -> None:
    with pytest.raises(ValueError, match="schema"):
        validate_agent_result(
            {
                "outcome": "continue",
                "summary": "x",
                "next_action": "x",
                "needs_user": False,
                "blocker_reason": "",
                "unexpected": "must be rejected",
            }
        )


def test_agent_result_rejects_incomplete_nested_coordination_message() -> None:
    with pytest.raises(ValueError, match="schema"):
        validate_agent_result(
            {
                "outcome": "pass",
                "summary": "x",
                "next_action": "x",
                "needs_user": False,
                "blocker_reason": "",
                "coordination_messages": [
                    {"to": "backend", "priority": "high", "related": "B-1", "needs_response": False}
                ],
            }
        )
