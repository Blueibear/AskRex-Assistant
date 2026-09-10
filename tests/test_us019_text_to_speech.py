"""US-019: Text to speech pipeline tests.

Acceptance criteria:
- TTS engine loads
- audio generated
- audio plays automatically
- Typecheck passes
"""

from __future__ import annotations

import asyncio
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fake_tts_class():
    """Return a mock TTS class whose instance records tts_to_file calls."""
    instance = MagicMock()
    tts_cls = MagicMock(return_value=instance)
    return tts_cls, instance


def _make_fake_torch(cuda_available: bool = False):
    torch = types.ModuleType("torch")
    cuda = types.ModuleType("torch.cuda")
    cuda.is_available = lambda: cuda_available  # type: ignore[attr-defined]
    torch.cuda = cuda  # type: ignore[attr-defined]
    return torch


# ---------------------------------------------------------------------------
# 1. TTS engine loads
# ---------------------------------------------------------------------------


class TestTTSEngineLoads:
    """Verify that TextToSpeech initialises without crashing."""

    def test_loads_with_xtts_unavailable(self):
        """TextToSpeech initialises cleanly when XTTS is not installed."""
        with patch("rex.voice_loop._lazy_import_tts", return_value=None):
            from rex.voice_loop import TextToSpeech

            tts = TextToSpeech(language="en")
            assert tts is not None
            assert tts._tts is None

    def test_loads_with_xtts_available(self):
        """TextToSpeech initialises and stores _tts when XTTS succeeds."""
        fake_cls, fake_instance = _make_fake_tts_class()
        fake_torch = _make_fake_torch(cuda_available=False)

        with (
            patch("rex.voice_loop._lazy_import_tts", return_value=fake_cls),
            patch.dict(sys.modules, {"torch": fake_torch}),
            patch("rex.voice_loop.import_module", return_value=fake_torch),
            patch("rex.voice_loop.settings") as mock_settings,
        ):
            mock_settings.tts_provider = "xtts"
            mock_settings.tts_voice = None
            mock_settings.tts_speed = 1.0
            from rex.voice_loop import TextToSpeech

            tts = TextToSpeech(language="en")
            assert tts._tts is fake_instance

    def test_loads_with_xtts_init_failure(self):
        """TextToSpeech still loads even when XTTS model init raises."""
        fake_cls = MagicMock(side_effect=RuntimeError("GPU not found"))
        fake_torch = _make_fake_torch()

        with (
            patch("rex.voice_loop._lazy_import_tts", return_value=fake_cls),
            patch.dict(sys.modules, {"torch": fake_torch}),
        ):
            from rex.voice_loop import TextToSpeech

            tts = TextToSpeech(language="en")
            assert tts._tts is None

    def test_xtts_retries_init_during_speak_and_surfaces_reason(self):
        """When XTTS init fails at runtime, error includes actionable reason."""
        with (
            patch("rex.voice_loop._lazy_import_tts", return_value=None),
            patch("rex.voice_loop.settings") as mock_settings,
        ):
            mock_settings.tts_provider = "xtts"
            mock_settings.tts_voice = None
            mock_settings.tts_speed = 1.0
            from rex.voice_loop import TextToSpeech

            tts = TextToSpeech(language="en")
            assert tts._tts is None
            with patch.object(tts, "_speak_edge", new_callable=AsyncMock) as fallback:
                fallback.side_effect = RuntimeError("private fallback detail")
                result = asyncio.run(tts.speak("hello"))
            assert result["fallback_used"] is True
            assert "installed" in str(result.get("user_message", "")).lower()
            assert "private fallback detail" not in repr(result)

    def test_loads_with_edge_provider(self):
        """TextToSpeech initialises cleanly with edge provider (no XTTS needed)."""
        with (
            patch("rex.voice_loop._lazy_import_tts", return_value=None),
            patch("rex.voice_loop.settings") as mock_settings,
        ):
            mock_settings.tts_provider = "edge"
            mock_settings.tts_voice = "en-US-AriaNeural"
            mock_settings.tts_speed = 1.0

            from rex.voice_loop import TextToSpeech

            tts = TextToSpeech(language="en")
            assert tts is not None
            assert tts._provider == "edge"

    def test_loads_with_windows_provider(self):
        """TextToSpeech initialises cleanly with windows/pyttsx3 provider."""
        with (
            patch("rex.voice_loop._lazy_import_tts", return_value=None),
            patch("rex.voice_loop.settings") as mock_settings,
        ):
            mock_settings.tts_provider = "windows"
            mock_settings.tts_voice = None
            mock_settings.tts_speed = 1.0

            from rex.voice_loop import TextToSpeech

            tts = TextToSpeech(language="en")
            assert tts._provider == "windows"

    def test_pyttsx3_provider_alias_uses_windows_tts_path(self):
        """The documented pyttsx3 provider must not silently fall back to stdout."""
        with (
            patch("rex.voice_loop._lazy_import_tts", return_value=None),
            patch("rex.voice_loop.settings") as mock_settings,
        ):
            mock_settings.tts_provider = "pyttsx3"
            mock_settings.tts_voice = None
            mock_settings.tts_speed = 1.0

            from rex.voice_loop import TextToSpeech

            tts = TextToSpeech(language="en")
            assert tts._provider == "windows"


# ---------------------------------------------------------------------------
# 2. Audio generated
# ---------------------------------------------------------------------------


class TestAudioGenerated:
    """Verify that speak() triggers audio synthesis."""

    def test_xtts_generates_audio(self, tmp_path):
        """speak() calls tts_to_file when XTTS is available."""
        np = pytest.importorskip("numpy")

        fake_cls, fake_instance = _make_fake_tts_class()
        fake_torch = _make_fake_torch()

        # Fake soundfile that returns a short silence array
        fake_sf = MagicMock()
        fake_sf.read.return_value = (np.zeros(16000, dtype="float32"), 22050)
        fake_sf.write = MagicMock()

        # Fake simpleaudio
        fake_play_obj = MagicMock()
        fake_play_obj.wait_done = MagicMock()
        fake_wave_obj = MagicMock()
        fake_wave_obj.play.return_value = fake_play_obj
        fake_sa = MagicMock()
        fake_sa.WaveObject.from_wave_file.return_value = fake_wave_obj

        with (
            patch("rex.voice_loop._lazy_import_tts", return_value=fake_cls),
            patch.dict(sys.modules, {"torch": fake_torch}),
            patch("rex.voice_loop._lazy_import_soundfile", return_value=fake_sf),
            patch("rex.voice_loop.sa", fake_sa),
            patch("rex.voice_loop.settings") as mock_settings,
        ):
            mock_settings.tts_provider = "xtts"
            mock_settings.tts_voice = None
            mock_settings.tts_speed = 1.0
            from rex.voice_loop import TextToSpeech

            tts = TextToSpeech(language="en")
            tts._tts = fake_instance

            asyncio.run(tts.speak("Hello world"))

        # tts_to_file should have been called at least once
        assert fake_instance.tts_to_file.called

    def test_edge_generates_audio(self, tmp_path):
        """speak() calls edge_tts.Communicate when edge provider is selected."""
        np = pytest.importorskip("numpy")
        fake_communicate = AsyncMock()
        fake_communicate.save = AsyncMock()

        fake_edge_tts = MagicMock()
        fake_edge_tts.Communicate.return_value = fake_communicate

        fake_sf = MagicMock()
        fake_sf.read.return_value = (np.zeros(16000, dtype="float32"), 22050)
        fake_sf.write = MagicMock()

        with (
            patch("rex.voice_loop._lazy_import_tts", return_value=None),
            patch.dict(sys.modules, {"edge_tts": fake_edge_tts}),
            patch("rex.voice_loop._lazy_import_soundfile", return_value=fake_sf),
            patch("rex.voice_loop.settings") as mock_settings,
            patch("rex.voice_loop.sa", MagicMock()),
        ):
            mock_settings.tts_provider = "edge"
            mock_settings.tts_voice = "en-US-AriaNeural"
            mock_settings.tts_speed = 1.0

            from rex.voice_loop import TextToSpeech

            tts = TextToSpeech(language="en")
            tts._provider = "edge"
            asyncio.run(tts.speak("Hello edge"))

        fake_edge_tts.Communicate.assert_called_once()

    def test_windows_generates_audio(self):
        """speak() calls pyttsx3.say when windows provider is selected."""
        fake_engine = MagicMock()
        fake_pyttsx3 = MagicMock()
        fake_pyttsx3.init.return_value = fake_engine

        with (
            patch("rex.voice_loop._lazy_import_tts", return_value=None),
            patch.dict(sys.modules, {"pyttsx3": fake_pyttsx3}),
            patch("rex.voice_loop.settings") as mock_settings,
        ):
            mock_settings.tts_provider = "windows"
            mock_settings.tts_voice = None
            mock_settings.tts_speed = 1.0

            from rex.voice_loop import TextToSpeech

            tts = TextToSpeech(language="en")
            tts._provider = "windows"
            asyncio.run(tts.speak("Hello windows"))

        fake_pyttsx3.init.assert_called_once()
        # _clean_text() adds a trailing period
        fake_engine.say.assert_called_once_with("Hello windows.")
        fake_engine.runAndWait.assert_called_once()

    def test_speak_empty_text_skips_synthesis(self):
        """speak() returns immediately for empty text without calling any engine."""
        with patch("rex.voice_loop._lazy_import_tts", return_value=None):
            from rex.voice_loop import TextToSpeech

            tts = TextToSpeech(language="en")
            # Should not raise
            asyncio.run(tts.speak(""))

    def test_speak_text_cleaned_before_synthesis(self):
        """_clean_text() strips URLs and extra sentences before synthesis."""
        with patch("rex.voice_loop._lazy_import_tts", return_value=None):
            from rex.voice_loop import TextToSpeech

            tts = TextToSpeech(language="en")
            cleaned = tts._clean_text("Hello world. https://example.com more text.")
            assert "https://example.com" not in cleaned


# ---------------------------------------------------------------------------
# 3. Audio plays automatically
# ---------------------------------------------------------------------------


class TestAudioPlaysAutomatically:
    """Verify that playback is triggered after synthesis."""

    def test_xtts_plays_audio_automatically(self, tmp_path):
        """simpleaudio.WaveObject.play() is called after XTTS synthesis."""
        np = pytest.importorskip("numpy")

        fake_cls, fake_instance = _make_fake_tts_class()
        fake_torch = _make_fake_torch()

        fake_sf = MagicMock()
        fake_sf.read.return_value = (np.zeros(16000, dtype="float32"), 22050)
        fake_sf.write = MagicMock()

        fake_play_obj = MagicMock()
        fake_wave_obj = MagicMock()
        fake_wave_obj.play.return_value = fake_play_obj
        fake_sa = MagicMock()
        fake_sa.WaveObject.from_wave_file.return_value = fake_wave_obj

        with (
            patch("rex.voice_loop._lazy_import_tts", return_value=fake_cls),
            patch.dict(sys.modules, {"torch": fake_torch}),
            patch("rex.voice_loop._lazy_import_soundfile", return_value=fake_sf),
            patch("rex.voice_loop.sa", fake_sa),
            patch("rex.voice_loop.settings") as mock_settings,
        ):
            mock_settings.tts_provider = "xtts"
            mock_settings.tts_voice = None
            mock_settings.tts_speed = 1.0
            from rex.voice_loop import TextToSpeech

            tts = TextToSpeech(language="en")
            tts._tts = fake_instance
            asyncio.run(tts.speak("Play this"))

        fake_sa.WaveObject.from_wave_file.assert_called_once()
        fake_wave_obj.play.assert_called_once()
        fake_play_obj.wait_done.assert_called_once()

    def test_edge_uses_selected_local_output_device(self, caplog):
        """Explicit runtime speaker selection must reach actual local playback."""
        np = pytest.importorskip("numpy")

        class FakeCommunicate:
            async def stream(self):
                yield {"type": "audio", "data": b"encoded-audio"}

        fake_edge_tts = MagicMock()
        fake_edge_tts.Communicate.return_value = FakeCommunicate()
        fake_sf = MagicMock()
        fake_sf.read.return_value = (np.zeros((320, 1), dtype="int16"), 16000)
        fake_sd = MagicMock()
        fake_sa = MagicMock()

        with (
            patch.dict(sys.modules, {"edge_tts": fake_edge_tts}),
            patch("rex.voice_loop._lazy_import_soundfile", return_value=fake_sf),
            patch("rex.voice_loop.sd", fake_sd),
            patch("rex.voice_loop.sa", fake_sa),
            patch("rex.voice_loop.settings") as mock_settings,
        ):
            mock_settings.tts_provider = "edge"
            mock_settings.tts_voice = "en-US-AriaNeural"
            mock_settings.tts_speed = 1.0
            mock_settings.audio_output_device = 7
            mock_settings.tts_output_device = None
            from rex.voice_loop import TextToSpeech

            tts = TextToSpeech(language="en")
            private_marker = "private spoken response marker phi"
            with caplog.at_level("DEBUG"):
                asyncio.run(tts.speak(private_marker))

        assert fake_sd.play.call_args.kwargs["device"] == 7
        fake_sd.wait.assert_called_once()
        fake_sa.play_buffer.assert_not_called()
        serialized_logs = caplog.text + repr([record.__dict__ for record in caplog.records])
        assert private_marker not in serialized_logs

    def test_xtts_uses_selected_local_output_device(self):
        """XTTS playback honors the same canonical speaker index."""
        np = pytest.importorskip("numpy")
        fake_cls, fake_instance = _make_fake_tts_class()
        fake_torch = _make_fake_torch()
        fake_sf = MagicMock()
        fake_sf.read.return_value = (np.zeros((320, 1), dtype="float32"), 16000)
        fake_sd = MagicMock()
        fake_sa = MagicMock()

        with (
            patch("rex.voice_loop._lazy_import_tts", return_value=fake_cls),
            patch.dict(sys.modules, {"torch": fake_torch}),
            patch("rex.voice_loop._lazy_import_soundfile", return_value=fake_sf),
            patch("rex.voice_loop.sd", fake_sd),
            patch("rex.voice_loop.sa", fake_sa),
            patch("rex.voice_loop.settings") as mock_settings,
        ):
            mock_settings.tts_provider = "xtts"
            mock_settings.tts_voice = None
            mock_settings.tts_speed = 1.0
            mock_settings.audio_output_device = 5
            mock_settings.tts_output_device = None
            from rex.voice_loop import TextToSpeech

            tts = TextToSpeech(language="en")
            tts._tts = fake_instance
            asyncio.run(tts.speak("Use XTTS speaker"))

        assert fake_sd.play.call_args.kwargs["device"] == 5
        fake_sd.wait.assert_called_once()
        fake_sa.WaveObject.from_wave_file.assert_not_called()

    def test_windows_tts_uses_selected_local_output_device(self):
        """pyttsx3 synthesis is routed through the selected local speaker."""
        np = pytest.importorskip("numpy")
        fake_engine = MagicMock()
        fake_pyttsx3 = MagicMock()
        fake_pyttsx3.init.return_value = fake_engine
        fake_sf = MagicMock()
        fake_sf.read.return_value = (np.zeros((320, 1), dtype="float32"), 16000)
        fake_sd = MagicMock()

        with (
            patch.dict(sys.modules, {"pyttsx3": fake_pyttsx3}),
            patch("rex.voice_loop._lazy_import_soundfile", return_value=fake_sf),
            patch("rex.voice_loop.sd", fake_sd),
            patch("rex.voice_loop.settings") as mock_settings,
        ):
            mock_settings.tts_provider = "pyttsx3"
            mock_settings.tts_voice = None
            mock_settings.tts_speed = 1.0
            mock_settings.audio_output_device = 9
            mock_settings.tts_output_device = None
            from rex.voice_loop import TextToSpeech

            tts = TextToSpeech(language="en")
            asyncio.run(tts.speak("Use Windows speaker"))

        fake_engine.save_to_file.assert_called_once()
        assert fake_sd.play.call_args.kwargs["device"] == 9
        fake_sd.wait.assert_called_once()

    def test_tool_request_suppression_log_does_not_include_request_content(self, caplog):
        """Suppressed tool payloads stay out of always-on TTS diagnostics."""
        private_marker = "private tool argument marker upsilon"
        with (
            patch("rex.voice_loop._lazy_import_tts", return_value=None),
            patch("rex.voice_loop.settings") as mock_settings,
        ):
            mock_settings.tts_provider = "edge"
            mock_settings.tts_voice = None
            mock_settings.tts_speed = 1.0
            mock_settings.tts_max_spoken_chars = 1000
            mock_settings.audio_output_device = None
            mock_settings.tts_output_device = None
            from rex.voice_loop import TextToSpeech

            tts = TextToSpeech(language="en")
            payload = f'TOOL_REQUEST: {{"tool":"x","args":{{"value":"{private_marker}"}}}}'
            with caplog.at_level("INFO"):
                result = asyncio.run(tts.speak(payload))

        assert result["path_used"] == "suppressed_tool_request"
        serialized_logs = caplog.text + repr([record.__dict__ for record in caplog.records])
        assert private_marker not in serialized_logs

    def test_fallback_print_when_no_engine(self, capsys):
        """speak() falls back to print() when provider is unknown."""
        with (
            patch("rex.voice_loop._lazy_import_tts", return_value=None),
            patch("rex.voice_loop.settings") as mock_settings,
        ):
            mock_settings.tts_provider = "unknown_provider"
            mock_settings.tts_voice = None
            mock_settings.tts_speed = 1.0

            from rex.voice_loop import TextToSpeech

            tts = TextToSpeech(language="en")
            tts._provider = "unknown_provider"
            asyncio.run(tts.speak("Fallback text"))

        captured = capsys.readouterr()
        assert "Fallback text" in captured.out

    def test_xtts_error_falls_back_to_print(self, capsys):
        """speak() prints text and does not raise when XTTS playback fails."""
        fake_cls, fake_instance = _make_fake_tts_class()
        fake_torch = _make_fake_torch()

        fake_sf = MagicMock()
        fake_instance.tts_to_file.side_effect = RuntimeError("tts synthesis error")

        with (
            patch("rex.voice_loop._lazy_import_tts", return_value=fake_cls),
            patch.dict(sys.modules, {"torch": fake_torch}),
            patch("rex.voice_loop._lazy_import_soundfile", return_value=fake_sf),
        ):
            from rex.voice_loop import TextToSpeech

            tts = TextToSpeech(language="en")
            tts._tts = fake_instance
            # Should not raise; falls back to print
            asyncio.run(tts.speak("Error recovery text"))

        captured = capsys.readouterr()
        assert "Error recovery text" in captured.out
