"""Text-to-speech synthesis — extracted verbatim from ``rex/voice_loop.py`` (US-REM-028)."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import io
import json
import os
import re
import tempfile
import threading
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from rex.assistant_errors import (
    AudioDeviceError,
    TextToSpeechError,
)
from rex.runtime.cancellation import (
    TurnCancelledError,
    await_with_cancellation,
    current_turn_cancellation,
)
from rex.tts_utils import chunk_text_for_xtts
from rex.voice._types import (
    AudioArray,
)
from rex.voice.audio_utils import (
    _voice_log_extra,
)
from rex.voice.optional_imports import (
    _require_numpy,
    _require_sounddevice,
)
from rex.voice.transcripts import (
    _WARMUP_PHRASE,
    _split_into_sentences,
)


def _raise_if_turn_cancelled() -> None:
    cancellation = current_turn_cancellation()
    if cancellation is not None:
        cancellation.raise_if_cancelled()


def _vl():
    """Return the ``rex.voice_loop`` facade module at call time.

    ``rex.voice_loop`` remains the single patch point for settings, lazy
    importers, audio helpers, and pipeline classes (tests monkeypatch
    ``rex.voice_loop.<name>``). Resolving through the facade at call time
    preserves that behavior without an import cycle at module load time.
    """
    import importlib

    return importlib.import_module("rex.voice_loop")


@dataclass
class SynthesizedAudio:
    """Container for synthesized audio data."""

    data: AudioArray
    sample_rate: int


class TextToSpeech:
    """Text-to-speech synthesis."""

    def __init__(self, *, language: str, default_speaker: str | None = None) -> None:
        self._language = language
        self._default_speaker = default_speaker
        self._tts_speed = getattr(_vl().settings, "tts_speed", 1.08)

        # Get TTS settings from config (defaults: xtts provider, en-US-AndrewNeural voice)
        self._provider = getattr(_vl().settings, "tts_provider", "xtts").lower()
        if self._provider == "edge-tts":
            self._provider = "edge"
        elif self._provider == "pyttsx3":
            self._provider = "windows"

        self._edge_voice = getattr(_vl().settings, "tts_voice", None) or "en-US-AndrewNeural"
        raw_output_device = getattr(_vl().settings, "audio_output_device", None)
        self._local_output_device = (
            raw_output_device
            if isinstance(raw_output_device, int)
            and not isinstance(raw_output_device, bool)
            and raw_output_device >= 0
            else None
        )

        # Smart speaker output device name (US-SP-002); None → local audio
        self._tts_output_device: str | None = getattr(_vl().settings, "tts_output_device", None)

        self._tts_override: Any = None
        self._warm_manager: Any = None
        self._warm_component_name: str | None = None
        self._xtts_init_error: str | None = None
        self._xtts_init_user_message: str | None = None
        self._speaking = threading.Event()
        if self._provider == "xtts":
            self._initialize_xtts()

    def _current_edge_voice(self) -> str:
        """Return the active edge-tts voice, re-reading rex_config.json for hot-swap support."""
        try:
            from rex.config_manager import load_config as _load_json_config

            raw = _load_json_config()
            voice = str(raw.get("models", {}).get("tts_voice", "") or "")
            return voice or self._edge_voice
        except Exception:
            return self._edge_voice

    @property
    def _tts(self) -> Any:
        override = getattr(self, "_tts_override", None)
        if override is not None:
            return override
        manager = getattr(self, "_warm_manager", None)
        component_name = getattr(self, "_warm_component_name", None)
        if manager is not None and component_name:
            return manager.peek(component_name)
        return None

    @_tts.setter
    def _tts(self, value: Any) -> None:
        self._tts_override = value

    def is_speaking(self) -> bool:
        """Return True while TTS audio playback is in progress."""
        return self._speaking.is_set()

    def _initialize_xtts(self) -> bool:
        """Initialize XTTS model, storing diagnostics on failure."""
        if self._tts is not None:
            return True

        tts_class = _vl()._lazy_import_tts()
        if tts_class is None:
            self._xtts_init_error = "XTTSUnavailable"
            self._xtts_init_user_message = (
                "Coqui XTTS is not installed; install TTS support or select another voice provider."
            )
            _vl().logger.warning(
                "XTTS init skipped",
                extra=_voice_log_extra(event="xtts_init_skipped", error_code="XTTSUnavailable"),
            )
            return False

        try:
            from rex.runtime.warm import (
                WarmComponentSpec,
                default_idle_timeout,
                get_global_warm_runtime,
                warm_component_key,
            )
            from rex.tts_utils import apply_xtts_safe_globals

            apply_xtts_safe_globals()
            torch = _vl().import_module("torch")
            use_cuda = bool(torch.cuda.is_available())
            manager = get_global_warm_runtime(_vl().settings)
            component_name = warm_component_key("tts", "xtts_v2", use_cuda, id(tts_class))

            def _load_xtts() -> Any:
                engine = tts_class(
                    model_name="tts_models/multilingual/multi-dataset/xtts_v2",
                    progress_bar=False,
                )
                if use_cuda:
                    engine.to("cuda")
                return engine

            manager.register_if_absent(
                WarmComponentSpec(
                    name=component_name,
                    loader=_load_xtts,
                    estimated_cost_mb=2048.0,
                    idle_timeout_s=default_idle_timeout(),
                )
            )
            manager.warm(component_name)
            self._warm_manager = manager
            self._warm_component_name = component_name
            self._xtts_init_error = None
            self._xtts_init_user_message = None
            return True
        except Exception as exc:
            self._xtts_init_error = type(exc).__name__
            self._xtts_init_user_message = (
                "XTTS initialization failed; choose another voice provider or check Voice settings."
            )
            _vl().logger.warning(
                "XTTS init failed",
                extra=_voice_log_extra(event="xtts_init_failed", error_code=type(exc).__name__),
            )
            return False

    @staticmethod
    def _settings_int(name: str, default: int) -> int:
        value = getattr(_vl().settings, name, default)
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (int, float, str)):
            try:
                return int(value)
            except (TypeError, ValueError):
                return default
        return default

    def _play_selected_output(self, pcm_data: Any, sample_rate: int) -> None:
        device = getattr(self, "_local_output_device", None)
        if device is None:
            raise AudioDeviceError("No explicit local speaker is selected")
        sounddevice = _require_sounddevice()
        try:
            sounddevice.play(pcm_data, int(sample_rate), device=device)
            sounddevice.wait()
        except Exception as exc:
            raise AudioDeviceError(f"Selected speaker playback failed: {exc}") from exc

    def _play_wave_on_selected_output(self, wav_path: str) -> None:
        sf = _vl()._lazy_import_soundfile()
        if sf is None:
            raise AudioDeviceError("soundfile is required for selected speaker playback")
        try:
            pcm_data, sample_rate = sf.read(wav_path, dtype="float32", always_2d=True)
        except Exception as exc:
            raise AudioDeviceError(f"Selected speaker audio decode failed: {exc}") from exc
        self._play_selected_output(pcm_data, int(sample_rate))

    async def speak(
        self,
        text: str,
        *,
        speaker_wav: str | None = None,
        prefer_fast: bool = False,
    ) -> dict[str, object]:
        """Synthesize and play text as speech."""
        _raise_if_turn_cancelled()
        if not text:
            return {}

        original_text = text
        text = self._clean_text(text)
        if not text:
            return {}

        if text.lstrip().startswith("TOOL_REQUEST:"):
            trailing_answer = self._strip_tool_request_prefix(text)
            if trailing_answer:
                _vl().logger.warning(
                    "[TTS] Stripped raw TOOL_REQUEST prefix before speech",
                    extra=_voice_log_extra(
                        event="tts_tool_request_prefix_stripped",
                        request_chars=len(text),
                    ),
                )
                text = trailing_answer
            else:
                _vl().logger.error(
                    "[TTS] Suppressing raw TOOL_REQUEST from speech",
                    extra=_voice_log_extra(
                        event="tts_tool_request_suppressed",
                        request_chars=len(text),
                    ),
                )
                return {
                    "configured_provider": self._provider,
                    "path_used": "suppressed_tool_request",
                    "fast_short_candidate": False,
                    "fast_short_used": False,
                    "fallback_used": False,
                    "speech_start_delay_s": None,
                }

        max_spoken_chars = self._settings_int("tts_max_spoken_chars", 120)
        fast_short_candidate = (
            prefer_fast
            and self._provider == "edge"
            and os.name == "nt"
            and getattr(self, "_local_output_device", None) is None
            and len(text) <= self._settings_int("tts_fast_short_reply_max_chars", 140)
        )
        _vl().logger.info(
            "[TTS] Spoken text prepared",
            extra=_voice_log_extra(
                event="tts_spoken_text_prepared",
                provider=self._provider,
                original_text_chars=len(original_text.strip()),
                spoken_text_chars=len(text),
                compact_speech_used=len(text) < len(original_text.strip()),
                max_spoken_chars=max_spoken_chars,
                fast_short_candidate=fast_short_candidate,
            ),
        )
        run_metrics: dict[str, object] = {
            "configured_provider": self._provider,
            "fast_short_candidate": fast_short_candidate,
            "fast_short_used": False,
            "fallback_used": False,
            "speech_start_delay_s": None,
            "spoken_text_chars": len(text),
        }
        self._speaking.set()
        started_at = time.perf_counter()
        _vl().logger.info(
            "[TTS] Request started",
            extra=_voice_log_extra(
                event="tts_request_start",
                provider=self._provider,
                text_chars=len(text),
            ),
        )
        try:
            if fast_short_candidate:
                try:
                    _vl().logger.info(
                        "[TTS] Using fast local short-reply path",
                        extra=_voice_log_extra(
                            event="tts_fast_short_path_selected",
                            configured_provider=self._provider,
                            text_chars=len(text),
                        ),
                    )
                    run_metrics.update(
                        await await_with_cancellation(
                            self._speak_windows_direct(
                                text,
                                reason="fast_short_reply",
                                request_started_at=started_at,
                            )
                        )
                    )
                    run_metrics["fast_short_used"] = True
                    return run_metrics
                except Exception as exc:
                    _vl().logger.warning(
                        "[TTS] Fast local short-reply path failed; falling back to %s",
                        self._provider,
                        extra=_voice_log_extra(
                            event="tts_fast_short_path_failed",
                            configured_provider=self._provider,
                            error_code=type(exc).__name__,
                        ),
                    )
                    run_metrics["fallback_used"] = True
                    run_metrics["fast_short_failure"] = type(exc).__name__

            if self._provider == "xtts":
                run_metrics.update(
                    await await_with_cancellation(
                        self._speak_xtts(text, speaker_wav, request_started_at=started_at)
                    )
                )
            elif self._provider == "edge":
                run_metrics.update(
                    await await_with_cancellation(
                        self._speak_edge(text, request_started_at=started_at)
                    )
                )
            elif self._provider == "windows":
                run_metrics.update(
                    await await_with_cancellation(
                        self._speak_windows(text, request_started_at=started_at)
                    )
                )
            else:
                run_metrics["path_used"] = "stdout"
                run_metrics["speech_start_delay_s"] = 0.0
                print(f"Rex: {text}")
        except TurnCancelledError:
            raise
        except AudioDeviceError:
            raise
        except Exception as exc:
            error_code = self._xtts_init_error if self._provider == "xtts" else None
            if not error_code:
                error_code = type(exc).__name__
            _vl().logger.error(
                "[TTS] Failed",
                extra=_voice_log_extra(event="tts_failed", error_code=error_code),
            )
            run_metrics["fallback_used"] = True
            run_metrics["path_used"] = "stdout_fallback"
            if self._provider == "xtts":
                user_message = getattr(self, "_xtts_init_user_message", None)
                if user_message:
                    run_metrics["user_message"] = user_message
            print(f"Rex: {text}")
        finally:
            self._speaking.clear()
            run_metrics.setdefault("total_duration_s", round(time.perf_counter() - started_at, 3))
            _vl().logger.info(
                "[TTS] Request finished",
                extra=_voice_log_extra(
                    event="tts_request_end",
                    provider=self._provider,
                    duration_s=round(time.perf_counter() - started_at, 3),
                ),
            )
        return run_metrics

    def _clean_text(self, text: str) -> str:
        """Clean text for TTS."""
        original_text = text
        if "Additional info:" in text:
            text = text.split("Additional info:")[0].strip()
        text = re.sub(r"http[s]?://\S+", "", text)
        text = re.sub(r"uddg=\S+", "", text)
        text = re.sub(r"\[.*?\]", "", text)
        sentences = _split_into_sentences(text)
        text = " ".join(sentences[:2]) if sentences else text.strip()
        max_chars = self._settings_int("tts_max_spoken_chars", 120)
        if max_chars > 0 and len(text) > max_chars and len(text) > 80:
            selected: list[str] = []
            current_len = 0
            for sentence in sentences:
                sentence = sentence.strip()
                if not sentence:
                    continue
                next_len = len(sentence) if not selected else current_len + 1 + len(sentence)
                if next_len > max_chars:
                    break
                selected.append(sentence)
                current_len = next_len

            if selected:
                text = " ".join(selected)
            else:
                text = (
                    original_text.strip()
                    if len(original_text.strip()) <= 80
                    else "I have a longer answer ready. Please check the transcript for the details."
                )
            _vl().logger.info(
                "[TTS] Shortened spoken response for voice latency",
                extra=_voice_log_extra(
                    event="tts_spoken_text_shortened",
                    original_chars=len(original_text),
                    spoken_chars=len(text),
                    max_chars=max_chars,
                    sentence_safe=True,
                ),
            )
        return text if text.endswith((".", "!", "?")) else text + "."

    def _strip_tool_request_prefix(self, text: str) -> str:
        """Return natural trailing text after a leading TOOL_REQUEST, if present."""
        stripped = text.lstrip()
        if not stripped.startswith("TOOL_REQUEST:"):
            return text

        payload = stripped[len("TOOL_REQUEST:") :].strip()
        try:
            _, end = json.JSONDecoder().raw_decode(payload)
        except json.JSONDecodeError:
            return ""

        trailing = payload[end:].strip()
        if trailing in {"", ".", "!", "?"}:
            return ""
        return trailing.lstrip(" .!?,;:-")

    def _edge_rate(self) -> str:
        """Convert tts_speed into the rate string expected by edge-tts."""
        try:
            speed = float(self._tts_speed or 1.0)
        except (TypeError, ValueError):
            speed = 1.0
        percent = max(-50, min(100, int(round((speed - 1.0) * 100))))
        return f"{percent:+d}%"

    def _trim_pcm_silence(
        self,
        pcm_data: AudioArray,
        sample_rate: int,
        *,
        threshold: int = 180,
        padding_ms: int = 80,
    ) -> AudioArray:
        """Trim leading/trailing near-silence from decoded TTS PCM."""
        numpy = _require_numpy()
        samples = numpy.asarray(pcm_data)
        if samples.size == 0:
            return pcm_data

        mono = samples
        if mono.ndim > 1:
            mono = numpy.max(numpy.abs(mono), axis=1)
        else:
            mono = numpy.abs(mono)

        active = numpy.flatnonzero(mono > threshold)
        if active.size == 0:
            return pcm_data

        padding = max(0, int(round(sample_rate * padding_ms / 1000)))
        start = max(0, int(active[0]) - padding)
        end = min(int(samples.shape[0]), int(active[-1]) + padding + 1)
        if start == 0 and end == int(samples.shape[0]):
            return pcm_data

        trimmed = samples[start:end]
        dropped_frames = int(samples.shape[0]) - int(trimmed.shape[0])
        _vl().logger.info(
            "[TTS] Trimmed decoded edge-tts silence",
            extra=_voice_log_extra(
                event="tts_audio_silence_trimmed",
                original_frames=int(samples.shape[0]),
                trimmed_frames=int(trimmed.shape[0]),
                dropped_frames=dropped_frames,
                dropped_s=round(dropped_frames / sample_rate, 3) if sample_rate else 0.0,
            ),
        )
        return cast(AudioArray, numpy.ascontiguousarray(trimmed))

    def _try_smart_speaker(self, wav_path: str) -> bool:
        """Attempt to play *wav_path* on the configured smart speaker.

        Returns ``True`` if the audio was routed successfully so the caller
        can skip local playback.  Returns ``False`` if no smart speaker is
        configured or playback failed (caller should fall back to local audio).
        """
        tts_output_device = getattr(self, "_tts_output_device", None)
        if not tts_output_device:
            return False
        try:
            from rex.audio.smart_speaker_output import get_smart_speaker_output
            from rex.audio.speaker_discovery import get_speaker_discovery

            cached = get_speaker_discovery().get_cached_speakers()
            target = next(
                (s for s in cached if s.name == tts_output_device),
                None,
            )
            if target is None:
                _vl().logger.warning(
                    "[TTS] Smart speaker %r not found in cached speakers; falling back to local.",
                    tts_output_device,
                )
                return False
            return get_smart_speaker_output().play_wav(
                wav_path, provider=target.provider, ip=target.ip
            )
        except Exception as exc:
            _vl().logger.warning(
                "[TTS] Smart speaker routing failed",
                extra=_voice_log_extra(
                    event="tts_smart_speaker_route_failed", error_code=type(exc).__name__
                ),
            )
            return False

    async def _speak_xtts(
        self,
        text: str,
        speaker_wav: str | None,
        *,
        request_started_at: float | None = None,
    ) -> dict[str, object]:
        """Synthesize speech using XTTS, playing each chunk immediately."""
        if request_started_at is None:
            request_started_at = time.perf_counter()
        if self._tts is None and not self._initialize_xtts():
            error_code = self._xtts_init_error or "XTTSInitializationError"
            _vl().logger.error(
                "[TTS] XTTS unavailable",
                extra=_voice_log_extra(event="xtts_unavailable", error_code=error_code),
            )
            _vl().logger.warning("XTTS initialization failed; falling back to edge-tts")
            try:
                metrics = await self._speak_edge(text, request_started_at=request_started_at)
            except TypeError as exc:
                if "request_started_at" not in str(exc):
                    raise
                fallback_result = await self._speak_edge(text)  # type: ignore[call-arg]
                metrics = fallback_result if isinstance(fallback_result, dict) else {}
            metrics["fallback_used"] = True
            metrics["path_requested"] = "xtts"
            user_message = getattr(self, "_xtts_init_user_message", None)
            if user_message:
                metrics["user_message"] = user_message
            return metrics
        sf = _vl()._lazy_import_soundfile()
        if sf is None:
            raise TextToSpeechError("soundfile is required for XTTS output")
        chunks = chunk_text_for_xtts(text, max_tokens=300)
        if not chunks:
            return {"path_used": "xtts", "speech_start_delay_s": None}

        first_chunk_started_at: float | None = None
        for chunk in chunks:
            if first_chunk_started_at is None:
                first_chunk_started_at = time.perf_counter()
            await self._synthesize_and_play_chunk(chunk, speaker_wav, sf)
        return {
            "path_used": "xtts",
            "speech_start_delay_s": round(
                (first_chunk_started_at or time.perf_counter()) - request_started_at,
                3,
            ),
        }

    async def _synthesize_and_play_chunk(
        self, chunk: str, speaker_wav: str | None, sf: Any
    ) -> None:
        """Synthesize a single text chunk and play it immediately."""
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            chunk_path = tmp.name

        def _remove_chunk_file() -> None:
            try:
                os.unlink(chunk_path)
            except FileNotFoundError:
                return
            except OSError as exc:
                _vl().logger.warning(
                    "Failed to remove TTS temp file",
                    extra=_voice_log_extra(
                        event="tts_temp_cleanup_failed", error_code=type(exc).__name__
                    ),
                )

        try:

            def _synthesize(tts_engine: Any, _chunk=chunk, _chunk_path=chunk_path) -> None:
                tts_engine.tts_to_file(
                    text=_chunk,
                    speaker_wav=speaker_wav or self._default_speaker,
                    language=self._language,
                    file_path=_chunk_path,
                    speed=self._tts_speed,
                )

            manager = getattr(self, "_warm_manager", None)
            component_name = getattr(self, "_warm_component_name", None)
            override = getattr(self, "_tts_override", None)
            if override is None and manager is not None and component_name:

                def _synthesize_managed() -> None:
                    with manager.acquire(component_name) as tts_engine:
                        _synthesize(tts_engine)

                worker_task = asyncio.create_task(asyncio.to_thread(_synthesize_managed))
                try:
                    await asyncio.shield(worker_task)
                except asyncio.CancelledError:

                    def _cleanup_after_cancel(completed: asyncio.Task[None]) -> None:
                        try:
                            completed.result()
                        except BaseException as exc:
                            _vl().logger.warning(
                                "Cancelled XTTS worker ended with %s", type(exc).__name__
                            )
                        finally:
                            _remove_chunk_file()

                    worker_task.add_done_callback(_cleanup_after_cancel)
                    raise
            else:
                tts_engine = self._tts
                if tts_engine is None:
                    raise TextToSpeechError("XTTS not initialized")
                await asyncio.to_thread(_synthesize, tts_engine)

            if Path(chunk_path).exists():
                routed = await asyncio.to_thread(self._try_smart_speaker, chunk_path)
                if not routed:
                    if getattr(self, "_local_output_device", None) is not None:
                        await asyncio.to_thread(self._play_wave_on_selected_output, chunk_path)
                    else:
                        if _vl().sa is None:
                            raise AudioDeviceError(
                                "Local speaker playback is unavailable because simpleaudio is not installed."
                            )

                        def _play(_path=chunk_path) -> None:
                            wave_obj = _vl().sa.WaveObject.from_wave_file(_path)
                            play_obj = wave_obj.play()
                            play_obj.wait_done()

                        try:
                            await asyncio.to_thread(_play)
                        except Exception as exc:
                            raise AudioDeviceError(f"Speaker playback failed: {exc}") from exc
        finally:
            _remove_chunk_file()

    async def warmup(self, *, speaker_wav: str | None = None) -> None:
        """Pre-warm the TTS engine by synthesizing a short phrase in the background.

        Call via ``asyncio.create_task(tts.warmup())`` so it does not block startup.
        """
        try:
            _vl().logger.info("[TTS] Pre-warming engine...")
            await self.speak(_WARMUP_PHRASE, speaker_wav=speaker_wav)
            _vl().logger.info("[TTS] Pre-warm complete.")
        except Exception as exc:
            _vl().logger.warning(
                "[TTS] Pre-warm failed (non-fatal)",
                extra=_voice_log_extra(event="tts_prewarm_failed", error_code=type(exc).__name__),
            )

    async def speak_streaming(
        self,
        sentences: AsyncIterator[str],
        *,
        speaker_wav: str | None = None,
    ) -> None:
        """Speak each sentence from an async iterator as soon as it arrives.

        This enables first audio to begin playing before the full response is
        available, reducing perceived latency.
        """
        try:
            async for sentence in sentences:
                _raise_if_turn_cancelled()
                sentence = sentence.strip()
                if not sentence:
                    continue
                try:
                    await self.speak(sentence, speaker_wav=speaker_wav)
                except TurnCancelledError:
                    raise
                except AudioDeviceError:
                    raise
                except Exception as exc:
                    _vl().logger.error(
                        "[TTS streaming] chunk failed",
                        extra=_voice_log_extra(
                            event="tts_stream_chunk_failed", error_code=type(exc).__name__
                        ),
                    )
        except TurnCancelledError:
            raise
        except AudioDeviceError:
            raise
        except Exception as exc:
            _vl().logger.error(
                "[TTS streaming] failed",
                extra=_voice_log_extra(event="tts_stream_failed", error_code=type(exc).__name__),
            )

    async def _speak_edge(self, text: str, *, request_started_at: float) -> dict[str, object]:
        """Synthesize speech using Edge TTS."""
        try:
            import edge_tts
        except ImportError:
            raise TextToSpeechError("edge-tts is not installed")

        numpy = _require_numpy()
        sf = _vl()._lazy_import_soundfile()
        if sf is None:
            raise TextToSpeechError("soundfile is required for Edge TTS playback")

        voice = self._current_edge_voice()
        rate = self._edge_rate()
        edge_started_at = time.perf_counter()
        _vl().logger.info(
            "[TTS:edge] Synthesis request started",
            extra=_voice_log_extra(
                event="tts_edge_synthesis_start",
                voice=voice,
                rate=rate,
                simpleaudio_available=_vl().sa is not None,
                text_chars=len(text),
            ),
        )
        _vl().logger.debug(
            "EDGE DEBUG: entered _speak_edge voice=%s sa=%s text_chars=%d",
            voice,
            _vl().sa is not None,
            len(text),
        )

        audio_bytes = bytearray()
        used_streaming = True
        first_audio_chunk_s: float | None = None
        communicate = edge_tts.Communicate(text, voice, rate=rate)
        try:
            stream = communicate.stream()
            if inspect.isawaitable(stream):
                stream = await stream
            async for chunk in stream:
                if chunk.get("type") != "audio":
                    continue
                data = chunk.get("data", b"")
                if not isinstance(data, bytes):
                    continue
                if not audio_bytes:
                    first_audio_chunk_s = round(time.perf_counter() - request_started_at, 3)
                    _vl().logger.info(
                        "[TTS:edge] First synthesis audio chunk received",
                        extra=_voice_log_extra(
                            event="tts_edge_first_audio_chunk",
                            duration_s=round(time.perf_counter() - edge_started_at, 3),
                            bytes=len(data),
                        ),
                    )
                audio_bytes.extend(data)
        except Exception as exc:
            used_streaming = False
            _vl().logger.warning(
                "[TTS:edge] Streaming synthesis failed; falling back to file save",
                extra=_voice_log_extra(
                    event="tts_edge_stream_failed", error_code=type(exc).__name__
                ),
            )
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
                output_path = tmp.name
            try:
                await communicate.save(output_path)
                audio_bytes.extend(Path(output_path).read_bytes())
            finally:
                try:
                    os.unlink(output_path)
                except (OSError, PermissionError) as unlink_exc:
                    _vl().logger.warning(
                        "Failed to remove Edge TTS temp file",
                        extra=_voice_log_extra(
                            event="tts_temp_cleanup_failed", error_code=type(unlink_exc).__name__
                        ),
                    )

        if not audio_bytes:
            raise TextToSpeechError("Edge TTS returned no audio data")

        _vl().logger.info(
            "[TTS:edge] Synthesis audio ready",
            extra=_voice_log_extra(
                event="tts_edge_synthesis_ready",
                duration_s=round(time.perf_counter() - edge_started_at, 3),
                audio_bytes=len(audio_bytes),
                streaming=used_streaming,
            ),
        )
        synthesis_ready_s = round(time.perf_counter() - request_started_at, 3)

        def _decode_from_bytes(_audio=bytes(audio_bytes)):
            try:
                return sf.read(io.BytesIO(_audio), dtype="int16", always_2d=True)
            except Exception:
                with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
                    tmp.write(_audio)
                    fallback_path = tmp.name
                try:
                    return sf.read(fallback_path, dtype="int16", always_2d=True)
                finally:
                    try:
                        os.unlink(fallback_path)
                    except (OSError, PermissionError) as exc:
                        _vl().logger.warning(
                            "Failed to remove Edge TTS decode temp file",
                            extra=_voice_log_extra(
                                event="tts_temp_cleanup_failed", error_code=type(exc).__name__
                            ),
                        )

        decode_started_at = time.perf_counter()
        pcm_data, sample_rate = await asyncio.to_thread(_decode_from_bytes)
        pcm_data = numpy.ascontiguousarray(pcm_data)
        pcm_data = self._trim_pcm_silence(pcm_data, int(sample_rate))
        channel_count = int(pcm_data.shape[1]) if pcm_data.ndim > 1 else 1
        audio_duration_s = (
            round(int(pcm_data.shape[0]) / int(sample_rate), 3) if int(sample_rate) else 0.0
        )
        _vl().logger.info(
            "[TTS:edge] Audio decoded for local playback",
            extra=_voice_log_extra(
                event="tts_edge_audio_ready",
                duration_s=round(time.perf_counter() - decode_started_at, 3),
                sample_rate=sample_rate,
                channels=channel_count,
                frames=int(pcm_data.shape[0]),
                audio_duration_s=audio_duration_s,
            ),
        )

        if getattr(self, "_local_output_device", None) is not None:

            def _play_selected(_pcm=pcm_data, _sample_rate=sample_rate) -> None:
                self._play_selected_output(_pcm, int(_sample_rate))

            _play = _play_selected
        else:
            if _vl().sa is None:
                raise AudioDeviceError(
                    "Local speaker playback is unavailable because simpleaudio is not installed."
                )

            def _play(_pcm=pcm_data, _sample_rate=sample_rate, _channels=channel_count) -> None:
                play_obj = _vl().sa.play_buffer(
                    _pcm.tobytes(),
                    num_channels=_channels,
                    bytes_per_sample=2,
                    sample_rate=_sample_rate,
                )
                play_obj.wait_done()

        playback_started_at = time.perf_counter()
        _vl().logger.info(
            "[TTS:edge] Local playback started",
            extra=_voice_log_extra(
                event="tts_playback_start",
                sample_rate=sample_rate,
                channels=channel_count,
                frames=int(pcm_data.shape[0]),
                audio_duration_s=audio_duration_s,
                speech_start_delay_s=round(time.perf_counter() - request_started_at, 3),
            ),
        )
        _vl().logger.debug(
            "EDGE DEBUG: about to play PCM buffer locally sr=%s channels=%s frames=%s",
            sample_rate,
            channel_count,
            int(pcm_data.shape[0]),
        )
        try:
            await asyncio.to_thread(_play)
        except Exception as exc:
            raise AudioDeviceError(f"Speaker playback failed: {exc}") from exc
        playback_duration_s = round(time.perf_counter() - playback_started_at, 3)
        _vl().logger.info(
            "[TTS:edge] Local playback finished",
            extra=_voice_log_extra(
                event="tts_playback_end",
                duration_s=playback_duration_s,
                audio_duration_s=audio_duration_s,
            ),
        )
        return {
            "path_used": "edge",
            "voice": voice,
            "first_audio_chunk_s": first_audio_chunk_s,
            "synthesis_ready_s": synthesis_ready_s,
            "speech_start_delay_s": round(playback_started_at - request_started_at, 3),
            "playback_duration_s": playback_duration_s,
            "audio_duration_s": audio_duration_s,
        }

    def _windows_sapi_rate(self) -> int:
        try:
            speed = float(self._tts_speed or 1.0)
        except (TypeError, ValueError):
            speed = 1.0
        return max(-10, min(10, int(round((speed - 1.0) * 10))))

    async def _speak_windows_direct(
        self,
        text: str,
        *,
        reason: str = "windows_provider",
        request_started_at: float | None = None,
    ) -> dict[str, object]:
        """Speak through native Windows SAPI without Edge network synthesis."""
        if os.name != "nt":
            raise TextToSpeechError("Windows SAPI is only available on Windows")

        base_rate = self._windows_sapi_rate()
        rate_boost = 2 if reason == "fast_short_reply" else 0
        rate = max(-10, min(10, base_rate + rate_boost))
        started_at = time.perf_counter()
        speech_start_delay_s: float | None = None
        _vl().logger.info(
            "[TTS:windows] Local SAPI playback started",
            extra=_voice_log_extra(
                event="tts_windows_sapi_start",
                reason=reason,
                text_chars=len(text),
                rate=rate,
                rate_boost=rate_boost,
            ),
        )

        def _speak() -> None:
            nonlocal speech_start_delay_s
            try:
                import pythoncom
                import win32com.client
            except ImportError as exc:
                raise TextToSpeechError("pywin32 is required for Windows SAPI") from exc

            initialized = False
            try:
                try:
                    pythoncom.CoInitialize()
                    initialized = True
                except Exception:
                    initialized = False
                voice = win32com.client.Dispatch("SAPI.SpVoice")
                voice.Rate = rate
                if request_started_at is not None:
                    speech_start_delay_s = round(time.perf_counter() - request_started_at, 3)
                voice.Speak(text)
            finally:
                if initialized:
                    pythoncom.CoUninitialize()

        try:
            await asyncio.to_thread(_speak)
        except TextToSpeechError:
            raise
        except Exception as exc:
            raise AudioDeviceError(f"Windows speaker output failed: {exc}") from exc
        _vl().logger.info(
            "[TTS:windows] Local SAPI playback finished",
            extra=_voice_log_extra(
                event="tts_windows_sapi_end",
                reason=reason,
                duration_s=round(time.perf_counter() - started_at, 3),
                text_chars=len(text),
                rate=rate,
                speech_start_delay_s=speech_start_delay_s,
                rate_boost=rate_boost,
            ),
        )
        return {
            "path_used": "windows_sapi",
            "speech_start_delay_s": speech_start_delay_s,
            "playback_duration_s": round(time.perf_counter() - started_at, 3),
            "rate": rate,
            "rate_boost": rate_boost,
        }

    async def _speak_windows(
        self,
        text: str,
        *,
        request_started_at: float,
    ) -> dict[str, object]:
        """Synthesize speech using the pyttsx3 Windows provider."""

        try:
            import pyttsx3
        except ImportError:
            if getattr(self, "_local_output_device", None) is not None:
                raise AudioDeviceError(
                    "pyttsx3 is required to route Windows speech to the selected speaker"
                ) from None
            return await self._speak_windows_direct(
                text,
                request_started_at=request_started_at,
            )

        speech_start_delay_s: float | None = None
        started_at = time.perf_counter()

        if getattr(self, "_local_output_device", None) is not None:
            temporary_path: str | None = None
            try:
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                    temporary_path = tmp.name

                def _synthesize_selected() -> None:
                    engine = pyttsx3.init()
                    engine.save_to_file(text, temporary_path)
                    engine.runAndWait()

                await asyncio.to_thread(_synthesize_selected)
                speech_start_delay_s = round(time.perf_counter() - request_started_at, 3)
                await asyncio.to_thread(self._play_wave_on_selected_output, temporary_path)
            except AudioDeviceError:
                raise
            except Exception as exc:
                raise AudioDeviceError(f"Windows speaker output failed: {exc}") from exc
            finally:
                if temporary_path is not None:
                    with contextlib.suppress(OSError):
                        os.unlink(temporary_path)
            return {
                "path_used": "pyttsx3_selected_output",
                "speech_start_delay_s": speech_start_delay_s,
                "playback_duration_s": round(time.perf_counter() - started_at, 3),
            }

        def _speak() -> None:
            nonlocal speech_start_delay_s
            engine = pyttsx3.init()
            speech_start_delay_s = round(time.perf_counter() - request_started_at, 3)
            engine.say(text)
            engine.runAndWait()

        try:
            await asyncio.to_thread(_speak)
        except Exception as exc:
            raise AudioDeviceError(f"Windows speaker output failed: {exc}") from exc
        return {
            "path_used": "pyttsx3",
            "speech_start_delay_s": speech_start_delay_s,
            "playback_duration_s": round(time.perf_counter() - started_at, 3),
        }
