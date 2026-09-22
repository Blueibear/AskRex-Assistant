"""Regression for TEST-010 — spurious/unsupported model-emitted tool requests.

Ordinary LM Studio (OpenAI-compatible) chat turns must not be replaced by an
unrelated failed tool execution merely because the model emits a spurious or
unsupported ``TOOL_REQUEST:`` line. ``media_read`` is a real, canonically
registered tool used by deterministic/authorized dispatch elsewhere, but it
was never documented to the model as callable through the free-text
``TOOL_REQUEST:`` convention, so a model-emitted request for it must be
ignored rather than dispatched.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from rex.openclaw.tool_executor import TEXT_TOOL_REQUEST_ALLOWED_TOOLS, route_if_tool_request


class TestSpuriousMediaToolRequestIgnored:
    """Exact TEST-010 repro turns: the model hallucinates a media_read call."""

    def test_declarative_turn_does_not_dispatch_media_read(self):
        # Reproduces the exact declarative turn: "My test phrase is purple
        # elephant 7319." The completion below is the kind of spurious
        # model-emitted tool call observed on physical LM Studio hardware.
        llm_text = (
            'TOOL_REQUEST: {"tool": "media_read", "args": {"query": "purple elephant 7319"}}'
        )
        model_call_fn = MagicMock(return_value="Got it, purple elephant 7319.")

        with patch("rex.openclaw.tool_executor.execute_tool") as mock_execute:
            result = route_if_tool_request(llm_text, {}, model_call_fn)

        mock_execute.assert_not_called()
        assert result == "Got it, purple elephant 7319."
        model_call_fn.assert_called_once()
        (tool_message,) = model_call_fn.call_args.args
        assert tool_message["role"] == "tool"
        assert "media_read" in tool_message["content"]
        assert "not available" in tool_message["content"]

    def test_contextual_turn_does_not_dispatch_media_read(self):
        # Reproduces the exact contextual follow-up: "What was the exact
        # test phrase I just gave you?"
        llm_text = (
            'TOOL_REQUEST: {"tool": "media_read", '
            '"args": {"query": "what was the exact test phrase"}}'
        )
        model_call_fn = MagicMock(return_value="You said: purple elephant 7319.")

        with patch("rex.openclaw.tool_executor.execute_tool") as mock_execute:
            result = route_if_tool_request(llm_text, {}, model_call_fn)

        mock_execute.assert_not_called()
        assert result == "You said: purple elephant 7319."

    def test_unsupported_tool_call_never_reaches_audit_log(self):
        llm_text = 'TOOL_REQUEST: {"tool": "media_read", "args": {}}'
        model_call_fn = MagicMock(return_value="A normal reply.")

        with patch("rex.openclaw.tool_executor._log_audit_entry") as mock_audit:
            result = route_if_tool_request(llm_text, {}, model_call_fn)

        mock_audit.assert_not_called()
        assert result == "A normal reply."

    def test_model_call_failure_after_unsupported_tool_falls_back_gracefully(self):
        llm_text = 'TOOL_REQUEST: {"tool": "media_read", "args": {}}'

        def _raise(_msg):
            raise RuntimeError("boom")

        result = route_if_tool_request(llm_text, {}, _raise)
        assert result == "Sorry, I could not complete that tool request."

    @pytest.mark.parametrize("tool_name", ["media_read", "media_manage", "shell_command"])
    def test_arbitrary_unsupported_tools_are_rejected(self, tool_name):
        assert tool_name not in TEXT_TOOL_REQUEST_ALLOWED_TOOLS
        llm_text = f'TOOL_REQUEST: {{"tool": "{tool_name}", "args": {{}}}}'
        model_call_fn = MagicMock(return_value="ok")

        with patch("rex.openclaw.tool_executor.execute_tool") as mock_execute:
            route_if_tool_request(llm_text, {}, model_call_fn)

        mock_execute.assert_not_called()


class TestLegitimateToolRequestStillDispatches:
    """Control: explicitly documented tools must still execute normally."""

    def test_time_now_control_still_dispatches(self):
        llm_text = 'TOOL_REQUEST: {"tool": "time_now", "args": {"location": "Dallas, TX"}}'
        model_call_fn = MagicMock(return_value="It is 3:33 PM in Dallas.")
        fake_result = {
            "local_time": "2026-09-22 15:33",
            "date": "2026-09-22",
            "timezone": "America/Chicago",
        }

        with patch(
            "rex.openclaw.tool_executor.execute_tool", return_value=fake_result
        ) as mock_execute:
            result = route_if_tool_request(llm_text, {}, model_call_fn)

        mock_execute.assert_called_once()
        assert result == "It is 3:33 PM in Dallas."
        (tool_message,) = model_call_fn.call_args.args
        assert tool_message["role"] == "tool"
        assert "local_time" in tool_message["content"]

    def test_time_now_is_in_allowed_set(self):
        assert "time_now" in TEXT_TOOL_REQUEST_ALLOWED_TOOLS
        assert "weather_now" in TEXT_TOOL_REQUEST_ALLOWED_TOOLS
        assert "web_search" in TEXT_TOOL_REQUEST_ALLOWED_TOOLS
