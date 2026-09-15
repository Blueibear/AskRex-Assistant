from __future__ import annotations

import pytest

from rex.agents.lifecycle import AgentLifecycle, LifecycleTransitionError, transition


def test_canonical_lifecycle_transitions_are_allowed() -> None:
    state = AgentLifecycle.DRAFT
    for next_state in (AgentLifecycle.VALIDATED, AgentLifecycle.APPROVED, AgentLifecycle.ACTIVE):
        state = transition(state, next_state)
    assert state is AgentLifecycle.ACTIVE


@pytest.mark.parametrize(
    ("current", "target"),
    [(AgentLifecycle.DRAFT, AgentLifecycle.ACTIVE), (AgentLifecycle.VALIDATED, AgentLifecycle.PAUSED)],
)
def test_lifecycle_invalid_skips_fail_closed(current: AgentLifecycle, target: AgentLifecycle) -> None:
    with pytest.raises(LifecycleTransitionError):
        transition(current, target)


def test_pause_revoke_and_archive_semantics() -> None:
    assert transition(AgentLifecycle.ACTIVE, AgentLifecycle.PAUSED) is AgentLifecycle.PAUSED
    assert transition(AgentLifecycle.PAUSED, AgentLifecycle.REVOKED) is AgentLifecycle.REVOKED
    assert transition(AgentLifecycle.REVOKED, AgentLifecycle.ARCHIVED) is AgentLifecycle.ARCHIVED


def test_reactivation_after_revocation_requires_a_new_approval() -> None:
    with pytest.raises(LifecycleTransitionError, match="new approval"):
        transition(AgentLifecycle.REVOKED, AgentLifecycle.ACTIVE)

    assert transition(AgentLifecycle.REVOKED, AgentLifecycle.APPROVED, new_approval=True) is AgentLifecycle.APPROVED
    assert transition(AgentLifecycle.APPROVED, AgentLifecycle.ACTIVE) is AgentLifecycle.ACTIVE
