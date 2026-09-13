"""Native provider wrappers over the existing Whisper/XTTS/edge-tts/pyttsx3 stack.

These wrap :class:`rex.mobile_api.voice.SpeechToTextAdapter` and
:class:`rex.mobile_api.voice.TextToSpeechAdapter` -- the existing, already
production-used STT/TTS seams -- as ``SpeechRouter`` providers. No engine
logic is reimplemented here; this is representation only (S35 phase 2).
"""

from __future__ import annotations

from typing import Any

from rex.mobile_api.voice import SpeechToTextAdapter, TextToSpeechAdapter

NATIVE_PROVIDER_ID = "native"

# edge-tts synthesizes via a remote Microsoft cloud endpoint; the other native
# engines (xtts, pyttsx3) run entirely on-device.
_CLOUD_BACKED_TTS_ENGINES = {"edge-tts"}


class NativeSTTProvider:
    """Provider-neutral wrapper over the existing Whisper STT adapter."""

    provider_id = NATIVE_PROVIDER_ID
    is_local = True

    def __init__(self, adapter: SpeechToTextAdapter | None = None) -> None:
        self._adapter = adapter or SpeechToTextAdapter()

    def availability(self) -> tuple[bool, str]:
        return self._adapter.availability()

    def decode(self, path: str) -> Any:
        return self._adapter.decode(path)

    def transcribe(self, audio: Any) -> str:
        return self._adapter.transcribe(audio)


class NativeTTSProvider:
    """Provider-neutral wrapper over the existing native TTS adapter.

    ``is_local`` reflects the *configured* native engine: xtts/pyttsx3 run
    on-device, while edge-tts synthesizes through a remote Microsoft
    endpoint and is therefore treated as cloud-backed for Local Only policy.
    """

    provider_id = NATIVE_PROVIDER_ID

    def __init__(self, adapter: TextToSpeechAdapter | None = None) -> None:
        self._adapter = adapter or TextToSpeechAdapter()

    @property
    def is_local(self) -> bool:
        return self._adapter.provider() not in _CLOUD_BACKED_TTS_ENGINES

    def availability(self) -> tuple[bool, str]:
        return self._adapter.availability()

    def mime_type(self) -> str:
        return self._adapter.mime_type()

    def list_voice_ids(self) -> list[str]:
        return self._adapter._list_voice_ids()

    def resolve_voice(self, requested: str | None) -> str:
        return self._adapter.resolve_voice(requested)

    def synthesize(self, text: str, voice_id: str) -> bytes:
        return self._adapter.synthesize(text, voice_id)


__all__ = ["NATIVE_PROVIDER_ID", "NativeSTTProvider", "NativeTTSProvider"]
