from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

from rex.actions.dispatcher import ActionDispatcher
from rex.intent.router import IntentResult


class _ResultHandler:
    async def process(self, transcript, completion, **kwargs):
        return completion


class _SearchDispatcher:
    def __init__(self, result: str) -> None:
        self.result = result

    def select_tools(self, transcript):
        return [SimpleNamespace(name="web_search", operation="read")]

    def execute_tools(self, selected, transcript, *, user_id, scope="user"):
        return {"web_search": self.result}

    def format_tool_context(self, results):
        return f"web_search: {results['web_search']}"


def _context_builder():
    return SimpleNamespace(
        build=lambda *args, **kwargs: SimpleNamespace(
            messages=[
                {"role": "system", "content": kwargs.get("tool_context", "")},
                {"role": "user", "content": "what is in the news today"},
            ],
            prompt="what is in the news today",
        )
    )


def _context():
    return SimpleNamespace(
        messages=[{"role": "user", "content": "what is in the news today"}],
        prompt="what is in the news today",
    )


def test_verified_current_news_result_is_grounded_before_llm_answer() -> None:
    llm = MagicMock()
    llm.generate.return_value = "The verified result says the top story is current."
    dispatcher = ActionDispatcher(
        context_builder=_context_builder(),
        llm=llm,
        result_handler=_ResultHandler(),
        tool_dispatcher=_SearchDispatcher(
            "Top story - https://example.test/story\nVerified current snippet"
        ),
    )

    result = asyncio.run(
        dispatcher.dispatch(
            IntentResult(handled=False, response=None, intent_type="current_info"),
            _context(),
            "what is in the news today",
            user_id="james",
        )
    )

    assert result.model_generated is True
    assert "verified result" in result.response.lower()
    messages = llm.generate.call_args.kwargs["messages"]
    assert "Top story" in messages[0]["content"]
    assert "CURRENT-INFO GROUNDING" in messages[0]["content"]
    assert "supplementing with model memory" in messages[0]["content"]


def test_failed_current_news_search_returns_honest_failure_without_llm() -> None:
    llm = MagicMock()
    dispatcher = ActionDispatcher(
        context_builder=_context_builder(),
        llm=llm,
        result_handler=_ResultHandler(),
        tool_dispatcher=_SearchDispatcher("[tool error: search provider unavailable]"),
    )

    result = asyncio.run(
        dispatcher.dispatch(
            IntentResult(handled=False, response=None, intent_type="current_info"),
            _context(),
            "what is in the news today",
            user_id="james",
        )
    )

    assert result.model_generated is False
    assert "couldn't verify current news" in result.response.lower()
    assert "won't guess" in result.response.lower()
    llm.generate.assert_not_called()


def test_missing_dispatcher_fails_closed_for_current_news() -> None:
    llm = MagicMock()
    dispatcher = ActionDispatcher(
        context_builder=_context_builder(),
        llm=llm,
        result_handler=_ResultHandler(),
        tool_dispatcher=None,
    )

    result = asyncio.run(
        dispatcher.dispatch(
            IntentResult(handled=False, response=None, intent_type="current_info"),
            _context(),
            "what is in the news today",
            user_id="james",
        )
    )

    assert "couldn't verify current news" in result.response.lower()
    llm.generate.assert_not_called()


def test_current_news_bypasses_pending_home_assistant_clarification() -> None:
    llm = MagicMock()
    llm.generate.return_value = "Verified news response"
    ha_bridge = MagicMock()
    ha_bridge.enabled = True
    ha_bridge.process_transcript.return_value = "Which light did you mean?"
    dispatcher = ActionDispatcher(
        context_builder=_context_builder(),
        llm=llm,
        result_handler=_ResultHandler(),
        tool_dispatcher=_SearchDispatcher(
            "Top story - https://example.test/story\nVerified current snippet"
        ),
        ha_bridge=ha_bridge,
    )

    result = asyncio.run(
        dispatcher.dispatch(
            IntentResult(handled=False, response=None, intent_type="current_info"),
            _context(),
            "what happened today?",
            user_id="james",
        )
    )

    assert result.response == "Verified news response"
    ha_bridge.process_transcript.assert_not_called()
    ha_bridge.undo_last.assert_not_called()
