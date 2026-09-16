#!/usr/bin/env python3
"""Deterministic privacy-safe Rex latency baseline harness."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import rex.assistant as assistant_module  # noqa: E402
from rex.actions.graph import ActionGraph, ActionNode  # noqa: E402
from rex.actions.graph_executor import ActionGraphExecutor  # noqa: E402
from rex.actions.lifecycle import lifecycle_from_legacy_status  # noqa: E402
from rex.capabilities.registry import Capability, CapabilityRegistry  # noqa: E402
from rex.capabilities.retrieval import CapabilityRetriever  # noqa: E402
from rex.model_router import ModelRouter, ProviderRouteCandidate  # noqa: E402
from rex.provider_reliability import ProviderFailureKind, ProviderReliability  # noqa: E402
from rex.rexbench import BenchmarkSample, build_report  # noqa: E402
from rex.runtime.speculation import SpeculativePrefetcher  # noqa: E402
from rex.runtime.warm import WarmComponentSpec, WarmRuntimeManager  # noqa: E402
from rex.tools.execution import ToolOperation  # noqa: E402
from rex.tools.protocol import ToolResult  # noqa: E402
from rex.voice_loop import VoiceLoop  # noqa: E402

REQUEST_CLASSES = (
    "typed_chat",
    "voice",
    "read_only_tool",
    "mutating_tool",
    "unavailable_capability",
)


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
    response_cache_ttl: int = 0
    model_routing: _Routing = field(default_factory=_Routing)


class _BenchLLM:
    def __init__(self, response: str = "benchmark reply") -> None:
        self.model_name = "bench-model"
        self.response = response

    def generate(self, prompt=None, *, messages=None, config=None):
        time.sleep(0.002)
        return self.response

    def stream(self, prompt=None, *, messages=None, config=None):
        time.sleep(0.002)
        yield self.response


class _BenchToolDispatcher:
    def __init__(self, operation: str) -> None:
        self.operation = operation

    def select_tools(self, transcript):
        return [SimpleNamespace(operation=self.operation)]

    def execute_tools(self, selected, transcript, *, user_id):
        time.sleep(0.002)
        return []

    def format_tool_context(self, results):
        return "benchmark result"


class _RecordCapture(logging.Handler):
    def __init__(self, event: str) -> None:
        super().__init__()
        self.event = event
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        if getattr(record, "event", None) == self.event:
            self.records.append(record)


def _make_assistant(request_class: str):
    response = (
        "Capability unavailable."
        if request_class == "unavailable_capability"
        else "benchmark reply"
    )
    llm = _BenchLLM(response=response)

    class _Factory:
        def __new__(cls, *args, **kwargs):
            return llm

    started = time.perf_counter_ns()
    with (
        patch.object(assistant_module, "LanguageModel", _Factory),
        patch.object(ModelRouter, "_fetch_ollama_models", lambda self: None),
    ):
        assistant = assistant_module.Assistant(
            settings_obj=_Settings(), transcripts_dir="transcripts", user_id="benchmark"
        )
    startup_ms = (time.perf_counter_ns() - started) / 1_000_000
    assistant._router._available_ollama_models = {"bench-model"}

    tool_dispatcher = None
    if request_class == "read_only_tool":
        tool_dispatcher = _BenchToolDispatcher("read")
    elif request_class == "mutating_tool":
        tool_dispatcher = _BenchToolDispatcher("mutate")
    assistant._tool_dispatcher = tool_dispatcher
    assistant._ha_bridge = None
    assistant._shopping_list_handler = None
    assistant._music_handler = None
    assistant._device_state_handler = None
    assistant._skill_trainer = None
    assistant._skill_router = None
    assistant._action_dispatcher._tool_dispatcher = tool_dispatcher
    assistant._action_dispatcher._ha_bridge = None
    assistant._action_dispatcher._shopping_list_handler = None
    assistant._action_dispatcher._music_handler = None
    assistant._action_dispatcher._device_state_handler = None
    assistant._action_dispatcher._skill_trainer = None
    assistant._action_dispatcher._skill_router = None
    return assistant, startup_ms


def _chat_sample(
    request_class: str, *, warm_state: str, assistant=None
) -> tuple[BenchmarkSample, object]:
    startup_ms = 0.0
    if assistant is None:
        assistant, startup_ms = _make_assistant(request_class)

    logger = logging.getLogger("rex.assistant")
    capture = _RecordCapture("chat_latency")
    previous_level, previous_propagate = logger.level, logger.propagate
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.addHandler(capture)
    try:
        asyncio.run(
            assistant.generate_reply(f"benchmark {request_class}", active_user_id="benchmark")
        )
    finally:
        logger.removeHandler(capture)
        logger.setLevel(previous_level)
        logger.propagate = previous_propagate
    if not capture.records:
        raise RuntimeError("Chat latency event was not emitted")
    record = capture.records[-1]
    stages = {
        key[:-3]: float(value)
        for key, value in vars(record).items()
        if key.endswith("_ms") and isinstance(value, (int, float))
    }
    if warm_state == "cold":
        stages["startup"] = startup_ms
        stages["total"] = stages.get("total", 0.0) + startup_ms
    return (
        BenchmarkSample(
            request_class=request_class,
            warm_state=warm_state,
            evidence_class="deterministic_mock",
            stages_ms=stages,
        ),
        assistant,
    )


def _stream_chat_sample(*, warm_state: str, assistant=None) -> tuple[BenchmarkSample, object]:
    startup_ms = 0.0
    if assistant is None:
        assistant, startup_ms = _make_assistant("typed_chat")

    logger = logging.getLogger("rex.assistant")
    capture = _RecordCapture("chat_stream_latency")
    previous_level, previous_propagate = logger.level, logger.propagate
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.addHandler(capture)

    async def _consume() -> None:
        async for _chunk in assistant.stream_reply(
            "benchmark typed_chat", active_user_id="benchmark"
        ):
            pass

    try:
        asyncio.run(_consume())
    finally:
        logger.removeHandler(capture)
        logger.setLevel(previous_level)
        logger.propagate = previous_propagate
    if not capture.records:
        raise RuntimeError("Streaming chat latency event was not emitted")
    record = capture.records[-1]
    stages = {
        key[:-3]: float(value)
        for key, value in vars(record).items()
        if key.endswith("_ms") and isinstance(value, (int, float))
    }
    if warm_state == "cold":
        stages["startup"] = startup_ms
        stages["total"] = stages.get("total", 0.0) + startup_ms
    return (
        BenchmarkSample(
            request_class="typed_chat",
            warm_state=warm_state,
            evidence_class="deterministic_mock",
            stages_ms=stages,
        ),
        assistant,
    )


class _OnceListener:
    async def listen(self, _source):
        yield b"wake"

    def mark_listening_started(self, *, reason: str = "benchmark") -> None:
        return None

    def reset(self, *, reason: str = "benchmark") -> None:
        return None


def _voice_sample(*, warm_state: str) -> BenchmarkSample:
    async def detection_source():
        return b"wake"

    async def record_phrase():
        time.sleep(0.001)
        return b"audio"

    async def transcribe(_audio):
        await asyncio.sleep(0.001)
        return "benchmark voice request"

    async def speak(_text: str) -> None:
        await asyncio.sleep(0.001)

    assistant = MagicMock()
    assistant.generate_reply = AsyncMock(return_value="benchmark reply")
    del assistant.stream_reply

    started = time.perf_counter_ns()
    loop = VoiceLoop(
        assistant,
        wake_listener=_OnceListener(),
        detection_source=detection_source,
        record_phrase=record_phrase,
        transcribe=transcribe,
        speak=speak,
        post_interaction_cooldown=0,
        post_wake_preroll_seconds=0,
    )
    startup_ms = (time.perf_counter_ns() - started) / 1_000_000

    logger = logging.getLogger("rex.voice_loop")
    capture = logging.Handler()
    records: list[logging.LogRecord] = []
    capture.emit = lambda record: records.append(record)  # type: ignore[method-assign]
    previous_level, previous_propagate = logger.level, logger.propagate
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.addHandler(capture)
    try:
        asyncio.run(loop.run(max_interactions=1))
    finally:
        logger.removeHandler(capture)
        logger.setLevel(previous_level)
        logger.propagate = previous_propagate

    by_event = {
        record.event: record
        for record in records
        if getattr(record, "event", None)
        in {
            "wake_detected",
            "capture_started",
            "capture_ended",
            "stt_completed",
            "llm_completed",
            "playback_completed",
        }
    }
    required = {
        "wake_detected",
        "capture_started",
        "capture_ended",
        "stt_completed",
        "llm_completed",
        "playback_completed",
    }
    if set(by_event) != required:
        raise RuntimeError(f"Voice latency events missing: {sorted(required - set(by_event))}")
    wake = by_event["wake_detected"]
    playback = by_event["playback_completed"]
    total_ms = ((playback.start_ns - wake.start_ns) / 1_000_000) + float(playback.duration_ms)
    stages = {
        "capture": float(by_event["capture_ended"].duration_ms),
        "stt": float(by_event["stt_completed"].duration_ms),
        "llm": float(by_event["llm_completed"].duration_ms),
        "first_audio": (playback.start_ns - wake.start_ns) / 1_000_000,
        "completion": total_ms,
        "total": total_ms,
    }
    if warm_state == "cold":
        stages["startup"] = startup_ms
        stages["total"] += startup_ms
    return BenchmarkSample(
        request_class="voice",
        warm_state=warm_state,
        evidence_class="deterministic_mock",
        stages_ms=stages,
    )


def run_baseline(iterations: int) -> dict:
    if iterations < 1:
        raise ValueError("iterations must be at least 1")
    samples: list[BenchmarkSample] = []
    for request_class in REQUEST_CLASSES:
        if request_class == "voice":
            for _ in range(iterations):
                samples.append(_voice_sample(warm_state="cold"))
                samples.append(_voice_sample(warm_state="warm"))
            continue
        if request_class == "typed_chat":
            warm_assistant, _ = _make_assistant(request_class)
            for _ in range(iterations):
                cold, _ = _stream_chat_sample(warm_state="cold")
                warm, warm_assistant = _stream_chat_sample(
                    warm_state="warm", assistant=warm_assistant
                )
                samples.extend((cold, warm))
            continue
        warm_assistant, _ = _make_assistant(request_class)
        for _ in range(iterations):
            cold, _ = _chat_sample(request_class, warm_state="cold")
            warm, warm_assistant = _chat_sample(
                request_class, warm_state="warm", assistant=warm_assistant
            )
            samples.extend((cold, warm))
    return build_report(samples, profile="baseline")


class _BrokenCapabilitySemanticScorer:
    def score(self, query: str, capability: Capability) -> float:
        del query, capability
        raise RuntimeError("deterministic semantic fallback")


def _capability_benchmark_registry() -> CapabilityRegistry:
    registry = CapabilityRegistry()
    for capability in (
        Capability(
            name="web_search",
            description="Search the web for current information",
            triggers=["search", "lookup", "web"],
        ),
        Capability(
            name="weather_now",
            description="Get current weather conditions",
            triggers=["weather", "forecast", "temperature"],
        ),
        Capability(
            name="calendar_create",
            description="Create a calendar event",
            triggers=["calendar", "schedule", "meeting"],
            operation="mutation",
            requires_identity=True,
        ),
        Capability(
            name="send_email",
            description="Send an email message",
            triggers=["email", "mail", "message"],
            operation="mutation",
            requires_identity=True,
            required_permissions=("email_send",),
        ),
    ):
        registry.register(capability)
    return registry


def run_capability_retrieval(iterations: int) -> dict:
    if iterations < 1:
        raise ValueError("iterations must be at least 1")
    registry = _capability_benchmark_registry()
    hybrid = CapabilityRetriever(registry)
    fallback = CapabilityRetriever(registry, semantic_scorer=_BrokenCapabilitySemanticScorer())
    samples: list[BenchmarkSample] = []
    scenarios = (
        ("hybrid", hybrid, "research this online", "web_search"),
        ("lexical_fallback", fallback, "weather forecast", "weather_now"),
    )
    for request_class, retriever, query, expected in scenarios:
        for _ in range(iterations):
            started = time.perf_counter_ns()
            matches = retriever.retrieve(
                query, user_id="benchmark", granted_permissions=frozenset()
            )
            elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
            if not matches or matches[0].capability.id != expected:
                raise RuntimeError(
                    f"Capability retrieval benchmark failed correctness check for {request_class}"
                )
            samples.append(
                BenchmarkSample(
                    request_class=request_class,
                    warm_state="warm",
                    evidence_class="deterministic_local",
                    stages_ms={"retrieval": elapsed_ms, "total": elapsed_ms},
                )
            )
    return build_report(samples, profile="capability-retrieval")


class _ParallelBenchDispatcher:
    def __init__(self, statuses: dict[str, str]) -> None:
        self._statuses = statuses
        self._lock = threading.Lock()
        self._active = 0
        self.max_active = 0

    def dispatch(self, name: str, args: dict, context: dict | None = None) -> ToolResult:
        del name, args
        action_id = str((context or {})["request_id"])
        plan_id = str((context or {})["plan_id"])
        with self._lock:
            self._active += 1
            self.max_active = max(self.max_active, self._active)
        try:
            time.sleep(0.005)
            status = self._statuses.get(action_id, "completed")
            lifecycle = lifecycle_from_legacy_status(status, action_id=action_id, plan_id=plan_id)
            return ToolResult(
                success=lifecycle.success,
                status=status,
                request_id=action_id,
                lifecycle=lifecycle,
            )
        finally:
            with self._lock:
                self._active -= 1


def run_parallel_actions(iterations: int) -> dict:
    if iterations < 1:
        raise ValueError("iterations must be at least 1")
    samples: list[BenchmarkSample] = []
    for request_class in ("parallel_reads", "serialized_mutations"):
        for _ in range(iterations):
            if request_class == "parallel_reads":
                operations = {"r1": ToolOperation.READ, "r2": ToolOperation.READ}
                statuses: dict[str, str] = {}
                nodes = (
                    ActionNode("r1", "r1", conflict_keys=("resource:r1",)),
                    ActionNode("r2", "r2", conflict_keys=("resource:r2",)),
                )
                expected_active = 2
            else:
                operations = {"m1": ToolOperation.MUTATION, "m2": ToolOperation.MUTATION}
                statuses = {"m1": "verified", "m2": "verified"}
                nodes = (
                    ActionNode(
                        "m1",
                        "m1",
                        operation=ToolOperation.MUTATION,
                        verification_required=True,
                        postcondition="m1 verified",
                    ),
                    ActionNode(
                        "m2",
                        "m2",
                        operation=ToolOperation.MUTATION,
                        verification_required=True,
                        postcondition="m2 verified",
                    ),
                )
                expected_active = 1
            dispatcher = _ParallelBenchDispatcher(statuses)
            executor = ActionGraphExecutor(
                dispatcher,
                operation_resolver=lambda name, ops=operations: ops[name],
                max_parallel_reads=2,
            )
            started = time.perf_counter_ns()
            result = executor.execute(ActionGraph(plan_id="rexbench", nodes=nodes))
            elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
            if not result.success or dispatcher.max_active != expected_active:
                raise RuntimeError(f"parallel-actions correctness failed for {request_class}")
            samples.append(
                BenchmarkSample(
                    request_class=request_class,
                    warm_state="warm",
                    evidence_class="deterministic_local",
                    stages_ms={"execution": elapsed_ms, "total": elapsed_ms},
                )
            )
    return build_report(samples, profile="parallel-actions")


def _speculative_prefetch_registry() -> CapabilityRegistry:
    registry = CapabilityRegistry()
    for capability in (
        Capability(
            name="speculative_read",
            description="Harmless, currently permitted prefetchable read",
            operation="read",
            risk="safe",
            health="healthy",
        ),
        Capability(
            name="speculative_mutation",
            description="Mutation must never be speculatively dispatched",
            operation="mutation",
            risk="safe",
            health="healthy",
        ),
        Capability(
            name="speculative_sensitive_read",
            description="Sensitive read must never be speculatively dispatched",
            operation="read",
            risk="sensitive",
            health="healthy",
        ),
    ):
        registry.register(capability)
    return registry


class _SpeculativePrefetchBenchDispatcher:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def dispatch(self, name: str, args: dict, context: dict | None = None) -> ToolResult:
        del args, context
        self.calls.append(name)
        time.sleep(0.002)
        return ToolResult(success=True, output=f"{name}-output")


def run_speculative_prefetch(iterations: int) -> dict:
    if iterations < 1:
        raise ValueError("iterations must be at least 1")
    registry = _speculative_prefetch_registry()
    samples: list[BenchmarkSample] = []
    for _ in range(iterations):
        dispatcher = _SpeculativePrefetchBenchDispatcher()
        prefetcher = SpeculativePrefetcher(registry, dispatcher)

        started = time.perf_counter_ns()
        outcome = prefetcher.prefetch(
            ("speculative_read", "speculative_mutation", "speculative_sensitive_read"),
            user_id="benchmark",
            scope="user",
            granted_permissions=frozenset({"admin"}),
        )
        elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
        if dispatcher.calls != ["speculative_read"]:
            raise RuntimeError(
                "speculative-prefetch correctness failed: an ineligible capability was dispatched"
            )
        if "speculative_read" not in outcome.results:
            raise RuntimeError(
                "speculative-prefetch correctness failed: eligible read was not prefetched"
            )
        samples.append(
            BenchmarkSample(
                request_class="eligible_read_hit",
                warm_state="warm",
                evidence_class="deterministic_local",
                stages_ms={"prefetch": elapsed_ms, "total": elapsed_ms},
            )
        )

        consume_started = time.perf_counter_ns()
        consumed = prefetcher.consume(
            outcome.get("speculative_read"),
            user_id="benchmark",
            scope="user",
            granted_permissions=frozenset({"admin"}),
        )
        consume_elapsed_ms = (time.perf_counter_ns() - consume_started) / 1_000_000
        if consumed is None:
            raise RuntimeError(
                "speculative-prefetch correctness failed: fresh result was not consumable"
            )
        samples.append(
            BenchmarkSample(
                request_class="consume_revalidation",
                warm_state="warm",
                evidence_class="deterministic_local",
                stages_ms={"consume": consume_elapsed_ms, "total": consume_elapsed_ms},
            )
        )
    return build_report(samples, profile="speculative-prefetch")


def run_warm_runtime(iterations: int) -> dict:
    if iterations < 1:
        raise ValueError("iterations must be at least 1")

    def _synthetic_loader() -> object:
        time.sleep(0.002)
        return object()

    samples: list[BenchmarkSample] = []
    costs = {"executive": 256.0, "stt": 128.0, "tts": 256.0, "index": 64.0}

    for component, cost in costs.items():
        for _ in range(iterations):
            cold = WarmRuntimeManager(max_cost_mb=1024.0)
            cold.register(
                WarmComponentSpec(
                    name=component,
                    loader=_synthetic_loader,
                    estimated_cost_mb=cost,
                )
            )
            started = time.perf_counter_ns()
            cold.get(component)
            elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
            samples.append(
                BenchmarkSample(
                    request_class=component,
                    warm_state="cold",
                    evidence_class="deterministic_local",
                    stages_ms={"acquire": elapsed_ms, "total": elapsed_ms},
                )
            )

        warm = WarmRuntimeManager(max_cost_mb=1024.0)
        warm.register(
            WarmComponentSpec(
                name=component,
                loader=_synthetic_loader,
                estimated_cost_mb=cost,
            )
        )
        warm.get(component)
        for _ in range(iterations):
            started = time.perf_counter_ns()
            warm.get(component)
            elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
            samples.append(
                BenchmarkSample(
                    request_class=component,
                    warm_state="warm",
                    evidence_class="deterministic_local",
                    stages_ms={"acquire": elapsed_ms, "total": elapsed_ms},
                )
            )

    return build_report(samples, profile="warm-runtime")


def run_model_routing(iterations: int) -> dict:
    if iterations < 1:
        raise ValueError("iterations must be at least 1")
    routing = SimpleNamespace(
        default="fast-local",
        fast="fast-local",
        coding="deep-local",
        reasoning="deep-local",
        search="deep-local",
        vision="deep-local",
    )
    router = ModelRouter()
    samples: list[BenchmarkSample] = []
    scenarios = (
        ("simple_command", "Hello", {}, {"fast-local", "deep-local"}, "fast", "fast-local"),
        (
            "ambiguous_tool_choice",
            "Use whichever tool makes sense to handle that.",
            {},
            {"fast-local", "deep-local"},
            "deep",
            "deep-local",
        ),
        (
            "complex_reasoning",
            "Analyze the tradeoffs and plan a complex migration strategy.",
            {},
            {"fast-local", "deep-local"},
            "deep",
            "deep-local",
        ),
        (
            "provider_outage",
            "Analyze the tradeoffs in this architecture.",
            {"deep_provider_available": False},
            {"fast-local", "deep-local"},
            "fast",
            "fast-local",
        ),
        (
            "unavailable_local_model",
            "Analyze a complex migration plan.",
            {},
            {"fast-local"},
            "fast",
            "fast-local",
        ),
    )
    for request_class, message, kwargs, available, expected_tier, expected_model in scenarios:
        for _ in range(iterations):
            router._available_ollama_models = set(available)

            started = time.perf_counter_ns()
            decision = router.decide(
                message,
                routing_config=routing,
                current_model="fast-local",
                **kwargs,
            )
            elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
            if decision.tier != expected_tier or decision.model != expected_model:
                raise RuntimeError(
                    f"model-routing correctness failed for {request_class}: "
                    f"{decision.tier}/{decision.model}"
                )
            if decision.escalation_count > 1:
                raise RuntimeError(f"model-routing escalation exceeded bound for {request_class}")
            samples.append(
                BenchmarkSample(
                    request_class=request_class,
                    warm_state="warm",
                    evidence_class="deterministic_local",
                    stages_ms={"routing": elapsed_ms, "total": elapsed_ms},
                )
            )
    return build_report(samples, profile="model-routing")


ROUTING_EVAL_CORPUS = REPO_ROOT / "tests" / "fixtures" / "rexbench" / "routing-eval.json"


def _timing_percentiles(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("routing eval requires timing samples")

    def percentile(q: float) -> float:
        if len(ordered) == 1:
            return ordered[0]
        position = (len(ordered) - 1) * q
        lower = int(position)
        upper = min(lower + 1, len(ordered) - 1)
        fraction = position - lower
        return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction

    return {"p50": round(percentile(0.50), 3), "p95": round(percentile(0.95), 3)}


def _routing_eval_reliability(raw_case: dict[str, object]) -> ProviderReliability:
    reliability = ProviderReliability(cooldown_seconds=60)
    failures = raw_case.get("failures", [])
    if not isinstance(failures, list):
        raise ValueError("Routing evaluation failures must be a list")
    for failure in failures:
        if not isinstance(failure, dict):
            raise ValueError("Routing evaluation failure must be an object")
        reliability.record_failure(
            str(failure.get("provider") or ""),
            ProviderFailureKind(str(failure.get("kind") or "unknown")),
        )
    return reliability


def _routing_eval_candidates(raw_case: dict[str, object]) -> tuple[ProviderRouteCandidate, ...]:
    raw_candidates = raw_case.get("candidates", [])
    if not isinstance(raw_candidates, list):
        raise ValueError("Routing evaluation candidates must be a list")
    return tuple(
        ProviderRouteCandidate(str(item.get("provider") or ""), str(item.get("model") or ""))
        for item in raw_candidates
        if isinstance(item, dict)
    )


def _routing_eval_case(
    raw_case: dict[str, object], iterations: int
) -> tuple[bool, dict[str, object]]:
    timings: list[float] = []
    case_passed = True
    for _ in range(iterations):
        router = ModelRouter(provider_reliability=_routing_eval_reliability(raw_case))
        started = time.perf_counter_ns()
        selection = router.select_provider(_routing_eval_candidates(raw_case))
        timings.append((time.perf_counter_ns() - started) / 1_000_000)
        case_passed = case_passed and (
            selection.provider == str(raw_case.get("expected_provider") or "")
            and selection.model == str(raw_case.get("expected_model") or "")
            and selection.fallback_reason == raw_case.get("expected_fallback_reason")
        )
    return case_passed, {
        "passed": case_passed,
        "evidence_class": "deterministic_local",
        "iterations": iterations,
        "routing_ms": _timing_percentiles(timings),
    }


def run_routing_eval(
    iterations: int,
    *,
    corpus_path: Path = ROUTING_EVAL_CORPUS,
    live_provider_eval: bool = False,
) -> dict[str, object]:
    """Evaluate deterministic provider selection/fallback against a checked-in corpus."""
    if iterations < 1:
        raise ValueError("iterations must be at least 1")
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    if corpus.get("schema_version") != 1 or corpus.get("evidence_class") != "deterministic_local":
        raise ValueError("Unsupported routing evaluation corpus")
    cases = corpus.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("Routing evaluation corpus contains no cases")

    results: dict[str, dict[str, object]] = {}
    passed_cases = 0
    for raw_case in cases:
        if not isinstance(raw_case, dict):
            raise ValueError("Routing evaluation case must be an object")
        case_id = str(raw_case.get("id") or "")
        if not case_id or case_id in results:
            raise ValueError("Routing evaluation case IDs must be unique and non-empty")
        case_passed, result = _routing_eval_case(raw_case, iterations)
        results[case_id] = result
        passed_cases += int(case_passed)

    report: dict[str, object] = {
        "schema_version": 1,
        "profile": "routing-eval",
        "privacy": "bounded_provider_health_and_timing_metadata_only",
        "evidence_class": "deterministic_local",
        "live_provider_eval": bool(live_provider_eval),
        "corpus_version": int(corpus["schema_version"]),
        "total_cases": len(cases),
        "passed_cases": passed_cases,
        "selection_accuracy": round(passed_cases / len(cases), 3),
        "results": results,
    }
    if live_provider_eval:
        report["live_provider"] = _live_provider_eval()
    return report


def _live_provider_eval() -> dict[str, object]:
    """Run one explicitly requested live provider probe without retaining content."""
    from rex.config import load_config  # noqa: PLC0415
    from rex.llm_client import LanguageModel  # noqa: PLC0415
    from rex.provider_reliability import classify_provider_failure  # noqa: PLC0415

    config = load_config()
    provider = str(getattr(config, "llm_provider", "unknown") or "unknown")
    model = LanguageModel(config)
    started = time.perf_counter_ns()
    try:
        model.generate("Reply with OK.")
    except Exception as exc:
        elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
        return {
            "evidence_class": "live_provider",
            "provider": provider,
            "model": model.active_model_name(),
            "success": False,
            "failure_kind": classify_provider_failure(exc).value,
            "latency_ms": round(elapsed_ms, 3),
        }
    elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
    return {
        "evidence_class": "live_provider",
        "provider": provider,
        "model": model.active_model_name(),
        "success": True,
        "failure_kind": None,
        "latency_ms": round(elapsed_ms, 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile",
        choices=(
            "baseline",
            "capability-retrieval",
            "parallel-actions",
            "speculative-prefetch",
            "warm-runtime",
            "model-routing",
            "routing-eval",
        ),
        default="baseline",
    )
    parser.add_argument("--iterations", type=int, default=8)
    parser.add_argument(
        "--live-provider-eval",
        action="store_true",
        help="Opt in to one labeled live provider probe; never enabled by default or CI.",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    if args.profile == "capability-retrieval":
        report = run_capability_retrieval(args.iterations)
    elif args.profile == "parallel-actions":
        report = run_parallel_actions(args.iterations)
    elif args.profile == "speculative-prefetch":
        report = run_speculative_prefetch(args.iterations)
    elif args.profile == "warm-runtime":
        report = run_warm_runtime(args.iterations)
    elif args.profile == "model-routing":
        report = run_model_routing(args.iterations)
    elif args.profile == "routing-eval":
        report = run_routing_eval(args.iterations, live_provider_eval=args.live_provider_eval)
    else:
        report = run_baseline(args.iterations)
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
        print(f"RexBench {args.profile} written: {args.output}")
    else:
        print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
