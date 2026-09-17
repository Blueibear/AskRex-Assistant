from __future__ import annotations

import logging
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from rex.actions.dispatcher import ActionDispatcher
from rex.audit import AuditLogger
from rex.capabilities.registry import Capability, CapabilityRegistry
from rex.intent.router import IntentResult
from rex.runtime.cancellation import TurnCancellation, TurnCancelledError, turn_cancellation_scope
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
from rex.tools.execution import ToolExecutionLifecycle
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
        budget=SpeculationBudget(max_candidates=4, max_concurrency=2, total_timeout_seconds=2.0),
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
        budget=SpeculationBudget(max_candidates=1, max_concurrency=1, total_timeout_seconds=0.05),
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


class _PayloadBearingDispatcher:
    """Simulates a dispatch failure whose exception text carries private content."""

    SECRET_PAYLOAD = "ssn=123-45-6789 raw_prompt='tell me about my medical results'"

    def dispatch(self, name, args, context=None):  # noqa: ANN001
        del name, args, context
        raise RuntimeError(f"upstream call failed for payload: {self.SECRET_PAYLOAD}")


def test_dispatch_failure_never_logs_raw_exception_text_that_may_carry_payload(
    caplog: pytest.LogCaptureFixture,
) -> None:
    registry = _registry()
    prefetcher = SpeculativePrefetcher(registry, _PayloadBearingDispatcher())

    with caplog.at_level(logging.DEBUG):
        outcome = prefetcher.prefetch(
            ("safe_read",),
            user_id="james",
            scope="user",
            granted_permissions=frozenset(),
        )

    assert outcome.results == {}
    assert outcome.attempts[0].outcome == "failed"
    for record in caplog.records:
        assert _PayloadBearingDispatcher.SECRET_PAYLOAD not in record.getMessage()
        assert record.exc_info is None
        assert record.exc_text is None


def test_attempt_records_never_carry_payload_even_on_dispatch_failure() -> None:
    registry = _registry()
    prefetcher = SpeculativePrefetcher(registry, _PayloadBearingDispatcher())

    outcome = prefetcher.prefetch(
        ("safe_read",),
        user_id="james",
        scope="user",
        granted_permissions=frozenset(),
    )

    for attempt in outcome.attempts:
        allowed_fields = {"capability_id", "outcome", "duration_ms"}
        assert set(vars(attempt)) == allowed_fields
        for value in vars(attempt).values():
            if isinstance(value, str):
                assert _PayloadBearingDispatcher.SECRET_PAYLOAD not in value


def test_speculative_prefetch_through_real_dispatcher_never_leaks_raw_failure_text(
    caplog: pytest.LogCaptureFixture, tmp_path
) -> None:
    """Real ToolDispatcher/ToolExecutionLifecycle failure text must never leak."""
    secret = "ssn=123-45-6789 raw_prompt='tell me about my medical results'"

    def failing_handler(**_kwargs: object) -> dict[str, str]:
        raise ConnectionError(f"connection failed for payload: {secret}")

    read_tool = Tool(
        name="archive_read_secret",
        description="Read archive",
        capability_tags=["archive"],
        requires_config=[],
        handler=failing_handler,
        operation="read",
        risk="safe",
        health="healthy",
    )
    dispatcher = _tool_dispatcher_with(read_tool)
    audit_logger = AuditLogger(log_path=tmp_path / "audit.log")

    with (
        patch("rex.tools.execution.get_audit_logger", return_value=audit_logger),
        caplog.at_level(logging.DEBUG),
    ):
        outcome = dispatcher._prefetcher.prefetch(
            ("archive_read_secret",),
            user_id="james",
            scope="user",
            granted_permissions=frozenset(),
        )

    assert outcome.results == {}

    for record in caplog.records:
        assert secret not in record.getMessage()
        assert record.exc_info is None
        assert record.exc_text is None

    entries = audit_logger.read()
    assert entries
    for entry in entries:
        assert secret not in entry.model_dump_json()


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
        prefetcher.consume(result, user_id="cole", scope="user", granted_permissions=frozenset())
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
        prefetcher.consume(result, user_id="james", scope="user", granted_permissions=frozenset())
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
        prefetcher.consume(result, user_id="james", scope="user", granted_permissions=frozenset())
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
# The underlying dispatch itself must be bound to the speculation deadline,
# never the dispatcher's own (much larger) ordinary timeout, so a timed-out
# candidate cannot occupy the bounded pool past the declared budget.
# ---------------------------------------------------------------------------


def test_speculative_dispatch_context_bounds_underlying_timeout_to_remaining_budget() -> None:
    captured: dict[str, object] = {}
    real_execute = ToolExecutionLifecycle.execute

    def spy_execute(self, tool, args, context=None, **kwargs):  # noqa: ANN001
        if context and context.get("speculative"):
            captured["timeout_seconds"] = kwargs.get("timeout_seconds")
        return real_execute(self, tool, args, context, **kwargs)

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
    registry = ToolRegistry()
    registry.register(read_tool)
    # A dispatcher-level timeout far larger than the speculation budget: the
    # underlying dispatch call must never be allowed to inherit this value
    # for a speculative candidate.
    config = SimpleNamespace(tool_timeout_seconds=10.0)
    dispatcher = ToolDispatcher(registry, config=config)
    dispatcher._prefetcher = SpeculativePrefetcher(
        registry.capability_registry,
        dispatcher,
        budget=SpeculationBudget(max_candidates=1, max_concurrency=1, total_timeout_seconds=0.05),
    )

    with patch("rex.tools.execution.ToolExecutionLifecycle.execute", spy_execute):
        dispatcher._prefetcher.prefetch(
            ("archive_read",), user_id="james", scope="user", granted_permissions=frozenset()
        )

    assert captured, "expected the speculative call to reach ToolExecutionLifecycle.execute"
    assert captured["timeout_seconds"] is not None
    assert captured["timeout_seconds"] <= 0.05
    assert captured["timeout_seconds"] < config.tool_timeout_seconds


def test_dispatch_bounds_speculative_call_even_though_handler_and_dispatcher_timeout_are_slower() -> (
    None
):
    handler_started = threading.Event()

    def slow_handler(**_kwargs: object) -> dict[str, str]:
        handler_started.set()
        time.sleep(0.6)
        return {"value": "too-late"}

    read_tool = Tool(
        name="slow_read",
        description="Slow read",
        capability_tags=["slow"],
        requires_config=[],
        handler=slow_handler,
        operation="read",
        risk="safe",
        health="healthy",
    )
    registry = ToolRegistry()
    registry.register(read_tool)
    config = SimpleNamespace(tool_timeout_seconds=5.0)
    dispatcher = ToolDispatcher(registry, config=config)

    started = time.monotonic()
    result = dispatcher.dispatch(
        "slow_read",
        {},
        {"speculative": True, "user_id": "james", "_speculative_timeout_seconds": 0.05},
    )
    elapsed = time.monotonic() - started

    assert handler_started.wait(timeout=1.0)
    # Bounded by the (tiny) speculation budget, never by the handler's own
    # 0.6s sleep nor the dispatcher's much larger configured 5.0s timeout.
    assert elapsed < 0.4
    assert result.success is False


def test_timed_out_prefetches_keep_actual_handlers_within_one_bounded_pool() -> None:
    """Timed-out speculative handlers remain counted until they really return.

    Python cannot forcibly stop arbitrary tool-handler threads.  The safe
    boundary is therefore to retain the handler in the prefetcher's fixed
    pool rather than letting ToolExecutionLifecycle time it out in a nested
    executor and immediately release the outer slot.
    """
    started = threading.Event()
    release = threading.Event()
    lock = threading.Lock()
    active = 0
    max_active = 0
    starts = 0

    def slow_handler(**_kwargs: object) -> dict[str, str]:
        nonlocal active, max_active, starts
        with lock:
            active += 1
            starts += 1
            max_active = max(max_active, active)
            if starts == 2:
                started.set()
        try:
            assert release.wait(timeout=2.0)
            return {"value": "slow"}
        finally:
            with lock:
                active -= 1

    tool = Tool(
        name="slow_read",
        description="Slow read",
        capability_tags=["slow"],
        requires_config=[],
        handler=slow_handler,
        operation="read",
        risk="safe",
        health="healthy",
    )
    registry = ToolRegistry()
    registry.register(tool)
    dispatcher = ToolDispatcher(registry, config=SimpleNamespace(tool_timeout_seconds=5.0))
    dispatcher._prefetcher = SpeculativePrefetcher(
        registry.capability_registry,
        dispatcher,
        budget=SpeculationBudget(max_candidates=1, max_concurrency=2, total_timeout_seconds=0.03),
    )

    for _ in range(4):
        outcome = dispatcher._prefetcher.prefetch(
            ("slow_read",), user_id="james", scope="user", granted_permissions=frozenset()
        )
        assert outcome.results == {}
    assert started.wait(timeout=1.0)
    with lock:
        assert starts == 2
        assert max_active == 2

    release.set()
    dispatcher._prefetcher.close()


def test_queued_prefetch_never_dispatches_after_its_original_deadline() -> None:
    """A saturated pool must not turn a stale queued candidate into a late read."""
    first_started = threading.Event()
    release_first = threading.Event()
    calls: list[str] = []

    def slow_handler(**_kwargs: object) -> dict[str, str]:
        calls.append("slow")
        first_started.set()
        assert release_first.wait(timeout=2.0)
        return {"value": "slow"}

    def queued_handler(**_kwargs: object) -> dict[str, str]:
        calls.append("queued")
        return {"value": "queued"}

    registry = ToolRegistry()
    registry.register(
        Tool(
            name="slow_read",
            description="Slow read",
            capability_tags=["slow"],
            requires_config=[],
            handler=slow_handler,
            operation="read",
            risk="safe",
            health="healthy",
        )
    )
    registry.register(
        Tool(
            name="queued_read",
            description="Queued read",
            capability_tags=["queued"],
            requires_config=[],
            handler=queued_handler,
            operation="read",
            risk="safe",
            health="healthy",
        )
    )
    dispatcher = ToolDispatcher(registry, config=SimpleNamespace(tool_timeout_seconds=5.0))
    prefetcher = SpeculativePrefetcher(
        registry.capability_registry,
        dispatcher,
        budget=SpeculationBudget(max_candidates=1, max_concurrency=1, total_timeout_seconds=0.03),
    )

    _first = prefetcher.begin(
        ("slow_read",), user_id="james", scope="user", granted_permissions=frozenset()
    )
    assert first_started.wait(timeout=1.0)
    second = prefetcher.begin(
        ("queued_read",), user_id="james", scope="user", granted_permissions=frozenset()
    )
    time.sleep(0.05)
    release_first.set()
    time.sleep(0.05)

    assert calls == ["slow"]
    assert prefetcher.finish(second).results == {}
    prefetcher.close()


def test_prefetch_discards_successful_result_larger_than_payload_budget() -> None:
    registry = _registry()
    dispatcher = _SpyDispatcher()
    prefetcher = SpeculativePrefetcher(
        registry,
        dispatcher,
        budget=SpeculationBudget(max_result_bytes=8),
    )

    outcome = prefetcher.prefetch(
        ("safe_read",), user_id="james", scope="user", granted_permissions=frozenset()
    )

    assert outcome.results == {}
    assert [(attempt.capability_id, attempt.outcome) for attempt in outcome.attempts] == [
        ("safe_read", "discarded_result_budget")
    ]


def test_prefetch_result_budget_caps_total_retained_payload_across_candidates() -> None:
    """The payload cap applies to the complete transient result set, not each result."""

    class _SmallPayloadDispatcher:
        def dispatch(self, name, args, context=None):  # noqa: ANN001
            del args, context
            return ToolResult(
                success=True,
                output="aaaaaa" if name == "safe_read" else "bbbbbb",
            )

    registry = _registry()
    prefetcher = SpeculativePrefetcher(
        registry,
        _SmallPayloadDispatcher(),
        # Each JSON-encoded string is eight bytes, but retaining both would
        # exceed this total budget.
        budget=SpeculationBudget(max_result_bytes=10),
    )

    outcome = prefetcher.prefetch(
        ("safe_read", "another_safe_read"),
        user_id="james",
        scope="user",
        granted_permissions=frozenset(),
    )

    assert len(outcome.results) == 1
    assert [attempt.outcome for attempt in outcome.attempts].count("discarded_result_budget") == 1
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
    # write_tool has no verifier, so the canonical truthful lifecycle reports
    # its outcome as attempted-but-unverified rather than echoing the raw
    # handler payload as a confirmed success.
    assert "attempted" in results["archive_write"]
    assert "not independently verified" in results["archive_write"]
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


# ---------------------------------------------------------------------------
# Routing-time speculation: dispatch begins while routing is still resolving,
# not only after the authoritative selection has already finished.
# ---------------------------------------------------------------------------


def test_begin_speculative_prefetch_dispatches_before_full_selection_ever_runs() -> None:
    """`begin_speculative_prefetch` must dispatch from a fast lexical signal
    alone; it must never require the authoritative hybrid-ranked
    `select_tools_for_user`/`retrieve()` selection to have run first."""
    dispatch_started = threading.Event()

    def handler(**_kwargs: object) -> dict[str, str]:
        dispatch_started.set()
        return {"value": "ok"}

    read_tool = Tool(
        name="archive_read",
        description="Read archived household notes",
        capability_tags=["archive", "notes"],
        requires_config=[],
        handler=handler,
        operation="read",
        risk="safe",
        health="healthy",
    )
    dispatcher = _tool_dispatcher_with(read_tool)

    # No call to select_tools/select_tools_for_user/retrieve happens here at
    # all; only the fast routing-time signal is used.
    handle = dispatcher.begin_speculative_prefetch(
        "please read the archived notes", user_id="james", scope="user"
    )

    assert handle is not None
    assert dispatch_started.wait(timeout=1.0)
    outcome = dispatcher._prefetcher.finish(handle)
    assert outcome.results.get("archive_read") is not None


def test_begin_speculative_prefetch_never_dispatches_mutating_capability() -> None:
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
    )
    dispatcher = _tool_dispatcher_with(write_tool)

    handle = dispatcher.begin_speculative_prefetch(
        "please write to the archive", user_id="james", scope="user"
    )

    assert handle is None
    assert call_order == []


@pytest.mark.asyncio
async def test_action_dispatcher_prefetch_overlaps_with_still_resolving_routing_selection() -> None:
    """The production ActionDispatcher wiring must start speculative dispatch
    while the authoritative hybrid-ranked selection is still resolving, not
    only once `select_tools_for_user` has already returned."""
    handler_started = threading.Event()
    calls: list[str] = []

    def read_handler(**_kwargs: object) -> dict[str, str]:
        calls.append("archive_read")
        handler_started.set()
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

    import rex.capabilities.retrieval as retrieval_module

    real_retrieve = retrieval_module.CapabilityRetriever.retrieve
    retrieve_saw_prefetch_already_running = threading.Event()

    def slow_retrieve(self, *args, **kwargs):  # noqa: ANN001
        # By the time the authoritative hybrid-ranked selection actually
        # runs, routing-time speculative dispatch must already be under way
        # -- proving prefetch began *while* routing was still resolving,
        # not only once it had already finished.
        if handler_started.wait(timeout=1.0):
            retrieve_saw_prefetch_already_running.set()
        return real_retrieve(self, *args, **kwargs)

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

    with patch.object(retrieval_module.CapabilityRetriever, "retrieve", slow_retrieve):
        result = await engine.execute_async(context, operation)

    assert result.success is True
    assert retrieve_saw_prefetch_already_running.is_set()
    # Still exactly one real invocation of the read handler: the routing-time
    # prefetch result is consumed rather than dispatched a second time.
    assert calls == ["archive_read"]
