"""Async wake-word listener built around ``detect_wakeword``."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import TypeAlias

import numpy as np

from ..assistant_errors import WakeWordError
from .utils import (
    WakeWordDetectionResult,
    WakeWordModelSelection,
    evaluate_wakeword,
    load_wakeword_model,
    load_wakeword_model_with_metadata,
)

logger = logging.getLogger(__name__)
_DEFAULT_LOAD_WAKEWORD_MODEL = load_wakeword_model

# Thresholds for detecting unreliable custom-embedding models: a model that
# repeatedly reports high confidence on quiet/background audio is broken and
# would fire constantly. Frames are only counted inside the self-test window
# at the start of a listening cycle to avoid false fallbacks from marginal
# models during long quiet periods.
_UNRELIABLE_CONFIDENCE_THRESHOLD: float = 0.85
_UNRELIABLE_RMS_MAX: float = 0.006
_UNRELIABLE_PEAK_MAX: float = 0.025
_UNRELIABLE_MIN_FRAMES: int = 5
_UNRELIABLE_WINDOW_S: float = 10.0

DetectorResult: TypeAlias = bool | WakeWordDetectionResult
DetectorCallable: TypeAlias = Callable[[np.ndarray], DetectorResult]
DetectorFactoryResult: TypeAlias = tuple[DetectorCallable, Callable[[], None] | None, str | None]
DetectorFactory: TypeAlias = Callable[[], DetectorFactoryResult]
WakeEventCallback: TypeAlias = Callable[[dict[str, object]], None]


class WakeWordListener:
    """Continuously read audio frames and yield when the wake word fires."""

    def __init__(
        self,
        detector: DetectorCallable,
        *,
        poll_interval: float = 0.05,
        reset_detector: Callable[[], None] | None = None,
        rebuild_detector: DetectorFactory | None = None,
        threshold: float | None = None,
        keyword: str | None = None,
        backend: str | None = None,
        event_callback: WakeEventCallback | None = None,
        fallback_detector_factory: DetectorFactory | None = None,
        fallback_keyword: str | None = None,
        fallback_backend: str | None = None,
        unreliable_silence_enabled: bool = False,
    ) -> None:
        self._detector = detector
        self._poll_interval = poll_interval
        self._reset_detector = reset_detector
        self._rebuild_detector = rebuild_detector
        self._threshold = threshold
        self._keyword = keyword
        self._backend = backend
        self._running = False
        self._attempt_count = 0
        self._detector_generation = 1
        self._needs_rebuild = False
        self._event_callback = event_callback
        self._listening_started_at: float | None = None
        self._listening_cycle = 0
        self._fallback_detector_factory = fallback_detector_factory
        self._fallback_keyword = fallback_keyword
        self._fallback_backend = fallback_backend
        self._unreliable_silence_enabled = unreliable_silence_enabled
        self._high_confidence_quiet_frames = 0
        self._model_marked_unreliable = False
        self._suppress_next_trigger = False

    def mark_listening_started(self, *, reason: str = "manual") -> None:
        """Mark a user-visible wake-listening cycle as active."""
        self._attempt_count = 0
        self._listening_cycle += 1
        self._listening_started_at = time.monotonic()
        # Restart the unreliable-model self-test window for the new cycle.
        # A model already marked unreliable stays on the fallback detector.
        self._high_confidence_quiet_frames = 0
        extra: dict[str, object] = {
            "event": "wakeword_listening_cycle_started",
            "reason": reason,
            "threshold": self._threshold,
            "keyword": self._keyword,
            "backend": self._backend,
            "detector_generation": self._detector_generation,
            "listening_cycle": self._listening_cycle,
            "listening_started_at": self._listening_started_at,
        }
        logger.info("Wake-word listening cycle started", extra=extra)
        self._emit_event("INFO", "Wake-word listening cycle started", extra)

    def _mark_listening_ended(
        self,
        *,
        reason: str,
        accepted: bool,
        confidence: float | None = None,
        threshold: float | None = None,
    ) -> None:
        ended_at = time.monotonic()
        started_at = self._listening_started_at or ended_at
        extra = {
            "event": "wakeword_listening_cycle_ended",
            "reason": reason,
            "accepted": accepted,
            "threshold": threshold if threshold is not None else self._threshold,
            "confidence": confidence,
            "keyword": self._keyword,
            "backend": self._backend,
            "detector_generation": self._detector_generation,
            "listening_cycle": self._listening_cycle,
            "attempts": self._attempt_count,
            "duration_s": round(ended_at - started_at, 3),
        }
        logger.info("Wake-word listening cycle ended", extra=extra)
        self._emit_event("INFO", "Wake-word listening cycle ended", extra)

    async def listen(
        self, source: Callable[[], Awaitable[np.ndarray]]
    ) -> AsyncIterator[np.ndarray]:
        self._running = True
        if self._listening_started_at is None:
            self.mark_listening_started(reason="listener_loop_entered")
        enter_extra: dict[str, object] = {
            "event": "wakeword_listener_loop_entered",
            "threshold": self._threshold,
            "keyword": self._keyword,
            "backend": self._backend,
            "detector_generation": self._detector_generation,
            "detector_ready": True,
            "listening_cycle": self._listening_cycle,
            "listening_started_at": self._listening_started_at,
        }
        logger.info(
            "Wake-word listener loop entered",
            extra=enter_extra,
        )
        self._emit_event("INFO", "Wake-word listener loop entered", enter_extra)
        try:
            while self._running:
                frame = await source()
                detector_ready = True
                try:
                    result = await asyncio.get_running_loop().run_in_executor(
                        None, self._detector, frame
                    )
                except Exception as exc:  # pragma: no cover - defensive guard
                    logger.exception("Wake-word detection failed: %s", exc)
                    detector_ready = False
                    result = False

                self._attempt_count += 1
                (
                    triggered,
                    threshold,
                    confidence,
                    keyword,
                    reason,
                    result_extra,
                ) = self._coerce_result(result)
                accept_reason = "confidence_met_threshold" if triggered else None
                attempt_at = time.monotonic()
                listening_started_at = self._listening_started_at or attempt_at
                time_since_listening_start_s = round(attempt_at - listening_started_at, 3)
                log_level = (
                    logging.INFO
                    if triggered or self._attempt_count <= 8 or self._attempt_count % 4 == 0
                    else logging.DEBUG
                )
                attempt_extra = {
                    "event": "wakeword_attempt",
                    "attempt": self._attempt_count,
                    "detector_ready": detector_ready,
                    "threshold": threshold,
                    "confidence": confidence,
                    "keyword": keyword,
                    "accepted": triggered,
                    "accept_reason": accept_reason,
                    "reject_reason": None if triggered else reason,
                    "detector_generation": self._detector_generation,
                    "listening_cycle": self._listening_cycle,
                    "listening_started_at": listening_started_at,
                    "time_since_wake_listening_start_s": time_since_listening_start_s,
                    "early_listening_attempt": (
                        self._attempt_count <= 8 or time_since_listening_start_s <= 3.0
                    ),
                    **result_extra,
                }
                logger.log(
                    log_level,
                    "Wake-word attempt %d: %s confidence=%.6f threshold=%.3f keyword=%r",
                    self._attempt_count,
                    "accepted" if triggered else "rejected",
                    confidence,
                    threshold,
                    keyword,
                    extra=attempt_extra,
                )
                self._emit_event(
                    logging.getLevelName(log_level),
                    (
                        f"Wake-word attempt {self._attempt_count}: "
                        f"{'accepted' if triggered else 'rejected'}"
                    ),
                    attempt_extra,
                )

                # Unreliable custom-model self-test: count frames where the
                # model reports high confidence on quiet/background audio
                # inside the self-test window at the start of the cycle.
                # After _UNRELIABLE_MIN_FRAMES such frames the model is
                # declared unreliable and the fallback detector is activated.
                if (
                    self._unreliable_silence_enabled
                    and not self._model_marked_unreliable
                    and time_since_listening_start_s <= _UNRELIABLE_WINDOW_S
                ):
                    _rms_raw = result_extra.get("audio_rms")
                    _peak_raw = result_extra.get("audio_peak")
                    _rms = float(_rms_raw) if isinstance(_rms_raw, (int, float)) else 0.0
                    _peak = float(_peak_raw) if isinstance(_peak_raw, (int, float)) else 0.0
                    _is_quiet = _rms <= _UNRELIABLE_RMS_MAX and _peak <= _UNRELIABLE_PEAK_MAX
                    _is_high_conf = confidence >= _UNRELIABLE_CONFIDENCE_THRESHOLD
                    if _is_quiet and _is_high_conf:
                        self._high_confidence_quiet_frames += 1
                        _sample_extra: dict[str, object] = {
                            "event": "wakeword_custom_embedding_score_sample",
                            "self_test_state": "accumulating",
                            "confidence": confidence,
                            "threshold": threshold,
                            "audio_rms": _rms,
                            "audio_peak": _peak,
                            "high_confidence_quiet_frames": self._high_confidence_quiet_frames,
                            "unreliable_min_frames": _UNRELIABLE_MIN_FRAMES,
                            "active_wake_phrase": self._keyword,
                            "active_backend": self._backend,
                        }
                        logger.debug(
                            "Custom embedding high-confidence silence frame %d/%d",
                            self._high_confidence_quiet_frames,
                            _UNRELIABLE_MIN_FRAMES,
                            extra=_sample_extra,
                        )
                        self._emit_event(
                            "DEBUG",
                            "Custom embedding high-confidence silence frame",
                            _sample_extra,
                        )
                        if self._high_confidence_quiet_frames >= _UNRELIABLE_MIN_FRAMES:
                            await self._activate_fallback(
                                audio_rms=_rms,
                                audio_peak=_peak,
                                confidence=confidence,
                            )

                if triggered and self._suppress_next_trigger:
                    self._suppress_next_trigger = False
                    logger.debug(
                        "Wake-word trigger suppressed after fallback activation",
                        extra={
                            "event": "wakeword_trigger_suppressed_fallback",
                            "confidence": confidence,
                            "keyword": keyword,
                            "backend": self._backend,
                        },
                    )
                elif triggered:
                    detected_at = time.monotonic()
                    self._mark_listening_ended(
                        reason="accepted_wake",
                        accepted=True,
                        confidence=confidence,
                        threshold=threshold,
                    )
                    logger.info(
                        "Wake word detected; initiating audio capture",
                        extra={"event": "wakeword_detected", "detected_at": detected_at},
                    )
                    capture_at = time.monotonic()
                    logger.debug(
                        "Audio capture started (%.1f ms after detection)",
                        (capture_at - detected_at) * 1000,
                        extra={"event": "audio_capture_start", "capture_at": capture_at},
                    )
                    source_owner = getattr(source, "__self__", None)
                    reset_detection_buffer = getattr(source_owner, "reset_detection_buffer", None)
                    if callable(reset_detection_buffer):
                        reset_detection_buffer(reason="accepted_wake")
                    self.reset(reason="accepted_wake")
                    yield frame

                await asyncio.sleep(self._poll_interval)
        finally:
            self._running = False
            exit_extra: dict[str, object] = {
                "event": "wakeword_listener_loop_exited",
                "threshold": self._threshold,
                "keyword": self._keyword,
                "backend": self._backend,
                "detector_generation": self._detector_generation,
            }
            logger.info(
                "Wake-word listener loop exited",
                extra=exit_extra,
            )
            self._emit_event("INFO", "Wake-word listener loop exited", exit_extra)

    def _emit_event(self, level: str, message: str, extra: dict[str, object]) -> None:
        if self._event_callback is None:
            return
        try:
            self._event_callback({"level": level, "message": message, "extra": dict(extra)})
        except Exception:
            logger.debug("Wake event callback failed", exc_info=True)

    async def _activate_fallback(
        self, *, audio_rms: float, audio_peak: float, confidence: float
    ) -> None:
        """Mark the custom model unreliable and switch to the fallback detector.

        Emits ``high_confidence_silence`` and ``custom_wake_model_unreliable``
        events, then loads the fallback detector (openWakeWord) and emits
        ``wakeword_backend_fallback_activated``.  Suppresses the frame that
        triggered activation so no false STT capture fires.
        """
        self._model_marked_unreliable = True
        self._high_confidence_quiet_frames = 0
        self._suppress_next_trigger = True

        original_backend = self._backend
        original_keyword = self._keyword

        base_extra: dict[str, object] = {
            "confidence": confidence,
            "audio_rms": audio_rms,
            "audio_peak": audio_peak,
            "unreliable_confidence_threshold": _UNRELIABLE_CONFIDENCE_THRESHOLD,
            "unreliable_rms_max": _UNRELIABLE_RMS_MAX,
            "unreliable_peak_max": _UNRELIABLE_PEAK_MAX,
            "original_backend": original_backend,
            "original_keyword": original_keyword,
            "fallback_keyword": self._fallback_keyword,
            "fallback_backend": self._fallback_backend,
            "detector_generation": self._detector_generation,
        }

        silence_extra: dict[str, object] = {**base_extra, "event": "high_confidence_silence"}
        logger.warning(
            "Wake-word custom model unreliable: high-confidence on quiet audio "
            "(backend=%s keyword=%r rms=%.4f peak=%.4f confidence=%.4f)",
            original_backend,
            original_keyword,
            audio_rms,
            audio_peak,
            confidence,
            extra=silence_extra,
        )
        self._emit_event(
            "WARNING",
            "Wake-word custom model unreliable: high-confidence on quiet audio",
            silence_extra,
        )

        unreliable_extra: dict[str, object] = {
            **base_extra,
            "event": "custom_wake_model_unreliable",
        }
        self._emit_event("WARNING", "Custom wake model marked unreliable", unreliable_extra)

        if self._fallback_detector_factory is None:
            return

        loading_extra: dict[str, object] = {
            "event": "wakeword_fallback_loading",
            "original_backend": original_backend,
            "original_keyword": original_keyword,
            "fallback_keyword": self._fallback_keyword,
            "fallback_backend": self._fallback_backend,
        }
        self._emit_event("INFO", "Wake-word fallback detector loading", loading_extra)

        factory = self._fallback_detector_factory
        try:
            fallback_detector, fallback_reset, fallback_label = (
                await asyncio.get_running_loop().run_in_executor(None, factory)
            )
        except Exception as exc:
            disabled_extra: dict[str, object] = {
                "event": "wakeword_backend_fallback_disabled",
                "error": str(exc),
                "original_backend": original_backend,
                "original_keyword": original_keyword,
            }
            logger.error(
                "Wake-word fallback detector failed to load: %s",
                exc,
                extra=disabled_extra,
            )
            self._emit_event(
                "ERROR",
                "Wake-word fallback detector failed to load",
                disabled_extra,
            )
            return

        self._detector = fallback_detector
        self._reset_detector = fallback_reset
        self._rebuild_detector = factory
        if fallback_label:
            self._keyword = fallback_label
        if self._fallback_backend:
            self._backend = self._fallback_backend
        self._detector_generation += 1

        activated_extra: dict[str, object] = {
            "event": "wakeword_backend_fallback_activated",
            "fallback_keyword": self._keyword,
            "fallback_backend": self._backend,
            "detector_generation": self._detector_generation,
            "original_backend": original_backend,
            "original_keyword": original_keyword,
            "confidence_at_activation": confidence,
            "audio_rms_at_activation": audio_rms,
            "audio_peak_at_activation": audio_peak,
        }
        logger.info("Wake-word backend fallback activated", extra=activated_extra)
        self._emit_event("INFO", "Wake-word backend fallback activated", activated_extra)

    @staticmethod
    def _coerce_result(
        result: DetectorResult,
    ) -> tuple[bool, float, float, str | None, str, dict[str, object]]:
        if isinstance(result, WakeWordDetectionResult):
            return (
                result.triggered,
                result.threshold,
                result.confidence,
                result.keyword,
                result.reason,
                {
                    "audio_rms": result.audio_rms,
                    "audio_peak": result.audio_peak,
                    "effective_peak": result.effective_peak,
                    "applied_gain": result.applied_gain,
                    "gain_limit": result.gain_limit,
                    "target_peak": result.target_peak,
                },
            )
        return bool(result), float("nan"), float("nan"), None, "legacy_bool_detector", {}

    def reset(self, *, reason: str = "manual") -> None:
        """Reset detector state when the backend supports it."""
        self._attempt_count = 0

        if self._rebuild_detector is not None:
            if reason == "accepted_wake":
                self._needs_rebuild = True
                logger.info(
                    "Wake-word detector rebuild deferred until interaction completes",
                    extra={
                        "event": "wakeword_detector_rebuild_deferred",
                        "reason": reason,
                        "detector_generation": self._detector_generation,
                        "threshold": self._threshold,
                        "keyword": self._keyword,
                        "backend": self._backend,
                        "reset_supported": self._reset_detector is not None,
                    },
                )
                return
            if reason == "post_interaction" and self._needs_rebuild:
                self._rebuild(reason=reason)
                return
            if self._reset_detector is None and reason == "post_interaction":
                self._rebuild(reason=reason)
                return

        if self._reset_detector is None:
            logger.info(
                "Wake-word detector reset skipped; backend has no reset hook",
                extra={
                    "event": "wakeword_detector_reset",
                    "reason": reason,
                    "reset": False,
                    "rebuild_available": self._rebuild_detector is not None,
                    "detector_generation": self._detector_generation,
                    "threshold": self._threshold,
                    "keyword": self._keyword,
                    "backend": self._backend,
                },
            )
            return
        try:
            self._reset_detector()
            logger.info(
                "Wake-word detector state reset",
                extra={
                    "event": "wakeword_detector_reset",
                    "reason": reason,
                    "reset": True,
                    "detector_generation": self._detector_generation,
                    "threshold": self._threshold,
                    "keyword": self._keyword,
                    "backend": self._backend,
                },
            )
        except Exception as exc:  # pragma: no cover - defensive guard
            logger.warning(
                "Wake-word detector reset failed: %s",
                exc,
                extra={"event": "wakeword_detector_reset_failed", "reason": reason},
            )

    def _rebuild(self, *, reason: str) -> None:
        if self._rebuild_detector is None:
            return
        started_at = time.perf_counter()
        try:
            detector, reset_detector, keyword = self._rebuild_detector()
        except Exception as exc:  # pragma: no cover - dependency/setup dependent
            failed_extra: dict[str, object] = {
                "event": "wakeword_detector_rebuild_failed",
                "reason": reason,
                "detector_generation": self._detector_generation,
                "threshold": self._threshold,
                "keyword": self._keyword,
                "backend": self._backend,
            }
            failed_extra["error_type"] = type(exc).__name__
            logger.error(
                "Wake-word detector rebuild failed",
                extra=failed_extra,
            )
            self._emit_event("ERROR", "Wake-word detector rebuild failed", failed_extra)
            return

        self._detector = detector
        self._reset_detector = reset_detector
        if keyword:
            self._keyword = keyword
        self._detector_generation += 1
        self._needs_rebuild = False
        rebuilt_extra: dict[str, object] = {
            "event": "wakeword_detector_rebuilt",
            "reason": reason,
            "detector_generation": self._detector_generation,
            "threshold": self._threshold,
            "keyword": self._keyword,
            "backend": self._backend,
            "reset_supported": self._reset_detector is not None,
            "duration_s": round(time.perf_counter() - started_at, 3),
        }
        logger.info("Wake-word detector rebuilt", extra=rebuilt_extra)
        self._emit_event("INFO", "Wake-word detector rebuilt", rebuilt_extra)

    def stop(self) -> None:
        self._running = False


def build_default_detector(
    *,
    sample_rate: int,
    chunk_duration: float,
    threshold: float = 0.5,
    poll_interval: float | None = None,
    keyword: str | None = None,
    model_path: str | None = None,
    embedding_path: str | None = None,
    backend: str | None = None,
    fallback_to_builtin: bool | None = None,
    fallback_keyword: str | None = None,
    event_callback: WakeEventCallback | None = None,
) -> WakeWordListener:
    """Build a WakeWordListener with the default wake-word model."""
    threshold = float(threshold)
    if keyword is not None and keyword.strip() == "":
        raise WakeWordError(
            "Wake word keyword must not be empty. "
            "Set a valid keyword or leave keyword=None to use the default."
        )
    if model_path is not None and model_path.strip():
        resolved = Path(model_path)
        if not resolved.is_file():
            raise WakeWordError(
                f"Wake word model file not found: {resolved}. "
                "Check wake_word.model_path in rex_config.json."
            )

    selection: WakeWordModelSelection | None = None

    def _build_detector_instance() -> DetectorFactoryResult:
        nonlocal selection
        if load_wakeword_model is _DEFAULT_LOAD_WAKEWORD_MODEL:
            model, selection = load_wakeword_model_with_metadata(
                keyword=keyword,
                model_path=model_path,
                embedding_path=embedding_path,
                backend=backend,
                fallback_to_builtin=fallback_to_builtin,
                fallback_keyword=fallback_keyword,
            )
            active_label = selection.active_label
        else:
            model, active_label = load_wakeword_model(
                keyword=keyword,
                model_path=model_path,
                embedding_path=embedding_path,
                backend=backend,
                fallback_to_builtin=fallback_to_builtin,
                fallback_keyword=fallback_keyword,
            )
            selection = WakeWordModelSelection(
                requested_backend=backend or "openwakeword",
                active_backend=backend or "openwakeword",
                requested_phrase=keyword,
                active_label=active_label,
                requested_model_path=model_path,
                requested_embedding_path=embedding_path,
                fallback_keyword=fallback_keyword,
            )

        def _detector(frame: np.ndarray) -> WakeWordDetectionResult:
            return evaluate_wakeword(model, frame, threshold=threshold)

        reset = getattr(model, "reset", None)
        reset_detector = reset if callable(reset) else None
        return _detector, reset_detector, active_label

    try:
        detector, reset_detector, active_label = _build_detector_instance()
    except Exception as exc:  # pragma: no cover - dependency/setup dependent
        raise WakeWordError(f"Failed to load wake-word model: {exc}") from exc

    active_backend = (
        selection.active_backend if selection is not None else (backend or "openwakeword")
    )

    if poll_interval is None:
        poll_interval = min(0.05, max(0.0, chunk_duration / 2))

    # Build a fallback factory for runtime unreliable-model detection when
    # using a custom embedding backend.  The factory loads the builtin
    # openWakeWord model with the configured fallback keyword so the listener
    # can swap it in if the custom model proves unreliable at runtime.
    _fallback_factory: DetectorFactory | None = None
    _fallback_kw: str | None = None
    _fallback_be: str | None = None
    if active_backend == "custom_embedding" and fallback_to_builtin is not False:
        _fb_kw = (fallback_keyword or "hey jarvis").strip()
        _fb_threshold = threshold

        def _build_fallback_instance() -> DetectorFactoryResult:
            fb_model, fb_selection = load_wakeword_model_with_metadata(
                keyword=_fb_kw,
                backend="openwakeword",
                fallback_to_builtin=True,
                fallback_keyword=_fb_kw,
            )
            fb_label = fb_selection.active_label

            def _fb_detector(frame: np.ndarray) -> WakeWordDetectionResult:
                return evaluate_wakeword(fb_model, frame, threshold=_fb_threshold)

            fb_reset = getattr(fb_model, "reset", None)
            fb_reset_fn = fb_reset if callable(fb_reset) else None
            return _fb_detector, fb_reset_fn, fb_label

        _fallback_factory = _build_fallback_instance
        _fallback_kw = _fb_kw
        _fallback_be = "openwakeword"

    logger.info(
        "Wake-word listener configured",
        extra={
            "event": "wakeword_listener_configured",
            "threshold": threshold,
            "poll_interval": poll_interval,
            "chunk_duration_s": chunk_duration,
            "keyword": active_label,
            "backend": active_backend,
            "requested_backend": selection.requested_backend if selection else backend,
            "requested_phrase": selection.requested_phrase if selection else keyword,
            "requested_model_path": selection.requested_model_path if selection else model_path,
            "requested_embedding_path": (
                selection.requested_embedding_path if selection else embedding_path
            ),
            "resolved_model_path": selection.resolved_model_path if selection else None,
            "resolved_embedding_path": selection.resolved_embedding_path if selection else None,
            "used_fallback": selection.used_fallback if selection else False,
            "fallback_keyword": selection.fallback_keyword if selection else fallback_keyword,
            "validation_error": selection.validation_error if selection else None,
            "reset_supported": reset_detector is not None,
            "unreliable_silence_enabled": _fallback_factory is not None
            or active_backend == "custom_embedding",
            "fallback_factory_available": _fallback_factory is not None,
        },
    )
    logger.info(
        "Wake-word detector instance ready",
        extra={
            "event": "wakeword_detector_instance_ready",
            "threshold": threshold,
            "keyword": active_label,
            "backend": active_backend,
            "requested_backend": selection.requested_backend if selection else backend,
            "requested_phrase": selection.requested_phrase if selection else keyword,
            "requested_model_path": selection.requested_model_path if selection else model_path,
            "requested_embedding_path": (
                selection.requested_embedding_path if selection else embedding_path
            ),
            "resolved_model_path": selection.resolved_model_path if selection else None,
            "resolved_embedding_path": selection.resolved_embedding_path if selection else None,
            "used_fallback": selection.used_fallback if selection else False,
            "fallback_keyword": selection.fallback_keyword if selection else fallback_keyword,
            "validation_error": selection.validation_error if selection else None,
            "detector_generation": 1,
            "reset_supported": reset_detector is not None,
        },
    )

    return WakeWordListener(
        detector,
        poll_interval=poll_interval,
        reset_detector=reset_detector,
        rebuild_detector=_build_detector_instance,
        threshold=threshold,
        keyword=active_label,
        backend=active_backend,
        event_callback=event_callback,
        fallback_detector_factory=_fallback_factory,
        fallback_keyword=_fallback_kw,
        fallback_backend=_fallback_be,
        unreliable_silence_enabled=(active_backend == "custom_embedding"),
    )


__all__ = ["WakeWordListener", "build_default_detector", "load_wakeword_model"]
