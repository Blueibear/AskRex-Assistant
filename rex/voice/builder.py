"""Voice loop construction and voice-identity callback wiring — extracted verbatim from ``rex/voice_loop.py`` (US-REM-028)."""

from __future__ import annotations

import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from rex.assistant_errors import (
    AudioDeviceError,
)
from rex.memory import (
    extract_voice_reference,
    load_all_profiles,
    load_users_map,
    resolve_user_key,
)
from rex.voice._types import (
    AudioArray,
    IdentifySpeakerCallable,
    RecorderCallable,
)
from rex.voice.optional_imports import _lazy_import_numpy

if TYPE_CHECKING:
    from rex.voice.loop import VoiceLoop


def _vl():
    """Return the ``rex.voice_loop`` facade module at call time.

    ``rex.voice_loop`` remains the single patch point for settings, lazy
    importers, audio helpers, and pipeline classes (tests monkeypatch
    ``rex.voice_loop.<name>``). Resolving through the facade at call time
    preserves that behavior without an import cycle at module load time.
    """
    import importlib

    return importlib.import_module("rex.voice_loop")


def _build_voice_id_callback() -> IdentifySpeakerCallable | None:
    """Build an identify_speaker callback if voice identity is enabled.

    Reads the voice_identity config section, loads enrolled embeddings, and
    returns a callback that:
    - Converts a numpy audio array to PCM bytes
    - Generates an embedding via the configured backend
    - Runs recognition against all enrolled users
    - Calls resolve_speaker_identity() to update the session user

    Returns None when voice identity is disabled or no users are enrolled.
    All errors are caught and logged; the callback never raises.
    """
    try:
        from rex.config_manager import load_config as _load_json_config
        from rex.voice_identity.types import VoiceIdentityConfig

        raw_cfg = _load_json_config()
        vi_dict = raw_cfg.get("voice_identity", {})
        vi_cfg = VoiceIdentityConfig(
            enabled=vi_dict.get("enabled", False),
            accept_threshold=float(vi_dict.get("accept_threshold", 0.85)),
            review_threshold=float(vi_dict.get("review_threshold", 0.65)),
            embedding_dim=int(vi_dict.get("embedding_dim", 192)),
            model_id=str(vi_dict.get("model_id", "synthetic")),
        )
    except Exception as exc:
        _vl().logger.debug(
            "Could not load voice_identity config",
            extra={"event": "voice_identity_config_failed", "error_code": type(exc).__name__},
        )
        return None

    if not vi_cfg.enabled:
        return None

    try:
        from rex.voice_identity.embeddings_store import EmbeddingsStore
        from rex.voice_identity.optional_deps import get_embedding_backend
        from rex.voice_identity.recognizer import SpeakerRecognizer

        memory_dir = Path(__file__).resolve().parent.parent / "Memory"
        store = EmbeddingsStore(memory_dir)
        enrolled = store.load_all()

        if not enrolled:
            _vl().logger.info(
                "Voice identity enabled but no users are enrolled. "
                "Use 'rex voice-id enroll' to enroll users."
            )
            return None

        backend = get_embedding_backend(vi_cfg.model_id, dim=vi_cfg.embedding_dim)
        recognizer = SpeakerRecognizer(vi_cfg)

        _vl().logger.info(
            "Voice identity active: backend=%s, enrolled=%d user(s), " "accept=%.2f, review=%.2f",
            vi_cfg.model_id,
            len(enrolled),
            vi_cfg.accept_threshold,
            vi_cfg.review_threshold,
        )
    except ImportError as exc:
        _vl().logger.warning(
            "Voice identity backend unavailable. Install optional extras: pip install '.[voice-id]'",
            extra={"event": "voice_identity_backend_unavailable", "error_code": type(exc).__name__},
        )
        return None
    except Exception as exc:
        _vl().logger.warning(
            "Failed to initialise voice identity",
            extra={"event": "voice_identity_init_failed", "error_code": type(exc).__name__},
        )
        return None

    def _identify(audio: AudioArray) -> str | None:
        try:
            # Convert numpy float32 array to raw bytes for the embedding backend
            np_mod = _lazy_import_numpy()
            if np_mod is not None:
                pcm_bytes = np_mod.asarray(audio, dtype=np_mod.float32).tobytes()
            else:
                # Fallback: use bytes() if numpy unavailable at call time
                pcm_bytes = bytes(audio)

            vector = backend.embed(pcm_bytes)
            result = recognizer.recognize(vector, enrolled)

            from rex.voice_identity.fallback_flow import resolve_speaker_identity

            resolved = resolve_speaker_identity(result)

            if result.decision.value == "recognized":
                _vl().logger.info(
                    "Voice recognized",
                    extra={"event": "voice_identity_recognized", "score": round(result.score, 3)},
                )
            elif result.decision.value == "review":
                _vl().logger.info(
                    "Voice uncertain; run 'rex identify' to set user manually",
                    extra={"event": "voice_identity_review", "score": round(result.score, 3)},
                )

            return resolved
        except Exception as exc:
            _vl().logger.warning(
                "Voice identity check failed",
                extra={"event": "voice_identity_check_failed", "error_code": type(exc).__name__},
            )
            return None

    return _identify


def build_voice_loop(
    assistant,
    *,
    activation_mode: str = "wake-word",
    diagnostic_callback: Callable[[dict[str, object]], None] | None = None,
    sample_rate: int = 16000,
    detection_seconds: float = 1.0,
    capture_seconds: float | None = None,
    whisper_model: str = "base",
    device: str = "auto",
    language: str = "en",
    speaker_wav: str | None = None,
    wake_sound_path: Path | None = None,
) -> VoiceLoop:
    """Build a VoiceLoop with default components.

    When ``voice_identity.enabled=true`` is set in ``config/rex_config.json``
    and at least one user is enrolled, an ``identify_speaker`` callback is
    built and wired into the voice loop automatically.
    """
    _vl().logger.info(
        "[Pipeline] Initialising voice pipeline stages...",
        extra={"event": "pipeline_stage_start", "stage": "audio_device"},
    )
    if capture_seconds is None:
        configured_capture = getattr(_vl().settings, "capture_seconds", None)
        if not isinstance(configured_capture, (int, float, str)) or configured_capture == "":
            configured_capture = getattr(_vl().settings, "command_duration", 5.0)
        if not isinstance(configured_capture, (int, float, str)) or configured_capture == "":
            configured_capture = 5.0
        capture_seconds = float(configured_capture)
    try:
        input_device_index = _vl()._validate_input_device_index(_vl().settings.audio_input_device)
    except AudioDeviceError as exc:
        _vl().logger.error(
            "[Pipeline] Audio device stage failed",
            extra={
                "event": "pipeline_stage_failed",
                "stage": "audio_device",
                "error_code": type(exc).__name__,
            },
        )
        raise
    _vl().logger.info(
        "[Pipeline] Audio device stage OK (index=%s)",
        input_device_index,
        extra={
            "event": "pipeline_stage_ok",
            "stage": "audio_device",
            "device_index": input_device_index,
        },
    )

    if activation_mode not in {"hold-to-talk", "wake-word"}:
        raise ValueError(f"Unsupported voice activation mode: {activation_mode!r}")

    # Smart speaker microphone input (US-SP-003)
    smart_mic_recorder = None
    wake_word_device = getattr(_vl().settings, "wake_word_input_device", None)
    if wake_word_device and wake_word_device != "auto":
        try:
            from rex.audio.smart_speaker_mic import SmartSpeakerMic
            from rex.audio.speaker_discovery import get_speaker_discovery

            cached = get_speaker_discovery().get_cached_speakers()
            target = next((s for s in cached if s.name == wake_word_device), None)
            if target is not None:
                smart_mic = SmartSpeakerMic(
                    provider=target.provider,
                    ip=target.ip,
                    sample_rate=sample_rate,
                )
                if smart_mic.connect():
                    smart_mic_recorder = cast(RecorderCallable, smart_mic.read_frame)
                    _vl().logger.info(
                        "[voice] Wake word input routed to configured smart speaker",
                        extra={"event": "smart_speaker_mic_selected", "provider": target.provider},
                    )
                else:
                    _vl().logger.warning(
                        "[voice] Smart speaker mic %r unavailable; falling back to local mic.",
                        wake_word_device,
                    )
            else:
                _vl().logger.warning(
                    "[voice] Wake word device %r not found in cached speakers; using local mic.",
                    wake_word_device,
                )
        except Exception as exc:
            _vl().logger.warning(
                "[voice] Smart speaker mic setup failed; using local mic",
                extra={"event": "smart_speaker_mic_setup_failed", "error_code": type(exc).__name__},
            )

    mic = _vl().AsyncMicrophone(
        sample_rate=sample_rate,
        detection_seconds=detection_seconds,
        capture_seconds=capture_seconds,
        device_index=input_device_index,
        recorder=smart_mic_recorder,
    )

    wake_listener: Any
    if activation_mode == "hold-to-talk":
        from rex.voice.activation import ManualActivationListener

        wake_listener = ManualActivationListener()
        _vl().logger.info(
            "[Pipeline] Manual hold-to-talk activation ready; wake detector not loaded",
            extra={
                "event": "pipeline_stage_ok",
                "stage": "activation",
                "activation_mode": activation_mode,
            },
        )
    else:
        from rex.wakeword.listener import build_default_detector

        _vl().logger.info(
            "[Pipeline] Initialising wake-word detector...",
            extra={"event": "pipeline_stage_start", "stage": "wake_word"},
        )
        try:
            wake_listener = build_default_detector(
                sample_rate=sample_rate,
                chunk_duration=detection_seconds,
                threshold=getattr(_vl().settings, "wakeword_threshold", 0.1),
                poll_interval=getattr(_vl().settings, "wakeword_poll_interval", 0.01),
                keyword=getattr(_vl().settings, "wakeword_keyword", None)
                or getattr(_vl().settings, "wakeword", None),
                model_path=getattr(_vl().settings, "wakeword_model_path", None),
                embedding_path=getattr(_vl().settings, "wakeword_embedding_path", None),
                backend=getattr(_vl().settings, "wakeword_backend", None),
                fallback_to_builtin=getattr(_vl().settings, "wakeword_fallback_to_builtin", True),
                fallback_keyword=getattr(_vl().settings, "wakeword_fallback_keyword", "hey jarvis"),
            )
        except Exception as exc:
            _vl().logger.error(
                "[Pipeline] Wake-word stage failed",
                extra={
                    "event": "pipeline_stage_failed",
                    "stage": "wake_word",
                    "error_code": type(exc).__name__,
                },
            )
            raise
        _vl().logger.info(
            "[Pipeline] Wake-word detector ready",
            extra={"event": "pipeline_stage_ok", "stage": "wake_word"},
        )

    _vl().logger.info(
        "[Pipeline] Initialising STT (model=%s, device=%s)...",
        whisper_model,
        device,
        extra={
            "event": "pipeline_stage_start",
            "stage": "stt",
            "model": whisper_model,
            "device": device,
        },
    )
    stt = _vl().SpeechToText(
        model_name=whisper_model,
        device=device,
        language=language,
        async_load=True,
    )
    _vl().logger.info(
        "[Pipeline] STT initialised (background model load in progress)",
        extra={"event": "pipeline_stage_ok", "stage": "stt"},
    )

    _vl().logger.info(
        "[Pipeline] Initialising TTS (language=%s)...",
        language,
        extra={"event": "pipeline_stage_start", "stage": "tts", "language": language},
    )
    tts = _vl().TextToSpeech(language=language, default_speaker=speaker_wav)
    _vl().logger.info(
        "[Pipeline] TTS initialised (provider=%s)",
        tts._provider,
        extra={"event": "pipeline_stage_ok", "stage": "tts", "provider": tts._provider},
    )

    # AC US-020 #1: warn at startup if FFmpeg is absent and XTTS is active.
    # XTTS relies on torio which uses FFmpeg for audio decoding; other providers
    # (edge-tts, pyttsx3) work without it so the warning is scoped to XTTS.
    if tts._provider == "xtts" and shutil.which("ffmpeg") is None:
        _vl().logger.warning(
            "[Pipeline] FFmpeg not found on PATH. XTTS requires FFmpeg for audio "
            "decoding. Install FFmpeg or switch to a different TTS provider. "
            "Windows: https://ffmpeg.org/download.html  "
            "macOS: brew install ffmpeg  "
            "Linux: sudo apt install ffmpeg",
            extra={"event": "ffmpeg_missing", "tts_provider": tts._provider},
        )

    async def _speak_for_callback(text: str) -> None:
        await tts.speak(text, speaker_wav=speaker_wav)

    async def _speak_default_for_callback(text: str) -> None:
        await tts.speak(text)

    async def _post_stt_phrase_callback() -> None:
        await tts.speak("On it")

    ack_sound = getattr(_vl().settings, "acknowledgment_sound", "chime")
    if ack_sound and ack_sound != "chime" and not ack_sound.lower().endswith((".wav", ".mp3")):
        # Spoken filler phrase (e.g. "mm-hmm", "one moment")
        ack = _vl().WakeAcknowledgement(
            filler_phrase=ack_sound,
            is_speaking=tts.is_speaking,
            filler_speak=_speak_default_for_callback,
        )
    elif ack_sound and ack_sound != "chime":
        # Custom audio file path
        ack = _vl().WakeAcknowledgement(
            sound_path=Path(ack_sound),
            is_speaking=tts.is_speaking,
        )
    else:
        # Default chime (use wake_sound_path override if provided)
        ack = _vl().WakeAcknowledgement(
            sound_path=wake_sound_path,
            is_speaking=tts.is_speaking,
        )

    identify_speaker = _build_voice_id_callback()

    # Build post-STT acknowledgment based on acknowledgment_mode config.
    # "sound" → play the chime after STT; "phrase" → speak a filler phrase;
    # "none" → no post-STT acknowledgment.
    ack_mode = getattr(_vl().settings, "acknowledgment_mode", "sound")
    post_stt_ack: Callable[[], Awaitable[None]] | None
    if ack_mode == "phrase":
        post_stt_ack = _post_stt_phrase_callback
    elif ack_mode == "sound":
        post_stt_ack = ack.play
    else:
        post_stt_ack = None

    _vl().logger.info(
        "[Pipeline] All stages ready — voice loop active",
        extra={"event": "pipeline_ready"},
    )

    return cast(
        "VoiceLoop",
        _vl().VoiceLoop(
            assistant,
            wake_listener=wake_listener,
            detection_source=mic.detection_frame,
            record_phrase=mic.record_phrase,
            transcribe=lambda audio: stt.transcribe(audio, sample_rate),
            speak=_speak_for_callback,
            speak_streaming=lambda sentences: tts.speak_streaming(
                sentences, speaker_wav=speaker_wav
            ),
            warmup=lambda: tts.warmup(speaker_wav=speaker_wav),
            acknowledge=ack.play,
            post_stt_acknowledge=post_stt_ack,
            identify_speaker=identify_speaker,
            diagnostic_callback=diagnostic_callback,
            sample_rate=sample_rate,
        ),
    )


def _resolve_voice_reference() -> str | None:
    """Resolve the selected user's voice reference.

    Returns:
        Path to voice sample file, or None if not configured
    """
    try:
        users_map = load_users_map()
        profiles = load_all_profiles()

        active_user = _vl().settings.default_user
        if not active_user:
            return None
        user_key = resolve_user_key(active_user, users_map, profiles=profiles)

        if not user_key:
            return None

        # Load profile and extract voice reference
        if user_key in profiles:
            return extract_voice_reference(profiles[user_key], user_key=user_key)

        return None
    except Exception as exc:
        _vl().logger.warning(
            "Failed to resolve voice reference",
            extra={"event": "voice_reference_resolution_failed", "error_code": type(exc).__name__},
        )
        return None
