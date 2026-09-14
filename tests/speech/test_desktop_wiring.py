"""Desktop STT/TTS SpeechRouter wiring regression tests (S35).

These are the P0 regression proof: desktop must resolve its speech
provider through the same policy-enforcing ``SpeechRouter`` the
authenticated mobile gateway uses, and must never activate a non-native
provider merely because ``speech.stt_provider``/``speech.tts_provider``
happens to say so -- ``speech.enabled`` is the explicit, authoritative
opt-in/rollback switch, and ``allow_cloud``/``policy_mode`` (including
Local Only) must always be honored.
"""

from __future__ import annotations

import asyncio
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

from rex.config import SpeechConfig
from rex.speech.policy import SpeechPolicy, SpeechPolicyMode
from rex.speech.providers.native import NATIVE_PROVIDER_ID
from rex.speech.router import SpeechRouter


class FakeTTSProvider:
    def __init__(self, provider_id: str, *, is_local: bool = True) -> None:
        self.provider_id = provider_id
        self.is_local = is_local

    def availability(self) -> tuple[bool, str]:
        return True, "ok"


def _stub_wakeword_modules() -> dict[str, object | None]:
    """Stub ``rex.wakeword``/``rex.wakeword.listener`` so build_voice_loop's
    default wake-word activation import succeeds without optional audio deps.
    """
    mock_listener_mod = types.ModuleType("rex.wakeword.listener")
    mock_listener_mod.build_default_detector = MagicMock()  # type: ignore[attr-defined]
    mock_wakeword_mod = types.ModuleType("rex.wakeword")
    mock_wakeword_mod.listener = mock_listener_mod  # type: ignore[attr-defined]
    stub_modules: dict[str, object] = {
        "rex.wakeword": mock_wakeword_mod,
        "rex.wakeword.listener": mock_listener_mod,
    }
    original = {k: sys.modules.get(k) for k in stub_modules}
    sys.modules.update(stub_modules)
    return original


def _restore_modules(original: dict[str, object | None]) -> None:
    for name, module in original.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


def _setup_mock_settings(mock_settings, *, speech: SpeechConfig) -> None:
    mock_settings.audio_input_device = None
    mock_settings.wake_word_input_device = None
    mock_settings.acknowledgment_sound = "chime"
    mock_settings.tts_speed = 1.0
    mock_settings.tts_provider = "xtts"
    mock_settings.tts_voice = None
    mock_settings.tts_output_device = None
    mock_settings.use_openclaw_voice_backend = False
    mock_settings.speech = speech


class TestBuildVoiceLoopSTTRouting:
    def test_speech_disabled_by_default_never_builds_a_speech_router(self) -> None:
        """The explicit rollback path: no SpeechRouter is constructed at all."""
        original_modules = _stub_wakeword_modules()
        native_stt_instance = MagicMock()
        native_stt_instance.transcribe = AsyncMock(return_value="native-text")
        mock_tts = MagicMock()
        mock_tts._provider = "xtts"

        try:
            with (
                patch("rex.voice_loop.settings") as mock_settings,
                patch("rex.voice_loop._validate_input_device_index", return_value=0),
                patch("rex.voice_loop.AsyncMicrophone"),
                patch("rex.voice_loop.SpeechToText", return_value=native_stt_instance),
                patch("rex.voice_loop.TextToSpeech", return_value=mock_tts),
                patch("rex.voice_loop.WakeAcknowledgement"),
                patch("rex.voice_loop._build_voice_id_callback", return_value=None),
                patch("rex.voice_loop.VoiceLoop") as mock_voice_loop_cls,
                patch("rex.speech.registry.build_default_router") as mock_build_router,
            ):
                # Stale/leftover provider selection must not matter while disabled.
                _setup_mock_settings(
                    mock_settings,
                    speech=SpeechConfig(
                        enabled=False, stt_provider="voicestudio", voicestudio_enabled=True
                    ),
                )

                import rex.voice_loop as vl

                vl.build_voice_loop(MagicMock())

            mock_build_router.assert_not_called()
            transcribe_cb = mock_voice_loop_cls.call_args.kwargs["transcribe"]
            result = asyncio.run(transcribe_cb("fake-audio"))
            assert result == "native-text"
            native_stt_instance.transcribe.assert_awaited_once_with("fake-audio", 16000)
        finally:
            _restore_modules(original_modules)

    def test_speech_enabled_routes_through_speech_router_policy(self) -> None:
        """When enabled, resolution -- not a raw provider string -- decides the engine."""
        original_modules = _stub_wakeword_modules()
        native_stt_instance = MagicMock()
        native_stt_instance.transcribe = AsyncMock(
            side_effect=AssertionError(
                "native STT must not be used when policy resolves voicestudio"
            )
        )
        mock_tts = MagicMock()
        mock_tts._provider = "xtts"

        class FakeSTTProvider:
            provider_id = "voicestudio"
            is_local = True

            def availability(self) -> tuple[bool, str]:
                return True, "ok"

            def transcribe(self, audio: object) -> str:
                return "routed-voicestudio-text"

        fake_router = SpeechRouter(
            stt_providers={"voicestudio": FakeSTTProvider()},
            tts_providers={},
            policy=SpeechPolicy(
                mode=SpeechPolicyMode.CUSTOM, stt_provider="voicestudio", allow_cloud=True
            ),
        )

        try:
            with (
                patch("rex.voice_loop.settings") as mock_settings,
                patch("rex.voice_loop._validate_input_device_index", return_value=0),
                patch("rex.voice_loop.AsyncMicrophone"),
                patch("rex.voice_loop.SpeechToText", return_value=native_stt_instance),
                patch("rex.voice_loop.TextToSpeech", return_value=mock_tts),
                patch("rex.voice_loop.WakeAcknowledgement"),
                patch("rex.voice_loop._build_voice_id_callback", return_value=None),
                patch("rex.voice_loop.VoiceLoop") as mock_voice_loop_cls,
                patch("rex.speech.registry.build_default_router", return_value=fake_router),
                patch(
                    "rex.speech.desktop_adapter._to_wav_buffer",
                    return_value=b"RIFF0000WAVEfmt ",
                ),
            ):
                _setup_mock_settings(
                    mock_settings,
                    speech=SpeechConfig(
                        enabled=True,
                        voicestudio_enabled=True,
                        stt_provider="voicestudio",
                        policy_mode="custom",
                        allow_cloud=True,
                    ),
                )

                import rex.voice_loop as vl

                vl.build_voice_loop(MagicMock())

                transcribe_cb = mock_voice_loop_cls.call_args.kwargs["transcribe"]
                result = asyncio.run(transcribe_cb("fake-audio"))

            assert result == "routed-voicestudio-text"
            native_stt_instance.transcribe.assert_not_awaited()
        finally:
            _restore_modules(original_modules)


class TestTextToSpeechRouting:
    def test_speech_disabled_by_default_ignores_stale_voicestudio_provider(self) -> None:
        """P0 regression: a stale ``speech.tts_provider`` must never activate
        VoiceStudio while ``speech.enabled`` is false."""
        with (
            patch("rex.voice_loop._lazy_import_tts", return_value=None),
            patch("rex.voice_loop.settings") as mock_settings,
        ):
            mock_settings.tts_provider = "xtts"
            mock_settings.tts_voice = None
            mock_settings.tts_speed = 1.0
            mock_settings.speech = SpeechConfig(
                enabled=False, tts_provider="voicestudio", voicestudio_enabled=True
            )

            from rex.voice_loop import TextToSpeech

            tts = TextToSpeech(language="en")
            assert tts._speech_router is None
            assert tts._provider == "xtts"

    def test_speech_enabled_builds_router_from_settings(self) -> None:
        with (
            patch("rex.voice_loop._lazy_import_tts", return_value=None),
            patch("rex.voice_loop.settings") as mock_settings,
            patch("rex.speech.registry.build_default_router") as mock_build_router,
        ):
            mock_settings.tts_provider = "xtts"
            mock_settings.tts_voice = None
            mock_settings.tts_speed = 1.0
            mock_settings.speech = SpeechConfig(enabled=True)
            sentinel_router = object()
            mock_build_router.return_value = sentinel_router

            from rex.voice_loop import TextToSpeech

            tts = TextToSpeech(language="en")
            assert tts._speech_router is sentinel_router

    def test_speak_dispatches_to_voicestudio_only_via_router_resolution(self) -> None:
        """P0 regression: dispatch follows the resolved provider, not the flat field."""
        with (
            patch("rex.voice_loop._lazy_import_tts", return_value=None),
            patch("rex.voice_loop.settings") as mock_settings,
        ):
            mock_settings.tts_provider = "xtts"
            mock_settings.tts_voice = None
            mock_settings.tts_speed = 1.0
            mock_settings.tts_max_spoken_chars = 120
            mock_settings.tts_fast_short_reply_max_chars = 140
            mock_settings.speech = SpeechConfig(enabled=True)

            fake_native = FakeTTSProvider(NATIVE_PROVIDER_ID)
            fake_voicestudio = FakeTTSProvider("voicestudio")
            router = SpeechRouter(
                stt_providers={},
                tts_providers={
                    NATIVE_PROVIDER_ID: fake_native,
                    "voicestudio": fake_voicestudio,
                },
                policy=SpeechPolicy(
                    mode=SpeechPolicyMode.CUSTOM, tts_provider="voicestudio", allow_cloud=True
                ),
            )

            with patch("rex.speech.registry.build_default_router", return_value=router):
                from rex.voice_loop import TextToSpeech

                tts = TextToSpeech(language="en")

            with patch.object(
                tts,
                "_speak_voicestudio",
                new=AsyncMock(return_value={"path_used": "voicestudio"}),
            ) as fake_speak:
                result = asyncio.run(tts.speak("hello"))

            fake_speak.assert_awaited_once()
            assert fake_speak.await_args.kwargs["provider"] is fake_voicestudio
            assert result["path_used"] == "voicestudio"

    def test_speak_recovers_to_next_permitted_provider_when_primary_fails_at_call_time(
        self,
    ) -> None:
        """P0 regression: a provider that passes health resolution but then
        fails once actually invoked must recover to the next permitted
        provider (here: native xtts) instead of printing a stdout fallback
        after a single attempt."""
        with (
            patch("rex.voice_loop._lazy_import_tts", return_value=None),
            patch("rex.voice_loop.settings") as mock_settings,
        ):
            mock_settings.tts_provider = "xtts"
            mock_settings.tts_voice = None
            mock_settings.tts_speed = 1.0
            mock_settings.tts_max_spoken_chars = 120
            mock_settings.tts_fast_short_reply_max_chars = 140
            mock_settings.speech = SpeechConfig(enabled=True)

            fake_native = FakeTTSProvider(NATIVE_PROVIDER_ID)
            fake_voicestudio = FakeTTSProvider("voicestudio")
            router = SpeechRouter(
                stt_providers={},
                tts_providers={
                    NATIVE_PROVIDER_ID: fake_native,
                    "voicestudio": fake_voicestudio,
                },
                policy=SpeechPolicy(
                    mode=SpeechPolicyMode.CUSTOM,
                    tts_provider="voicestudio",
                    tts_fallback_order=(NATIVE_PROVIDER_ID,),
                    allow_cloud=True,
                ),
            )

            with patch("rex.speech.registry.build_default_router", return_value=router):
                from rex.voice_loop import TextToSpeech

                tts = TextToSpeech(language="en")

            with (
                patch.object(
                    tts,
                    "_speak_voicestudio",
                    new=AsyncMock(side_effect=RuntimeError("VoiceStudio call failed")),
                ) as fake_speak_voicestudio,
                patch.object(
                    tts,
                    "_speak_xtts",
                    new=AsyncMock(return_value={"path_used": "xtts"}),
                ) as fake_speak_xtts,
                patch("builtins.print") as fake_print,
            ):
                result = asyncio.run(tts.speak("hello"))

            fake_speak_voicestudio.assert_awaited_once()
            fake_speak_xtts.assert_awaited_once()
            assert result["path_used"] == "xtts"
            fake_print.assert_not_called()

    def test_routed_call_time_exhaustion_raises_instead_of_stdout_fallback(self) -> None:
        """All policy-permitted providers can fail after their health checks."""
        with (
            patch("rex.voice_loop._lazy_import_tts", return_value=None),
            patch("rex.voice_loop.settings") as mock_settings,
        ):
            mock_settings.tts_provider = "xtts"
            mock_settings.tts_voice = None
            mock_settings.tts_speed = 1.0
            mock_settings.tts_max_spoken_chars = 120
            mock_settings.tts_fast_short_reply_max_chars = 140
            mock_settings.speech = SpeechConfig(enabled=True)
            router = SpeechRouter(
                stt_providers={},
                tts_providers={
                    "voicestudio": FakeTTSProvider("voicestudio"),
                    NATIVE_PROVIDER_ID: FakeTTSProvider(NATIVE_PROVIDER_ID),
                },
                policy=SpeechPolicy(
                    mode=SpeechPolicyMode.CUSTOM,
                    tts_provider="voicestudio",
                    tts_fallback_order=(NATIVE_PROVIDER_ID,),
                    allow_cloud=True,
                ),
            )
            with patch("rex.speech.registry.build_default_router", return_value=router):
                from rex.voice_loop import TextToSpeech

                tts = TextToSpeech(language="en")

            from rex.assistant_errors import TextToSpeechError

            with (
                patch.object(
                    tts,
                    "_speak_voicestudio",
                    new=AsyncMock(side_effect=RuntimeError("VoiceStudio failed")),
                ),
                patch.object(
                    tts,
                    "_speak_xtts",
                    new=AsyncMock(side_effect=RuntimeError("XTTS failed")),
                ),
                patch("builtins.print") as fake_print,
            ):
                try:
                    asyncio.run(tts.speak("hello"))
                except TextToSpeechError:
                    pass
                else:
                    raise AssertionError("Expected routed TTS exhaustion to propagate")
            fake_print.assert_not_called()

    def test_local_only_policy_failure_raises_instead_of_silently_using_native(self) -> None:
        """No silent cloud/degraded fallback: an exhausted Local Only policy raises."""
        with (
            patch("rex.voice_loop._lazy_import_tts", return_value=None),
            patch("rex.voice_loop.settings") as mock_settings,
        ):
            mock_settings.tts_provider = "xtts"
            mock_settings.tts_voice = None
            mock_settings.tts_speed = 1.0
            mock_settings.tts_max_spoken_chars = 120
            mock_settings.tts_fast_short_reply_max_chars = 140
            mock_settings.speech = SpeechConfig(enabled=True)

            remote_only = FakeTTSProvider("cloud-tts", is_local=False)
            router = SpeechRouter(
                stt_providers={},
                tts_providers={"cloud-tts": remote_only},
                policy=SpeechPolicy(
                    mode=SpeechPolicyMode.LOCAL_ONLY,
                    tts_provider="cloud-tts",
                    allow_cloud=True,
                ),
            )

            with patch("rex.speech.registry.build_default_router", return_value=router):
                from rex.voice_loop import TextToSpeech

                tts = TextToSpeech(language="en")

            from rex.assistant_errors import TextToSpeechError

            with patch("builtins.print") as fake_print:
                try:
                    asyncio.run(tts.speak("hello"))
                except TextToSpeechError:
                    pass
                else:
                    raise AssertionError(
                        "Expected TextToSpeechError to propagate from an exhausted Local "
                        "Only policy"
                    )
            fake_print.assert_not_called()
