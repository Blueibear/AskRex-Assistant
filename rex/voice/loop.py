"""Main voice loop orchestration — extracted verbatim from ``rex/voice_loop.py`` (US-REM-028)."""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, cast

from rex.assistant_errors import (
    AudioDeviceError,
    SpeechToTextError,
    TextToSpeechError,
)
from rex.audio_config import build_audio_device_diagnostic
from rex.runtime.invocation import turn_invocation
from rex.runtime.turn import TurnSource
from rex.voice._types import (
    AudioArray,
    IdentifySpeakerCallable,
)
from rex.voice.audio_utils import (
    _VOICE_INTERACTION_ID,
    _voice_log_extra,
)
from rex.voice.optional_imports import (
    _require_numpy,
    np,
)
from rex.voice.transcripts import (
    _MIN_WAKE_PREROLL_SOURCE_SECONDS,
    _SUSPICIOUS_TRANSCRIPT_RETRY_PROMPT,
    _WEAK_TRANSCRIPT_RETRY_PROMPT,
    _combine_followup_transcript,
    _is_low_value_transcript,
    _is_suspicious_voice_transcript,
    _is_weak_transcript_fragment,
    _looks_like_clarification_reply,
    _sentence_buffer_stream,
    _strip_wake_prefix,
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


class VoiceLoop:
    """Main voice assistant loop coordinating wake word, STT, LLM, and TTS."""

    def __init__(
        self,
        assistant,
        *,
        wake_listener,
        detection_source: Callable[[], Awaitable[np.ndarray]],
        record_phrase: Callable[[], Awaitable[np.ndarray]],
        transcribe: Callable[[np.ndarray], Awaitable[str]],
        speak: Callable[[str], Awaitable[None]],
        speak_streaming: Callable[[AsyncIterator[str]], Awaitable[None]] | None = None,
        warmup: Callable[[], Awaitable[None]] | None = None,
        acknowledge: Callable[[], Awaitable[None]] | None = None,
        post_stt_acknowledge: Callable[[], Awaitable[None]] | None = None,
        identify_speaker: IdentifySpeakerCallable | None = None,
        state_callback: Callable[[str], None] | None = None,
        diagnostic_callback: Callable[[dict[str, object]], None] | None = None,
        sample_rate: int = 16000,
        stt_timeout: float = 30.0,
        llm_timeout: float = 60.0,
        tts_timeout: float = 30.0,
        post_interaction_cooldown: float = 0.75,
        post_wake_preroll_seconds: float = 0.35,
    ) -> None:
        self._assistant = assistant
        if getattr(_vl().settings, "use_openclaw_voice_backend", False):
            _vl().logger.warning(
                "openclaw.use_voice_backend is ignored by the canonical voice loop; "
                "Rex Assistant remains the TurnEngine brain and OpenClaw is an external capability provider",
                extra={"event": "openclaw_voice_backend_legacy_flag_ignored"},
            )

        self._wake_listener = wake_listener
        self._detection_source = detection_source
        self._record_phrase = record_phrase
        self._transcribe = transcribe
        self._speak = speak
        self._speak_streaming = speak_streaming
        self._warmup = warmup
        self._acknowledge = acknowledge
        self._post_stt_acknowledge = post_stt_acknowledge
        self._identify_speaker = identify_speaker
        self._state_callback = state_callback
        self._diagnostic_callback = diagnostic_callback
        self._identify_speaker_accepts_audio = self._resolve_identify_speaker_signature(
            identify_speaker
        )
        self._sample_rate = sample_rate
        self._stt_timeout = stt_timeout
        self._llm_timeout = llm_timeout
        self._tts_timeout = tts_timeout
        self._post_interaction_cooldown = max(0.0, post_interaction_cooldown)
        self._post_wake_preroll_seconds = max(0.0, post_wake_preroll_seconds)
        self._interaction_id = 0
        from rex.logging_utils import runtime_session_id

        self._session_id = runtime_session_id()

    def _log_pipeline_event(
        self,
        event: str,
        *,
        interaction_id: int,
        start_ns: int,
        duration_ms: float | None = None,
        **fields: object,
    ) -> None:
        """Emit one stable structured timing record for a voice pipeline stage."""
        extra: dict[str, object] = {
            "event": event,
            "session_id": self._session_id,
            "interaction_id": interaction_id,
            "start_ns": start_ns,
            **fields,
        }
        if duration_ms is not None:
            extra["duration_ms"] = float(duration_ms)
        _vl().logger.info("[VoicePipeline] %s", event, extra=extra)

    @staticmethod
    def _duration_ms(start_ns: int) -> float:
        return round((time.perf_counter_ns() - start_ns) / 1_000_000, 3)

    def _report_audio_device_error(
        self,
        device_kind: str,
        exc: AudioDeviceError,
        *,
        interaction_id: int | None = None,
    ) -> None:
        diagnostic = build_audio_device_diagnostic(device_kind, exc)
        bounded_diagnostic: dict[str, object] = {
            "event": diagnostic["event"],
            "code": diagnostic["code"],
            "device_kind": diagnostic["device_kind"],
            "user_message": diagnostic["user_message"],
        }
        extra: dict[str, object] = {
            **bounded_diagnostic,
            "session_id": self._session_id,
        }
        if interaction_id is not None:
            extra["interaction_id"] = interaction_id
        _vl().logger.error(
            "Audio %s unavailable",
            bounded_diagnostic["device_kind"],
            extra=extra,
        )
        if self._diagnostic_callback is not None:
            try:
                self._diagnostic_callback(bounded_diagnostic)
            except Exception:
                _vl().logger.exception("Voice diagnostic callback failed")

    @staticmethod
    def _resolve_identify_speaker_signature(
        identify_speaker: IdentifySpeakerCallable | None,
    ) -> bool:
        """Return True when identify_speaker accepts an audio argument."""
        if identify_speaker is None:
            return False
        try:
            signature = inspect.signature(identify_speaker)
        except (TypeError, ValueError):
            return False

        for parameter in signature.parameters.values():
            if parameter.kind in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.VAR_POSITIONAL,
            ):
                return True

        return False

    async def _safe_acknowledge(
        self, *, interaction_id: int = 0, requested_at: float | None = None
    ) -> None:
        try:
            ack = self._acknowledge
            if ack is not None:
                await ack()
        except Exception as exc:
            _vl().logger.warning(
                "[Ack] Acknowledgement tone failed (non-fatal)",
                extra=_voice_log_extra(event="wake_ack_failed", error_code=type(exc).__name__),
            )

    async def _settle_wake_acknowledgement(
        self,
        task: asyncio.Task[None] | None,
        *,
        interaction_id: int,
    ) -> None:
        """Finish or cancel a wake acknowledgement task before later audio stages."""
        if task is None:
            return
        if task.done():
            try:
                await task
            except asyncio.CancelledError:
                pass
            return
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=0.5)
        except TimeoutError:
            _vl().logger.warning(
                "[Ack] Wake acknowledgement was still pending after phrase capture; "
                "cancelling stale acknowledgement task",
                extra={"event": "wake_ack_stale_cancelled", "interaction_id": interaction_id},
            )
            task.cancel()
        except asyncio.CancelledError:
            pass

    async def _post_interaction_reset(self, *, interaction_id: int) -> None:
        """Reset wake state and wait briefly so speaker audio cannot re-trigger wake."""
        token = _VOICE_INTERACTION_ID.set(interaction_id)
        reset = getattr(self._wake_listener, "reset", None)
        try:
            if callable(reset):
                reset(reason="post_interaction")

            source_owner = getattr(self._detection_source, "__self__", None)
            reset_detection_buffer = getattr(source_owner, "reset_detection_buffer", None)
            if callable(reset_detection_buffer):
                reset_detection_buffer(reason="post_interaction")

            if self._post_interaction_cooldown > 0:
                _vl().logger.info(
                    "[Wake] Post-interaction cooldown before listening resumes",
                    extra=_voice_log_extra(
                        event="wakeword_cooldown_start",
                        cooldown_s=self._post_interaction_cooldown,
                    ),
                )
                await asyncio.sleep(self._post_interaction_cooldown)
                _vl().logger.info(
                    "[Wake] Post-interaction cooldown complete; detector ready",
                    extra=_voice_log_extra(
                        event="wakeword_cooldown_end",
                        detector_ready=True,
                    ),
                )

            await self._prime_wake_detection(reason="post_interaction_reset")
        finally:
            _VOICE_INTERACTION_ID.reset(token)

    async def _prime_wake_detection(self, *, reason: str) -> None:
        """Prime source-side wake audio before marking the listener as ready."""
        source_owner = getattr(self._detection_source, "__self__", None)
        prime_detection_buffer = getattr(source_owner, "prime_detection_buffer", None)
        if not callable(prime_detection_buffer):
            return
        _vl().logger.info(
            "[Wake] Priming wake listener audio window",
            extra=_voice_log_extra(event="wake_listen_prime_start", reason=reason),
        )
        try:
            await prime_detection_buffer(reason=reason)
        except Exception as exc:
            _vl().logger.warning(
                "[Wake] Wake listener audio priming failed; continuing unprimed",
                extra=_voice_log_extra(
                    event="wake_listen_prime_failed",
                    reason=reason,
                    error_code=type(exc).__name__,
                ),
            )
            return
        _vl().logger.info(
            "[Wake] Wake listener audio window primed",
            extra=_voice_log_extra(event="wake_listen_prime_complete", reason=reason),
        )

    async def _safe_post_stt_acknowledge(self) -> None:
        """Play post-STT acknowledgement (after transcription, before LLM), suppressing errors."""
        started_at = time.perf_counter()
        try:
            if self._post_stt_acknowledge is not None:
                _vl().logger.info(
                    "[Ack] Post-STT acknowledgement starting",
                    extra=_voice_log_extra(event="post_stt_ack_start"),
                )
                await self._post_stt_acknowledge()
                _vl().logger.info(
                    "[Ack] Post-STT acknowledgement finished",
                    extra=_voice_log_extra(
                        event="post_stt_ack_end",
                        duration_s=round(time.perf_counter() - started_at, 3),
                    ),
                )
        except Exception as exc:
            _vl().logger.warning(
                "[Ack] Post-STT acknowledgement failed (non-fatal)",
                extra=_voice_log_extra(
                    event="post_stt_ack_failed",
                    duration_s=round(time.perf_counter() - started_at, 3),
                    error_code=type(exc).__name__,
                ),
            )

    def _prepend_wake_preroll(
        self,
        wake_frame: AudioArray,
        audio: AudioArray,
        *,
        interaction_id: int,
    ) -> AudioArray:
        """Prepend the end of the wake frame so no-pause commands are not clipped."""
        if isinstance(audio, (bytes, bytearray, memoryview)) or isinstance(
            wake_frame, (bytes, bytearray, memoryview)
        ):
            return audio
        if wake_frame is None:
            return audio
        if self._post_wake_preroll_seconds <= 0 or self._sample_rate <= 0:
            return audio

        numpy = _require_numpy()
        wake_samples = numpy.asarray(wake_frame, dtype=numpy.float32).reshape(-1)
        phrase_samples = numpy.asarray(audio, dtype=numpy.float32).reshape(-1)
        wake_duration_s = wake_samples.size / self._sample_rate
        if wake_duration_s < _MIN_WAKE_PREROLL_SOURCE_SECONDS:
            return audio

        preroll_samples = min(
            wake_samples.size,
            max(0, int(round(self._sample_rate * self._post_wake_preroll_seconds))),
        )
        if preroll_samples <= 0:
            return audio

        combined = numpy.concatenate([wake_samples[-preroll_samples:], phrase_samples])
        _vl().logger.info(
            "[Audio] Wake-frame pre-roll prepended before STT",
            extra={
                "event": "post_wake_preroll_applied",
                "interaction_id": interaction_id,
                "preroll_s": round(preroll_samples / self._sample_rate, 3),
                "preroll_samples": int(preroll_samples),
                "phrase_samples": int(phrase_samples.size),
                "combined_samples": int(combined.size),
            },
        )
        return cast(AudioArray, combined)

    async def _capture_followup_transcript(
        self,
        *,
        interaction_id: int,
        reason: str,
        emit_state: Callable[[str], None],
    ) -> str:
        _vl().logger.info(
            "[Voice] Immediate follow-up capture starting",
            extra=_voice_log_extra(
                event="voice_followup_capture_start",
                interaction_id=interaction_id,
                reason=reason,
            ),
        )
        emit_state("followup_listening")
        capture_started_at = time.perf_counter()
        audio = await self._record_phrase()
        audio_samples = len(audio) if hasattr(audio, "__len__") else 0
        audio_duration_s = audio_samples / self._sample_rate if self._sample_rate > 0 else 0.0
        _vl().logger.info(
            "[Voice] Immediate follow-up audio capture complete",
            extra=_voice_log_extra(
                event="voice_followup_capture_complete",
                interaction_id=interaction_id,
                reason=reason,
                audio_duration_s=audio_duration_s,
                audio_samples=audio_samples,
                capture_elapsed_s=round(time.perf_counter() - capture_started_at, 3),
            ),
        )

        emit_state("processing")
        try:
            transcript = await asyncio.wait_for(self._transcribe(audio), timeout=self._stt_timeout)
        except TimeoutError:
            _vl().logger.error(
                "Follow-up STT stage timed out after %.0fs",
                self._stt_timeout,
                extra=_voice_log_extra(
                    event="pipeline_timeout",
                    interaction_id=interaction_id,
                    stage="followup_stt",
                ),
            )
            return ""

        raw_transcript = transcript.strip()
        transcript = _strip_wake_prefix(raw_transcript)
        _vl().logger.info(
            "[Voice] Immediate follow-up transcript captured",
            extra=_voice_log_extra(
                event="voice_followup_transcript",
                interaction_id=interaction_id,
                reason=reason,
                raw_transcript_chars=len(raw_transcript),
                transcript_chars=len(transcript),
            ),
        )
        return transcript

    async def warmup(self) -> None:
        """Pre-warm TTS in the background.

        Schedule as a fire-and-forget task::

            asyncio.create_task(voice_loop.warmup())
        """
        if self._warmup is not None:
            await self._warmup()

    async def run(self, max_interactions: int | None = None) -> None:
        """Run the voice loop for a specified number of interactions."""
        from rex.voice_latency import VoiceLatencyTracker  # noqa: PLC0415

        def _emit(status: str) -> None:
            """Emit a status event (best-effort, never raises)."""
            try:
                from rex.dashboard.sse import emit_status  # noqa: PLC0415

                emit_status(status)
            except Exception:
                pass
            if self._state_callback is not None:
                try:
                    self._state_callback(status)
                except Exception:
                    pass

        def _emit_wake_listening(*, reason: str) -> None:
            mark_listening_started = getattr(
                self._wake_listener,
                "mark_listening_started",
                None,
            )
            if callable(mark_listening_started):
                mark_listening_started(reason=reason)
            _vl().logger.info(
                "[Wake] Wake listener armed",
                extra={"event": "wake_listen_armed", "reason": reason},
            )
            _emit("wake_listening")

        interactions = 0
        # Assistant calls below pass voice_mode=True for concise spoken replies.
        _speak_streaming = self._speak_streaming

        _vl().logger.info(
            "[Wake] Wake listen requested",
            extra={"event": "wake_listen_requested", "reason": "voice_loop_start"},
        )
        await self._prime_wake_detection(reason="voice_loop_start")
        _emit_wake_listening(reason="voice_loop_start")
        try:
            async for wake_frame in self._wake_listener.listen(self._detection_source):
                try:
                    self._interaction_id += 1
                    interaction_id = self._interaction_id
                    audio_device_kind = "microphone"
                    tracker = VoiceLatencyTracker()
                    wake_detected_ns = time.perf_counter_ns()
                    self._log_pipeline_event(
                        "wake_detected", interaction_id=interaction_id, start_ns=wake_detected_ns
                    )
                    _vl().logger.info(
                        "[Wake] Interaction accepted",
                        extra={"event": "wake_interaction_start", "interaction_id": interaction_id},
                    )
                    _emit("listening")

                    # Fire acknowledgment tone concurrently with recording so the
                    # microphone starts capturing immediately after wake word.
                    # Playback failure is suppressed to keep the pipeline running.
                    ack_task: asyncio.Task[None] | None = None
                    if self._acknowledge:
                        ack_task = asyncio.create_task(
                            self._safe_acknowledge(
                                interaction_id=interaction_id,
                                requested_at=time.monotonic(),
                            )
                        )

                    # Record user speech.  Keep the tail of the accepted wake frame
                    # because no-pause commands can otherwise be clipped before
                    # phrase capture begins.
                    capture_started_at = time.perf_counter()
                    capture_start_ns = time.perf_counter_ns()
                    self._log_pipeline_event(
                        "capture_started", interaction_id=interaction_id, start_ns=capture_start_ns
                    )
                    _vl().logger.info(
                        "[Audio] Post-wake phrase capture starting",
                        extra={
                            "event": "post_wake_capture_start",
                            "interaction_id": interaction_id,
                        },
                    )
                    audio = await self._record_phrase()
                    audio = self._prepend_wake_preroll(
                        wake_frame,
                        audio,
                        interaction_id=interaction_id,
                    )
                    self._log_pipeline_event(
                        "capture_ended",
                        interaction_id=interaction_id,
                        start_ns=capture_start_ns,
                        duration_ms=self._duration_ms(capture_start_ns),
                    )
                    await self._settle_wake_acknowledgement(ack_task, interaction_id=interaction_id)

                    audio_samples = len(audio) if hasattr(audio, "__len__") else 0
                    audio_duration_s = (
                        audio_samples / self._sample_rate if self._sample_rate > 0 else 0.0
                    )
                    _vl().logger.info(
                        "Audio capture complete: %.2fs captured",
                        audio_duration_s,
                        extra={
                            "event": "audio_capture_complete",
                            "interaction_id": interaction_id,
                            "audio_duration_s": audio_duration_s,
                            "audio_samples": audio_samples,
                            "capture_elapsed_s": round(time.perf_counter() - capture_started_at, 3),
                        },
                    )

                    # Optionally identify the speaker from voice
                    if self._identify_speaker is not None:
                        try:
                            if self._identify_speaker_accepts_audio:
                                cast(Any, self._identify_speaker)(audio)
                            else:
                                cast(Any, self._identify_speaker)()
                        except Exception as exc:
                            _vl().logger.warning(
                                "Voice identity check failed",
                                extra=_voice_log_extra(
                                    event="voice_identity_check_failed",
                                    error_code=type(exc).__name__,
                                ),
                            )

                    # Transcribe to text
                    _vl().logger.debug(
                        "Handing audio buffer to STT engine (%d samples)",
                        audio_samples,
                        extra={
                            "event": "stt_handoff",
                            "interaction_id": interaction_id,
                            "audio_samples": audio_samples,
                        },
                    )
                    tracker.mark("stt_start")
                    stt_start_ns = time.perf_counter_ns()
                    self._log_pipeline_event(
                        "stt_started", interaction_id=interaction_id, start_ns=stt_start_ns
                    )
                    try:
                        transcript = await asyncio.wait_for(
                            self._transcribe(audio), timeout=self._stt_timeout
                        )
                    except TimeoutError:
                        _vl().logger.error(
                            "STT stage timed out after %.0fs — resetting pipeline",
                            self._stt_timeout,
                            extra={
                                "event": "pipeline_timeout",
                                "interaction_id": interaction_id,
                                "stage": "stt",
                            },
                        )
                        continue
                    tracker.mark("stt_end")
                    self._log_pipeline_event(
                        "stt_completed",
                        interaction_id=interaction_id,
                        start_ns=stt_start_ns,
                        duration_ms=self._duration_ms(stt_start_ns),
                    )
                    raw_transcript = transcript.strip()
                    stripped_transcript = _strip_wake_prefix(raw_transcript)
                    transcript = (
                        stripped_transcript
                        if stripped_transcript != raw_transcript
                        else raw_transcript
                    )
                    if transcript != raw_transcript:
                        _vl().logger.info(
                            "[STT] Stripped leaked wake phrase from transcript",
                            extra={
                                "event": "stt_wake_prefix_stripped",
                                "interaction_id": interaction_id,
                                "raw_transcript_chars": len(raw_transcript),
                                "transcript_chars": len(transcript),
                            },
                        )
                    if not transcript:
                        _vl().logger.info("No speech detected")
                        _emit("cooldown")
                        await self._post_interaction_reset(interaction_id=interaction_id)
                        _emit_wake_listening(reason="no_speech_reset")
                        continue

                    _vl().logger.info(
                        "[STT] Transcript captured",
                        extra={
                            "event": "stt_transcript",
                            "interaction_id": interaction_id,
                            "transcript_chars": len(transcript),
                        },
                    )
                    if _is_weak_transcript_fragment(transcript):
                        initial_fragment = transcript
                        _vl().logger.warning(
                            "[STT] Asking for repeat after weak transcript fragment",
                            extra={
                                "event": "stt_weak_transcript",
                                "interaction_id": interaction_id,
                                "transcript_chars": len(transcript),
                            },
                        )
                        _emit("thinking")
                        token = _VOICE_INTERACTION_ID.set(interaction_id)
                        try:
                            audio_device_kind = "speaker"
                            await asyncio.wait_for(
                                self._speak(_WEAK_TRANSCRIPT_RETRY_PROMPT),
                                timeout=self._tts_timeout,
                            )
                        finally:
                            _VOICE_INTERACTION_ID.reset(token)
                        audio_device_kind = "microphone"
                        transcript = await self._capture_followup_transcript(
                            interaction_id=interaction_id,
                            reason="weak_transcript_retry",
                            emit_state=_emit,
                        )
                        if not transcript or _is_weak_transcript_fragment(transcript):
                            _vl().logger.warning(
                                "[STT] Follow-up after weak transcript was still unusable",
                                extra={
                                    "event": "stt_weak_transcript_followup_failed",
                                    "interaction_id": interaction_id,
                                    "initial_transcript_chars": len(initial_fragment),
                                    "followup_transcript_chars": len(transcript),
                                },
                            )
                            _emit("cooldown")
                            await self._post_interaction_reset(interaction_id=interaction_id)
                            _emit_wake_listening(reason="weak_transcript_reset")
                            continue
                        _vl().logger.info(
                            "[Voice] Continuing interaction with immediate follow-up transcript",
                            extra={
                                "event": "voice_followup_continued",
                                "interaction_id": interaction_id,
                                "initial_transcript_chars": len(initial_fragment),
                                "followup_transcript_chars": len(transcript),
                            },
                        )

                    if _is_suspicious_voice_transcript(transcript):
                        suspicious_transcript = transcript
                        _vl().logger.warning(
                            "[STT] Asking for confirmation after suspicious transcript",
                            extra={
                                "event": "stt_suspicious_transcript",
                                "interaction_id": interaction_id,
                                "transcript_chars": len(transcript),
                            },
                        )
                        _emit("thinking")
                        token = _VOICE_INTERACTION_ID.set(interaction_id)
                        try:
                            audio_device_kind = "speaker"
                            await asyncio.wait_for(
                                self._speak(_SUSPICIOUS_TRANSCRIPT_RETRY_PROMPT),
                                timeout=self._tts_timeout,
                            )
                        finally:
                            _VOICE_INTERACTION_ID.reset(token)
                        audio_device_kind = "microphone"
                        transcript = await self._capture_followup_transcript(
                            interaction_id=interaction_id,
                            reason="suspicious_transcript_retry",
                            emit_state=_emit,
                        )
                        if (
                            not transcript
                            or _is_weak_transcript_fragment(transcript)
                            or _is_low_value_transcript(transcript)
                            or _is_suspicious_voice_transcript(transcript)
                        ):
                            _vl().logger.warning(
                                "[STT] Follow-up after suspicious transcript was unusable",
                                extra={
                                    "event": "stt_suspicious_transcript_followup_failed",
                                    "interaction_id": interaction_id,
                                    "initial_transcript_chars": len(suspicious_transcript),
                                    "followup_transcript_chars": len(transcript),
                                },
                            )
                            _emit("cooldown")
                            await self._post_interaction_reset(interaction_id=interaction_id)
                            _emit_wake_listening(reason="suspicious_transcript_reset")
                            continue
                        _vl().logger.info(
                            "[Voice] Continuing interaction with confirmed follow-up transcript",
                            extra={
                                "event": "voice_suspicious_transcript_continued",
                                "interaction_id": interaction_id,
                                "initial_transcript_chars": len(suspicious_transcript),
                                "followup_transcript_chars": len(transcript),
                            },
                        )

                    if _is_low_value_transcript(transcript):
                        _vl().logger.warning(
                            "[STT] Ignoring likely filler transcript",
                            extra={
                                "event": "stt_transcript_ignored",
                                "interaction_id": interaction_id,
                                "transcript_chars": len(transcript),
                            },
                        )
                        _emit("cooldown")
                        await self._post_interaction_reset(interaction_id=interaction_id)
                        _emit_wake_listening(reason="ignored_transcript_reset")
                        continue

                    _emit("thinking")

                    # Post-STT acknowledgment: fires after transcription and before
                    # LLM processing, giving the user quick confirmation that their
                    # command was heard.  Runs inline (not as a background task) so
                    # the ack completes within the 500 ms budget before LLM starts.
                    if self._post_stt_acknowledge is not None:
                        await self._safe_post_stt_acknowledge()

                    stream_reply = getattr(self._assistant, "stream_reply", None)

                    # Get LLM response - voice_mode=True enables conciseness prompt
                    _emit("executing")
                    tracker.mark("llm_start")
                    llm_start_ns = time.perf_counter_ns()
                    self._log_pipeline_event(
                        "llm_started", interaction_id=interaction_id, start_ns=llm_start_ns
                    )
                    llm_response: str | None = None
                    if _speak_streaming is not None and callable(stream_reply):
                        tracker.mark("tts_synthesis_start")
                        tracker.mark("tts_first_chunk")
                        tts_start_ns = time.perf_counter_ns()
                        self._log_pipeline_event(
                            "tts_started",
                            interaction_id=interaction_id,
                            start_ns=tts_start_ns,
                            timing_scope="streaming_llm_tts_playback",
                        )
                        try:
                            token = _VOICE_INTERACTION_ID.set(interaction_id)
                            try:
                                audio_device_kind = "speaker"
                                with turn_invocation(TurnSource.VOICE):
                                    await asyncio.wait_for(
                                        _speak_streaming(
                                            _sentence_buffer_stream(
                                                stream_reply(transcript, voice_mode=True)
                                            )
                                        ),
                                        timeout=self._llm_timeout + self._tts_timeout,
                                    )
                            finally:
                                _VOICE_INTERACTION_ID.reset(token)
                        except TimeoutError:
                            _vl().logger.error(
                                "LLM+TTS streaming stage timed out after %.0fs — resetting pipeline",
                                self._llm_timeout + self._tts_timeout,
                                extra={
                                    "event": "pipeline_timeout",
                                    "interaction_id": interaction_id,
                                    "stage": "llm_tts_streaming",
                                },
                            )
                            continue
                        tracker.mark("llm_end")
                        self._log_pipeline_event(
                            "llm_completed",
                            interaction_id=interaction_id,
                            start_ns=llm_start_ns,
                            duration_ms=self._duration_ms(llm_start_ns),
                            timing_scope="streaming_llm_tts_playback",
                        )
                        self._log_pipeline_event(
                            "playback_completed",
                            interaction_id=interaction_id,
                            start_ns=tts_start_ns,
                            duration_ms=self._duration_ms(tts_start_ns),
                            timing_scope="streaming_llm_tts_playback",
                        )
                    else:
                        try:
                            with turn_invocation(TurnSource.VOICE):
                                llm_response = await asyncio.wait_for(
                                    self._assistant.generate_reply(transcript, voice_mode=True),
                                    timeout=self._llm_timeout,
                                )
                        except TimeoutError:
                            _vl().logger.error(
                                "LLM stage timed out after %.0fs — resetting pipeline",
                                self._llm_timeout,
                                extra={
                                    "event": "pipeline_timeout",
                                    "interaction_id": interaction_id,
                                    "stage": "llm",
                                },
                            )
                            continue
                        tracker.mark("llm_end")
                        self._log_pipeline_event(
                            "llm_completed",
                            interaction_id=interaction_id,
                            start_ns=llm_start_ns,
                            duration_ms=self._duration_ms(llm_start_ns),
                        )

                        if not llm_response:
                            continue

                        if not llm_response.endswith((".", "!", "?")):
                            llm_response = llm_response + "."

                        try:
                            _vl().logger.info(
                                "[Voice] Text response ready; starting TTS",
                                extra={
                                    "event": "voice_text_response_ready",
                                    "interaction_id": interaction_id,
                                    "response_chars": len(llm_response),
                                },
                            )
                            tracker.mark("tts_synthesis_start")
                            tts_start_ns = time.perf_counter_ns()
                            self._log_pipeline_event(
                                "tts_started", interaction_id=interaction_id, start_ns=tts_start_ns
                            )
                            token = _VOICE_INTERACTION_ID.set(interaction_id)
                            try:
                                audio_device_kind = "speaker"
                                await asyncio.wait_for(
                                    self._speak(llm_response), timeout=self._tts_timeout
                                )
                            finally:
                                _VOICE_INTERACTION_ID.reset(token)
                            self._log_pipeline_event(
                                "playback_completed",
                                interaction_id=interaction_id,
                                start_ns=tts_start_ns,
                                duration_ms=self._duration_ms(tts_start_ns),
                            )
                        except TimeoutError:
                            _vl().logger.error(
                                "TTS stage timed out after %.0fs — resetting pipeline",
                                self._tts_timeout,
                                extra={
                                    "event": "pipeline_timeout",
                                    "interaction_id": interaction_id,
                                    "stage": "tts",
                                    "response_chars": len(llm_response),
                                },
                            )
                            continue
                        if _looks_like_clarification_reply(llm_response, transcript):
                            audio_device_kind = "microphone"
                            followup_transcript = await self._capture_followup_transcript(
                                interaction_id=interaction_id,
                                reason="assistant_clarification",
                                emit_state=_emit,
                            )
                            if (
                                followup_transcript
                                and not _is_weak_transcript_fragment(followup_transcript)
                                and not _is_low_value_transcript(followup_transcript)
                                and not _is_suspicious_voice_transcript(followup_transcript)
                            ):
                                continued_transcript = _combine_followup_transcript(
                                    transcript,
                                    followup_transcript,
                                )
                                _vl().logger.info(
                                    "[Voice] Assistant clarification answered; generating follow-up reply",
                                    extra={
                                        "event": "voice_clarification_followup",
                                        "interaction_id": interaction_id,
                                        "initial_transcript_chars": len(transcript),
                                        "followup_transcript_chars": len(followup_transcript),
                                        "continued_transcript_chars": len(continued_transcript),
                                    },
                                )
                                _emit("executing")
                                try:
                                    with turn_invocation(TurnSource.VOICE):
                                        followup_response = await asyncio.wait_for(
                                            self._assistant.generate_reply(
                                                continued_transcript,
                                                voice_mode=True,
                                            ),
                                            timeout=self._llm_timeout,
                                        )
                                except TimeoutError:
                                    _vl().logger.error(
                                        "Clarification follow-up LLM stage timed out after %.0fs",
                                        self._llm_timeout,
                                        extra={
                                            "event": "pipeline_timeout",
                                            "interaction_id": interaction_id,
                                            "stage": "clarification_followup_llm",
                                        },
                                    )
                                    continue

                                if followup_response:
                                    if not followup_response.endswith((".", "!", "?")):
                                        followup_response = followup_response + "."
                                    _vl().logger.info(
                                        "[Voice] Clarification follow-up response ready; starting TTS",
                                        extra={
                                            "event": "voice_clarification_followup_response_ready",
                                            "interaction_id": interaction_id,
                                            "response_chars": len(followup_response),
                                        },
                                    )
                                    token = _VOICE_INTERACTION_ID.set(interaction_id)
                                    try:
                                        try:
                                            audio_device_kind = "speaker"
                                            await asyncio.wait_for(
                                                self._speak(followup_response),
                                                timeout=self._tts_timeout,
                                            )
                                        except TimeoutError:
                                            _vl().logger.error(
                                                "Clarification follow-up TTS stage timed out after %.0fs",
                                                self._tts_timeout,
                                                extra={
                                                    "event": "pipeline_timeout",
                                                    "interaction_id": interaction_id,
                                                    "stage": "clarification_followup_tts",
                                                },
                                            )
                                            continue
                                    finally:
                                        _VOICE_INTERACTION_ID.reset(token)
                            else:
                                _vl().logger.info(
                                    "[Voice] No usable immediate answer to clarification",
                                    extra={
                                        "event": "voice_clarification_followup_empty",
                                        "interaction_id": interaction_id,
                                        "initial_transcript_chars": len(transcript),
                                        "followup_transcript_chars": len(followup_transcript),
                                    },
                                )
                    tracker.mark("tts_synthesis_end")
                    tracker.mark("playback_start")
                    tracker.log_summary()
                    _emit("cooldown")
                    await self._post_interaction_reset(interaction_id=interaction_id)
                    _emit_wake_listening(reason="post_interaction_reset")

                except SpeechToTextError as exc:
                    _vl().logger.error(
                        "STT error — resetting pipeline",
                        extra={"event": "stt_error", "error_code": type(exc).__name__},
                    )
                    _emit("error")
                    # Continue loop on transcription errors
                except TextToSpeechError as exc:
                    _vl().logger.error(
                        "TTS error — resetting pipeline",
                        extra={
                            "event": "tts_error",
                            "error_code": type(exc).__name__,
                            "response_chars": len(llm_response or ""),
                        },
                    )
                    _emit("error")
                    # Continue loop on TTS errors without logging response content
                except AudioDeviceError as exc:
                    self._report_audio_device_error(
                        audio_device_kind,
                        exc,
                        interaction_id=interaction_id,
                    )
                    _emit("idle" if self._diagnostic_callback is not None else "error")
                    break
                except Exception as exc:
                    _vl().logger.error(
                        "Unexpected error in voice loop",
                        extra=_voice_log_extra(
                            event="voice_loop_unexpected_error", error_code=type(exc).__name__
                        ),
                    )
                    _emit("error")

                interactions += 1
                if max_interactions is not None and interactions >= max_interactions:
                    break
        except AudioDeviceError as exc:
            self._report_audio_device_error("microphone", exc)
            _vl().logger.error(
                "Audio device unavailable — pipeline halted",
                extra={"event": "pipeline_blocker", "stage": "audio_device"},
            )
            _emit("idle" if self._diagnostic_callback is not None else "error")
