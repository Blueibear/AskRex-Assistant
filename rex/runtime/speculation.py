"""Bounded, cancellable speculative read-only capability prefetch (US-101).

Speculation never grants authority. A candidate is eligible only when it is
currently healthy, enabled, explicitly ``operation="read"``/``risk="safe"``,
and authorized for the requesting identity's current permission snapshot.
Prefetch runs under strict concurrency/time/resource budgets, inherits the
current turn's cancellation, and never bypasses confirmation because mutating
and sensitive/prohibited capabilities are filtered out before any dispatch
call is made. Results are transient: audit/telemetry retains only bounded
content-free metadata (capability ID, outcome, duration), never payloads, and
a prefetched result may be used for the final plan only after a fresh
identity/scope/freshness/eligibility recheck.
"""

from __future__ import annotations

import concurrent.futures
import contextvars
import json
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from rex.capabilities.registry import CapabilityRegistry
from rex.runtime.cancellation import TurnCancelledError, current_turn_cancellation
from rex.tools.protocol import ToolDispatcherProtocol, ToolResult

logger = logging.getLogger(__name__)

_ELIGIBLE_OPERATION = "read"
_ELIGIBLE_RISK = "safe"
_REQUIRED_HEALTH = "healthy"


@dataclass(frozen=True)
class SpeculationBudget:
    """Strict, validated concurrency/time/resource bounds for one prefetch call."""

    max_concurrency: int = 2
    max_candidates: int = 3
    total_timeout_seconds: float = 0.2
    max_age_seconds: float = 2.0
    max_result_bytes: int = 65_536

    def __post_init__(self) -> None:
        if self.max_concurrency < 1:
            raise ValueError("max_concurrency must be at least 1")
        if self.max_candidates < 1:
            raise ValueError("max_candidates must be at least 1")
        if self.total_timeout_seconds <= 0:
            raise ValueError("total_timeout_seconds must be positive")
        if self.max_age_seconds <= 0:
            raise ValueError("max_age_seconds must be positive")
        if self.max_result_bytes < 1:
            raise ValueError("max_result_bytes must be at least 1")


@dataclass(frozen=True)
class SpeculativeAttempt:
    """Content-free audit/timing evidence for one speculative candidate."""

    capability_id: str
    outcome: str
    duration_ms: float


@dataclass(frozen=True)
class SpeculativeResult:
    """A transient, not-yet-authoritative prefetched read result."""

    capability_id: str
    tool_result: ToolResult
    user_id: str
    scope: str
    captured_monotonic: float = field(default_factory=time.monotonic)


@dataclass(frozen=True)
class SpeculationOutcome:
    """Bounded prefetch evidence: usable transient results plus a content-free audit trail."""

    results: dict[str, SpeculativeResult]
    attempts: tuple[SpeculativeAttempt, ...]

    def get(self, capability_id: str) -> SpeculativeResult | None:
        return self.results.get(capability_id)


@dataclass
class PrefetchHandle:
    """Non-blocking token returned by :meth:`SpeculativePrefetcher.begin`.

    Background dispatch for eligible candidates is already running (or
    already resolved as skipped/cancelled) by the time this is returned;
    :meth:`SpeculativePrefetcher.finish` waits only for whatever remains of
    the *original* budget deadline recorded here, so a caller may keep doing
    other routing/turn work in between without extending the bound.
    """

    futures: dict[Any, str]
    deadline: float
    user_id: str
    scope: str
    pre_attempts: tuple[SpeculativeAttempt, ...] = ()


class SpeculativePrefetcher:
    """Speculatively dispatch only healthy, permitted, read-only/low-risk candidates."""

    def __init__(
        self,
        registry: CapabilityRegistry,
        dispatcher: ToolDispatcherProtocol,
        *,
        budget: SpeculationBudget | None = None,
    ) -> None:
        self._registry = registry
        self._dispatcher = dispatcher
        self._budget = budget or SpeculationBudget()
        # One persistent, fixed-size pool owned by this instance bounds total
        # background dispatch work to `max_concurrency` threads for the life
        # of the prefetcher, instead of creating (and abandoning) a fresh pool
        # object per call whenever candidates are still running at timeout.
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=self._budget.max_concurrency,
            thread_name_prefix="rex-speculative-prefetch",
        )

    def eligible(
        self,
        capability_id: str,
        *,
        user_id: str | None,
        granted_permissions: frozenset[str] | set[str],
    ) -> bool:
        """Fail closed unless the candidate is currently a safe, permitted read."""
        capability = self._registry.get(capability_id)
        if capability is None:
            return False
        if not capability.enabled:
            return False
        if capability.health != _REQUIRED_HEALTH:
            return False
        if capability.integration_state in {"unavailable", "unconfigured"}:
            return False
        if capability.operation != _ELIGIBLE_OPERATION:
            return False
        if capability.risk != _ELIGIBLE_RISK:
            return False
        if capability.requires_identity and not user_id:
            return False
        return self._registry.is_authorized(capability_id, frozenset(granted_permissions))

    def begin(
        self,
        candidate_ids: Sequence[str],
        *,
        user_id: str,
        scope: str,
        granted_permissions: frozenset[str] | set[str],
        args_by_capability: dict[str, dict[str, Any]] | None = None,
    ) -> PrefetchHandle:
        """Submit eligible candidates to the bounded pool without blocking.

        The returned handle's deadline is fixed at call time; the caller may
        keep resolving other routing work and later call :meth:`finish` to
        collect whatever completed within the remainder of that same
        deadline. Each submitted candidate's own dispatch call is bounded to
        (at most) that same deadline via ``_speculative_timeout_seconds``, so
        a slow/hung handler cannot occupy this bounded pool past the budget
        merely because the caller stopped waiting on it.
        """
        granted = frozenset(granted_permissions)
        args_by_capability = args_by_capability or {}
        pre_attempts: list[SpeculativeAttempt] = []
        deadline = time.monotonic() + self._budget.total_timeout_seconds

        cancellation = current_turn_cancellation()
        if cancellation is not None and cancellation.cancelled:
            return PrefetchHandle(
                futures={},
                deadline=deadline,
                user_id=user_id,
                scope=scope,
                pre_attempts=tuple(
                    SpeculativeAttempt(capability_id, "skipped_cancelled", 0.0)
                    for capability_id in candidate_ids
                ),
            )

        eligible_ids: list[str] = []
        for capability_id in candidate_ids:
            if len(eligible_ids) >= self._budget.max_candidates:
                pre_attempts.append(SpeculativeAttempt(capability_id, "skipped_budget", 0.0))
                continue
            if not self.eligible(capability_id, user_id=user_id, granted_permissions=granted):
                pre_attempts.append(SpeculativeAttempt(capability_id, "skipped_ineligible", 0.0))
                continue
            eligible_ids.append(capability_id)

        if not eligible_ids:
            return PrefetchHandle(
                futures={},
                deadline=deadline,
                user_id=user_id,
                scope=scope,
                pre_attempts=tuple(pre_attempts),
            )

        def _run(capability_id: str) -> tuple[ToolResult | None, str, float]:
            started = time.monotonic()
            try:
                if cancellation is not None:
                    cancellation.raise_if_cancelled()
                # Bound the underlying dispatch itself to whatever remains of
                # this handle's deadline. Without this, a timed-out/abandoned
                # candidate keeps its ToolExecutionLifecycle handler running
                # under the dispatcher's ordinary (much longer) timeout,
                # occupying this bounded pool well past the speculation
                # budget declared above.
                # Clamp explicitly to the declared budget as well as the
                # deadline remainder.  Floating-point subtraction can be a
                # few picoseconds larger than the original budget, and this
                # value is passed to the lifecycle as an enforceable timeout.
                remaining = min(
                    self._budget.total_timeout_seconds,
                    max(0.001, deadline - time.monotonic()),
                )
                result = self._dispatcher.dispatch(
                    capability_id,
                    dict(args_by_capability.get(capability_id, {})),
                    {
                        "speculative": True,
                        "user_id": user_id,
                        "_speculative_timeout_seconds": remaining,
                        # ToolExecutionLifecycle normally uses a private
                        # executor per call.  That permits an uncooperative
                        # handler to outlive its lifecycle timeout while this
                        # pool slot is reused.  Keep speculative handler work
                        # on this fixed pool instead, so actual handler
                        # concurrency cannot exceed max_concurrency.
                        "_speculative_retain_worker": True,
                        "_speculative_deadline_monotonic": deadline,
                    },
                )
                return result, "completed", (time.monotonic() - started) * 1000
            except TurnCancelledError:
                return None, "cancelled", (time.monotonic() - started) * 1000
            except Exception as exc:
                # Content-free by construction: only the bounded capability ID
                # and exception class name are logged, never the exception
                # message, args, or a traceback, any of which may embed
                # private request/result payload content.
                logger.warning(
                    "speculative_prefetch: candidate %s raised %s during dispatch",
                    capability_id,
                    type(exc).__name__,
                )
                return None, "failed", (time.monotonic() - started) * 1000

        futures = {
            self._executor.submit(
                contextvars.copy_context().run, _run, capability_id
            ): capability_id
            for capability_id in eligible_ids
        }
        return PrefetchHandle(
            futures=futures,
            deadline=deadline,
            user_id=user_id,
            scope=scope,
            pre_attempts=tuple(pre_attempts),
        )

    def finish(self, handle: PrefetchHandle) -> SpeculationOutcome:
        """Collect whatever completed within the remainder of the original budget.

        Never waits past ``handle.deadline``, regardless of how long ago
        :meth:`begin` was called; a candidate still running at that point is
        abandoned here exactly as it always was, while the dispatch call
        itself is independently bounded to the same deadline (see
        :meth:`begin`) so it cannot keep occupying the bounded pool.
        """
        attempts: list[SpeculativeAttempt] = list(handle.pre_attempts)
        results: dict[str, SpeculativeResult] = {}
        if not handle.futures:
            return SpeculationOutcome(results=results, attempts=tuple(attempts))

        remaining_wait = max(0.0, handle.deadline - time.monotonic())
        done, not_done = concurrent.futures.wait(list(handle.futures), timeout=remaining_wait)
        for future in done:
            capability_id = handle.futures[future]
            tool_result, outcome, duration_ms = future.result()
            if outcome == "completed" and tool_result is not None and tool_result.success:
                if self._result_size_bytes(tool_result) <= self._budget.max_result_bytes:
                    results[capability_id] = SpeculativeResult(
                        capability_id=capability_id,
                        tool_result=tool_result,
                        user_id=handle.user_id,
                        scope=handle.scope,
                    )
                else:
                    outcome = "discarded_result_budget"
            attempts.append(SpeculativeAttempt(capability_id, outcome, round(duration_ms, 3)))
        cancellation = current_turn_cancellation()
        now_cancelled = cancellation is not None and cancellation.cancelled
        for future in not_done:
            # Abandoned candidates (timeout/cancelled) may still be running in
            # the shared bounded pool; never block the caller waiting for them.
            # `cancel()` is best-effort (it only drops futures that have not
            # yet started), but the fixed-size pool still caps how many such
            # candidates can ever run concurrently for this instance, and the
            # dispatch call each one is running is itself bounded to
            # `handle.deadline` (see `begin`), so it frees the pool promptly
            # instead of occupying it for the dispatcher's ordinary timeout.
            future.cancel()
            capability_id = handle.futures[future]
            outcome = "cancelled" if now_cancelled else "timeout"
            attempts.append(
                SpeculativeAttempt(capability_id, outcome, round(remaining_wait * 1000, 3))
            )

        return SpeculationOutcome(results=results, attempts=tuple(attempts))

    @staticmethod
    def _result_size_bytes(result: ToolResult) -> int:
        """Measure retained output without logging or preserving its content."""
        try:
            encoded = json.dumps(result.output, default=lambda _value: None).encode("utf-8")
        except (TypeError, ValueError, OverflowError):
            return 2**63 - 1
        return len(encoded)

    def prefetch(
        self,
        candidate_ids: Sequence[str],
        *,
        user_id: str,
        scope: str,
        granted_permissions: frozenset[str] | set[str],
        args_by_capability: dict[str, dict[str, Any]] | None = None,
    ) -> SpeculationOutcome:
        """Dispatch only eligible candidates, bounded by the configured budget.

        Convenience wrapper equivalent to ``finish(begin(...))`` for callers
        that have no other routing work to overlap the wait with.
        """
        handle = self.begin(
            candidate_ids,
            user_id=user_id,
            scope=scope,
            granted_permissions=granted_permissions,
            args_by_capability=args_by_capability,
        )
        return self.finish(handle)

    def close(self) -> None:
        """Release the bounded background executor (explicit teardown)."""
        self._executor.shutdown(wait=False, cancel_futures=True)

    def consume(
        self,
        result: SpeculativeResult | None,
        *,
        user_id: str,
        scope: str,
        granted_permissions: frozenset[str] | set[str],
    ) -> ToolResult | None:
        """Revalidate identity/scope/freshness/eligibility before final-plan use."""
        if result is None:
            return None
        if result.user_id != user_id or result.scope != scope:
            return None
        age_seconds = time.monotonic() - result.captured_monotonic
        if age_seconds < 0 or age_seconds > self._budget.max_age_seconds:
            return None
        if not self.eligible(
            result.capability_id, user_id=user_id, granted_permissions=granted_permissions
        ):
            return None
        return result.tool_result


__all__ = [
    "PrefetchHandle",
    "SpeculationBudget",
    "SpeculationOutcome",
    "SpeculativeAttempt",
    "SpeculativePrefetcher",
    "SpeculativeResult",
]
