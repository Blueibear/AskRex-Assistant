"""Typed, truthful execution lifecycle for local and OpenClaw tools."""

from __future__ import annotations

import concurrent.futures
import hashlib
import inspect
import json
import logging
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal, Protocol

from rex.actions.lifecycle import lifecycle_from_legacy_status, render_action_outcome
from rex.audit import LogEntry, get_audit_logger
from rex.identity import validate_user_id
from rex.runtime.cancellation import TurnCancelledError, current_turn_cancellation
from rex.tools.protocol import ToolResult

logger = logging.getLogger(__name__)

_TRANSIENT_TYPES = (
    TimeoutError,
    ConnectionError,
    ConnectionResetError,
    ConnectionRefusedError,
    ConnectionAbortedError,
    OSError,
)


def _is_transient_error(exc: BaseException) -> bool:
    """Return whether a read-only tool failure is safe to retry once."""
    if isinstance(exc, _TRANSIENT_TYPES):
        return True
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if isinstance(status, int) and status >= 500:
        return True
    return bool(getattr(exc, "is_transient", False))


def _is_auth_error(exc: BaseException) -> bool:
    """Return whether a failure is authentication or authorization related."""
    if isinstance(exc, PermissionError):
        return True
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if isinstance(status, int) and status in (401, 403):
        return True
    return bool(getattr(exc, "auth_error", False))


class ToolOperation(StrEnum):
    READ = "read"
    MUTATION = "mutation"


class ToolRisk(StrEnum):
    SAFE = "safe"
    SENSITIVE = "sensitive"
    PROHIBITED = "prohibited"


class ToolOutcome(StrEnum):
    COMPLETED = "completed"
    VERIFIED = "verified"
    ATTEMPTED_UNVERIFIED = "attempted_unverified"
    CONFIRMATION_REQUIRED = "confirmation_required"
    DENIED = "denied"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"


class ExecutableTool(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def handler(self) -> Callable[..., Any]: ...

    @property
    def operation(self) -> Literal["read", "mutation"]: ...

    @property
    def risk(self) -> Literal["safe", "sensitive", "prohibited"]: ...

    @property
    def requires_identity(self) -> bool: ...

    @property
    def required_args(self) -> tuple[str, ...]: ...

    @property
    def verifier(self) -> Callable[[dict[str, Any], Any], bool] | None: ...


@dataclass(frozen=True)
class ToolExecutionRequest:
    name: str
    args: dict[str, Any]
    context: dict[str, Any]
    request_id: str


_dedupe_lock = threading.Lock()
_dedupe_results: dict[tuple[str, str, str], tuple[str, ToolResult]] = {}


def _fingerprint(args: dict[str, Any]) -> str:
    encoded = json.dumps(args, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


class ToolExecutionLifecycle:
    """Run the ordered availability-to-audit contract for one registered tool."""

    def execute(
        self,
        tool: ExecutableTool,
        args: dict[str, Any],
        context: dict[str, Any] | None = None,
        *,
        timeout_seconds: float = 10.0,
        available: bool = True,
        runtime_config: Any = None,
    ) -> ToolResult:
        ambient = dict(context or {})
        request_id = str(
            ambient.get("request_id") or args.get("_request_id") or f"tool_{uuid.uuid4().hex}"
        )
        request = ToolExecutionRequest(tool.name, dict(args), ambient, request_id)
        started = time.monotonic()
        stages: list[str] = ["capability_availability"]
        operation = ToolOperation(tool.operation)
        risk = ToolRisk(tool.risk)
        # Speculative candidates (US-101) may only retain content-free
        # metadata/timing; a failing read's raw exception text can embed
        # private request/result payload content and must never reach logs
        # or the persisted audit trail for this call.
        is_speculative = bool(ambient.get("speculative"))
        cancellation = current_turn_cancellation()
        if cancellation is not None:
            cancellation.raise_if_cancelled()

        if not available:
            required_config = tuple(getattr(tool, "requires_config", ()) or ())
            missing_config = tuple(
                key
                for key in required_config
                if runtime_config is None
                or not (
                    runtime_config.get(key)
                    if isinstance(runtime_config, dict)
                    else getattr(runtime_config, key, None)
                )
            )
            next_action = (
                f" Required Rex config key(s): {', '.join(missing_config)}. "
                "Configure them through the existing settings/credential source, then retry."
                if missing_config
                else " Enable or configure the tool, then retry."
            )
            return self._finish(
                request,
                ToolOutcome.UNAVAILABLE,
                risk,
                stages,
                started,
                error=f"Tool is not configured.{next_action}",
            )

        stages.append("argument_validation")
        missing = [name for name in tool.required_args if args.get(name) in (None, "")]
        if missing:
            return self._finish(
                request,
                ToolOutcome.DENIED,
                risk,
                stages,
                started,
                error=(
                    f"Missing required arguments: {', '.join(missing)}. "
                    "Provide the missing value(s), then retry."
                ),
            )

        user_id = ""
        stages.append("identity_validation")
        if tool.requires_identity or operation == ToolOperation.MUTATION:
            candidate = ambient.get("user_id") or ambient.get("user") or args.get("_user_id")
            try:
                user_id = validate_user_id(str(candidate or ""))
            except ValueError:
                return self._finish(
                    request,
                    ToolOutcome.DENIED,
                    risk,
                    stages,
                    started,
                    error="A valid user identity is required",
                )

        stages.extend(("permission_evaluation", "risk_classification"))
        permitted_users = ambient.get("permitted_users")
        if permitted_users is not None and user_id not in set(permitted_users):
            return self._finish(
                request,
                ToolOutcome.DENIED,
                risk,
                stages,
                started,
                error=(
                    "User is not permitted to execute this tool. Ask an administrator to grant "
                    "access to this Rex profile, then retry."
                ),
            )

        required_permissions = set(getattr(tool, "required_permissions", ()) or ())
        if required_permissions:
            raw_permissions = ambient.get("granted_permissions")
            if raw_permissions is None:
                try:
                    from rex.mobile_api.action_context import (
                        current_mobile_action_context,
                    )  # noqa: PLC0415

                    mobile_context = current_mobile_action_context()
                except Exception:
                    mobile_context = None
                if mobile_context is not None:
                    raw_permissions = mobile_context.permissions
                elif user_id:
                    try:
                        from rex.permissions import get_permissions  # noqa: PLC0415

                        raw_permissions = get_permissions(user_id)
                    except Exception:
                        logger.exception(
                            "tool_execution: failed to resolve permissions for user %r", user_id
                        )
                        raw_permissions = ()
            granted_permissions = set(raw_permissions or ())
            missing_permissions = required_permissions - granted_permissions
            if "admin" not in granted_permissions and missing_permissions:
                return self._finish(
                    request,
                    ToolOutcome.DENIED,
                    risk,
                    stages,
                    started,
                    error=(
                        "Required user permission is not granted. Ask an administrator to grant "
                        f"{', '.join(sorted(missing_permissions))} to this Rex profile, then retry."
                    ),
                )

        if risk == ToolRisk.PROHIBITED:
            return self._finish(
                request,
                ToolOutcome.DENIED,
                risk,
                stages,
                started,
                error="Tool policy prohibits this operation",
            )

        stages.append("confirmation")
        if risk == ToolRisk.SENSITIVE and not ambient.get("confirmed"):
            return self._finish(request, ToolOutcome.CONFIRMATION_REQUIRED, risk, stages, started)

        dedupe_key = (user_id, tool.name, request_id)
        args_fingerprint = _fingerprint(args)

        def cancelled_mutation_result() -> ToolResult:
            result = self._finish(
                request,
                ToolOutcome.ATTEMPTED_UNVERIFIED,
                risk,
                stages,
                started,
                error="Cancellation observed after a possible write; outcome is unverified",
            )
            with _dedupe_lock:
                _dedupe_results[dedupe_key] = (args_fingerprint, result)
            return result

        if operation == ToolOperation.MUTATION:
            with _dedupe_lock:
                prior = _dedupe_results.get(dedupe_key)
            if prior is not None:
                if prior[0] != args_fingerprint:
                    return self._finish(
                        request,
                        ToolOutcome.DENIED,
                        risk,
                        stages,
                        started,
                        error="request_id was already used with different arguments",
                    )
                return prior[1]

        stages.append("execution")
        handler_args = dict(args)
        handler_context = {**ambient, "request_id": request_id}
        retain_speculative_worker = is_speculative and bool(
            ambient.get("_speculative_retain_worker")
        )
        speculative_deadline = ambient.get("_speculative_deadline_monotonic")
        if retain_speculative_worker and isinstance(speculative_deadline, int | float):
            # Cooperative handlers can observe the same deadline/cancellation
            # that bounds their owning speculative worker.  Uncooperative
            # handlers cannot be force-killed safely in Python, so they stay
            # counted in that fixed-size worker pool until they return.
            handler_context["deadline_monotonic"] = float(speculative_deadline)
            if cancellation is not None:
                handler_context["cancellation"] = cancellation
        if user_id:
            handler_context["user_id"] = user_id
        signature = inspect.signature(tool.handler)
        accepts_kwargs = any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        )
        if "context" in signature.parameters or accepts_kwargs:
            handler_args.setdefault("context", handler_context)
        if user_id and ("_user_id" in signature.parameters or accepts_kwargs):
            handler_args.setdefault("_user_id", user_id)
        if ambient.get("confirmed") and ("confirmed" in signature.parameters or accepts_kwargs):
            handler_args.setdefault("confirmed", True)
        if runtime_config is not None and "_runtime_config" in signature.parameters:
            handler_args.setdefault("_runtime_config", runtime_config)
        attempts = 2 if operation == ToolOperation.READ else 1
        for attempt in range(attempts):
            if cancellation is not None:
                cancellation.raise_if_cancelled()
            executor: concurrent.futures.ThreadPoolExecutor | None = None
            # A speculative task may have waited in its bounded outer pool
            # before reaching this lifecycle.  Preserve the original handle
            # deadline when supplied instead of granting a fresh timeout here.
            deadline = time.monotonic() + timeout_seconds
            if retain_speculative_worker and isinstance(
                speculative_deadline, int | float
            ):
                deadline = min(deadline, float(speculative_deadline))
            try:
                if retain_speculative_worker:
                    output = tool.handler(**handler_args)
                    if cancellation is not None and cancellation.cancelled:
                        if operation == ToolOperation.MUTATION:
                            return cancelled_mutation_result()
                        cancellation.raise_if_cancelled()
                    if time.monotonic() > deadline:
                        raise concurrent.futures.TimeoutError
                else:
                    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
                    future = executor.submit(tool.handler, **handler_args)
                    while True:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise concurrent.futures.TimeoutError
                        try:
                            output = future.result(timeout=min(0.05, remaining))
                        except concurrent.futures.TimeoutError:
                            if cancellation is not None and cancellation.cancelled:
                                future.cancel()
                                if operation == ToolOperation.MUTATION:
                                    return cancelled_mutation_result()
                                cancellation.raise_if_cancelled()
                            continue
                        if cancellation is not None and cancellation.cancelled:
                            if operation == ToolOperation.MUTATION:
                                return cancelled_mutation_result()
                            cancellation.raise_if_cancelled()
                        break
                break
            except concurrent.futures.TimeoutError:
                if executor is not None:
                    future.cancel()
                outcome = (
                    ToolOutcome.ATTEMPTED_UNVERIFIED
                    if operation == ToolOperation.MUTATION
                    else ToolOutcome.FAILED
                )
                result = self._finish(
                    request,
                    outcome,
                    risk,
                    stages,
                    started,
                    error=(
                        "Execution timed out after a possible write"
                        if operation == ToolOperation.MUTATION
                        else "Execution timed out"
                    ),
                )
                if operation == ToolOperation.MUTATION:
                    with _dedupe_lock:
                        _dedupe_results[dedupe_key] = (args_fingerprint, result)
                return result
            except TurnCancelledError:
                if executor is not None:
                    future.cancel()
                if operation == ToolOperation.MUTATION:
                    return cancelled_mutation_result()
                raise
            except Exception as exc:
                if cancellation is not None and cancellation.cancelled:
                    if executor is not None:
                        future.cancel()
                    if operation == ToolOperation.MUTATION:
                        return cancelled_mutation_result()
                    cancellation.raise_if_cancelled()
                if operation == ToolOperation.MUTATION and bool(
                    getattr(exc, "outcome_unknown", False)
                ):
                    stages.extend(("normalized_result", "independent_verification"))
                    verified = False
                    unknown_detail = (
                        "The remote mutation may have been dispatched, but its outcome is "
                        "unknown after a transport failure."
                    )
                    if tool.verifier is not None:
                        try:
                            verified = bool(tool.verifier(args, None))
                        except Exception as verify_exc:
                            unknown_detail = (
                                "Verification failed after an uncertain remote outcome: "
                                f"{verify_exc}"
                            )
                    outcome = ToolOutcome.VERIFIED if verified else ToolOutcome.ATTEMPTED_UNVERIFIED
                    stages.append("truthful_response")
                    result = self._finish(
                        request,
                        outcome,
                        risk,
                        stages,
                        started,
                        detail=None if verified else unknown_detail,
                    )
                    with _dedupe_lock:
                        _dedupe_results[dedupe_key] = (args_fingerprint, result)
                    return result
                if (
                    operation == ToolOperation.READ
                    and attempt == 0
                    and _is_transient_error(exc)
                    and not _is_auth_error(exc)
                ):
                    if is_speculative:
                        logger.debug(
                            "tool_execution: speculative %r transient read failure "
                            "(%s); retrying once",
                            tool.name,
                            type(exc).__name__,
                        )
                    else:
                        logger.debug(
                            "tool_execution: %r transient read failure; retrying once: %s",
                            tool.name,
                            exc,
                        )
                    continue
                return self._finish(
                    request,
                    ToolOutcome.FAILED,
                    risk,
                    stages,
                    started,
                    error=(
                        f"Speculative read failed: {type(exc).__name__}"
                        if is_speculative
                        else str(exc)
                    ),
                )
            finally:
                if executor is not None:
                    executor.shutdown(wait=False, cancel_futures=True)

        stages.append("normalized_result")
        normalized = self._normalize_handler_result(output)
        stages.append("independent_verification")
        positive_mutation_claim = (
            operation == ToolOperation.MUTATION
            and normalized is not None
            and normalized[0] in {ToolOutcome.COMPLETED, ToolOutcome.VERIFIED}
        )
        if normalized is not None and not positive_mutation_claim:
            outcome, detail = normalized
        elif operation == ToolOperation.READ:
            outcome, detail = ToolOutcome.COMPLETED, None
        elif tool.verifier is None:
            outcome = ToolOutcome.ATTEMPTED_UNVERIFIED
            detail = (
                f"Tool reported: {output}. The mutation was attempted, "
                "but the result was not independently verified."
            )
        else:
            try:
                verified = bool(tool.verifier(args, output))
            except Exception as exc:
                verified = False
                detail = f"Verification failed: {exc}"
            else:
                detail = None
            outcome = ToolOutcome.VERIFIED if verified else ToolOutcome.ATTEMPTED_UNVERIFIED

        stages.append("truthful_response")
        result = self._finish(request, outcome, risk, stages, started, output=output, detail=detail)
        if operation == ToolOperation.MUTATION:
            with _dedupe_lock:
                _dedupe_results[dedupe_key] = (args_fingerprint, result)
        return result

    @staticmethod
    def _normalize_handler_result(output: Any) -> tuple[ToolOutcome, str | None] | None:
        if isinstance(output, str) and output.startswith("["):
            if "not configured" in output.lower():
                return ToolOutcome.UNAVAILABLE, output
            if "error" in output.lower():
                return ToolOutcome.FAILED, output
        if not isinstance(output, dict):
            return None
        if output.get("requires_confirmation") is True:
            return ToolOutcome.CONFIRMATION_REQUIRED, str(
                output.get("message") or "Explicit confirmation is required"
            )
        if output.get("ok") is False or output.get("success") is False or output.get("error"):
            return ToolOutcome.FAILED, str(output.get("error") or output.get("message") or "failed")
        raw_status = output.get("status")
        if raw_status not in {item.value for item in ToolOutcome}:
            return None
        return ToolOutcome(str(raw_status)), str(
            output.get("detail") or output.get("message") or ""
        )

    def _finish(
        self,
        request: ToolExecutionRequest,
        outcome: ToolOutcome,
        risk: ToolRisk,
        stages: list[str],
        started: float,
        *,
        output: Any = None,
        detail: str | None = None,
        error: str | None = None,
    ) -> ToolResult:
        stages.append("audit_recording")
        plan_id = request.context.get("plan_id") or request.context.get("task_id")
        lifecycle = lifecycle_from_legacy_status(
            outcome.value,
            action_id=request.request_id,
            plan_id=str(plan_id) if plan_id else None,
        )
        rendered = render_action_outcome(
            lifecycle,
            request.name,
            detail=detail or error,
        )
        result = ToolResult(
            success=lifecycle.success,
            output=output,
            error=error,
            status=outcome.value,
            detail=rendered,
            request_id=request.request_id,
            risk=risk.value,
            stages=tuple(stages),
            lifecycle=lifecycle,
        )
        try:
            get_audit_logger().log(
                LogEntry(
                    action_id=request.request_id,
                    task_id=str(plan_id) if plan_id else None,
                    tool=request.name,
                    tool_call_args={
                        "argument_names": sorted(request.args),
                        "arguments_hash": _fingerprint(request.args),
                    },
                    policy_decision=(
                        "denied"
                        if outcome in {ToolOutcome.DENIED, ToolOutcome.UNAVAILABLE}
                        else "allowed"
                    ),
                    tool_result={
                        "status": result.status,
                        "success": result.success,
                        "risk": result.risk,
                        "lifecycle": lifecycle.to_dict(),
                    },
                    error=error,
                    requested_by=str(
                        request.context.get("user_id") or request.context.get("user") or ""
                    ),
                    duration_ms=int((time.monotonic() - started) * 1000),
                )
            )
        except Exception:
            logger.exception("Tool audit recording failed for %s", request.name)
        return result


__all__ = [
    "ToolExecutionLifecycle",
    "ToolExecutionRequest",
    "ToolOperation",
    "ToolOutcome",
    "ToolRisk",
]
