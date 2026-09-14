"""``SpeechRouter`` -- the single provider-resolution layer for STT and TTS (S35).

Both the desktop voice pipeline and the authenticated mobile gateway resolve
providers through this class so provider-selection policy is never
duplicated per surface. The router never touches identity, authorization,
Assistant/TurnEngine state, or action verification -- it only turns audio
into text and text into audio through whichever provider policy currently
permits.
"""

from __future__ import annotations

from typing import Any

from rex.speech.contracts import (
    ProviderHealth,
    SpeechProviderResponseError,
    SpeechProviderTimeoutError,
    SpeechProviderUnavailableError,
    SpeechToTextProvider,
    SynthesisResult,
    TextToSpeechProvider,
    TranscriptionResult,
)
from rex.speech.policy import (
    ProviderCandidate,
    SpeechPolicy,
    SpeechPolicyError,
    resolve_provider_chain,
    resolve_provider_fallback_chain,
)

_RECOVERABLE_PROVIDER_ERRORS = (
    SpeechProviderTimeoutError,
    SpeechProviderUnavailableError,
    SpeechProviderResponseError,
)


def _candidates(
    providers: dict[str, SpeechToTextProvider] | dict[str, TextToSpeechProvider],
) -> dict[str, ProviderCandidate]:
    candidates: dict[str, ProviderCandidate] = {}
    for provider_id, provider in providers.items():
        available, reason = provider.availability()
        candidates[provider_id] = ProviderCandidate(
            provider_id=provider_id,
            is_local=bool(provider.is_local),
            available=available,
            reason=reason,
        )
    return candidates


class SpeechRouter:
    """Provider-neutral STT/TTS router bound to one explicit policy."""

    def __init__(
        self,
        *,
        stt_providers: dict[str, SpeechToTextProvider],
        tts_providers: dict[str, TextToSpeechProvider],
        policy: SpeechPolicy,
    ) -> None:
        self._stt_providers = dict(stt_providers)
        self._tts_providers = dict(tts_providers)
        self.policy = policy

    def resolve_stt_provider(self) -> SpeechToTextProvider:
        candidates = _candidates(self._stt_providers)
        provider_id = resolve_provider_chain(
            mode=self.policy.mode,
            explicit_provider=self.policy.stt_provider,
            fallback_order=self.policy.stt_fallback_order,
            allow_cloud=self.policy.allow_cloud,
            candidates=candidates,
        )
        return self._stt_providers[provider_id]

    def resolve_tts_provider(self) -> TextToSpeechProvider:
        candidates = _candidates(self._tts_providers)
        provider_id = resolve_provider_chain(
            mode=self.policy.mode,
            explicit_provider=self.policy.tts_provider,
            fallback_order=self.policy.tts_fallback_order,
            allow_cloud=self.policy.allow_cloud,
            candidates=candidates,
        )
        return self._tts_providers[provider_id]

    def resolve_stt_fallback_chain(self) -> list[SpeechToTextProvider]:
        """Return every STT provider policy permits, in priority order.

        Used to retry the next permitted provider when the primary
        resolution passes its health check but then fails or times out
        once actually invoked, instead of leaving the caller stuck on a
        single provider that has just become unavailable.
        """
        return self._fallback_chain(
            self._stt_providers, self.policy.stt_provider, self.policy.stt_fallback_order
        )

    def resolve_tts_fallback_chain(self) -> list[TextToSpeechProvider]:
        """Return every TTS provider policy permits, in priority order."""
        return self._fallback_chain(
            self._tts_providers, self.policy.tts_provider, self.policy.tts_fallback_order
        )

    def _fallback_chain(
        self,
        providers: dict[str, SpeechToTextProvider] | dict[str, TextToSpeechProvider],
        explicit_provider: str | None,
        fallback_order: tuple[str, ...],
    ) -> list[Any]:
        candidates = _candidates(providers)
        provider_ids = resolve_provider_fallback_chain(
            mode=self.policy.mode,
            explicit_provider=explicit_provider,
            fallback_order=fallback_order,
            allow_cloud=self.policy.allow_cloud,
            candidates=candidates,
        )
        available_ids = [pid for pid in provider_ids if candidates[pid].available]
        if not available_ids:
            # Reuses resolve_provider_chain purely to raise the same truthful,
            # mode-aware SpeechPolicyError message an empty chain implies.
            resolve_provider_chain(
                mode=self.policy.mode,
                explicit_provider=explicit_provider,
                fallback_order=fallback_order,
                allow_cloud=self.policy.allow_cloud,
                candidates=candidates,
            )
        return [providers[pid] for pid in available_ids]

    def transcribe_file(self, path: str) -> TranscriptionResult:
        """Resolve permitted STT providers and transcribe, retrying the next
        permitted provider if one fails or times out at call time."""
        chain = self.resolve_stt_fallback_chain()
        last_error: Exception | None = None
        for provider in chain:
            try:
                audio = provider.decode(path)
                text = provider.transcribe(audio)
                return TranscriptionResult(text=text, provider_id=provider.provider_id)
            except _RECOVERABLE_PROVIDER_ERRORS as exc:
                last_error = exc
                continue
        raise SpeechPolicyError(
            f"All permitted speech-to-text providers failed; last error: {last_error}"
        ) from last_error

    def synthesize(self, text: str, voice: str | None = None) -> SynthesisResult:
        """Resolve permitted TTS providers, resolve the voice (including
        aliases), and synthesize, retrying the next permitted provider if
        one fails or times out at call time."""
        chain = self.resolve_tts_fallback_chain()
        last_error: Exception | None = None
        for provider in chain:
            try:
                # The requested (possibly aliased) voice is resolved again for
                # every attempted provider, and the MIME type is read from the
                # same provider, so a fallback never reuses another provider's
                # voice ID or mislabels the audio it returns.
                voice_id = provider.resolve_voice(voice)
                mime_type = provider.mime_type()
                audio = provider.synthesize(text, voice_id)
                return SynthesisResult(
                    audio=audio,
                    mime_type=mime_type,
                    provider_id=provider.provider_id,
                    voice_id=voice_id,
                )
            except _RECOVERABLE_PROVIDER_ERRORS as exc:
                last_error = exc
                continue
        raise SpeechPolicyError(
            f"All permitted text-to-speech providers failed; last error: {last_error}"
        ) from last_error

    def stt_health(self) -> list[ProviderHealth]:
        return [
            ProviderHealth(
                provider_id=pid, available=c.available, reason=c.reason, is_local=c.is_local
            )
            for pid, c in _candidates(self._stt_providers).items()
        ]

    def tts_health(self) -> list[ProviderHealth]:
        return [
            ProviderHealth(
                provider_id=pid, available=c.available, reason=c.reason, is_local=c.is_local
            )
            for pid, c in _candidates(self._tts_providers).items()
        ]


__all__ = ["SpeechRouter", "SynthesisResult", "TranscriptionResult"]
