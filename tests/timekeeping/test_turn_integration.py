from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

from rex.mobile_api.action_context import mobile_action_context
from rex.tools.protocol import ToolResult
from tests.test_us016_action_dispatcher import (
    _make_context,
    _make_dispatcher,
    _unhandled_intent,
)


class ExactTimekeepingDispatcher:
    def __init__(self) -> None:
        self.dispatch_calls: list[tuple[str, dict, dict]] = []
        self.select_calls = 0

    def dispatch(self, name, args, context):
        self.dispatch_calls.append((name, args, context))
        return ToolResult(
            success=True,
            output={"ok": True, "tool": name},
            status="verified" if name == "timekeeping_manage" else "completed",
        )

    def select_tools(self, transcript):
        self.select_calls += 1
        raise AssertionError(
            "generic capability selection must not run for exact timekeeping intent"
        )

    def format_tool_context(self, results):
        return f"timekeeping={results!r}"


def test_desktop_timer_mutation_uses_exact_manage_tool_and_skips_ha(monkeypatch) -> None:
    monkeypatch.setattr(
        "rex.timekeeping.tools.resolve_user_timezone",
        lambda user_id: "America/Chicago",
    )
    tools = ExactTimekeepingDispatcher()
    ha = MagicMock()
    ha.enabled = True
    dispatcher = _make_dispatcher(tool_dispatcher=tools, ha_bridge=ha)

    result = asyncio.run(
        dispatcher.dispatch(
            _unhandled_intent(),
            _make_context(),
            "set a 10-minute pasta timer",
            user_id="james",
        )
    )

    assert result.success is True
    assert [call[0] for call in tools.dispatch_calls] == ["timekeeping_manage"]
    assert tools.dispatch_calls[0][1] == {"transcript": "set a 10-minute pasta timer"}
    assert tools.dispatch_calls[0][2]["user_id"] == "james"
    assert tools.select_calls == 0
    ha.process_transcript.assert_not_called()


def test_desktop_timer_query_uses_exact_read_tool(monkeypatch) -> None:
    monkeypatch.setattr(
        "rex.timekeeping.tools.resolve_user_timezone",
        lambda user_id: "America/Chicago",
    )
    tools = ExactTimekeepingDispatcher()
    dispatcher = _make_dispatcher(tool_dispatcher=tools)

    asyncio.run(
        dispatcher.dispatch(
            _unhandled_intent(),
            _make_context(),
            "how much time is left on the pasta timer",
            user_id="james",
        )
    )

    assert [call[0] for call in tools.dispatch_calls] == ["timekeeping_read"]
    assert tools.select_calls == 0


class MobileMutationDispatcher:
    def __init__(self) -> None:
        self.dispatch_calls = 0
        self.execute_calls = 0

    def dispatch(self, name, args, context):
        self.dispatch_calls += 1
        raise AssertionError("mobile free-form mutation must not pre-dispatch")

    def select_tools(self, transcript):
        return [SimpleNamespace(name="timekeeping_manage", operation="mutation")]

    def execute_tools(self, selected, transcript, *, user_id, scope="user"):
        self.execute_calls += 1
        raise AssertionError("mobile mutation must be filtered until structured action binding")

    def format_tool_context(self, results):
        return ""


def test_mobile_free_form_timer_mutation_waits_for_structured_action(monkeypatch) -> None:
    monkeypatch.setattr(
        "rex.timekeeping.tools.resolve_user_timezone",
        lambda user_id: "America/Chicago",
    )
    tools = MobileMutationDispatcher()
    dispatcher = _make_dispatcher(tool_dispatcher=tools)

    with mobile_action_context(frozenset({"chat.send"})):
        result = asyncio.run(
            dispatcher.dispatch(
                _unhandled_intent(),
                _make_context(),
                "set a 10-minute pasta timer",
                user_id="james",
            )
        )

    assert result.response == "LLM reply"
    assert tools.dispatch_calls == 0
    assert tools.execute_calls == 0


def test_voice_mode_uses_same_exact_timekeeping_route(monkeypatch) -> None:
    monkeypatch.setattr(
        "rex.timekeeping.tools.resolve_user_timezone",
        lambda user_id: "America/Chicago",
    )
    tools = ExactTimekeepingDispatcher()
    dispatcher = _make_dispatcher(tool_dispatcher=tools)

    asyncio.run(
        dispatcher.dispatch(
            _unhandled_intent(),
            _make_context(),
            "set a 45 second timer",
            user_id="james",
            voice_mode=True,
        )
    )

    assert [call[0] for call in tools.dispatch_calls] == ["timekeeping_manage"]
    assert tools.select_calls == 0


def test_desktop_turn_with_real_tool_dispatcher_persists_verified_timer(
    tmp_path, monkeypatch
) -> None:
    from rex.capabilities.registry import CapabilityRegistry
    from rex.timekeeping.runtime import (
        set_timekeeping_service,
        shutdown_timekeeping_runtime,
    )
    from rex.timekeeping.service import TimekeepingService
    from rex.tools.dispatcher import ToolDispatcher
    from rex.tools.registry import _build_default_registry

    service = TimekeepingService(tmp_path / "timekeeping.json")
    set_timekeeping_service(service)
    monkeypatch.setattr(
        "rex.timekeeping.tools.resolve_user_timezone",
        lambda user_id: "America/Chicago",
    )
    tools = ToolDispatcher(_build_default_registry(capability_registry=CapabilityRegistry()))
    dispatcher = _make_dispatcher(tool_dispatcher=tools)
    try:
        result = asyncio.run(
            dispatcher.dispatch(
                _unhandled_intent(),
                _make_context(),
                "set a 2-minute pasta timer",
                user_id="james",
            )
        )

        assert result.success is True
        timers = service.list_timers("james")
        assert len(timers) == 1
        assert timers[0].name == "pasta"
        assert 110 <= service.remaining_seconds(timers[0].timer_id, "james") <= 120
    finally:
        shutdown_timekeeping_runtime()
        set_timekeeping_service(None)
