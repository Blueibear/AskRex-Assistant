"""Rex voice bridge — persistent NDJSON event emitter for the voice panel.

Spawned by the Electron GUI main process (src/main/handlers/voice.ts).

Emits NDJSON lines to stdout:
  {"type": "state",      "state": "idle"|"wake_listening"|"listening"|"followup_listening"|"processing"|"speaking"}
  {"type": "transcript", "text": "...", "role": "user"|"rex", "timestamp": <ms>}
  {"type": "error",      "error": "..."}

Reads control commands from stdin (one JSON object per line):
  {"command": "stop"}
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import re
import sys
import threading
import time
import traceback as _traceback
import uuid
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any, TypeVar

_VOICE_BRIDGE_SESSION_ID = (
    f"voice-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{os.getpid()}-" f"{uuid.uuid4().hex[:8]}"
)
logger = logging.getLogger(__name__)
_SESSION_USER_ID: str | None = None
_MICROPHONE_LABEL: str | None = None
_ResourceT = TypeVar("_ResourceT")
_INTERNAL_TOOL_SYNTAX_RE = re.compile(r"\bTOOL_(?:REQUEST|RESULT)\s*:", re.IGNORECASE)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_DEFAULT_GUI_SPOKEN_MAX_CHARS = 120
_GUI_LONG_ANSWER_HANDOFF = (
    "I put the full answer on screen. Please read the transcript for the details."
)
_GUI_RECIPE_HANDOFF = (
    "I put the full recipe on screen. Please read the transcript for the ingredients and steps."
)
_TIME_REPLY_RE = re.compile(
    r"^(?:it is|it's)\s+(?P<clock>.+?)(?:\s+in\s+(?P<location>.+?))?(?:\s+right now)?[.!?]?$",
    re.IGNORECASE,
)


def _sanitize_user_facing_voice_text(text: str) -> str:
    if not _INTERNAL_TOOL_SYNTAX_RE.search(text):
        return text
    logger.error(
        "Suppressed raw internal tool syntax before voice transcript/TTS",
        extra={
            "event": "voice_bridge_internal_tool_syntax_suppressed",
            "voice_bridge_session_id": _VOICE_BRIDGE_SESSION_ID,
        },
    )
    return "I could not complete that tool request."


def _compact_spoken_reply_for_gui(
    text: str,
    *,
    max_chars: int = _DEFAULT_GUI_SPOKEN_MAX_CHARS,
) -> tuple[str, bool]:
    """Return a short intentional spoken form while the full answer stays on screen."""
    clean = re.sub(r"\s+", " ", text).strip()
    if not clean:
        return "", False

    time_match = _TIME_REPLY_RE.match(clean)
    if time_match:
        clock = (time_match.group("clock") or "").strip()
        location = (time_match.group("location") or "").strip()
        if re.search(r"\b(?:\d{1,2}:\d{2}|noon|midnight)\b", clock, re.IGNORECASE):
            spoken = f"{clock} in {location}." if location else f"{clock}."
            if spoken != clean:
                return spoken, True

    if max_chars <= 0 or len(clean) <= max_chars:
        return clean, False

    lower = clean.lower()
    handoff = _GUI_RECIPE_HANDOFF if "recipe" in lower else _GUI_LONG_ANSWER_HANDOFF
    sentences = [
        sentence.strip() for sentence in _SENTENCE_SPLIT_RE.split(clean) if sentence.strip()
    ]

    intro = ""
    if sentences:
        first = sentences[0].rstrip()
        if len(first) <= 70 and not first[:3].strip().isdigit():
            intro = first if first.endswith((".", "!", "?")) else f"{first}."

    candidate = f"{intro} {handoff}".strip() if intro else handoff
    if len(candidate) > max_chars:
        candidate = handoff
    return candidate, candidate != clean


class _VoiceBridgeSessionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "voice_bridge_session_id"):
            record.voice_bridge_session_id = _VOICE_BRIDGE_SESSION_ID
        return True


class _LazyAsyncResource:
    def __init__(
        self,
        name: str,
        factory: Callable[[], _ResourceT],
    ) -> None:
        self._name = name
        self._factory = factory
        self._task: asyncio.Task[_ResourceT] | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._load())
            self._task.add_done_callback(self._observe_completion)

    async def _load(self) -> _ResourceT:
        started_at = time.perf_counter()
        logger.info(
            "Starting optional %s warmup",
            self._name,
            extra={
                "event": f"voice_bridge_{self._name}_warmup_start",
                "voice_bridge_session_id": _VOICE_BRIDGE_SESSION_ID,
            },
        )
        try:
            resource = await asyncio.to_thread(self._factory)
        except Exception:
            logger.exception(
                "Optional %s warmup failed",
                self._name,
                extra={
                    "event": f"voice_bridge_{self._name}_warmup_failed",
                    "voice_bridge_session_id": _VOICE_BRIDGE_SESSION_ID,
                    "duration_s": round(time.perf_counter() - started_at, 3),
                },
            )
            raise
        logger.info(
            "Optional %s warmup complete",
            self._name,
            extra={
                "event": f"voice_bridge_{self._name}_warmup_end",
                "voice_bridge_session_id": _VOICE_BRIDGE_SESSION_ID,
                "duration_s": round(time.perf_counter() - started_at, 3),
            },
        )
        return resource

    async def get(self) -> _ResourceT:
        self.start()
        assert self._task is not None
        return await asyncio.shield(self._task)

    @staticmethod
    def _observe_completion(task: asyncio.Task[object]) -> None:
        with suppress(Exception):
            task.result()

    def peek(self) -> _ResourceT | None:
        if self._task is None or not self._task.done() or self._task.cancelled():
            return None
        if self._task.exception() is not None:
            return None
        return self._task.result()


class _DeferredAssistant:
    def __init__(
        self, factory: Callable[[], Any], *, event_observer: Callable[[Any], None] | None = None
    ) -> None:
        self._resource = _LazyAsyncResource("assistant", factory)
        self._event_observer = event_observer

    def start_warmup(self) -> None:
        self._resource.start()

    async def generate_reply(self, *args: Any, **kwargs: Any) -> str:
        assistant = await self._resource.get()
        if self._event_observer is not None:
            kwargs.setdefault("event_observer", self._event_observer)
        return await assistant.generate_reply(*args, **kwargs)

    async def stream_reply(self, *args: Any, **kwargs: Any):
        assistant = await self._resource.get()
        if self._event_observer is not None:
            kwargs.setdefault("event_observer", self._event_observer)
        async for chunk in assistant.stream_reply(*args, **kwargs):
            yield chunk


class _DeferredTextToSpeech:
    def __init__(self, factory: Callable[[], Any]) -> None:
        self._resource = _LazyAsyncResource("tts", factory)

    def start_warmup(self) -> None:
        self._resource.start()

    def is_speaking(self) -> bool:
        tts = self._resource.peek()
        return bool(tts and tts.is_speaking())

    async def speak(self, text: str, *, prefer_fast: bool = False) -> None:
        tts = await self._resource.get()
        await tts.speak(text, prefer_fast=prefer_fast)


def emit(obj: dict) -> None:  # type: ignore[type-arg]
    print(json.dumps(obj), flush=True)


def _json_safe(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def emit_log(level: str, message: str, extra: dict[str, object] | None = None) -> None:
    emit(
        {
            "type": "log",
            "level": level,
            "message": message,
            "extra": _json_safe(extra or {}),
        }
    )


def emit_audio_diagnostic(diagnostic: dict[str, object]) -> None:
    """Forward a structured audio diagnostic to the Electron error channel."""
    user_message = str(diagnostic.get("user_message") or "Voice audio device unavailable.")
    emit_log("ERROR", "Voice audio device unavailable", diagnostic)
    emit(
        {
            "type": "error",
            "error": user_message,
            "code": diagnostic.get("code"),
            "device_kind": diagnostic.get("device_kind"),
        }
    )


class WakeWordRuntimeTracker:
    """Project privacy-safe wake detector events onto the GUI bridge."""

    _PRIVATE_EXTRA_KEYS = {
        "requested_model_path",
        "requested_embedding_path",
        "resolved_model_path",
        "resolved_embedding_path",
        "transcript",
        "prompt",
        "user_id",
        "credential",
        "credentials",
        "token",
    }

    def __init__(
        self,
        *,
        configured_phrase: str | None,
        configured_backend: str | None,
        fallback_phrase: str | None,
        microphone_label: str | None,
        portaudio_device_index: int | None,
    ) -> None:
        self._configured_phrase = self._clean_text(configured_phrase)
        self._configured_backend = self._clean_text(configured_backend)
        self._fallback_phrase = self._clean_text(fallback_phrase)
        self._microphone_label = self._clean_text(microphone_label)
        self._portaudio_device_index = (
            portaudio_device_index
            if isinstance(portaudio_device_index, int)
            and not isinstance(portaudio_device_index, bool)
            else None
        )
        self._active_phrase = self._configured_phrase
        self._active_backend = self._configured_backend
        self._threshold: float | None = None
        self._detector_generation = 1
        self._fallback_active = False
        self._armed = False
        self._max_confidence: float | None = None

    @staticmethod
    def _clean_text(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        value = value.strip()
        return value or None

    @staticmethod
    def _finite_number(value: object) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        number = float(value)
        return number if math.isfinite(number) else None

    @staticmethod
    def _integer(value: object) -> int | None:
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        return value

    @classmethod
    def _safe_log_extra(cls, extra: dict[str, object]) -> dict[str, object]:
        return {
            key: value
            for key, value in extra.items()
            if key not in cls._PRIVATE_EXTRA_KEYS and not key.endswith("_path")
        }

    def _update_common(self, extra: dict[str, object]) -> None:
        phrase = self._clean_text(extra.get("keyword"))
        backend = self._clean_text(extra.get("backend"))
        threshold = self._finite_number(extra.get("threshold"))
        generation = self._integer(extra.get("detector_generation"))
        if phrase is not None:
            self._active_phrase = phrase
        if backend is not None:
            self._active_backend = backend
        if threshold is not None:
            self._threshold = threshold
        if generation is not None:
            self._detector_generation = generation
        if self._configured_backend and self._active_backend:
            self._fallback_active = self._active_backend != self._configured_backend
        elif self._configured_phrase and self._active_phrase and self._fallback_phrase:
            self._fallback_active = (
                self._active_phrase == self._fallback_phrase
                and self._active_phrase != self._configured_phrase
            )

    def _emit_runtime(self, reason: str) -> None:
        emit(
            {
                "type": "wakeword_runtime_status",
                "runtime": {
                    "reason": reason,
                    "configured_phrase": self._configured_phrase,
                    "active_phrase": self._active_phrase,
                    "configured_backend": self._configured_backend,
                    "active_backend": self._active_backend,
                    "threshold": self._threshold,
                    "fallback_active": self._fallback_active,
                    "fallback_phrase": self._fallback_phrase,
                    "detector_generation": self._detector_generation,
                    "armed": self._armed,
                    "microphone_label": self._microphone_label,
                    "portaudio_device_index": self._portaudio_device_index,
                },
            }
        )

    def _emit_attempt(self, extra: dict[str, object]) -> None:
        attempt_count = self._integer(extra.get("attempt"))
        if attempt_count is None:
            return
        confidence = self._finite_number(extra.get("confidence"))
        if attempt_count == 1:
            self._max_confidence = confidence
        elif confidence is not None:
            self._max_confidence = (
                confidence
                if self._max_confidence is None
                else max(self._max_confidence, confidence)
            )
        accepted = extra.get("accepted") is True
        if not accepted and attempt_count > 3 and attempt_count % 10 != 0:
            return
        phrase = self._clean_text(extra.get("keyword"))
        if phrase is not None:
            self._active_phrase = phrase
        threshold = self._finite_number(extra.get("threshold"))
        if threshold is not None:
            self._threshold = threshold
        generation = self._integer(extra.get("detector_generation"))
        if generation is not None:
            self._detector_generation = generation
        emit(
            {
                "type": "wakeword_attempt_evidence",
                "evidence": {
                    "attempt_count": attempt_count,
                    "latest_confidence": confidence,
                    "max_confidence": self._max_confidence,
                    "threshold": self._threshold,
                    "audio_rms": self._finite_number(extra.get("audio_rms")),
                    "audio_peak": self._finite_number(extra.get("audio_peak")),
                    "reject_reason": self._clean_text(extra.get("reject_reason")),
                    "active_phrase": self._active_phrase,
                    "active_backend": self._active_backend,
                    "detector_generation": self._detector_generation,
                    "accepted": accepted,
                    "microphone_label": self._microphone_label,
                    "portaudio_device_index": self._portaudio_device_index,
                },
            }
        )

    def handle(self, payload: dict[str, object]) -> None:
        level = str(payload.get("level", "INFO"))
        message = str(payload.get("message", "Wake-word event"))
        raw_extra = payload.get("extra")
        extra = raw_extra if isinstance(raw_extra, dict) else {"event": "wakeword_event"}
        emit_log(level, message, self._safe_log_extra(extra))

        event_name = self._clean_text(extra.get("event")) or "wakeword_event"
        if event_name == "wakeword_attempt":
            self._emit_attempt(extra)
            return

        if event_name in {
            "wakeword_listening_cycle_started",
            "wakeword_listener_loop_entered",
        }:
            self._update_common(extra)
            self._armed = True
            if event_name == "wakeword_listening_cycle_started":
                self._max_confidence = None
            self._emit_runtime(event_name)
            return

        if event_name == "wakeword_backend_fallback_activated":
            phrase = self._clean_text(extra.get("fallback_keyword"))
            backend = self._clean_text(extra.get("fallback_backend"))
            generation = self._integer(extra.get("detector_generation"))
            if phrase is not None:
                self._active_phrase = phrase
            if backend is not None:
                self._active_backend = backend
            if generation is not None:
                self._detector_generation = generation
            self._fallback_active = True
            self._emit_runtime(event_name)
            return

        if event_name == "wakeword_listening_cycle_ended":
            self._update_common(extra)
            self._armed = False
            self._emit_runtime(event_name)
            return

        if event_name == "wakeword_detector_rebuilt":
            self._update_common(extra)
            self._emit_runtime(event_name)
            return

        if event_name == "wakeword_listener_loop_exited":
            self._update_common(extra)
            self._armed = False
            self._emit_runtime(event_name)
            return

        if event_name in {
            "wakeword_detector_rebuild_failed",
            "wakeword_backend_fallback_disabled",
        }:
            self._update_common(extra)
            self._emit_runtime(event_name)


def time_ms() -> int:
    return int(time.time() * 1000)


def _move_stream_logging_to_stderr() -> None:
    """Keep stdout reserved for the NDJSON bridge protocol."""
    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        stream = getattr(handler, "stream", None)
        stream_name = getattr(stream, "name", None)
        if isinstance(handler, logging.StreamHandler) and (
            stream is sys.stdout or stream is sys.__stdout__ or stream_name == "<stdout>"
        ):
            handler.setStream(sys.stderr)


def _install_voice_bridge_log_filter() -> None:
    root_logger = logging.getLogger()
    log_filter = _VoiceBridgeSessionFilter()
    if not any(isinstance(existing, _VoiceBridgeSessionFilter) for existing in root_logger.filters):
        root_logger.addFilter(log_filter)
    for handler in root_logger.handlers:
        if not any(isinstance(existing, _VoiceBridgeSessionFilter) for existing in handler.filters):
            handler.addFilter(log_filter)


stop_event = threading.Event()


def _stdin_watcher() -> None:
    """Background thread: watch stdin for a stop command."""
    try:
        for raw in sys.stdin:
            raw = raw.strip()
            if not raw:
                continue
            try:
                cmd = json.loads(raw)
                if cmd.get("command") == "stop":
                    stop_event.set()
                    break
            except Exception:
                pass
    except Exception:
        pass
    # stdin closed or stop received
    stop_event.set()


def _run_stub_loop() -> None:
    """Simulate voice sessions until a stop command is received.

    Used when the real voice pipeline is unavailable.  Attempts a real LLM
    reply for the stub 'user turn'; falls back to a canned response if the
    backend is not importable.
    """
    try:
        from rex import settings as rex_settings  # type: ignore[import]
        from rex.assistant import Assistant  # type: ignore[import]
        from rex.identity import validate_user_id  # type: ignore[import]
        from rex.services import initialize_services  # type: ignore[import]

        initialize_services()
        # Deliberate single-user profile selection (issue #303): Assistant no
        # longer invents an identity when user_id is omitted.
        assistant = Assistant(
            history_limit=rex_settings.max_memory_items,
            plugins=[],
            user_id=validate_user_id(_SESSION_USER_ID or ""),
        )
        has_backend = True
    except Exception:
        has_backend = False
        assistant = None  # type: ignore[assignment]

    while not stop_event.is_set():
        # ── Listening phase ──────────────────────────────────────────────────
        emit({"type": "state", "state": "listening"})
        if stop_event.wait(timeout=4.0):
            break

        # ── Processing phase ─────────────────────────────────────────────────
        emit({"type": "state", "state": "processing"})
        stub_user_text = "[Stub mode — microphone not active]"
        emit(
            {
                "type": "transcript",
                "text": stub_user_text,
                "role": "user",
                "timestamp": time_ms(),
            }
        )
        if stop_event.wait(timeout=0.5):
            break

        # ── LLM reply ────────────────────────────────────────────────────────
        if has_backend and assistant is not None:
            try:
                from rex.runtime.invocation import turn_invocation
                from rex.runtime.turn import TurnSource

                with turn_invocation(TurnSource.VOICE):
                    reply_text = str(asyncio.run(assistant.generate_reply(stub_user_text)))
            except Exception as exc:
                reply_text = f"(Backend error: {exc})"
        else:
            reply_text = (
                "Voice pipeline is running in stub mode. "
                "Install all voice dependencies and connect a microphone to enable real voice input."
            )
        reply_text = _sanitize_user_facing_voice_text(reply_text)

        if stop_event.is_set():
            break

        # ── Speaking phase ────────────────────────────────────────────────────
        emit({"type": "state", "state": "speaking"})
        emit(
            {
                "type": "transcript",
                "text": reply_text,
                "role": "rex",
                "timestamp": time_ms(),
            }
        )
        if stop_event.wait(timeout=2.5):
            break

        # ── Brief idle between sessions ───────────────────────────────────────
        emit({"type": "state", "state": "idle"})
        if stop_event.wait(timeout=1.0):
            break

    emit({"type": "state", "state": "idle"})


async def _run_real_loop() -> None:
    """Build and run the real voice pipeline with state/transcript emission.

    Constructs VoiceLoop with the correct required arguments (assistant,
    wake_listener, detection_source, record_phrase, transcribe, speak) and
    wraps transcribe/speak to emit NDJSON events to stdout.
    """
    emit({"type": "status", "status": "importing_rex"})
    import rex
    from rex import settings as rex_settings

    emit({"type": "status", "status": "importing_assistant"})
    from rex import config as rex_config_module
    from rex.assistant import Assistant
    from rex.audio_config import resolve_input_device_index_by_name
    from rex.config import load_config as load_runtime_config
    from rex.logging_utils import configure_logging
    from rex.plugins import load_plugins
    from rex.runtime.status import TurnStatusProjector
    from rex.services import initialize_services

    emit({"type": "status", "status": "importing_voice_loop"})
    import rex.voice_loop as voice_loop_module
    from rex.voice_loop import (
        AsyncMicrophone,
        SpeechToText,
        TextToSpeech,
        VoiceLoop,
        WakeAcknowledgement,
    )

    emit({"type": "status", "status": "importing_wakeword_detector"})
    from rex.wakeword.listener import build_default_detector

    configure_logging()
    _move_stream_logging_to_stderr()
    _install_voice_bridge_log_filter()
    emit({"type": "status", "status": "loading_config"})

    try:
        runtime_config = load_runtime_config(reload=True)
        active_settings = runtime_config
    except Exception:
        active_settings = rex_settings

    rex.settings = active_settings
    rex_config_module.settings = active_settings
    voice_loop_module.settings = active_settings
    configure_logging()
    _move_stream_logging_to_stderr()
    _install_voice_bridge_log_filter()
    logger.info(
        "GUI voice bridge session starting",
        extra={
            "event": "voice_bridge_session_start",
            "voice_bridge_session_id": _VOICE_BRIDGE_SESSION_ID,
        },
    )
    emit({"type": "status", "status": "initializing_services"})
    initialize_services()

    # Read voice settings from config with sensible defaults
    sample_rate = int(getattr(active_settings, "sample_rate", 16000) or 16000)
    detection_seconds = float(getattr(active_settings, "detection_frame_seconds", 1.0) or 1.0)
    capture_seconds = float(
        getattr(active_settings, "capture_seconds", None)
        or getattr(active_settings, "command_duration", 5.0)
        or 5.0
    )
    whisper_model = str(getattr(active_settings, "whisper_model", "base") or "base")
    device = str(getattr(active_settings, "whisper_device", "auto") or "auto")
    language = str(getattr(active_settings, "whisper_language", "en") or "en")
    detection_hop_seconds = max(0.125, detection_seconds / 8)
    wakeword_threshold = float(getattr(active_settings, "wakeword_threshold", None) or 0.1)
    configured_wake_phrase = (
        str(
            getattr(active_settings, "wakeword_keyword", None)
            or getattr(active_settings, "wakeword", None)
            or ""
        ).strip()
        or None
    )
    configured_wake_backend = (
        str(getattr(active_settings, "wakeword_backend", None) or "openwakeword").strip()
        or "openwakeword"
    )
    fallback_wake_phrase = (
        str(getattr(active_settings, "wakeword_fallback_keyword", "hey jarvis") or "").strip()
        or None
    )
    microphone_label = (_MICROPHONE_LABEL or "").strip() or None
    configured_device_index = getattr(active_settings, "input_device_index", None)
    microphone_device_index = (
        resolve_input_device_index_by_name(microphone_label)
        if microphone_label
        else configured_device_index
    )

    logger.info(
        "GUI voice bridge runtime configuration resolved",
        extra={
            "event": "voice_bridge_config",
            "voice_bridge_session_id": _VOICE_BRIDGE_SESSION_ID,
            "sample_rate": sample_rate,
            "detection_frame_seconds": detection_seconds,
            "detection_hop_seconds": detection_hop_seconds,
            "capture_seconds": capture_seconds,
            "microphone_label": microphone_label,
            "microphone_device_index": microphone_device_index,
            "wakeword_backend": getattr(active_settings, "wakeword_backend", None),
            "wakeword_threshold": wakeword_threshold,
            "wakeword_keyword": getattr(active_settings, "wakeword_keyword", None)
            or getattr(active_settings, "wakeword", None),
            "wakeword_model_path": getattr(active_settings, "wakeword_model_path", None),
            "wakeword_embedding_path": getattr(active_settings, "wakeword_embedding_path", None),
            "wakeword_fallback_to_builtin": getattr(
                active_settings, "wakeword_fallback_to_builtin", True
            ),
            "wakeword_fallback_keyword": getattr(
                active_settings, "wakeword_fallback_keyword", "hey jarvis"
            ),
            "stt_model": whisper_model,
            "stt_device": device,
            "stt_language": language,
            "tts_provider": getattr(active_settings, "tts_provider", None),
            "tts_voice": getattr(active_settings, "tts_voice", None),
        },
    )
    emit_log(
        "INFO",
        "GUI voice bridge runtime configuration resolved",
        {
            "event": "voice_bridge_config",
            "voice_bridge_session_id": _VOICE_BRIDGE_SESSION_ID,
            "sample_rate": sample_rate,
            "detection_frame_seconds": detection_seconds,
            "detection_hop_seconds": detection_hop_seconds,
            "capture_seconds": capture_seconds,
            "microphone_label": microphone_label,
            "microphone_device_index": microphone_device_index,
            "wakeword_backend": getattr(active_settings, "wakeword_backend", None),
            "wakeword_threshold": wakeword_threshold,
            "wakeword_keyword": getattr(active_settings, "wakeword_keyword", None)
            or getattr(active_settings, "wakeword", None),
            "wakeword_model_path": getattr(active_settings, "wakeword_model_path", None),
            "wakeword_embedding_path": getattr(active_settings, "wakeword_embedding_path", None),
            "wakeword_fallback_to_builtin": getattr(
                active_settings, "wakeword_fallback_to_builtin", True
            ),
            "wakeword_fallback_keyword": getattr(
                active_settings, "wakeword_fallback_keyword", "hey jarvis"
            ),
            "stt_model": whisper_model,
            "stt_device": device,
            "stt_language": language,
            "tts_provider": getattr(active_settings, "tts_provider", None),
            "tts_voice": getattr(active_settings, "tts_voice", None),
        },
    )

    emit({"type": "status", "status": "loading_plugins"})
    plugin_specs = load_plugins()
    emit({"type": "status", "status": "creating_assistant"})
    from rex.identity import validate_user_id  # type: ignore[import]

    def emit_turn_status(update) -> None:  # noqa: ANN001
        emit({"type": "turn_status", **update.to_dict()})

    status_projector = TurnStatusProjector(emit_turn_status)

    # Deliberate single-user profile selection (issue #303): Assistant no
    # longer invents an identity when user_id is omitted.
    assistant = _DeferredAssistant(
        lambda: Assistant(
            history_limit=active_settings.max_memory_items,
            plugins=plugin_specs,
            user_id=validate_user_id(_SESSION_USER_ID or ""),
        ),
        event_observer=status_projector.observe,
    )

    emit({"type": "status", "status": "initializing_microphone"})
    mic = AsyncMicrophone(
        sample_rate=sample_rate,
        detection_seconds=detection_seconds,
        detection_hop_seconds=detection_hop_seconds,
        capture_seconds=capture_seconds,
        device_index=microphone_device_index,
    )

    emit({"type": "status", "status": "loading_wakeword_detector"})
    wake_runtime_tracker = WakeWordRuntimeTracker(
        configured_phrase=configured_wake_phrase,
        configured_backend=configured_wake_backend,
        fallback_phrase=fallback_wake_phrase,
        microphone_label=microphone_label,
        portaudio_device_index=microphone_device_index,
    )
    wake_listener = build_default_detector(
        sample_rate=sample_rate,
        chunk_duration=detection_seconds,
        threshold=wakeword_threshold,
        poll_interval=getattr(active_settings, "wakeword_poll_interval", 0.01),
        keyword=configured_wake_phrase,
        model_path=getattr(active_settings, "wakeword_model_path", None),
        embedding_path=getattr(active_settings, "wakeword_embedding_path", None),
        backend=configured_wake_backend,
        fallback_to_builtin=getattr(active_settings, "wakeword_fallback_to_builtin", True),
        fallback_keyword=fallback_wake_phrase,
        event_callback=wake_runtime_tracker.handle,
    )

    emit({"type": "status", "status": "initializing_stt"})
    stt = SpeechToText(
        model_name=whisper_model,
        device=device,
        language=language,
        async_load=True,
    )
    emit({"type": "status", "status": "initializing_tts"})
    tts = _DeferredTextToSpeech(lambda: TextToSpeech(language=language))
    emit({"type": "status", "status": "initializing_acknowledgement"})
    ack = WakeAcknowledgement(is_speaking=tts.is_speaking)
    ack_mode = str(getattr(active_settings, "acknowledgment_mode", "none") or "none").lower()
    ack_sound = str(getattr(active_settings, "acknowledgment_sound", "") or "")

    async def wrapped_post_stt_ack() -> None:
        started_at = time.perf_counter()
        emit({"type": "status", "status": "request_captured"})
        emit({"type": "state", "state": "processing"})
        logger.info(
            "GUI voice bridge request captured acknowledgement started",
            extra={
                "event": "voice_bridge_request_ack_start",
                "voice_bridge_session_id": _VOICE_BRIDGE_SESSION_ID,
                "ack_mode": ack_mode,
            },
        )
        if ack_mode == "phrase":
            phrase = (
                ack_sound
                if ack_sound and not ack_sound.lower().endswith((".wav", ".mp3"))
                else "Got it."
            )
            await tts.speak(phrase)
        elif ack_mode == "sound":
            await ack.play()
        logger.info(
            "GUI voice bridge request captured acknowledgement finished",
            extra={
                "event": "voice_bridge_request_ack_end",
                "voice_bridge_session_id": _VOICE_BRIDGE_SESSION_ID,
                "ack_mode": ack_mode,
                "duration_s": round(time.perf_counter() - started_at, 3),
            },
        )

    # Wrap transcribe: emit processing state, then emit user transcript
    async def wrapped_transcribe(audio) -> str:  # type: ignore[type-arg]
        started_at = time.perf_counter()
        logger.info(
            "GUI voice bridge STT started",
            extra={
                "event": "voice_bridge_stt_start",
                "voice_bridge_session_id": _VOICE_BRIDGE_SESSION_ID,
                "audio_samples": len(audio) if hasattr(audio, "__len__") else None,
            },
        )
        emit({"type": "state", "state": "processing"})
        text = await stt.transcribe(audio, sample_rate)
        logger.info(
            "GUI voice bridge STT finished",
            extra={
                "event": "voice_bridge_stt_end",
                "voice_bridge_session_id": _VOICE_BRIDGE_SESSION_ID,
                "duration_s": round(time.perf_counter() - started_at, 3),
                "transcript": text,
            },
        )
        if text:
            emit(
                {
                    "type": "transcript",
                    "text": text,
                    "role": "user",
                    "timestamp": time_ms(),
                }
            )
        return text

    # Wrap speak: emit speaking state + rex transcript; VoiceLoop emits cooldown/idle.
    async def wrapped_speak(text: str) -> None:
        text = _sanitize_user_facing_voice_text(text)
        max_spoken_chars = int(getattr(active_settings, "tts_max_spoken_chars", 120) or 120)
        spoken_text, compact_speech_used = _compact_spoken_reply_for_gui(
            text,
            max_chars=max_spoken_chars,
        )
        fast_short_enabled = bool(getattr(active_settings, "tts_fast_short_reply_enabled", True))
        fast_short_max_chars = int(
            getattr(active_settings, "tts_fast_short_reply_max_chars", 140) or 140
        )
        prefer_fast_speech = fast_short_enabled and len(spoken_text) <= fast_short_max_chars
        started_at = time.perf_counter()
        logger.info(
            "GUI voice bridge reply ready; starting TTS playback path",
            extra={
                "event": "voice_bridge_reply_ready",
                "voice_bridge_session_id": _VOICE_BRIDGE_SESSION_ID,
                "reply_chars": len(text),
                "spoken_reply_chars": len(spoken_text),
                "compact_speech_used": compact_speech_used,
                "max_spoken_chars": max_spoken_chars,
                "fast_short_path_selected": prefer_fast_speech,
                "fast_short_max_chars": fast_short_max_chars,
                "reply_preview": text[:160],
                "spoken_reply_preview": spoken_text[:160],
            },
        )
        emit_log(
            "INFO",
            "GUI voice bridge TTS path selected",
            {
                "event": "voice_bridge_tts_path_selected",
                "voice_bridge_session_id": _VOICE_BRIDGE_SESSION_ID,
                "configured_tts_provider": getattr(active_settings, "tts_provider", None),
                "reply_chars": len(text),
                "spoken_reply_chars": len(spoken_text),
                "compact_speech_used": compact_speech_used,
                "fast_short_eligible": prefer_fast_speech,
                "fast_short_max_chars": fast_short_max_chars,
                "spoken_reply_preview": spoken_text[:160],
            },
        )
        emit({"type": "status", "status": "preparing_voice"})
        emit({"type": "state", "state": "speaking"})
        emit(
            {
                "type": "transcript",
                "text": text,
                "role": "rex",
                "timestamp": time_ms(),
            }
        )
        tts_result = await tts.speak(spoken_text, prefer_fast=prefer_fast_speech)
        emit_log(
            "INFO",
            "GUI voice bridge TTS path result",
            {
                "event": "voice_bridge_tts_path_result",
                "voice_bridge_session_id": _VOICE_BRIDGE_SESSION_ID,
                **(tts_result or {}),
            },
        )
        logger.info(
            "GUI voice bridge TTS playback path complete",
            extra={
                "event": "voice_bridge_tts_end",
                "voice_bridge_session_id": _VOICE_BRIDGE_SESSION_ID,
                "duration_s": round(time.perf_counter() - started_at, 3),
                "compact_speech_used": compact_speech_used,
                "fast_short_path_selected": prefer_fast_speech,
                "spoken_reply_chars": len(spoken_text),
                **(tts_result or {}),
            },
        )
        emit({"type": "status", "status": "voice_playback_complete"})

    wake_listening_event = asyncio.Event()

    def emit_voice_loop_state(status: str) -> None:
        if status in {"idle", "done"}:
            emit({"type": "state", "state": "idle"})
        elif status == "wake_listening":
            wake_listening_event.set()
            emit_log(
                "INFO",
                "Wake listener armed",
                {
                    "event": "wake_listen_armed",
                    "voice_bridge_session_id": _VOICE_BRIDGE_SESSION_ID,
                },
            )
            emit({"type": "state", "state": "wake_listening"})
        elif status == "cooldown":
            emit({"type": "state", "state": "cooldown"})
        elif status == "listening":
            emit({"type": "state", "state": "listening"})
        elif status == "followup_listening":
            emit_log(
                "INFO",
                "Voice bridge waiting for immediate follow-up",
                {
                    "event": "voice_followup_listening",
                    "voice_bridge_session_id": _VOICE_BRIDGE_SESSION_ID,
                },
            )
            emit({"type": "state", "state": "followup_listening"})
        elif status == "error":
            emit({"type": "error", "error": "Voice pipeline error; see logs for details."})
            emit({"type": "state", "state": "idle"})

    voice_loop = VoiceLoop(
        assistant,
        wake_listener=wake_listener,
        detection_source=mic.detection_frame,
        record_phrase=mic.record_phrase,
        transcribe=wrapped_transcribe,
        speak=wrapped_speak,
        acknowledge=ack.play,
        post_stt_acknowledge=wrapped_post_stt_ack,
        state_callback=emit_voice_loop_state,
        diagnostic_callback=emit_audio_diagnostic,
    )

    # Start stdin watcher after startup. On Windows, reading a live stdin pipe
    # in a background thread during heavy imports can stall bridge readiness.
    watcher = threading.Thread(target=_stdin_watcher, daemon=True)
    watcher.start()

    # Launch optional warmups before wake-listening is announced so the first
    # user-visible listening window does not also trigger lazy initialization.
    assistant.start_warmup()
    tts.start_warmup()

    # Run the voice loop as a cancellable task and only report bridge readiness
    # after it has reached the actual wake-listening state.
    emit({"type": "status", "status": "arming_wake_listener"})
    emit_log(
        "INFO",
        "GUI voice bridge wake listen requested",
        {
            "event": "voice_bridge_wake_listen_requested",
            "voice_bridge_session_id": _VOICE_BRIDGE_SESSION_ID,
        },
    )
    logger.info(
        "GUI voice bridge wake listen requested",
        extra={
            "event": "voice_bridge_wake_listen_requested",
            "voice_bridge_session_id": _VOICE_BRIDGE_SESSION_ID,
        },
    )
    loop_task = asyncio.create_task(voice_loop.run())
    try:
        await asyncio.wait_for(wake_listening_event.wait(), timeout=10.0)
    except TimeoutError:
        logger.error(
            "GUI voice bridge wake listener failed to arm",
            extra={
                "event": "voice_bridge_wake_listen_failed",
                "voice_bridge_session_id": _VOICE_BRIDGE_SESSION_ID,
                "timeout_s": 10.0,
            },
        )
        emit(
            {
                "type": "error",
                "error": "Wake-word listener did not arm within 10 seconds.",
            }
        )
        loop_task.cancel()
        with suppress(asyncio.CancelledError):
            await loop_task
        return

    logger.info(
        "GUI voice bridge wake listen acknowledged",
        extra={
            "event": "voice_bridge_wake_listen_acknowledged",
            "voice_bridge_session_id": _VOICE_BRIDGE_SESSION_ID,
        },
    )
    emit_log(
        "INFO",
        "GUI voice bridge wake listen acknowledged",
        {
            "event": "voice_bridge_wake_listen_acknowledged",
            "voice_bridge_session_id": _VOICE_BRIDGE_SESSION_ID,
        },
    )
    emit({"type": "ready", "mode": "wake_word"})
    logger.info(
        "GUI voice bridge ready for wake-word mode",
        extra={
            "event": "voice_bridge_ready",
            "voice_bridge_session_id": _VOICE_BRIDGE_SESSION_ID,
        },
    )

    # Cancel the voice loop task when stop_event fires
    async def _wait_for_stop() -> None:
        event_loop = asyncio.get_running_loop()
        await event_loop.run_in_executor(None, stop_event.wait)
        loop_task.cancel()

    stop_watcher = asyncio.create_task(_wait_for_stop())

    try:
        await loop_task
    except asyncio.CancelledError:
        pass
    finally:
        stop_watcher.cancel()
        with suppress(asyncio.CancelledError):
            await stop_watcher

    emit({"type": "state", "state": "idle"})


def main() -> None:
    global _MICROPHONE_LABEL, _SESSION_USER_ID
    parser = argparse.ArgumentParser(description="AskRex Electron voice bridge")
    parser.add_argument("--user", required=True, help="Validated Electron session user")
    parser.add_argument(
        "--microphone-label",
        default=None,
        help="Selected Chromium microphone label to map to the wake-word audio backend",
    )
    args = parser.parse_args()
    from rex.identity import validate_user_id

    _SESSION_USER_ID = validate_user_id(args.user)
    _MICROPHONE_LABEL = (args.microphone_label or "").strip() or None
    # Try to use the real voice loop. GUI wake-word mode should never silently
    # simulate listening because that hides microphone/dependency failures.
    try:
        asyncio.run(_run_real_loop())
    except ImportError as exc:
        # Voice dependencies (whisper, sounddevice, openWakeWord, etc.) missing
        emit({"type": "error", "error": f"Voice dependencies unavailable: {exc}"})
        sys.exit(1)
    except Exception as exc:
        emit({"type": "error", "error": str(exc), "traceback": _traceback.format_exc()})
        sys.exit(1)


if __name__ == "__main__":
    main()
