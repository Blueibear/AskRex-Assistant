from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from rex.agents.definition import AgentDefinition
from rex.agents.store import AgentDefinitionStore, AgentStoreCorruptRecordError


def _definition(agent_id: str, owner: str, name: str = "same-name") -> AgentDefinition:
    now = datetime(2026, 9, 14, tzinfo=UTC)
    return AgentDefinition(
        agent_id=agent_id,
        name=name,
        description="Description",
        purpose="Purpose",
        owner_user_id=owner,
        workspace_scope="project-alpha",
        instructions="Follow Rex policy.",
        provenance={"source": "test"},
        created_at=now,
        updated_at=now,
    )


def test_records_live_beneath_the_owner_private_data_root(tmp_path: Path) -> None:
    store = AgentDefinitionStore(users_data_dir=tmp_path / "users")
    definition = _definition("agent-one", "james")

    saved = store.save(definition, actor_user_id="james")

    assert saved == definition
    assert store.path_for("james", "agent-one") == tmp_path / "users" / "james" / "agents" / "agent-one.json"
    assert store.path_for("james", "agent-one").is_file()


def test_store_is_owner_scoped_and_same_name_agents_do_not_collide(tmp_path: Path) -> None:
    store = AgentDefinitionStore(users_data_dir=tmp_path / "users")
    first = _definition("agent-one", "james")
    second = _definition("agent-two", "james")
    other = _definition("agent-one", "alex")
    for definition in (first, second, other):
        store.save(definition, actor_user_id=definition.owner_user_id)

    assert {item.agent_id for item in store.list("james", actor_user_id="james")} == {
        "agent-one",
        "agent-two",
    }
    assert store.get("alex", "agent-one", actor_user_id="alex") == other
    with pytest.raises(PermissionError):
        store.get("james", "agent-one", actor_user_id="alex")


def test_malformed_persisted_record_fails_closed(tmp_path: Path) -> None:
    store = AgentDefinitionStore(users_data_dir=tmp_path / "users")
    path = store.path_for("james", "agent-one")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"schema_version": 1, "owner_user_id": "alex"}), encoding="utf-8")

    with pytest.raises(AgentStoreCorruptRecordError):
        store.get("james", "agent-one", actor_user_id="james")
