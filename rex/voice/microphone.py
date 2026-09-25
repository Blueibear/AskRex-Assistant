"""Async microphone capture — extracted verbatim from ``rex/voice_loop.py`` (US-REM-028)."""

from __future__ import annotations

import asyncio
import time
from typing import cast

from rex.assistant_errors import (
    AudioDeviceError,
)
from rex.audio_config import resolve_input_capture_rate
from rex.voice._types import (
    AudioArray,
    RecorderCallable,
)
from rex.voice.audio_utils import (
    _audio_level,
    _voice_log_extra,
)
from rex.voice.optional_imports import (
    _require_numpy,
    _require_sounddevice,
    np,
)
from rex.voice.transcripts import (
    _COMMAND_CAPTURE_CHUNK_SECONDS,
    _COMMAND_CAPTURE_END_SILENCE_SECONDS,
    _COMMAND_CAPTURE_MAX_SECONDS,
    _COMMAND_CAPTURE_MIN_SECONDS,
    _COMMAND_CAPTURE_RMS_THRESHOLD,
)


def _resample_mono(audio: AudioArray, *, target_frames: int) -> AudioArray:
    """Resample mono float audio to an exact frame count using linear interpolation."""
    np = _require_numpy()
    source = np.asarray(audio, dtype=np.float32).reshape(-1)
    if target_frames <= 0:
        raise AudioDeviceError("Target resample frame count must be positive")
    if source.size == 0:
        raise AudioDeviceError("Audio capture returned no samples")
    if source.size == target_frames:
        return cast(AudioArray, source)
    if source.size == 1:
        return cast(AudioArray, np.full(target_frames, source[0], dtype=np.float32))

    source_positions = np.linspace(0.0, 1.0, num=source.size, endpoint=True)
    target_positions = np.linspace(0.0, 1.0, num=target_frames, endpoint=True)
    return cast(
        AudioArray,
        np.interp(target_positions, source_positions, source).astype(np.float32, copy=False),
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


class AsyncMicrophone:
    """Async microphone recording."""

    def __init__(
        self,
        *,
        sample_rate: int,
        detection_seconds: float,
        capture_seconds: float,
        detection_hop_seconds: float | None = None,
        device_index: int | None = None,
        recorder: RecorderCallable | None = None,
    ) -> None:
        self.sample_rate = sample_rate
        self._detection_seconds = detection_seconds
        self._capture_seconds = capture_seconds
        self._detection_hop_seconds = detection_hop_seconds or detection_seconds
        self._device_index = device_index
        self._recorder = recorder
        self._detection_buffer: AudioArray | None = None
        self._detection_buffer_filled_samples = 0

    async def detection_frame(self) -> AudioArray:
        """Record a short frame for wake word detection."""
        if self._detection_hop_seconds >= self._detection_seconds:
            return await self._record(self._detection_seconds)

        np = _require_numpy()
        chunk = await self._record(self._detection_hop_seconds)
        window_samples = max(int(self.sample_rate * self._detection_seconds), 1)

        previous = (
            self._detection_buffer
            if self._detection_buffer is not None
            else np.zeros(0, dtype=np.float32)
        )
        combined = np.concatenate([previous, chunk])
        filled_samples = min(int(previous.size + chunk.size), window_samples)
        self._detection_buffer_filled_samples = filled_samples
        self._detection_buffer = combined[-window_samples:]

        if combined.size < window_samples:
            frame = np.pad(combined, (window_samples - combined.size, 0))
        else:
            frame = combined[-window_samples:]

        zero_pad_samples = max(window_samples - filled_samples, 0)
        chunk_rms, chunk_peak = _audio_level(chunk)
        frame_rms, frame_peak = _audio_level(cast(AudioArray, frame))
        _vl().logger.debug(
            (
                "MIC DEBUG: overlapping detection frame window=%.2fs hop=%.2fs "
                "samples=%d fill=%.2f"
            ),
            self._detection_seconds,
            self._detection_hop_seconds,
            len(frame),
            filled_samples / window_samples,
            extra=_voice_log_extra(
                event="audio_detection_frame",
                window_s=self._detection_seconds,
                hop_s=self._detection_hop_seconds,
                audio_samples=len(frame),
                buffer_filled_samples=filled_samples,
                buffer_fill_ratio=round(filled_samples / window_samples, 3),
                zero_pad_samples=zero_pad_samples,
                priming=zero_pad_samples > 0,
                chunk_audio_rms=round(chunk_rms, 6),
                chunk_audio_peak=round(chunk_peak, 6),
                frame_audio_rms=round(frame_rms, 6),
                frame_audio_peak=round(frame_peak, 6),
            ),
        )
        return cast(AudioArray, np.asarray(frame, dtype=np.float32))

    async def prime_detection_buffer(self, *, reason: str = "manual") -> None:
        """Fill the rolling wake-detection window before reporting wake readiness."""
        if self._detection_hop_seconds >= self._detection_seconds:
            _vl().logger.info(
                "[Audio] Detection buffer priming skipped for non-overlap mode",
                extra=_voice_log_extra(
                    event="audio_detection_buffer_prime_skipped",
                    reason=reason,
                    window_s=self._detection_seconds,
                    hop_s=self._detection_hop_seconds,
                ),
            )
            return

        window_samples = max(int(self.sample_rate * self._detection_seconds), 1)
        if self._detection_buffer_filled_samples >= window_samples:
            _vl().logger.info(
                "[Audio] Detection buffer already primed",
                extra=_voice_log_extra(
                    event="audio_detection_buffer_primed",
                    reason=reason,
                    frames_recorded=0,
                    duration_s=0.0,
                    buffer_filled_samples=self._detection_buffer_filled_samples,
                    window_samples=window_samples,
                    ready=True,
                ),
            )
            return

        timeout_s = max(2.0, self._detection_seconds * 2.0)
        started_at = time.perf_counter()
        frames_recorded = 0
        _vl().logger.info(
            "[Audio] Priming wake detection buffer",
            extra=_voice_log_extra(
                event="audio_detection_buffer_prime_start",
                reason=reason,
                window_s=self._detection_seconds,
                hop_s=self._detection_hop_seconds,
                buffer_filled_samples=self._detection_buffer_filled_samples,
                window_samples=window_samples,
                timeout_s=timeout_s,
            ),
        )

        while self._detection_buffer_filled_samples < window_samples:
            if time.perf_counter() - started_at >= timeout_s:
                break
            before = self._detection_buffer_filled_samples
            await self.detection_frame()
            frames_recorded += 1
            if self._detection_buffer_filled_samples <= before:
                break

        duration_s = round(time.perf_counter() - started_at, 3)
        ready = self._detection_buffer_filled_samples >= window_samples
        log = _vl().logger.info if ready else _vl().logger.warning
        message = (
            "[Audio] Wake detection buffer primed"
            if ready
            else "[Audio] Wake detection buffer prime incomplete"
        )
        log(
            message,
            extra=_voice_log_extra(
                event="audio_detection_buffer_primed",
                reason=reason,
                frames_recorded=frames_recorded,
                duration_s=duration_s,
                buffer_filled_samples=self._detection_buffer_filled_samples,
                window_samples=window_samples,
                ready=ready,
            ),
        )

    def reset_detection_buffer(self, *, reason: str = "manual") -> None:
        self._detection_buffer = None
        self._detection_buffer_filled_samples = 0
        _vl().logger.debug(
            "MIC DEBUG: detection overlap buffer reset",
            extra=_voice_log_extra(event="audio_detection_buffer_reset", reason=reason),
        )

    async def record_phrase(self, duration: float | None = None) -> AudioArray:
        """Record user speech after wake word."""
        if duration is not None:
            return await self._record(duration)
        if not bool(getattr(_vl().settings, "command_adaptive_capture_enabled", True)):
            return await self._record(self._capture_seconds)
        return await self._record_adaptive_phrase()

    async def _record_adaptive_phrase(self) -> AudioArray:
        """Record until end-of-speech, bounded by configured safety limits."""
        np = _require_numpy()
        base_duration = max(float(self._capture_seconds), 0.1)
        min_duration = max(
            float(
                getattr(_vl().settings, "command_min_capture_seconds", _COMMAND_CAPTURE_MIN_SECONDS)
            ),
            base_duration,
        )
        max_duration = max(
            float(
                getattr(_vl().settings, "command_max_capture_seconds", _COMMAND_CAPTURE_MAX_SECONDS)
            ),
            min_duration,
        )
        silence_seconds = max(
            float(
                getattr(
                    _vl().settings,
                    "command_end_silence_seconds",
                    _COMMAND_CAPTURE_END_SILENCE_SECONDS,
                )
            ),
            _COMMAND_CAPTURE_CHUNK_SECONDS,
        )
        rms_threshold = max(
            float(
                getattr(_vl().settings, "command_vad_rms_threshold", _COMMAND_CAPTURE_RMS_THRESHOLD)
            ),
            0.0,
        )
        chunk_seconds = min(_COMMAND_CAPTURE_CHUNK_SECONDS, max_duration)

        _vl().logger.info(
            "[Audio] Adaptive command capture starting",
            extra=_voice_log_extra(
                event="audio_adaptive_capture_start",
                base_duration_s=base_duration,
                min_duration_s=min_duration,
                max_duration_s=max_duration,
                end_silence_s=silence_seconds,
                rms_threshold=rms_threshold,
            ),
        )

        chunks: list[AudioArray] = []
        total_duration = 0.0
        speech_started = False
        last_voice_at: float | None = None
        peak_rms = 0.0
        stop_reason = "max_duration"

        while total_duration < max_duration:
            remaining = max_duration - total_duration
            chunk_duration = min(chunk_seconds, remaining)
            chunk = await self._record(chunk_duration)
            chunk = cast(AudioArray, np.asarray(chunk, dtype=np.float32).reshape(-1))
            chunks.append(chunk)
            total_duration += chunk_duration

            rms = 0.0
            if chunk.size:
                rms = float(np.sqrt(np.mean(chunk * chunk)))
                peak_rms = max(peak_rms, rms)
            if rms >= rms_threshold:
                speech_started = True
                last_voice_at = total_duration

            if total_duration < min_duration:
                continue

            if speech_started and last_voice_at is not None:
                trailing_silence = total_duration - last_voice_at
                if trailing_silence >= silence_seconds:
                    stop_reason = "end_silence"
                    break
            elif total_duration >= base_duration:
                stop_reason = "no_speech"
                break

        audio = (
            np.concatenate(chunks).astype(np.float32, copy=False)
            if chunks
            else np.zeros(0, dtype=np.float32)
        )
        _vl().logger.info(
            "[Audio] Adaptive command capture complete",
            extra=_voice_log_extra(
                event="audio_adaptive_capture_complete",
                audio_duration_s=round(total_duration, 3),
                audio_samples=int(audio.size),
                speech_started=speech_started,
                peak_rms=round(peak_rms, 6),
                stop_reason=stop_reason,
            ),
        )
        return cast(AudioArray, audio)

    async def _record(self, duration: float) -> AudioArray:
        """Internal recording method."""
        np = _require_numpy()
        if duration <= 0:
            raise AudioDeviceError("Recording duration must be positive")

        if self._recorder is not None:
            result = self._recorder(duration)
            if asyncio.iscoroutine(result):
                result = await result
            if result is None:
                raise AudioDeviceError("Audio recorder returned no audio")
            return cast(AudioArray, np.asarray(result, dtype=np.float32).reshape(-1))

        sd = _require_sounddevice()

        frames = max(int(self.sample_rate * duration), 1)

        def _capture() -> np.ndarray:  # type: ignore[name-defined]
            start = time.perf_counter()
            capture_rate = resolve_input_capture_rate(sd, self._device_index, self.sample_rate)
            capture_frames = max(int(round(capture_rate * duration)), 1)
            _vl().logger.debug(
                "MIC DEBUG: _record start duration=%.2f target_rate=%d capture_rate=%.0f frames=%d",
                duration,
                self.sample_rate,
                capture_rate,
                capture_frames,
            )

            recording = sd.rec(
                capture_frames,
                samplerate=capture_rate,
                channels=1,
                dtype="float32",
                device=self._device_index,
                blocking=True,
            ).reshape(-1)

            if capture_rate != float(self.sample_rate):
                _vl().logger.info(
                    "[Audio] Resampling microphone input to Rex processing rate",
                    extra=_voice_log_extra(
                        event="audio_capture_rate_fallback",
                        device_index=self._device_index,
                        capture_rate_hz=capture_rate,
                        processing_rate_hz=self.sample_rate,
                    ),
                )
                recording = _resample_mono(recording, target_frames=frames)

            end = time.perf_counter()
            _vl().logger.debug("MIC DEBUG: blocking sd.rec returned after %.3fs total", end - start)

            return recording

        try:
            data = await asyncio.to_thread(_capture)
        except Exception as exc:
            raise AudioDeviceError(str(exc)) from exc
        return cast(AudioArray, np.asarray(data, dtype=np.float32))
