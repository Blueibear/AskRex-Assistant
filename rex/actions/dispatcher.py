"""Action dispatch orchestration (US-016).

Extracted from ``rex.assistant.Assistant.generate_reply``.  Handles
skill invocation, HA command routing, auto tool dispatch, the LLM call,
and OpenClaw tool-bridge post-processing in one component so that
``generate_reply`` reads as a thin orchestration spec.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from rex.context.active import ActiveContextRef, ActiveContextStore
from rex.latency import LatencyTrace
from rex.runtime.cancellation import await_with_cancellation, current_turn_cancellation
from rex.runtime.events import EventKind, TurnEventStream
from rex.runtime.invocation import current_turn_invocation

logger = logging.getLogger(__name__)

_UNDO_PATTERN = re.compile(r"^\s*undo\s*(?:that)?\s*$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# ActionResult
# ---------------------------------------------------------------------------


@dataclass
class ActionResult:
    """Result returned by :class:`ActionDispatcher`."""

    success: bool
    response: str
    actions_taken: list[str] = field(default_factory=list)
    error: str | None = None
    model_generated: bool = False
    recovery_actions: list[dict[str, object]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _ActiveFollowup:
    tool_name: str | None = None
    args: dict[str, object] = field(default_factory=dict)
    clarification: str | None = None


def _active_clarification(candidates: tuple[ActiveContextRef, ...]) -> str:
    domains = {ref.domain for ref in candidates}
    if domains == {"timekeeping"}:
        record_types = {ref.payload.get("record_type") for ref in candidates}
        if record_types == {"timer"}:
            return "Which timer do you mean?"
        if record_types == {"alarm"}:
            return "Which alarm do you mean?"
        return "Which timer or alarm do you mean?"
    if domains == {"media"}:
        return "Which media session do you mean?"
    return "Which one do you mean?"


def _timekeeping_active_followup(
    ref: ActiveContextRef,
    transcript: str,
) -> _ActiveFollowup | None:
    text = " ".join(transcript.casefold().strip().rstrip(".?!").split())
    record_type = ref.payload.get("record_type")
    verb = text.split(" ", 1)[0] if text else ""
    action: str | None = None
    if record_type == "timer":
        action = {
            "pause": "pause_timer",
            "resume": "resume_timer",
            "cancel": "cancel_timer",
            "stop": "cancel_timer",
        }.get(verb)
        if action is None and any(
            phrase in text for phrase in ("how much time", "time left", "remaining", "how long")
        ):
            action = "query_timer"
    elif record_type == "alarm":
        action = {
            "snooze": "snooze_alarm",
            "dismiss": "dismiss_alarm",
            "enable": "enable_alarm",
            "disable": "disable_alarm",
            "cancel": "cancel_alarm",
            "stop": "cancel_alarm",
        }.get(verb)
    if action is None:
        return None
    tool_name = "timekeeping_read" if action == "query_timer" else "timekeeping_manage"
    return _ActiveFollowup(
        tool_name=tool_name,
        args={"action": action, "reference": ref.key},
    )


def _media_active_followup(
    ref: ActiveContextRef,
    transcript: str,
) -> _ActiveFollowup | None:
    from rex.media.parser import MediaCommandAction, parse_media_command  # noqa: PLC0415

    command = parse_media_command(transcript)
    if command is None:
        return None
    action = MediaCommandAction(command.action)
    contextual_actions = {
        MediaCommandAction.PAUSE,
        MediaCommandAction.RESUME,
        MediaCommandAction.STOP,
        MediaCommandAction.NEXT,
        MediaCommandAction.PREVIOUS,
        MediaCommandAction.SET_VOLUME,
        MediaCommandAction.MUTE,
        MediaCommandAction.UNMUTE,
        MediaCommandAction.QUERY_STATE,
    }
    if action not in contextual_actions:
        return None
    target_id = ref.payload.get("target_id")
    if not isinstance(target_id, str) or not target_id:
        return None
    args: dict[str, object] = {
        "action": action.value,
        "target_text": target_id,
    }
    if command.query is not None:
        args["query"] = command.query
    if command.level is not None:
        args["level"] = command.level
    tool_name = "media_read" if action is MediaCommandAction.QUERY_STATE else "media_manage"
    return _ActiveFollowup(tool_name=tool_name, args=args)


def _resolve_active_followup(
    store: ActiveContextStore | None,
    user_id: str,
    transcript: str,
) -> _ActiveFollowup | None:
    if store is None:
        return None
    resolution = store.resolve(
        user_id,
        transcript,
        candidate_domains=("media", "timekeeping"),
    )
    if resolution.reason == "ambiguous":
        return _ActiveFollowup(clarification=_active_clarification(resolution.candidates))
    ref = resolution.ref
    if ref is None:
        return None
    if ref.domain == "timekeeping":
        return _timekeeping_active_followup(ref, transcript)
    if ref.domain == "media":
        return _media_active_followup(ref, transcript)
    return None


def _parsed_timekeeping_route(transcript: str) -> _ActiveFollowup | None:
    from rex.timekeeping.parser import parse_timekeeping_command  # noqa: PLC0415

    command = parse_timekeeping_command(transcript, user_timezone="UTC")
    if command is None:
        return None
    read_actions = {"list_timers", "query_timer", "list_alarms"}
    tool_name = "timekeeping_read" if command.action in read_actions else "timekeeping_manage"
    return _ActiveFollowup(tool_name=tool_name, args={"transcript": transcript})


def _parsed_media_route(transcript: str, origin_device_id: str | None) -> _ActiveFollowup | None:
    from rex.media.parser import MediaCommandAction, parse_media_command  # noqa: PLC0415

    command = parse_media_command(transcript)
    if command is None:
        return None
    tool_name = "media_read" if command.action is MediaCommandAction.QUERY_STATE else "media_manage"
    return _ActiveFollowup(
        tool_name=tool_name,
        args={"transcript": transcript, "origin_device_id": origin_device_id},
    )


def _select_exact_route(
    store: ActiveContextStore | None,
    user_id: str,
    transcript: str,
    origin_device_id: str | None,
) -> _ActiveFollowup | None:
    active = _resolve_active_followup(store, user_id, transcript)
    if active is not None:
        if active.tool_name in {"media_read", "media_manage"}:
            args = dict(active.args)
            args["origin_device_id"] = origin_device_id
            return _ActiveFollowup(
                tool_name=active.tool_name,
                args=args,
                clarification=active.clarification,
            )
        return active
    return _parsed_timekeeping_route(transcript) or _parsed_media_route(
        transcript, origin_device_id
    )


# ---------------------------------------------------------------------------
# ActionDispatcher
# ---------------------------------------------------------------------------


class ActionDispatcher:
    """Route a user transcript through all action layers before returning a response.

    Handles (in order):

    1. Skill training detection (natural-language skill creation)
    2. Skill invocation (registered trigger matching)
    3. Shopping list voice commands
    4. Music Assistant voice commands
    5. Device state queries
    6. Auto tool dispatch (pre-LLM tool context building via ToolDispatcher)
    7. Home Assistant command routing (undo + process_transcript)
    8. LLM call (via ContextBuilder + LanguageModel)
    9. Post-LLM tool-request resolution (ToolResultHandler / OpenClaw bridge)

    Args:
        context_builder:      :class:`~rex.context.builder.ContextBuilder` instance.
        llm:                  :class:`~rex.llm_client.LanguageModel` instance.
        result_handler:       :class:`~rex.tools.result_handler.ToolResultHandler` instance.
        ha_bridge:            Optional :class:`~rex.ha_bridge.HABridge` instance.
        tool_dispatcher:      Optional :class:`~rex.tools.dispatcher.ToolDispatcher` instance.
        skill_trainer:        Optional :class:`~rex.skills.trainer.SkillTrainer` instance.
        skill_registry:       Optional :class:`~rex.skills.registry.SkillRegistry` instance.
        skill_router:         Optional :class:`~rex.skills.router.SkillRouter` instance.
        shopping_list_handler: Optional :class:`~rex.shopping_list_handler.ShoppingListHandler`.
        music_handler:        Optional :class:`~rex.music_handler.MusicHandler`.
        device_state_handler: Optional :class:`~rex.ha.state_handler.DeviceStateHandler`.
        suggestion_engine:    Optional proactive suggestion engine.
        pattern_entries:      Mutable dict mapping user_id to that user's list of
                              PatternEntry objects for suggestion detection (issue #303).
        build_tool_context_fn: Callable returning ``{"location": ..., "timezone": ...}`` dict.
        model_call_fn_builder: Callable ``(transcript) -> model_call_fn`` used for tool re-prompts.
        run_plugins_fn:       Async callable ``(transcript) -> list[str]`` for plugin enrichments.
    """

    def __init__(
        self,
        *,
        context_builder: Any,
        llm: Any,
        result_handler: Any,
        ha_bridge: Any = None,
        tool_dispatcher: Any = None,
        skill_trainer: Any = None,
        skill_registry: Any = None,
        skill_router: Any = None,
        shopping_list_handler: Any = None,
        music_handler: Any = None,
        device_state_handler: Any = None,
        suggestion_engine: Any = None,
        pattern_entries: dict | None = None,
        active_context_store: ActiveContextStore | None = None,
        build_tool_context_fn: Callable[..., dict] | None = None,
        model_call_fn_builder: Callable[..., Any] | None = None,
        run_plugins_fn: Callable[..., Awaitable[list[str]]] | None = None,
    ) -> None:
        self._context_builder = context_builder
        self._llm = llm
        self._result_handler = result_handler
        self._ha_bridge = ha_bridge
        self._tool_dispatcher = tool_dispatcher
        self._skill_trainer = skill_trainer
        self._skill_registry = skill_registry
        self._skill_router = skill_router
        self._shopping_list_handler = shopping_list_handler
        self._music_handler = music_handler
        self._device_state_handler = device_state_handler
        self._suggestion_engine = suggestion_engine
        # Per-user command log for pattern detection, keyed by user_id (#303)
        self._pattern_entries: dict = pattern_entries if pattern_entries is not None else {}
        self._active_context_store = active_context_store
        self._build_tool_context_fn = build_tool_context_fn
        self._model_call_fn_builder = model_call_fn_builder
        self._run_plugins_fn = run_plugins_fn

    def _tool_context_for_user(self, user_id: str) -> dict:
        if self._build_tool_context_fn is None:
            return {}
        try:
            return self._build_tool_context_fn(user_id)
        except TypeError:
            return self._build_tool_context_fn()

    # ------------------------------------------------------------------
    # Primary entry point
    # ------------------------------------------------------------------

    async def dispatch(
        self,
        intent: Any,
        context: Any,
        transcript: str,
        *,
        voice_mode: bool = False,
        active_user_id: str | None = None,
        user_id: str | None = None,
        loop: asyncio.AbstractEventLoop | None = None,
        latency_trace: LatencyTrace | None = None,
        turn_events: TurnEventStream | None = None,
        scope: str = "user",
    ) -> ActionResult:
        """Dispatch *transcript* through all action layers and return an :class:`ActionResult`.

        Args:
            intent:         :class:`~rex.intent.router.IntentResult` from the intent router.
                            Passed for context; the caller already handled ``handled=True`` cases.
            context:        :class:`~rex.context.builder.ContextPackage` from the context builder.
                            Used as a fallback when no auto tool context is available.
            transcript:     The user's original message.  (The PRD spec refers to this
                            parameter as ``llm_response`` but it carries the *input* transcript
                            so that pre-LLM handlers can pattern-match against it.)
            voice_mode:     Append the voice-concise instruction to the LLM prompt.
            active_user_id: Per-request user override for multi-user scenarios.
            user_id:        Explicit session user ID.  At least one of
                            *active_user_id* / *user_id* is required: user-scoped
                            handlers and tools fail closed without an identity
                            (issue #303); a missing identity never becomes
                            ``"default"``.
            loop:           Running event loop; obtained via ``asyncio.get_running_loop()`` when
                            *None*.
            turn_events:    Optional canonical turn event stream for truthful progress
                            observation. It never grants or widens execution authority.
            scope:          Current turn's immutable data/authority scope value (e.g.
                            ``TurnScope.USER``/``TurnScope.HOUSEHOLD``), forwarded to
                            bounded speculative tool prefetch/consume revalidation
                            (US-101). Defaults to the single-user scope in effect today.

        Returns:
            :class:`ActionResult` with ``success=True`` and the final response string.

        Raises:
            IdentityRequiredError: When neither *active_user_id* nor *user_id*
                is provided.
            ValueError: When the provided identity fails canonical validation.
        """
        from rex.assistant_errors import IdentityRequiredError
        from rex.identity import validate_user_id

        _loop = loop or asyncio.get_running_loop()
        effective_user = active_user_id or user_id
        if effective_user is None:
            raise IdentityRequiredError(
                "No user identity is bound for this operation. "
                "Pass user_id or active_user_id to dispatch()."
            )
        effective_user = validate_user_id(effective_user)

        def emit(kind: EventKind, details: dict[str, Any]) -> None:
            if turn_events is not None:
                turn_events.emit(kind, details)

        from rex.mobile_api.action_context import (  # noqa: PLC0415
            mobile_action_context_active,
            mobile_scope_granted,
            run_in_executor_with_mobile_context,
        )

        def check_cancelled() -> None:
            cancellation = current_turn_cancellation()
            if cancellation is not None:
                cancellation.raise_if_cancelled()

        async def run_blocking(func: Callable[..., Any], *args: Any) -> Any:
            check_cancelled()
            return await await_with_cancellation(
                run_in_executor_with_mobile_context(_loop, func, *args)
            )

        check_cancelled()

        # 1. Skill training: intercept natural-language skill creation before LLM
        if (
            self._skill_trainer is not None
            and self._skill_registry is not None
            and not mobile_action_context_active()
        ):
            training_response = self._skill_trainer.handle_if_training_request(
                transcript, self._skill_registry
            )
            if training_response is not None:
                emit(
                    EventKind.CAPABILITY_PROGRESS,
                    {"capability": "skill_training", "status": "selected"},
                )
                emit(
                    EventKind.ACTION_PROGRESS,
                    {"capability": "skill_training", "status": "returned"},
                )
                return ActionResult(
                    success=True,
                    response=str(training_response),
                    actions_taken=["skill_training"],
                )

        # 2. Skill invocation: check registered skill triggers before the LLM
        if self._skill_router is not None and not mobile_action_context_active():
            matched_skill = self._skill_router.match(transcript)
            if matched_skill is not None:
                emit(
                    EventKind.CAPABILITY_PROGRESS,
                    {"capability": "skill_invocation", "status": "selected"},
                )
                skill_response = str(self._skill_router.execute(matched_skill, transcript))
                emit(
                    EventKind.ACTION_PROGRESS,
                    {"capability": "skill_invocation", "status": "returned"},
                )
                return ActionResult(
                    success=True,
                    response=skill_response,
                    actions_taken=["skill_invocation"],
                )

        # 3. Shopping list voice commands
        if self._shopping_list_handler is not None and mobile_scope_granted("tasks.write"):
            _sl_response = self._shopping_list_handler.handle(transcript, user_id=effective_user)
            if _sl_response is not None:
                emit(
                    EventKind.CAPABILITY_PROGRESS,
                    {"capability": "shopping_list", "status": "selected"},
                )
                emit(
                    EventKind.ACTION_PROGRESS, {"capability": "shopping_list", "status": "returned"}
                )
                return ActionResult(
                    success=True,
                    response=str(_sl_response),
                    actions_taken=["shopping_list"],
                )

        # 4. Media commands are routed through canonical tools below.

        # 5. Device state queries
        if self._device_state_handler is not None and mobile_scope_granted("home.read"):
            _ds_response = self._device_state_handler.handle(transcript)
            if _ds_response is not None:
                emit(
                    EventKind.CAPABILITY_PROGRESS,
                    {"capability": "device_state", "status": "selected"},
                )
                emit(
                    EventKind.ACTION_PROGRESS, {"capability": "device_state", "status": "returned"}
                )
                return ActionResult(
                    success=True,
                    response=str(_ds_response),
                    actions_taken=["device_state"],
                )

        # 6. Auto tool dispatch: build pre-LLM tool context string
        _tool_context: str | None = None
        _tool_results: dict[str, Any] = {}
        _selected_tools: list[Any] = []
        _tool_candidates_found = False
        _timekeeping_handled = False
        _media_handled = False
        _recovery_actions: list[dict[str, object]] = []
        current_info_requested = getattr(intent, "intent_type", None) == "current_info"
        selection_text = f"web search {transcript}" if current_info_requested else transcript
        if self._tool_dispatcher is not None:
            exact_route = _select_exact_route(
                self._active_context_store,
                effective_user,
                transcript,
                current_turn_invocation().device_id,
            )
            if exact_route is not None and exact_route.clarification is not None:
                return ActionResult(
                    success=True,
                    response=exact_route.clarification,
                    actions_taken=["context_clarification"],
                )

            if exact_route is not None and exact_route.tool_name is not None:
                exact_tool = exact_route.tool_name
                exact_args = dict(exact_route.args)
                is_media_route = exact_tool.startswith("media_")
                can_pre_dispatch = not (
                    mobile_action_context_active() and exact_tool.endswith("_manage")
                )
                dispatch_fn = getattr(self._tool_dispatcher, "dispatch", None)
                if is_media_route:
                    _tool_candidates_found = True
                    _media_handled = True
                if can_pre_dispatch and callable(dispatch_fn):
                    _tool_candidates_found = True
                    if not is_media_route:
                        _timekeeping_handled = True
                    emit(
                        EventKind.CAPABILITY_PROGRESS,
                        {"stage": "tool_selection", "capabilities": [exact_tool]},
                    )
                    if latency_trace is not None:
                        latency_trace.start("tool")
                    try:
                        exact_result = await run_blocking(
                            functools.partial(
                                dispatch_fn,
                                exact_tool,
                                exact_args,
                                {"user_id": effective_user},
                            )
                        )
                    finally:
                        if latency_trace is not None:
                            latency_trace.end("tool")
                    if getattr(exact_result, "success", False):
                        _tool_results[exact_tool] = getattr(exact_result, "output", None)
                    elif getattr(exact_result, "error", None) == "Execution timed out":
                        _tool_results[exact_tool] = f"I couldn't reach {exact_tool} in time"
                    else:
                        detail = (
                            getattr(exact_result, "detail", None)
                            or getattr(exact_result, "error", None)
                            or "unknown error"
                        )
                        _tool_results[exact_tool] = f"[tool error: {detail}]"
                    _tool_context = self._tool_dispatcher.format_tool_context(_tool_results) or None
                    emit(
                        EventKind.ACTION_PROGRESS,
                        {"stage": "tool_execution", "status": "returned", "count": 1},
                    )

            if not _timekeeping_handled and not _media_handled:
                # Kick off bounded speculative prefetch (US-101) from a fast,
                # semantic-free candidate signal *before* the authoritative
                # hybrid-ranked selection below is even called, so eligible
                # background dispatch is already in flight while that routing
                # decision is still resolving rather than only starting once
                # it has already finished.
                _routing_prefetch = None
                begin_prefetch_fn = inspect.getattr_static(
                    self._tool_dispatcher, "begin_speculative_prefetch", None
                )
                if begin_prefetch_fn is not None:
                    _routing_prefetch = await run_blocking(
                        functools.partial(
                            self._tool_dispatcher.begin_speculative_prefetch,
                            selection_text,
                            user_id=effective_user,
                            scope=str(scope),
                        )
                    )
                defined_select_for_user = inspect.getattr_static(
                    self._tool_dispatcher, "select_tools_for_user", None
                )
                if defined_select_for_user is not None:
                    select_for_user = self._tool_dispatcher.select_tools_for_user
                    _selected_tools = select_for_user(selection_text, user_id=effective_user)
                else:
                    # Compatibility adapters predating US-106 implement only
                    # select_tools(message). The canonical dispatcher exposes the
                    # user-aware extension above, while older adapters remain valid.
                    _selected_tools = self._tool_dispatcher.select_tools(selection_text)
                _tool_candidates_found = bool(_selected_tools)
                if mobile_action_context_active():
                    # Pre-LLM dispatch has only free-form transcript text. Mobile
                    # mutations must wait for a canonical structured tool call so
                    # S8 can bind the exact action hash before execution.
                    _selected_tools = [
                        tool
                        for tool in _selected_tools
                        if getattr(tool, "operation", "read") == "read"
                    ]
                if _selected_tools:
                    capability_names = [
                        str(getattr(tool, "name", getattr(tool, "tool_name", "unknown")))
                        for tool in _selected_tools
                    ]
                    emit(
                        EventKind.CAPABILITY_PROGRESS,
                        {"stage": "tool_selection", "capabilities": capability_names},
                    )
                    if latency_trace is not None:
                        latency_trace.start("tool")
                    _execute_tools_kwargs: dict[str, Any] = {
                        "user_id": effective_user,
                        "scope": str(scope),
                    }
                    _execute_tools_signature = inspect.signature(
                        self._tool_dispatcher.execute_tools
                    )
                    if "routing_prefetch" in _execute_tools_signature.parameters or any(
                        parameter.kind == inspect.Parameter.VAR_KEYWORD
                        for parameter in _execute_tools_signature.parameters.values()
                    ):
                        _execute_tools_kwargs["routing_prefetch"] = _routing_prefetch
                    try:
                        _tool_results = await run_blocking(
                            functools.partial(
                                self._tool_dispatcher.execute_tools,
                                _selected_tools,
                                transcript,
                                **_execute_tools_kwargs,
                            ),
                        )
                    finally:
                        if latency_trace is not None:
                            latency_trace.end("tool")
                    _tool_context = self._tool_dispatcher.format_tool_context(_tool_results) or None
                    emit(
                        EventKind.ACTION_PROGRESS,
                        {
                            "stage": "tool_execution",
                            "status": "returned",
                            "count": len(_tool_results),
                        },
                    )

        completion: str | None = None
        if current_info_requested:
            search_result = _tool_results.get("web_search")
            search_failed = (
                not isinstance(search_result, str)
                or not search_result.strip()
                or search_result.startswith("[tool error:")
                or search_result.startswith("I couldn't reach web_search")
            )
            if search_failed:
                completion = (
                    "I couldn't verify current news through Web Search, so I won't guess at live "
                    "events. Check the configured search provider/network and "
                    "`docs/configuration.md` under Integrations > Web Search."
                )
            else:
                grounding_rule = (
                    "CURRENT-INFO GROUNDING: Make current/live factual claims only from the "
                    "web_search result below. If that result is insufficient, say so instead of "
                    "supplementing with model memory."
                )
                _tool_context = f"{grounding_rule}\n{_tool_context or ''}".strip()

        model_generated = False

        # 7. HA command routing (including undo and proactive suggestion injection)
        if (
            self._ha_bridge is not None
            and self._ha_bridge.enabled
            and not current_info_requested
            and not _timekeeping_handled
            and not _media_handled
            and not mobile_action_context_active()
            and mobile_scope_granted("home.control")
        ):
            emit(
                EventKind.CAPABILITY_PROGRESS,
                {"capability": "home_assistant", "status": "entered"},
            )
            if latency_trace is not None:
                latency_trace.start("tool")
            if _UNDO_PATTERN.match(transcript):
                completion = await run_blocking(self._ha_bridge.undo_last)
            else:
                _hist_len_before = len(getattr(self._ha_bridge, "_command_history", None) or [])
                completion = await run_blocking(self._ha_bridge.process_transcript, transcript)
                if completion is not None:
                    _cmd_hist = getattr(self._ha_bridge, "_command_history", None)
                    if _cmd_hist is not None and len(_cmd_hist) > _hist_len_before:
                        try:
                            _last = _cmd_hist._entries[-1]
                            from rex.suggestions.pattern_detector import PatternEntry

                            self._pattern_entries.setdefault(effective_user, []).append(
                                PatternEntry(
                                    entity_id=_last.entity_id,
                                    service=_last.service,
                                    timestamp=time.time(),
                                )
                            )
                        except Exception:
                            pass
                    if self._suggestion_engine is not None:
                        try:
                            from rex.suggestions.pattern_detector import detect_patterns

                            _patterns = detect_patterns(
                                self._pattern_entries.get(effective_user, [])
                            )
                            _sug = self._suggestion_engine.get_suggestion(
                                _patterns, user_id=effective_user
                            )
                            if _sug is not None:
                                _, _spoken = _sug
                                completion = f"{completion} {_spoken}"
                        except Exception:
                            pass
            if latency_trace is not None:
                latency_trace.end("tool")
            if completion is not None:
                emit(
                    EventKind.ACTION_PROGRESS,
                    {"capability": "home_assistant", "status": "returned"},
                )

        # 8. Capability-gap recovery. This offers only structured next actions;
        # it never grants authority or executes the proposed recovery itself.
        if (
            completion is None
            and not current_info_requested
            and self._tool_dispatcher is not None
            and not _tool_candidates_found
        ):
            defined_recovery_plan = inspect.getattr_static(
                self._tool_dispatcher, "recovery_plan", None
            )
            if defined_recovery_plan is not None:
                try:
                    recovery_plan = self._tool_dispatcher.recovery_plan(
                        transcript, user_id=effective_user
                    )
                except Exception:
                    logger.exception("capability recovery planning failed")
                    recovery_plan = None
                if recovery_plan is not None:
                    completion = str(recovery_plan.message)
                    recovery_payload = recovery_plan.to_dict()
                    raw_actions = recovery_payload.get("actions")
                    if isinstance(raw_actions, list):
                        _recovery_actions = [
                            {str(key): value for key, value in action.items()}
                            for action in raw_actions
                            if isinstance(action, dict)
                        ]
                    emit(
                        EventKind.CAPABILITY_PROGRESS,
                        {
                            "stage": "recovery",
                            "recovery": recovery_payload,
                        },
                    )
                    emit(
                        EventKind.ACTION_PROGRESS,
                        {
                            "stage": "recovery",
                            "status": "offered",
                            "count": len(_recovery_actions),
                        },
                    )

        # 9. LLM call (if no pre-LLM handler or recovery path produced a completion)
        if completion is None:
            model_generated = True
            # Rebuild context with tool_context if auto-dispatch populated it.
            if _tool_context:
                ctx = self._context_builder.build(
                    transcript,
                    voice_mode=voice_mode,
                    active_user_id=active_user_id,
                    tool_context=_tool_context,
                )
            else:
                ctx = context

            messages = ctx.messages
            prompt = ctx.prompt
            if latency_trace is not None:
                latency_trace.start("llm")
            emit(
                EventKind.MODEL_PROGRESS,
                {"stage": "generation", "status": "started"},
            )
            try:
                try:
                    completion = await run_blocking(lambda: self._llm.generate(messages=messages))
                except TypeError:
                    completion = await run_blocking(lambda: self._llm.generate(prompt))
            finally:
                if latency_trace is not None:
                    latency_trace.end("llm")
            check_cancelled()
            emit(
                EventKind.MODEL_PROGRESS,
                {"stage": "generation", "status": "returned"},
            )

            # 10. Post-process LLM output (TOOL_REQUEST resolution, OpenClaw bridge)
            plugin_enrichments: list[str] = []
            if self._run_plugins_fn is not None and not mobile_action_context_active():
                plugin_enrichments = await await_with_cancellation(self._run_plugins_fn(transcript))

            tool_context_dict = self._tool_context_for_user(effective_user)
            model_call_fn = None
            if self._model_call_fn_builder is not None:
                try:
                    # Identity-aware builders receive the request user so tool
                    # re-prompts read the same user's history (issue #303).
                    model_call_fn = self._model_call_fn_builder(transcript, user_id=effective_user)
                except TypeError:
                    model_call_fn = self._model_call_fn_builder(transcript)
            if latency_trace is not None:
                latency_trace.start("postprocess")
            try:
                result_handler_kwargs = {
                    "tool_context": tool_context_dict,
                    "model_call_fn": model_call_fn,
                    "plugin_enrichments": plugin_enrichments,
                }
                try:
                    process_signature = inspect.signature(self._result_handler.process)
                    process_parameters = process_signature.parameters
                    supports_media_ha_flag = "allow_ha_postprocess" in process_parameters or any(
                        parameter.kind is inspect.Parameter.VAR_KEYWORD
                        for parameter in process_parameters.values()
                    )
                except (TypeError, ValueError):
                    supports_media_ha_flag = True
                if supports_media_ha_flag:
                    result_handler_kwargs["allow_ha_postprocess"] = not _media_handled
                completion = await await_with_cancellation(
                    self._result_handler.process(
                        transcript,
                        completion,
                        **result_handler_kwargs,
                    )
                )
                check_cancelled()
                emit(
                    EventKind.ACTION_PROGRESS,
                    {"stage": "result_handler", "status": "returned"},
                )
            finally:
                if latency_trace is not None:
                    latency_trace.end("postprocess")

        check_cancelled()
        if completion is None:
            raise RuntimeError("action dispatch completed without a response")
        return ActionResult(
            success=True,
            response=completion,
            actions_taken=["llm"] if not completion else ["dispatch"],
            model_generated=model_generated,
            recovery_actions=_recovery_actions,
        )
