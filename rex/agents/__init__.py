"""Versioned, owner-scoped agent configuration contracts.

This package intentionally defines configuration and lifecycle only.  Agent
execution remains out of scope until it can enter the canonical TurnEngine.
"""

from .definition import AgentDefinition, AgentDefinitionValidationError
from .lifecycle import AgentLifecycle, LifecycleTransitionError, transition
from .store import AgentDefinitionStore, AgentStoreCorruptRecordError

__all__ = [
    "AgentDefinition",
    "AgentDefinitionStore",
    "AgentDefinitionValidationError",
    "AgentLifecycle",
    "AgentStoreCorruptRecordError",
    "LifecycleTransitionError",
    "transition",
]
