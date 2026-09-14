"""Normalize legacy speech failures into recoverable provider errors (S35).

Provider wrappers are the boundary between AskRex's pre-existing speech
stack (which raises ``MobileApiError``/``SpeechToTextError``/engine
exceptions) and the provider-neutral contracts in
:mod:`rex.speech.contracts`. Ordered fallback in
:class:`~rex.speech.router.SpeechRouter` and the routed adapters recovers
only from ``SpeechProvider*`` errors, so a legacy failure that escapes a
wrapper unchanged would strand the turn on one provider instead of trying
the next permitted one.

Two failure classes are deliberately never normalized:

- cancellation/interpreter-exit signals, which must propagate immediately
  so a cancelled turn is never retried against another provider;
- client-input truth (HTTP 4xx ``MobileApiError`` such as ``INVALID_MEDIA``
  or ``PAYLOAD_TOO_LARGE``), because undecodable or oversized audio is not
  a provider outage and must not be relabeled as one or retried elsewhere.

Normalized messages carry only bounded, content-free diagnostics (exception
type names and the fixed mobile error strings) -- never transcripts, prompts,
audio, voice samples, or credentials.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Iterator
from typing import Any

from rex.speech.contracts import (
    SpeechProviderError,
    SpeechProviderTimeoutError,
    SpeechProviderUnavailableError,
)

# Control-flow signals that must never become a recoverable provider error.
_NEVER_NORMALIZED: tuple[type[BaseException], ...] = (
    KeyboardInterrupt,
    SystemExit,
    asyncio.CancelledError,
)


def _is_cancellation(exc: BaseException) -> bool:
    try:
        from rex.runtime.cancellation import TurnCancelledError  # noqa: PLC0415
    except Exception:  # pragma: no cover - runtime package is always present
        return False
    return isinstance(exc, TurnCancelledError)


def _as_mobile_api_error(exc: BaseException) -> Any | None:
    try:
        from rex.mobile_api.errors import MobileApiError  # noqa: PLC0415
    except Exception:  # pragma: no cover - mobile_api is always importable
        return None
    return exc if isinstance(exc, MobileApiError) else None


def normalize_legacy_error(exc: BaseException, *, context: str) -> BaseException:
    """Return the ``SpeechProvider*`` equivalent of ``exc``, or ``exc`` itself.

    ``context`` is a bounded provider/operation label such as
    ``"native speech-to-text"``; it is the only caller-supplied text that
    reaches the normalized message.
    """
    if isinstance(exc, SpeechProviderError) or isinstance(exc, _NEVER_NORMALIZED):
        return exc
    if _is_cancellation(exc):
        return exc

    mobile_error = _as_mobile_api_error(exc)
    if mobile_error is not None:
        if mobile_error.http_status < 500:
            # Client-input truth: not a provider failure.
            return exc
        return SpeechProviderUnavailableError(f"{context} failed: {mobile_error.message}")

    if isinstance(exc, TimeoutError):
        return SpeechProviderTimeoutError(f"{context} timed out")
    if isinstance(exc, Exception):
        return SpeechProviderUnavailableError(f"{context} failed: {type(exc).__name__}")
    return exc


@contextlib.contextmanager
def normalized_provider_errors(context: str) -> Iterator[None]:
    """Run a legacy speech call, re-raising its failure as a provider error."""
    try:
        yield
    except BaseException as exc:
        normalized = normalize_legacy_error(exc, context=context)
        if normalized is exc:
            raise
        raise normalized from exc


__all__ = ["normalize_legacy_error", "normalized_provider_errors"]
