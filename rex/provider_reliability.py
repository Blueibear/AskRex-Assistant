"""Privacy-safe provider reliability signals for ModelRouter decisions.

Only bounded operational metadata is retained. Prompt text, responses, user
identity, credentials, and exception messages are never accepted or stored.
"""

from __future__ import annotations

import math
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

_PROVIDER_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,47}$")
_MAX_COUNT = 1_000_000


class ProviderFailureKind(StrEnum):
    AUTH = "auth"
    RATE_LIMIT = "rate_limit"
    TRANSIENT = "transient"
    UNAVAILABLE = "unavailable"
    MODEL_NOT_FOUND = "model_not_found"
    UNKNOWN = "unknown"


def _provider_id(value: str) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if _PROVIDER_ID.fullmatch(normalized) else "unknown"


def _bounded_latency(value: float | int | None, maximum: float) -> float | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    return round(max(0.0, min(maximum, numeric)), 3)


def _inc(value: int) -> int:
    return min(_MAX_COUNT, value + 1)


@dataclass(slots=True)
class _ProviderRecord:
    provider: str
    attempts: int = 0
    successes: int = 0
    failures: int = 0
    rate_limits: int = 0
    consecutive_failures: int = 0
    latency_ms: float | None = None
    last_failure: ProviderFailureKind | None = None
    cooldown_until: float = 0.0


@dataclass(frozen=True, slots=True)
class ProviderStatus:
    provider: str
    state: str
    available: bool
    reason: str | None
    latency_ms: float | None
    attempts: int
    successes: int
    failures: int
    rate_limits: int
    consecutive_failures: int
    cooldown_remaining_s: int

    def to_metadata(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "state": self.state,
            "available": self.available,
            "reason": self.reason,
            "latency_ms": self.latency_ms,
            "attempts": self.attempts,
            "successes": self.successes,
            "failures": self.failures,
            "rate_limits": self.rate_limits,
            "consecutive_failures": self.consecutive_failures,
            "cooldown_remaining_s": self.cooldown_remaining_s,
        }


class ProviderReliability:
    """Keep bounded provider health evidence without request/private content."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        cooldown_seconds: int = 3600,
        max_latency_ms: float = 120_000.0,
    ) -> None:
        self._clock = clock
        self._cooldown_seconds = max(0, int(cooldown_seconds))
        self._max_latency_ms = max(1.0, float(max_latency_ms))
        self._records: dict[str, _ProviderRecord] = {}

    def _record(self, provider: str) -> _ProviderRecord:
        key = _provider_id(provider)
        record = self._records.get(key)
        if record is None:
            record = _ProviderRecord(provider=key)
            self._records[key] = record
        return record

    def record_success(self, provider: str, *, latency_ms: float | int | None = None) -> None:
        record = self._record(provider)
        record.attempts = _inc(record.attempts)
        record.successes = _inc(record.successes)
        record.consecutive_failures = 0
        record.last_failure = None
        record.cooldown_until = 0.0
        bounded = _bounded_latency(latency_ms, self._max_latency_ms)
        if bounded is not None:
            record.latency_ms = bounded

    def record_failure(
        self,
        provider: str,
        kind: ProviderFailureKind | str,
        *,
        latency_ms: float | int | None = None,
        cooldown_seconds: int | None = None,
    ) -> None:
        record = self._record(provider)
        try:
            failure = ProviderFailureKind(str(kind))
        except ValueError:
            failure = ProviderFailureKind.UNKNOWN
        record.attempts = _inc(record.attempts)
        record.failures = _inc(record.failures)
        record.consecutive_failures = _inc(record.consecutive_failures)
        record.last_failure = failure
        if failure is ProviderFailureKind.RATE_LIMIT:
            record.rate_limits = _inc(record.rate_limits)
        bounded = _bounded_latency(latency_ms, self._max_latency_ms)
        if bounded is not None:
            record.latency_ms = bounded

        should_cooldown = failure in {
            ProviderFailureKind.AUTH,
            ProviderFailureKind.RATE_LIMIT,
            ProviderFailureKind.UNAVAILABLE,
            ProviderFailureKind.MODEL_NOT_FOUND,
        }
        if should_cooldown:
            seconds = (
                self._cooldown_seconds
                if cooldown_seconds is None
                else max(0, int(cooldown_seconds))
            )
            record.cooldown_until = max(record.cooldown_until, self._clock() + seconds)

    def status(self, provider: str) -> ProviderStatus:
        key = _provider_id(provider)
        record = self._records.get(key)
        if record is None:
            return ProviderStatus(
                provider=key,
                state="unknown",
                available=True,
                reason=None,
                latency_ms=None,
                attempts=0,
                successes=0,
                failures=0,
                rate_limits=0,
                consecutive_failures=0,
                cooldown_remaining_s=0,
            )

        remaining = max(0.0, record.cooldown_until - self._clock())
        # Normalize sub-nanosecond binary-float noise before rounding up for diagnostics.
        display_remaining = round(remaining, 9)
        in_cooldown = remaining > 0
        if in_cooldown:
            state = "cooldown"
        elif record.last_failure is not None or record.failures:
            state = "degraded"
        else:
            state = "healthy"
        return ProviderStatus(
            provider=record.provider,
            state=state,
            available=not in_cooldown,
            reason=record.last_failure.value if record.last_failure is not None else None,
            latency_ms=record.latency_ms,
            attempts=record.attempts,
            successes=record.successes,
            failures=record.failures,
            rate_limits=record.rate_limits,
            consecutive_failures=record.consecutive_failures,
            cooldown_remaining_s=int(math.ceil(display_remaining)),
        )

    def diagnostics(self) -> list[dict[str, object]]:
        return [self.status(provider).to_metadata() for provider in sorted(self._records)]


def classify_provider_failure(exc: BaseException) -> ProviderFailureKind:
    """Classify a provider exception without retaining its message or payload."""
    status = getattr(exc, "status_code", None)
    if not isinstance(status, int):
        response = getattr(exc, "response", None)
        candidate = getattr(response, "status_code", None)
        status = candidate if isinstance(candidate, int) else None
    if status in {401, 403}:
        return ProviderFailureKind.AUTH
    if status in {402, 429}:
        return ProviderFailureKind.RATE_LIMIT
    if status == 404:
        return ProviderFailureKind.MODEL_NOT_FOUND
    if status in {408, 409, 425} or (isinstance(status, int) and status >= 500):
        return ProviderFailureKind.TRANSIENT

    if isinstance(exc, (ConnectionError, TimeoutError)):
        return ProviderFailureKind.UNAVAILABLE
    name = type(exc).__name__.lower()
    if "timeout" in name or "connection" in name or "connect" in name:
        return ProviderFailureKind.UNAVAILABLE
    return ProviderFailureKind.UNKNOWN


__all__ = [
    "ProviderFailureKind",
    "ProviderReliability",
    "ProviderStatus",
    "classify_provider_failure",
]
