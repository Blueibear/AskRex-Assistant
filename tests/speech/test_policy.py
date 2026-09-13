"""Explicit SpeechRouter policy resolution tests (S35)."""

from __future__ import annotations

import pytest

from rex.speech.policy import (
    ProviderCandidate,
    SpeechPolicyError,
    SpeechPolicyMode,
    resolve_provider_chain,
)


def _candidate(provider_id: str, *, is_local: bool, available: bool = True) -> ProviderCandidate:
    return ProviderCandidate(provider_id=provider_id, is_local=is_local, available=available)


class TestResolveProviderChain:
    def test_no_candidates_raises(self) -> None:
        with pytest.raises(SpeechPolicyError):
            resolve_provider_chain(
                mode=SpeechPolicyMode.AUTOMATIC,
                explicit_provider=None,
                fallback_order=(),
                allow_cloud=False,
                candidates={},
            )

    def test_prefer_local_picks_local_over_cloud(self) -> None:
        candidates = {
            "cloud": _candidate("cloud", is_local=False),
            "native": _candidate("native", is_local=True),
        }
        chosen = resolve_provider_chain(
            mode=SpeechPolicyMode.PREFER_LOCAL,
            explicit_provider=None,
            fallback_order=(),
            allow_cloud=True,
            candidates=candidates,
        )
        assert chosen == "native"

    def test_prefer_cloud_picks_cloud_when_permitted(self) -> None:
        candidates = {
            "cloud": _candidate("cloud", is_local=False),
            "native": _candidate("native", is_local=True),
        }
        chosen = resolve_provider_chain(
            mode=SpeechPolicyMode.PREFER_CLOUD,
            explicit_provider=None,
            fallback_order=(),
            allow_cloud=True,
            candidates=candidates,
        )
        assert chosen == "cloud"

    def test_local_only_never_selects_cloud_even_if_available(self) -> None:
        candidates = {"cloud": _candidate("cloud", is_local=False)}
        with pytest.raises(SpeechPolicyError, match="Local Only"):
            resolve_provider_chain(
                mode=SpeechPolicyMode.LOCAL_ONLY,
                explicit_provider=None,
                fallback_order=(),
                allow_cloud=True,
                candidates=candidates,
            )

    def test_local_only_selects_local_when_available(self) -> None:
        candidates = {
            "cloud": _candidate("cloud", is_local=False),
            "native": _candidate("native", is_local=True),
        }
        chosen = resolve_provider_chain(
            mode=SpeechPolicyMode.LOCAL_ONLY,
            explicit_provider=None,
            fallback_order=(),
            allow_cloud=False,
            candidates=candidates,
        )
        assert chosen == "native"

    def test_cloud_blocked_without_explicit_permission(self) -> None:
        candidates = {"cloud": _candidate("cloud", is_local=False)}
        with pytest.raises(SpeechPolicyError, match="not permitted"):
            resolve_provider_chain(
                mode=SpeechPolicyMode.AUTOMATIC,
                explicit_provider=None,
                fallback_order=(),
                allow_cloud=False,
                candidates=candidates,
            )

    def test_custom_mode_uses_explicit_provider_first(self) -> None:
        candidates = {
            "native": _candidate("native", is_local=True),
            "voicestudio": _candidate("voicestudio", is_local=True),
        }
        chosen = resolve_provider_chain(
            mode=SpeechPolicyMode.CUSTOM,
            explicit_provider="voicestudio",
            fallback_order=("native",),
            allow_cloud=False,
            candidates=candidates,
        )
        assert chosen == "voicestudio"

    def test_custom_mode_falls_back_when_explicit_unavailable(self) -> None:
        candidates = {
            "native": _candidate("native", is_local=True, available=True),
            "voicestudio": _candidate("voicestudio", is_local=True, available=False),
        }
        chosen = resolve_provider_chain(
            mode=SpeechPolicyMode.CUSTOM,
            explicit_provider="voicestudio",
            fallback_order=("native",),
            allow_cloud=False,
            candidates=candidates,
        )
        assert chosen == "native"

    def test_unknown_fallback_entries_are_dropped_not_fatal(self) -> None:
        candidates = {"native": _candidate("native", is_local=True)}
        chosen = resolve_provider_chain(
            mode=SpeechPolicyMode.CUSTOM,
            explicit_provider="stale-provider",
            fallback_order=("also-stale", "native"),
            allow_cloud=False,
            candidates=candidates,
        )
        assert chosen == "native"

    def test_unavailable_candidate_is_skipped(self) -> None:
        candidates = {
            "native": _candidate("native", is_local=True, available=False),
        }
        with pytest.raises(SpeechPolicyError):
            resolve_provider_chain(
                mode=SpeechPolicyMode.AUTOMATIC,
                explicit_provider=None,
                fallback_order=(),
                allow_cloud=False,
                candidates=candidates,
            )
