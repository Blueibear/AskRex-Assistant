from __future__ import annotations

from datetime import UTC, datetime

import pytest

from rex.agents.definition import AgentDefinition, AgentDefinitionValidationError


def _definition(**overrides: object) -> AgentDefinition:
    values: dict[str, object] = {
        "agent_id": "agent-123",
        "name": "research-helper",
        "description": "Summarizes approved project material.",
        "purpose": "Research assistance",
        "owner_user_id": "james",
        "workspace_scope": "project-alpha",
        "instructions": "Use canonical Rex policy.",
        "provenance": {"source": "user"},
        "created_at": datetime(2026, 9, 14, tzinfo=UTC),
        "updated_at": datetime(2026, 9, 14, tzinfo=UTC),
    }
    values.update(overrides)
    return AgentDefinition(**values)


def test_complete_schema_has_deterministic_versioned_round_trip() -> None:
    definition = _definition(
        model_policy={"providers": ["local"], "max_tokens": 500},
        skill_refs=("research.v1",),
        capability_bindings=({"capability_id": "search.read", "effect": "allow"},),
        memory_policy={"read_namespaces": ["project"]},
        trigger_definitions=({"type": "manual"},),
        resource_policy={"max_tool_calls": 3},
        approval_policy={"required": True},
        escalation_policy={"on": ["insufficient_authority"]},
        verification_policy={"required": True},
        delegation_policy={"max_depth": 0},
        retry_policy={"max_attempts": 1},
        notification_policy={"channels": []},
        audit_policy={"extra": False},
    )

    payload = definition.to_dict()
    restored = AgentDefinition.from_dict(payload)

    assert payload["schema_version"] == 1
    assert payload["version"] == 1
    assert restored == definition
    assert restored.to_json() == definition.to_json()


@pytest.mark.parametrize("owner, workspace", [("../other", "project"), ("james", "../project"), ("james", "")])
def test_owner_and_workspace_are_validated(owner: str, workspace: str) -> None:
    with pytest.raises((AgentDefinitionValidationError, ValueError)):
        _definition(owner_user_id=owner, workspace_scope=workspace)


@pytest.mark.parametrize(
    "field",
    ["secret", "token", "password", "api_key", "credential", "access_token"],
)
def test_raw_credential_fields_are_rejected(field: str) -> None:
    payload = _definition().to_dict()
    payload["model_policy"] = {field: "not-a-secret"}

    with pytest.raises(AgentDefinitionValidationError, match="secret"):
        AgentDefinition.from_dict(payload)


def test_same_name_agents_have_distinct_stable_ids() -> None:
    first = _definition(agent_id="agent-one", name="same-name")
    second = _definition(agent_id="agent-two", name="same-name")

    assert first.name == second.name
    assert first.agent_id != second.agent_id
