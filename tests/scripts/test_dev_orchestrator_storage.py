from __future__ import annotations

from pathlib import Path

import pytest

from scripts.dev_orchestrator.schema import validate_agent_result
from scripts.dev_orchestrator.storage import AtomicJsonStore
from scripts.dev_orchestrator.types import (
    AgentResult,
    OrchestratorConfig,
    TaskItem,
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
    with pytest.raises(ValueError, match="outcome"):
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
