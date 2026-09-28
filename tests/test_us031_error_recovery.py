"""Tests for US-031: Error recovery with alternative suggestions.

Covers:
- Device-offline message when no alternatives exist
- Same-room alternative suggestion (single)
- Same-room alternative suggestion (multiple)
- Recently-used alternative suggestion
- process_transcript returns recovery message on HA failure
- process_transcript does NOT return recovery message on success
"""

from __future__ import annotations

from unittest.mock import MagicMock

from rex.ha.error_recovery import (
    _extract_room,
    _same_room_alternatives,
    suggest_alternatives,
)

# ---------------------------------------------------------------------------
# Unit tests for _extract_room
# ---------------------------------------------------------------------------


def test_extract_room_standard():
    assert _extract_room("light.kitchen_ceiling") == "kitchen"


def test_extract_room_no_underscore():
    assert _extract_room("light.bedroom") == "bedroom"


def test_extract_room_no_dot():
    assert _extract_room("kitchen") is None


# ---------------------------------------------------------------------------
# Unit tests for _same_room_alternatives
# ---------------------------------------------------------------------------


def test_same_room_alternatives_finds_match():
    entity_map = {
        "kitchen light": "light.kitchen_main",
        "kitchen lamp": "light.kitchen_corner",
    }
    result = _same_room_alternatives("light.kitchen_main", "light", entity_map, {})
    eids = [eid for _, eid in result]
    assert "light.kitchen_corner" in eids
    assert "light.kitchen_main" not in eids


def test_same_room_alternatives_ignores_other_domain():
    entity_map = {
        "kitchen fan": "fan.kitchen_fan",
        "kitchen lamp": "light.kitchen_corner",
    }
    result = _same_room_alternatives("light.kitchen_main", "light", entity_map, {})
    eids = [eid for _, eid in result]
    assert "fan.kitchen_fan" not in eids
    assert "light.kitchen_corner" in eids


def test_same_room_alternatives_ignores_other_room():
    entity_map = {
        "bedroom light": "light.bedroom_main",
    }
    result = _same_room_alternatives("light.kitchen_main", "light", entity_map, {})
    assert result == []


def test_same_room_alternatives_caps_at_two():
    entity_map = {
        "kitchen lamp a": "light.kitchen_a",
        "kitchen lamp b": "light.kitchen_b",
        "kitchen lamp c": "light.kitchen_c",
    }
    result = _same_room_alternatives("light.kitchen_main", "light", entity_map, {})
    assert len(result) <= 2


# ---------------------------------------------------------------------------
# Unit tests for suggest_alternatives
# ---------------------------------------------------------------------------


def test_no_alternatives_returns_offline_message():
    msg = suggest_alternatives("light.kitchen_main", "light", entity_map={}, entity_cache={})
    assert "not responding" in msg
    assert "may be offline" in msg


def test_single_alternative_suggests_it():
    entity_map = {"kitchen lamp": "light.kitchen_corner"}
    msg = suggest_alternatives(
        "light.kitchen_main", "light", entity_map=entity_map, entity_cache={}
    )
    assert "kitchen lamp" in msg
    assert "instead" in msg


def test_two_alternatives_listed_with_or():
    entity_map = {
        "kitchen lamp": "light.kitchen_corner",
        "kitchen spotlight": "light.kitchen_spot",
    }
    msg = suggest_alternatives(
        "light.kitchen_main", "light", entity_map=entity_map, entity_cache={}
    )
    assert " or " in msg


def test_recent_entity_ids_used_as_fallback():
    # No same-room alternatives, but a recently used device of same domain
    msg = suggest_alternatives(
        "light.kitchen_main",
        "light",
        entity_map={"living room light": "light.living_room"},
        entity_cache={"living room light": "light.living_room"},
        recent_entity_ids=["light.living_room"],
    )
    # living room light is different room but same domain → recent fallback
    assert "living room light" in msg or "living room" in msg


def test_failed_entity_excluded_from_suggestions():
    entity_map = {"kitchen light": "light.kitchen_main"}
    msg = suggest_alternatives(
        "light.kitchen_main",
        "light",
        entity_map=entity_map,
        entity_cache={},
        recent_entity_ids=["light.kitchen_main"],
    )
    # The failing device should not be suggested as its own alternative
    assert "may be offline" in msg or "instead" in msg
    # It should NOT suggest the same device as alternative
    assert msg.count("kitchen main") <= 1  # only in base_msg


# ---------------------------------------------------------------------------
# Integration: HABridge.process_transcript returns recovery message on failure
# ---------------------------------------------------------------------------


def _make_bridge_with_mocks(
    *,
    execute_success: bool,
    entity_map: dict | None = None,
    entity_cache: dict | None = None,
) -> HABridge:  # type: ignore[name-defined]  # noqa: F821
    """Return an HABridge stub with controlled _execute_intent outcome."""
    from rex.ha_bridge import HABridge, IntentMatch

    bridge = HABridge.__new__(HABridge)
    bridge._base_url = "http://ha.local"
    bridge._token = "tok"
    bridge._entity_map = entity_map or {}
    bridge._entity_cache = entity_cache or {}
    bridge._clarification = MagicMock()
    bridge._clarification.check.return_value = None
    from rex.ha.command_history import CommandHistory

    bridge._command_history = CommandHistory()

    fake_match = IntentMatch(
        domain="light",
        service="turn_on",
        entity_id="light.kitchen_main",
        data={"entity_id": "light.kitchen_main"},
        description="turn on kitchen main",
        source="transcript",
    )
    bridge._match_transcript = MagicMock(return_value=fake_match)
    bridge._log_event = MagicMock()

    from rex.ha.mutation_service import HAMutationResult, HAOutcome, HARisk

    if execute_success:
        mutation_result = HAMutationResult(
            status=HAOutcome.VERIFIED,
            detail="Verified light.kitchen_main state.",
            entity_id="light.kitchen_main",
            domain="light",
            service="turn_on",
            request_id="test-request",
            risk=HARisk.SAFE,
            expected={"state": "on", "attributes": {}},
        )
    else:
        mutation_result = HAMutationResult(
            status=HAOutcome.FAILED,
            detail="Connection refused",
            entity_id="light.kitchen_main",
            domain="light",
            service="turn_on",
            request_id="test-request",
            risk=HARisk.SAFE,
        )
    bridge._mutation_service = MagicMock()
    bridge._mutation_service.execute.return_value = mutation_result
    bridge._user_id = "default"
    return bridge


def test_process_transcript_success_returns_verified_message():
    bridge = _make_bridge_with_mocks(execute_success=True)
    result = bridge.process_transcript("turn on the kitchen light")
    assert result == "Confirmed light kitchen main is on."


def test_process_transcript_failure_returns_recovery_message():
    bridge = _make_bridge_with_mocks(
        execute_success=False,
        entity_map={"kitchen lamp": "light.kitchen_corner"},
    )
    result = bridge.process_transcript("turn on the kitchen light")
    assert result is not None
    assert "not responding" in result or "offline" in result


def test_process_transcript_failure_no_alternatives_says_offline():
    bridge = _make_bridge_with_mocks(execute_success=False, entity_map={})
    result = bridge.process_transcript("turn on the kitchen light")
    assert result is not None
    assert "may be offline" in result
