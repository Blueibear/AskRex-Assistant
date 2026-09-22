"""OpenClaw tool executor — replaces rex/tool_router.py (US-P7-008).

All tool calls are evaluated by the policy engine before execution to determine
whether they should auto-execute, require approval, or be denied.

Tool executions are automatically logged to the audit log for accountability
and traceability. Sensitive data is redacted before being written to the log.

Tools are registered in the ToolRegistry, which provides metadata and health
checks. Before execution, required credentials are validated via the
CredentialManager.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from rex.actions.lifecycle import lifecycle_from_legacy_status
from rex.audit import LogEntry, get_audit_logger
from rex.contracts import ToolCall
from rex.geolocation import get_cached_city, get_cached_timezone
from rex.openclaw.tool_registry import (
    ToolRegistry,
    get_tool_registry,
)
from rex.policy_engine import PolicyEngine, get_policy_engine

logger = logging.getLogger(__name__)

TOOL_REQUEST_PREFIX = "TOOL_REQUEST:"
TOOL_RESULT_PREFIX = "TOOL_RESULT:"

# Tools the free-text ``TOOL_REQUEST:`` convention may invoke. This must stay
# in sync with the tools documented to the model in
# ``rex.context.builder._TOOL_INSTRUCTIONS``. A model-emitted tool name
# outside this set was never taught to the model as callable through this
# convention; being registered elsewhere (e.g. for deterministic/authorized
# dispatch such as ``media_read``) does not authorize a spurious or
# unsupported model-emitted call here (TEST-010).
TEXT_TOOL_REQUEST_ALLOWED_TOOLS = frozenset({"time_now", "weather_now", "web_search"})


@dataclass(frozen=True)
class ToolError:
    message: str


class PolicyDeniedError(Exception):
    """Raised when a tool call is denied by the policy engine."""

    def __init__(self, tool: str, reason: str) -> None:
        self.tool = tool
        self.reason = reason
        super().__init__(f"Tool '{tool}' denied by policy: {reason}")


class ApprovalRequiredError(Exception):
    """Raised when a tool call requires user approval.

    This exception is raised when the policy engine determines that a tool
    call requires approval before execution. The actual approval flow should
    be handled by the caller.
    """

    def __init__(self, tool: str, reason: str) -> None:
        self.tool = tool
        self.reason = reason
        super().__init__(f"Tool '{tool}' requires approval: {reason}")


class CredentialMissingError(Exception):
    """Raised when required credentials are not configured for a tool.

    This exception signals that the user needs to configure credentials
    before the tool can be executed.
    """

    def __init__(self, tool: str, missing_credentials: list[str]) -> None:
        self.tool = tool
        self.missing_credentials = missing_credentials
        creds = ", ".join(missing_credentials)
        super().__init__(
            f"Tool '{tool}' requires credentials that are not configured: {creds}. "
            f"Please configure: {creds}"
        )


def parse_tool_request(text: str) -> dict[str, Any] | None:
    """Return parsed tool request data or None if not a valid request."""
    return _parse_tool_request(text, allow_trailing_text=False)


def _parse_tool_request(
    text: str,
    *,
    allow_trailing_text: bool,
) -> dict[str, Any] | None:
    if not isinstance(text, str):
        return None
    if "\n" in text or "\r" in text:
        return None
    line = text.strip()
    if not line.startswith(TOOL_REQUEST_PREFIX):
        return None

    json_payload = line[len(TOOL_REQUEST_PREFIX) :].strip()
    if not json_payload:
        return None

    try:
        data = json.loads(json_payload)
    except json.JSONDecodeError:
        try:
            data, end = json.JSONDecoder().raw_decode(json_payload)
        except json.JSONDecodeError:
            return None
        trailing = json_payload[end:].strip()
        if trailing and not (allow_trailing_text or trailing in {".", "!", "?"}):
            return None

    if not isinstance(data, dict):
        return None

    tool = data.get("tool")
    args = data.get("args")

    if not isinstance(tool, str) or not tool:
        return None

    if args is None:
        args = {}
    if not isinstance(args, dict):
        return None

    return {"tool": tool, "args": args}


def execute_tool(
    request: dict[str, Any],
    default_context: dict[str, Any],
    *,
    policy_engine: PolicyEngine | None = None,
    tool_registry: ToolRegistry | None = None,
    skip_policy_check: bool = False,
    skip_credential_check: bool = False,
    task_id: str | None = None,
    requested_by: str | None = None,
    skip_audit_log: bool = False,
) -> dict[str, Any]:
    """Execute a tool request and return a result dictionary.

    Before executing a tool, credentials are validated against the tool's
    requirements, and the policy engine is consulted to determine whether
    the action should proceed. If credentials are missing, a
    CredentialMissingError is raised. If the policy denies the action,
    a PolicyDeniedError is raised. If approval is required, an
    ApprovalRequiredError is raised.

    All tool executions (successful or failed) are logged to the audit log
    unless skip_audit_log is True.

    Args:
        request: Tool request dict with 'tool' and 'args' keys.
        default_context: Default context for tool execution.
        policy_engine: Optional policy engine instance. If not provided,
            uses the default singleton.
        tool_registry: Optional tool registry instance. If not provided,
            uses the default singleton.
        skip_policy_check: If True, skip the policy check. Use with caution.
        skip_credential_check: If True, skip credential validation.
        task_id: Optional task ID for audit logging.
        requested_by: Optional identifier of who requested the action.
        skip_audit_log: If True, skip audit logging. Use with caution.

    Returns:
        A result dictionary with the tool output or error.

    Raises:
        CredentialMissingError: If required credentials are not configured.
        PolicyDeniedError: If the policy engine denies the action.
        ApprovalRequiredError: If the action requires user approval.
    """
    if not isinstance(request, dict):
        return _error_result("Invalid tool request payload")

    tool = request.get("tool")
    args = request.get("args")
    if not isinstance(tool, str) or not tool:
        return _error_result("Invalid tool name")
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return _error_result("Invalid tool arguments", tool=tool, args={})

    from rex.mobile_api.action_context import authorize_mobile_action  # noqa: PLC0415

    authorize_mobile_action(None, f"openclaw:{tool}")

    if skip_policy_check:
        skip_credential_check = True

    # Generate action ID for audit logging
    action_id = f"act_{uuid.uuid4().hex[:12]}"
    policy_decision: Literal["allowed", "denied", "requires_approval"] = "allowed"
    start_time = time.monotonic()

    # Check policy before execution
    if not skip_policy_check:
        engine = policy_engine or get_policy_engine()
        tool_call = ToolCall(tool=tool, args=args)
        metadata = _extract_policy_metadata(args)
        decision = engine.decide(tool_call, metadata)

        if decision.denied:
            logger.warning("Policy denied tool=%s: %s", tool, decision.reason)
            policy_decision = "denied"
            # Log the denial before raising
            if not skip_audit_log:
                _log_audit_entry(
                    action_id=action_id,
                    task_id=task_id,
                    tool=tool,
                    args=args,
                    policy_decision=policy_decision,
                    result=None,
                    error=f"Policy denied: {decision.reason}",
                    start_time=start_time,
                    requested_by=requested_by,
                )
            raise PolicyDeniedError(tool, decision.reason)

        if decision.requires_approval:
            logger.info("Policy requires approval for tool=%s: %s", tool, decision.reason)
            policy_decision = "requires_approval"
            # Log the approval requirement before raising
            if not skip_audit_log:
                _log_audit_entry(
                    action_id=action_id,
                    task_id=task_id,
                    tool=tool,
                    args=args,
                    policy_decision=policy_decision,
                    result=None,
                    error=f"Requires approval: {decision.reason}",
                    start_time=start_time,
                    requested_by=requested_by,
                )
            raise ApprovalRequiredError(tool, decision.reason)

        logger.debug("Policy allowed tool=%s: %s", tool, decision.reason)

    # For tools outside the built-in set, check the canonical registry before
    # declaring "Unknown tool".  Tools registered via register_tool() or the
    # default registry are dispatched through ToolDispatcher further below.
    # Note: this built-in set governs execute_tool()'s general dispatch
    # validation and intentionally differs from TEXT_TOOL_REQUEST_ALLOWED_TOOLS,
    # which additionally restricts which tools the untrusted free-text
    # TOOL_REQUEST: convention may invoke (TEST-010); execute_tool() itself
    # remains usable for any canonically registered tool through explicit
    # authorized callers.
    _BUILTIN_TOOLS = {"time_now", "weather_now", "web_search"}
    if tool not in _BUILTIN_TOOLS:
        try:
            from rex.tools.registry import get_default_registry as _get_default_registry

            _canonical_tool = _get_default_registry().get(tool)
        except Exception:
            _canonical_tool = None

        if _canonical_tool is None:
            result = _error_result(f"Unknown tool {tool}", tool=tool, args=args)
            if not skip_audit_log:
                _log_audit_entry(
                    action_id=action_id,
                    task_id=task_id,
                    tool=tool,
                    args=args,
                    policy_decision=policy_decision,
                    result=result,
                    error=f"Unknown tool {tool}",
                    start_time=start_time,
                    requested_by=requested_by,
                )
            return result

    # Check credentials before execution
    if not skip_credential_check:
        registry = tool_registry or get_tool_registry()
        tool_meta = registry.get_tool(tool)
        if tool_meta is not None:
            all_available, missing = registry.check_credentials(tool)
            if not all_available:
                logger.warning("Missing credentials for tool=%s: %s", tool, ", ".join(missing))
                # Log the credential failure before raising
                if not skip_audit_log:
                    _log_audit_entry(
                        action_id=action_id,
                        task_id=task_id,
                        tool=tool,
                        args=args,
                        policy_decision="denied",
                        result=None,
                        error=f"Missing credentials: {', '.join(missing)}",
                        start_time=start_time,
                        requested_by=requested_by,
                    )
                raise CredentialMissingError(tool, missing)

    # Execute the tool
    error: str | None = None

    try:
        if tool == "time_now":
            result = _execute_time_now(args, default_context)
        elif tool == "weather_now":
            result = _execute_weather_now(args, default_context)
        elif tool == "web_search":
            result = _execute_web_search(args, default_context)
        else:
            # Delegate to canonical dispatcher for tools registered outside the
            # built-in set.  Built-ins are handled above to avoid the circular
            # path: canonical handler → time_tool.time_now → execute_tool().
            from rex.tools.dispatcher import ToolDispatcher
            from rex.tools.registry import get_default_registry as _get_default_registry

            dispatch_context = {
                **default_context,
                "request_id": action_id,
                "plan_id": task_id,
                "user_id": default_context.get("user_id")
                or default_context.get("user")
                or requested_by,
            }
            _tool_result = ToolDispatcher(_get_default_registry()).dispatch(
                tool, args, dispatch_context
            )
            result = {
                "success": _tool_result.success,
                "status": _tool_result.status,
                "detail": _tool_result.detail,
                "request_id": _tool_result.request_id,
                "risk": _tool_result.risk,
                "result": _tool_result.output,
                "lifecycle": (
                    _tool_result.lifecycle.to_dict() if _tool_result.lifecycle is not None else None
                ),
            }
            if _tool_result.error:
                result["error"] = _tool_result.error
                error = _tool_result.error
    except Exception as e:
        result = _error_result(str(e), tool=tool, args=args)
        error = str(e)

    if isinstance(result, dict) and not isinstance(result.get("lifecycle"), dict):
        legacy_status = str(
            result.get("status") or ("failed" if result.get("error") else "completed")
        )
        result["lifecycle"] = lifecycle_from_legacy_status(
            legacy_status, action_id=action_id, plan_id=task_id
        ).to_dict()

    # Log to audit
    if not skip_audit_log:
        _log_audit_entry(
            action_id=action_id,
            task_id=task_id,
            tool=tool,
            args=args,
            policy_decision=policy_decision,
            result=result,
            error=error,
            start_time=start_time,
            requested_by=requested_by,
        )

    return result


def _log_audit_entry(
    action_id: str,
    task_id: str | None,
    tool: str,
    args: dict[str, Any],
    policy_decision: Literal["allowed", "denied", "requires_approval"],
    result: dict[str, Any] | None,
    error: str | None,
    start_time: float,
    requested_by: str | None,
) -> None:
    """Log an audit entry for a tool execution.

    Args:
        action_id: Unique action identifier.
        task_id: Optional parent task ID.
        tool: Tool name.
        args: Tool arguments.
        policy_decision: Policy engine decision.
        result: Tool result (if any).
        error: Error message (if any).
        start_time: Monotonic time when execution started.
        requested_by: Who requested the action.
    """
    try:
        duration_ms = int((time.monotonic() - start_time) * 1000)
        entry = LogEntry(
            timestamp=datetime.now(UTC),
            action_id=action_id,
            task_id=task_id,
            tool=tool,
            tool_call_args=args,
            policy_decision=policy_decision,
            tool_result=result,
            error=error,
            requested_by=requested_by,
            duration_ms=duration_ms,
        )
        audit_logger = get_audit_logger()
        audit_logger.log(entry)
    except Exception as e:
        # Don't let audit logging failures break tool execution
        logger.error("Failed to log audit entry: %s", e)


def _extract_policy_metadata(args: dict[str, Any]) -> dict[str, Any]:
    """Extract metadata from tool arguments for policy evaluation.

    Looks for common argument patterns that indicate recipients or domains:
    - to, recipient, email: Email address
    - url, domain: Domain name
    - target: Generic target identifier

    Args:
        args: Tool arguments dictionary.

    Returns:
        Metadata dictionary for policy evaluation.
    """
    metadata: dict[str, Any] = {}

    # Check for recipient-like arguments
    for key in ("to", "recipient", "email", "address"):
        if key in args and isinstance(args[key], str):
            metadata["recipient"] = args[key]
            break

    # Check for domain-like arguments
    for key in ("domain", "host"):
        if key in args and isinstance(args[key], str):
            metadata["domain"] = args[key]
            break

    # Extract domain from URL if present
    if "url" in args and isinstance(args["url"], str):
        url = args["url"]
        # Simple domain extraction from URL
        if "://" in url:
            domain_part = url.split("://", 1)[1].split("/", 1)[0]
            # Remove port if present
            domain_part = domain_part.split(":")[0]
            metadata["domain"] = domain_part

    # Check for generic target
    if "target" in args and isinstance(args["target"], str):
        metadata["target"] = args["target"]

    return metadata


def format_tool_result(tool: str, args: dict[str, Any], result: dict[str, Any]) -> str:
    """Format the tool result as a single line message."""
    payload = {
        "tool": tool,
        "args": args,
        "result": result,
    }
    json_payload = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return f"{TOOL_RESULT_PREFIX} {json_payload}"


def route_if_tool_request(
    llm_text: str,
    default_context: dict[str, Any],
    model_call_fn: Callable[[dict[str, str]], str],
    *,
    policy_engine: PolicyEngine | None = None,
    skip_policy_check: bool = False,
) -> str:
    """Route a tool request if present and return the final model output.

    Args:
        llm_text: The LLM output text that may contain a tool request.
        default_context: Default context for tool execution.
        model_call_fn: Function to call the model with tool results.
        policy_engine: Optional policy engine instance for policy checks.
        skip_policy_check: If True, skip policy checks on tool execution.

    Returns:
        The final model output or an error message.
    """
    request = _parse_tool_request(llm_text, allow_trailing_text=True)
    if request is None:
        return llm_text

    tool = request.get("tool", "unknown")
    args = request.get("args", {})

    if tool not in TEXT_TOOL_REQUEST_ALLOWED_TOOLS:
        logger.warning(
            "Ignoring unsupported model-emitted tool request: tool=%s",
            tool,
            extra={"event": "tool_request_unsupported_tool_ignored"},
        )
        unsupported_result = _error_result(
            f"Tool '{tool}' is not available for a direct tool request.",
            tool=tool,
            args=args,
        )
        tool_result_line = format_tool_result(tool, args, unsupported_result)
        tool_message = {"role": "tool", "content": tool_result_line}
        try:
            return model_call_fn(tool_message)
        except Exception:
            return "Sorry, I could not complete that tool request."

    try:
        result = execute_tool(
            request,
            default_context,
            policy_engine=policy_engine,
            skip_policy_check=skip_policy_check,
        )
    except CredentialMissingError as e:
        logger.warning("Tool request missing credentials: %s", e)
        creds = ", ".join(e.missing_credentials)
        return f"I cannot execute that action. Missing credentials: {creds}. Please configure them first."
    except PolicyDeniedError as e:
        logger.warning("Tool request denied: %s", e)
        return f"I cannot execute that action: {e.reason}"
    except ApprovalRequiredError as e:
        logger.info("Tool request requires approval: %s", e)
        return f"This action requires your approval: {e.reason}"

    tool_result_line = format_tool_result(tool, args, result)
    tool_message = {"role": "tool", "content": tool_result_line}

    try:
        return model_call_fn(tool_message)
    except Exception:
        return "Sorry, I could not complete that tool request."


def _execute_time_now(args: dict[str, Any], default_context: dict[str, Any]) -> dict[str, Any]:
    requested_location = args.get("location")
    location = requested_location
    if not isinstance(location, str) or not location.strip():
        location = default_context.get("location")
        requested_location = None

    if not isinstance(location, str) or not location.strip():
        return _error_result("Missing required location for time_now", tool="time_now", args=args)

    location = location.strip()
    allow_default_context_fallback = requested_location is None or _same_location(
        location, default_context.get("location")
    )
    timezone = _resolve_timezone(
        location,
        default_context,
        allow_default_context_fallback=allow_default_context_fallback,
    )
    if isinstance(timezone, ToolError):
        return _error_result(timezone.message, tool="time_now", args=args)

    try:
        zone = ZoneInfo(timezone)
    except ZoneInfoNotFoundError:
        return _error_result(
            "Timezone database unavailable; install the required tzdata package",
            tool="time_now",
            args=args,
        )
    except (TypeError, ValueError):
        return _error_result("Invalid timezone for time_now", tool="time_now", args=args)

    now = datetime.now(tz=zone)
    return {
        "local_time": now.strftime("%Y-%m-%d %H:%M"),
        "date": now.strftime("%Y-%m-%d"),
        "timezone": timezone,
    }


def _execute_weather_now(args: dict[str, Any], default_context: dict[str, Any]) -> dict[str, Any]:
    from rex.weather import get_weather

    location = args.get("location")
    if not isinstance(location, str) or not location.strip():
        location = default_context.get("location")
    if not isinstance(location, str) or not location.strip():
        location = get_cached_city()
    if not isinstance(location, str) or not location.strip():
        return _error_result(
            "Missing required location for weather_now", tool="weather_now", args=args
        )

    location = location.strip()
    from rex.credentials import get_persisted_credential, legacy_plaintext_fallback_enabled

    api_key = (
        os.getenv("OPENWEATHERMAP_API_KEY", "") if legacy_plaintext_fallback_enabled() else ""
    ) or (get_persisted_credential("OPENWEATHERMAP_API_KEY") or "")
    if not api_key:
        return _error_result(
            "OPENWEATHERMAP_API_KEY is not configured", tool="weather_now", args=args
        )

    def _run_weather() -> dict[str, Any]:
        return asyncio.run(get_weather(location, api_key))

    try:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            weather = _run_weather()
        else:
            # This executor is synchronous API code. A nested event loop cannot
            # run in the same thread, so isolate the coroutine in one worker.
            with ThreadPoolExecutor(max_workers=1) as pool:
                weather = pool.submit(_run_weather).result()
    except Exception as exc:
        return _error_result(str(exc), tool="weather_now", args=args)

    if "error" in weather:
        return _error_result(weather["error"], tool="weather_now", args=args)

    return weather


def _execute_web_search(args: dict[str, Any], default_context: dict[str, Any]) -> dict[str, Any]:
    query = args.get("query") or args.get("q") or ""
    if not isinstance(query, str) or not query.strip():
        return _error_result("web_search requires a 'query' argument", tool="web_search", args=args)
    query = query.strip()
    try:
        from plugins.web_search import search_web

        result_text = search_web(query)
    except ImportError:
        return _error_result("web_search plugin is not installed", tool="web_search", args=args)
    except Exception as exc:
        return _error_result(str(exc), tool="web_search", args=args)
    if result_text is None:
        return {"query": query, "result": "No results found.", "success": False}
    return {"query": query, "result": result_text, "success": True}


_CITY_TIMEZONES: dict[str, str] = {
    # --- North America: United States ---
    "dallas": "America/Chicago",
    "dallas tx": "America/Chicago",
    "dallas texas": "America/Chicago",
    "dallas texas usa": "America/Chicago",
    "fort worth": "America/Chicago",
    "fort worth tx": "America/Chicago",
    "houston": "America/Chicago",
    "houston tx": "America/Chicago",
    "san antonio": "America/Chicago",
    "austin": "America/Chicago",
    "austin tx": "America/Chicago",
    "oklahoma city": "America/Chicago",
    "tulsa": "America/Chicago",
    "kansas city": "America/Chicago",
    "st louis": "America/Chicago",
    "saint louis": "America/Chicago",
    "st. louis": "America/Chicago",
    "memphis": "America/Chicago",
    "nashville": "America/Chicago",
    "new orleans": "America/Chicago",
    "milwaukee": "America/Chicago",
    "minneapolis": "America/Chicago",
    "chicago": "America/Chicago",
    "omaha": "America/Chicago",
    "wichita": "America/Chicago",
    "little rock": "America/Chicago",
    "birmingham al": "America/Chicago",
    "jackson ms": "America/Chicago",
    "new york": "America/New_York",
    "new york city": "America/New_York",
    "new york ny": "America/New_York",
    "new york new york": "America/New_York",
    "new york usa": "America/New_York",
    "nyc": "America/New_York",
    "manhattan": "America/New_York",
    "brooklyn": "America/New_York",
    "philadelphia": "America/New_York",
    "boston": "America/New_York",
    "washington": "America/New_York",
    "washington dc": "America/New_York",
    "atlanta": "America/New_York",
    "miami": "America/New_York",
    "orlando": "America/New_York",
    "tampa": "America/New_York",
    "jacksonville": "America/New_York",
    "charlotte": "America/New_York",
    "raleigh": "America/New_York",
    "baltimore": "America/New_York",
    "richmond": "America/New_York",
    "pittsburgh": "America/New_York",
    "cleveland": "America/New_York",
    "columbus": "America/New_York",
    "cincinnati": "America/New_York",
    "detroit": "America/Detroit",
    "louisville": "America/Kentucky/Louisville",
    "indianapolis": "America/Indiana/Indianapolis",
    "los angeles": "America/Los_Angeles",
    "la": "America/Los_Angeles",
    "san francisco": "America/Los_Angeles",
    "sf": "America/Los_Angeles",
    "san jose": "America/Los_Angeles",
    "san diego": "America/Los_Angeles",
    "seattle": "America/Los_Angeles",
    "portland": "America/Los_Angeles",
    "las vegas": "America/Los_Angeles",
    "sacramento": "America/Los_Angeles",
    "fresno": "America/Los_Angeles",
    "long beach": "America/Los_Angeles",
    "oakland": "America/Los_Angeles",
    "spokane": "America/Los_Angeles",
    "denver": "America/Denver",
    "salt lake city": "America/Denver",
    "albuquerque": "America/Denver",
    "colorado springs": "America/Denver",
    "boise": "America/Boise",
    "phoenix": "America/Phoenix",
    "tucson": "America/Phoenix",
    "mesa": "America/Phoenix",
    "anchorage": "America/Anchorage",
    "honolulu": "Pacific/Honolulu",
    "juneau": "America/Juneau",
    # --- North America: Canada ---
    "toronto": "America/Toronto",
    "montreal": "America/Toronto",
    "ottawa": "America/Toronto",
    "quebec city": "America/Toronto",
    "hamilton": "America/Toronto",
    "winnipeg": "America/Winnipeg",
    "regina": "America/Regina",
    "saskatoon": "America/Regina",
    "calgary": "America/Edmonton",
    "edmonton": "America/Edmonton",
    "vancouver": "America/Vancouver",
    "victoria": "America/Vancouver",
    "halifax": "America/Halifax",
    "st john's": "America/St_Johns",
    # --- North America: Mexico ---
    "mexico city": "America/Mexico_City",
    "guadalajara": "America/Mexico_City",
    "puebla": "America/Mexico_City",
    "monterrey": "America/Monterrey",
    "tijuana": "America/Tijuana",
    "ciudad juarez": "America/Ojinaga",
    "cancun": "America/Cancun",
    "merida": "America/Merida",
    "acapulco": "America/Mexico_City",
    # --- Central America & Caribbean ---
    "panama city": "America/Panama",
    "san jose costa rica": "America/Costa_Rica",
    "guatemala city": "America/Guatemala",
    "tegucigalpa": "America/Tegucigalpa",
    "managua": "America/Managua",
    "san salvador": "America/El_Salvador",
    "havana": "America/Havana",
    "santo domingo": "America/Santo_Domingo",
    "san juan": "America/Puerto_Rico",
    "kingston": "America/Jamaica",
    "port-au-prince": "America/Port-au-Prince",
    "nassau": "America/Nassau",
    # --- South America ---
    "bogota": "America/Bogota",
    "medellin": "America/Bogota",
    "lima": "America/Lima",
    "buenos aires": "America/Argentina/Buenos_Aires",
    "cordoba": "America/Argentina/Cordoba",
    "santiago": "America/Santiago",
    "sao paulo": "America/Sao_Paulo",
    "rio de janeiro": "America/Sao_Paulo",
    "rio": "America/Sao_Paulo",
    "brasilia": "America/Sao_Paulo",
    "belo horizonte": "America/Sao_Paulo",
    "caracas": "America/Caracas",
    "quito": "America/Guayaquil",
    "guayaquil": "America/Guayaquil",
    "la paz": "America/La_Paz",
    "asuncion": "America/Asuncion",
    "montevideo": "America/Montevideo",
    "georgetown": "America/Guyana",
    "paramaribo": "America/Paramaribo",
    "cayenne": "America/Cayenne",
    # --- Western Europe ---
    "london": "Europe/London",
    "paris": "Europe/Paris",
    "berlin": "Europe/Berlin",
    "madrid": "Europe/Madrid",
    "rome": "Europe/Rome",
    "amsterdam": "Europe/Amsterdam",
    "brussels": "Europe/Brussels",
    "vienna": "Europe/Vienna",
    "zurich": "Europe/Zurich",
    "geneva": "Europe/Zurich",
    "bern": "Europe/Zurich",
    "lisbon": "Europe/Lisbon",
    "dublin": "Europe/Dublin",
    "oslo": "Europe/Oslo",
    "stockholm": "Europe/Stockholm",
    "helsinki": "Europe/Helsinki",
    "copenhagen": "Europe/Copenhagen",
    "barcelona": "Europe/Madrid",
    "seville": "Europe/Madrid",
    "valencia": "Europe/Madrid",
    "milan": "Europe/Rome",
    "naples": "Europe/Rome",
    "turin": "Europe/Rome",
    "munich": "Europe/Berlin",
    "hamburg": "Europe/Berlin",
    "frankfurt": "Europe/Berlin",
    "cologne": "Europe/Berlin",
    "dusseldorf": "Europe/Berlin",
    "stuttgart": "Europe/Berlin",
    "rotterdam": "Europe/Amsterdam",
    "the hague": "Europe/Amsterdam",
    "antwerp": "Europe/Brussels",
    "lyon": "Europe/Paris",
    "marseille": "Europe/Paris",
    "edinburgh": "Europe/London",
    "manchester": "Europe/London",
    "birmingham": "Europe/London",
    "glasgow": "Europe/London",
    "liverpool": "Europe/London",
    "leeds": "Europe/London",
    "belfast": "Europe/London",
    "luxembourg": "Europe/Luxembourg",
    "reykjavik": "Atlantic/Reykjavik",
    # --- Central & Eastern Europe ---
    "warsaw": "Europe/Warsaw",
    "krakow": "Europe/Warsaw",
    "prague": "Europe/Prague",
    "budapest": "Europe/Budapest",
    "bucharest": "Europe/Bucharest",
    "athens": "Europe/Athens",
    "sofia": "Europe/Sofia",
    "belgrade": "Europe/Belgrade",
    "zagreb": "Europe/Zagreb",
    "ljubljana": "Europe/Ljubljana",
    "sarajevo": "Europe/Sarajevo",
    "skopje": "Europe/Skopje",
    "tirana": "Europe/Tirane",
    "podgorica": "Europe/Podgorica",
    "chisinau": "Europe/Chisinau",
    "riga": "Europe/Riga",
    "tallinn": "Europe/Tallinn",
    "vilnius": "Europe/Vilnius",
    "kyiv": "Europe/Kyiv",
    "kiev": "Europe/Kyiv",
    "minsk": "Europe/Minsk",
    # --- Russia & Post-Soviet ---
    "moscow": "Europe/Moscow",
    "st petersburg": "Europe/Moscow",
    "saint petersburg": "Europe/Moscow",
    "novosibirsk": "Asia/Novosibirsk",
    "yekaterinburg": "Asia/Yekaterinburg",
    "chelyabinsk": "Asia/Yekaterinburg",
    "omsk": "Asia/Omsk",
    "krasnoyarsk": "Asia/Krasnoyarsk",
    "vladivostok": "Asia/Vladivostok",
    "irkutsk": "Asia/Irkutsk",
    "almaty": "Asia/Almaty",
    "nur-sultan": "Asia/Almaty",
    "astana": "Asia/Almaty",
    "tashkent": "Asia/Tashkent",
    "baku": "Asia/Baku",
    "tbilisi": "Asia/Tbilisi",
    "yerevan": "Asia/Yerevan",
    "bishkek": "Asia/Bishkek",
    "dushanbe": "Asia/Dushanbe",
    "ashgabat": "Asia/Ashgabat",
    # --- Middle East ---
    "dubai": "Asia/Dubai",
    "abu dhabi": "Asia/Dubai",
    "riyadh": "Asia/Riyadh",
    "jeddah": "Asia/Riyadh",
    "mecca": "Asia/Riyadh",
    "medina": "Asia/Riyadh",
    "kuwait city": "Asia/Kuwait",
    "doha": "Asia/Qatar",
    "manama": "Asia/Bahrain",
    "muscat": "Asia/Muscat",
    "amman": "Asia/Amman",
    "beirut": "Asia/Beirut",
    "damascus": "Asia/Damascus",
    "baghdad": "Asia/Baghdad",
    "tehran": "Asia/Tehran",
    "jerusalem": "Asia/Jerusalem",
    "tel aviv": "Asia/Jerusalem",
    "sanaa": "Asia/Aden",
    "kabul": "Asia/Kabul",
    # --- South Asia ---
    "mumbai": "Asia/Kolkata",
    "bombay": "Asia/Kolkata",
    "delhi": "Asia/Kolkata",
    "new delhi": "Asia/Kolkata",
    "bangalore": "Asia/Kolkata",
    "bengaluru": "Asia/Kolkata",
    "kolkata": "Asia/Kolkata",
    "calcutta": "Asia/Kolkata",
    "chennai": "Asia/Kolkata",
    "madras": "Asia/Kolkata",
    "hyderabad": "Asia/Kolkata",
    "ahmedabad": "Asia/Kolkata",
    "pune": "Asia/Kolkata",
    "surat": "Asia/Kolkata",
    "jaipur": "Asia/Kolkata",
    "lucknow": "Asia/Kolkata",
    "kanpur": "Asia/Kolkata",
    "karachi": "Asia/Karachi",
    "lahore": "Asia/Karachi",
    "islamabad": "Asia/Karachi",
    "rawalpindi": "Asia/Karachi",
    "dhaka": "Asia/Dhaka",
    "chittagong": "Asia/Dhaka",
    "colombo": "Asia/Colombo",
    "kathmandu": "Asia/Kathmandu",
    "thimphu": "Asia/Thimphu",
    "male": "Indian/Maldives",
    # --- East Asia ---
    "tokyo": "Asia/Tokyo",
    "osaka": "Asia/Tokyo",
    "kyoto": "Asia/Tokyo",
    "nagoya": "Asia/Tokyo",
    "yokohama": "Asia/Tokyo",
    "sapporo": "Asia/Tokyo",
    "kobe": "Asia/Tokyo",
    "beijing": "Asia/Shanghai",
    "shanghai": "Asia/Shanghai",
    "guangzhou": "Asia/Shanghai",
    "shenzhen": "Asia/Shanghai",
    "chengdu": "Asia/Shanghai",
    "wuhan": "Asia/Shanghai",
    "chongqing": "Asia/Shanghai",
    "tianjin": "Asia/Shanghai",
    "nanjing": "Asia/Shanghai",
    "xi'an": "Asia/Shanghai",
    "xian": "Asia/Shanghai",
    "hong kong": "Asia/Hong_Kong",
    "taipei": "Asia/Taipei",
    "seoul": "Asia/Seoul",
    "busan": "Asia/Seoul",
    "ulaanbaatar": "Asia/Ulaanbaatar",
    "pyongyang": "Asia/Pyongyang",
    # --- Southeast Asia ---
    "singapore": "Asia/Singapore",
    "bangkok": "Asia/Bangkok",
    "kuala lumpur": "Asia/Kuala_Lumpur",
    "jakarta": "Asia/Jakarta",
    "surabaya": "Asia/Jakarta",
    "manila": "Asia/Manila",
    "hanoi": "Asia/Ho_Chi_Minh",
    "ho chi minh city": "Asia/Ho_Chi_Minh",
    "saigon": "Asia/Ho_Chi_Minh",
    "phnom penh": "Asia/Phnom_Penh",
    "vientiane": "Asia/Vientiane",
    "yangon": "Asia/Rangoon",
    "rangoon": "Asia/Rangoon",
    "naypyidaw": "Asia/Rangoon",
    "dili": "Asia/Dili",
    "bandar seri begawan": "Asia/Brunei",
    # --- Africa ---
    "cairo": "Africa/Cairo",
    "alexandria": "Africa/Cairo",
    "lagos": "Africa/Lagos",
    "abuja": "Africa/Lagos",
    "kano": "Africa/Lagos",
    "nairobi": "Africa/Nairobi",
    "mombasa": "Africa/Nairobi",
    "johannesburg": "Africa/Johannesburg",
    "cape town": "Africa/Johannesburg",
    "durban": "Africa/Johannesburg",
    "pretoria": "Africa/Johannesburg",
    "addis ababa": "Africa/Addis_Ababa",
    "khartoum": "Africa/Khartoum",
    "dar es salaam": "Africa/Dar_es_Salaam",
    "casablanca": "Africa/Casablanca",
    "rabat": "Africa/Casablanca",
    "accra": "Africa/Accra",
    "dakar": "Africa/Dakar",
    "abidjan": "Africa/Abidjan",
    "lusaka": "Africa/Lusaka",
    "harare": "Africa/Harare",
    "maputo": "Africa/Maputo",
    "tunis": "Africa/Tunis",
    "algiers": "Africa/Algiers",
    "tripoli": "Africa/Tripoli",
    "kinshasa": "Africa/Kinshasa",
    "kampala": "Africa/Kampala",
    "mogadishu": "Africa/Mogadishu",
    "luanda": "Africa/Luanda",
    "bamako": "Africa/Bamako",
    "conakry": "Africa/Conakry",
    "freetown": "Africa/Freetown",
    "ouagadougou": "Africa/Ouagadougou",
    "niamey": "Africa/Niamey",
    "ndjamena": "Africa/Ndjamena",
    "kigali": "Africa/Kigali",
    "bujumbura": "Africa/Bujumbura",
    "lilongwe": "Africa/Blantyre",
    "antananarivo": "Indian/Antananarivo",
    "port louis": "Indian/Mauritius",
    # --- Oceania ---
    "sydney": "Australia/Sydney",
    "melbourne": "Australia/Melbourne",
    "brisbane": "Australia/Brisbane",
    "perth": "Australia/Perth",
    "adelaide": "Australia/Adelaide",
    "canberra": "Australia/Sydney",
    "darwin": "Australia/Darwin",
    "hobart": "Australia/Hobart",
    "auckland": "Pacific/Auckland",
    "wellington": "Pacific/Auckland",
    "christchurch": "Pacific/Auckland",
    "suva": "Pacific/Fiji",
    "port moresby": "Pacific/Port_Moresby",
    "honiara": "Pacific/Guadalcanal",
    "apia": "Pacific/Apia",
    "nuku'alofa": "Pacific/Tongatapu",
}

_LOCATION_TRAILING_FILLERS: tuple[str, ...] = (
    "right now",
    "now",
    "today",
    "currently",
    "please",
    "for me",
    "at the moment",
)


def _normalize_location_for_timezone_lookup(location: str) -> str:
    normalized = " ".join(location.strip().lower().replace(",", " ").split())
    while True:
        updated = normalized
        for suffix in _LOCATION_TRAILING_FILLERS:
            suffix_text = f" {suffix}"
            if updated.endswith(suffix_text):
                updated = updated[: -len(suffix_text)].strip()
                break
        if updated == normalized:
            return normalized
        normalized = updated


def _same_location(left: str, right: object) -> bool:
    if not isinstance(right, str) or not right.strip():
        return False
    return _normalize_location_for_timezone_lookup(left) == _normalize_location_for_timezone_lookup(
        right
    )


def _resolve_timezone(
    location: str,
    default_context: dict[str, Any],
    *,
    allow_default_context_fallback: bool = True,
) -> str | ToolError:
    normalized = _normalize_location_for_timezone_lookup(location)
    alias_timezone = _CITY_TIMEZONES.get(normalized)
    if alias_timezone is not None:
        return alias_timezone

    # Fall back to default_timezone from config (passed via default_context)
    if allow_default_context_fallback:
        fallback = default_context.get("timezone")
        if isinstance(fallback, str) and fallback.strip():
            return fallback.strip()

        # Fall back to geolocation cache
        geo_tz = get_cached_timezone()
        if geo_tz is not None:
            return geo_tz

        return "UTC"

    return ToolError(f"Unknown timezone for location: {location}")


def _error_result(
    message: str,
    tool: str | None = None,
    args: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"message": message}
    if tool:
        payload["tool"] = tool
    if args is not None:
        payload["args"] = args
    return {"error": payload}


__all__ = [
    "parse_tool_request",
    "execute_tool",
    "format_tool_result",
    "route_if_tool_request",
    "PolicyDeniedError",
    "ApprovalRequiredError",
    "CredentialMissingError",
]
