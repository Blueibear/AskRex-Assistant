"""Wake acknowledgement sound playback — extracted verbatim from ``rex/voice_loop.py`` (US-REM-028)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

from rex.assistant_errors import (
    AudioDeviceError,
)
from rex.voice.optional_imports import (
    _require_sounddevice,
)


def _vl():
    """Return the ``rex.voice_loop`` facade module at call time.

    ``rex.voice_loop`` remains the single patch point for settings, lazy
    importers, audio helpers, and pipeline classes (tests monkeypatch
    ``rex.voice_loop.<name>``). Resolving through the facade at call time
    preserves that behavior without an import cycle at module load time.
    """
    import importlib

    return importlib.import_module("rex.voice_loop")


class WakeAcknowledgement:
    """Play acknowledgement sound when wake word is detected."""

    def __init__(
        self,
        sound_path: Path | None = None,
        *,
        filler_phrase: str | None = None,
        is_speaking: Callable[[], bool] | None = None,
        filler_speak: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        default_path = Path(__file__).resolve().parents[1] / "assets" / "wake_acknowledgment.wav"
        self._sound_path = Path(sound_path) if sound_path else default_path
        self._filler_phrase = filler_phrase
        self._is_speaking = is_speaking
        self._filler_speak = filler_speak
        if not filler_phrase and not self._sound_path.exists():
            try:
                _vl().ensure_wake_acknowledgment_sound(path=str(self._sound_path))
            except Exception as exc:
                _vl().logger.warning(
                    "Failed to generate wake acknowledgment sound",
                    extra={"event": "wake_ack_generate_failed", "error_code": type(exc).__name__},
                )

    async def play(self) -> None:
        """Play the wake acknowledgement sound or spoken filler phrase."""
        if self._is_speaking is not None and self._is_speaking():
            _vl().logger.debug("TTS is speaking; skipping wake acknowledgment")
            return

        if self._filler_phrase and self._filler_speak is not None:
            try:
                await self._filler_speak(self._filler_phrase)
            except Exception as exc:
                _vl().logger.warning(
                    "Filler phrase acknowledgment failed",
                    extra={"event": "wake_ack_filler_failed", "error_code": type(exc).__name__},
                )
            return

        if not self._sound_path.exists():
            return

        def _play() -> None:
            if _vl().sa is None and _vl()._load_sounddevice() is None:
                _vl().logger.warning("No audio playback backend available for wake acknowledgment.")
                return
            if _vl().sa is not None:
                wave_obj = _vl().sa.WaveObject.from_wave_file(str(self._sound_path))
                play_obj = wave_obj.play()
                play_obj.wait_done()
                return
            sd = _require_sounddevice()
            sf = _vl()._lazy_import_soundfile()
            if sf is None:
                raise AudioDeviceError("soundfile is required for wake acknowledgement playback")
            data, rate = sf.read(str(self._sound_path), dtype="float32")
            sd.play(data, rate)
            sd.wait()

        try:
            await asyncio.to_thread(_play)
        except Exception as exc:
            _vl().logger.warning(
                "Wake acknowledgement failed",
                extra={"event": "wake_ack_playback_failed", "error_code": type(exc).__name__},
            )
