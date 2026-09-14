"""Central configuration loader and CLI utilities for the Rex assistant.

Uses rex_config.json for non-secret settings and the OS-backed vault for secrets.
"""

from __future__ import annotations

# ruff: noqa: I001, UP006, UP035, UP045

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from urllib.parse import urlparse

from rex.exception_handler import wrap_entrypoint
from pathlib import Path
import warnings

from typing import ClassVar, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, field_validator

try:
    from dotenv import load_dotenv, set_key
except ImportError:

    def load_dotenv(*args, **kwargs):  # type: ignore[misc]
        return False

    def set_key(env_path: str, key: str, value: str):  # type: ignore[misc]
        path = Path(env_path)
        lines = (
            [line for line in path.read_text().splitlines() if not line.startswith(f"{key}=")]
            if path.exists()
            else []
        )
        lines.append(f"{key}={value}")
        path.write_text("\n".join(lines) + "\n")
        return key, value, True


from rex.assistant_errors import ConfigurationError
from rex.config_manager import get_legacy_env_warnings
from rex.log_paths import DEFAULT_ERROR_LOG_FILE, DEFAULT_RUNTIME_LOG_FILE
from rex.logging_utils import get_logger, set_global_level
from rex.runtime_paths import env_path as resolve_env_path
from rex.runtime_paths import household_data_path

from rex.profile_manager import (
    DEFAULT_PROFILES_DIR,
    apply_profile,
    ensure_default_profile,
    get_active_profile_name,
    load_profile,
)

LOGGER = get_logger(__name__)
OPENROUTER_DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
ENV_PATH = resolve_env_path()


def _parse_int(name: str, value: Optional[str], *, default: int = 0) -> int:
    """Parse integer from string value.

    Args:
        name: Parameter name (for error messages, unused)
        value: String value to parse
        default: Default value if parsing fails

    Returns:
        Parsed integer or default
    """
    if value is None:
        return default
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return default


def resolve_wakeword_keyword(
    keyword: Optional[str],
    wakeword: Optional[str],
    *,
    default: Optional[str] = None,
) -> Optional[str]:
    for candidate in (keyword, wakeword, default):
        if candidate is None:
            continue
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


@dataclass
class ModelRoutingConfig:
    """Maps task categories to LLM model identifiers.

    Each field accepts a model identifier string (e.g. ``"gpt-4o"``,
    ``"llama3"``) or an empty string to fall back to the global
    ``AppConfig.llm_model`` setting.  All fields are optional.
    """

    default: str = ""
    coding: str = ""
    reasoning: str = ""
    search: str = ""
    vision: str = ""
    fast: str = ""


@dataclass
class EmailAccountConfig:
    """Configuration for a single email account (IMAP read + SMTP send)."""

    id: str
    address: str
    imap_host: str
    imap_port: int = 993
    smtp_host: str = ""
    smtp_port: int = 587
    credential_ref: str = ""
    use_starttls: bool = True


@dataclass
class UserEmailAccount:
    """Per-user email account entry (US-ME-001).

    Lightweight descriptor that names an email account, its backend type, and
    the ``.env`` key where credentials are stored.  Full connection details
    (host, port, etc.) are resolved at runtime from the credentials key.
    """

    account_id: str
    display_name: str = ""
    backend: str = "imap"  # "imap" | "gmail" | "outlook"
    credentials_key: str = ""  # e.g. "EMAIL_ALICE_WORK" in .env


# ---------------------------------------------------------------------------
# Sub-config Pydantic v2 models (US-001)
# These are additive — AppConfig is unchanged.  Nested fields will be wired
# in US-002.  `extra="ignore"` lets unknown JSON keys pass through silently.
# ---------------------------------------------------------------------------


class AudioConfig(BaseModel):
    """Audio hardware settings."""

    model_config = ConfigDict(extra="ignore")

    sample_rate: int = 16000
    channels: int = 1
    chunk_size: int = 1024
    input_device: Optional[int] = None
    output_device: Optional[int] = None
    vad_sensitivity: float = 0.003  # matches command_vad_rms_threshold default


class VoiceConfig(BaseModel):
    """Voice-pipeline settings (TTS, STT, wake word)."""

    model_config = ConfigDict(extra="ignore")

    tts_engine: str = "xtts"  # maps to tts_provider
    tts_voice: Optional[str] = None
    tts_speed: float = 1.08
    stt_model: str = "base"  # maps to whisper_model
    whisper_device: str = "auto"
    wakeword_model: str = "hey_rex"  # maps to wakeword
    wakeword_sensitivity: float = 0.5  # maps to wakeword_threshold
    wakeword_fallback_keyword: str = "hey jarvis"
    wakeword_backend: str = "openwakeword"


class LLMConfig(BaseModel):
    """Language model settings."""

    model_config = ConfigDict(extra="ignore")

    llm_provider: str = "transformers"
    model_name: Optional[str] = (
        "sshleifer/tiny-gpt2"  # maps to llm_model; None when openai_model is used
    )
    openai_api_key_env: str = "OPENAI_API_KEY"  # env var name (not the value)
    openrouter_api_key_env: str = "OPENROUTER_API_KEY"
    openrouter_model: Optional[str] = None
    openrouter_base_url: str = OPENROUTER_DEFAULT_BASE_URL
    ollama_url: str = "http://localhost:11434"  # maps to ollama_base_url
    context_length: int = 120  # maps to llm_max_tokens
    temperature: float = 0.7  # maps to llm_temperature
    llm_routing_mode: str = "local_preferred"


class ToolsConfig(BaseModel):
    """Tool dispatch settings."""

    model_config = ConfigDict(extra="ignore")

    tool_timeout: float = 10.0  # maps to tool_timeout_seconds
    tool_max_retries: int = 3
    enabled_tools: List[str] = []
    tool_permissions: Dict[str, List[str]] = {}


class IntegrationsConfig(BaseModel):
    """External integration settings."""

    model_config = ConfigDict(extra="ignore")

    home_assistant_base_url: Optional[str] = None  # maps to ha_base_url
    ha_token_env: str = "HA_TOKEN"  # env var name holding the HA token
    email_provider: str = "none"
    calendar_provider: str = "none"
    music_assistant_url: Optional[str] = None
    music_assistant_token_env: str = "MUSIC_ASSISTANT_TOKEN"
    shopping_pwa_pin: Optional[str] = None
    openclaw_gateway_url: str = ""
    openclaw_gateway_timeout: int = 30
    openclaw_gateway_max_retries: int = 3


class UIConfig(BaseModel):
    """GUI and dashboard settings."""

    model_config = ConfigDict(extra="ignore")

    gui_port: int = 5000
    gui_host: str = "127.0.0.1"
    ui_enabled: bool = True
    theme: str = "system"


class SecurityConfig(BaseModel):
    """API security settings."""

    model_config = ConfigDict(extra="ignore")

    api_key_env: str = "REX_SPEAK_API_KEY"  # env var name for the speak API key
    rate_limit_per_minute: int = 30  # maps to rate_limit
    allowed_origins: List[str] = ["*"]


_RATE_LIMIT_PATTERN = r"^\d+\s*(?:per\s+|/)\s*\d*\s*(?:second|minute|hour|day|month|year)s?$"


class MobileApiConfig(BaseModel):
    """Typed configuration for the authenticated mobile API gateway (issue #323).

    Canonical JSON group: ``mobile_api`` in ``config/rex_config.json``.
    The JWT signing secret is not part of this model. It is stored in the
    credential vault as ``REX_JWT_SECRET``.
    """

    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 8765
    advertised_host: str | None = None
    advertised_port: int | None = None
    allowed_origins: List[str] = []
    # S7: any non-loopback host always requires usable TLS regardless of this
    # flag — rex.mobile_api.tls.resolve_mobile_tls() enforces that and fails
    # closed when TLS material cannot be provisioned. This flag only opts a
    # loopback (127.0.0.1/localhost) bind into TLS for local testing; it
    # cannot disable TLS for a non-loopback bind.
    require_tls: bool = False
    api_version: str = "1.0"
    access_token_ttl_seconds: int = 900
    refresh_token_ttl_days: int = 30
    max_json_bytes: int = 1_048_576
    max_audio_bytes: int = 15_728_640
    max_audio_seconds: int = 60
    rate_limit_default: str = "60 per minute"
    rate_limit_login: str = "10 per minute"
    rate_limit_refresh: str = "30 per minute"
    rate_limit_chat: str = "30 per minute"
    rate_limit_voice: str = "10 per minute"
    # Retention window for the cross-transport (user_id, message_id)
    # idempotency records; must cover realistic reconnect/retry behaviour.
    idempotency_retention_hours: int = 48

    @field_validator("host", "api_version")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        return value

    @field_validator("advertised_host")
    @classmethod
    def _valid_advertised_host(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().strip("[]").lower()
        if not value or value in {"0.0.0.0", "::", "*"}:
            raise ValueError("advertised_host must be a concrete host")
        return value

    @field_validator("port", "advertised_port")
    @classmethod
    def _valid_port(cls, value: int | None) -> int | None:
        if value is not None and not 1 <= value <= 65535:
            raise ValueError("port must be between 1 and 65535")
        return value

    @field_validator(
        "access_token_ttl_seconds",
        "refresh_token_ttl_days",
        "max_json_bytes",
        "max_audio_bytes",
        "max_audio_seconds",
        "idempotency_retention_hours",
    )
    @classmethod
    def _positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("must be a positive integer")
        return value

    @field_validator(
        "rate_limit_default",
        "rate_limit_login",
        "rate_limit_refresh",
        "rate_limit_chat",
        "rate_limit_voice",
    )
    @classmethod
    def _valid_rate_limit(cls, value: str) -> str:
        import re as _re  # noqa: PLC0415

        value = value.strip()
        if not _re.fullmatch(_RATE_LIMIT_PATTERN, value):
            raise ValueError(f"invalid rate limit string {value!r}; expected e.g. '60 per minute'")
        return value

    @field_validator("allowed_origins")
    @classmethod
    def _valid_origins(cls, value: List[str]) -> List[str]:
        cleaned: List[str] = []
        for origin in value:
            origin = str(origin).strip().rstrip("/")
            if not origin:
                continue
            if origin == "*":
                raise ValueError(
                    "wildcard '*' is not allowed for mobile_api.allowed_origins; "
                    "CORS is deny-by-default"
                )
            cleaned.append(origin)
        return cleaned


class SpeechConfig(BaseModel):
    """Provider-neutral SpeechRouter policy (S35).

    Canonical JSON group: ``speech`` in ``config/rex_config.json``. Disabled
    by default: when ``enabled`` is false, both desktop and the mobile
    gateway use the pre-existing native STT/TTS adapters unchanged, which is
    the explicit rollback path. STT and TTS provider selection are
    independent of each other and of ``models.llm_provider``.
    """

    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    policy_mode: str = "prefer_local"  # automatic|prefer_local|local_only|prefer_cloud|custom
    allow_cloud: bool = False
    stt_provider: str = "native"
    tts_provider: str = "native"
    stt_fallback_order: List[str] = []
    tts_fallback_order: List[str] = []

    voicestudio_enabled: bool = False
    # Matches upstream VoiceStudio's documented loopback API port (Docker
    # quick start publishes 127.0.0.1:3900); see debpalash/VoiceStudio.
    voicestudio_base_url: str = "http://127.0.0.1:3900"
    voicestudio_timeout_seconds: float = 30.0
    voicestudio_stt_model: Optional[str] = None
    voicestudio_tts_model: Optional[str] = None
    voicestudio_default_voice: Optional[str] = None
    voicestudio_api_key_env: Optional[str] = None
    voicestudio_voice_aliases: Dict[str, str] = {}

    @field_validator("policy_mode")
    @classmethod
    def _valid_policy_mode(cls, value: str) -> str:
        value = value.strip().lower()
        allowed = {"automatic", "prefer_local", "local_only", "prefer_cloud", "custom"}
        if value not in allowed:
            raise ValueError(f"speech.policy_mode must be one of {sorted(allowed)}")
        return value

    @field_validator("stt_provider", "tts_provider")
    @classmethod
    def _non_empty_provider(cls, value: str) -> str:
        value = value.strip().lower()
        if not value:
            raise ValueError("must not be empty")
        return value

    @field_validator("voicestudio_timeout_seconds")
    @classmethod
    def _positive_timeout(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("speech.voicestudio_timeout_seconds must be positive")
        return value


@dataclass
class AppConfig:
    """Application configuration combining JSON config and environment secrets."""

    wakeword: str = "hey_rex"
    wakeword_backend: str = "openwakeword"
    wakeword_threshold: float = 0.5
    wakeword_window: float = 1.0
    wakeword_poll_interval: float = 0.01
    wakeword_model_path: Optional[str] = None
    wakeword_embedding_path: Optional[str] = None
    wakeword_fallback_to_builtin: bool = True
    wakeword_fallback_keyword: str = "hey jarvis"
    wakeword_auto_gain: bool = True
    wakeword_target_peak: float = 0.35
    wakeword_max_gain: float = 30.0
    wakeword_min_rms_for_gain: float = 0.0005
    command_duration: float = 5.0
    command_vad_rms_threshold: float = 0.003

    sample_rate: int = 16000
    detection_frame_seconds: float = 1.0
    capture_seconds: float = 5.0

    whisper_model: str = "base"
    whisper_device: str = "auto"
    whisper_language: Optional[str] = "en"
    stt_auto_gain: bool = True
    stt_target_peak: float = 0.45
    stt_max_gain: float = 12.0
    stt_min_rms_for_gain: float = 0.0005
    llm_provider: str = "transformers"
    llm_model: str = "sshleifer/tiny-gpt2"
    llm_max_tokens: int = 120
    voice_max_tokens: int = 150
    llm_temperature: float = 0.7
    autonomy_mode: str = "manual"
    llm_top_p: float = 0.9
    llm_top_k: int = 50
    llm_seed: int = 42
    tts_provider: str = "xtts"
    tts_voice: Optional[str] = None
    tts_speed: float = 1.08
    tts_fast_short_reply_enabled: bool = True
    tts_fast_short_reply_max_chars: int = 140

    speak_api_key: Optional[str] = None
    rate_limit: str = "30/minute"
    allowed_origins: List[str] = field(default_factory=lambda: ["*"])

    memory_max_turns: int = 50
    transcripts_enabled: bool = True
    transcripts_dir: Path = Path("transcripts")
    session_ttl_hours: int = 8
    default_user: Optional[str] = None
    wake_sound_path: Optional[str] = None
    acknowledgment_sound: str = "chime"  # "chime", a .wav path, or a spoken filler phrase
    acknowledgment_mode: str = "sound"  # "sound", "phrase", or "none"
    response_cache_ttl: float = 300.0  # seconds; 0 disables response caching

    active_profile: str = "default"
    capabilities: List[str] = field(default_factory=list)
    personality: str = "Friendly"  # default personality (US-050)

    audio_input_device: Optional[int] = None
    audio_output_device: Optional[int | str] = None

    debug_logging: bool = False
    debug_mode: bool = False  # set via REX_DEBUG=1 or rex --debug
    file_logging_enabled: bool = False
    log_path: Path = DEFAULT_RUNTIME_LOG_FILE
    error_log_path: Path = DEFAULT_ERROR_LOG_FILE
    memory_max_bytes: int = 131072
    warm_runtime_max_cost_mb: float = 6144.0
    warm_runtime_idle_timeout_s: float = 900.0
    conversation_export: bool = True

    brave_api_key: Optional[str] = None
    voicestudio_api_key: Optional[str] = None
    openai_api_key: Optional[str] = None
    openai_model: Optional[str] = None
    openai_base_url: Optional[str] = None

    openrouter_api_key: Optional[str] = None
    openrouter_model: Optional[str] = None
    openrouter_base_url: str = OPENROUTER_DEFAULT_BASE_URL

    anthropic_api_key: Optional[str] = None
    anthropic_model: Optional[str] = None

    ollama_api_key: Optional[str] = None
    ollama_base_url: str = "http://localhost:11434"
    ollama_use_cloud: bool = False

    search_providers: str = "serpapi,brave,duckduckgo,google"
    speak_language: str = "en"

    followups_enabled: bool = False
    followups_max_per_session: int = 2
    followups_lookback_hours: int = 72
    followups_expire_hours: int = 168

    # Home Assistant integration
    ha_base_url: Optional[str] = None
    ha_token: Optional[str] = None
    ha_secret: Optional[str] = None
    ha_verify_ssl: bool = True
    ha_timeout: float = 10.0
    ha_entity_map: Optional[Dict[str, str]] = None

    # Integration credential detection
    email_provider: str = "none"  # none | gmail | outlook
    calendar_provider: str = "none"  # none | google | outlook

    # Multi-account email config (US-208)
    email_accounts: List[EmailAccountConfig] = field(default_factory=list)
    email_default_account_id: str = ""

    # Per-user multi-email accounts (US-ME-001)
    # Keyed by user_id; each value is the list of email accounts for that user.
    user_email_accounts: Dict[str, List[UserEmailAccount]] = field(default_factory=dict)

    # Per-user default email account selection (issue #303).
    # Keyed by user_id; value is the account_id of that user's default account.
    user_default_email_accounts: Dict[str, str] = field(default_factory=dict)

    # Location and weather
    default_location: Optional[str] = None
    default_timezone: Optional[str] = None
    openweathermap_api_key: Optional[str] = None

    # Conversation history persistence
    persist_history: bool = True
    history_db_path: Path = field(default_factory=lambda: household_data_path("history.db"))
    history_retention_days: int = 30

    # Autonomy budget limits (0 = unlimited)
    autonomy_budget_per_plan_usd: float = 0.0
    autonomy_budget_per_step_usd: float = 0.0

    # Music Assistant integration
    music_assistant_url: Optional[str] = None
    music_assistant_token: Optional[str] = None

    # Telegram bot integration (US-039)
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None

    # Push notifications (US-042)
    push_provider: Optional[str] = None  # "ntfy" or "pushover"
    push_token: Optional[str] = None  # bearer token (ntfy) or app token (pushover)
    push_topic: Optional[str] = None  # ntfy topic or pushover user key

    # Room context: maps device IDs to room names (e.g. {"mic_kitchen": "kitchen"})
    device_room_map: Dict[str, str] = field(default_factory=dict)

    # OpenClaw integration
    use_openclaw_tools: bool = False
    use_openclaw_voice_backend: bool = False
    openclaw_gateway_url: str = ""
    openclaw_gateway_timeout: int = 30
    openclaw_gateway_max_retries: int = 3
    openclaw_gateway_token: Optional[str] = None

    # Model routing
    model_routing: ModelRoutingConfig = field(default_factory=ModelRoutingConfig)
    llm_routing_mode: str = "local_preferred"  # "local_preferred", "cloud_only", "local_only"
    cloud_fallback_cooldown_seconds: int = 3600  # cooldown after 429/402 before retrying cloud

    # Voice identity
    speaker_id_threshold: float = 0.75

    # Tool dispatch
    tool_timeout_seconds: float = 10.0

    # Local file access allowlist (US-WIN-001)
    allowed_file_roots: List[str] = field(default_factory=lambda: [str(Path.home())])

    # Computer control confirmation mode (US-055)
    # "always" = confirm all actions, "dangerous_only" = confirm dangerous only, "never" = no confirm
    computer_control_confirmation: str = "dangerous_only"

    # Windows settings — require user confirmation before applying system changes (US-WIN-003)
    require_confirm_system_changes: bool = True

    # Web UI (US-UI-001)
    ui_enabled: bool = True

    # Shopping list PWA (US-SL-004) — optional PIN; empty/None means no auth
    shopping_pwa_pin: Optional[str] = None

    # Smart speaker TTS output (US-SP-002) — name of discovered speaker, or None for local audio
    tts_output_device: Optional[str] = None

    # Smart speaker microphone input (US-SP-003) — name of discovered speaker, or None/auto for local mic
    wake_word_input_device: Optional[str] = None

    # Outbound calling — path to contacts JSON or vCard file (US-PH-003)
    contacts_file: Optional[str] = None

    # Aliases
    llm_backend: Optional[str] = None
    temperature: Optional[float] = None
    max_memory_items: Optional[int] = None
    user_id: str = "default"
    wakeword_keyword: Optional[str] = None

    # Nested sub-configs (US-002) — derived views over flat fields; populated in __post_init__
    audio: Optional[AudioConfig] = field(default=None, repr=False, compare=False)
    voice: Optional[VoiceConfig] = field(default=None, repr=False, compare=False)
    llm: Optional[LLMConfig] = field(default=None, repr=False, compare=False)
    tools: Optional[ToolsConfig] = field(default=None, repr=False, compare=False)
    integrations: Optional[IntegrationsConfig] = field(default=None, repr=False, compare=False)
    ui: Optional[UIConfig] = field(default=None, repr=False, compare=False)
    security: Optional[SecurityConfig] = field(default=None, repr=False, compare=False)

    # Mobile API gateway (issue #323) — canonical nested group, parsed from the
    # ``mobile_api`` JSON section (no flat-field equivalents).
    mobile_api: MobileApiConfig = field(default_factory=MobileApiConfig, repr=False, compare=False)

    # Provider-neutral SpeechRouter policy (S35) — canonical nested group,
    # parsed from the ``speech`` JSON section (no flat-field equivalents).
    speech: SpeechConfig = field(default_factory=SpeechConfig, repr=False, compare=False)

    # ---------------------------------------------------------------------------
    # US-003 — Deprecated flat-field access map (ClassVar, not a dataclass field)
    # Maps flat field name → sub-config group name; used by __getattribute__
    # ---------------------------------------------------------------------------
    _DEPRECATED_FIELDS: ClassVar[Dict[str, str]] = {
        "llm_provider": "llm",
        "tts_voice": "voice",
        "whisper_device": "voice",
        "openclaw_gateway_url": "integrations",
    }

    def __getattribute__(self, name: str):
        """Emit DeprecationWarning for high-traffic flat fields that now have nested equivalents."""
        _deprecated = type(self).__dict__.get("_DEPRECATED_FIELDS", {})
        if name in _deprecated:
            d = object.__getattribute__(self, "__dict__")
            # Only warn after sub-configs are built; guards against noise during __post_init__
            if d.get("_deprecated_warnings_active"):
                group = _deprecated[name]
                warnings.warn(
                    f"AppConfig.{name} is deprecated. Use config.{group}.{name} instead.",
                    DeprecationWarning,
                    stacklevel=2,
                )
        return object.__getattribute__(self, name)

    # -- Deprecated property aliases (US-003) — sub-config field names as deprecated AppConfig attrs

    @property
    def model_name(self) -> Optional[str]:
        """Deprecated. Use config.llm.model_name instead."""
        warnings.warn(
            "AppConfig.model_name is deprecated. Use config.llm.model_name instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        _llm = object.__getattribute__(self, "llm")
        return _llm.model_name if _llm is not None else object.__getattribute__(self, "llm_model")  # type: ignore[no-any-return]

    @property
    def tts_engine(self) -> str:
        """Deprecated. Use config.voice.tts_engine instead."""
        warnings.warn(
            "AppConfig.tts_engine is deprecated. Use config.voice.tts_engine instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        _voice = object.__getattribute__(self, "voice")
        return (  # type: ignore[no-any-return]
            _voice.tts_engine
            if _voice is not None
            else object.__getattribute__(self, "tts_provider")
        )

    @property
    def wakeword_model(self) -> str:
        """Deprecated. Use config.voice.wakeword_model instead."""
        warnings.warn(
            "AppConfig.wakeword_model is deprecated. Use config.voice.wakeword_model instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        _voice = object.__getattribute__(self, "voice")
        return (  # type: ignore[no-any-return]
            _voice.wakeword_model
            if _voice is not None
            else object.__getattribute__(self, "wakeword")
        )

    @property
    def home_assistant_base_url(self) -> Optional[str]:
        """Deprecated. Use config.integrations.home_assistant_base_url instead."""
        warnings.warn(
            "AppConfig.home_assistant_base_url is deprecated. "
            "Use config.integrations.home_assistant_base_url instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        _intg = object.__getattribute__(self, "integrations")
        return (  # type: ignore[no-any-return]
            _intg.home_assistant_base_url
            if _intg is not None
            else object.__getattribute__(self, "ha_base_url")
        )

    @property
    def tool_timeout(self) -> float:
        """Deprecated. Use config.tools.tool_timeout instead."""
        warnings.warn(
            "AppConfig.tool_timeout is deprecated. Use config.tools.tool_timeout instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        _tools = object.__getattribute__(self, "tools")
        return (  # type: ignore[no-any-return]
            _tools.tool_timeout
            if _tools is not None
            else object.__getattribute__(self, "tool_timeout_seconds")
        )

    @property
    def gui_port(self) -> int:
        """Deprecated. Use config.ui.gui_port instead."""
        warnings.warn(
            "AppConfig.gui_port is deprecated. Use config.ui.gui_port instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        _ui = object.__getattribute__(self, "ui")
        return _ui.gui_port if _ui is not None else 5000

    @property
    def api_key_env(self) -> str:
        """Deprecated. Use config.security.api_key_env instead."""
        warnings.warn(
            "AppConfig.api_key_env is deprecated. Use config.security.api_key_env instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        _sec = object.__getattribute__(self, "security")
        return _sec.api_key_env if _sec is not None else "REX_SPEAK_API_KEY"

    @property
    def rate_limit_per_minute(self) -> int:
        """Deprecated. Use config.security.rate_limit_per_minute instead."""
        warnings.warn(
            "AppConfig.rate_limit_per_minute is deprecated. "
            "Use config.security.rate_limit_per_minute instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        _sec = object.__getattribute__(self, "security")
        return _sec.rate_limit_per_minute if _sec is not None else 30

    def to_dict(self) -> dict:
        # Suppress deprecation warnings from flat-field access during asdict() iteration
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            raw = asdict(self)
        # Remove nested sub-config objects (US-002) — derived views; not part of serialised output
        for _key in ("audio", "voice", "llm", "tools", "integrations", "ui", "security"):
            raw.pop(_key, None)
        # mobile_api is canonical nested config with no flat equivalents, so it
        # is serialised as its validated dictionary (it contains no secrets —
        # the JWT secret lives in the credential vault).
        raw["mobile_api"] = self.mobile_api.model_dump()
        raw["speech"] = self.speech.model_dump()
        raw["transcripts_dir"] = str(self.transcripts_dir)
        raw["log_path"] = str(self.log_path)
        raw["error_log_path"] = str(self.error_log_path)
        return raw

    def __post_init__(self) -> None:
        if self.warm_runtime_max_cost_mb < 0 or self.warm_runtime_idle_timeout_s < 0:
            raise ValueError("warm runtime limits must be non-negative")
        provider = self.llm_provider.lower()
        self.openrouter_base_url = (self.openrouter_base_url or OPENROUTER_DEFAULT_BASE_URL).rstrip(
            "/"
        )
        local_path_providers = {"transformers"}
        if provider in local_path_providers and isinstance(self.llm_model, str):
            model_path = Path(self.llm_model)
            if model_path.is_absolute() or ".." in model_path.parts:
                raise ValueError("llm_model must not contain path traversal components.")
        if provider == "openai" and not self.openai_model:
            raise ValueError("openai.model must be set when llm_provider is 'openai'.")
        if provider == "openrouter":
            if not self.openrouter_model:
                raise ValueError("openrouter.model must be set when llm_provider is 'openrouter'.")
            if self.openrouter_base_url != OPENROUTER_DEFAULT_BASE_URL:
                raise ValueError(
                    "openrouter.base_url must use the official https://openrouter.ai/api/v1 endpoint."
                )
        if provider == "anthropic" and not self.anthropic_model:
            raise ValueError("anthropic.model must be set when llm_provider is 'anthropic'.")
        if self.llm_backend is None:
            self.llm_backend = self.llm_provider
        if self.temperature is None:
            self.temperature = self.llm_temperature
        if self.max_memory_items is None:
            self.max_memory_items = self.memory_max_turns
        self.wakeword_keyword = resolve_wakeword_keyword(self.wakeword_keyword, self.wakeword)

        # Build nested sub-configs from flat fields (US-002)
        self.audio = AudioConfig(
            sample_rate=self.sample_rate,
            input_device=self.audio_input_device,
            output_device=(
                self.audio_output_device if isinstance(self.audio_output_device, int) else None
            ),
            vad_sensitivity=self.command_vad_rms_threshold,
        )
        self.voice = VoiceConfig(
            tts_engine=self.tts_provider,
            tts_voice=self.tts_voice,
            tts_speed=self.tts_speed,
            stt_model=self.whisper_model,
            whisper_device=self.whisper_device,
            wakeword_model=self.wakeword,
            wakeword_sensitivity=self.wakeword_threshold,
            wakeword_fallback_keyword=self.wakeword_fallback_keyword,
            wakeword_backend=self.wakeword_backend,
        )
        self.llm = LLMConfig(
            llm_provider=self.llm_provider,
            model_name=self.llm_model,
            openrouter_model=self.openrouter_model,
            openrouter_base_url=self.openrouter_base_url,
            ollama_url=self.ollama_base_url,
            context_length=self.llm_max_tokens,
            temperature=self.llm_temperature,
            llm_routing_mode=self.llm_routing_mode,
        )
        self.tools = ToolsConfig(
            tool_timeout=self.tool_timeout_seconds,
        )
        _rate_limit_per_min = 30
        if isinstance(self.rate_limit, str) and "/" in self.rate_limit:
            try:
                _rate_limit_per_min = int(self.rate_limit.split("/")[0])
            except (ValueError, IndexError):
                pass
        self.integrations = IntegrationsConfig(
            home_assistant_base_url=self.ha_base_url,
            email_provider=self.email_provider,
            calendar_provider=self.calendar_provider,
            music_assistant_url=self.music_assistant_url,
            shopping_pwa_pin=self.shopping_pwa_pin,
            openclaw_gateway_url=self.openclaw_gateway_url,
            openclaw_gateway_timeout=self.openclaw_gateway_timeout,
            openclaw_gateway_max_retries=self.openclaw_gateway_max_retries,
        )
        self.ui = UIConfig(
            ui_enabled=self.ui_enabled,
        )
        self.security = SecurityConfig(
            rate_limit_per_minute=_rate_limit_per_min,
            allowed_origins=list(self.allowed_origins),
        )
        # Enable deprecation warnings for flat field access (US-003)
        # Must be set LAST so flat field reads during sub-config construction are silent
        self._deprecated_warnings_active = True


_cached_config: Optional[AppConfig] = None

# Required environment variables (secrets only)
REQUIRED_ENV_KEYS: set = set()  # No required env vars - secrets are optional

# Backward compatibility: ENV_MAPPING removed - use rex_config.json for runtime settings
# For migration, see rex.config_manager.ENV_TO_CONFIG_MAPPING
ENV_MAPPING: Dict[str, str] = {}


def _get_nested(data: dict, path: str, default=None):
    """Get value from nested dict using dot notation."""
    keys = path.split(".")
    value = data
    for key in keys:
        if isinstance(value, dict):
            value = value.get(key, default)
        else:
            return default
    return value


def _coerce_float(json_config: dict, path: str, default: float) -> float:
    """Get a float config value, warning if the raw value is a string.

    Pydantic/dataclass coercion silently accepts string-typed floats from
    JSON, which can hide misconfigured ``rex_config.json`` files.  This
    helper logs a WARNING so operators know to fix the source file.

    Raises:
        ConfigurationError: If the value cannot be converted to float.
    """
    raw = _get_nested(json_config, path, default)
    if isinstance(raw, str):
        LOGGER.warning(
            "Config field %r has string value %r — expected float; "
            "coercing automatically.  Fix the value in rex_config.json.",
            path,
            raw,
        )
    try:
        return float(raw)
    except (ValueError, TypeError) as exc:
        raise ConfigurationError(
            f"Config field {path!r} has invalid value {raw!r}: cannot convert to float."
        ) from exc


def _coerce_int(json_config: dict, path: str, default: int) -> int:
    """Get an int config value, warning if the raw value is a string.

    See :func:`_coerce_float` for rationale.

    Raises:
        ConfigurationError: If the value cannot be converted to int.
    """
    raw = _get_nested(json_config, path, default)
    if isinstance(raw, str):
        LOGGER.warning(
            "Config field %r has string value %r — expected int; "
            "coercing automatically.  Fix the value in rex_config.json.",
            path,
            raw,
        )
    try:
        return int(float(raw))
    except (ValueError, TypeError) as exc:
        raise ConfigurationError(
            f"Config field {path!r} has invalid value {raw!r}: cannot convert to int."
        ) from exc


def _normalize_calendar_provider(value: object) -> str:
    """Normalize GUI/provider aliases for the calendar service."""
    provider = str(value or "none").strip().lower()
    if provider == "gmail":
        return "google"
    return provider or "none"


def _parse_email_accounts(raw: object) -> List[EmailAccountConfig]:
    """Parse ``email.accounts`` from JSON config into a list of EmailAccountConfig."""
    if not isinstance(raw, list):
        return []
    accounts: List[EmailAccountConfig] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            accounts.append(
                EmailAccountConfig(
                    id=str(item["id"]),
                    address=str(item["address"]),
                    imap_host=str(item.get("imap_host", item.get("imap", {}).get("host", ""))),
                    imap_port=int(item.get("imap_port", item.get("imap", {}).get("port", 993))),
                    smtp_host=str(item.get("smtp_host", item.get("smtp", {}).get("host", ""))),
                    smtp_port=int(item.get("smtp_port", item.get("smtp", {}).get("port", 587))),
                    credential_ref=str(item.get("credential_ref", "")),
                    use_starttls=bool(item.get("use_starttls", True)),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            LOGGER.warning("Skipping malformed email account entry: %s", exc)
    return accounts


def _parse_user_email_account(raw: dict) -> UserEmailAccount:
    """Parse a single user email account dict."""
    return UserEmailAccount(
        account_id=str(raw["account_id"]),
        display_name=str(raw.get("display_name", "")),
        backend=str(raw.get("backend", "imap")).lower(),
        credentials_key=str(raw.get("credentials_key", "")),
    )


def _parse_user_email_accounts(
    users_block: object,
    legacy_email_accounts: object,
) -> Dict[str, List[UserEmailAccount]]:
    """Parse ``users.{user_id}.email_accounts`` into a per-user dict.

    Migration shim: if the new ``users`` block is absent or empty but the
    legacy ``email.accounts`` list is present, its entries are migrated to the
    ``"default"`` user using their ``id`` as ``account_id`` and ``credential_ref``
    as ``credentials_key``.
    """
    result: Dict[str, List[UserEmailAccount]] = {}

    # Parse new format: users.{user_id}.email_accounts
    if isinstance(users_block, dict):
        for user_id, user_data in users_block.items():
            if not isinstance(user_data, dict):
                continue
            accounts_raw = user_data.get("email_accounts", [])
            if not isinstance(accounts_raw, list):
                continue
            parsed: List[UserEmailAccount] = []
            for entry in accounts_raw:
                if not isinstance(entry, dict) or "account_id" not in entry:
                    continue
                try:
                    parsed.append(_parse_user_email_account(entry))
                except (KeyError, TypeError, ValueError) as exc:
                    LOGGER.warning("Skipping malformed user email account: %s", exc)
            if parsed:
                result[str(user_id)] = parsed

    # Migration shim: promote legacy email.accounts to user "default" if no new entries
    if not result and isinstance(legacy_email_accounts, list):
        migrated: List[UserEmailAccount] = []
        for item in legacy_email_accounts:
            if not isinstance(item, dict):
                continue
            account_id = str(item.get("id", ""))
            if not account_id:
                continue
            migrated.append(
                UserEmailAccount(
                    account_id=account_id,
                    display_name=str(item.get("address", "")),
                    backend="imap",
                    credentials_key=str(item.get("credential_ref", "")),
                )
            )
        if migrated:
            result["default"] = migrated

    return result


def _parse_user_default_email_accounts(users_block: object) -> Dict[str, str]:
    """Parse ``users.{user_id}.default_email_account_id`` into a per-user map.

    Each user's default may only reference an account assigned to that user;
    ownership is enforced at resolution time by ``rex.email_accounts``.
    """
    result: Dict[str, str] = {}
    if not isinstance(users_block, dict):
        return result
    for user_id, user_data in users_block.items():
        if not isinstance(user_data, dict):
            continue
        default_id = user_data.get("default_email_account_id")
        if isinstance(default_id, str) and default_id.strip():
            result[str(user_id)] = default_id.strip()
    return result


def _parse_model_routing(raw: object) -> ModelRoutingConfig:
    """Parse ``model_routing`` block from JSON config."""
    if not isinstance(raw, dict):
        return ModelRoutingConfig()
    return ModelRoutingConfig(
        default=str(raw.get("default", "")),
        coding=str(raw.get("coding", "")),
        reasoning=str(raw.get("reasoning", "")),
        search=str(raw.get("search", "")),
        vision=str(raw.get("vision", "")),
        fast=str(raw.get("fast", "")),
    )


def _parse_allowed_file_roots(raw: object) -> list[str]:
    """Parse ``file_ops.allowed_roots`` list from JSON config.

    Returns a list of root path strings.  Falls back to the user home directory
    when *raw* is not a non-empty list.
    """
    if isinstance(raw, list) and raw:
        return [str(item) for item in raw if item]
    return [str(Path.home())]


def _merge_profile_config(base_config: dict) -> dict:
    profile_name = get_active_profile_name(base_config)
    profiles_dir = base_config.get("profiles_dir", DEFAULT_PROFILES_DIR)
    ensure_default_profile(profiles_dir)
    try:
        profile = load_profile(profile_name, profiles_dir=profiles_dir)
    except FileNotFoundError:
        if profile_name != "default":
            LOGGER.warning("Profile '%s' not found; falling back to 'default'.", profile_name)
            profile = load_profile("default", profiles_dir=profiles_dir)
            profile_name = "default"
        else:
            raise
    merged_config = apply_profile(base_config, profile)
    merged_config["active_profile"] = profile_name
    merged_config.setdefault("profiles_dir", profiles_dir)
    merged_config["capabilities"] = profile.get("capabilities", [])
    return merged_config


def _migrate_wake_word_section(json_config: dict) -> dict:
    """Migrate the legacy ``wake_word`` key to the canonical ``wakeword`` key.

    If ``wake_word`` is present its values are merged into ``wakeword`` (without
    overwriting keys already present in ``wakeword``), and ``wake_word`` is
    removed.  A deprecation notice is logged so operators know to update their
    config files.
    """
    legacy = json_config.pop("wake_word", None)
    if legacy is None:
        return json_config

    LOGGER.warning(
        "Config key 'wake_word' is deprecated — rename it to 'wakeword' in "
        "rex_config.json.  Values have been migrated automatically for this run."
    )
    if isinstance(legacy, dict):
        canonical = json_config.get("wakeword")
        if not isinstance(canonical, dict):
            canonical = {}
            json_config["wakeword"] = canonical
        for k, v in legacy.items():
            canonical.setdefault(k, v)
    return json_config


def _parse_mobile_api_config(raw: object) -> MobileApiConfig:
    """Parse and validate the ``mobile_api`` JSON group.

    Raises:
        ConfigurationError: If any mobile_api value fails validation, so that
            startup fails before serving rather than running misconfigured.
    """
    if not isinstance(raw, dict):
        if raw is not None:
            raise ConfigurationError("Config group 'mobile_api' must be a JSON object.")
        raw = {}
    try:
        return MobileApiConfig(**raw)
    except Exception as exc:
        raise ConfigurationError(f"Invalid 'mobile_api' configuration: {exc}") from exc


def _parse_speech_config(raw: object) -> SpeechConfig:
    """Parse and validate the ``speech`` JSON group (S35).

    Raises:
        ConfigurationError: If any speech value fails validation, so that
            startup fails before serving rather than running misconfigured.
    """
    if not isinstance(raw, dict):
        if raw is not None:
            raise ConfigurationError("Config group 'speech' must be a JSON object.")
        raw = {}
    try:
        return SpeechConfig(**raw)
    except Exception as exc:
        raise ConfigurationError(f"Invalid 'speech' configuration: {exc}") from exc


def _secret_env_or_vault(env_var: str, json_config: dict) -> str | None:
    """Resolve a secret from the household credential vault (S4).

    A configured vault reference is the real production authority: when one
    exists for ``env_var`` and resolves to a value, that value wins
    unconditionally - it is never overridden by the plaintext environment.
    Reading the plaintext ``os.getenv(env_var)`` value is only considered as
    a *fallback*, and only when ``rex.credentials.legacy_plaintext_fallback_enabled()``
    is true (an explicit, non-production development/CI/operator opt-in),
    and only when no vault reference is configured or the configured
    reference does not resolve to a value. In normal (packaged/production)
    operation this flag is unset, so a secret not present in the vault is
    simply absent - never silently read from plaintext ``.env``/process
    environment. Invalid registry or vault data always raises
    ``ConfigurationError`` so tampering is never reclassified as an
    unconfigured credential, regardless of the legacy flag.
    """
    from rex.credentials import (
        credential_context_for_name,
        legacy_plaintext_fallback_enabled,
    )

    def _legacy_fallback() -> str | None:
        if legacy_plaintext_fallback_enabled():
            value = os.getenv(env_var)
            if value:
                return value
        return None

    refs_root = json_config.get("credential_refs")
    if refs_root is None:
        return _legacy_fallback()
    if not isinstance(refs_root, dict):
        raise ConfigurationError("Credential reference registry is invalid")
    household_refs = refs_root.get("household")
    if household_refs is None:
        return _legacy_fallback()
    if not isinstance(household_refs, dict):
        raise ConfigurationError("Household credential reference registry is invalid")
    record = household_refs.get(env_var)
    if record is None:
        return _legacy_fallback()
    allowed_fields = {"ref", "integration", "account", "slot"}
    if isinstance(record, dict) and "migrated_from" in record:
        allowed_fields.add("migrated_from")
    if (
        not isinstance(record, dict)
        or set(record) != allowed_fields
        or not isinstance(record.get("ref"), str)
        or (
            "migrated_from" in record
            and record.get("migrated_from") not in {"env", "credentials.json"}
        )
    ):
        raise ConfigurationError(f"Credential reference metadata for {env_var} is invalid")
    expected_integration, expected_account, expected_slot = credential_context_for_name(env_var)
    if (
        record.get("integration") != expected_integration
        or record.get("account") != expected_account
        or record.get("slot") != expected_slot
    ):
        raise ConfigurationError(f"Credential reference context for {env_var} is invalid")
    try:
        from rex.credential_vault import (
            VaultCorruptedError,
            VaultUnavailableError,
            get_credential_vault,
            validate_credential_ref,
        )
    except ImportError:  # pragma: no cover - module always present in this repo
        return _legacy_fallback()
    try:
        validate_credential_ref(record["ref"])
    except ValueError as exc:
        raise ConfigurationError(f"Credential reference for {env_var} is invalid") from exc
    try:
        vault_value = get_credential_vault(scope="household").get_secret(
            record["ref"],
            integration=expected_integration,
            account=expected_account,
            slot=expected_slot,
        )
    except VaultUnavailableError:
        return _legacy_fallback()
    except VaultCorruptedError as exc:
        raise ConfigurationError(f"Credential vault data for {env_var} is invalid") from exc
    if vault_value:
        return vault_value
    return _legacy_fallback()


def build_app_config(json_config: dict) -> AppConfig:
    """Build an AppConfig from a merged JSON configuration."""
    # Migrate legacy wake_word key to canonical wakeword key
    json_config = _migrate_wake_word_section(json_config)
    # Parse allowed origins from JSON config
    allowed_origins_value = _get_nested(json_config, "api.allowed_origins", ["*"])
    if isinstance(allowed_origins_value, str):
        allowed_origins = [
            origin.strip().rstrip("/")
            for origin in allowed_origins_value.split(",")
            if origin.strip()
        ] or ["*"]
    elif isinstance(allowed_origins_value, list):
        allowed_origins = [str(o).strip().rstrip("/") for o in allowed_origins_value if o]
    else:
        allowed_origins = ["*"]

    capabilities_value = _get_nested(json_config, "capabilities", [])
    if isinstance(capabilities_value, list):
        capabilities = [str(item) for item in capabilities_value if item]
    else:
        capabilities = []

    # Provider-neutral SpeechRouter policy (S35) — parsed once so its
    # configured env var name can resolve the vault-backed API key below.
    speech_cfg = _parse_speech_config(json_config.get("speech"))

    # Build config from JSON config + vault-backed secrets
    config = AppConfig(
        # Wake word settings from JSON (canonical key: wakeword)
        wakeword=_get_nested(json_config, "wakeword.wakeword", "hey_rex") or "hey_rex",
        wakeword_backend=_get_nested(json_config, "wakeword.backend", "openwakeword"),
        wakeword_keyword=_get_nested(json_config, "wakeword.keyword"),
        wakeword_threshold=_coerce_float(json_config, "wakeword.threshold", 0.5),
        wakeword_window=_coerce_float(json_config, "wakeword.window", 1.0),
        wakeword_poll_interval=_coerce_float(json_config, "wakeword.poll_interval", 0.01),
        wake_sound_path=_get_nested(json_config, "wakeword.wake_sound_path"),
        acknowledgment_sound=_get_nested(json_config, "acknowledgment.sound", "chime"),
        acknowledgment_mode=_get_nested(json_config, "acknowledgment.mode", "sound"),
        response_cache_ttl=_coerce_float(json_config, "response_cache.ttl", 300.0),
        wakeword_model_path=_get_nested(json_config, "wakeword.model_path"),
        wakeword_embedding_path=_get_nested(json_config, "wakeword.embedding_path"),
        wakeword_fallback_to_builtin=bool(
            _get_nested(json_config, "wakeword.fallback_to_builtin", True)
        ),
        wakeword_fallback_keyword=_get_nested(
            json_config, "wakeword.fallback_keyword", "hey jarvis"
        ),
        wakeword_auto_gain=bool(_get_nested(json_config, "wakeword.auto_gain", True)),
        wakeword_target_peak=_coerce_float(json_config, "wakeword.target_peak", 0.35),
        wakeword_max_gain=_coerce_float(json_config, "wakeword.max_gain", 30.0),
        wakeword_min_rms_for_gain=_coerce_float(json_config, "wakeword.min_rms_for_gain", 0.0005),
        # Runtime settings from JSON
        command_duration=_coerce_float(json_config, "runtime.command_duration", 5.0),
        command_vad_rms_threshold=_coerce_float(
            json_config, "runtime.command_vad_rms_threshold", 0.003
        ),
        detection_frame_seconds=_coerce_float(json_config, "runtime.detection_frame_seconds", 1.0),
        capture_seconds=_coerce_float(json_config, "runtime.capture_seconds", 5.0),
        memory_max_turns=_coerce_int(json_config, "runtime.memory_max_turns", 50),
        transcripts_enabled=bool(_get_nested(json_config, "runtime.transcripts_enabled", True)),
        transcripts_dir=Path(_get_nested(json_config, "runtime.transcripts_dir", "transcripts")),
        session_ttl_hours=_coerce_int(json_config, "runtime.session_ttl_hours", 8),
        default_user=_get_nested(json_config, "runtime.active_user"),
        conversation_export=bool(_get_nested(json_config, "runtime.conversation_export", True)),
        speak_language=_get_nested(json_config, "runtime.speak_language", "en"),
        user_id=_get_nested(json_config, "runtime.user_id", "default"),
        # Audio settings from JSON
        sample_rate=_coerce_int(json_config, "audio.sample_rate", 16000),
        audio_input_device=_get_nested(json_config, "audio.input_device_index"),
        audio_output_device=_get_nested(json_config, "audio.output_device_index"),
        tts_output_device=_get_nested(json_config, "audio.tts_output_device"),
        wake_word_input_device=_get_nested(json_config, "audio.wake_word_input_device"),
        # Model settings from JSON
        whisper_model=_get_nested(json_config, "models.stt_model", "base"),
        whisper_device=_get_nested(json_config, "models.stt_device", "auto"),
        whisper_language=_get_nested(json_config, "models.stt_language", "en"),
        stt_auto_gain=bool(_get_nested(json_config, "models.stt_auto_gain", True)),
        stt_target_peak=_coerce_float(json_config, "models.stt_target_peak", 0.45),
        stt_max_gain=_coerce_float(json_config, "models.stt_max_gain", 12.0),
        stt_min_rms_for_gain=_coerce_float(json_config, "models.stt_min_rms_for_gain", 0.0005),
        llm_provider=_get_nested(json_config, "models.llm_provider", "transformers"),
        llm_model=_get_nested(json_config, "models.llm_model", "sshleifer/tiny-gpt2"),
        llm_max_tokens=_coerce_int(json_config, "models.llm_max_tokens", 120),
        llm_temperature=_coerce_float(json_config, "models.llm_temperature", 0.7),
        autonomy_mode=_get_nested(json_config, "models.autonomy_mode", "manual"),
        llm_top_p=_coerce_float(json_config, "models.llm_top_p", 0.9),
        llm_top_k=_coerce_int(json_config, "models.llm_top_k", 50),
        llm_seed=_coerce_int(json_config, "models.llm_seed", 42),
        tts_provider=_get_nested(json_config, "models.tts_provider", "xtts"),
        tts_voice=_get_nested(json_config, "models.tts_voice"),
        tts_speed=_coerce_float(json_config, "models.tts_speed", 1.08),
        tts_fast_short_reply_enabled=bool(
            _get_nested(json_config, "models.tts_fast_short_reply_enabled", True)
        ),
        tts_fast_short_reply_max_chars=int(
            _coerce_float(json_config, "models.tts_fast_short_reply_max_chars", 140)
        ),
        # API settings from JSON
        rate_limit=_get_nested(json_config, "api.rate_limit", "30/minute"),
        allowed_origins=allowed_origins,
        # Search settings from JSON
        search_providers=_get_nested(
            json_config, "search.providers", "serpapi,brave,duckduckgo,google"
        ),
        # Home Assistant non-secret config plus vault-backed credentials
        ha_base_url=_get_nested(json_config, "home_assistant.base_url"),
        ha_verify_ssl=bool(_get_nested(json_config, "home_assistant.verify_ssl", True)),
        ha_timeout=_coerce_float(json_config, "home_assistant.timeout", 10.0),
        ha_token=_secret_env_or_vault("HA_TOKEN", json_config),
        ha_secret=_secret_env_or_vault("HA_SECRET", json_config),
        ha_entity_map=None,
        # Ollama from JSON + vault-backed secrets
        ollama_base_url=_get_nested(json_config, "ollama.base_url", "http://localhost:11434"),
        ollama_use_cloud=bool(_get_nested(json_config, "ollama.use_cloud", False)),
        ollama_api_key=_secret_env_or_vault("OLLAMA_API_KEY", json_config),
        # OpenAI from JSON + vault-backed secrets
        openai_model=_get_nested(json_config, "openai.model"),
        openai_base_url=_get_nested(json_config, "openai.base_url"),
        openai_api_key=_secret_env_or_vault("OPENAI_API_KEY", json_config),
        # OpenRouter from JSON + vault-backed secrets
        openrouter_model=_get_nested(json_config, "openrouter.model"),
        openrouter_base_url=(
            _get_nested(json_config, "openrouter.base_url", OPENROUTER_DEFAULT_BASE_URL)
            or OPENROUTER_DEFAULT_BASE_URL
        ),
        openrouter_api_key=_secret_env_or_vault("OPENROUTER_API_KEY", json_config),
        # Anthropic from JSON + vault-backed secrets
        anthropic_model=_get_nested(json_config, "anthropic.model"),
        anthropic_api_key=_secret_env_or_vault("ANTHROPIC_API_KEY", json_config),
        # All secrets from the vault (or explicit unpackaged legacy mode)
        brave_api_key=_secret_env_or_vault("BRAVE_API_KEY", json_config),
        speak_api_key=_secret_env_or_vault("REX_SPEAK_API_KEY", json_config),
        # Logging from JSON + env
        debug_logging=_get_nested(json_config, "runtime.log_level", "INFO").upper() == "DEBUG",
        debug_mode=os.getenv("REX_DEBUG", "0").strip() not in ("0", "false", "no", ""),
        file_logging_enabled=bool(_get_nested(json_config, "runtime.file_logging_enabled", False)),
        log_path=Path(_get_nested(json_config, "runtime.log_path", str(DEFAULT_RUNTIME_LOG_FILE))),
        error_log_path=Path(
            _get_nested(json_config, "runtime.error_log_path", str(DEFAULT_ERROR_LOG_FILE))
        ),
        memory_max_bytes=_coerce_int(json_config, "runtime.memory_max_bytes", 131072),
        warm_runtime_max_cost_mb=_coerce_float(
            json_config, "runtime.warm_runtime_max_cost_mb", 6144.0
        ),
        warm_runtime_idle_timeout_s=_coerce_float(
            json_config, "runtime.warm_runtime_idle_timeout_s", 900.0
        ),
        # Profile metadata
        active_profile=_get_nested(json_config, "active_profile", "default"),
        capabilities=capabilities,
        # Location and weather (location from JSON, API key from env)
        default_location=_get_nested(json_config, "location.default_location"),
        default_timezone=_get_nested(json_config, "location.default_timezone"),
        openweathermap_api_key=_secret_env_or_vault("OPENWEATHERMAP_API_KEY", json_config),
        # Conversational followups
        followups_enabled=bool(_get_nested(json_config, "conversation.followups.enabled", False)),
        followups_max_per_session=_coerce_int(
            json_config, "conversation.followups.max_per_session", 2
        ),
        followups_lookback_hours=_coerce_int(
            json_config, "conversation.followups.lookback_hours", 72
        ),
        followups_expire_hours=_coerce_int(json_config, "conversation.followups.expire_hours", 168),
        # OpenClaw integration
        use_openclaw_tools=bool(_get_nested(json_config, "openclaw.use_tools", False)),
        use_openclaw_voice_backend=bool(
            _get_nested(json_config, "openclaw.use_voice_backend", False)
        ),
        openclaw_gateway_url=_get_nested(json_config, "openclaw.gateway_url", ""),
        openclaw_gateway_timeout=_coerce_int(json_config, "openclaw.gateway_timeout", 30),
        openclaw_gateway_max_retries=_coerce_int(json_config, "openclaw.gateway_max_retries", 3),
        openclaw_gateway_token=_secret_env_or_vault("OPENCLAW_GATEWAY_TOKEN", json_config),
        # Telegram bot integration (US-039)
        telegram_bot_token=_secret_env_or_vault("TELEGRAM_BOT_TOKEN", json_config),
        telegram_chat_id=_get_nested(json_config, "telegram.chat_id"),
        # Push notifications (US-042)
        push_provider=_get_nested(json_config, "notifications.push_provider"),
        push_token=_secret_env_or_vault("PUSH_TOKEN", json_config),
        push_topic=_get_nested(json_config, "notifications.push_topic"),
        # Email/calendar provider selection
        email_provider=_get_nested(json_config, "email.provider", "none"),
        calendar_provider=_normalize_calendar_provider(
            _get_nested(json_config, "calendar.provider", "none")
        ),
        # Multi-account email (US-208)
        email_accounts=_parse_email_accounts(_get_nested(json_config, "email.accounts", [])),
        email_default_account_id=_get_nested(json_config, "email.default_account_id", ""),
        # Per-user multi-email accounts (US-ME-001)
        user_email_accounts=_parse_user_email_accounts(
            _get_nested(json_config, "users", {}),
            _get_nested(json_config, "email.accounts", []),
        ),
        # Per-user default email account selection (issue #303)
        user_default_email_accounts=_parse_user_default_email_accounts(
            _get_nested(json_config, "users", {}),
        ),
        # History persistence
        persist_history=bool(_get_nested(json_config, "runtime.persist_history", True)),
        history_db_path=Path(
            _get_nested(
                json_config,
                "runtime.history_db_path",
                str(household_data_path("history.db")),
            )
        ),
        history_retention_days=_coerce_int(json_config, "runtime.history_retention_days", 30),
        # Model routing
        model_routing=_parse_model_routing(_get_nested(json_config, "model_routing", {})),
        llm_routing_mode=str(
            _get_nested(json_config, "model_routing.llm_routing_mode", "local_preferred")
        ),
        cloud_fallback_cooldown_seconds=_coerce_int(
            json_config, "model_routing.cloud_fallback_cooldown_seconds", 3600
        ),
        # Voice identity
        speaker_id_threshold=_coerce_float(
            json_config, "voice_identity.speaker_id_threshold", 0.75
        ),
        # Local file access allowlist
        allowed_file_roots=_parse_allowed_file_roots(
            _get_nested(json_config, "file_ops.allowed_roots", [])
        ),
        # Windows settings confirmation
        require_confirm_system_changes=bool(
            _get_nested(json_config, "windows.require_confirm_system_changes", True)
        ),
        # Mobile API gateway (issue #323)
        mobile_api=_parse_mobile_api_config(json_config.get("mobile_api")),
        # Provider-neutral SpeechRouter policy (S35)
        speech=speech_cfg,
        voicestudio_api_key=_secret_env_or_vault(
            speech_cfg.voicestudio_api_key_env or "VOICESTUDIO_API_KEY", json_config
        ),
    )

    return config


def load_config(
    *, env_path: Optional[Path] = None, reload: bool = False, json_config: Optional[dict] = None
) -> AppConfig:
    """Load non-secret configuration and resolve vault-backed secrets.

    Args:
        env_path: Legacy .env path, consulted only with explicit operator opt-in
        reload: Force reload instead of using cached config
        json_config: Pre-loaded JSON config dict (if None, loads from rex/config_manager)

    Returns:
        AppConfig with runtime settings from JSON and secrets from the vault

    Note:
        Non-secret environment variables are now ignored. Use rex_config.json instead.
    """
    global _cached_config
    if not reload and json_config is None:
        config_module = sys.modules.get("config")
        if config_module is not None:
            cached = getattr(config_module, "_cached_config", None)
            if cached is not None:
                _cached_config = cached
                return _cached_config
        if _cached_config is not None:
            return _cached_config

    # Persisted plaintext `.env` has no authority by default. Loading it into
    # process state is permitted only in explicit legacy/operator mode.
    from rex.credentials import legacy_plaintext_fallback_enabled

    if legacy_plaintext_fallback_enabled():
        load_dotenv(env_path or ENV_PATH, override=False)

    # Load JSON config for runtime settings
    if json_config is None:
        from rex.config_manager import load_config as load_json_config

        json_config = load_json_config()

    # Warn about legacy environment variables
    warnings = get_legacy_env_warnings()
    if warnings:
        for warning in warnings[:3]:  # Limit to first 3 to avoid spam
            print(warning, file=sys.stderr)
        if len(warnings) > 3:
            print(
                f"... and {len(warnings) - 3} more legacy env vars. "
                f"Run 'rex-config migrate-legacy-env' to migrate all.",
                file=sys.stderr,
            )

    try:
        json_config = _merge_profile_config(json_config)
    except Exception as exc:
        raise ConfigurationError(f"Profile loading failed: {exc}") from exc

    config = build_app_config(json_config)

    validate_config(config)
    _cached_config = config

    if config.debug_logging:
        set_global_level(10)

    return config


def validate_config(config: AppConfig) -> None:
    if not (0 < config.wakeword_threshold <= 1):
        raise ConfigurationError("wakeword_threshold must be between 0 and 1.")
    if config.command_duration <= 0:
        raise ConfigurationError("command_duration must be positive.")
    if config.wakeword_window <= 0:
        raise ConfigurationError("wakeword_window must be positive.")
    if config.llm_max_tokens <= 0:
        raise ConfigurationError("llm_max_tokens must be positive.")
    if not (0 <= config.llm_temperature <= 5.0):
        raise ConfigurationError("llm_temperature must be between 0 and 5.")
    if not isinstance(config.autonomy_mode, str) or config.autonomy_mode not in {
        "manual",
        "supervised",
        "full-auto",
    }:
        raise ConfigurationError("autonomy_mode must be manual, supervised, or full-auto.")
    if config.memory_max_turns <= 0:
        raise ConfigurationError("memory_max_turns must be positive.")
    if config.use_openclaw_tools or config.use_openclaw_voice_backend:
        integrations = config.integrations
        if integrations is None:
            raise ConfigurationError("OpenClaw integration settings are unavailable.")
        gateway_url = integrations.openclaw_gateway_url.strip()
        gateway_token = (config.openclaw_gateway_token or "").strip()
        if not gateway_url or not gateway_token:
            raise ConfigurationError(
                "OpenClaw features are enabled but a gateway URL and token are not both configured."
            )
        parsed_gateway = urlparse(gateway_url)
        if parsed_gateway.scheme not in {"http", "https"} or not parsed_gateway.hostname:
            raise ConfigurationError(
                "OpenClaw features require a valid HTTP(S) gateway URL and token."
            )


def reload_settings(
    *, env_path: Optional[Path] = None, json_config: Optional[dict] = None
) -> AppConfig:
    """Reload configuration, optionally with new JSON config."""
    return load_config(env_path=env_path, reload=True, json_config=json_config)


def show_config(config: Optional[AppConfig] = None) -> None:
    """Print the resolved configuration to stdout in stable JSON format."""
    cfg = config or load_config()
    print(json.dumps(cfg.to_dict(), indent=2, sort_keys=True, default=str))


def _cmd_show(args: argparse.Namespace) -> int:
    """Print the current configuration."""
    cfg = load_config(env_path=ENV_PATH, reload=True)
    show_config(cfg)
    return 0


def _cmd_migrate_legacy_env(args: argparse.Namespace) -> int:
    """Migrate legacy environment variables to rex_config.json."""
    from rex.config_manager import migrate_legacy_env_to_config

    env_path = Path(args.env_path) if args.env_path else ENV_PATH
    notes = migrate_legacy_env_to_config(
        env_path=env_path,
        config_path=args.config_path,
        dry_run=args.dry_run,
    )
    for note in notes:
        print(note)
    return 0


@wrap_entrypoint
def cli(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="rex-config",
        description="Configure Rex Assistant",
    )
    subparsers = parser.add_subparsers(dest="command")

    # show
    show_parser = subparsers.add_parser(
        "show",
        help="Print current configuration",
    )
    show_parser.set_defaults(func=_cmd_show)

    # migrate-legacy-env
    migrate_parser = subparsers.add_parser(
        "migrate-legacy-env",
        help="Migrate legacy environment variables into config/rex_config.json",
        description=(
            "Reads legacy non-secret environment variables (e.g. OPENAI_BASE_URL) "
            "and writes their values into config/rex_config.json. Existing non-default "
            "config values are never overwritten."
        ),
    )
    migrate_parser.add_argument(
        "--config-path",
        default="config/rex_config.json",
        help="Path to rex_config.json (default: config/rex_config.json)",
    )
    migrate_parser.add_argument(
        "--env-path",
        default=None,
        help=("Path to .env file to read legacy variables from " "(default: repo root .env)"),
    )
    migrate_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be migrated without writing any changes",
    )
    migrate_parser.set_defaults(func=_cmd_migrate_legacy_env)

    # Backward compat: --show and --reload still work
    parser.add_argument("--show", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--reload", action="store_true", help=argparse.SUPPRESS)

    args = parser.parse_args(argv)

    ENV_PATH.touch(exist_ok=True)

    # Handle legacy flags
    if args.show or args.reload:
        cfg = load_config(env_path=ENV_PATH, reload=True)
        show_config(cfg)
        return 0

    if args.command is None:
        parser.print_help()
        return 0

    return args.func(args)  # type: ignore[no-any-return]


settings = load_config()
Settings = AppConfig

if __name__ == "__main__":
    import sys

    try:
        raise SystemExit(cli())
    except ConfigurationError as exc:
        LOGGER.error("Config error: %s", exc)
        raise SystemExit(1) from exc
