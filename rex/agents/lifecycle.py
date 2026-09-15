"""Fail-closed AgentDefinition lifecycle transitions."""

from __future__ import annotations

from enum import StrEnum


class AgentLifecycle(StrEnum):
    """Persistent lifecycle states for an agent configuration."""

    DRAFT = "draft"
    VALIDATED = "validated"
    APPROVED = "approved"
    ACTIVE = "active"
    PAUSED = "paused"
    REVOKED = "revoked"
    ARCHIVED = "archived"


class LifecycleTransitionError(ValueError):
    """Raised when an AgentDefinition lifecycle transition is not permitted."""


def transition(
    current: AgentLifecycle | str,
    target: AgentLifecycle | str,
    *,
    new_approval: bool = False,
) -> AgentLifecycle:
    """Validate and return a lifecycle transition without running an agent.

    Revocation is terminal for authority.  A revoked definition must receive a
    new approval before it can return to the ordinary approved-to-active path.
    Archiving is historical and never grants authority.
    """
    source = AgentLifecycle(current)
    destination = AgentLifecycle(target)
    if source is destination:
        raise LifecycleTransitionError("lifecycle transition must change state")
    if destination is AgentLifecycle.ARCHIVED and source is not AgentLifecycle.ARCHIVED:
        return destination
    if source is AgentLifecycle.REVOKED and destination is AgentLifecycle.APPROVED:
        if new_approval:
            return destination
        raise LifecycleTransitionError("reactivation after revocation requires a new approval")
    allowed: dict[AgentLifecycle, frozenset[AgentLifecycle]] = {
        AgentLifecycle.DRAFT: frozenset({AgentLifecycle.VALIDATED}),
        AgentLifecycle.VALIDATED: frozenset({AgentLifecycle.APPROVED}),
        AgentLifecycle.APPROVED: frozenset({AgentLifecycle.ACTIVE, AgentLifecycle.REVOKED}),
        AgentLifecycle.ACTIVE: frozenset({AgentLifecycle.PAUSED, AgentLifecycle.REVOKED}),
        AgentLifecycle.PAUSED: frozenset({AgentLifecycle.ACTIVE, AgentLifecycle.REVOKED}),
        AgentLifecycle.REVOKED: frozenset(),
        AgentLifecycle.ARCHIVED: frozenset(),
    }
    if destination not in allowed[source]:
        raise LifecycleTransitionError(
            f"invalid agent lifecycle transition: {source} -> {destination}"
        )
    return destination
