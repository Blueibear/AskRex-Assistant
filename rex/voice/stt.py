"""Speech-to-text (Whisper) — extracted verbatim from ``rex/voice_loop.py`` (US-REM-028)."""

from __future__ import annotations

import asyncio
import threading
from typing import Any, cast

from rex.assistant_errors import (
    AudioFormatError,
    SpeechToTextError,
)
from rex.voice._types import (
    _USE_CONFIG_LANGUAGE,
    AudioArray,
)
from rex.voice.audio_utils import (
    _audio_quality_summary,
    _detect_audio_format,
    _voice_log_extra,
)
from rex.voice.transcripts import (
    _DEFAULT_STT_INITIAL_PROMPT,
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


class SpeechToText:
    """Speech-to-text using Whisper."""

    def __init__(
        self,
        model_name: str,
        device: str,
        language: str | None | object = _USE_CONFIG_LANGUAGE,
        *,
        async_load: bool = False,
    ) -> None:
        whisper_module = _vl()._lazy_import_whisper()
        if whisper_module is None:
            raise SpeechToTextError("openai-whisper is not installed")

        if language is _USE_CONFIG_LANGUAGE:
            language = getattr(_vl().settings, "whisper_language", "en")
        # Normalise "auto" and "" to None so Whisper uses its built-in auto-detect.
        if language in ("auto", ""):
            language = None
        self._language = cast(str | None, language)
        configured_prompt = getattr(_vl().settings, "whisper_initial_prompt", None)
        self._initial_prompt = (
            str(configured_prompt).strip()
            if configured_prompt
            else (_DEFAULT_STT_INITIAL_PROMPT if self._language in (None, "en") else "")
        )

        if device == "auto":
            try:
                import torch

                device = "cuda" if torch.cuda.is_available() else "cpu"
            except ImportError:
                device = "cpu"
        self._device = device
        self._model_name = model_name
        self._whisper_module = whisper_module
        self._model: Any = None
        self._warm_manager: Any = None
        self._warm_component_name: str | None = None
        self._load_event = threading.Event()
        self._load_error: str | None = None

        if async_load:
            t = threading.Thread(target=self._load_model, daemon=True, name="stt-warmup")
            t.start()
        else:
            self._load_model()
            if self._load_error is not None:
                raise SpeechToTextError(self._load_error)

    def _load_model(self) -> None:
        """Load or attach the process-warm Whisper model."""
        try:
            from rex.runtime.warm import (
                WarmComponentSpec,
                default_idle_timeout,
                get_global_warm_runtime,
                warm_component_key,
            )

            manager = get_global_warm_runtime(_vl().settings)
            whisper_module = self._whisper_module
            model_name = self._model_name
            device = self._device
            component_name = warm_component_key("stt", model_name, device, id(whisper_module))
            manager.register_if_absent(
                WarmComponentSpec(
                    name=component_name,
                    loader=lambda: whisper_module.load_model(model_name, device=device),
                    estimated_cost_mb=1024.0,
                    idle_timeout_s=default_idle_timeout(),
                )
            )
            manager.warm(component_name)
            self._warm_manager = manager
            self._warm_component_name = component_name
            _vl().logger.info("[STT] Model '%s' loaded on %s", self._model_name, self._device)
        except Exception as exc:
            self._load_error = type(exc).__name__
            _vl().logger.error(
                "[STT] Model load failed",
                extra=_voice_log_extra(
                    event="stt_model_load_failed", error_code=type(exc).__name__
                ),
            )
        finally:
            self._load_event.set()

    def _resolve_model(self) -> Any:
        """Return the active model, reloading through the bounded manager after eviction."""
        manager = getattr(self, "_warm_manager", None)
        component_name = getattr(self, "_warm_component_name", None)
        if manager is not None and component_name:
            return manager.get(component_name)
        return self._model

    def is_loaded(self) -> bool:
        """Return True when the Whisper model has finished loading without error."""
        return self._load_event.is_set() and self._load_error is None

    async def transcribe(
        self,
        audio: AudioArray | bytes | bytearray | memoryview,
        sample_rate: int,
    ) -> str:
        """Transcribe audio to text."""

        model_name = getattr(self, "_model_name", "whisper")
        device = getattr(self, "_device", "unknown")
        initial_prompt = getattr(self, "_initial_prompt", "")

        load_event = getattr(self, "_load_event", None)
        if load_event is not None and not load_event.is_set():
            _vl().logger.info("[STT] Waiting for model warm-up to complete...")
            await asyncio.to_thread(load_event.wait)

        if self._load_error is not None:
            raise SpeechToTextError(f"Model failed to load: {self._load_error}")

        prepared_audio = _vl()._prepare_audio_for_stt(audio)
        _vl().logger.info(
            "[STT] Audio input prepared",
            extra=_voice_log_extra(
                event="stt_audio_quality",
                model=model_name,
                device=device,
                language=self._language or "auto",
                condition_on_previous_text=False,
                initial_prompt_enabled=bool(initial_prompt),
                **_audio_quality_summary(prepared_audio, sample_rate),
            ),
        )
        audio_buffer = _vl()._to_wav_buffer(prepared_audio, sample_rate)
        if audio_buffer[:4] != b"RIFF":
            detected_format = _detect_audio_format(audio_buffer)
            raise AudioFormatError(f"Expected WAV, got {detected_format}")

        def _transcribe_with_model(model: Any) -> str:
            def run_transcribe(language: str | None) -> dict[str, Any]:
                kwargs: dict[str, Any] = {
                    "language": language,
                    "fp16": False,
                    "condition_on_previous_text": False,
                }
                if initial_prompt:
                    kwargs["initial_prompt"] = initial_prompt
                try:
                    return cast(dict[str, Any], model.transcribe(prepared_audio, **kwargs))
                except TypeError:
                    _vl().logger.debug(
                        "[STT] Whisper version does not support all transcription options; "
                        "retrying with basic options",
                    )
                    return cast(
                        dict[str, Any],
                        model.transcribe(
                            prepared_audio,
                            language=language,
                            fp16=False,
                        ),
                    )

            try:
                result = run_transcribe(self._language)
            except Exception:
                if self._language is None:
                    _vl().logger.warning(
                        "[STT] Auto-detect not supported; falling back to language='en'"
                    )
                    result = run_transcribe("en")
                else:
                    raise
            return str(result.get("text", "")).strip()

        def _transcribe() -> str:
            manager = getattr(self, "_warm_manager", None)
            component_name = getattr(self, "_warm_component_name", None)
            if manager is not None and component_name:
                with manager.acquire(component_name) as model:
                    return _transcribe_with_model(model)
            return _transcribe_with_model(self._model)

        try:
            return await asyncio.to_thread(_transcribe)
        except Exception as exc:
            _vl().logger.error(
                "[STT] Whisper failed",
                extra=_voice_log_extra(
                    event="stt_transcription_failed", error_code=type(exc).__name__
                ),
            )
            raise SpeechToTextError("Speech transcription failed") from exc
