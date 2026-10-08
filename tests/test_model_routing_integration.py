"""Integration tests for model routing wired into Assistant.generate_reply()."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

import rex.assistant as assistant_module
from rex.assistant_errors import ConfigurationError
from rex.model_router import ModelRouter

# ---------------------------------------------------------------------------
# Minimal Settings stub
# ---------------------------------------------------------------------------


@dataclass
class _ModelRoutingStub:
    default: str = ""
    coding: str = ""
    reasoning: str = ""
    search: str = ""
    vision: str = ""
    fast: str = ""


@dataclass
class _SettingsStub:
    llm_provider: str = "transformers"
    llm_model: str = "stub-model"
    llm_max_tokens: int = 10
    llm_temperature: float = 0.7
    llm_top_p: float = 0.9
    llm_top_k: int = 50
    llm_seed: int = 42
    max_memory_items: int = 5
    transcripts_dir: str = "transcripts"
    persist_history: bool = False
    followups_enabled: bool = False
    ha_base_url: str | None = None
    ha_token: str | None = None
    user_id: str = "test"
    active_profile: str = "default"
    llm_routing_mode: str = "local_preferred"
    model_routing: _ModelRoutingStub = field(
        default_factory=lambda: _ModelRoutingStub(
            default="default-model",
            coding="coding-model",
        )
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _CapturingLLM:
    """Minimal LLM stub that records the model_name at call time."""

    def __init__(self):
        self.model_name = "stub-model"
        self._request_model: str | None = None
        self.calls: list[str] = []

    def set_request_model(self, model_name: str):
        previous = self._request_model
        self._request_model = model_name
        return previous

    def reset_request_model(self, token) -> None:
        self._request_model = token

    def generate(self, prompt, config=None):
        self.calls.append(self._request_model or self.model_name)
        return "stub reply"


def _make_assistant(monkeypatch, settings_obj, llm, *, available_models: set[str] | None = None):
    """Build an Assistant with stubbed LanguageModel and settings.

    *available_models* seeds the router's Ollama available-model cache so
    tests are not dependent on a live Ollama instance.  Pass an empty set to
    simulate "Ollama not running".  If omitted, all models are declared
    available (bypasses the check entirely).
    """

    class _LLMFactory:
        def __new__(cls, *args, **kwargs):
            return llm

    monkeypatch.setattr(assistant_module, "LanguageModel", _LLMFactory)

    # Suppress the real Ollama HTTP probe and optionally inject known models.
    def _fake_fetch(self) -> None:
        if available_models is not None:
            self._available_ollama_models = available_models

    monkeypatch.setattr(ModelRouter, "_fetch_ollama_models", _fake_fetch)

    a = assistant_module.Assistant(
        settings_obj=settings_obj,
        transcripts_dir="transcripts",
        user_id="default",
    )

    # If no specific available_models given, mark all routing targets as available
    # so tests that don't care about Ollama probing still work correctly.
    if available_models is None:
        routing = getattr(settings_obj, "model_routing", None)
        if routing is not None:
            all_models = {
                getattr(routing, f, "") or ""
                for f in ("default", "coding", "reasoning", "search", "vision", "fast")
            }
            a._router._available_ollama_models = all_models - {""}

    return a


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_coding_message_uses_coding_model(monkeypatch, tmp_path):
    """A code-related message should route to the coding model."""
    settings = _SettingsStub(
        model_routing=_ModelRoutingStub(
            default="default-model",
            coding="coding-model",
        )
    )
    llm = _CapturingLLM()
    a = _make_assistant(monkeypatch, settings, llm)

    asyncio.run(a.generate_reply("Write a function to reverse a string"))

    assert llm.calls, "LLM was never called"
    assert (
        llm.calls[0] == "coding-model"
    ), f"Expected coding-model but LLM was called with model {llm.calls[0]!r}"


def test_unconfigured_category_falls_back_to_default(monkeypatch, tmp_path):
    """A vision message with no vision model should fall back to routing default."""
    settings = _SettingsStub(
        model_routing=_ModelRoutingStub(
            default="default-model",
            coding="coding-model",
            # vision intentionally left empty
        )
    )
    llm = _CapturingLLM()
    a = _make_assistant(monkeypatch, settings, llm)

    asyncio.run(a.generate_reply("Describe this image for me"))

    assert llm.calls, "LLM was never called"
    assert (
        llm.calls[0] == "default-model"
    ), f"Expected default-model fallback but got {llm.calls[0]!r}"


def test_model_restored_after_call(monkeypatch):
    """The LLM model_name is restored to its original value after generate_reply."""
    settings = _SettingsStub(
        model_routing=_ModelRoutingStub(
            default="default-model",
            coding="coding-model",
        )
    )
    llm = _CapturingLLM()
    original_model = llm.model_name
    a = _make_assistant(monkeypatch, settings, llm)

    asyncio.run(a.generate_reply("Write a function in Python"))

    assert llm.model_name == original_model, "model_name was not restored after generate_reply"


def test_no_routing_config_uses_existing_model(monkeypatch):
    """When model_routing is not configured, the LLM is called without override."""

    @dataclass
    class _NoRoutingSettings(_SettingsStub):
        model_routing: None = None  # type: ignore[assignment]

    settings = _NoRoutingSettings()
    llm = _CapturingLLM()
    llm.model_name = "my-configured-model"
    a = _make_assistant(monkeypatch, settings, llm)

    asyncio.run(a.generate_reply("Hello"))

    assert llm.model_name == "my-configured-model"


def test_unavailable_ollama_model_falls_back_to_default(monkeypatch):
    """When the preferred Ollama model is not running, falls back to routing default."""
    settings = _SettingsStub(
        model_routing=_ModelRoutingStub(
            default="default-model",
            coding="codellama",  # Ollama model that is NOT in available list
        )
    )
    llm = _CapturingLLM()
    # available_models only contains default-model, not codellama
    a = _make_assistant(monkeypatch, settings, llm, available_models={"default-model"})

    asyncio.run(a.generate_reply("Write a Python function"))

    assert llm.calls, "LLM was never called"
    assert (
        llm.calls[0] == "default-model"
    ), f"Expected default-model fallback but got {llm.calls[0]!r}"


def test_no_network_call_for_openai_models(monkeypatch):
    """When all routing targets are OpenAI models, no Ollama probe is made."""
    settings = _SettingsStub(
        model_routing=_ModelRoutingStub(
            default="gpt-3.5-turbo",
            coding="gpt-4",
        )
    )
    llm = _CapturingLLM()

    fetch_called = []

    class _TrackedOpenAIRouter(ModelRouter):
        def _fetch_ollama_models(self) -> None:
            fetch_called.append(True)

    class _LLMFactory:
        def __new__(cls, *args, **kwargs):
            return llm

    monkeypatch.setattr(assistant_module, "LanguageModel", _LLMFactory)
    monkeypatch.setattr(assistant_module, "ModelRouter", _TrackedOpenAIRouter)
    assistant_module.Assistant(
        settings_obj=settings,
        transcripts_dir="transcripts",
        # Assistant instances are deliberately unbound unless the caller
        # supplies an identity.  This routing-only construction is still a
        # user-scoped application path, so model the explicit entrypoint
        # binding instead of relying on the deprecated settings fallback.
        user_id="test",
    )

    assert not fetch_called, "Ollama was probed even though all routing targets are OpenAI models"


def test_local_only_mode_is_passed_to_model_router(monkeypatch):
    settings = _SettingsStub(
        llm_provider="openai",
        llm_routing_mode="local_only",
        model_routing=_ModelRoutingStub(
            default="gpt-fast",
            reasoning="gpt-deep",
            fast="gpt-fast",
        ),
    )
    llm = _CapturingLLM()
    llm.model_name = "gpt-fast"
    a = _make_assistant(monkeypatch, settings, llm, available_models=set())

    with pytest.raises(ConfigurationError, match="local_only"):
        asyncio.run(a.generate_reply("Analyze the tradeoffs."))

    assert llm.calls == []
    assert llm.model_name == "gpt-fast"
    assert llm._request_model is None
