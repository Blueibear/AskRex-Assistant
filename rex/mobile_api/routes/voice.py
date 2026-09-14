"""Authenticated mobile voice and TTS routes (issue #323, Session 2).

``POST /mobile/voice/upload``  — multipart audio → STT → canonical
Assistant → optional TTS of the reply.
``POST /mobile/tts/playback``  — JSON text → existing configured TTS →
authenticated JSON base64 audio.

Security and truthfulness:

- Bearer authentication is required before any multipart parsing, temp
  file, STT, or synthesis work.
- The declared MIME type and filename are never trusted: actual byte
  signatures are sniffed and a successful decode is required.
- Limits: 15 MiB and 60 seconds for uploads (config), bounded TTS text.
- Temporary audio lives in a private per-request directory and is deleted
  on every success, failure, timeout, and cancellation path.
- Missing STT/TTS runtime dependencies return truthful
  ``BACKEND_UNAVAILABLE`` — no model downloads mid-request, no mock
  transcripts, no fake verified actions.
- Audio bytes, transcripts, chat text, and TTS text are never logged.
"""

from __future__ import annotations

import base64
import json
import logging
import tempfile
from pathlib import Path
from typing import Any

from flask import Blueprint, g, jsonify, request

from rex.mobile_api import errors as merr
from rex.mobile_api.auth import require_mobile_auth, revalidate_principal
from rex.mobile_api.authorization import ROUTE_SCOPES
from rex.mobile_api.errors import MobileApiError
from rex.mobile_api.services import MobileApiServices
from rex.mobile_api.voice import (
    MAX_TTS_TEXT_CHARS,
    WHISPER_SAMPLE_RATE,
    sniff_audio_container,
    synthesize_for_request,
)

logger = logging.getLogger(__name__)

# Fields that must never appear in the upload form (identity comes from the
# validated principal only).
_FORBIDDEN_FORM_FIELDS = {"user_id", "role", "permissions", "risk", "approval", "biometric"}
_VOICE_MODE = "mobile_voice"


def _reject_forbidden_form_fields() -> None:
    present = _FORBIDDEN_FORM_FIELDS.intersection(request.form.keys())
    if present:
        names = ", ".join(sorted(present))
        raise MobileApiError(merr.BAD_REQUEST, f"Unsupported field(s): {names}.", 400)


def _validate_upload_form() -> bytes:
    """Validate the multipart form and return the raw audio bytes."""
    if not request.content_type or "multipart/form-data" not in request.content_type:
        raise MobileApiError(merr.INVALID_MEDIA, "Content-Type must be multipart/form-data.", 415)
    _reject_forbidden_form_fields()

    mode = request.form.get("mode", _VOICE_MODE)
    if mode != _VOICE_MODE:
        raise MobileApiError(merr.BAD_REQUEST, f"Field 'mode' must be '{_VOICE_MODE}'.", 400)

    context_raw = request.form.get("client_context")
    if context_raw:
        try:
            parsed_context = json.loads(context_raw)
        except ValueError as exc:
            raise MobileApiError(
                merr.BAD_REQUEST, "Field 'client_context' must be JSON.", 400
            ) from exc
        if not isinstance(parsed_context, dict):
            raise MobileApiError(
                merr.BAD_REQUEST, "Field 'client_context' must be a JSON object.", 400
            )

    audio_parts = request.files.getlist("audio")
    if len(audio_parts) != 1:
        raise MobileApiError(merr.BAD_REQUEST, "Exactly one 'audio' file part is required.", 400)
    extra_files = [key for key in request.files if key != "audio"]
    if extra_files:
        raise MobileApiError(merr.BAD_REQUEST, "Unexpected file part(s) in upload.", 400)

    return bytes(audio_parts[0].read())


def _decode_upload(services: MobileApiServices, data: bytes) -> str:
    cfg = services.config
    container = sniff_audio_container(data)
    if container is None:
        raise MobileApiError(
            merr.INVALID_MEDIA, "Unsupported audio format. Use M4A/MP4, AAC, MP3, or WAV.", 415
        )
    with tempfile.TemporaryDirectory(prefix="rex-mobile-voice-") as tmp_dir:
        tmp_path = Path(tmp_dir) / f"upload.{container}"
        tmp_path.write_bytes(data)
        audio = services.stt.decode(str(tmp_path))
        duration_seconds = float(len(audio)) / WHISPER_SAMPLE_RATE
        if duration_seconds > cfg.max_audio_seconds:
            raise MobileApiError(
                merr.PAYLOAD_TOO_LARGE,
                f"Audio is longer than {cfg.max_audio_seconds} seconds.",
                413,
            )
        return services.stt.transcribe(audio)


def _add_optional_tts(services: MobileApiServices, body: dict[str, Any]) -> None:
    response_text = str(body["response"])
    tts_available, _ = services.tts.availability()
    if not tts_available or not response_text:
        return
    try:
        # One provider decision: the reported MIME type always describes the
        # provider that actually synthesized this audio, even after a
        # SpeechRouter fallback selected a different provider.
        synthesized = synthesize_for_request(services.tts, response_text, None)
        body["ttsBase64"] = base64.b64encode(synthesized.audio).decode("ascii")
    except MobileApiError:
        logger.info("Voice reply TTS unavailable; returning text only")


def _handle_voice_upload(services: MobileApiServices) -> Any:
    cfg = services.config
    if request.content_length is not None and request.content_length > (
        cfg.max_audio_bytes + 128 * 1024
    ):
        raise MobileApiError(merr.PAYLOAD_TOO_LARGE, "The upload is too large.", 413)
    revalidate_principal(
        services,
        g.mobile_principal,
        required_scope=ROUTE_SCOPES["voice.upload"],
    )
    services.stt.require_available()
    data = _validate_upload_form()
    if not data:
        raise MobileApiError(merr.INVALID_MEDIA, "The audio file is empty.", 415)
    if len(data) > cfg.max_audio_bytes:
        raise MobileApiError(merr.PAYLOAD_TOO_LARGE, "The audio file is too large.", 413)
    transcript = _decode_upload(services, data)
    if not transcript:
        raise MobileApiError(merr.INVALID_MEDIA, "No speech was recognized in the audio.", 415)
    revalidate_principal(
        services,
        g.mobile_principal,
        required_scope=ROUTE_SCOPES["voice.upload"],
    )

    def authorization_check() -> None:
        revalidate_principal(
            services,
            g.mobile_principal,
            required_scope=ROUTE_SCOPES["voice.upload"],
        )

    response_text = services.chat_service.generate(
        transcript,
        user_id=g.mobile_principal.user_id,
        device_id=g.mobile_principal.paired_device_id,
        voice_mode=True,
        capability_scopes=g.mobile_principal.scopes,
        capability_permissions=g.mobile_principal.permissions,
        authorization_check=authorization_check,
    )
    revalidate_principal(
        services,
        g.mobile_principal,
        required_scope=ROUTE_SCOPES["voice.upload"],
    )
    body: dict[str, Any] = {
        "request_id": getattr(g, "request_id", None),
        "transcript": transcript,
        "response": response_text,
        # The mobile VoiceResult contract owns this status vocabulary.  It is
        # deliberately separate from the canonical chat/action status, so a
        # conversational response never claims action verification.
        "status": "attempted",
        "toolUsed": None,
    }
    _add_optional_tts(services, body)
    return jsonify(body), 200


def _handle_tts_playback(services: MobileApiServices) -> Any:
    from rex.mobile_api.validation import parse_json_body  # noqa: PLC0415

    payload = parse_json_body()
    unknown = set(payload) - {"text", "voice"}
    if unknown:
        names = ", ".join(sorted(unknown))
        raise MobileApiError(merr.BAD_REQUEST, f"Unsupported field(s): {names}.", 400)
    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        raise MobileApiError(merr.BAD_REQUEST, "Field 'text' is required.", 400)
    text = text.strip()
    if len(text) > MAX_TTS_TEXT_CHARS:
        raise MobileApiError(merr.BAD_REQUEST, "Field 'text' is too long.", 400)
    voice = payload.get("voice")
    if voice is not None and not isinstance(voice, str):
        raise MobileApiError(merr.BAD_REQUEST, "Field 'voice' must be a string.", 400)
    revalidate_principal(
        services,
        g.mobile_principal,
        required_scope=ROUTE_SCOPES["tts.playback"],
    )
    services.tts.require_available()
    # ``voice`` is the AskRex-owned request (alias or provider voice ID).
    # ``synthesized.voice_id`` is what the provider that actually produced the
    # audio used, so a fallback never reports another provider's voice.
    synthesized = synthesize_for_request(services.tts, text, voice)
    revalidate_principal(
        services,
        g.mobile_principal,
        required_scope=ROUTE_SCOPES["tts.playback"],
    )
    return (
        jsonify(
            {
                "request_id": getattr(g, "request_id", None),
                # The authenticated response keeps audio inline.  The mobile
                # client consumes a URL, so expose a self-contained data URL
                # instead of an unauthenticated artifact URL.
                "audio_url": (
                    f"data:{synthesized.mime_type};base64,"
                    f"{base64.b64encode(synthesized.audio).decode('ascii')}"
                ),
            }
        ),
        200,
    )


def build_voice_blueprint(services: MobileApiServices, limiter: Any) -> Blueprint:
    bp = Blueprint("mobile_voice", __name__, url_prefix="/mobile")

    @bp.post("/voice/upload")
    @limiter.limit(services.config.rate_limit_voice)
    @require_mobile_auth(required_scope=ROUTE_SCOPES["voice.upload"])
    def voice_upload() -> Any:
        return _handle_voice_upload(services)

    @bp.post("/tts/playback")
    @limiter.limit(services.config.rate_limit_voice)
    @require_mobile_auth(required_scope=ROUTE_SCOPES["tts.playback"])
    def tts_playback() -> Any:
        return _handle_tts_playback(services)

    return bp


__all__ = ["build_voice_blueprint"]
