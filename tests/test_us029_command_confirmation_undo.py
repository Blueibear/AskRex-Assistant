"""Tests for US-029: HA command confirmation and undo support."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from rex.ha.command_history import _INVERSE_SERVICES, CommandEntry, CommandHistory

# ---------------------------------------------------------------------------
# CommandHistory unit tests
# ---------------------------------------------------------------------------


class TestCommandHistory:
    def test_push_and_len(self):
        ch = CommandHistory()
        ch.push(
            entity_id="light.kitchen",
            domain="light",
            service="turn_on",
            data={"entity_id": "light.kitchen"},
            description="turn on kitchen",
        )
        assert len(ch) == 1

    def test_max_size_fifo(self):
        ch = CommandHistory(max_size=3)
        for i in range(5):
            ch.push(
                entity_id=f"light.room{i}",
                domain="light",
                service="turn_on",
                data={},
                description=f"turn on room{i}",
            )
        assert len(ch) == 3

    def test_peek_returns_last_within_window(self):
        ch = CommandHistory(undo_window=30.0)
        ch.push(
            entity_id="light.bedroom",
            domain="light",
            service="turn_on",
            data={"entity_id": "light.bedroom"},
            description="turn on bedroom",
        )
        candidate = ch.peek_undo_candidate()
        assert candidate is not None
        assert candidate.entity_id == "light.bedroom"
        assert candidate.inverse_service == "turn_off"

    def test_peek_returns_none_outside_window(self):
        ch = CommandHistory(undo_window=0.0)
        ch.push(
            entity_id="light.bedroom",
            domain="light",
            service="turn_on",
            data={},
            description="turn on bedroom",
        )
        # window is 0 — any elapsed time exceeds it
        assert ch.peek_undo_candidate() is None

    def test_peek_returns_none_for_irreversible_service(self):
        ch = CommandHistory(undo_window=30.0)
        ch.push(
            entity_id="climate.living_room",
            domain="climate",
            service="set_temperature",
            data={"temperature": 22.0},
            description="set living room to 22",
        )
        assert ch.peek_undo_candidate() is None

    def test_pop_removes_entry_on_success(self):
        ch = CommandHistory(undo_window=30.0)
        ch.push(
            entity_id="switch.fan",
            domain="switch",
            service="turn_off",
            data={},
            description="turn off fan",
        )
        assert len(ch) == 1
        candidate = ch.pop_undo_candidate()
        assert candidate is not None
        assert candidate.inverse_service == "turn_on"
        assert len(ch) == 0

    def test_pop_returns_none_when_no_candidate(self):
        ch = CommandHistory()
        assert ch.pop_undo_candidate() is None

    def test_inverse_service_mapping(self):
        assert _INVERSE_SERVICES["turn_on"] == "turn_off"
        assert _INVERSE_SERVICES["turn_off"] == "turn_on"
        assert _INVERSE_SERVICES["lock"] == "unlock"
        assert _INVERSE_SERVICES["unlock"] == "lock"


class TestCommandEntryInverse:
    def test_entry_inverse_service(self):
        entry = CommandEntry(
            entity_id="light.hall",
            domain="light",
            service="turn_on",
            data={},
            description="turn on hall",
        )
        assert entry.inverse_service == "turn_off"

    def test_entry_no_inverse(self):
        entry = CommandEntry(
            entity_id="climate.bedroom",
            domain="climate",
            service="set_temperature",
            data={},
            description="set bedroom to 21",
        )
        assert entry.inverse_service is None


# ---------------------------------------------------------------------------
# HABridge confirmation and undo_last tests
# ---------------------------------------------------------------------------


def _make_bridge(mock_request_fn=None):
    """Return a HABridge with requests mocked out."""
    with patch("rex.ha_bridge._require_requests"):
        with patch("rex.ha_bridge.requests") as mock_requests:
            mock_session = MagicMock()
            mock_requests.Session.return_value = mock_session
            if mock_request_fn is not None:
                mock_session.request.side_effect = mock_request_fn
            from rex.ha_bridge import HABridge

            bridge = HABridge(
                base_url="http://ha.local:8123",
                token="test-token",
                entity_map={"bedroom light": "light.bedroom"},
            )
            bridge._session = mock_session
            return bridge


class TestHABridgeConfirmationAndUndo:
    def test_process_transcript_returns_confirmation(self):
        bridge = _make_bridge()
        bridge._session.request.return_value = MagicMock(status_code=200, json=lambda: {})
        result = bridge.process_transcript("turn on the bedroom light")
        assert result is not None
        assert "bedroom" in result.lower() or "turn on" in result.lower()

    def test_process_transcript_does_not_record_unverified_action(self):
        bridge = _make_bridge()
        bridge._session.request.return_value = MagicMock(status_code=200, json=lambda: {})
        bridge.process_transcript("turn on the bedroom light")
        assert len(bridge._command_history) == 0

    def test_undo_last_reverses_command(self):
        bridge = _make_bridge()
        bridge._session.request.return_value = MagicMock(status_code=200, json=lambda: {})
        bridge.process_transcript("turn on the bedroom light")
        result = bridge.undo_last()
        assert (
            "undo" in result.lower() or "turn off" in result.lower() or "undone" in result.lower()
        )

    def test_undo_last_returns_nothing_when_empty(self):
        bridge = _make_bridge()
        result = bridge.undo_last()
        assert "nothing" in result.lower() or "undo" in result.lower()

    def test_undo_last_returns_nothing_after_window(self):
        bridge = _make_bridge()
        bridge._session.request.return_value = MagicMock(status_code=200, json=lambda: {})
        bridge.process_transcript("turn on the bedroom light")
        result = bridge.undo_last(window=0.0)
        assert "nothing" in result.lower()


# ---------------------------------------------------------------------------
# Assistant undo routing test
# ---------------------------------------------------------------------------


def test_undo_pattern_matches():
    """_UNDO_PATTERN should match bare undo/undo-that utterances only."""
    from rex.assistant import _UNDO_PATTERN

    assert _UNDO_PATTERN.match("undo")
    assert _UNDO_PATTERN.match("undo that")
    assert _UNDO_PATTERN.match("  Undo That  ")
    assert not _UNDO_PATTERN.match("undo the thing")
    assert not _UNDO_PATTERN.match("please undo that")
