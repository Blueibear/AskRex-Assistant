from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from rex.actions.dispatcher import ActionDispatcher
from rex.capabilities.registry import Capability, CapabilityRegistry
from rex.intent.router import IntentResult
from rex.runtime.cancellation import TurnCancelledError, TurnCancellation, turn_cancellation_scope
from rex.runtime.speculation import (
    SpeculationBudget,
    SpeculativePrefetcher,
    SpeculativeResult,
)
from rex.runtime.turn import (
    AuthorizationSnapshotRef,
    ResponseMode,
    TurnContext,
    TurnScope,
    TurnSource,
)
from rex.runtime.turn_engine import TurnEngine
from rex.tools.dispatcher import ToolDispatcher
from rex.tools.protocol import ToolResult
from rex.tools.registry import Tool, ToolRegistry


class _SpyDispatcher:
    """Records every capability actually dispatched, optionally with a delay."""

    def __init__(self, *, delay: float = 0.0, fail: frozenset[str] = frozenset()) -> None:
        self.calls: list[str] = []
        self._delay = delay
        self._fail = fail
        self._lock = threading.Lock()
        self.active = 0
        self.max_active = 0

    def dispatch(self, name, args, context=None):  # noqa: ANN001
        del args, context
        with self._lock:
            self.calls.append(name)
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            if self._delay:
                time.sleep(self._delay)
            if name in self._fail:
                raise RuntimeError("simulated dispatch failure")
            return ToolResult(success=True, output=f"{name}-output")
        finally:
            with self._lock:
                self.active -= 1


def _registry() -> CapabilityRegistry:
    registry = CapabilityRegistry()
    for capability in (
        Capability(
            name="safe_read",
            description="Read something harmless",
            operation="read",
            risk="safe",
            health="healthy",
            enabled=True,
        ),
        Capability(
            name="another_safe_read",
            description="Read something else harmless",
            operation="read",
            risk="safe",
            health="healthy",
            enabled=True,
        ),
        Capability(
            name="mutating_write",
            description="Write something",
            operation="mutation",
            risk="safe",
            health="healthy",
            enabled=True,
        ),
        Capability(
            name="risky_read",
            description="Read something sensitive",
            operation="read",
            risk="sensitive",
            health="healthy",
            enabled=True,
        ),
        Capability(
            name="prohibited_read",
            description="Read something prohibited",
            operation="read",
            risk="prohibited",
            health="healthy",
            enabled=True,
        ),
        Capability(
            name="disabled_read",
            description="Read something disabled",
            operation="read",
            risk="safe",
            health="healthy",
            enabled=False,
        ),
        Capability(
            name="degraded_read",
            description="Read something degraded",
            operation="read",
            risk="safe",
            health="degraded",
            enabled=True,
        ),
        Capability(
            name="unavailable_integration_read",
            description="Read from an unavailable integration",
            operation="read",
            risk="safe",
            health="healthy",
            enabled=True,
            integration_state="unavailable",
        ),
        Capability(
            name="unauthorized_read",
            description="Read gated behind a permission",
            operation="read",
            risk="safe",
            health="healthy",
            enabled=True,
            required_permissions=("special_scope",),
        ),
        Capability(
            name="identity_required_read",
            description="Read that requires identity",
            operation="read",
            risk="safe",
            health="healthy",
            enabled=True,
            requires_identity=True,
        ),
    ):
        registry.register(capability)
    return registry


ALL_INELIGIBLE_IDS = (
    "mutating_write",
    "risky_read",
    "prohibited_read",
    "disabled_read",
    "degraded_read",
    "unavailable_integration_read",
    "unauthorized_read",
)


# ---------------------------------------------------------------------------
# Eligibility
# ---------------------------------------------------------------------------


def test_eligible_true_only_for_healthy_permitted_read_only_safe_capability() -> None:
    registry = _registry()
    prefetcher = SpeculativePrefetcher(registry, _SpyDispatcher())

    assert prefetcher.eligible("safe_read", user_id="james", granted_permissions=frozenset())

    for capability_id in ALL_INELIGIBLE_IDS:
        assert not prefetcher.eligible(
            capability_id, user_id="james", granted_permissions=frozenset()
        )


def test_eligible_requires_identity_when_capability_declares_it() -> None:
    registry = _registry()
    prefetcher = SpeculativePrefetcher(registry, _SpyDispatcher())

    assert not prefetcher.eligible(
        "identity_required_read", user_id=None, granted_permissions=frozenset()
    )
    assert prefetcher.eligible(
        "identity_required_read", user_id="james", granted_permissions=frozenset()
    )


def test_eligible_unknown_capability_is_false() -> None:
    registry = _registry()
    prefetcher = SpeculativePrefetcher(registry, _SpyDispatcher())
    assert not prefetcher.eligible(
        "does_not_exist", user_id="james", granted_permissions=frozenset()
    )


# ---------------------------------------------------------------------------
# Never speculatively call mutating/risky/disabled/unauthorized capabilities
# ---------------------------------------------------------------------------


def test_prefetch_never_dispatches_ineligible_candidates() -> None:
    registry = _registry()
    dispatcher = _SpyDispatcher()
    prefetcher = SpeculativePrefetcher(registry, dispatcher)

    candidate_ids = ("safe_read", *ALL_INELIGIBLE_IDS)
    outcome = prefetcher.prefetch(
        candidate_ids,
        user_id="james",
        scope="user",
        granted_permissions=frozenset(),
    )

    assert dispatcher.calls == ["safe_read"]
    assert set(outcome.results) == {"safe_read"}
    skipped = {
        attempt.capability_id: attempt.outcome
        for attempt in outcome.attempts
        if attempt.capability_id != "safe_read"
    }
    assert set(skipped) == set(ALL_INELIGIBLE_IDS)
    assert all(outcome_value == "skipped_ineligible" for outcome_value in skipped.values())


def test_prefetch_never_dispatches_mutating_capability_even_when_listed_first() -> None:
    registry = _registry()
    dispatcher = _SpyDispatcher()
    prefetcher = SpeculativePrefetcher(registry, dispatcher)

    prefetcher.prefetch(
        ("mutating_write", "safe_read"),
        user_id="james",
        scope="user",
        granted_permissions=frozenset(),
    )

    assert "mutating_write" not in dispatcher.calls


def test_prefetch_grants_no_authority_from_admin_style_risk_bypass() -> None:
    # Even an admin-equivalent permission set must not resurrect a
    # sensitive/prohibited/mutating candidate for speculation.
    registry = _registry()
    dispatcher = _SpyDispatcher()
    prefetcher = SpeculativePrefetcher(registry, dispatcher)

    prefetcher.prefetch(
        ("risky_read", "prohibited_read", "mutating_write"),
        user_id="james",
        scope="user",
        granted_permissions=frozenset({"admin"}),
    )

    assert dispatcher.calls == []


# ---------------------------------------------------------------------------
# Budgets: concurrency / candidate count / wall-clock timeout
# ---------------------------------------------------------------------------


def test_prefetch_respects_max_candidates_budget() -> None:
    registry = _registry()
    dispatcher = _SpyDispatcher()
    prefetcher = SpeculativePrefetcher(
        registry,
        dispatcher,
        budget=SpeculationBudget(max_candidates=1, max_concurrency=2, total_timeout_seconds=1.0),
    )

    outcome = prefetcher.prefetch(
        ("safe_read", "another_safe_read"),
        user_id="james",
        scope="user",
        granted_permissions=frozenset(),
    )

    assert len(dispatcher.calls) == 1
    budget_skipped = [a for a in outcome.attempts if a.outcome == "skipped_budget"]
    assert len(budget_skipped) == 1


def test_prefetch_respects_max_concurrency_budget() -> None:
    registry = _registry()
    for extra_name in ("safe_read_3", "safe_read_4"):
        registry.register(
            Capability(
                name=extra_name,
                description="Extra harmless read",
                operation="read",
                risk="safe",
                health="healthy",
                enabled=True,
            )
        )
    dispatcher = _SpyDispatcher(delay=0.05)
    prefetcher = SpeculativePrefetcher(
        registry,
        dispatcher,
        budget=SpeculationBudget(
            max_candidates=4, max_concurrency=2, total_timeout_seconds=2.0
        ),
    )

    outcome = prefetcher.prefetch(
        ("safe_read", "another_safe_read", "safe_read_3", "safe_read_4"),
        user_id="james",
        scope="user",
        granted_permissions=frozenset(),
    )

    assert dispatcher.max_active <= 2
    assert len(outcome.results) == 4


def test_prefetch_wall_clock_bounded_by_total_timeout_even_with_slow_candidate() -> None:
    registry = _registry()
    dispatcher = _SpyDispatcher(delay=0.3)
    prefetcher = SpeculativePrefetcher(
        registry,
        dispatcher,
        budget=SpeculationBudget(
            max_candidates=1, max_concurrency=1, total_timeout_seconds=0.05
        ),
    )

    started = time.monotonic()
    outcome = prefetcher.prefetch(
        ("safe_read",),
        user_id="james",
        scope="user",
        granted_permissions=frozenset(),
    )
    elapsed = time.monotonic() - started

    assert elapsed < 0.25
    assert outcome.results == {}
    assert outcome.attempts[0].outcome == "timeout"


# ---------------------------------------------------------------------------
# Cancellation inheritance
# ---------------------------------------------------------------------------


def test_prefetch_never_dispatches_when_turn_already_cancelled() -> None:
    registry = _registry()
    dispatcher = _SpyDispatcher()
    prefetcher = SpeculativePrefetcher(registry, dispatcher)
    cancellation = TurnCancellation("turn-1")
    cancellation.cancel("stale_request")

    with turn_cancellation_scope(cancellation):
        outcome = prefetcher.prefetch(
            ("safe_read",),
            user_id="james",
            scope="user",
            granted_permissions=frozenset(),
        )

    assert dispatcher.calls == []
    assert outcome.attempts[0].outcome == "skipped_cancelled"


# ---------------------------------------------------------------------------
# Audit/metrics stay content-free
# ---------------------------------------------------------------------------


def test_attempt_records_expose_only_bounded_metadata_never_payload() -> None:
    registry = _registry()
    dispatcher = _SpyDispatcher()
    prefetcher = SpeculativePrefetcher(registry, dispatcher)

    outcome = prefetcher.prefetch(
        ("safe_read", "mutating_write"),
        user_id="james",
        scope="user",
        granted_permissions=frozenset(),
    )

    for attempt in outcome.attempts:
        allowed_fields = {"capability_id", "outcome", "duration_ms"}
        assert set(vars(attempt)) == allowed_fields
        assert isinstance(attempt.capability_id, str)
        assert isinstance(attempt.outcome, str)
        assert isinstance(attempt.duration_ms, float)


def test_dispatch_failure_is_recorded_without_raising_and_without_payload() -> None:
    registry = _registry()
    dispatcher = _SpyDispatcher(fail=frozenset({"safe_read"}))
    prefetcher = SpeculativePrefetcher(registry, dispatcher)

    outcome = prefetcher.prefetch(
        ("safe_read",),
        user_id="james",
        scope="user",
        granted_permissions=frozenset(),
    )

    assert outcome.results == {}
    assert outcome.attempts[0].outcome == "failed"


# ---------------------------------------------------------------------------
# Revalidation before final-plan use
# ---------------------------------------------------------------------------


def test_consume_returns_payload_for_fresh_matching_authorized_result() -> None:
    registry = _registry()
    prefetcher = SpeculativePrefetcher(registry, _SpyDispatcher())
    tool_result = ToolResult(success=True, output="value")
    result = SpeculativeResult(
        capability_id="safe_read", tool_result=tool_result, user_id="james", scope="user"
    )

    consumed = prefetcher.consume(
        result, user_id="james", scope="user", granted_permissions=frozenset()
    )

    assert consumed is tool_result


def test_consume_discards_result_for_mismatched_user() -> None:
    registry = _registry()
    prefetcher = SpeculativePrefetcher(registry, _SpyDispatcher())
    result = SpeculativeResult(
        capability_id="safe_read",
        tool_result=ToolResult(success=True, output="value"),
        user_id="james",
        scope="user",
    )

    assert (
        prefetcher.consume(
            result, user_id="cole", scope="user", granted_permissions=frozenset()
        )
        is None
    )


def test_consume_discards_result_for_mismatched_scope() -> None:
    registry = _registry()
    prefetcher = SpeculativePrefetcher(registry, _SpyDispatcher())
    result = SpeculativeResult(
        capability_id="safe_read",
        tool_result=ToolResult(success=True, output="value"),
        user_id="james",
        scope="user",
    )

    assert (
        prefetcher.consume(
            result, user_id="james", scope="household", granted_permissions=frozenset()
        )
        is None
    )


def test_consume_discards_stale_result_past_max_age_budget() -> None:
    registry = _registry()
    prefetcher = SpeculativePrefetcher(
        registry, _SpyDispatcher(), budget=SpeculationBudget(max_age_seconds=0.01)
    )
    result = SpeculativeResult(
        capability_id="safe_read",
        tool_result=ToolResult(success=True, output="value"),
        user_id="james",
        scope="user",
    )

    time.sleep(0.05)

    assert (
        prefetcher.consume(
            result, user_id="james", scope="user", granted_permissions=frozenset()
        )
        is None
    )


def test_consume_discards_result_when_capability_becomes_ineligible_before_use() -> None:
    registry = _registry()
    prefetcher = SpeculativePrefetcher(registry, _SpyDispatcher())
    result = SpeculativeResult(
        capability_id="safe_read",
        tool_result=ToolResult(success=True, output="value"),
        user_id="james",
        scope="user",
    )

    registry.update_runtime_state("safe_read", enabled=False)

    assert (
        prefetcher.consume(
            result, user_id="james", scope="user", granted_permissions=frozenset()
        )
        is None
    )


def test_consume_none_result_is_none() -> None:
    registry = _registry()
    prefetcher = SpeculativePrefetcher(registry, _SpyDispatcher())
    assert (
        prefetcher.consume(None, user_id="james", scope="user", granted_permissions=frozenset())
        is None
    )


# ---------------------------------------------------------------------------
# Bounded resource lifecycle: one persistent, fixed-size pool per instance
# ---------------------------------------------------------------------------


def test_prefetcher_reuses_one_bounded_executor_across_calls() -> None:
    registry = _registry()
    prefetcher = SpeculativePrefetcher(registry, _SpyDispatcher())
    executor_before = prefetcher._executor

    prefetcher.prefetch(
        ("safe_read",), user_id="james", scope="user", granted_permissions=frozenset()
    )
    prefetcher.prefetch(
        ("safe_read",), user_id="james", scope="user", granted_permissions=frozenset()
    )

    # No new pool object is created (and abandoned) per call; total background
    # dispatch work stays bounded to one fixed-size executor for this instance.
    assert prefetcher._executor is executor_before
    prefetcher.close()


def test_prefetch_does_not_block_caller_past_timeout_even_when_pool_is_saturated() -> None:
    registry = _registry()
    dispatcher = _SpyDispatcher(delay=0.3)
    prefetcher = SpeculativePrefetcher(
        registry,
        dispatcher,
        budget=SpeculationBudget(max_candidates=1, max_concurrency=1, total_timeout_seconds=0.05),
    )

    started = time.monotonic()
    outcome = prefetcher.prefetch(
        ("safe_read",), user_id="james", scope="user", granted_permissions=frozenset()
    )
    elapsed = time.monotonic() - started

    assert elapsed < 0.25
    assert outcome.results == {}
    prefetcher.close()


# ---------------------------------------------------------------------------
# Canonical ToolDispatcher integration (US-101 routing-time wiring)
# ---------------------------------------------------------------------------


def _tool_dispatcher_with(*tools: Tool) -> ToolDispatcher:
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    return ToolDispatcher(registry)


def test_execute_tools_speculatively_prefetches_eligible_reads_concurrently() -> None:
    calls: list[str] = []
    lock = threading.Lock()

    def _handler(tag: str):
        def _run(**_kwargs: object) -> dict[str, str]:
            with lock:
                calls.append(tag)
            time.sleep(0.1)
            return {"value": tag}

        return _run

    tool_a = Tool(
        name="archive_read_a",
        description="Read archive A",
        capability_tags=["archive"],
        requires_config=[],
        handler=_handler("a"),
        operation="read",
        risk="safe",
        health="healthy",
    )
    tool_b = Tool(
        name="archive_read_b",
        description="Read archive B",
        capability_tags=["archive"],
        requires_config=[],
        handler=_handler("b"),
        operation="read",
        risk="safe",
        health="healthy",
    )
    dispatcher = _tool_dispatcher_with(tool_a, tool_b)

    started = time.monotonic()
    results = dispatcher.execute_tools([tool_a, tool_b], "archive", user_id="james")
    elapsed = time.monotonic() - started

    assert results["archive_read_a"] == {"value": "a"}
    assert results["archive_read_b"] == {"value": "b"}
    assert sorted(calls) == ["a", "b"]
    # Sequential dispatch would take >= 0.2s; bounded concurrent speculative
    # prefetch keeps wall time close to a single call.
    assert elapsed < 0.18


def test_execute_tools_never_speculatively_dispatches_mutating_tool() -> None:
    call_order: list[str] = []
    lock = threading.Lock()

    def read_handler(**_kwargs: object) -> dict[str, object]:
        with lock:
            call_order.append("archive_read")
        return {"value": "ok"}

    def write_handler(**_kwargs: object) -> dict[str, object]:
        with lock:
            call_order.append("archive_write")
        return {"ok": True}

    read_tool = Tool(
        name="archive_read",
        description="Read archive",
        capability_tags=["archive"],
        requires_config=[],
        handler=read_handler,
        operation="read",
        risk="safe",
        health="healthy",
    )
    write_tool = Tool(
        name="archive_write",
        description="Write archive",
        capability_tags=["archive"],
        requires_config=[],
        handler=write_handler,
        operation="mutation",
        risk="safe",
        health="healthy",
        requires_identity=True,
    )
    dispatcher = _tool_dispatcher_with(read_tool, write_tool)

    results = dispatcher.execute_tools([write_tool, read_tool], "archive", user_id="james")

    assert results["archive_read"] == {"value": "ok"}
    assert results["archive_write"] == {"ok": True}
    # If the mutating tool had ever been included in speculative prefetch, its
    # handler would run twice (once speculatively, once for real). It never
    # runs more than once because operation="mutation" is never eligible.
    assert call_order.count("archive_write") == 1
    assert call_order.count("archive_read") == 1


def test_execute_tools_uses_consumed_speculative_result_without_a_second_dispatch_call() -> None:
    dispatch_calls: list[str] = []
    read_tool = Tool(
        name="archive_read",
        description="Read archive",
        capability_tags=["archive"],
        requires_config=[],
        handler=lambda **_kw: {"value": "ok"},
        operation="read",
        risk="safe",
        health="healthy",
    )
    dispatcher = _tool_dispatcher_with(read_tool)
    real_dispatch = dispatcher.dispatch

    def spy_dispatch(name: str, args: dict, context: dict | None = None):
        dispatch_calls.append(name)
        return real_dispatch(name, args, context)

    dispatcher.dispatch = spy_dispatch  # type: ignore[method-assign]
    consumed_result = ToolResult(success=True, output={"value": "speculative"})
    dispatcher._prefetcher = SimpleNamespace(
        eligible=lambda *a, **k: True,
        prefetch=lambda *a, **k: SimpleNamespace(get=lambda name: object()),
        consume=lambda *a, **k: consumed_result,
    )

    results = dispatcher.execute_tools([read_tool], "archive", user_id="james")

    assert results["archive_read"] == {"value": "speculative"}
    assert dispatch_calls == []


def test_execute_tools_falls_back_to_real_dispatch_when_consume_revalidation_fails() -> None:
    read_tool = Tool(
        name="archive_read",
        description="Read archive",
        capability_tags=["archive"],
        requires_config=[],
        handler=lambda **_kw: {"value": "fresh"},
        operation="read",
        risk="safe",
        health="healthy",
    )
    dispatcher = _tool_dispatcher_with(read_tool)
    dispatcher._prefetcher = SimpleNamespace(
        eligible=lambda *a, **k: True,
        prefetch=lambda *a, **k: SimpleNamespace(get=lambda name: object()),
        consume=lambda *a, **k: None,  # revalidation failed: stale/mismatched/ineligible
    )

    results = dispatcher.execute_tools([read_tool], "archive", user_id="james")

    assert results["archive_read"] == {"value": "fresh"}


def test_execute_tools_never_speculatively_dispatches_when_turn_already_cancelled() -> None:
    calls: list[str] = []

    def handler(**_kwargs: object) -> dict[str, str]:
        calls.append("dispatched")
        return {"value": "ok"}

    read_tool = Tool(
        name="archive_read",
        description="Read archive",
        capability_tags=["archive"],
        requires_config=[],
        handler=handler,
        operation="read",
        risk="safe",
        health="healthy",
    )
    dispatcher = _tool_dispatcher_with(read_tool)
    cancellation = TurnCancellation("turn-cancel-1")
    cancellation.cancel("stale_request")

    with turn_cancellation_scope(cancellation):
        with pytest.raises(TurnCancelledError):
            dispatcher.execute_tools([read_tool], "archive", user_id="james")

    assert calls == []


# ---------------------------------------------------------------------------
# End-to-end: canonical TurnEngine / ActionDispatcher routing invokes prefetch
# ---------------------------------------------------------------------------


def _turn_context(user_id: str = "james") -> TurnContext:
    return TurnContext.create(
        user_id=user_id,
        scope=TurnScope.USER,
        source=TurnSource.ELECTRON,
        device_id="desktop-1",
        response_mode=ResponseMode.SCREEN,
        authorization=AuthorizationSnapshotRef("policy:v1", f"permissions:{user_id}:v1"),
    )


@pytest.mark.asyncio
async def test_action_dispatcher_speculative_prefetch_is_invoked_through_turn_engine() -> None:
    calls: list[str] = []

    def read_handler(**_kwargs: object) -> dict[str, str]:
        calls.append("archive_read")
        return {"value": "archived-notes"}

    read_tool = Tool(
        name="archive_read",
        description="Read archived household notes",
        capability_tags=["archive", "notes"],
        requires_config=[],
        handler=read_handler,
        operation="read",
        risk="safe",
        health="healthy",
    )
    tool_dispatcher = _tool_dispatcher_with(read_tool)

    builder = MagicMock()
    package = SimpleNamespace(messages=[], prompt="prompt")
    builder.build.return_value = package
    llm = MagicMock()
    llm.generate.return_value = "Here is what I found."
    result_handler = MagicMock()
    result_handler.process = AsyncMock(return_value="Here is what I found.")

    action_dispatcher = ActionDispatcher(
        context_builder=builder,
        llm=llm,
        result_handler=result_handler,
        tool_dispatcher=tool_dispatcher,
    )
    context = _turn_context()
    engine = TurnEngine()

    async def operation(events):
        return await action_dispatcher.dispatch(
            IntentResult(handled=False, response=None, intent_type=None),
            package,
            "please read the archived notes",
            user_id="james",
            turn_events=events,
            scope=context.scope.value,
        )

    result = await engine.execute_async(context, operation)

    assert result.success is True
    # The canonical TurnEngine -> ActionDispatcher -> ToolDispatcher route
    # actually reaches SpeculativePrefetcher: the eligible read is dispatched
    # exactly once (speculatively, then consumed after revalidation), never
    # duplicated by a second synchronous dispatch call.
    assert calls == ["archive_read"]


@pytest.mark.asyncio
async def test_action_dispatcher_never_speculatively_dispatches_mutation_through_turn_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call_order: list[str] = []

    def write_handler(**_kwargs: object) -> dict[str, bool]:
        call_order.append("archive_write")
        return {"ok": True}

    write_tool = Tool(
        name="archive_write",
        description="Write archive entry",
        capability_tags=["archive", "write"],
        requires_config=[],
        handler=write_handler,
        operation="mutation",
        risk="safe",
        health="healthy",
        requires_identity=True,
        required_permissions=("computer_control",),
    )
    tool_dispatcher = _tool_dispatcher_with(write_tool)
    granted = frozenset({"computer_control"})

    builder = MagicMock()
    package = SimpleNamespace(messages=[], prompt="prompt")
    builder.build.return_value = package
    llm = MagicMock()
    llm.generate.return_value = "Done."
    result_handler = MagicMock()
    result_handler.process = AsyncMock(return_value="Done.")

    action_dispatcher = ActionDispatcher(
        context_builder=builder,
        llm=llm,
        result_handler=result_handler,
        tool_dispatcher=tool_dispatcher,
    )
    context = _turn_context()
    engine = TurnEngine()

    async def operation(events):
        return await action_dispatcher.dispatch(
            IntentResult(handled=False, response=None, intent_type=None),
            package,
            "please write to the archive",
            user_id="james",
            turn_events=events,
            scope=context.scope.value,
        )

    import rex.permissions as permissions_module

    monkeypatch.setattr(permissions_module, "get_permissions", lambda _user_id: granted)
    result = await engine.execute_async(context, operation)

    assert result.success is True
    # The mutating capability may still be selected and dispatched once for
    # real, but never twice: it was never eligible for speculative prefetch.
    assert call_order.count("archive_write") == 1
