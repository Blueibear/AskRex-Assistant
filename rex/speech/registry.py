"""Build the default :class:`~rex.speech.router.SpeechRouter` from ``AppConfig`` (S35).

This is the single place that turns persisted speech policy into a live
router. Both the authenticated mobile gateway and (future) desktop wiring
should build their router through this function rather than constructing
providers ad hoc, so provider registration never diverges by surface.
"""

from __future__ import annotations

from rex.config import AppConfig
from rex.speech.contracts import SpeechToTextProvider, TextToSpeechProvider
from rex.speech.policy import SpeechPolicy, SpeechPolicyMode
from rex.speech.providers.native import NativeSTTProvider, NativeTTSProvider
from rex.speech.providers.voicestudio import (
    VoiceStudioConfig,
    VoiceStudioSTTProvider,
    VoiceStudioTTSProvider,
)
from rex.speech.router import SpeechRouter


def build_voicestudio_config(app_config: AppConfig) -> VoiceStudioConfig:
    speech = app_config.speech
    return VoiceStudioConfig(
        base_url=speech.voicestudio_base_url,
        timeout_seconds=speech.voicestudio_timeout_seconds,
        api_key=app_config.voicestudio_api_key,
        stt_model=speech.voicestudio_stt_model,
        tts_model=speech.voicestudio_tts_model,
        default_voice=speech.voicestudio_default_voice,
        voice_aliases=dict(speech.voicestudio_voice_aliases),
    )


def build_speech_policy(app_config: AppConfig) -> SpeechPolicy:
    """Build the explicit :class:`SpeechPolicy` from persisted ``speech`` config."""
    speech = app_config.speech
    explicit_stt = speech.stt_provider if speech.stt_provider != "native" else None
    explicit_tts = speech.tts_provider if speech.tts_provider != "native" else None
    return SpeechPolicy(
        mode=SpeechPolicyMode(speech.policy_mode),
        allow_cloud=speech.allow_cloud,
        stt_provider=explicit_stt,
        tts_provider=explicit_tts,
        stt_fallback_order=tuple(speech.stt_fallback_order) or ("native",),
        tts_fallback_order=tuple(speech.tts_fallback_order) or ("native",),
    )


def build_default_router(app_config: AppConfig | None = None) -> SpeechRouter:
    """Build the default provider-neutral router from ``AppConfig``.

    Registers the native provider unconditionally (existing Whisper/XTTS/
    edge-tts/pyttsx3 stack, zero behavior change) and registers VoiceStudio
    only when ``speech.voicestudio_enabled`` -- disabled by default, which is
    the explicit rollback path.
    """
    from rex.config import settings as _default_settings  # noqa: PLC0415

    config = app_config or _default_settings

    stt_providers: dict[str, SpeechToTextProvider] = {"native": NativeSTTProvider()}
    tts_providers: dict[str, TextToSpeechProvider] = {"native": NativeTTSProvider()}

    if config.speech.voicestudio_enabled:
        vs_config = build_voicestudio_config(config)
        stt_providers["voicestudio"] = VoiceStudioSTTProvider(vs_config)
        tts_providers["voicestudio"] = VoiceStudioTTSProvider(vs_config)

    return SpeechRouter(
        stt_providers=stt_providers,
        tts_providers=tts_providers,
        policy=build_speech_policy(config),
    )


__all__ = ["build_default_router", "build_speech_policy", "build_voicestudio_config"]
