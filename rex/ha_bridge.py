"""Home Assistant intent bridge for Rex."""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from rex.ha.clarification import ClarificationHandler
from rex.ha.command_history import CommandHistory
from rex.ha.device_aliases import AliasResolver
from rex.ha.device_state import get_device_state
from rex.ha.error_recovery import suggest_alternatives
from rex.ha.mutation_service import (
    HAMutation,
    HAMutationService,
    HAOutcome,
    HARisk,
    classify_ha_risk,
)

try:
    import requests as _imported_requests
except ImportError as exc:
    requests: Any | None = None
    _REQUESTS_IMPORT_ERROR: Exception | None = exc
else:
    requests = _imported_requests
    _REQUESTS_IMPORT_ERROR = None
if TYPE_CHECKING:
    from flask import Blueprint

from rex.config import settings

logger = logging.getLogger(__name__)

_MEDIA_PLAYER_ENTITY_ID_PATTERN = re.compile(r"media_player\.[a-z0-9_]+")

_flask_blueprint = None
_flask_jsonify = None
_flask_request = None


def _require_requests() -> None:
    if requests is None:
        raise RuntimeError(
            "The Home Assistant bridge requires the 'requests' package. "
            "Install with: pip install requests"
        ) from _REQUESTS_IMPORT_ERROR


def _require_flask():
    global _flask_blueprint, _flask_jsonify, _flask_request
    if _flask_blueprint is not None:
        return _flask_blueprint, _flask_jsonify, _flask_request
    if importlib.util.find_spec("flask") is None:
        raise RuntimeError(
            "Flask is required for Home Assistant bridge. Install with: pip install flask"
        )
    flask = importlib.import_module("flask")
    _flask_blueprint = flask.Blueprint
    _flask_jsonify = flask.jsonify
    _flask_request = flask.request
    return _flask_blueprint, _flask_jsonify, _flask_request


def _utc_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


@dataclass
class IntentMatch:
    """Represents an intent matched from natural language."""

    domain: str
    service: str
    entity_id: str
    data: dict[str, Any]
    description: str
    source: str


@dataclass(frozen=True)
class _PendingHAConfirmation:
    """One action-bound confirmation that may be consumed by the next user turn."""

    mutation: HAMutation
    token: str
    intent: IntentMatch


class _BridgeMutationClient:
    """Adapter that gives HAMutationService the minimal HA client contract."""

    def __init__(self, bridge: HABridge) -> None:
        self._bridge = bridge

    def call_service(self, domain: str, service: str, data: dict[str, Any]) -> None:
        self._bridge._request("POST", f"/api/services/{domain}/{service}", json=data)

    def get_state(self, entity_id: str) -> dict[str, Any] | None:
        value = self._bridge._request("GET", f"/api/states/{entity_id}")
        return value if isinstance(value, dict) else None


class HABridge:
    """Translate Rex intents into Home Assistant service calls."""

    _TAG_PATTERN = re.compile(
        r"\[\[ha:(?P<domain>[a-z_]+)\.(?P<service>[a-z_]+)"
        r"(?P<params>(?:\s+[a-z_]+=[^;\]]+)*(?:\s*;\s*[a-z_]+=[^;\]]+)*)\]\]",
        re.IGNORECASE,
    )
    _TURN_ON_PATTERN = re.compile(
        r"\bturn\s+on\s+(?:the\s+)?(?P<entity>[a-z0-9\s]+)", re.IGNORECASE
    )
    _TURN_OFF_PATTERN = re.compile(
        r"\bturn\s+off\s+(?:the\s+)?(?P<entity>[a-z0-9\s]+)", re.IGNORECASE
    )
    _SET_TEMP_PATTERN = re.compile(
        r"\bset\s+(?:the\s+)?(?P<entity>[a-z0-9\s]+?)\s+to\s+(?P<value>\d{2,3})(?:\s*degrees)?",
        re.IGNORECASE,
    )
    _SET_PERCENT_PATTERN = re.compile(
        r"\b(set|dim)\s+(?:the\s+)?(?P<entity>[a-z0-9\s]+)\s+to\s+(?P<value>\d{1,3})\s*%",
        re.IGNORECASE,
    )
    _ACTIVATE_PATTERN = re.compile(
        r"\b(activate|start|run)\s+(?:the\s+)?(?P<entity>[a-z0-9\s]+)", re.IGNORECASE
    )
    _LOCK_PATTERN = re.compile(
        r"\b(lock|unlock)\s+(?:the\s+)?(?P<entity>[a-z0-9\s]+)", re.IGNORECASE
    )
    _CONFIRM_PATTERN = re.compile(
        r"^\s*(?:yes[\s,]+)?confirm(?:\s+(?:it|that|action))?[.!]?\s*$",
        re.IGNORECASE,
    )
    _CANCEL_PATTERN = re.compile(
        r"^\s*(?:no[\s,]+)?(?:cancel|never mind|nevermind)[.!]?\s*$",
        re.IGNORECASE,
    )

    SUPPORTED_INTENTS = [
        {"intent": "turn_on", "description": "Turn on a light, switch, or scene"},
        {"intent": "turn_off", "description": "Turn off a light, switch, or scene"},
        {"intent": "set_temperature", "description": "Set thermostat temperature"},
        {"intent": "set_percentage", "description": "Set brightness or fan speed"},
        {"intent": "activate", "description": "Activate a scene or script"},
        {"intent": "lock_control", "description": "Lock or unlock a lock"},
    ]

    def __init__(
        self,
        *,
        base_url: str | None = None,
        token: str | None = None,
        secret: str | None = None,
        verify_ssl: bool | None = None,
        timeout: float | None = None,
        entity_map: dict[str, str] | None = None,
        user_id: str | None = None,
        mutation_service: HAMutationService | None = None,
    ) -> None:
        cfg = settings
        cfg_base_url = getattr(cfg, "ha_base_url", "") or ""
        cfg_token = getattr(cfg, "ha_token", "") or ""
        cfg_secret = getattr(cfg, "ha_secret", "") or ""
        self._base_url = (cfg_base_url if base_url is None else base_url or "").rstrip("/")
        self._token = cfg_token if token is None else token or ""
        self._secret = cfg_secret if secret is None else secret or ""
        self._verify_ssl = cfg.ha_verify_ssl if verify_ssl is None else verify_ssl
        self._timeout = cfg.ha_timeout if timeout is None else timeout
        self._entity_map = {
            alias.lower(): entity_id
            for alias, entity_id in (entity_map or cfg.ha_entity_map or {}).items()
            if isinstance(alias, str) and isinstance(entity_id, str)
        }
        _require_requests()
        requests_module = requests
        assert requests_module is not None
        self._session = requests_module.Session()
        self._entity_cache: dict[str, str] = {}
        self._entity_states: dict[str, dict[str, Any]] = {}
        self._entity_cache_ts: float = 0.0
        self._entity_cache_ttl: float = 60.0
        self._lock = threading.Lock()
        self._log_path = Path("logs/test_ha_integration.log")
        self._command_history = CommandHistory()
        _aliases_path = getattr(cfg, "ha_aliases_path", None)
        self._clarification = ClarificationHandler(
            AliasResolver(_aliases_path) if _aliases_path else AliasResolver()
        )
        self._user_id = str(user_id or getattr(cfg, "user_id", "") or "default")
        # Lazily construct the canonical mutation service. Many HABridge users
        # only need read/media helpers, and construction must not create state.
        self._mutation_service = mutation_service
        self._pending_confirmation: _PendingHAConfirmation | None = None
        self._pending_confirmation_lock = threading.Lock()

    @staticmethod
    def _request_exception() -> type[Exception]:
        requests_module = requests
        if requests_module is None:
            return Exception
        return cast(type[Exception], requests_module.RequestException)

    # ------------------------------------------------------------------ #
    # Public properties
    # ------------------------------------------------------------------ #

    @property
    def enabled(self) -> bool:
        return bool(self._base_url and self._token)

    @property
    def secret(self) -> str:
        return self._secret

    # ------------------------------------------------------------------ #
    # Transcript and response handling
    # ------------------------------------------------------------------ #

    def _get_mutation_service(self) -> HAMutationService:
        service = getattr(self, "_mutation_service", None)
        if service is None:
            token = str(getattr(self, "_token", ""))
            confirmation_secret = hashlib.sha256(
                f"askrex-ha-confirmation:{token}".encode()
            ).digest()
            service = HAMutationService(
                _BridgeMutationClient(self),
                confirmation_secret=confirmation_secret,
            )
            self._mutation_service = service
        return service

    @staticmethod
    def _non_actionable_transcript(transcript: str) -> bool:
        text = transcript.strip().lower()
        if not text:
            return True
        if "?" in transcript:
            return True
        return bool(
            re.search(
                r"\b(?:don't|do not|should i|could i|would i|what if|is it|did i)\b",
                text,
            )
        )

    def _render_mutation_result(self, match: IntentMatch, result: Any) -> str:
        self._log_event(match, result.success, result.detail)
        if result.status == HAOutcome.VERIFIED:
            self._command_history.push(
                entity_id=match.entity_id,
                domain=match.domain,
                service=match.service,
                data=match.data,
                description=match.description,
            )
        elif result.status == HAOutcome.FAILED:
            return suggest_alternatives(
                failed_entity_id=match.entity_id,
                domain=match.domain,
                entity_map=self._entity_map,
                entity_cache=self._entity_cache,
                recent_entity_ids=self._command_history.recent_entity_ids(),
            )
        elif result.status == HAOutcome.CONFIRMATION_REQUIRED:
            detail = result.detail or "This Home Assistant action requires confirmation."
            return f"{detail} Say 'confirm' as your next reply to continue."
        from rex.response.builder import home_assistant_status_message

        return home_assistant_status_message(
            result.status.value,
            entity_id=result.entity_id,
            detail=result.detail,
            expected=result.expected,
        )

    def _handle_pending_confirmation(self, transcript: str) -> str | None:
        lock = getattr(self, "_pending_confirmation_lock", None)
        if lock is None:
            # A few legacy unit fixtures construct HABridge via __new__.
            # Production construction always initializes this lock.
            lock = threading.Lock()
            self._pending_confirmation_lock = lock
        with lock:
            pending = getattr(self, "_pending_confirmation", None)
            if pending is None:
                return None
            if self._CANCEL_PATTERN.fullmatch(transcript):
                self._pending_confirmation = None
                friendly = pending.mutation.entity_id.replace("_", " ").replace(".", " ")
                return f"Canceled. I did not change {friendly}."
            if not self._CONFIRM_PATTERN.fullmatch(transcript):
                # Confirmation is intentionally next-turn only. Any intervening
                # user turn clears it so a later unrelated "confirm" cannot
                # accidentally authorize a stale action.
                self._pending_confirmation = None
                return None
            self._pending_confirmation = None

        mutation = pending.mutation
        confirmed = HAMutation(
            user_id=mutation.user_id,
            entity_id=mutation.entity_id,
            domain=mutation.domain,
            service=mutation.service,
            parameters=dict(mutation.parameters),
            request_id=mutation.request_id,
            confirmation_token=pending.token,
        )
        result = self._get_mutation_service().execute(confirmed)
        return self._render_mutation_result(pending.intent, result)

    def process_transcript(self, transcript: str) -> str | None:
        """Detect HA intents but execute mutations only through canonical policy."""
        if not self.enabled:
            return None
        pending_reply = self._handle_pending_confirmation(transcript)
        if pending_reply is not None:
            return pending_reply
        if self._non_actionable_transcript(transcript):
            return None
        clarification = self._clarification.check(transcript)
        if clarification is not None:
            return clarification
        match = self._match_transcript(transcript)
        if not match:
            return None

        mutation = HAMutation(
            user_id=str(getattr(self, "_user_id", "") or "default"),
            entity_id=match.entity_id,
            domain=match.domain,
            service=match.service,
            parameters=dict(match.data),
            request_id=uuid.uuid4().hex,
        )
        result = self._get_mutation_service().execute(mutation)
        if result.status == HAOutcome.CONFIRMATION_REQUIRED:
            token = result.confirmation_token
            if not isinstance(token, str) or not token:
                logger.error("HA confirmation result omitted its action-bound token")
                return "I did not perform that Home Assistant action because confirmation failed."
            with self._pending_confirmation_lock:
                self._pending_confirmation = _PendingHAConfirmation(
                    mutation=mutation,
                    token=token,
                    intent=match,
                )
        return self._render_mutation_result(match, result)

    def undo_last(self, window: float = 30.0) -> str:
        """Reverse the most recent reversible command if within *window* seconds.

        Returns a confirmation string suitable for TTS playback.
        """
        self._command_history.undo_window = window
        candidate = self._command_history.pop_undo_candidate()
        if candidate is None:
            return "There is nothing to undo right now."

        inverse = candidate.inverse_service
        assert inverse is not None  # guaranteed by pop_undo_candidate

        undo_intent = IntentMatch(
            domain=candidate.domain,
            service=inverse,
            entity_id=candidate.entity_id,
            data={"entity_id": candidate.entity_id},
            description=f"undo: {inverse.replace('_', ' ')} {candidate.entity_id}",
            source="undo",
        )
        result = self._get_mutation_service().execute(
            HAMutation(
                user_id=str(getattr(self, "_user_id", "") or "default"),
                entity_id=undo_intent.entity_id,
                domain=undo_intent.domain,
                service=undo_intent.service,
                parameters=dict(undo_intent.data),
                request_id=uuid.uuid4().hex,
            )
        )
        self._log_event(undo_intent, result.success, result.detail)
        if result.status == HAOutcome.VERIFIED:
            inv_friendly = inverse.replace("_", " ")
            return f"Undone. {inv_friendly.capitalize()} {candidate.entity_id.split('.')[-1]}."
        from rex.response.builder import home_assistant_status_message

        return home_assistant_status_message(
            result.status.value,
            entity_id=result.entity_id,
            detail=result.detail,
            expected=result.expected,
        )

    def post_process_response(self, response: str) -> str:
        """Strip legacy model-emitted HA tags without granting execution authority."""
        if "[[ha:" not in response.lower():
            return response

        tags = list(self._TAG_PATTERN.finditer(response))
        if not tags:
            return response
        sanitized = response
        for tag in tags:
            sanitized = sanitized.replace(tag.group(0), "").strip()
        logger.warning(
            "Suppressed model-emitted Home Assistant command syntax",
            extra={"event": "assistant_ha_model_tag_suppressed", "count": len(tags)},
        )
        return sanitized or "I did not execute that Home Assistant command."

    # ------------------------------------------------------------------ #
    # HTTP endpoints helpers
    # ------------------------------------------------------------------ #

    def list_entities(self) -> list[dict[str, Any]]:
        """Return cached Home Assistant entities (refreshing as needed)."""
        if not self.enabled:
            raise RuntimeError("Home Assistant bridge is not configured.")
        self._refresh_entity_cache(force=True)
        entities = []
        for entity_id, state in sorted(self._entity_states.items()):
            attributes = dict(state.get("attributes") or {})
            friendly_name = attributes.get("friendly_name")
            entities.append(
                {
                    **state,
                    "friendly_name": (
                        friendly_name if isinstance(friendly_name, str) else entity_id
                    ),
                    "attributes": attributes,
                }
            )
        return entities

    def get_entity_state(self, entity_id: str) -> dict[str, Any] | None:
        """Read one entity through Rex's existing Home Assistant state path."""
        return get_device_state(
            entity_id,
            self._base_url,
            self._token,
            verify_ssl=self._verify_ssl,
            timeout=self._timeout,
        )

    def execute_media_service(
        self,
        entity_id: str,
        service: str,
        *,
        volume_level: float | None = None,
        is_volume_muted: bool | None = None,
    ) -> tuple[bool, str]:
        """Execute one explicitly supported media-player service."""
        supported_services = {
            "media_play",
            "media_pause",
            "media_stop",
            "media_next_track",
            "media_previous_track",
            "volume_set",
            "volume_mute",
        }
        if service not in supported_services:
            raise ValueError(f"Unsupported Home Assistant media service: {service}")
        if _MEDIA_PLAYER_ENTITY_ID_PATTERN.fullmatch(entity_id) is None:
            raise ValueError(f"Invalid Home Assistant media-player entity: {entity_id}")

        data: dict[str, Any] = {"entity_id": entity_id}
        if service == "volume_set":
            if (
                isinstance(volume_level, bool)
                or not isinstance(volume_level, (int, float))
                or not 0 <= volume_level <= 1
            ):
                raise ValueError("volume_set requires a volume level from 0 to 1")
            if is_volume_muted is not None:
                raise ValueError("volume_set does not accept mute state")
            data["volume_level"] = float(volume_level)
        elif service == "volume_mute":
            if not isinstance(is_volume_muted, bool):
                raise ValueError("volume_mute requires a boolean mute state")
            if volume_level is not None:
                raise ValueError("volume_mute does not accept volume level")
            data["is_volume_muted"] = is_volume_muted
        elif volume_level is not None or is_volume_muted is not None:
            raise ValueError(f"Home Assistant media service {service} does not accept volume data")

        intent = IntentMatch(
            domain="media_player",
            service=service,
            entity_id=entity_id,
            data=data,
            description=f"{service} {entity_id}",
            source="canonical_media",
        )
        return self._execute_intent(intent)

    def control_light(
        self,
        entity_id: str,
        action: str,
        *,
        brightness_pct: int | None = None,
    ) -> dict[str, Any]:
        """Turn on/off a light entity with optional brightness.

        Args:
            entity_id: The HA entity id (e.g. ``light.living_room``).
            action: ``"turn_on"`` or ``"turn_off"``.
            brightness_pct: Optional 0-100 brightness percentage (only used with turn_on).

        Returns:
            The raw HA API response dict.
        """
        if not entity_id:
            raise ValueError("entity_id is required")
        action = action.lower()
        if action not in ("turn_on", "turn_off"):
            raise ValueError(f"action must be 'turn_on' or 'turn_off', got {action!r}")
        data: dict[str, Any] = {"entity_id": entity_id}
        if action == "turn_on" and brightness_pct is not None:
            data["brightness_pct"] = max(0, min(100, int(brightness_pct)))
        intent = IntentMatch(
            domain="light",
            service=action,
            entity_id=entity_id,
            data=data,
            description=f"{action.replace('_', ' ')} {entity_id}",
            source="api",
        )
        success, message = self._execute_intent(intent)
        self._log_event(intent, success, message)
        return {"success": success, "message": message, "entity_id": entity_id}

    def control_switch(
        self,
        entity_id: str,
        action: str,
    ) -> dict[str, Any]:
        """Turn on/off a switch entity.

        Args:
            entity_id: The HA entity id (e.g. ``switch.garage``).
            action: ``"turn_on"`` or ``"turn_off"``.

        Returns:
            The raw HA API response dict.
        """
        if not entity_id:
            raise ValueError("entity_id is required")
        action = action.lower()
        if action not in ("turn_on", "turn_off"):
            raise ValueError(f"action must be 'turn_on' or 'turn_off', got {action!r}")
        intent = IntentMatch(
            domain="switch",
            service=action,
            entity_id=entity_id,
            data={"entity_id": entity_id},
            description=f"{action.replace('_', ' ')} {entity_id}",
            source="api",
        )
        success, message = self._execute_intent(intent)
        self._log_event(intent, success, message)
        return {"success": success, "message": message, "entity_id": entity_id}

    def call_script(
        self,
        script_id: str,
        variables: dict[str, Any] | None = None,
        *,
        confirmation_token: str | None = None,
    ) -> dict[str, Any]:
        """Execute a script only through the canonical sensitive-mutation gate."""
        if not script_id:
            raise ValueError("script identifier is required")
        entity_id = script_id.strip().lower()
        if not entity_id.startswith("script."):
            entity_id = f"script.{entity_id}"
        mutation = HAMutation(
            user_id=str(getattr(self, "_user_id", "") or "default"),
            entity_id=entity_id,
            domain="script",
            service="turn_on",
            parameters={"entity_id": entity_id, "variables": variables or {}},
            request_id=uuid.uuid4().hex,
            confirmation_token=confirmation_token,
        )
        return self._get_mutation_service().execute(mutation).to_dict()

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _match_transcript(self, transcript: str) -> IntentMatch | None:
        text = transcript.strip().lower()
        if not text:
            return None

        turn_on = self._TURN_ON_PATTERN.search(text)
        if turn_on:
            entity = turn_on.group("entity").strip()
            entity_id = self._resolve_entity(entity)
            if entity_id:
                return IntentMatch(
                    domain=entity_id.split(".", 1)[0],
                    service="turn_on",
                    entity_id=entity_id,
                    data={"entity_id": entity_id},
                    description=f"turn on {entity}",
                    source="transcript",
                )

        turn_off = self._TURN_OFF_PATTERN.search(text)
        if turn_off:
            entity = turn_off.group("entity").strip()
            entity_id = self._resolve_entity(entity)
            if entity_id:
                return IntentMatch(
                    domain=entity_id.split(".", 1)[0],
                    service="turn_off",
                    entity_id=entity_id,
                    data={"entity_id": entity_id},
                    description=f"turn off {entity}",
                    source="transcript",
                )

        temp = self._SET_TEMP_PATTERN.search(text)
        if temp:
            entity = temp.group("entity").strip()
            entity_id = self._resolve_entity(entity)
            value = temp.group("value")
            if entity_id and value:
                return IntentMatch(
                    domain="climate",
                    service="set_temperature",
                    entity_id=entity_id,
                    data={"entity_id": entity_id, "temperature": float(value)},
                    description=f"set {entity} to {value}",
                    source="transcript",
                )

        percent = self._SET_PERCENT_PATTERN.search(text)
        if percent:
            entity = percent.group("entity").strip()
            entity_id = self._resolve_entity(entity)
            value = percent.group("value")
            if entity_id and value:
                level = max(0, min(100, int(value)))
                service = "set_percentage" if entity_id.startswith("fan.") else "turn_on"
                data = {"entity_id": entity_id}
                if entity_id.startswith("light.") or entity_id.startswith("switch."):
                    data["brightness_pct"] = level  # type: ignore[assignment]
                else:
                    data["percentage"] = level  # type: ignore[assignment]
                return IntentMatch(
                    domain=entity_id.split(".", 1)[0],
                    service=service,
                    entity_id=entity_id,
                    data=data,
                    description=f"set {entity} to {level}%",
                    source="transcript",
                )

        activate = self._ACTIVATE_PATTERN.search(text)
        if activate:
            entity = activate.group("entity").strip()
            entity_id = self._resolve_entity(entity)
            if entity_id:
                domain = entity_id.split(".", 1)[0]
                service = "turn_on" if domain in {"scene", "script"} else "start"
                return IntentMatch(
                    domain=domain,
                    service=service,
                    entity_id=entity_id,
                    data={"entity_id": entity_id},
                    description=f"activate {entity}",
                    source="transcript",
                )

        lock = self._LOCK_PATTERN.search(text)
        if lock:
            entity = lock.group("entity").strip()
            entity_id = self._resolve_entity(entity)
            if entity_id and entity_id.startswith("lock."):
                intent = lock.group(1).lower()
                service = "lock" if intent == "lock" else "unlock"
                return IntentMatch(
                    domain="lock",
                    service=service,
                    entity_id=entity_id,
                    data={"entity_id": entity_id},
                    description=f"{service} {entity}",
                    source="transcript",
                )

        return None

    def _execute_intent(self, intent: IntentMatch) -> tuple[bool, str]:
        # This legacy helper is intentionally safe-domain only. Sensitive or
        # prohibited mutations must flow through HAMutationService so
        # confirmation, verification, and audit semantics cannot be bypassed by
        # a new internal caller.
        if classify_ha_risk(intent.domain, intent.service) != HARisk.SAFE:
            return (
                False,
                "Sensitive Home Assistant actions require the canonical confirmation path.",
            )
        try:
            self._request(
                "POST",
                f"/api/services/{intent.domain}/{intent.service}",
                json=intent.data,
            )
            pretty = intent.description.capitalize()
            return True, f"{pretty}."
        except self._request_exception() as exc:
            logger.warning("Home Assistant request failed: %s", exc)
            return False, str(exc)

    def _parse_params(self, params: str) -> dict[str, Any]:
        data: dict[str, Any] = {}
        if not params:
            return data
        parts = re.split(r"[;\s]+", params.strip())
        for part in parts:
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            key = key.strip().lower()
            value = value.strip()
            if value.isdigit():
                data[key] = int(value)
            else:
                try:
                    data[key] = float(value)
                except ValueError:
                    data[key] = value
        return data

    def _resolve_entity(self, name: str) -> str | None:
        """Resolve only exact aliases/friendly names on the legacy regex path.

        Fuzzy or substring resolution is intentionally not authoritative for
        mutations: ambiguous language must fall through to the canonical
        capability/tool path where clarification and policy are available.
        """
        key = name.strip().lower()
        if not key:
            return None
        if key in self._entity_map:
            return self._entity_map[key]
        if re.fullmatch(r"[a-z0-9_]+\.[a-z0-9_]+", key):
            mapped_ids = set(self._entity_map.values())
            if key in mapped_ids:
                return key

        self._refresh_entity_cache()
        if key in self._entity_cache:
            return self._entity_cache[key]
        if re.fullmatch(r"[a-z0-9_]+\.[a-z0-9_]+", key):
            if key in set(self._entity_cache.values()):
                return key
        return None

    def _refresh_entity_cache(self, force: bool = False) -> None:
        if not self.enabled:
            return
        with self._lock:
            if not force and (time.perf_counter() - self._entity_cache_ts) < self._entity_cache_ttl:
                return
            try:
                payload = self._request("GET", "/api/states")
            except self._request_exception() as exc:
                logger.debug("Failed to refresh Home Assistant entities: %s", exc)
                return

            cache: dict[str, str] = {}
            states: dict[str, dict[str, Any]] = {}
            for item in payload if isinstance(payload, list) else []:
                if not isinstance(item, dict):
                    continue
                entity_id = item.get("entity_id")
                attributes = item.get("attributes")
                if not isinstance(attributes, dict):
                    attributes = {}
                friendly = attributes.get("friendly_name")
                if isinstance(entity_id, str):
                    states[entity_id] = {
                        **item,
                        "attributes": dict(attributes),
                    }
                    if isinstance(friendly, str):
                        cache[friendly.lower()] = entity_id
            self._entity_cache = cache
            self._entity_states = states
            self._entity_cache_ts = time.perf_counter()

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        if not self.enabled:
            raise RuntimeError("Home Assistant bridge is not configured.")
        url = f"{self._base_url}{path}"
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self._token}"
        headers["Content-Type"] = "application/json"
        response = self._session.request(
            method=method,
            url=url,
            headers=headers,
            timeout=self._timeout,
            verify=self._verify_ssl,
            **kwargs,
        )
        response.raise_for_status()
        if response.content:
            try:
                return response.json()
            except ValueError:
                return response.text
        return {}

    def _log_event(self, intent: IntentMatch, success: bool, message: str) -> None:
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            status = "SUCCESS" if success else "ERROR"
            with self._log_path.open("a", encoding="utf-8") as handle:
                handle.write(
                    f"[{_utc_iso()}] {status} {intent.source} {intent.domain}.{intent.service} "
                    f"{intent.entity_id} -> {message}\n"
                )
        except Exception:  # pragma: no cover - logging must not fail flow
            logger.debug("Unable to write HA bridge log.", exc_info=True)


def create_blueprint(bridge: HABridge | None = None) -> Blueprint:
    """Create a Flask blueprint exposing HA bridge helpers.

    Requires ``HA_SECRET`` (env var) or ``bridge.secret`` to be non-empty.
    Raises ``RuntimeError`` if the secret is unset so the caller can log a
    warning and skip blueprint registration rather than mounting unauthenticated
    HA routes.
    """
    Blueprint, jsonify, request = _require_flask()
    bridge = bridge or HABridge()
    if not bridge.secret:
        raise RuntimeError(
            "HA blueprint requires HA_SECRET to be set; "
            "routes will not be mounted without a shared secret."
        )
    bp = Blueprint("ha_bridge", __name__)

    from rex.http_errors import (  # noqa: PLC0415
        BAD_REQUEST,
        FORBIDDEN,
        INTERNAL_ERROR,
        SERVICE_UNAVAILABLE,
        error_response,
    )

    @bp.before_request
    def _validate_secret() -> Any:
        if request.headers.get("HASS_SECRET") != bridge.secret:
            return error_response(FORBIDDEN, "Forbidden", 403)
        return None

    def _require_enabled():
        if not bridge.enabled:
            return error_response(SERVICE_UNAVAILABLE, "Home Assistant bridge disabled", 503)
        return None

    @bp.route("/ha/intents", methods=["GET"])
    def list_intents():
        if (resp := _require_enabled()) is not None:
            return resp
        return jsonify({"intents": HABridge.SUPPORTED_INTENTS})

    @bp.route("/ha/entities", methods=["GET"])
    def list_entities():
        if (resp := _require_enabled()) is not None:
            return resp
        try:
            entities = bridge.list_entities()
            return jsonify({"entities": entities})
        except Exception as exc:
            logger.warning("Failed to list Home Assistant entities: %s", exc)
            return error_response(INTERNAL_ERROR, str(exc), 500)

    @bp.route("/ha/script", methods=["POST"])
    def run_script():
        if (resp := _require_enabled()) is not None:
            return resp
        payload = request.get_json(silent=True) or {}
        script_id = payload.get("script")
        variables = payload.get("variables") or {}
        if not script_id:
            return error_response(BAD_REQUEST, "script field is required", 400)
        if not isinstance(script_id, str):
            return error_response(BAD_REQUEST, "script must be a string", 400)
        if len(script_id) > 256:
            return error_response(
                BAD_REQUEST, "script exceeds maximum length of 256 characters", 400
            )
        if not isinstance(variables, dict):
            return error_response(BAD_REQUEST, "variables must be an object", 400)
        try:
            result = bridge.call_script(script_id, variables)
            sanitized = dict(result)
            # The legacy shared-secret route is not a human-confirmation
            # channel. Never expose the action-bound token here, otherwise an
            # automated caller could immediately replay it and bypass the
            # intended user confirmation boundary.
            sanitized.pop("confirmation_token", None)
            status = sanitized.get("status")
            if status == HAOutcome.CONFIRMATION_REQUIRED.value:
                sanitized["detail"] = (
                    "This script requires confirmation through an authenticated Rex action flow."
                )
                return jsonify(sanitized), 409
            if status == HAOutcome.VERIFIED.value:
                return jsonify(sanitized), 200
            if status == HAOutcome.ATTEMPTED_UNVERIFIED.value:
                return jsonify(sanitized), 202
            if status == HAOutcome.DENIED.value:
                return jsonify(sanitized), 403
            return jsonify(sanitized), 503
        except Exception as exc:
            logger.warning("Failed to execute Home Assistant script: %s", exc)
            return error_response(INTERNAL_ERROR, str(exc), 500)

    return bp  # type: ignore[no-any-return]
