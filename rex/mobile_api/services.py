"""Process-level service container for the mobile API gateway.

Holds neutral, reusable dependencies only.  User-private state is never
cached here — every private operation receives a validated user ID
explicitly.  Clock, token, and ID generators are injectable so tests are
deterministic, and the runtime adapters (Assistant chat service, STT, TTS,
idempotency store) are injectable so tests never touch heavy ML
dependencies.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from rex.config import MobileApiConfig
from rex.mobile_api.attachments import MobileAttachmentStore
from rex.mobile_api.auth import load_jwt_secret
from rex.mobile_api.chat import MobileChatService
from rex.mobile_api.db import default_users_db_path
from rex.mobile_api.idempotency import MobileMessageStore
from rex.mobile_api.pairing import PairingAuthority
from rex.mobile_api.sessions import MobileSessionStore
from rex.mobile_api.strong_auth import StrongAuthAuthority
from rex.mobile_api.tls import (
    MobileTlsConfigurationError,
    TlsMaterial,
    TransportBinding,
    host_is_wildcard,
    resolve_mobile_tls,
)
from rex.mobile_api.voice import (
    MobileSpeechToTextService,
    MobileTextToSpeechService,
    SpeechToTextAdapter,
    TextToSpeechAdapter,
)


def _default_id_generator() -> str:
    return str(uuid.uuid4())


def _default_speech_services() -> tuple[MobileSpeechToTextService, MobileTextToSpeechService]:
    """Return the production default STT/TTS services (S35).

    When ``speech.enabled`` is false (the default), this returns the
    pre-existing :class:`SpeechToTextAdapter`/:class:`TextToSpeechAdapter`
    unchanged -- the explicit rollback path. Enabling it routes mobile voice
    through the provider-neutral ``SpeechRouter`` (native still registered
    unconditionally; VoiceStudio only when separately enabled).
    """
    from rex.config import settings as _global_settings  # noqa: PLC0415

    if not _global_settings.speech.enabled:
        return SpeechToTextAdapter(), TextToSpeechAdapter()

    from rex.speech.mobile_adapter import (  # noqa: PLC0415
        RoutedSpeechToTextAdapter,
        RoutedTextToSpeechAdapter,
    )
    from rex.speech.registry import build_default_router  # noqa: PLC0415

    router = build_default_router(_global_settings)
    return RoutedSpeechToTextAdapter(router), RoutedTextToSpeechAdapter(router)


@dataclass
class MobileApiServices:
    """Neutral dependencies shared by all mobile requests."""

    config: MobileApiConfig
    db_path: Path
    jwt_secret: str
    session_store: MobileSessionStore
    message_store: MobileMessageStore
    attachment_store: MobileAttachmentStore
    pairing_authority: PairingAuthority
    strong_auth_authority: StrongAuthAuthority
    chat_service: MobileChatService
    stt: MobileSpeechToTextService
    tts: MobileTextToSpeechService
    id_generator: Callable[[], str] = field(default=_default_id_generator)
    websocket_registered: bool = False
    tls_material: TlsMaterial | None = None
    transport_binding: TransportBinding | None = None

    @property
    def clock(self) -> Callable[[], datetime]:
        """Return the store's injected clock (single time source)."""
        return self.session_store.now

    @classmethod
    def build(
        cls,
        config: MobileApiConfig | None = None,
        *,
        db_path: Path | str | None = None,
        jwt_secret: str | None = None,
        clock: Callable[[], datetime] | None = None,
        token_generator: Callable[[], str] | None = None,
        id_generator: Callable[[], str] | None = None,
        audit_logger: object | None = None,
        message_store: MobileMessageStore | None = None,
        chat_service: MobileChatService | None = None,
        stt: MobileSpeechToTextService | None = None,
        tts: MobileTextToSpeechService | None = None,
    ) -> MobileApiServices:
        """Build the default production container with optional test overrides."""
        cfg = config or MobileApiConfig()
        resolved_db_path = Path(db_path) if db_path is not None else default_users_db_path()
        secret = jwt_secret if jwt_secret is not None else load_jwt_secret()
        # Fail closed here, before any store/session object exists, when
        # cfg.host is non-loopback and usable TLS material cannot be
        # provisioned (S7). Loopback binds stay plain HTTP unless cfg
        # explicitly opts into TLS.
        advertised_host = cfg.advertised_host or cfg.host
        if host_is_wildcard(cfg.host) and cfg.advertised_host is None:
            raise MobileTlsConfigurationError(
                "Wildcard mobile binds require a concrete advertised_host."
            )
        tls_material = resolve_mobile_tls(
            host=cfg.host,
            require_tls=cfg.require_tls,
            data_dir=resolved_db_path.parent,
            advertised_host=advertised_host,
        )
        transport_binding = (
            tls_material.binding(
                advertised_host=advertised_host,
                advertised_port=cfg.advertised_port or cfg.port,
            )
            if tls_material is not None
            else None
        )
        store = MobileSessionStore(
            resolved_db_path,
            refresh_ttl_seconds=cfg.refresh_token_ttl_days * 86400,
            clock=clock,
            token_generator=token_generator,
            id_generator=id_generator,
            audit_logger=audit_logger,
        )
        messages = message_store or MobileMessageStore(
            resolved_db_path,
            retention_hours=cfg.idempotency_retention_hours,
            clock=clock,
        )
        default_stt: MobileSpeechToTextService | None = None
        default_tts: MobileTextToSpeechService | None = None
        if stt is None or tts is None:
            default_stt, default_tts = _default_speech_services()
        return cls(
            config=cfg,
            db_path=resolved_db_path,
            jwt_secret=secret,
            session_store=store,
            message_store=messages,
            attachment_store=MobileAttachmentStore(
                resolved_db_path,
                retention_seconds=cfg.attachment_retention_seconds,
            ),
            pairing_authority=PairingAuthority(
                resolved_db_path,
                clock=clock,
                id_generator=id_generator,
                transport_binding_provider=(
                    (lambda: transport_binding) if transport_binding is not None else None
                ),
            ),
            strong_auth_authority=StrongAuthAuthority(
                resolved_db_path,
                clock=clock,
                id_generator=id_generator,
            ),
            chat_service=chat_service or MobileChatService(),
            stt=stt or default_stt,
            tts=tts or default_tts,
            id_generator=id_generator or _default_id_generator,
            tls_material=tls_material,
            transport_binding=transport_binding,
        )


__all__ = ["MobileApiServices"]
