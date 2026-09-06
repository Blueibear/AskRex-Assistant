"""US-111 provider reliability and deterministic fallback tests."""

from types import SimpleNamespace

from rex.model_router import ModelRouter, ProviderRouteCandidate
from rex.provider_reliability import ProviderFailureKind, ProviderReliability


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _routing():
    return SimpleNamespace(
        default="fast-local",
        fast="fast-local",
        coding="deep-local",
        reasoning="deep-local",
        search="deep-local",
        vision="deep-local",
    )


def test_rate_limit_enters_bounded_cooldown_then_recovers():
    clock = _Clock()
    reliability = ProviderReliability(clock=clock, cooldown_seconds=60)

    reliability.record_failure("openai", ProviderFailureKind.RATE_LIMIT, latency_ms=250)
    status = reliability.status("openai")

    assert not status.available
    assert status.state == "cooldown"
    assert status.reason == "rate_limit"
    assert status.cooldown_remaining_s == 60
    assert status.rate_limits == 1

    clock.advance(61)
    recovered = reliability.status("openai")
    assert recovered.available
    assert recovered.state == "degraded"
    assert recovered.cooldown_remaining_s == 0


def test_success_resets_failure_streak_and_clamps_latency():
    reliability = ProviderReliability(max_latency_ms=120_000)
    reliability.record_failure("ollama", ProviderFailureKind.TRANSIENT, latency_ms=-10)
    reliability.record_success("ollama", latency_ms=999_999)

    status = reliability.status("ollama")
    assert status.available
    assert status.consecutive_failures == 0
    assert status.successes == 1
    assert status.failures == 1
    assert status.latency_ms == 120_000


def test_diagnostics_are_bounded_and_content_free():
    reliability = ProviderReliability()
    reliability.record_failure("openai", ProviderFailureKind.AUTH, latency_ms=12.3456)

    diagnostics = reliability.diagnostics()
    assert diagnostics == [
        {
            "provider": "openai",
            "state": "cooldown",
            "available": False,
            "reason": "auth",
            "latency_ms": 12.346,
            "attempts": 1,
            "successes": 0,
            "failures": 1,
            "rate_limits": 0,
            "consecutive_failures": 1,
            "cooldown_remaining_s": 3600,
        }
    ]
    rendered = repr(diagnostics)
    assert "prompt" not in rendered.lower()
    assert "response" not in rendered.lower()
    assert "credential" not in rendered.lower()


def test_cooldown_diagnostics_ignore_floating_point_deadline_noise():
    clock = _Clock()
    clock.now = 14179.124999999998
    reliability = ProviderReliability(clock=clock)

    reliability.record_failure("openai", ProviderFailureKind.AUTH)

    assert reliability.status("openai").cooldown_remaining_s == 3600


def test_router_falls_back_to_next_configured_healthy_provider():
    clock = _Clock()
    reliability = ProviderReliability(clock=clock, cooldown_seconds=90)
    router = ModelRouter(provider_reliability=reliability)
    reliability.record_failure("openai", ProviderFailureKind.RATE_LIMIT)

    selection = router.select_provider(
        (
            ProviderRouteCandidate("openai", "gpt-5.5"),
            ProviderRouteCandidate("ollama", "qwen3:8b"),
        )
    )

    assert selection.provider == "ollama"
    assert selection.model == "qwen3:8b"
    assert selection.fallback_reason == "provider_cooldown"
    assert selection.evidence == ("provider_openai_cooldown", "provider_fallback_selected")


def test_router_provider_fallback_order_is_deterministic():
    reliability = ProviderReliability()
    router = ModelRouter(provider_reliability=reliability)

    selection = router.select_provider(
        (
            ProviderRouteCandidate("ollama", "fast-local"),
            ProviderRouteCandidate("openai", "gpt-5.5"),
            ProviderRouteCandidate("anthropic", "claude-sonnet"),
        )
    )

    assert selection.provider == "ollama"
    assert selection.model == "fast-local"
    assert selection.fallback_reason is None
    assert selection.evidence == ("provider_primary_selected",)


def test_decide_respects_current_provider_cooldown_for_deep_route():
    reliability = ProviderReliability(cooldown_seconds=60)
    router = ModelRouter(provider_reliability=reliability)
    router._available_ollama_models = {"fast-local", "deep-local"}
    reliability.record_failure("openai", ProviderFailureKind.RATE_LIMIT)

    decision = router.decide(
        "Analyze the tradeoffs in this architecture.",
        routing_config=_routing(),
        current_model="fast-local",
        provider="openai",
    )

    assert decision.tier == "fast"
    assert decision.model == "fast-local"
    assert decision.fallback_reason == "deep_provider_cooldown"
    assert "provider_openai_cooldown" in decision.evidence


def test_legacy_cloud_limit_hook_updates_provider_reliability():
    reliability = ProviderReliability(cooldown_seconds=30)
    router = ModelRouter(provider_reliability=reliability, cooldown_seconds=30)

    router.cloud_limit_hit(429, provider="openrouter")

    status = reliability.status("openrouter")
    assert not status.available
    assert status.reason == "rate_limit"
    assert status.rate_limits == 1


def _assistant_with_llm(llm):
    from rex.assistant import Assistant

    assistant = Assistant.__new__(Assistant)
    assistant._settings = SimpleNamespace(
        llm_provider="openai",
        llm=SimpleNamespace(llm_provider="openai", model_name="gpt-test"),
        llm_model="gpt-test",
    )
    assistant._llm = llm
    assistant._router = ModelRouter(provider_reliability=ProviderReliability(cooldown_seconds=45))
    return assistant


def test_assistant_records_provider_success_at_generation_boundary():
    class _LLM:
        model_name = "gpt-test"

        def generate(self, *, messages):
            return "ok"

    assistant = _assistant_with_llm(_LLM())

    assert assistant._generate_model_reply("prompt", [{"role": "user", "content": "hello"}]) == "ok"
    status = assistant._router.provider_reliability.status("openai")
    assert status.attempts == 1
    assert status.successes == 1
    assert status.failures == 0
    assert status.latency_ms is not None


def test_assistant_records_rate_limit_without_storing_exception_text():
    class _RateLimitedError(RuntimeError):
        status_code = 429

    class _LLM:
        model_name = "gpt-test"

        def generate(self, *, messages):
            raise _RateLimitedError("PRIVATE-PROVIDER-ERROR-TEXT")

    assistant = _assistant_with_llm(_LLM())

    import pytest

    with pytest.raises(_RateLimitedError):
        assistant._generate_model_reply("prompt", [{"role": "user", "content": "hello"}])
    status = assistant._router.provider_reliability.status("openai")
    assert not status.available
    assert status.reason == "rate_limit"
    assert status.rate_limits == 1
    assert "PRIVATE-PROVIDER-ERROR-TEXT" not in repr(assistant._router.provider_diagnostics())


def test_legacy_generate_signature_typeerror_is_not_counted_as_provider_failure():
    class _LLM:
        model_name = "gpt-test"

        def generate(self, prompt=None, **kwargs):
            if "messages" in kwargs:
                raise TypeError("legacy signature")
            return "legacy ok"

    assistant = _assistant_with_llm(_LLM())
    assert (
        assistant._generate_model_reply("prompt", [{"role": "user", "content": "hello"}])
        == "legacy ok"
    )
    status = assistant._router.provider_reliability.status("openai")
    assert status.attempts == 1
    assert status.successes == 1
    assert status.failures == 0


def test_assistant_records_stream_failure_when_iteration_raises():
    class _StreamError(ConnectionError):
        pass

    class _LLM:
        model_name = "gpt-test"

        def stream(self, *, messages):
            yield "first"
            raise _StreamError("PRIVATE-STREAM-ERROR")

    assistant = _assistant_with_llm(_LLM())
    stream = assistant._stream_model_reply("prompt", [{"role": "user", "content": "hello"}])

    import pytest

    assert next(iter(stream)) == "first"
    with pytest.raises(_StreamError):
        next(stream)
    status = assistant._router.provider_reliability.status("openai")
    assert not status.available
    assert status.reason == "unavailable"
    assert status.failures == 1
    assert "PRIVATE-STREAM-ERROR" not in repr(assistant._router.provider_diagnostics())
