"""Truthful mobile capability reporting (issue #323).

A feature is ``true`` only when its real backend path exists, its focused
automated tests exist, and its required runtime dependencies are available
right now.  Configuration alone never makes a capability true, and the
response must never expose secrets, file paths, model paths, account IDs,
usernames, or integration tokens.

Session 2 implements chat (HTTP), SSE streaming, WebSocket chat, voice
upload, and TTS.  ``chat``/``chat_streaming`` are code-complete and tested;
``websocket_chat`` additionally requires the validated Flask-Sock stack;
``voice_upload`` requires Whisper + ffmpeg + a locally cached model;
``tts`` requires the configured engine's dependency.

``attachments`` (US-086 slice) covers ``POST /mobile/attachments`` and the
``attachment_ids``/``attachments`` chat fields.  It has no optional ML
runtime dependency: private per-conversation storage is always available
once the gateway process starts, so it is reported unconditionally true,
like ``authentication``.

``live_voice``, ``notifications``, ``approvals``, and ``home_assistant``
remain false: their complete server-authoritative mobile paths are not
implemented.  A configured Home Assistant integration alone does not make
the mobile ``home_assistant`` capability true.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from rex.config import MobileApiConfig

if TYPE_CHECKING:
    from rex.mobile_api.services import MobileApiServices

logger = logging.getLogger(__name__)

MINIMUM_APP_VERSION = "0.1.0"


def server_version() -> str:
    """Return the installed package version, or a safe fallback value."""
    try:
        from importlib.metadata import version  # noqa: PLC0415

        return version("askrex-assistant")
    except Exception:  # pragma: no cover - metadata edge cases
        return "unknown"


def resolve_features(services: MobileApiServices | None = None) -> dict[str, bool]:
    """Return the truthful feature map for the current build and runtime.

    ``services`` provides the runtime adapters whose dependency checks make
    voice/TTS truthful; without it (bare config contexts) those features are
    reported false rather than guessed.
    """
    chat = False
    voice_upload = False
    tts = False
    if services is not None:
        try:
            chat = services.chat_service.availability()[0]
        except Exception:  # adapter/configuration failure means false
            logger.warning("Chat capability check failed", exc_info=True)
        try:
            voice_upload = services.stt.availability()[0]
        except Exception:  # pragma: no cover - adapter failure means false
            logger.warning("Voice capability check failed", exc_info=True)
        try:
            tts = services.tts.availability()[0]
        except Exception:  # pragma: no cover - adapter failure means false
            logger.warning("TTS capability check failed", exc_info=True)

    return {
        "authentication": True,
        "chat": chat,
        "chat_streaming": chat,
        "websocket_chat": chat and bool(services and services.websocket_registered),
        "voice_upload": chat and voice_upload,
        "tts": tts,
        "attachments": True,
        # Not implemented as complete server-authoritative mobile paths:
        "live_voice": False,
        "notifications": False,
        "approvals": False,
        "home_assistant": False,
    }


def capabilities_payload(
    config: MobileApiConfig, services: MobileApiServices | None = None
) -> dict[str, Any]:
    """Build the ``GET /mobile/capabilities`` response body."""
    return {
        "api_version": config.api_version,
        "minimum_app_version": MINIMUM_APP_VERSION,
        "server_version": server_version(),
        "features": resolve_features(services),
    }


__all__ = [
    "MINIMUM_APP_VERSION",
    "capabilities_payload",
    "resolve_features",
    "server_version",
]
