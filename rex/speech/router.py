"""``SpeechRouter`` -- the single provider-resolution layer for STT and TTS (S35).

Both the desktop voice pipeline and the authenticated mobile gateway resolve
providers through this class so provider-selection policy is never
duplicated per surface. The router never touches identity, authorization,
Assistant/TurnEngine state, or action verification -- it only turns audio
into text and text into audio through whichever provider policy currently
permits.
"""

from __future__ import annotations

from rex.speech.contracts import (
    ProviderHealth,
    SpeechToTextProvider,
    SynthesisResult,
    TextToSpeechProvider,
    TranscriptionResult,
)
from rex.speech.policy import ProviderCandidate, SpeechPolicy, resolve_provider_chain


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

    def transcribe_file(self, path: str) -> TranscriptionResult:
        """Resolve an STT provider and transcribe an audio file end to end."""
        provider = self.resolve_stt_provider()
        audio = provider.decode(path)
        text = provider.transcribe(audio)
        return TranscriptionResult(text=text, provider_id=provider.provider_id)

    def synthesize(self, text: str, voice: str | None = None) -> SynthesisResult:
        """Resolve a TTS provider, resolve the voice (including aliases), and synthesize."""
        provider = self.resolve_tts_provider()
        voice_id = provider.resolve_voice(voice)
        audio = provider.synthesize(text, voice_id)
        return SynthesisResult(
            audio=audio,
            mime_type=provider.mime_type(),
            provider_id=provider.provider_id,
            voice_id=voice_id,
        )

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
