"""US-016: Provider routing acceptance tests.

Covers:
- provider selection configurable
- routing logic implemented
- fallback behavior works
- Typecheck passes
"""

from __future__ import annotations

import types

import pytest

from config import AppConfig
from llm_client import LanguageModel
from rex.llm_client import (
    AnthropicStrategy,
    EchoStrategy,
    OfflineTransformersStrategy,
    OpenAIStrategy,
)


def _make_fake_openai_client(content: str = "ok") -> types.SimpleNamespace:
    class _Choice:
        def __init__(self) -> None:
            self.message = types.SimpleNamespace(content=content, tool_calls=None)

    class _Response:
        def __init__(self) -> None:
            self.choices = [_Choice()]

    fake = types.SimpleNamespace()
    fake.chat = types.SimpleNamespace(
        completions=types.SimpleNamespace(create=lambda **_: _Response())
    )
    return fake


def _make_fake_anthropic_client(content: str = "ok") -> types.SimpleNamespace:
    class _Content:
        text = content

    class _Response:
        content = [_Content()]

    fake = types.SimpleNamespace()
    fake.messages = types.SimpleNamespace(create=lambda **_: _Response())
    return fake


# ---------------------------------------------------------------------------
# Criterion 1: provider selection configurable
# ---------------------------------------------------------------------------


def test_provider_selected_openai(monkeypatch):
    """Setting llm_provider=openai routes to OpenAIStrategy."""
    monkeypatch.setattr(
        LanguageModel, "_ensure_openai_client", lambda self: _make_fake_openai_client()
    )
    cfg = AppConfig(llm_model=None, llm_provider="openai", openai_model="gpt-test")
    model = LanguageModel(cfg)
    assert isinstance(model.strategy, OpenAIStrategy)
    assert model.provider == "openai"


def test_provider_selected_anthropic(monkeypatch):
    """Setting llm_provider=anthropic routes to AnthropicStrategy."""
    monkeypatch.setattr(
        LanguageModel, "_ensure_anthropic_client", lambda self: _make_fake_anthropic_client()
    )
    cfg = AppConfig(
        llm_model=None,
        llm_provider="anthropic",
        anthropic_model="claude-test",
        anthropic_api_key="key",  # pragma: allowlist secret
    )
    model = LanguageModel(cfg)
    assert isinstance(model.strategy, AnthropicStrategy)
    assert model.provider == "anthropic"


def test_provider_selected_echo():
    """Setting llm_provider=echo routes to EchoStrategy."""
    cfg = AppConfig(llm_model="my-model", llm_provider="echo")
    model = LanguageModel(cfg)
    assert isinstance(model.strategy, EchoStrategy)
    assert model.provider == "echo"


# ---------------------------------------------------------------------------
# Criterion 2: routing logic implemented
# ---------------------------------------------------------------------------


def test_routing_openai_generates_response(monkeypatch):
    """OpenAI route produces a response string."""
    monkeypatch.setattr(
        LanguageModel,
        "_ensure_openai_client",
        lambda self: _make_fake_openai_client("routed-openai"),
    )
    cfg = AppConfig(llm_model=None, llm_provider="openai", openai_model="gpt-test")
    model = LanguageModel(cfg)
    result = model.generate("hello")
    assert result == "routed-openai"


def test_routing_anthropic_generates_response(monkeypatch):
    """Anthropic route produces a response string."""
    monkeypatch.setattr(
        LanguageModel,
        "_ensure_anthropic_client",
        lambda self: _make_fake_anthropic_client("routed-anthropic"),
    )
    cfg = AppConfig(
        llm_model=None,
        llm_provider="anthropic",
        anthropic_model="claude-test",
        anthropic_api_key="key",  # pragma: allowlist secret
    )
    model = LanguageModel(cfg)
    result = model.generate("hello")
    assert result == "routed-anthropic"


def test_routing_echo_generates_response():
    """Echo route echoes the prompt back."""
    cfg = AppConfig(llm_model="echo-model", llm_provider="echo")
    model = LanguageModel(cfg)
    result = model.generate("test prompt")
    assert "test prompt" in result


def test_routing_ollama_unavailable_raises(monkeypatch):
    """When ollama package is absent, OllamaStrategy raises ConfigurationError."""
    from rex.assistant_errors import ConfigurationError

    monkeypatch.setattr("rex.llm_client.OLLAMA_AVAILABLE", False)
    cfg = AppConfig(llm_model="llama3", llm_provider="ollama")
    with pytest.raises(ConfigurationError):
        LanguageModel(cfg)


def test_routing_transformers_unavailable_falls_back_to_offline(monkeypatch):
    """When torch/transformers absent, transformers provider falls back to offline mode."""
    monkeypatch.setattr("rex.llm_client.TORCH_AVAILABLE", False)
    monkeypatch.setattr("rex.llm_client.TRANSFORMERS_AVAILABLE", False)
    cfg = AppConfig(llm_model="some-model", llm_provider="transformers")
    model = LanguageModel(cfg)
    assert isinstance(model.strategy, OfflineTransformersStrategy)


# ---------------------------------------------------------------------------
# Criterion 3: fallback behavior works
# ---------------------------------------------------------------------------


def test_unknown_provider_falls_back_to_echo():
    """Completely unknown provider name falls back to EchoStrategy."""
    cfg = AppConfig(llm_model="x-model", llm_provider="nonexistent-provider-xyz")
    model = LanguageModel(cfg)
    assert isinstance(model.strategy, EchoStrategy)


def test_unknown_provider_still_generates_response():
    """Fallback EchoStrategy should still return a non-empty string."""
    cfg = AppConfig(llm_model="x-model", llm_provider="totally-unknown")
    model = LanguageModel(cfg)
    result = model.generate("hello fallback")
    assert isinstance(result, str)
    assert len(result) > 0


def test_provider_routing_respects_override(monkeypatch):
    """Provider override kwarg takes precedence over config value."""
    monkeypatch.setattr(
        LanguageModel, "_ensure_openai_client", lambda self: _make_fake_openai_client()
    )
    # Config says "echo" but override says "openai"
    cfg = AppConfig(llm_model=None, llm_provider="echo", openai_model="gpt-test")
    model = LanguageModel(cfg, provider="openai")
    assert model.provider == "openai"
    assert isinstance(model.strategy, OpenAIStrategy)
