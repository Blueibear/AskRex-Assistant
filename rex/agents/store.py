"""Owner-scoped, atomic persistence for AgentDefinition records."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from rex.identity import validate_user_id
from rex.runtime_paths import users_data_dir as canonical_users_data_dir

from .definition import AgentDefinition, AgentDefinitionValidationError, _require_identifier


class AgentStoreCorruptRecordError(ValueError):
    """Raised when a persisted AgentDefinition cannot be trusted."""


class AgentDefinitionStore:
    """Persist independent agent records under each owner's private data root."""

    def __init__(self, *, users_data_dir: Path | None = None) -> None:
        self._users_data_dir = (
            Path(users_data_dir) if users_data_dir is not None else canonical_users_data_dir()
        )

    @staticmethod
    def _require_owner(owner_user_id: str, actor_user_id: str) -> str:
        owner = validate_user_id(owner_user_id)
        actor = validate_user_id(actor_user_id)
        if owner != actor:
            raise PermissionError("owner authorization required")
        return owner

    def path_for(self, owner_user_id: str, agent_id: str) -> Path:
        owner = validate_user_id(owner_user_id)
        identifier = _require_identifier(agent_id, "agent_id")
        return self._users_data_dir / owner / "agents" / f"{identifier}.json"

    @staticmethod
    def _atomic_write(path: Path, payload: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False
            ) as handle:
                temporary = Path(handle.name)
                json.dump(payload, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def save(self, definition: AgentDefinition, *, actor_user_id: str) -> AgentDefinition:
        if not isinstance(definition, AgentDefinition):
            raise TypeError("definition must be an AgentDefinition")
        owner = self._require_owner(definition.owner_user_id, actor_user_id)
        self._atomic_write(self.path_for(owner, definition.agent_id), definition.to_dict())
        return definition

    def get(
        self, owner_user_id: str, agent_id: str, *, actor_user_id: str
    ) -> AgentDefinition | None:
        owner = self._require_owner(owner_user_id, actor_user_id)
        path = self.path_for(owner, agent_id)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            definition = AgentDefinition.from_dict(payload)
        except (OSError, json.JSONDecodeError, AgentDefinitionValidationError, TypeError) as exc:
            raise AgentStoreCorruptRecordError("agent definition record is unreadable") from exc
        if definition.owner_user_id != owner or definition.agent_id != agent_id:
            raise AgentStoreCorruptRecordError("agent definition record has invalid ownership")
        return definition

    def list(self, owner_user_id: str, *, actor_user_id: str) -> list[AgentDefinition]:
        owner = self._require_owner(owner_user_id, actor_user_id)
        directory = self._users_data_dir / owner / "agents"
        if not directory.exists():
            return []
        definitions: list[AgentDefinition] = []
        for path in sorted(directory.glob("*.json")):
            try:
                definition = self.get(owner, path.stem, actor_user_id=owner)
            except AgentDefinitionValidationError as exc:
                raise AgentStoreCorruptRecordError("agent definition record has an invalid path") from exc
            if definition is not None:
                definitions.append(definition)
        return definitions
