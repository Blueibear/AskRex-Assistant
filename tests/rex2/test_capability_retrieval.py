from __future__ import annotations

from dataclasses import dataclass

from rex.capabilities.registry import Capability, CapabilityRegistry


def _registry(*cards: Capability) -> CapabilityRegistry:
    registry = CapabilityRegistry()
    for card in cards:
        registry.register(card)
    return registry


def _card(name: str, description: str, **kwargs: object) -> Capability:
    return Capability(name=name, description=description, **kwargs)  # type: ignore[arg-type]


def test_filters_security_health_and_identity_before_ranking() -> None:
    from rex.capabilities.retrieval import CapabilityRetriever

    registry = _registry(
        _card("safe_read", "Search local notes", triggers=["find notes"]),
        _card(
            "denied",
            "Search private mail",
            triggers=["find mail"],
            required_permissions=("email_send",),
        ),
        _card("unhealthy", "Search remote source", triggers=["find remote"], health="unhealthy"),
        _card("disabled", "Search disabled source", triggers=["find disabled"], enabled=False),
        _card(
            "identity_only",
            "Search identity-bound data",
            triggers=["find identity"],
            requires_identity=True,
        ),
        _card(
            "prohibited",
            "Search prohibited source",
            triggers=["find prohibited"],
            risk="prohibited",
        ),
    )
    retriever = CapabilityRetriever(registry)

    matches = retriever.retrieve("find search", granted_permissions=frozenset())

    assert [match.capability.id for match in matches] == ["safe_read"]


def test_retrieve_lexical_only_applies_same_security_health_filtering_without_semantic_scoring() -> (
    None
):
    """Used only to drive routing-time speculation (US-101): must never
    surface an unhealthy/disabled/denied/prohibited/identity-gated candidate
    even though it skips the (slower) local semantic scoring pass."""
    from rex.capabilities.retrieval import CapabilityRetriever

    registry = _registry(
        _card("safe_read", "Search local notes", triggers=["find notes"]),
        _card(
            "denied",
            "Search private mail",
            triggers=["find mail"],
            required_permissions=("email_send",),
        ),
        _card("unhealthy", "Search remote source", triggers=["find remote"], health="unhealthy"),
        _card("disabled", "Search disabled source", triggers=["find disabled"], enabled=False),
        _card(
            "identity_only",
            "Search identity-bound data",
            triggers=["find identity"],
            requires_identity=True,
        ),
        _card(
            "prohibited",
            "Search prohibited source",
            triggers=["find prohibited"],
            risk="prohibited",
        ),
    )
    retriever = CapabilityRetriever(registry)

    matches = retriever.retrieve_lexical_only("find search", granted_permissions=frozenset())

    assert [match.capability.id for match in matches] == ["safe_read"]
    assert matches[0].semantic_score == 0.0
    assert matches[0].reasons == ("lexical",)


def test_current_permission_snapshot_changes_results_without_reindexing() -> None:
    from rex.capabilities.retrieval import CapabilityRetriever

    registry = _registry(
        _card(
            "mail_search",
            "Search email messages",
            triggers=["mail", "email"],
            required_permissions=("email_send",),
        )
    )
    retriever = CapabilityRetriever(registry)

    assert retriever.retrieve("search my email", granted_permissions=frozenset()) == []
    allowed = retriever.retrieve("search my email", granted_permissions=frozenset({"email_send"}))
    assert [match.capability.id for match in allowed] == ["mail_search"]


def test_config_requirements_are_evaluated_before_ranking() -> None:
    from rex.capabilities.retrieval import CapabilityRetriever

    card = _card(
        "weather_now",
        "Get current weather conditions",
        triggers=["weather", "forecast"],
        enabled=False,
        requires_config=("weather_key",),
    )
    registry = _registry(card)

    assert CapabilityRetriever(registry, config=object()).retrieve("weather") == []
    configured = type("Config", (), {"weather_key": "local-key"})()
    matches = CapabilityRetriever(registry, config=configured).retrieve("weather")
    assert [match.capability.id for match in matches] == ["weather_now"]


def test_lexical_ranking_is_deterministic_and_exposes_safe_reasons() -> None:
    from rex.capabilities.retrieval import CapabilityRetriever

    registry = _registry(
        _card("weather_now", "Get current weather", triggers=["weather", "forecast"]),
        _card("web_search", "Search the web", triggers=["search", "web", "lookup"]),
    )
    retriever = CapabilityRetriever(registry, semantic_scorer=None)

    first = retriever.retrieve("weather forecast")
    second = retriever.retrieve("weather forecast")

    assert [m.capability.id for m in first] == ["weather_now"]
    assert [(m.capability.id, m.score, m.reasons) for m in first] == [
        (m.capability.id, m.score, m.reasons) for m in second
    ]
    assert first[0].lexical_score > 0
    assert first[0].semantic_score == 0
    assert all("weather forecast" not in reason for reason in first[0].reasons)


def test_lexical_ranking_ignores_non_informative_stopword_overlap() -> None:
    from rex.capabilities.retrieval import CapabilityRetriever

    registry = _registry(
        _card("calendar_create", "Create a new calendar event", triggers=["calendar", "event"]),
        _card("time_now", "Get the current local time for a location", triggers=["time", "clock"]),
    )

    matches = CapabilityRetriever(registry, semantic_scorer=None).retrieve(
        "Explain a benchmark fixture"
    )

    assert matches == []


def test_local_semantic_signal_handles_paraphrase_without_paid_service() -> None:
    from rex.capabilities.retrieval import CapabilityRetriever, LocalConceptSemanticScorer

    registry = _registry(
        _card("web_search", "Search the web for information", triggers=["search", "lookup", "web"]),
        _card("weather_now", "Get current weather", triggers=["weather", "forecast"]),
    )
    retriever = CapabilityRetriever(registry, semantic_scorer=LocalConceptSemanticScorer())

    matches = retriever.retrieve("research this online")

    assert matches[0].capability.id == "web_search"
    assert matches[0].semantic_score > 0
    assert "semantic" in matches[0].reasons


@dataclass
class _FakeSemanticScorer:
    scores: dict[str, float]

    def score(self, query: str, capability: Capability) -> float:
        del query
        return self.scores.get(capability.id, 0.0)


def test_hybrid_ranking_combines_lexical_and_semantic_evidence() -> None:
    from rex.capabilities.retrieval import CapabilityRetriever

    registry = _registry(
        _card("alpha", "Inspect a computer", triggers=["computer"]),
        _card("beta", "Inspect a machine", triggers=["machine"]),
    )
    scorer = _FakeSemanticScorer({"alpha": 0.1, "beta": 1.0})

    matches = CapabilityRetriever(registry, semantic_scorer=scorer).retrieve("inspect computer")

    assert [match.capability.id for match in matches] == ["beta", "alpha"]
    assert matches[0].score > matches[1].score
    assert matches[0].lexical_score > 0
    assert matches[0].semantic_score == 1.0


class _BrokenSemanticScorer:
    def score(self, query: str, capability: Capability) -> float:
        raise RuntimeError(f"broken semantic scorer for {query!r}/{capability.id}")


def test_broken_semantic_signal_falls_back_to_exact_lexical_result() -> None:
    from rex.capabilities.retrieval import CapabilityRetriever

    registry = _registry(
        _card("weather_now", "Get current weather", triggers=["weather", "forecast"]),
        _card("web_search", "Search the web", triggers=["search", "web"]),
    )
    lexical = CapabilityRetriever(registry, semantic_scorer=None).retrieve("weather forecast")
    fallback = CapabilityRetriever(registry, semantic_scorer=_BrokenSemanticScorer()).retrieve(
        "weather forecast"
    )

    assert [(m.capability.id, m.score) for m in fallback] == [
        (m.capability.id, m.score) for m in lexical
    ]
    assert fallback[0].reasons == lexical[0].reasons


def test_ambiguous_results_are_stably_ordered_by_score_then_id() -> None:
    from rex.capabilities.retrieval import CapabilityRetriever

    registry = _registry(
        _card("zeta_mail", "Send a message", triggers=["message"]),
        _card("alpha_sms", "Send a message", triggers=["message"]),
    )

    matches = CapabilityRetriever(registry, semantic_scorer=None).retrieve("send message")

    assert [match.capability.id for match in matches] == ["alpha_sms", "zeta_mail"]


def test_limit_is_applied_only_after_filtering_and_ranking() -> None:
    from rex.capabilities.retrieval import CapabilityRetriever

    registry = _registry(
        *[_card(f"search_{index}", "Search information", triggers=["search"]) for index in range(8)]
    )
    matches = CapabilityRetriever(registry, semantic_scorer=None).retrieve("search", limit=3)
    assert len(matches) == 3
    assert [match.capability.id for match in matches] == ["search_0", "search_1", "search_2"]


def test_tool_dispatcher_uses_hybrid_retrieval_for_paraphrase() -> None:
    from rex.tools.dispatcher import ToolDispatcher
    from rex.tools.registry import Tool, ToolRegistry

    registry = ToolRegistry()
    registry.register(
        Tool(
            name="web_search",
            description="Search the web for current information.",
            capability_tags=["search", "web", "lookup"],
            requires_config=[],
            handler=lambda **_kwargs: {},
        )
    )

    selected = ToolDispatcher(registry).select_tools("research this online")
    assert [tool.name for tool in selected] == ["web_search"]


def test_tool_dispatcher_filters_unhealthy_before_ranking() -> None:
    from rex.tools.dispatcher import ToolDispatcher
    from rex.tools.registry import Tool, ToolRegistry

    registry = ToolRegistry()
    registry.register(
        Tool(
            name="healthy_search",
            description="Search the web.",
            capability_tags=["search", "web"],
            requires_config=[],
            handler=lambda **_kwargs: {},
            health="healthy",
        )
    )
    registry.register(
        Tool(
            name="broken_search",
            description="Search the web.",
            capability_tags=["search", "web"],
            requires_config=[],
            handler=lambda **_kwargs: {},
            health="unhealthy",
        )
    )

    selected = ToolDispatcher(registry).select_tools("search the web")
    assert [tool.name for tool in selected] == ["healthy_search"]


def test_runtime_candidate_filter_runs_before_ranking_and_limit() -> None:
    from rex.capabilities.retrieval import CapabilityRetriever

    registry = _registry(
        _card("disabled_weather", "Weather weather weather forecast", triggers=["weather"]),
        _card("available_weather", "Weather conditions", triggers=["weather"]),
    )
    retriever = CapabilityRetriever(
        registry,
        semantic_scorer=None,
        candidate_filter=lambda card: card.id != "disabled_weather",
    )

    matches = retriever.retrieve("weather forecast", limit=1)

    assert [match.capability.id for match in matches] == ["available_weather"]


def test_tool_dispatcher_does_not_reenable_explicitly_disabled_configured_tool() -> None:
    from rex.tools.dispatcher import ToolDispatcher
    from rex.tools.registry import Tool, ToolRegistry

    registry = ToolRegistry()
    registry.register(
        Tool(
            name="disabled_weather",
            description="Get weather forecast",
            capability_tags=["weather", "forecast"],
            requires_config=["weather_key"],
            handler=lambda **_kwargs: {},
            enabled=False,
        )
    )
    config = type("Config", (), {"weather_key": "configured"})()

    selected = ToolDispatcher(registry, config=config).select_tools("weather forecast")

    assert selected == []


def test_semantic_fallback_log_does_not_leak_query_or_exception_payload(caplog) -> None:
    from rex.capabilities.retrieval import CapabilityRetriever

    secret_query = "search private payroll for secret-project-orchid"  # pragma: allowlist secret

    class _LeakyScorer:
        def score(self, query: str, capability: Capability) -> float:
            raise RuntimeError(f"embedding failed for {query}: api-token-like-payload")

    registry = _registry(_card("web_search", "Search the web", triggers=["search", "web"]))

    with caplog.at_level("WARNING", logger="rex.capabilities.retrieval"):
        matches = CapabilityRetriever(registry, semantic_scorer=_LeakyScorer()).retrieve(
            secret_query
        )

    assert matches
    assert "local semantic signal failed; using lexical fallback" in caplog.text
    assert secret_query not in caplog.text
    assert "api-token-like-payload" not in caplog.text
