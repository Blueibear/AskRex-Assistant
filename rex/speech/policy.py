"""Explicit speech routing policy (S35).

The first implementation intentionally favors explicit provider selection
and ordered fallback over an elaborate optimizer. Cloud fallback is never
silent: a cloud-capable provider is only offered when ``allow_cloud`` is
true, and ``LOCAL_ONLY`` must fail rather than reach for a cloud provider
once permitted local providers are exhausted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class SpeechPolicyMode(str, Enum):
    """Ordered-fallback policy modes for STT/TTS provider selection."""

    AUTOMATIC = "automatic"
    PREFER_LOCAL = "prefer_local"
    LOCAL_ONLY = "local_only"
    PREFER_CLOUD = "prefer_cloud"
    CUSTOM = "custom"


class SpeechPolicyError(Exception):
    """Raised when policy resolution cannot select any provider."""


@dataclass(frozen=True)
class ProviderCandidate:
    """Bounded, content-free provider metadata used for policy resolution."""

    provider_id: str
    is_local: bool
    available: bool
    reason: str = "ok"


@dataclass(frozen=True)
class SpeechPolicy:
    """Explicit STT/TTS routing policy.

    ``stt_provider`` / ``tts_provider`` are the explicit provider selected in
    ``CUSTOM`` mode (or any mode's fallback order primary entry). Fallback
    orders are consulted only for candidates present in the caller's
    registered provider set.
    """

    mode: SpeechPolicyMode = SpeechPolicyMode.PREFER_LOCAL
    allow_cloud: bool = False
    stt_provider: str | None = None
    tts_provider: str | None = None
    stt_fallback_order: tuple[str, ...] = field(default_factory=tuple)
    tts_fallback_order: tuple[str, ...] = field(default_factory=tuple)


def _ordered_candidates(
    mode: SpeechPolicyMode,
    explicit_provider: str | None,
    fallback_order: tuple[str, ...],
    candidates: dict[str, ProviderCandidate],
) -> list[str]:
    """Return provider IDs in the order policy should try them.

    Unknown provider IDs (not present in ``candidates``) are dropped rather
    than raising, so a stale fallback entry never blocks resolution.
    """
    ordered: list[str] = []

    def _add(provider_id: str | None) -> None:
        if provider_id and provider_id in candidates and provider_id not in ordered:
            ordered.append(provider_id)

    if mode is SpeechPolicyMode.CUSTOM:
        _add(explicit_provider)
        for provider_id in fallback_order:
            _add(provider_id)
        return ordered

    _add(explicit_provider)
    for provider_id in fallback_order:
        _add(provider_id)

    remaining = [pid for pid in candidates if pid not in ordered]
    if mode is SpeechPolicyMode.PREFER_LOCAL or mode is SpeechPolicyMode.LOCAL_ONLY:
        remaining.sort(key=lambda pid: (not candidates[pid].is_local, pid))
    elif mode is SpeechPolicyMode.PREFER_CLOUD:
        remaining.sort(key=lambda pid: (candidates[pid].is_local, pid))
    else:  # AUTOMATIC
        remaining.sort()
    ordered.extend(remaining)
    return ordered


def resolve_provider_fallback_chain(
    *,
    mode: SpeechPolicyMode,
    explicit_provider: str | None,
    fallback_order: tuple[str, ...],
    allow_cloud: bool,
    candidates: dict[str, ProviderCandidate],
) -> list[str]:
    """Return every provider ID this policy permits, in priority order.

    Unlike :func:`resolve_provider_chain`, this is not truncated to the
    first available entry: it is the ordered set a caller may retry against
    if a provider that passed its health check nonetheless times out or
    fails once actually invoked. Local Only/cloud-permission filtering is
    still fully applied -- a provider excluded by policy is never included
    here regardless of its live availability.
    """
    if not candidates:
        return []
    ordered = _ordered_candidates(mode, explicit_provider, fallback_order, candidates)
    permitted: list[str] = []
    for provider_id in ordered:
        candidate = candidates[provider_id]
        if mode is SpeechPolicyMode.LOCAL_ONLY and not candidate.is_local:
            continue
        if not candidate.is_local and not allow_cloud:
            continue
        permitted.append(provider_id)
    return permitted


def resolve_provider_chain(
    *,
    mode: SpeechPolicyMode,
    explicit_provider: str | None,
    fallback_order: tuple[str, ...],
    allow_cloud: bool,
    candidates: dict[str, ProviderCandidate],
) -> str:
    """Resolve the single provider ID policy selects, or raise.

    Raises:
        SpeechPolicyError: No permitted, available candidate exists. The
            message is truthful about whether the cause was Local Only
            exhaustion, missing cloud permission, or no healthy provider.
    """
    if not candidates:
        raise SpeechPolicyError("No speech providers are registered.")

    ordered = _ordered_candidates(mode, explicit_provider, fallback_order, candidates)

    saw_cloud_blocked = False
    saw_unavailable = False
    for provider_id in ordered:
        candidate = candidates[provider_id]
        if mode is SpeechPolicyMode.LOCAL_ONLY and not candidate.is_local:
            continue
        if not candidate.is_local and not allow_cloud:
            saw_cloud_blocked = True
            continue
        if not candidate.available:
            saw_unavailable = True
            continue
        return provider_id

    if mode is SpeechPolicyMode.LOCAL_ONLY:
        raise SpeechPolicyError(
            "Local Only policy is active and no local speech provider is available."
        )
    if saw_cloud_blocked and not saw_unavailable:
        raise SpeechPolicyError(
            "No local speech provider is available and cloud processing is not permitted."
        )
    raise SpeechPolicyError("No speech provider is currently available.")


__all__ = [
    "ProviderCandidate",
    "SpeechPolicy",
    "SpeechPolicyError",
    "SpeechPolicyMode",
    "resolve_provider_chain",
    "resolve_provider_fallback_chain",
]
