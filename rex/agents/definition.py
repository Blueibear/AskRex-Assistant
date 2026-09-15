"""Canonical, secret-free AgentDefinition persistence contract."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, ClassVar

from rex.identity import validate_user_id

from .lifecycle import AgentLifecycle

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SENSITIVE_FIELD_PARTS = ("secret", "token", "password", "credential", "api_key", "apikey")


class AgentDefinitionValidationError(ValueError):
    """Raised for invalid or unsafe persistent agent configuration."""


def _require_identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value) or value in {".", ".."}:
        raise AgentDefinitionValidationError(f"invalid {label}")
    return value


def _require_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AgentDefinitionValidationError(f"{label} must be non-empty text")
    return value


def _json_value(value: Any, path: str = "") -> Any:
    """Return JSON-compatible data after rejecting raw secret-bearing keys."""
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise AgentDefinitionValidationError("policy keys must be text")
            key_lower = key.casefold()
            if any(part in key_lower for part in _SENSITIVE_FIELD_PARTS):
                raise AgentDefinitionValidationError("agent definitions must not contain raw secret fields")
            normalized[key] = _json_value(item, f"{path}.{key}" if path else key)
        return normalized
    if isinstance(value, (list, tuple)):
        return [_json_value(item, path) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise AgentDefinitionValidationError("agent policy values must be JSON-compatible")


def _timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise AgentDefinitionValidationError(f"{label} must be a timezone-aware datetime")
    return value.astimezone(UTC)


def _timestamp_text(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise AgentDefinitionValidationError(f"{label} must be an ISO timestamp")
    try:
        return _timestamp(datetime.fromisoformat(value.replace("Z", "+00:00")), label)
    except ValueError as exc:
        raise AgentDefinitionValidationError(f"{label} must be an ISO timestamp") from exc


@dataclass(frozen=True)
class AgentDefinition:
    """A versioned policy overlay that has no independent execution authority."""

    schema_version: int = 1
    agent_id: str = ""
    name: str = ""
    description: str = ""
    purpose: str = ""
    owner_user_id: str = ""
    workspace_scope: str = ""
    instructions: str = ""
    model_policy: dict[str, Any] = field(default_factory=dict)
    skill_refs: tuple[str, ...] = ()
    capability_bindings: tuple[dict[str, Any], ...] = ()
    memory_policy: dict[str, Any] = field(default_factory=dict)
    trigger_definitions: tuple[dict[str, Any], ...] = ()
    resource_policy: dict[str, Any] = field(default_factory=dict)
    approval_policy: dict[str, Any] = field(default_factory=dict)
    escalation_policy: dict[str, Any] = field(default_factory=dict)
    verification_policy: dict[str, Any] = field(default_factory=dict)
    delegation_policy: dict[str, Any] = field(default_factory=dict)
    retry_policy: dict[str, Any] = field(default_factory=dict)
    notification_policy: dict[str, Any] = field(default_factory=dict)
    audit_policy: dict[str, Any] = field(default_factory=dict)
    lifecycle_status: AgentLifecycle = AgentLifecycle.DRAFT
    version: int = 1
    provenance: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    CURRENT_SCHEMA_VERSION: ClassVar[int] = 1
    _PERSISTED_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "schema_version", "agent_id", "name", "description", "purpose", "owner_user_id",
            "workspace_scope", "instructions", "model_policy", "skill_refs", "capability_bindings",
            "memory_policy", "trigger_definitions", "resource_policy", "approval_policy",
            "escalation_policy", "verification_policy", "delegation_policy", "retry_policy",
            "notification_policy", "audit_policy", "lifecycle_status", "version", "provenance",
            "created_at", "updated_at",
        }
    )

    def __post_init__(self) -> None:
        if self.schema_version != self.CURRENT_SCHEMA_VERSION:
            raise AgentDefinitionValidationError("unsupported agent definition schema version")
        object.__setattr__(self, "agent_id", _require_identifier(self.agent_id, "agent_id"))
        object.__setattr__(self, "name", _require_text(self.name, "name"))
        object.__setattr__(self, "description", _require_text(self.description, "description"))
        object.__setattr__(self, "purpose", _require_text(self.purpose, "purpose"))
        try:
            owner = validate_user_id(self.owner_user_id)
        except ValueError as exc:
            raise AgentDefinitionValidationError("invalid owner_user_id") from exc
        object.__setattr__(self, "owner_user_id", owner)
        object.__setattr__(
            self,
            "workspace_scope",
            _require_identifier(self.workspace_scope, "workspace_scope"),
        )
        object.__setattr__(self, "instructions", _require_text(self.instructions, "instructions"))
        if not isinstance(self.version, int) or isinstance(self.version, bool) or self.version < 1:
            raise AgentDefinitionValidationError("version must be a positive integer")
        object.__setattr__(self, "lifecycle_status", AgentLifecycle(self.lifecycle_status))
        object.__setattr__(self, "created_at", _timestamp(self.created_at, "created_at"))
        object.__setattr__(self, "updated_at", _timestamp(self.updated_at, "updated_at"))
        for field_name in (
            "model_policy",
            "memory_policy",
            "resource_policy",
            "approval_policy",
            "escalation_policy",
            "verification_policy",
            "delegation_policy",
            "retry_policy",
            "notification_policy",
            "audit_policy",
            "provenance",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, dict):
                raise AgentDefinitionValidationError(f"{field_name} must be an object")
            object.__setattr__(self, field_name, _json_value(value))
        for field_name in ("skill_refs", "capability_bindings", "trigger_definitions"):
            value = getattr(self, field_name)
            if not isinstance(value, (tuple, list)):
                raise AgentDefinitionValidationError(f"{field_name} must be a list")
            normalized = tuple(_json_value(item) for item in value)
            if field_name == "skill_refs" and not all(
                isinstance(item, str) and item for item in normalized
            ):
                raise AgentDefinitionValidationError("skill_refs must contain non-empty text references")
            if field_name != "skill_refs" and not all(isinstance(item, dict) for item in normalized):
                raise AgentDefinitionValidationError(f"{field_name} must contain objects")
            object.__setattr__(self, field_name, normalized)

    def to_dict(self) -> dict[str, Any]:
        """Return the complete deterministic, JSON-safe persistent representation."""
        return {
            "schema_version": self.schema_version,
            "agent_id": self.agent_id,
            "name": self.name,
            "description": self.description,
            "purpose": self.purpose,
            "owner_user_id": self.owner_user_id,
            "workspace_scope": self.workspace_scope,
            "instructions": self.instructions,
            "model_policy": self.model_policy,
            "skill_refs": list(self.skill_refs),
            "capability_bindings": list(self.capability_bindings),
            "memory_policy": self.memory_policy,
            "trigger_definitions": list(self.trigger_definitions),
            "resource_policy": self.resource_policy,
            "approval_policy": self.approval_policy,
            "escalation_policy": self.escalation_policy,
            "verification_policy": self.verification_policy,
            "delegation_policy": self.delegation_policy,
            "retry_policy": self.retry_policy,
            "notification_policy": self.notification_policy,
            "audit_policy": self.audit_policy,
            "lifecycle_status": self.lifecycle_status.value,
            "version": self.version,
            "provenance": self.provenance,
            "created_at": _timestamp_text(self.created_at),
            "updated_at": _timestamp_text(self.updated_at),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    @classmethod
    def from_dict(cls, payload: object) -> "AgentDefinition":
        if not isinstance(payload, dict):
            raise AgentDefinitionValidationError("agent definition must be an object")
        if set(payload) != cls._PERSISTED_FIELDS:
            raise AgentDefinitionValidationError("agent definition has unsupported or missing fields")
        values = dict(payload)
        values["created_at"] = _parse_timestamp(values["created_at"], "created_at")
        values["updated_at"] = _parse_timestamp(values["updated_at"], "updated_at")
        return cls(**values)
