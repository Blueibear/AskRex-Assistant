"""Async assistant orchestration."""

from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from .actions.dispatcher import _UNDO_PATTERN, ActionResult
from .assistant_errors import ConfigurationError, IdentityRequiredError
from .calendar_service import get_calendar_service
from .config import Settings, settings
from .context.revisions import ContextCacheRequest
from .followup_engine import FollowupEngine
from .ha_bridge import HABridge
from .history_store import HistoryStore
from .identity import validate_user_id
from .latency import LatencyTrace
from .llm_client import LanguageModel
from .memory import trim_history
from .model_router import ModelRouteDecision, ModelRouter
from .plugins import PluginSpec
from .provider_reliability import classify_provider_failure
from .runtime.events import EventKind, EventObserver, TurnEvent, TurnEventStream
from .runtime.invocation import current_turn_invocation
from .runtime.turn import (
    AuthorizationSnapshotRef,
    ResponseMode,
    TurnContext,
    TurnScope,
)
from .runtime.turn_engine import TurnEngine
from .runtime_paths import household_data_path

logger = logging.getLogger(__name__)

# Deterministic fail-closed error for private operations without an identity
# (issue #303).  Must never include paths, credentials, or other user IDs.
_IDENTITY_REQUIRED_MESSAGE = (
    "No user identity is bound for this operation. "
    "Construct Assistant(user_id=...) or pass active_user_id with the request."
)

__all__ = [
    "Assistant",
    "ConversationTurn",
    "FollowupEngine",
    "_UNDO_PATTERN",
    "get_calendar_service",
]

_UNVERIFIED_ACTION_CLAIM_PATTERNS = (
    re.compile(
        r"\bi(?:'ve| have| just)?\s+"
        r"(?:added|put|saved|sent|scheduled|set|changed|updated|deleted|removed)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:done|okay|sure)[,.\s]+(?:i(?:'ve| have| just)\s+)?"
        r"(?:added|put|saved|sent|scheduled|set|changed|updated|deleted|removed)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:it|that|this)\s+(?:has\s+been|was)\s+"
        r"(?:added|put|saved|sent|scheduled|set|changed|updated|deleted|removed)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:i(?:'ve| have| just)|done|okay|sure)[^.!?\n]{0,80}"
        r"\bcreated\s+(?:an?\s+)?(?:event|task|reminder|calendar|file|note)\b",
        re.IGNORECASE,
    ),
)
_TOOL_REQUEST_PREFIX = "TOOL_REQUEST:"
_ACTION_CLAIM_STREAM_PREFIXES = (
    "i",
    "i'",
    "i have",
    "i've",
    "i just",
    "i added",
    "done",
    "okay",
    "sure",
    "it",
    "that",
    "this",
)


@dataclass
class ConversationTurn:
    speaker: str
    text: str


class Assistant:
    """Coordinate wake-word, transcription, language model, and plugins."""

    def __init__(
        self,
        *,
        history_limit: int | None = None,
        plugins: Iterable[PluginSpec] | None = None,
        settings_obj: Settings | None = None,
        transcripts_dir: str | Path | None = None,
        user_id: str | None = None,
        conversation_id: str | None = None,
    ) -> None:
        self._settings = settings_obj or settings
        self._llm = LanguageModel(config=self._settings)

        # Identity binding (issue #303): an omitted user_id leaves the
        # instance explicitly unbound.  A missing identity never becomes
        # "default" and never inherits settings.user_id — private requests
        # on an unbound instance must supply a validated active_user_id.
        # Explicit user_id="default" remains a valid, deliberate selection
        # of the profile named "default".
        self._user_id: str | None = validate_user_id(user_id) if user_id is not None else None
        self._conversation_id = conversation_id

        # Per-user in-memory history windows keyed by user id (issue #303).
        # ``self._history`` is a property that resolves to the current
        # ``self._user_id``'s list, so identified-speaker requests never see
        # another user's turns.
        self._histories: dict[str, list[ConversationTurn]] = {}
        self._history_limit = history_limit or self._settings.max_memory_items
        self._plugins = list(plugins or [])
        self._transcripts_dir = Path(transcripts_dir or self._settings.transcripts_dir)

        # Conversation history persistence.  Creating the store handle is a
        # neutral operation; per-user rows are only read once an identity is
        # bound.
        self._history_store: HistoryStore | None = None
        self._prune_timer: threading.Timer | None = None
        if getattr(self._settings, "persist_history", True):
            try:
                db_path = getattr(self._settings, "history_db_path", None)
                if db_path is None:
                    db_path = household_data_path("history.db")
                self._history_store = HistoryStore(db_path=db_path)
                if self._user_id is not None:
                    # Preload the last 50 turns into in-memory history
                    stored = self._history_store.load_history(self._user_id, limit=50, conversation_id=self._conversation_id)
                    self._history = [
                        ConversationTurn(speaker=row["role"], text=row["content"]) for row in stored
                    ]
                    # Run an initial prune at startup and schedule daily repeats
                    self._schedule_daily_prune()
            except Exception as exc:
                logger.warning("Failed to initialize HistoryStore: %s", exc)
                self._history_store = None

        # Follow-up engine for natural conversation cues (US-018: init extracted
        # to followup_engine).  Follow-up sessions are user-private state, so an
        # unbound assistant defers initialization until a request arrives with a
        # validated identity (see _ensure_followup_session).
        self._followup_lock = asyncio.Lock()
        self._followup_engine: Any = None
        self._pending_followups: dict[str, str | None] = {}
        self._followup_sessions: set[str] = set()
        self._followup_bootstrap_pending = self._user_id is None
        if self._user_id is not None:
            from .followup_engine import init_followup_engine as _init_fe

            self._followup_engine, _pending = _init_fe(self._settings, self._user_id)
            self._pending_followups[self._user_id] = _pending
            self._followup_sessions.add(self._user_id)

        # Model router for task-category-based model selection.
        # Pass routing config so the router can probe Ollama availability at init.
        _routing_cfg = getattr(self._settings, "model_routing", None)
        _ollama_url: str = getattr(
            self._settings, "ollama_base_url", ModelRouter._DEFAULT_OLLAMA_URL
        )
        self._router = ModelRouter(
            ollama_base_url=_ollama_url,
            routing_config=_routing_cfg,
        )

        # Canonical turn runtime. Surfaces remain adapters; generate_reply owns
        # the shared non-streaming orchestration path from US-095 onward.
        self._turn_engine = TurnEngine()
        self._turn_event_observer: EventObserver | None = None

        # Route all tool calls through OpenClaw ToolBridge (US-P7-008)
        from .openclaw.tool_bridge import ToolBridge

        self._tool_router_fn = ToolBridge().route_if_tool_request

        # Skill trainer, registry, and router (US-SK-003, US-SK-004)
        from .skills.registry import SkillRegistry
        from .skills.router import SkillRouter
        from .skills.trainer import SkillTrainer

        _skills_path = getattr(self._settings, "skills_path", None)
        self._skill_registry = SkillRegistry(skills_path=_skills_path)
        self._skill_trainer = SkillTrainer()
        self._skill_router = SkillRouter(self._skill_registry)

        # Auto tool dispatcher (US-TD-002). Dynamic OpenClaw discovery updates
        # the same canonical capability registry before the dispatcher is built.
        from .openclaw.capability_sync import initialize_openclaw_capability_sync
        from .tools.dispatcher import ToolDispatcher
        from .tools.registry import get_default_registry

        tool_registry = get_default_registry()
        initialize_openclaw_capability_sync(
            self._settings,
            registry=tool_registry.capability_registry,
            tool_registry=tool_registry,
        )
        self._tool_dispatcher = ToolDispatcher(tool_registry, config=self._settings)

        # First-class timers and alarms (US-120) use one process-wide,
        # deadline-driven runtime. Starting it with Assistant startup restores
        # persisted deadlines and reconciles overdue items after a restart.
        from .timekeeping.runtime import ensure_timekeeping_runtime

        self._timekeeping_runtime = ensure_timekeeping_runtime()

        # Shopping list voice handler (US-SL-002)
        from .shopping_list import ShoppingList
        from .shopping_list_handler import ShoppingListHandler

        _sl_path = getattr(self._settings, "shopping_list_path", None)
        _shopping_list = ShoppingList(path=_sl_path) if _sl_path else ShoppingList()
        self._shopping_list_handler: ShoppingListHandler | None = ShoppingListHandler(
            _shopping_list
        )

        # Music Assistant provider client remains available only behind the
        # canonical media adapter. Conversational routing never calls it directly.
        from .integrations.music_assistant import MusicAssistantClient

        _ma_url = getattr(self._settings, "music_assistant_url", None)
        _ma_token = getattr(self._settings, "music_assistant_token", None)
        _ma_client = MusicAssistantClient(base_url=_ma_url, token=_ma_token)
        self._music_assistant_client = _ma_client
        self._music_handler = None

        # Device state query handler (US-028)
        from .ha.state_handler import DeviceStateHandler

        _ha_url = getattr(self._settings, "ha_base_url", None)
        _ha_token = getattr(self._settings, "ha_token", None)
        self._device_state_handler: DeviceStateHandler | None = DeviceStateHandler(
            base_url=_ha_url,
            token=_ha_token,
        )

        # Response cache for repeated factual queries (US-LAT-004)
        from .response_cache import ResponseCache

        _cache_ttl = float(getattr(self._settings, "response_cache_ttl", 300.0))
        self._response_cache: ResponseCache | None = (
            ResponseCache(ttl=_cache_ttl) if _cache_ttl > 0 else None
        )

        # Only create HABridge if HA is configured
        self._ha_bridge: HABridge | None = None
        if self._settings.ha_base_url and self._settings.ha_token:
            try:
                self._ha_bridge = HABridge()
                logger.info("Home Assistant bridge initialized")
            except Exception as exc:
                logger.warning("Failed to initialize Home Assistant bridge: %s", exc)
                self._ha_bridge = None

        # Canonical media runtime (US-121). This initial snapshot is deliberately
        # conservative; Task 6 owns dynamic refresh and group-management surfaces.
        self._configure_media_service(music_assistant_client=_ma_client)

        # Tool result post-processing handler (US-013)
        from .tools.result_handler import ToolResultHandler

        self._result_handler = ToolResultHandler(
            tool_router_fn=self._tool_router_fn,
            ha_bridge=self._ha_bridge,
        )

        # Context builder: assembles system prompt, messages, and user facts (US-014)
        # while US-123 source policy supplies content-free cache invalidation.
        from .context.active import get_active_context_store
        from .context.builder import ContextBuilder
        from .context.source_policy import ContextSourcePolicyStore

        self._context_source_policy = ContextSourcePolicyStore()
        self._active_context_store = get_active_context_store()
        self._context_builder = ContextBuilder(
            settings=self._settings,
            history=[],
            user_id=self._user_id,
            followup_engine=self._followup_engine,
            # Live per-user view: resolves to the request user's history at
            # build time instead of a construction-time snapshot (#303).  The
            # optional argument keeps request identities out of shared state.
            history_provider=lambda user_id=None: self._history_for(
                user_id if user_id is not None else self._require_user_id()
            ),
            source_policy_store=self._context_source_policy,
            active_context_store=self._active_context_store,
        )

        # Intent router: handles direct-reply shortcuts without LLM (US-015)
        from .intent.router import IntentRouter

        self._intent_router = IntentRouter(tool_context_fn=self._build_tool_context)

        # Proactive suggestion engine (US-036)
        from .suggestions.engine import SuggestionEngine
        from .suggestions.pattern_detector import PatternEntry

        _dismissed_path = getattr(self._settings, "dismissed_suggestions_path", None)
        _automations_path = getattr(self._settings, "automations_path", None)
        self._suggestion_engine: SuggestionEngine | None = SuggestionEngine(
            dismissed_path=_dismissed_path,
            automations_path=_automations_path,
        )
        # In-memory command log for pattern detection (wall-clock timestamps),
        # keyed by user_id so one user's commands never seed another user's
        # suggestions (issue #303).
        self._pattern_entries: dict[str, list[PatternEntry]] = {}

        # Response builder: cache, TTS cleaning, suggestions, followups (US-017)
        from .response.builder import ResponseBuilder

        self._response_builder = ResponseBuilder(
            settings=self._settings,
            response_cache=self._response_cache,
            suggestion_engine=self._suggestion_engine,
            followup_engine=self._followup_engine,
        )

        # Action dispatcher: skill invocation, HA routing, tool dispatch, LLM, post-process (US-016)
        from .actions.dispatcher import ActionDispatcher

        self._action_dispatcher = ActionDispatcher(
            context_builder=self._context_builder,
            llm=self._llm,
            result_handler=self._result_handler,
            ha_bridge=self._ha_bridge,
            tool_dispatcher=self._tool_dispatcher,
            skill_trainer=self._skill_trainer,
            skill_registry=self._skill_registry,
            skill_router=self._skill_router,
            shopping_list_handler=self._shopping_list_handler,
            music_handler=None,
            device_state_handler=self._device_state_handler,
            suggestion_engine=self._suggestion_engine,
            pattern_entries=self._pattern_entries,
            active_context_store=self._active_context_store,
            build_tool_context_fn=self._build_tool_context,
            model_call_fn_builder=self._build_tool_model_call,
            run_plugins_fn=self._run_plugins,
        )

    def _configure_media_service(self, *, music_assistant_client: Any | None = None) -> None:
        """Configure canonical media tools with command-time discovery refresh."""
        from .audio.speaker_discovery import get_speaker_discovery
        from .identity import list_known_users
        from .media.adapters import (
            HomeAssistantMediaAdapter,
            MusicAssistantAdapter,
            SmartSpeakerAdapter,
        )
        from .media.groups import SpeakerGroupStore
        from .media.models import AudioTarget, TargetKind, TargetProviderAdapter
        from .media.registry import AudioTargetRegistry
        from .media.service import MediaService
        from .media.tools import set_media_service
        from .permissions import get_permissions

        adapters: dict[str, TargetProviderAdapter] = {}

        class CachedSpeakerDiscovery:
            def discover_now(self):
                return get_speaker_discovery().get_cached_speakers()

        smart_adapter = SmartSpeakerAdapter(CachedSpeakerDiscovery())
        adapters[smart_adapter.provider] = smart_adapter

        ha_bridge = getattr(self, "_ha_bridge", None)
        if ha_bridge is not None:
            ha_adapter = HomeAssistantMediaAdapter(ha_bridge)
            adapters[ha_adapter.provider] = ha_adapter
        if music_assistant_client is not None:
            ma_adapter = MusicAssistantAdapter(music_assistant_client)
            adapters[ma_adapter.provider] = ma_adapter

        def normalize(value: str) -> str:
            return " ".join(value.casefold().replace("_", " ").split())

        def build_registry() -> AudioTargetRegistry:
            targets: list[AudioTarget] = []
            for adapter in adapters.values():
                try:
                    targets.extend(adapter.discover_targets())
                except Exception as exc:
                    logger.warning("Failed to refresh %s media targets: %s", adapter.provider, exc)

            base_by_id = {target.id: target for target in targets}
            group_store = SpeakerGroupStore(
                target_exists=base_by_id.__contains__,
                target_capabilities=lambda target_id: base_by_id[target_id].capabilities,
            )
            try:
                groups = group_store.list()
            except (KeyError, ValueError) as exc:
                logger.warning("Failed to load canonical speaker groups: %s", exc)
                groups = ()
            for group in groups:
                members = tuple(base_by_id[member_id] for member_id in group.member_ids)
                online = bool(members) and all(member.online for member in members)
                targets.append(
                    AudioTarget(
                        id=group.id,
                        native_id=group.id,
                        provider="group",
                        kind=TargetKind.GROUP,
                        display_name=group.name,
                        aliases=(),
                        room=None,
                        capabilities=frozenset(),
                        online=online,
                        health="configured" if online else "member_unavailable",
                    )
                )

            known_users: set[str] = set()
            bound_user = getattr(self, "_user_id", None)
            if isinstance(bound_user, str):
                known_users.add(bound_user)
            try:
                known_users.update(
                    str(user["id"])
                    for user in list_known_users()
                    if isinstance(user, dict) and isinstance(user.get("id"), str)
                )
            except Exception as exc:
                logger.warning("Failed to enumerate users for media authorization: %s", exc)

            base_ids = frozenset(base_by_id)
            group_members = {group.id: frozenset(group.member_ids) for group in groups}
            authorized: dict[str, frozenset[str]] = {}
            for user_id in known_users:
                try:
                    permissions = set(get_permissions(user_id))
                except Exception as exc:
                    logger.warning("Failed to resolve media permissions for %s: %s", user_id, exc)
                    permissions = set()
                allowed_base = (
                    base_ids if permissions.intersection({"ha_control", "admin"}) else frozenset()
                )
                allowed_groups = {
                    group_id
                    for group_id, member_ids in group_members.items()
                    if member_ids and member_ids.issubset(allowed_base)
                }
                authorized[user_id] = frozenset((*allowed_base, *allowed_groups))

            origin_targets: dict[str, str] = {}
            room_map = getattr(self._settings, "device_room_map", {}) or {}
            if isinstance(room_map, dict):
                for device_id, room_name in room_map.items():
                    if not isinstance(device_id, str) or not device_id.strip():
                        continue
                    if not isinstance(room_name, str) or not room_name.strip():
                        continue
                    wanted = normalize(room_name)
                    matches = []
                    for target in base_by_id.values():
                        labels = {
                            normalize(target.display_name),
                            normalize(target.native_id.rsplit(".", 1)[-1]),
                        }
                        if target.room:
                            labels.add(normalize(target.room))
                        if wanted in labels:
                            matches.append(target)
                    if len(matches) == 1:
                        origin_targets[device_id] = matches[0].id

            registry = AudioTargetRegistry(
                targets,
                authorized_target_ids=authorized,
                origin_device_targets=origin_targets,
            )
            self._media_registry = registry
            self._speaker_group_store = group_store
            return registry

        registry = build_registry()
        service = MediaService(
            registry=registry,
            adapters=adapters,
            registry_refresher=build_registry,
        )
        self._media_service = service
        set_media_service(service)

    def _schedule_daily_prune(self) -> None:
        """Prune old history turns and schedule the next prune in 24 hours.

        Runs once immediately at startup, then repeats daily via a daemon thread.
        Safe to call if ``_history_store`` is None (no-op).
        """
        if self._history_store is None or self._user_id is None:
            return
        retention_days = int(getattr(self._settings, "history_retention_days", 30))
        try:
            deleted = self._history_store.prune(self._user_id, keep_days=retention_days)
            if deleted:
                logger.debug(
                    "Pruned %d old history turns for user %s (retention=%d days)",
                    deleted,
                    self._user_id,
                    retention_days,
                )
        except Exception as exc:
            logger.warning("History prune failed: %s", exc)
        # Schedule next prune in 24 hours (daemon so it doesn't block process exit)
        timer = threading.Timer(86400, self._schedule_daily_prune)
        timer.daemon = True
        timer.start()
        self._prune_timer = timer

    @property
    def user_id(self) -> str | None:
        """Get the bound user ID for this assistant session (None when unbound)."""
        return self._user_id

    # ------------------------------------------------------------------
    # Identity resolution (issue #303)
    # ------------------------------------------------------------------

    def _require_user_id(self) -> str:
        """Return the bound user ID, failing closed when the instance is unbound."""
        user_id: str | None = getattr(self, "_user_id", None)
        if user_id is None:
            raise IdentityRequiredError(_IDENTITY_REQUIRED_MESSAGE)
        return user_id

    def _resolve_request_user_id(self, active_user_id: str | None) -> str:
        """Resolve the validated identity a request operates as.

        The effective identity comes only from an explicit validated request
        identity or the explicit validated constructor identity.  A missing
        identity fails closed before any private state is touched; it never
        becomes ``"default"``.

        Raises:
            ValueError: If *active_user_id* fails canonical validation.
            IdentityRequiredError: If no identity is available at all.
        """
        if active_user_id is not None:
            return validate_user_id(active_user_id)
        return self._require_user_id()

    # ------------------------------------------------------------------
    # Per-user in-memory history (issue #303)
    # ------------------------------------------------------------------

    def _history_key(self, user_id: str) -> str:
        return user_id if self._conversation_id is None else f"{user_id}\0{self._conversation_id}"

    def _load_persisted_history(self, user_id: str) -> list[ConversationTurn]:
        """Load the most recent persisted turns for *user_id* (empty when no store)."""
        store = getattr(self, "_history_store", None)
        if store is None:
            return []
        try:
            stored = store.load_history(user_id, limit=50, conversation_id=self._conversation_id)
            return [ConversationTurn(speaker=row["role"], text=row["content"]) for row in stored]
        except Exception as exc:
            logger.warning("Failed to load history for user %s: %s", user_id, exc)
            return []

    def _history_for(self, user_id: str) -> list[ConversationTurn]:
        """Return the in-memory history window for *user_id*, loading it lazily."""
        histories: dict[str, list[ConversationTurn]] = self.__dict__.setdefault("_histories", {})
        key = self._history_key(user_id)
        hist = histories.get(key)
        if hist is None:
            hist = self._load_persisted_history(user_id)
            histories[key] = hist
        return hist

    @property
    def _history(self) -> list[ConversationTurn]:
        """History window for the bound ``self._user_id`` (fails closed unbound)."""
        return self._history_for(self._require_user_id())

    @_history.setter
    def _history(self, value: list[ConversationTurn]) -> None:
        user_id = self._require_user_id()
        self.__dict__.setdefault("_histories", {})[self._history_key(user_id)] = list(value)

    # ------------------------------------------------------------------
    # Per-user pending follow-ups (issue #303)
    # ------------------------------------------------------------------

    def _pending_followups_map(self) -> dict[str, str | None]:
        """Return the per-user pending-followup map (``__new__``-fixture safe)."""
        pending: dict[str, str | None] = self.__dict__.setdefault("_pending_followups", {})
        return pending

    @property
    def _pending_followup(self) -> str | None:
        """Pending follow-up cue for the bound user (None when unbound)."""
        user_id = getattr(self, "_user_id", None)
        if user_id is None:
            return None
        return self._pending_followups_map().get(user_id)

    @_pending_followup.setter
    def _pending_followup(self, value: str | None) -> None:
        user_id = getattr(self, "_user_id", None)
        if user_id is None:
            if value is None:
                return
            raise IdentityRequiredError(_IDENTITY_REQUIRED_MESSAGE)
        self._pending_followups_map()[user_id] = value

    def _ensure_followup_session(self, user_id: str) -> None:
        """Initialize or attach *user_id*'s follow-up session exactly once.

        For a bound constructor identity this happened in ``__init__``.  For
        additional request identities (and the first identity on an unbound
        instance) the user-private cue state is only touched here, after the
        identity has been validated.
        """
        sessions: set[str] = self.__dict__.setdefault("_followup_sessions", set())
        if user_id in sessions:
            return
        sessions.add(user_id)
        engine = getattr(self, "_followup_engine", None)
        if engine is None:
            # Only genuinely unbound real constructions defer engine creation;
            # test shells built via __new__ never bootstrap implicitly.
            if not getattr(self, "_followup_bootstrap_pending", False):
                return
            from .followup_engine import init_followup_engine as _init_fe

            self._followup_bootstrap_pending = False
            engine, pending = _init_fe(self._settings, user_id)
            self._followup_engine = engine
            self._pending_followups_map()[user_id] = pending
            return
        try:
            if hasattr(engine, "start_session"):
                engine.start_session(user_id)
            pending = None
            if hasattr(engine, "get_followup_prompt"):
                pending = engine.get_followup_prompt(user_id)
            self._pending_followups_map()[user_id] = pending
        except Exception as exc:
            logger.debug("Follow-up session init failed for user: %s", exc)

    @property
    def has_pending_followup(self) -> bool:
        """Check if there's a pending follow-up for this session."""
        return self._pending_followup is not None

    @property
    def pending_followup_prompt(self) -> str | None:
        """Get the pending follow-up prompt if any."""
        return self._pending_followup

    def _get_followup_lock(self) -> asyncio.Lock:
        """Return the follow-up lock, creating it lazily when needed."""
        lock = getattr(self, "_followup_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            self._followup_lock = lock
        return lock

    async def _prepare_model_input(
        self,
        transcript: str,
        *,
        voice_mode: bool = False,
        active_user_id: str | None = None,
        tool_context: str | None = None,
    ) -> tuple[str, list[dict[str, str]]]:
        if not transcript.strip():
            raise ValueError("Transcript must not be empty")

        # Fail closed before any per-user context or cue state is touched.
        user_id = self._resolve_request_user_id(active_user_id)

        ctx = self._context_builder.build(
            transcript,
            voice_mode=voice_mode,
            active_user_id=active_user_id,
            tool_context=tool_context,
        )
        prompt = ctx.prompt
        messages = ctx.messages

        async with self._get_followup_lock():
            pending = self._pending_followups_map().get(user_id)
            if pending:
                followup_text = (
                    f'You may want to ask the user: "{pending}" '
                    "as a natural conversation starter."
                )
                followup_hint = f"\n[Note: {followup_text}]"
                prompt = prompt + followup_hint
                messages.insert(-1, {"role": "system", "content": followup_text})
                self._pending_followups_map()[user_id] = None
                engine = self._followup_engine
                if engine and hasattr(engine, "mark_current_cue_asked"):
                    try:
                        engine.mark_current_cue_asked(user_id)
                    except Exception as exc:
                        logger.debug("mark_current_cue_asked failed: %s", exc)

        return prompt, messages

    async def _prepare_prompt(
        self,
        transcript: str,
        *,
        voice_mode: bool = False,
        active_user_id: str | None = None,
        tool_context: str | None = None,
    ) -> str:
        prompt, _messages = await self._prepare_model_input(
            transcript,
            voice_mode=voice_mode,
            active_user_id=active_user_id,
            tool_context=tool_context,
        )
        return prompt

    def _latency_provider_model(self) -> tuple[str, str]:
        """Return latency-safe provider/model identifiers without deprecated config access."""
        settings_obj = getattr(self, "_settings", None)
        llm_config = getattr(settings_obj, "llm", None)
        provider = getattr(llm_config, "llm_provider", None)
        if provider is None:
            provider = getattr(settings_obj, "llm_provider", "unknown")
        llm_obj = getattr(self, "_llm", None)
        model = getattr(llm_obj, "model_name", None) or getattr(llm_config, "model_name", None)
        if model is None:
            model = getattr(settings_obj, "llm_model", "unknown")
        return str(provider or "unknown"), str(model or "unknown")

    def _provider_attempt_latency_ms(self, started_ns: int) -> float:
        return max(0.0, (time.perf_counter_ns() - started_ns) / 1_000_000)

    def _record_provider_success(self, started_ns: int) -> None:
        router = getattr(self, "_router", None)
        if not isinstance(router, ModelRouter):
            return
        provider, _model = self._latency_provider_model()
        router.record_provider_success(
            provider, latency_ms=self._provider_attempt_latency_ms(started_ns)
        )

    def _record_provider_failure(self, exc: BaseException, started_ns: int) -> None:
        router = getattr(self, "_router", None)
        if not isinstance(router, ModelRouter):
            return
        provider, _model = self._latency_provider_model()
        router.record_provider_failure(
            provider,
            classify_provider_failure(exc),
            latency_ms=self._provider_attempt_latency_ms(started_ns),
        )

    def _generate_model_reply(self, prompt: str, messages: list[dict[str, str]]) -> str:
        started_ns = time.perf_counter_ns()
        try:
            try:
                reply = self._llm.generate(messages=messages)
            except TypeError:
                reply = self._llm.generate(prompt)
        except Exception as exc:
            self._record_provider_failure(exc, started_ns)
            raise
        self._record_provider_success(started_ns)
        return reply

    def _track_provider_stream(self, stream: Iterable[str], started_ns: int) -> Iterable[str]:
        try:
            yield from stream
        except Exception as exc:
            self._record_provider_failure(exc, started_ns)
            raise
        self._record_provider_success(started_ns)

    def _stream_model_reply(self, prompt: str, messages: list[dict[str, str]]) -> Iterable[str]:
        started_ns = time.perf_counter_ns()
        try:
            try:
                stream = self._llm.stream(messages=messages)
            except TypeError:
                stream = self._llm.stream(prompt)
        except Exception as exc:
            self._record_provider_failure(exc, started_ns)
            raise
        return self._track_provider_stream(stream, started_ns)

    async def _post_process_completion(
        self, transcript: str, completion: str, *, user_id: str | None = None
    ) -> str:
        from rex.mobile_api.action_context import (  # noqa: PLC0415
            mobile_action_context_active,
        )

        plugin_enrichments = (
            [] if mobile_action_context_active() else await self._run_plugins(transcript)
        )
        return await self._result_handler.process(
            transcript,
            completion,
            tool_context=self._build_tool_context(user_id),
            model_call_fn=self._build_tool_model_call(transcript, user_id=user_id),
            plugin_enrichments=plugin_enrichments,
        )

    def _record_completion(
        self, transcript: str, completion: str, *, user_id: str | None = None
    ) -> None:
        """Persist and remember a completed turn under its owning user.

        *user_id* overrides ``self._user_id`` so early-return paths (intent
        shortcuts, cache hits) and post-``_end_request`` recording attribute
        the turn to the identified speaker rather than the session default
        (issue #303).
        """
        uid = user_id if user_id is not None else getattr(self, "_user_id", None)
        if uid is None:
            raise IdentityRequiredError(_IDENTITY_REQUIRED_MESSAGE)
        # Never write history, transcripts, or in-memory windows under an
        # unvalidated key (issue #303).
        uid = validate_user_id(uid)
        now = datetime.now(UTC)
        history_store = getattr(self, "_history_store", None)
        if history_store is not None:
            try:
                history_store.save_turn(uid, "user", transcript, now, conversation_id=self._conversation_id)
                history_store.save_turn(uid, "assistant", completion, now, conversation_id=self._conversation_id)
            except Exception as exc:
                logger.warning("Failed to persist conversation turn: %s", exc)

        hist = self._history_for(uid)
        hist.append(ConversationTurn("user", transcript))
        hist.append(ConversationTurn("assistant", completion))
        self._histories[self._history_key(uid)] = [
            ConversationTurn(**item) if isinstance(item, dict) else item
            for item in trim_history(hist, limit=self._history_limit)  # type: ignore[arg-type]
        ]

        self._log_turn(transcript, completion, user_id=uid)

    def _looks_like_unverified_action_claim(self, completion: str) -> bool:
        return any(pattern.search(completion) for pattern in _UNVERIFIED_ACTION_CLAIM_PATTERNS)

    def _stream_tool_prefix_state(self, text: str) -> str:
        stripped = text.lstrip().upper()
        if not stripped:
            return "pending"
        prefix = _TOOL_REQUEST_PREFIX
        if prefix.startswith(stripped):
            return "pending"
        if stripped.startswith(prefix):
            return "pending"

        lowered = text.lstrip().lower()
        if self._looks_like_unverified_action_claim(text):
            return "pending"
        if len(lowered) < 80 and any(
            prefix.startswith(lowered) or lowered.startswith(prefix)
            for prefix in _ACTION_CLAIM_STREAM_PREFIXES
        ):
            return "pending"
        return "text"

    async def _stream_home_assistant_completion(
        self,
        transcript: str,
        *,
        loop: asyncio.AbstractEventLoop,
        latency_trace: LatencyTrace,
    ) -> str | None:
        from rex.mobile_api.action_context import (  # noqa: PLC0415
            mobile_action_context_active,
            mobile_scope_granted,
            run_in_executor_with_mobile_context,
        )

        if not (
            self._ha_bridge
            and self._ha_bridge.enabled
            and not mobile_action_context_active()
            and mobile_scope_granted("home.control")
        ):
            return None

        latency_trace.start("tool")
        try:
            return cast(
                str | None,
                await run_in_executor_with_mobile_context(
                    loop, self._ha_bridge.process_transcript, transcript
                ),
            )
        finally:
            latency_trace.end("tool")

    def _validate_model_response(
        self,
        result: ActionResult,
        *,
        turn_events: TurnEventStream,
        route: str,
    ) -> str | None:
        """Return a failure reason when a model-backed response is clearly invalid."""
        if not result.model_generated:
            return None

        from rex.response.validation import (  # noqa: PLC0415
            MODEL_FAILURE_MESSAGE,
            validate_model_output,
        )

        response = result.response
        validation = validate_model_output(response)
        if validation.valid:
            turn_events.emit(
                EventKind.RESPONSE_PROGRESS,
                {"stage": "output_validation", "status": "passed"},
            )
            return None

        provider, model = self._latency_provider_model()
        reason = validation.reason or "invalid_output"
        logger.warning(
            "model_output_validation_failed provider=%s model=%s route=%s output_length=%d reason=%s",
            provider,
            model,
            route,
            len(response),
            reason,
        )
        result.response = MODEL_FAILURE_MESSAGE
        turn_events.emit(
            EventKind.RESPONSE_PROGRESS,
            {
                "stage": "output_validation",
                "status": "model_failure",
                "reason": reason,
            },
        )
        return reason

    async def _deliver_safe_response(
        self,
        text: str,
        *,
        turn_events: TurnEventStream,
        response_sink: Callable[[str], Awaitable[None]] | None,
        latency_trace: LatencyTrace,
        stream_started_ns: int | None,
    ) -> None:
        """Deliver only post-validation text as ordered sentence chunks."""
        if response_sink is None:
            return
        from rex.runtime.cancellation import (  # noqa: PLC0415
            await_with_cancellation,
            current_turn_cancellation,
        )
        from rex.voice.transcripts import _split_into_sentences  # noqa: PLC0415

        cancellation = current_turn_cancellation()
        chunks = _split_into_sentences(text)
        if not chunks and text.strip():
            chunks = [text.strip()]
        for index, chunk in enumerate(chunks):
            if cancellation is not None:
                cancellation.raise_if_cancelled()
            if index == 0 and stream_started_ns is not None:
                latency_trace.add_duration_ms(
                    "first_token",
                    (time.perf_counter_ns() - stream_started_ns) / 1_000_000,
                )
            turn_events.emit(
                EventKind.RESPONSE_PROGRESS,
                {"stage": "delta", "index": index, "kind": "sentence"},
            )
            await await_with_cancellation(response_sink(chunk))

    def _compose_event_observer(self, explicit: EventObserver | None) -> EventObserver | None:
        """Combine per-call and legacy observers without shared request state."""
        legacy = getattr(self, "_turn_event_observer", None)
        if explicit is None:
            return legacy
        if legacy is None or explicit == legacy:
            return explicit

        def observe(event) -> None:  # noqa: ANN001
            legacy(event)
            explicit(event)

        return observe

    async def stream_reply(
        self,
        transcript: str,
        *,
        voice_mode: bool = False,
        active_user_id: str | None = None,
        event_observer: EventObserver | None = None,
    ) -> AsyncIterator[str]:
        """Stream verified response sentences from the canonical TurnEngine path."""
        effective_user_id = self._resolve_request_user_id(active_user_id)
        turn_context = self._build_turn_context(effective_user_id, voice_mode=voice_mode)
        latency_provider, latency_model = self._latency_provider_model()
        latency_trace = LatencyTrace(
            channel="voice" if voice_mode else "chat",
            provider=latency_provider,
            model=latency_model,
            settings_id="voice_stream" if voice_mode else "text_stream",
        )
        stream_started_ns = time.perf_counter_ns()
        queue: asyncio.Queue[object] = asyncio.Queue()
        sentinel = object()
        legacy_observer = getattr(self, "_turn_event_observer", None)
        queued_observer = (
            event_observer
            if event_observer is not None and event_observer != legacy_observer
            else None
        )

        def stream_observer(event: TurnEvent) -> None:
            if legacy_observer is not None:
                legacy_observer(event)
            if queued_observer is not None:
                queue.put_nowait(("event", event))

        observer = (
            stream_observer if legacy_observer is not None or queued_observer is not None else None
        )

        async def response_sink(chunk: str) -> None:
            await queue.put(("chunk", chunk))

        async def run_turn() -> None:
            try:
                await self._get_or_create_turn_engine().execute_async(
                    turn_context,
                    lambda turn_events: self._run_reply_turn(
                        turn_events,
                        turn_context=turn_context,
                        transcript=transcript,
                        voice_mode=voice_mode,
                        active_user_id=active_user_id,
                        effective_user_id=effective_user_id,
                        latency_trace=latency_trace,
                        latency_event="chat_stream_latency",
                        response_sink=response_sink,
                        stream_started_ns=stream_started_ns,
                    ),
                    on_event=observer,
                )
            except BaseException as exc:
                await queue.put(("error", exc))
            finally:
                await queue.put(sentinel)

        task = asyncio.create_task(run_turn())
        try:
            while True:
                item = await queue.get()
                if item is sentinel:
                    break
                kind, payload = cast(tuple[str, object], item)
                if kind == "event":
                    if queued_observer is not None:
                        queued_observer(cast(TurnEvent, payload))
                    continue
                if kind == "error":
                    raise cast(BaseException, payload)
                yield str(payload)
        finally:
            await task

    def _get_or_create_turn_engine(self) -> TurnEngine:
        """Return the canonical turn engine, creating it for legacy test shells."""
        engine = getattr(self, "_turn_engine", None)
        if engine is None:
            engine = TurnEngine()
            self._turn_engine = engine
        return engine

    def _build_turn_context(self, effective_user_id: str, *, voice_mode: bool) -> TurnContext:
        """Create a turn context without widening existing runtime authority."""
        invocation = current_turn_invocation()
        return TurnContext.create(
            user_id=effective_user_id,
            scope=TurnScope.USER,
            source=invocation.source,
            device_id=invocation.device_id,
            response_mode=ResponseMode.VOICE if voice_mode else ResponseMode.SCREEN,
            authorization=AuthorizationSnapshotRef(
                policy_ref="rex-policy:existing-runtime",
                permission_ref=f"rex-permissions:validated-user:{effective_user_id}",
            ),
        )

    def _prepare_model_route(
        self,
        transcript: str,
        *,
        latency_trace: LatencyTrace,
        turn_events: TurnEventStream,
    ) -> tuple[Callable[[], None] | None, ModelRouteDecision | None]:
        """Apply and publish the request-scoped model route."""
        route_cleanup, route_decision = self._begin_request(transcript)
        if route_decision is not None and route_decision.model:
            latency_trace.model = route_decision.model
        else:
            latency_trace.model = str(getattr(self._llm, "model_name", None) or latency_trace.model)
        latency_trace.end("routing")
        route_details: dict[str, object] = {
            "stage": "model_router",
            "model": latency_trace.model,
        }
        if route_decision is not None:
            route_details.update(route_decision.to_metadata())
        turn_events.emit(EventKind.ROUTE_PROGRESS, route_details)
        return route_cleanup, route_decision

    def _context_cache_request(self, turn_context: TurnContext) -> ContextCacheRequest:
        """Build cache identity from the active request-scoped provider/model."""
        provider = getattr(self._llm, "provider", None)
        if not isinstance(provider, str) or not provider.strip():
            provider = str(getattr(self._settings, "llm_provider", None) or "unknown")
        active_model = getattr(self._llm, "active_model_name", None)
        model_name = active_model() if callable(active_model) else None
        if not isinstance(model_name, str) or not model_name.strip():
            model_name = getattr(self._llm, "model_name", None)
        if not isinstance(model_name, str) or not model_name.strip():
            model_name = str(getattr(self._settings, "llm_model", None) or "unknown")
        return ContextCacheRequest(
            user_id=turn_context.user_id if turn_context.scope is TurnScope.USER else None,
            scope=turn_context.scope,
            authorization=turn_context.authorization,
            model_provider=provider,
            model_name=model_name,
        )

    async def _run_reply_turn(
        self,
        turn_events: TurnEventStream,
        *,
        turn_context: TurnContext,
        transcript: str,
        voice_mode: bool,
        active_user_id: str | None,
        effective_user_id: str,
        latency_trace: LatencyTrace,
        latency_event: str,
        response_sink: Callable[[str], Awaitable[None]] | None = None,
        stream_started_ns: int | None = None,
    ) -> str:
        """Run the shared verified reply pipeline for text and streaming delivery."""
        loop = asyncio.get_running_loop()
        check_cancelled = turn_context.cancellation.raise_if_cancelled
        check_cancelled()
        self._ensure_followup_session(effective_user_id)
        latency_trace.start("routing")

        intent = self._get_or_create_intent_router().route(
            transcript,
            settings=self._settings,
            suggestion_engine=getattr(self, "_suggestion_engine", None),
            user_id=effective_user_id,
        )
        check_cancelled()
        turn_events.emit(
            EventKind.ROUTE_PROGRESS,
            {
                "stage": "intent",
                "handled": bool(intent.handled),
                "intent_type": intent.intent_type or "unknown",
            },
        )
        if intent.handled and not (intent.intent_type == "greeting" and active_user_id is not None):
            latency_trace.end("routing")
            latency_trace.start("completion")
            completion = cast(str, intent.response)
            check_cancelled()
            self._record_completion(transcript, completion, user_id=effective_user_id)
            turn_events.emit(
                EventKind.RESPONSE_PROGRESS,
                {"stage": "completed", "source": "intent", "history_recorded": True},
            )
            await self._deliver_safe_response(
                completion,
                turn_events=turn_events,
                response_sink=response_sink,
                latency_trace=latency_trace,
                stream_started_ns=stream_started_ns,
            )
            latency_trace.end("completion")
            latency_trace.finish()
            latency_trace.log_summary(logger, event=latency_event)
            return completion

        check_cancelled()
        cached = None
        if self._should_check_response_cache(intent):
            cached = self._get_or_create_response_builder().check_cache(
                transcript, user_id=effective_user_id
            )
        check_cancelled()
        turn_events.emit(
            EventKind.ROUTE_PROGRESS,
            {"stage": "cache", "cache_hit": cached is not None},
        )
        if cached is not None:
            latency_trace.end("routing")
            latency_trace.start("completion")
            completion = cast(str, cached)
            check_cancelled()
            self._record_completion(transcript, completion, user_id=effective_user_id)
            turn_events.emit(
                EventKind.RESPONSE_PROGRESS,
                {"stage": "completed", "source": "cache", "history_recorded": True},
            )
            await self._deliver_safe_response(
                completion,
                turn_events=turn_events,
                response_sink=response_sink,
                latency_trace=latency_trace,
                stream_started_ns=stream_started_ns,
            )
            latency_trace.end("completion")
            latency_trace.finish()
            latency_trace.log_summary(logger, event=latency_event)
            return completion

        check_cancelled()
        route_cleanup, _route_decision = self._prepare_model_route(
            transcript,
            latency_trace=latency_trace,
            turn_events=turn_events,
        )
        model_failure_reason: str | None = None
        try:
            check_cancelled()
            cache_request = self._context_cache_request(turn_context)
            context = self._get_or_create_context_builder().build(
                transcript,
                voice_mode=voice_mode,
                active_user_id=active_user_id,
                cache_request=cache_request,
            )
            check_cancelled()
            turn_events.emit(
                EventKind.CONTEXT_PROGRESS,
                {"stage": "built", "scope": turn_context.scope.value},
            )
            result = await self._get_or_create_action_dispatcher().dispatch(
                intent,
                context,
                transcript,
                voice_mode=voice_mode,
                active_user_id=active_user_id,
                user_id=effective_user_id,
                loop=loop,
                latency_trace=latency_trace,
                turn_events=turn_events,
            )
            check_cancelled()
            model_failure_reason = self._validate_model_response(
                result,
                turn_events=turn_events,
                route=intent.intent_type or "action_dispatch",
            )
            latency_trace.start("completion")
            if model_failure_reason is None:
                self._prepare_contextual_suggestion(
                    effective_user_id,
                    response_text=str(result.response),
                )
                final = self._get_or_create_response_builder().build(
                    result, context, transcript=transcript, user_id=effective_user_id
                )
                check_cancelled()
                completion = final.text
                turn_events.emit(
                    EventKind.RESPONSE_PROGRESS,
                    {"stage": "response_builder", "status": "completed"},
                )
            else:
                completion = str(result.response)
        except Exception:
            latency_trace.finish()
            latency_trace.log_summary(logger, event=latency_event)
            raise
        finally:
            self._end_request(route_cleanup)

        check_cancelled()
        self._record_completion(transcript, completion, user_id=effective_user_id)
        check_cancelled()
        turn_events.emit(
            EventKind.RESPONSE_PROGRESS,
            {"stage": "history", "history_recorded": True},
        )
        await self._deliver_safe_response(
            completion,
            turn_events=turn_events,
            response_sink=response_sink,
            latency_trace=latency_trace,
            stream_started_ns=stream_started_ns,
        )
        if model_failure_reason is not None:
            turn_events.finish(
                EventKind.FAILED,
                {
                    "failure_kind": "model_failure",
                    "reason": model_failure_reason,
                },
            )
        latency_trace.end("completion")
        latency_trace.finish()
        latency_trace.log_summary(logger, event=latency_event)
        return completion

    @staticmethod
    def _should_check_response_cache(intent: Any) -> bool:
        """Current-info replies must be freshly verified instead of served from cache."""
        return getattr(intent, "intent_type", None) != "current_info"

    async def generate_reply(
        self,
        transcript: str,
        *,
        voice_mode: bool = False,
        active_user_id: str | None = None,
        event_observer: EventObserver | None = None,
    ) -> str:
        """Generate one verified reply through the canonical TurnEngine pipeline."""
        effective_user_id = self._resolve_request_user_id(active_user_id)
        turn_context = self._build_turn_context(effective_user_id, voice_mode=voice_mode)
        observer = self._compose_event_observer(event_observer)
        latency_provider, latency_model = self._latency_provider_model()
        latency_trace = LatencyTrace(
            channel="voice" if voice_mode else "chat",
            provider=latency_provider,
            model=latency_model,
            settings_id="voice" if voice_mode else "text",
        )
        return await self._get_or_create_turn_engine().execute_async(
            turn_context,
            lambda turn_events: self._run_reply_turn(
                turn_events,
                turn_context=turn_context,
                transcript=transcript,
                voice_mode=voice_mode,
                active_user_id=active_user_id,
                effective_user_id=effective_user_id,
                latency_trace=latency_trace,
                latency_event="chat_latency",
            ),
            on_event=observer,
        )

    def _begin_request(
        self, transcript: str
    ) -> tuple[Callable[[], None] | None, ModelRouteDecision | None]:
        """Apply one request-scoped model route and return its cleanup callback."""
        router = getattr(self, "_router", None)
        base_model: str | None = getattr(self._llm, "model_name", None)
        decision: ModelRouteDecision | None = None
        cleanup: Callable[[], None] | None = None
        if router is None:
            return cleanup, decision

        routing_cfg = getattr(self._settings, "model_routing", None)
        if isinstance(router, ModelRouter):
            decision = router.decide(
                transcript,
                routing_config=routing_cfg,
                current_model=base_model or "",
                routing_mode=str(
                    getattr(self._settings, "llm_routing_mode", "local_preferred")
                    or "local_preferred"
                ),
                provider=str(
                    getattr(self._settings, "llm_provider", "")
                    or getattr(self._llm, "provider", None)
                    or ""
                ),
            )
            if decision.fallback_reason == "local_only_provider_conflict":
                raise ConfigurationError(
                    "llm_routing_mode='local_only' requires a local LLM provider."
                )
            if decision.fallback_reason == "cloud_provider_unconfigured":
                raise ConfigurationError(
                    "llm_routing_mode='cloud_only' requires an explicitly configured cloud provider."
                )
            resolved = decision.model
            category = decision.category
        else:
            category = router.classify(transcript)
            resolved = router.resolve_model(category, routing_cfg)

        if resolved and resolved != base_model:
            logger.debug("model_router: classified as %s, using %s", category, resolved)
            supports_scoped_model = callable(
                getattr(type(self._llm), "set_request_model", None)
            ) and callable(getattr(type(self._llm), "reset_request_model", None))
            if supports_scoped_model:
                set_request_model = self._llm.set_request_model
                reset_request_model = self._llm.reset_request_model
                token = set_request_model(resolved)

                def _reset_scoped_model() -> None:
                    reset_request_model(token)

                cleanup = _reset_scoped_model
            elif hasattr(self._llm, "model_name"):
                self._llm.model_name = resolved

                def _restore_legacy_model() -> None:
                    self._llm.model_name = base_model

                cleanup = _restore_legacy_model
        else:
            logger.debug(
                "model_router: classified as %s, using %s",
                category,
                base_model or "default",
            )
        return cleanup, decision

    @staticmethod
    def _end_request(cleanup: Callable[[], None] | None) -> None:
        """Restore request-local model state without mutating other turns."""
        if cleanup is not None:
            cleanup()

    async def _run_plugins(self, transcript: str) -> list[str]:
        from rex.runtime.cancellation import (  # noqa: PLC0415
            TurnCancelledError,
            await_with_cancellation,
        )

        loop = asyncio.get_running_loop()
        results: list[str] = []
        for spec in self._plugins:
            try:
                result = await await_with_cancellation(
                    loop.run_in_executor(None, spec.plugin.process, transcript)
                )
            except TurnCancelledError:
                raise
            except Exception as exc:  # pragma: no cover - defensive guard
                logger.warning("Plugin %s failed: %s", spec.name, exc)
                continue
            if result:
                results.append(str(result))
        return results

    # Context building constants and methods have been extracted to
    # rex.context.builder.ContextBuilder (US-014).  The thin delegates below
    # preserve backward compatibility for tests that call these methods directly.

    @property
    def _VOICE_CONCISE_INSTRUCTION(self) -> str:  # noqa: N802
        from .context.builder import _VOICE_CONCISE_INSTRUCTION

        return _VOICE_CONCISE_INSTRUCTION

    def _get_or_create_situational_assembler(self):
        """Return the privacy-bound situational assembler for proactive evaluation."""
        assembler = getattr(self, "_situational_assembler", None)
        if assembler is not None:
            return assembler
        from .context.situational import SituationalAssembler
        from .knowledge_base import get_knowledge_base
        from .user_facts import recall_all

        calendar = getattr(self, "_calendar_service", None)
        if calendar is None:
            try:
                calendar = get_calendar_service()
            except Exception:
                calendar = None
        assembler = SituationalAssembler(
            source_policy_store=self._get_or_create_context_source_policy(),
            calendar_service=calendar,
            knowledge_base=get_knowledge_base(),
            active_context_store=self._get_or_create_active_context_store(),
            memory_reader=recall_all,
            current_info_readers=getattr(self, "_situational_current_info_readers", None),
        )
        self._situational_assembler = assembler
        return assembler

    def _get_or_create_proactive_evaluator(self):
        evaluator = getattr(self, "_proactive_evaluator", None)
        if evaluator is None:
            from .proactivity.evaluator import ProactiveOpportunityEvaluator

            evaluator = ProactiveOpportunityEvaluator()
            self._proactive_evaluator = evaluator
        return evaluator

    def _get_or_create_context_privacy_service(self):
        service = getattr(self, "_context_privacy_service", None)
        if service is None:
            from .context.privacy import get_context_privacy_service

            service = get_context_privacy_service()
            self._context_privacy_service = service
        return service

    def _prepare_contextual_suggestion(self, user_id: str, *, response_text: str) -> None:
        """Queue one high-signal contextual suggestion without widening authority."""
        engine = getattr(self, "_suggestion_engine", None)
        if engine is None or "?" in response_text:
            return
        try:
            privacy = self._get_or_create_context_privacy_service()
            preferences = privacy.preference_store.get(user_id)
            if not preferences.proactive_assistance:
                return
            assembler = self._get_or_create_situational_assembler()
            evaluator = self._get_or_create_proactive_evaluator()
            snapshot = assembler.build(user_id=user_id)
            required = evaluator.required_current_info(snapshot)
            if required:
                snapshot = assembler.enrich_current_info(snapshot, required=required)
            candidates = evaluator.evaluate(snapshot)
            if candidates:
                engine.get_contextual_suggestion(candidates, user_id=user_id)
        except Exception as exc:
            logger.debug("Contextual suggestion evaluation skipped: %s", type(exc).__name__)

    def _get_or_create_response_builder(self):
        """Return self._response_builder, creating one lazily for __new__-based tests."""
        rb = getattr(self, "_response_builder", None)
        if rb is None:
            from .response.builder import ResponseBuilder

            rb = ResponseBuilder(
                settings=getattr(self, "_settings", None),
                response_cache=getattr(self, "_response_cache", None),
                suggestion_engine=getattr(self, "_suggestion_engine", None),
                followup_engine=getattr(self, "_followup_engine", None),
            )
            self._response_builder = rb
        return rb

    def _get_or_create_action_dispatcher(self):
        """Return self._action_dispatcher, creating one lazily for __new__-based tests."""
        ad = getattr(self, "_action_dispatcher", None)
        if ad is None:
            from .actions.dispatcher import ActionDispatcher

            ad = ActionDispatcher(
                context_builder=self._get_or_create_context_builder(),
                llm=getattr(self, "_llm", None),
                result_handler=getattr(self, "_result_handler", None),
                ha_bridge=getattr(self, "_ha_bridge", None),
                tool_dispatcher=getattr(self, "_tool_dispatcher", None),
                skill_trainer=getattr(self, "_skill_trainer", None),
                skill_registry=getattr(self, "_skill_registry", None),
                skill_router=getattr(self, "_skill_router", None),
                shopping_list_handler=getattr(self, "_shopping_list_handler", None),
                music_handler=None,
                device_state_handler=getattr(self, "_device_state_handler", None),
                suggestion_engine=getattr(self, "_suggestion_engine", None),
                pattern_entries=getattr(self, "_pattern_entries", None),
                active_context_store=self._get_or_create_active_context_store(),
                build_tool_context_fn=self._build_tool_context,
                model_call_fn_builder=self._build_tool_model_call,
                run_plugins_fn=self._run_plugins,
            )
            self._action_dispatcher = ad
        return ad

    def _get_or_create_intent_router(self):
        """Return self._intent_router, creating one lazily for __new__-based tests."""
        ir = getattr(self, "_intent_router", None)
        if ir is None:
            from .intent.router import IntentRouter

            ir = IntentRouter(tool_context_fn=self._build_tool_context)
            self._intent_router = ir
        return ir

    def _get_or_create_active_context_store(self):
        """Return the canonical short-lived active-reference store."""
        store = getattr(self, "_active_context_store", None)
        if store is None:
            from .context.active import get_active_context_store

            store = get_active_context_store()
            self._active_context_store = store
        return store

    def _get_or_create_context_source_policy(self):
        """Return the canonical source-policy store, creating it lazily."""
        store = getattr(self, "_context_source_policy", None)
        if store is None:
            from .context.source_policy import ContextSourcePolicyStore

            store = ContextSourcePolicyStore()
            self._context_source_policy = store
        return store

    def _get_or_create_context_builder(self):
        """Return self._context_builder, creating one lazily for __new__-based tests."""
        cb = getattr(self, "_context_builder", None)
        if cb is None:
            from .context.builder import ContextBuilder

            cb = ContextBuilder(
                settings=getattr(self, "_settings", None),
                history=[],
                user_id=getattr(self, "_user_id", None),
                followup_engine=getattr(self, "_followup_engine", None),
                history_provider=lambda user_id=None: self._history_for(
                    user_id if user_id is not None else self._require_user_id()
                ),
                source_policy_store=self._get_or_create_context_source_policy(),
                active_context_store=self._get_or_create_active_context_store(),
            )
            self._context_builder = cb
        return cb

    def _build_system_context(self) -> str:
        """Delegate to ContextBuilder.build_system_context() (US-014)."""
        return self._get_or_create_context_builder().build_system_context()  # type: ignore[no-any-return]

    def _build_prompt(
        self,
        transcript: str,
        *,
        voice_mode: bool = False,
        active_user_id: str | None = None,
        tool_context: str | None = None,
    ) -> str:
        """Delegate to ContextBuilder.build() (US-014)."""
        return (  # type: ignore[no-any-return]
            self._get_or_create_context_builder()
            .build(
                transcript,
                voice_mode=voice_mode,
                active_user_id=active_user_id,
                tool_context=tool_context,
            )
            .prompt
        )

    def _build_messages(
        self,
        transcript: str,
        *,
        voice_mode: bool = False,
        active_user_id: str | None = None,
        tool_context: str | None = None,
    ) -> list[dict[str, str]]:
        """Delegate to ContextBuilder.build() (US-014)."""
        return (  # type: ignore[no-any-return]
            self._get_or_create_context_builder()
            .build(
                transcript,
                voice_mode=voice_mode,
                active_user_id=active_user_id,
                tool_context=tool_context,
            )
            .messages
        )

    def _get_or_create_location_context_service(self):
        """Return the canonical per-user location service, creating it lazily."""
        service = getattr(self, "_location_context_service", None)
        if service is None:
            from .context.location_policy import LocationContextService, LocationGrantStore

            grants = LocationGrantStore(
                source_policy_store=self._get_or_create_context_source_policy()
            )
            service = LocationContextService(grant_store=grants)
            self._location_context_service = service
        return service

    def _build_tool_context(self, user_id: str | None = None) -> dict[str, str]:
        """Return static defaults plus authorized user-specific location context."""
        ctx: dict[str, str] = {}
        settings = getattr(self, "_settings", None)

        location: str | None = getattr(settings, "default_location", None)
        timezone: str | None = getattr(settings, "default_timezone", None)
        if location:
            ctx["location"] = location
        if timezone:
            ctx["timezone"] = timezone

        if user_id is not None:
            from .context.location_policy import LocationUsePurpose

            service = self._get_or_create_location_context_service()
            personal = service.get_for_assistance(user_id, LocationUsePurpose.TOOL_CONTEXT)
            if personal is not None:
                if personal.city:
                    ctx["location"] = personal.city
                if personal.timezone:
                    ctx["timezone"] = personal.timezone

        return ctx

    def _build_tool_model_call(self, transcript: str, *, user_id: str | None = None):
        # Tool re-prompts read the same user's history as the rest of the
        # request; missing identity fails closed (issue #303).
        history = self._history_for(user_id) if user_id is not None else self._history
        base_messages = [
            {"role": "system", "content": self._context_builder.build_system_context()},
            {
                "role": "system",
                "content": (
                    "A tool has already been executed for the user's request. "
                    "Use the tool result to answer the user directly in plain language. "
                    "Do not emit TOOL_REQUEST. "
                    "Do not output JSON. "
                    "Do not request another tool unless the tool result is missing or invalid."
                ),
            },
            *[{"role": turn.speaker, "content": turn.text} for turn in history[-4:]],
            {"role": "user", "content": transcript},
        ]

        def model_call(tool_message: dict[str, str]) -> str:
            messages = base_messages + [
                {
                    "role": "system",
                    "content": (
                        "The next message is the tool result. "
                        "Answer the user naturally using that result. "
                        "Do not emit TOOL_REQUEST. "
                        "Do not output JSON."
                    ),
                },
                tool_message,
            ]

            reply = self._llm.generate(messages=messages)

            if isinstance(reply, str) and reply.lstrip().startswith("TOOL_REQUEST:"):
                messages.extend(
                    [
                        {"role": "assistant", "content": reply},
                        {
                            "role": "system",
                            "content": (
                                "That was incorrect. "
                                "You already have the tool result. "
                                "Answer the user directly in one short natural sentence. "
                                "Do not emit TOOL_REQUEST. "
                                "Do not output JSON."
                            ),
                        },
                    ]
                )
                reply = self._llm.generate(messages=messages)

            return reply

        return model_call

    def history(self) -> list[ConversationTurn]:
        return list(self._history)

    def _log_turn(self, transcript: str, reply: str, *, user_id: str | None = None) -> None:
        # Resolve and validate the owner before any filesystem path is built:
        # transcripts are written under a per-user directory and must never
        # land under an invented or unvalidated identity (issue #303).
        uid = user_id if user_id is not None else getattr(self, "_user_id", None)
        if uid is None:
            raise IdentityRequiredError(_IDENTITY_REQUIRED_MESSAGE)
        uid = validate_user_id(uid)
        try:
            self._transcripts_dir.mkdir(parents=True, exist_ok=True)
            user_dir = self._transcripts_dir / uid
            user_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now(UTC)
            file_path = user_dir / f"{timestamp:%Y-%m-%d}.txt"
            with file_path.open("a", encoding="utf-8") as handle:
                handle.write(f"{timestamp:%H:%M:%S} user: {transcript.strip()}\n")
                handle.write(f"{timestamp:%H:%M:%S} assistant: {reply.strip()}\n\n")
        except Exception:  # pragma: no cover - logging must not break replies
            logger.exception("Failed to persist transcript entry")


__all__ = ["Assistant", "ConversationTurn", "PluginSpec"]
