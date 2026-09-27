from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from types import SimpleNamespace

import rex.assistant as assistant_module
from rex.actions.dispatcher import ActionDispatcher
from rex.latency import LatencyTrace
from rex.model_router import ModelRouter


@dataclass
class _Routing:
    default: str = "bench-model"
    coding: str = ""
    reasoning: str = ""
    search: str = ""
    vision: str = ""
    fast: str = ""


@dataclass
class _Settings:
    llm_provider: str = "transformers"
    llm_model: str = "bench-model"
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
    user_id: str = "benchmark"
    active_profile: str = "default"
    model_routing: _Routing = field(default_factory=_Routing)


class _LLM:
    model_name = "bench-model"

    def generate(self, prompt=None, *, messages=None, config=None):
        time.sleep(0.001)
        return "benchmark reply"

    def stream(self, prompt=None, *, messages=None, config=None):
        time.sleep(0.001)
        yield "benchmark "
        yield "reply"


def _assistant(monkeypatch) -> assistant_module.Assistant:
    llm = _LLM()

    class _Factory:
        def __new__(cls, *args, **kwargs):
            return llm

    monkeypatch.setattr(assistant_module, "LanguageModel", _Factory)
    monkeypatch.setattr(ModelRouter, "_fetch_ollama_models", lambda self: None)
    assistant = assistant_module.Assistant(
        settings_obj=_Settings(), transcripts_dir="transcripts", user_id="benchmark"
    )
    assistant._router._available_ollama_models = {"bench-model"}
    assistant._tool_dispatcher = None
    assistant._ha_bridge = None
    assistant._shopping_list_handler = None
    assistant._music_handler = None
    assistant._device_state_handler = None
    assistant._skill_trainer = None
    assistant._skill_router = None
    return assistant


def test_generate_reply_emits_privacy_safe_chat_stage_timings(monkeypatch, caplog) -> None:
    assistant = _assistant(monkeypatch)
    with caplog.at_level(logging.INFO, logger="rex.assistant"):
        result = asyncio.run(assistant.generate_reply("Explain a benchmark fixture"))

    assert result == "benchmark reply"
    records = [
        record for record in caplog.records if getattr(record, "event", None) == "chat_latency"
    ]
    assert len(records) == 1
    record = records[0]
    assert record.routing_ms >= 0
    assert hasattr(record, "llm_ms")
    assert hasattr(record, "postprocess_ms")
    assert not hasattr(record, "tool_ms")
    assert record.llm_ms >= 0
    assert record.postprocess_ms >= 0
    assert record.total_ms >= record.llm_ms
    assert record.provider == "transformers"
    assert record.model == "bench-model"
    assert not hasattr(record, "transcript")
    assert not hasattr(record, "user_id")


def test_voice_mode_latency_uses_safe_voice_identifiers(monkeypatch, caplog) -> None:
    assistant = _assistant(monkeypatch)
    with caplog.at_level(logging.INFO, logger="rex.assistant"):
        asyncio.run(assistant.generate_reply("Benchmark voice request", voice_mode=True))

    record = next(
        record for record in caplog.records if getattr(record, "event", None) == "chat_latency"
    )
    assert record.channel == "voice"
    assert record.provider == "transformers"
    assert record.model == "bench-model"
    assert record.settings_id == "voice"
    assert not hasattr(record, "transcript")
    assert not hasattr(record, "user_id")


def test_stream_reply_emits_privacy_safe_first_token_timings(monkeypatch, caplog) -> None:
    assistant = _assistant(monkeypatch)

    async def _collect() -> list[str]:
        return [
            chunk
            async for chunk in assistant.stream_reply(
                "Explain a streaming benchmark fixture", active_user_id="benchmark"
            )
        ]

    with caplog.at_level(logging.INFO, logger="rex.assistant"):
        chunks = asyncio.run(_collect())

    assert "".join(chunks).strip() == "benchmark reply"
    records = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "chat_stream_latency"
    ]
    assert len(records) == 1
    record = records[0]
    assert record.routing_ms >= 0
    assert record.first_token_ms >= 0
    assert record.llm_ms >= 0
    assert record.completion_ms >= 0
    assert record.total_ms >= record.first_token_ms
    assert record.provider == "transformers"
    assert record.model == "bench-model"
    assert record.settings_id == "text_stream"
    assert not hasattr(record, "transcript")
    assert not hasattr(record, "user_id")


class _ToolDispatcher:
    def select_tools(self, transcript):
        return [SimpleNamespace(operation="read")]

    def execute_tools(self, selected, transcript, *, user_id, scope="user"):
        time.sleep(0.001)
        return []

    def format_tool_context(self, results):
        return "benchmark tool context"


class _ResultHandler:
    async def process(self, transcript, completion, **kwargs):
        return completion


def test_action_dispatcher_records_tool_and_llm_segments() -> None:
    dispatcher = ActionDispatcher(
        context_builder=SimpleNamespace(
            build=lambda *args, **kwargs: SimpleNamespace(
                messages=[{"role": "user", "content": "benchmark"}],
                prompt="benchmark",
            )
        ),
        llm=_LLM(),
        result_handler=_ResultHandler(),
        tool_dispatcher=_ToolDispatcher(),
    )
    trace = LatencyTrace(channel="chat", provider="transformers", model="bench-model")
    context = SimpleNamespace(
        messages=[{"role": "user", "content": "benchmark"}], prompt="benchmark"
    )

    asyncio.run(
        dispatcher.dispatch(
            SimpleNamespace(handled=False),
            context,
            "benchmark request",
            user_id="benchmark",
            latency_trace=trace,
        )
    )
    trace.finish()
    summary = trace.summary()
    assert "tool_ms" in summary
    assert "llm_ms" in summary
    assert summary["tool_ms"] >= 0
    assert summary["llm_ms"] >= 0


def test_action_dispatcher_builds_tool_context_for_effective_user() -> None:
    seen_users: list[str] = []
    seen_contexts: list[dict[str, str]] = []

    class CapturingResultHandler:
        async def process(self, transcript, completion, **kwargs):
            seen_contexts.append(kwargs["tool_context"])
            return completion

    def build_tool_context(user_id: str) -> dict[str, str]:
        seen_users.append(user_id)
        return {"location": "authorized-location"}

    dispatcher = ActionDispatcher(
        context_builder=SimpleNamespace(
            build=lambda *args, **kwargs: SimpleNamespace(
                messages=[{"role": "user", "content": "benchmark"}],
                prompt="benchmark",
            )
        ),
        llm=_LLM(),
        result_handler=CapturingResultHandler(),
        build_tool_context_fn=build_tool_context,
    )

    context = SimpleNamespace(
        messages=[{"role": "user", "content": "benchmark"}], prompt="benchmark"
    )
    asyncio.run(
        dispatcher.dispatch(
            SimpleNamespace(handled=False),
            context,
            "benchmark request",
            user_id="benchmark",
        )
    )

    assert seen_users == ["benchmark"]
    assert seen_contexts == [{"location": "authorized-location"}]
