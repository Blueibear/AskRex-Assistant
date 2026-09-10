"""Unit tests for US-005: RexAgent.respond() HTTP path via OpenClaw.

Tests verify:
- When gateway is configured and use_openclaw_voice_backend is True, respond()
  POSTs to /v1/chat/completions and returns the response content.
- The POST payload has the expected shape (model, messages, user fields).
- On HTTP error, respond() falls back to local LLM and returns a result.
- In standalone mode (no gateway URL), respond() uses local LLM unchanged.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from rex.config import AppConfig
from rex.openclaw.agent import RexAgent
from rex.openclaw.errors import OpenClawAPIError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(
    *, use_voice_backend: bool = True, gateway_url: str = "http://localhost:18789"
) -> AppConfig:
    return AppConfig(
        openclaw_gateway_url=gateway_url,
        openclaw_gateway_token="test-token",
        use_openclaw_voice_backend=use_voice_backend,
        llm_model="openclaw:main",
    )


def _make_agent(config: AppConfig, llm_reply: str = "local reply") -> RexAgent:
    mock_llm = MagicMock()
    mock_llm.generate.return_value = llm_reply
    return RexAgent(llm=mock_llm, config=config, system_prompt="You are Rex.")


def _chat_response(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}]}


# ---------------------------------------------------------------------------
# US-005: OpenClaw HTTP path
# ---------------------------------------------------------------------------


class TestRespondHttpPath:
    def test_missing_identity_fails_before_openclaw_request(self, tmp_path):
        config = _make_config()
        agent = _make_agent(config)
        mock_client = MagicMock()

        with (
            patch(
                "rex.identity._session_state_path", return_value=tmp_path / "missing-session.json"
            ),
            patch("rex.openclaw.agent.get_openclaw_client", return_value=mock_client),
        ):
            import pytest

            with pytest.raises(PermissionError, match="identity"):
                agent.respond("Hello?")

        mock_client.post.assert_not_called()

    def test_posts_to_chat_completions_when_gateway_configured(self):
        """respond() POSTs to /v1/chat/completions when gateway and flag are set."""
        config = _make_config()
        agent = _make_agent(config)

        mock_client = MagicMock()
        mock_client.post.return_value = _chat_response("OpenClaw reply")

        with patch("rex.openclaw.agent.get_openclaw_client", return_value=mock_client):
            result = agent.respond("Hello?", user_key="james")

        mock_client.post.assert_called_once()
        (path,) = mock_client.post.call_args.args
        assert path == "/v1/chat/completions"
        assert result == "OpenClaw reply"

    def test_payload_contains_model_field(self):
        """The POST payload includes the model from config.llm_model."""
        config = _make_config()
        agent = _make_agent(config)

        mock_client = MagicMock()
        mock_client.post.return_value = _chat_response("reply")

        with patch("rex.openclaw.agent.get_openclaw_client", return_value=mock_client):
            agent.respond("Test prompt", user_key="james")

        payload = mock_client.post.call_args.kwargs["json"]
        assert payload["model"] == "openclaw:main"

    def test_payload_contains_messages_with_system_and_user(self):
        """The POST payload messages array includes system and user roles."""
        config = _make_config()
        agent = _make_agent(config)

        mock_client = MagicMock()
        mock_client.post.return_value = _chat_response("reply")

        with patch("rex.openclaw.agent.get_openclaw_client", return_value=mock_client):
            agent.respond("My question", user_key="james")

        payload = mock_client.post.call_args.kwargs["json"]
        messages = payload["messages"]
        roles = [m["role"] for m in messages]
        assert "system" in roles
        assert "user" in roles
        assert any(
            "My question" in message["content"] for message in messages if message["role"] == "user"
        )

    def test_payload_contains_user_field_when_user_key_provided(self):
        """When user_key is given, the POST payload includes a 'user' field."""
        config = _make_config()
        agent = _make_agent(config)

        mock_client = MagicMock()
        mock_client.post.return_value = _chat_response("reply")

        with patch("rex.openclaw.agent.get_openclaw_client", return_value=mock_client):
            with patch("rex.openclaw.agent.MemoryAdapter") as _mem:
                _mem.return_value.load_recent.return_value = []
                agent._memory = _mem.return_value
                agent.respond("Hello", user_key="alice")

        payload = mock_client.post.call_args.kwargs["json"]
        assert payload.get("user") == "alice"

    def test_payload_uses_identity_adapter_when_no_user_key(self):
        """When user_key is None, the POST payload derives 'user' from identity adapter."""
        config = _make_config()
        agent = _make_agent(config)

        mock_client = MagicMock()
        mock_client.post.return_value = _chat_response("reply")

        with patch("rex.openclaw.agent.get_openclaw_client", return_value=mock_client):
            with patch.object(agent._identity, "get_openclaw_user_key", return_value="james"):
                agent.respond("Hello")

        payload = mock_client.post.call_args.kwargs["json"]
        assert "user" in payload
        assert payload["user"] == "james"

    def test_local_llm_not_called_on_successful_http(self):
        """When OpenClaw replies successfully, the local LLM is not invoked."""
        config = _make_config()
        agent = _make_agent(config)

        mock_client = MagicMock()
        mock_client.post.return_value = _chat_response("OpenClaw reply")

        with patch("rex.openclaw.agent.get_openclaw_client", return_value=mock_client):
            agent.respond("Question", user_key="james")

        agent.llm.generate.assert_not_called()


# ---------------------------------------------------------------------------
# US-005: HTTP error fallback
# ---------------------------------------------------------------------------


class TestRespondHttpFallback:
    def test_falls_back_to_local_llm_on_api_error(self):
        """When OpenClaw returns an API error, respond() falls back to local LLM."""
        config = _make_config()
        agent = _make_agent(config, llm_reply="local fallback reply")

        mock_client = MagicMock()
        mock_client.post.side_effect = OpenClawAPIError(500, "internal server error")

        with patch("rex.openclaw.agent.get_openclaw_client", return_value=mock_client):
            result = agent.respond("Test prompt", user_key="james")

        agent.llm.generate.assert_called_once()
        assert result == "local fallback reply"

    def test_falls_back_to_local_llm_on_key_error(self):
        """When OpenClaw response is malformed (KeyError), falls back to local LLM."""
        config = _make_config()
        agent = _make_agent(config, llm_reply="local result")

        mock_client = MagicMock()
        mock_client.post.return_value = {"unexpected": "shape"}  # missing 'choices'

        with patch("rex.openclaw.agent.get_openclaw_client", return_value=mock_client):
            result = agent.respond("Prompt", user_key="james")

        agent.llm.generate.assert_called_once()
        assert result == "local result"

    def test_fallback_still_persists_history(self, tmp_path):
        """Fallback reply is still saved to memory when user_key is set."""
        from rex.openclaw.memory_adapter import MemoryAdapter

        config = _make_config()
        adapter = MemoryAdapter(memory_root=str(tmp_path))
        mock_llm = MagicMock()
        mock_llm.generate.return_value = "fallback"
        agent = RexAgent(
            llm=mock_llm,
            config=config,
            system_prompt="You are Rex.",
            memory_adapter=adapter,
        )

        mock_client = MagicMock()
        mock_client.post.side_effect = OpenClawAPIError(503, "service unavailable")

        with patch("rex.openclaw.agent.get_openclaw_client", return_value=mock_client):
            agent.respond("Save me", user_key="dan")

        history = adapter.load_recent("dan")
        assert len(history) == 2
        assert history[1]["text"] == "fallback"


# ---------------------------------------------------------------------------
# US-005: Standalone mode (no gateway URL)
# ---------------------------------------------------------------------------


class TestRespondStandaloneMode:
    def test_uses_local_llm_when_no_gateway_url(self):
        """With no gateway URL, respond() routes through local LLM unchanged."""
        config = _make_config(gateway_url="")
        agent = _make_agent(config, llm_reply="standalone reply")

        with patch("rex.openclaw.agent.get_openclaw_client", return_value=None):
            result = agent.respond("Hello")

        agent.llm.generate.assert_called_once()
        assert result == "standalone reply"

    def test_uses_local_llm_when_flag_is_false(self):
        """With gateway configured but flag=False, respond() uses local LLM."""
        config = _make_config(use_voice_backend=False)
        agent = _make_agent(config, llm_reply="flag-false reply")

        mock_client = MagicMock()

        with patch("rex.openclaw.agent.get_openclaw_client", return_value=mock_client):
            result = agent.respond("Hello")

        mock_client.post.assert_not_called()
        agent.llm.generate.assert_called_once()
        assert result == "flag-false reply"

    def test_no_http_call_in_standalone_mode(self):
        """In standalone mode, no HTTP client is created or called."""
        config = _make_config(gateway_url="")
        agent = _make_agent(config)

        with patch("rex.openclaw.agent.get_openclaw_client", return_value=None) as mock_get:
            agent.respond("Hello")

        # get_openclaw_client was called (to check), but no POST was made
        mock_get.assert_called_once()
