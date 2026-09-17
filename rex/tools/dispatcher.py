"""Auto tool selection — map user intent to tools (US-TD-002/US-TD-003).

``ToolDispatcher.select_tools()`` maps a user message to a small set of
registered tools through permission-aware hybrid capability retrieval. ``execute_tools()`` invokes
each selected tool with a configurable timeout and a single retry on
transient network errors.

Canonical implementation of ``ToolDispatcherProtocol`` (see
``rex.tools.protocol``).  The ``dispatch(name, args, context)`` signature
defined in the protocol will be added to this class in a follow-up story;
the current ``execute_tools()`` API is preserved for backward compatibility.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from rex.capabilities.recovery import (
    CapabilityGapResolver,
    ExternalCapabilityCandidate,
    RecoveryActionKind,
    RecoveryPlan,
    looks_like_action_request,
    looks_like_capability_request,
)
from rex.capabilities.registry import Capability
from rex.capabilities.retrieval import CapabilityRetriever
from rex.runtime.speculation import PrefetchHandle, SpeculativePrefetcher

from .execution import _is_auth_error as _is_auth_error
from .execution import _is_transient_error as _is_transient_error
from .registry import Tool, ToolRegistry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Error classification helpers
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# ToolDispatcher
# ---------------------------------------------------------------------------

_DEFAULT_TIMEOUT: float = 10.0


class ToolDispatcher:
    """Select and execute tools from a registry.

    Args:
        registry: The ``ToolRegistry`` to pull tools from.
        config: Optional ``AppConfig`` instance.  Used to filter unavailable
            tools and to read ``tool_timeout_seconds``.  When *None* all
            registered tools are candidates and the default timeout applies.

    Usage::

        dispatcher = ToolDispatcher(registry, config=app_config)
        tools = dispatcher.select_tools("What's the weather and check my email?")
        results = dispatcher.execute_tools(tools, message)
        context = dispatcher.format_tool_context(results)
    """

    def __init__(
        self,
        registry: ToolRegistry,
        config: Any = None,
        *,
        mcp_candidates: tuple[ExternalCapabilityCandidate, ...] = (),
        openapi_candidates: tuple[ExternalCapabilityCandidate, ...] = (),
    ) -> None:
        self._registry = registry
        self._config = config
        self._timeout_seconds: float = float(
            getattr(config, "tool_timeout_seconds", _DEFAULT_TIMEOUT) or _DEFAULT_TIMEOUT
        )
        self._capability_retriever = CapabilityRetriever(
            registry.capability_registry,
            config=config,
            candidate_filter=self._tool_candidate_enabled,
        )
        self._gap_resolver = CapabilityGapResolver(
            registry.capability_registry,
            mcp_candidates=mcp_candidates,
            openapi_candidates=openapi_candidates,
            config=config,
        )
        # Speculatively prefetches only currently healthy/permitted read-only
        # candidates from this same ranked selection, through this dispatcher's
        # own canonical `dispatch()` -> `ToolExecutionLifecycle` path (US-101).
        # Mutating/sensitive/prohibited/disabled/unauthorized tools are never
        # eligible and are always dispatched through the ordinary single call
        # below.
        self._prefetcher = SpeculativePrefetcher(registry.capability_registry, self)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def _tool_candidate_enabled(self, card: Capability) -> bool:
        tool = self._registry.get(card.id)
        return tool is not None and tool.enabled

    def select_tools(
        self,
        message: str,
        *,
        user_id: str | None = None,
        granted_permissions: set[str] | frozenset[str] | None = None,
    ) -> list[Tool]:
        """Return a small, security-filtered hybrid-ranked tool set.

        Canonical Capability retrieval filters current-user authorization, identity
        scope, configured/enabled state, health, and risk before any lexical or
        semantic ranking is performed. Semantic evidence is local-only and falls
        back deterministically to lexical scoring if unavailable.
        """
        matches = self._capability_retriever.retrieve(
            message,
            user_id=user_id,
            granted_permissions=granted_permissions,
        )
        selected: list[Tool] = []
        for match in matches:
            tool = self._registry.get(match.capability.id)
            if tool is None:
                continue
            selected.append(tool)
            logger.debug(
                "tool_dispatcher: selected tool=%r score=%.3f evidence=%s",
                tool.name,
                match.score,
                ",".join(match.reasons),
            )
        return selected

    def select_tools_for_user(
        self,
        message: str,
        *,
        user_id: str,
        granted_permissions: set[str] | frozenset[str] | None = None,
    ) -> list[Tool]:
        """Select tools with the current user's live authorization context."""
        return self.select_tools(
            message,
            user_id=user_id,
            granted_permissions=granted_permissions,
        )

    def recovery_plan(
        self,
        message: str,
        *,
        user_id: str | None = None,
        granted_permissions: set[str] | frozenset[str] | None = None,
    ) -> RecoveryPlan | None:
        """Return a non-executing recovery plan after ordinary tool selection fails."""
        if not looks_like_action_request(message):
            return None
        permissions = self._resolve_permissions_for_recovery(user_id, granted_permissions)
        plan = self._gap_resolver.resolve(
            message,
            user_id=user_id,
            granted_permissions=permissions,
            allow_build=looks_like_capability_request(message),
        )
        if not plan.actions and not plan.blocked:
            return None
        # An enabled/authorized local tool should have been returned by select_tools.
        # Do not hide a dispatcher mismatch behind a misleading "use it" recovery card.
        if plan.actions and plan.actions[0].kind is RecoveryActionKind.USE_CAPABILITY:
            return None
        return plan

    @staticmethod
    def _resolve_permissions_for_recovery(
        user_id: str | None,
        granted_permissions: set[str] | frozenset[str] | None,
    ) -> frozenset[str]:
        if granted_permissions is not None:
            return frozenset(granted_permissions)
        if not user_id:
            return frozenset()
        try:
            from rex.permissions import get_permissions  # noqa: PLC0415

            return frozenset(get_permissions(user_id))
        except Exception:
            logger.exception("tool_dispatcher: failed to resolve permissions for recovery")
            return frozenset()

    def begin_speculative_prefetch(
        self,
        message: str,
        *,
        user_id: str | None = None,
        granted_permissions: set[str] | frozenset[str] | None = None,
        scope: str = "user",
    ) -> PrefetchHandle | None:
        """Start bounded background dispatch while routing is still resolving (US-101).

        Uses a fast, semantic-free lexical pre-ranking so eligible read-only
        candidates can begin dispatching in the background *before* the
        authoritative hybrid-ranked :meth:`select_tools_for_user` decision
        finishes resolving, rather than only after routing has already
        selected the final tool list. Callers pass the returned handle to
        :meth:`execute_tools` (``routing_prefetch=``); it is revalidated and
        discarded there exactly like any other speculative result if it does
        not match the final selection.
        """
        permissions = self._resolve_permissions_for_recovery(user_id, granted_permissions)
        fast_matches = self._capability_retriever.retrieve_lexical_only(
            message, user_id=user_id, granted_permissions=permissions
        )
        candidate_ids = [
            match.capability.id
            for match in fast_matches
            if self._prefetcher.eligible(
                match.capability.id, user_id=user_id, granted_permissions=permissions
            )
        ]
        if not candidate_ids:
            return None
        return self._prefetcher.begin(
            candidate_ids,
            user_id=user_id or "",
            scope=scope,
            granted_permissions=permissions,
            args_by_capability={name: {"transcript": message} for name in candidate_ids},
        )

    def execute_tools(
        self,
        tools: list[Tool],
        message: str,
        *,
        user_id: str | None = None,
        scope: str = "user",
        routing_prefetch: PrefetchHandle | None = None,
    ) -> dict[str, Any]:
        """Invoke *tools* with timeout + one-retry on transient errors.

        Each handler is called with ``transcript=message``.  For each tool:

        * If the call succeeds within the timeout the result is stored.
        * If the call times out the message
          ``"I couldn't reach {name} in time"`` is stored.
        * If the call raises a transient error (network, HTTP 5xx) it is
          retried **once**.  Auth errors are never retried.
        * All invocations are logged with tool name, duration, and
          success/failure.

        Before the sequential loop, any currently healthy/permitted read-only
        candidates among *tools* are speculatively prefetched concurrently
        under a strict bounded budget (US-101) while this turn's cancellation
        is still observed. Each candidate's speculative payload is discarded
        unless it revalidates identity/scope/permission/freshness immediately
        before use; anything ineligible (mutating, sensitive, disabled,
        unauthorized) always falls through to the single ordinary dispatch
        call below and is never spuriously called twice.

        Args:
            tools:   Tools to execute (from :meth:`select_tools`).
            message: The user message passed as ``transcript`` kwarg.
            user_id: Active user identifier forwarded to each tool handler as
                     ``_user_id`` so that user-scoped tools (e.g. email) can
                     enforce per-user access control.
            scope:   Current turn's immutable data/authority scope (e.g.
                     ``TurnScope.USER``/``TurnScope.HOUSEHOLD`` value), used
                     to revalidate a speculative result before it is used.
            routing_prefetch: An in-flight handle from
                     :meth:`begin_speculative_prefetch`, started while routing
                     was still resolving. When absent, an ordinary blocking
                     prefetch is started here for *tools* instead (unchanged
                     legacy behavior for callers that never begin one early).

        Returns:
            Dict mapping tool name to its result (or error/timeout string).
        """
        granted_permissions = self._resolve_permissions_for_recovery(user_id, None)
        if routing_prefetch is not None:
            speculative_outcome = self._prefetcher.finish(routing_prefetch)
        else:
            eligible_names = [
                tool.name
                for tool in tools
                if self._prefetcher.eligible(
                    tool.name, user_id=user_id, granted_permissions=granted_permissions
                )
            ]
            speculative_outcome = None
            if eligible_names:
                speculative_outcome = self._prefetcher.prefetch(
                    eligible_names,
                    user_id=user_id or "",
                    scope=scope,
                    granted_permissions=granted_permissions,
                    args_by_capability={name: {"transcript": message} for name in eligible_names},
                )

        results: dict[str, Any] = {}
        for tool in tools:
            start = time.monotonic()
            consumed = None
            if speculative_outcome is not None:
                # Re-resolve permissions immediately before use rather than
                # trusting the snapshot taken before prefetch started.
                current_permissions = self._resolve_permissions_for_recovery(user_id, None)
                consumed = self._prefetcher.consume(
                    speculative_outcome.get(tool.name),
                    user_id=user_id or "",
                    scope=scope,
                    granted_permissions=current_permissions,
                )
            if consumed is not None:
                result = consumed
            else:
                result = self.dispatch(
                    tool.name,
                    {"transcript": message},
                    {"user_id": user_id} if user_id is not None else {},
                )
            duration = time.monotonic() - start
            logger.info(
                "tool_dispatcher: %r %.3fs %s%s",
                tool.name,
                duration,
                "ok" if result.success else result.status,
                " (speculative)" if consumed is not None else "",
            )
            if result.success:
                results[tool.name] = result.output
            elif result.error == "Execution timed out":
                results[tool.name] = f"I couldn't reach {tool.name} in time"
            else:
                results[tool.name] = (
                    f"[tool error: {result.detail or result.error or 'unknown error'}]"
                )
        return results

    def dispatch(
        self,
        name: str,
        args: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> Any:
        """Implements ``ToolDispatcherProtocol.dispatch()``.

        Looks up *name* in the registry and invokes its handler with *args*
        as keyword arguments.  Returns a ``ToolResult`` (from
        :mod:`rex.tools.protocol`).

        Args:
            name:    Tool name to invoke.
            args:    Keyword arguments forwarded to the handler.
            context: Optional execution context (not forwarded to handler;
                     reserved for future middleware use).

        Returns:
            ``ToolResult(success=True, output=...)`` on success or
            ``ToolResult(success=False, error=...)`` on failure / timeout.
        """
        from rex.tools.execution import ToolExecutionLifecycle
        from rex.tools.protocol import ToolResult  # local import — avoids circular dependency

        tool = self._registry.get(name)
        if tool is None:
            return ToolResult(success=False, error=f"Unknown tool: {name!r}")

        from rex.mobile_api.action_context import authorized_mobile_tool  # noqa: PLC0415

        available = self._config is None or tool in self._registry.available_tools(self._config)
        timeout_seconds = self._timeout_seconds
        if context and context.get("speculative"):
            # A speculative candidate's own dispatch call must never outlive
            # the speculation budget that launched it (US-101): otherwise an
            # abandoned/timed-out candidate keeps running under the ordinary,
            # much longer dispatcher timeout and occupies the bounded
            # speculative worker pool well past its declared budget.
            override = context.get("_speculative_timeout_seconds")
            if isinstance(override, int | float) and override > 0:
                timeout_seconds = min(timeout_seconds, float(override))
        with authorized_mobile_tool(
            tool.name,
            capability_tags=tool.capability_tags,
            operation=getattr(tool, "operation", None),
            arguments=args,
        ):
            return ToolExecutionLifecycle().execute(
                tool,
                args,
                context,
                timeout_seconds=timeout_seconds,
                available=available,
                runtime_config=self._config,
            )

    @staticmethod
    def format_tool_context(results: dict[str, Any]) -> str:
        """Format *results* dict as a context block for the LLM prompt.

        Returns an empty string when *results* is empty.
        """
        if not results:
            return ""
        lines = ["[Tool results:"]
        for name, value in results.items():
            lines.append(f"  {name}: {value}")
        lines.append("]")
        return "\n".join(lines)
