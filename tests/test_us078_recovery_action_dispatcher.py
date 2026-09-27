from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

from rex.actions.dispatcher import ActionDispatcher
from rex.capabilities.recovery import RecoveryAction, RecoveryActionKind, RecoveryPlan
from rex.intent.router import IntentResult
from rex.runtime.events import EventKind, TurnEventStream
from rex.runtime.turn import (
    AuthorizationSnapshotRef,
    ResponseMode,
    TurnContext,
    TurnScope,
    TurnSource,
)
from rex.tools.dispatcher import ToolDispatcher
from rex.tools.registry import ToolRegistry


class _ResultHandler:
    async def process(self, transcript, completion, **kwargs):
        return completion


class _RecoveryDispatcher:
    def __init__(self, plan: RecoveryPlan | None) -> None:
        self.plan = plan

    def select_tools_for_user(self, message, *, user_id):
        return []

    def recovery_plan(self, message, *, user_id):
        return self.plan


class _SelectedToolDispatcher(_RecoveryDispatcher):
    def select_tools_for_user(self, message, *, user_id):
        return [SimpleNamespace(name="existing_tool", operation="read")]

    def execute_tools(self, selected, transcript, *, user_id, scope="user"):
        return {"existing_tool": "working result"}

    def format_tool_context(self, results):
        return "existing_tool: working result"


def _context_builder():
    return SimpleNamespace(
        build=lambda *args, **kwargs: SimpleNamespace(
            messages=[{"role": "user", "content": "request"}], prompt="request"
        )
    )


def _context():
    return SimpleNamespace(messages=[{"role": "user", "content": "request"}], prompt="request")


def _dispatcher(tool_dispatcher, *, ha_bridge=None):
    llm = MagicMock()
    llm.generate.return_value = "normal llm answer"
    return (
        ActionDispatcher(
            context_builder=_context_builder(),
            llm=llm,
            result_handler=_ResultHandler(),
            tool_dispatcher=tool_dispatcher,
            ha_bridge=ha_bridge,
        ),
        llm,
    )


def _recovery_plan() -> RecoveryPlan:
    return RecoveryPlan(
        message="Weather is not configured. Set weather_api_key before retrying.",
        actions=(
            RecoveryAction(
                kind=RecoveryActionKind.ENABLE_CAPABILITY,
                label="Configure weather_lookup",
                detail="Set the required configuration: weather_api_key.",
                source="local",
                target="weather_lookup",
                requires_confirmation=True,
            ),
        ),
        searched_sources=("local_enabled", "local_disabled"),
    )


def test_gap_recovery_returns_structured_action_without_calling_llm() -> None:
    dispatcher, llm = _dispatcher(_RecoveryDispatcher(_recovery_plan()))
    events = []
    turn = TurnContext.create(
        user_id="james",
        scope=TurnScope.USER,
        source=TurnSource.ASSISTANT,
        device_id=None,
        response_mode=ResponseMode.SCREEN,
        authorization=AuthorizationSnapshotRef("policy:v1", "permissions:james:v1"),
    )
    stream = TurnEventStream(turn, observer=events.append)

    result = asyncio.run(
        dispatcher.dispatch(
            IntentResult(handled=False, response=None, intent_type=None),
            _context(),
            "check weather",
            user_id="james",
            turn_events=stream,
        )
    )

    assert result.response.startswith("Weather is not configured")
    assert result.model_generated is False
    assert result.recovery_actions[0]["kind"] == "enable_capability"
    assert result.recovery_actions[0]["requires_confirmation"] is True
    llm.generate.assert_not_called()
    recovery_events = [
        event
        for event in events
        if event.kind is EventKind.CAPABILITY_PROGRESS and event.details.get("stage") == "recovery"
    ]
    assert len(recovery_events) == 1
    assert recovery_events[0].details["recovery"]["actions"][0]["target"] == "weather_lookup"


def test_no_recovery_plan_preserves_normal_llm_path() -> None:
    dispatcher, llm = _dispatcher(_RecoveryDispatcher(None))

    result = asyncio.run(
        dispatcher.dispatch(
            IntentResult(handled=False, response=None, intent_type=None),
            _context(),
            "what is gravity",
            user_id="james",
        )
    )

    assert result.response == "normal llm answer"
    assert result.recovery_actions == []
    assert result.model_generated is True
    llm.generate.assert_called_once()


def test_existing_selected_tool_prevents_gap_recovery() -> None:
    recovery = MagicMock(return_value=_recovery_plan())
    tool_dispatcher = _SelectedToolDispatcher(_recovery_plan())
    tool_dispatcher.recovery_plan = recovery  # type: ignore[method-assign]
    dispatcher, llm = _dispatcher(tool_dispatcher)

    result = asyncio.run(
        dispatcher.dispatch(
            IntentResult(handled=False, response=None, intent_type=None),
            _context(),
            "existing tool",
            user_id="james",
        )
    )

    assert result.model_generated is True
    assert result.recovery_actions == []
    recovery.assert_not_called()
    llm.generate.assert_called_once()


def test_home_assistant_completion_prevents_gap_recovery() -> None:
    ha = MagicMock()
    ha.enabled = True
    ha.process_transcript.return_value = "Kitchen light is on."
    tool_dispatcher = _RecoveryDispatcher(_recovery_plan())
    tool_dispatcher.recovery_plan = MagicMock(return_value=_recovery_plan())  # type: ignore[method-assign]
    dispatcher, llm = _dispatcher(tool_dispatcher, ha_bridge=ha)

    result = asyncio.run(
        dispatcher.dispatch(
            IntentResult(handled=False, response=None, intent_type=None),
            _context(),
            "turn on kitchen light",
            user_id="james",
        )
    )

    assert result.response == "Kitchen light is on."
    assert result.recovery_actions == []
    tool_dispatcher.recovery_plan.assert_not_called()
    llm.generate.assert_not_called()


def test_recovery_message_never_claims_proposed_action_completed() -> None:
    dispatcher, _ = _dispatcher(_RecoveryDispatcher(_recovery_plan()))

    result = asyncio.run(
        dispatcher.dispatch(
            IntentResult(handled=False, response=None, intent_type=None),
            _context(),
            "check weather",
            user_id="james",
        )
    )

    lowered = result.response.lower()
    assert "done" not in lowered
    assert "enabled successfully" not in lowered
    assert "set weather_api_key before retrying" in lowered


def test_ordinary_creative_request_with_real_gap_resolver_stays_on_llm_path() -> None:
    dispatcher, llm = _dispatcher(ToolDispatcher(ToolRegistry()))

    result = asyncio.run(
        dispatcher.dispatch(
            IntentResult(handled=False, response=None, intent_type=None),
            _context(),
            "write me a poem about summer",
            user_id="james",
        )
    )

    assert result.response == "normal llm answer"
    assert result.recovery_actions == []
    assert result.model_generated is True
    llm.generate.assert_called_once()
