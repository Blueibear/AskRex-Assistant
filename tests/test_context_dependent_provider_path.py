"""Regression coverage for TEST-005.

Context-dependent desktop chat turns (e.g. "what did I just ask you?") must
use the exact same configured LLM provider/runtime as stateless turns, and
must actually include the prior user turn in the messages sent to the LLM.

Historically, "hello" and "what time is it" style prompts never reach the
LLM at all -- ``IntentRouter`` answers them directly (see
``rex/intent/router.py``).  The first turn that actually needs a real LLM
call is often a context-dependent follow-up, which made it look like
follow-up turns used a broken/different provider path when in fact *any*
real LLM call used the same (previously mis-packaged) OpenAI-compatible
client.  These tests prove the provider path is identical for both kinds of
turns and that prior context is genuinely included.
"""

from __future__ import annotations

import asyncio
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import rex.assistant as assistant_module
from rex.assistant import ConversationTurn
from rex.model_router import ModelRouter

_REPO_ROOT = Path(__file__).resolve().parent.parent


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
    llm_provider: str = "openai"
    llm_model: str = "lm-studio-model"
    llm_max_tokens: int = 64
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
    user_id: str = "default"
    active_profile: str = "default"
    llm_routing_mode: str = "local_preferred"
    openai_base_url: str | None = "http://127.0.0.1:1234/v1"
    model_routing: _ModelRoutingStub = field(default_factory=_ModelRoutingStub)


class _ContextCapturingLLM:
    """Stub standing in for the configured OpenAI-compatible (LM Studio) client.

    Records the provider/model and the exact ``messages`` payload for every
    call, so tests can assert both provider-path consistency and that prior
    turns are genuinely present in what would be sent to the LLM.
    """

    def __init__(self, model_name: str = "lm-studio-model", provider: str = "openai") -> None:
        self.model_name = model_name
        self.provider = provider
        self._request_model: str | None = None
        self.calls: list[dict] = []

    def set_request_model(self, model_name: str):
        previous = self._request_model
        self._request_model = model_name
        return previous

    def reset_request_model(self, token) -> None:
        self._request_model = token

    def generate(self, prompt=None, *, messages=None, **kwargs):
        self.calls.append(
            {
                "prompt": prompt,
                "messages": messages,
                "model": self._request_model or self.model_name,
                "provider": self.provider,
            }
        )
        return "stub reply"


def _make_assistant(monkeypatch, settings_obj, llm):
    class _LLMFactory:
        def __new__(cls, *args, **kwargs):
            return llm

    monkeypatch.setattr(assistant_module, "LanguageModel", _LLMFactory)
    monkeypatch.setattr(ModelRouter, "_fetch_ollama_models", lambda self: None)

    return assistant_module.Assistant(
        settings_obj=settings_obj,
        transcripts_dir="transcripts",
        user_id="default",
    )


def test_stateless_and_context_dependent_turns_share_same_provider_client(monkeypatch):
    """A follow-up turn must reuse the identical configured LLM client/provider."""
    settings = _SettingsStub()
    llm = _ContextCapturingLLM()
    a = _make_assistant(monkeypatch, settings, llm)

    # Stateless turn that is not an IntentRouter shortcut (real LLM call).
    asyncio.run(a.generate_reply("Tell me something interesting."))
    assert len(llm.calls) == 1
    assert llm.calls[0]["provider"] == "openai"

    # Seed prior context so the next turn is genuinely context-dependent.
    a._history = [
        ConversationTurn(speaker="user", text="Tell me something interesting."),
        ConversationTurn(speaker="assistant", text="stub reply"),
    ]

    asyncio.run(a.generate_reply("What did I just ask you?"))

    assert len(llm.calls) == 2, "context-dependent turn did not reach the LLM"
    # Same LanguageModel instance/provider handled both turns: no split
    # provider/runtime path exists for context-dependent turns.
    assert a._llm is llm
    assert llm.calls[1]["provider"] == llm.calls[0]["provider"] == "openai"
    assert llm.calls[1]["model"] == llm.calls[0]["model"]


def test_context_dependent_turn_includes_prior_user_turn(monkeypatch):
    """The prior user turn must actually be present in the LLM messages."""
    settings = _SettingsStub()
    llm = _ContextCapturingLLM()
    a = _make_assistant(monkeypatch, settings, llm)

    a._history = [
        ConversationTurn(speaker="user", text="My favorite color is teal."),
        ConversationTurn(speaker="assistant", text="Got it, teal is a great color."),
    ]

    asyncio.run(a.generate_reply("What did I just ask you?"))

    assert llm.calls, "LLM was never called for the context-dependent turn"
    messages = llm.calls[-1]["messages"]
    assert messages, "no messages were sent to the LLM for the context-dependent turn"
    contents = [str(m.get("content", "")) for m in messages]
    assert any("My favorite color is teal." in c for c in contents), (
        "prior user turn was not included in the context-dependent turn's messages: "
        f"{contents!r}"
    )
    assert any(
        str(m.get("role")) == "user" and "What did I just ask" in str(m.get("content", ""))
        for m in messages
    )


def test_openai_package_is_a_base_dependency():
    """``openai`` must not be ml-extra-only: the OpenAI/LM Studio provider is base.

    Root cause of TEST-005: ``openai`` previously lived only under the
    optional ``ml`` extra, so a base/dev install (and the packaged Electron
    runtime, see below) never installed it. IntentRouter shortcuts (hello,
    time) never call the LLM and so never exposed the gap; any turn that
    actually reached the real LLM call deterministically raised
    "OpenAI backend requires the `openai` package."
    """
    pyproject = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    base_deps = pyproject["project"]["dependencies"]
    assert any(dep.split(">=")[0].split("==")[0].strip() == "openai" for dep in base_deps), (
        "openai must be declared in [project.dependencies], not only in an optional extra"
    )


def test_openai_package_is_in_packaged_electron_runtime():
    """The packaged Electron minimal runtime must include the ``openai`` SDK.

    ``requirements-electron-runtime.txt`` is installed for every packaged
    profile (Voice, Core, Full); the OpenAI-compatible provider (real OpenAI,
    OpenRouter, and LM Studio) must work without requiring the Voice/Full
    ML profile.
    """
    lines = (_REPO_ROOT / "requirements-electron-runtime.txt").read_text(encoding="utf-8")
    assert any(
        line.strip().split("==")[0].split(">=")[0].strip() == "openai"
        for line in lines.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ), "openai must be listed in requirements-electron-runtime.txt"
