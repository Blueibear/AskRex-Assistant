from __future__ import annotations

from unittest.mock import MagicMock, patch

from rex.ha.mutation_service import (
    HAMutationResult,
    HAOutcome,
    HARisk,
)


class _RecordingMutationService:
    def __init__(self) -> None:
        self.calls = []

    def execute(self, mutation):
        self.calls.append(mutation)
        if mutation.confirmation_token == "server-only-token":
            return HAMutationResult(
                status=HAOutcome.VERIFIED,
                detail="Verified sensitive action.",
                entity_id=mutation.entity_id,
                domain=mutation.domain,
                service=mutation.service,
                request_id=mutation.request_id,
                risk=HARisk.SENSITIVE,
                expected={"state": "unlocked", "attributes": {}},
            )
        return HAMutationResult(
            status=HAOutcome.CONFIRMATION_REQUIRED,
            detail="Confirmation required before this sensitive action.",
            entity_id=mutation.entity_id,
            domain=mutation.domain,
            service=mutation.service,
            request_id=mutation.request_id,
            risk=HARisk.SENSITIVE,
            confirmation_token="server-only-token",
        )


def _bridge(service: _RecordingMutationService):
    with patch("rex.ha_bridge._require_requests"):
        with patch("rex.ha_bridge.requests") as mock_requests:
            session = MagicMock()
            mock_requests.Session.return_value = session
            from rex.ha_bridge import HABridge

            bridge = HABridge(
                base_url="http://ha.local:8123",
                token="token",
                entity_map={
                    "front door": "lock.front_door",
                    "kitchen light": "light.kitchen",
                },
                user_id="default",
                mutation_service=service,
            )
            bridge._session = session
            return bridge, session


def test_sensitive_transcript_requires_policy_confirmation_without_dispatch():
    service = _RecordingMutationService()
    bridge, session = _bridge(service)

    reply = bridge.process_transcript("Unlock the front door")

    assert reply and "confirm" in reply.lower()
    assert len(service.calls) == 1
    assert service.calls[0].domain == "lock"
    assert service.calls[0].service == "unlock"
    session.request.assert_not_called()


def test_negation_and_question_do_not_enter_legacy_mutation_path():
    service = _RecordingMutationService()
    bridge, session = _bridge(service)

    assert bridge.process_transcript("Please don't unlock the front door") is None
    assert bridge.process_transcript("Should I lock the front door tonight?") is None
    assert service.calls == []
    session.request.assert_not_called()


def test_substring_entity_match_is_not_authoritative():
    service = _RecordingMutationService()
    bridge, session = _bridge(service)

    assert bridge.process_transcript("turn off the light") is None
    assert service.calls == []
    assert not any(
        "/api/services/" in str(call.kwargs.get("url", ""))
        for call in session.request.call_args_list
    )


def test_model_emitted_ha_tags_are_stripped_without_execution():
    service = _RecordingMutationService()
    bridge, session = _bridge(service)
    reply = bridge.post_process_response(
        "Your day looks calm. [[ha:lock.unlock entity_id=lock.front_door]]"
    )
    assert reply == "Your day looks calm."
    assert service.calls == []
    session.request.assert_not_called()

    reply = bridge.post_process_response(
        "[[ha:alarm_control_panel.alarm_disarm entity_id=alarm_control_panel.home]]"
    )
    assert "did not execute" in reply.lower()
    assert service.calls == []
    session.request.assert_not_called()


def test_sensitive_confirmation_is_action_bound_and_next_turn_only():
    service = _RecordingMutationService()
    bridge, session = _bridge(service)

    first = bridge.process_transcript("Unlock the front door")
    assert first and "say 'confirm'" in first.lower()
    assert len(service.calls) == 1
    assert service.calls[0].confirmation_token is None

    confirmed = bridge.process_transcript("confirm")
    assert confirmed == "Confirmed lock front door is unlocked."
    assert len(service.calls) == 2
    assert service.calls[1].request_id == service.calls[0].request_id
    assert service.calls[1].confirmation_token == "server-only-token"
    session.request.assert_not_called()


def test_intervening_turn_clears_sensitive_confirmation():
    service = _RecordingMutationService()
    bridge, session = _bridge(service)

    assert bridge.process_transcript("Unlock the front door")
    assert bridge.process_transcript("What time is it?") is None
    assert bridge.process_transcript("confirm") is None
    assert len(service.calls) == 1
    session.request.assert_not_called()


def test_cancel_clears_sensitive_confirmation_without_dispatch():
    service = _RecordingMutationService()
    bridge, session = _bridge(service)

    assert bridge.process_transcript("Unlock the front door")
    cancelled = bridge.process_transcript("cancel")
    assert cancelled and "canceled" in cancelled.lower()
    assert bridge.process_transcript("confirm") is None
    assert len(service.calls) == 1
    session.request.assert_not_called()
