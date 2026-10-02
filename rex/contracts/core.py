"""Core contract models for Rex.

This module defines the Pydantic v2 models that represent the internal
contracts used across all Rex components. These models provide:

- Type-safe data structures for tasks, actions, tool calls, and results
- Consistent serialization/deserialization across components
- Validation and documentation via JSON Schema export

All datetime fields serialize to ISO 8601 format for JSON compatibility.
"""

from __future__ import annotations

import copy
import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

# --- Enums ---


class EvidenceKind(StrEnum):
    """Types of evidence that can be attached to actions or results."""

    SCREENSHOT = "screenshot"
    LOG = "log"
    FILE = "file"
    LINK = "link"
    MESSAGE_ID = "message_id"
    EVENT_ID = "event_id"
    OTHER = "other"


class ApprovalStatus(StrEnum):
    """Status of an approval request."""

    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"


class RiskLevel(StrEnum):
    """Risk classification for actions."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class TaskStatus(StrEnum):
    """Status of a task in its lifecycle."""

    QUEUED = "queued"
    RUNNING = "running"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


class NotificationChannel(StrEnum):
    """Channels through which notifications can be delivered."""

    DASHBOARD = "dashboard"
    PUSH = "push"
    EMAIL = "email"
    HA_TTS = "ha_tts"
    SMS = "sms"
    OTHER = "other"


class NotificationPriority(StrEnum):
    """Priority levels for notifications."""

    URGENT = "urgent"
    NORMAL = "normal"
    DIGEST = "digest"


# --- Utility Functions ---


# Keys that should be redacted when logging
_SENSITIVE_KEY_PATTERN = re.compile(
    r"(token|access_token|refresh_token|authorization|password|secret|api_key)",
    re.IGNORECASE,
)

_REDACTED_VALUE = "[REDACTED]"


def redact_sensitive_keys(
    data: dict[str, Any] | list[Any] | Any,
    *,
    redacted_value: str = _REDACTED_VALUE,
) -> dict[str, Any] | list[Any] | Any:
    """Redact sensitive keys from a dictionary for safe logging.

    This function recursively traverses dictionaries and lists, replacing
    values whose keys match sensitive patterns (token, password, secret, etc.)
    with a redacted placeholder.

    Args:
        data: The data structure to redact. Can be a dict, list, or any value.
        redacted_value: The placeholder string to use for redacted values.
            Defaults to "[REDACTED]".

    Returns:
        A deep copy of the input with sensitive values replaced.
        Non-dict/list values are returned unchanged.

    Example:
        >>> redact_sensitive_keys(
        ...     {"api_key": "secret123",  # pragma: allowlist secret
        ...      "name": "test"})
        {'api_key': '[REDACTED]', 'name': 'test'}

        >>> redact_sensitive_keys(
        ...     {"nested": {"password": "hunter2"}}  # pragma: allowlist secret
        ... )
        {'nested': {'password': '[REDACTED]'}}
    """
    if isinstance(data, dict):
        result = {}
        for key, value in data.items():
            if isinstance(key, str) and _SENSITIVE_KEY_PATTERN.search(key):
                result[key] = redacted_value
            else:
                result[key] = redact_sensitive_keys(value, redacted_value=redacted_value)  # type: ignore[assignment]
        return result
    elif isinstance(data, list):
        return [redact_sensitive_keys(item, redacted_value=redacted_value) for item in data]
    else:
        return copy.deepcopy(data) if isinstance(data, (dict, list)) else data


def _utc_now() -> datetime:
    """Return the current UTC datetime."""
    return datetime.now(UTC)


# --- Models ---


class EvidenceRef(BaseModel):
    """Reference to evidence supporting an action or result.

    Evidence can be screenshots, log files, links, or other artifacts
    that provide context or proof of an action's execution.
    """

    evidence_id: str = Field(
        ...,
        description="Unique identifier for this evidence reference",
    )
    kind: Literal["screenshot", "log", "file", "link", "message_id", "event_id", "other"] = Field(
        ...,
        description="Type of evidence",
    )
    uri: str | None = Field(
        default=None,
        description="URI or path to the evidence resource",
    )
    sha256: str | None = Field(
        default=None,
        description="SHA-256 hash of the evidence content for integrity verification",
    )
    created_at: datetime = Field(
        default_factory=_utc_now,
        description="When the evidence was created or captured",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "evidence_id": "ev_001",
                    "kind": "screenshot",
                    "uri": "s3://bucket/screenshots/ev_001.png",
                    "sha256": "abc123...",
                    "created_at": "2024-01-15T10:30:00Z",
                }
            ]
        }
    }


class ToolCall(BaseModel):
    """A request to invoke a tool.

    Tool calls represent requests from the AI or user to execute
    a specific tool with given arguments.
    """

    tool: str = Field(
        ...,
        description="Name of the tool to invoke",
    )
    args: dict[str, Any] = Field(
        default_factory=dict,
        description="Arguments to pass to the tool",
    )
    requested_by: str | None = Field(
        default=None,
        description="Identifier of who/what requested this tool call (user, AI, scheduler)",
    )
    idempotency_key: str | None = Field(
        default=None,
        description="Optional key to ensure idempotent execution",
    )
    created_at: datetime = Field(
        default_factory=_utc_now,
        description="When the tool call was created",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "tool": "time_now",
                    "args": {"location": "Dallas, TX"},
                    "requested_by": "assistant",
                    "created_at": "2024-01-15T10:30:00Z",
                }
            ]
        }
    }


class ToolResult(BaseModel):
    """Result of a tool invocation.

    Contains the outcome of a tool call, including success/failure status,
    output data, any errors, and optional evidence.
    """

    tool: str = Field(
        ...,
        description="Name of the tool that was invoked",
    )
    ok: bool = Field(
        ...,
        description="Whether the tool execution succeeded",
    )
    output: dict[str, Any] | None = Field(
        default=None,
        description="Tool output data on success",
    )
    error: str | None = Field(
        default=None,
        description="Error message if the tool failed",
    )
    evidence: list[EvidenceRef] = Field(
        default_factory=list,
        description="Evidence collected during tool execution",
    )
    started_at: datetime | None = Field(
        default=None,
        description="When tool execution started",
    )
    finished_at: datetime | None = Field(
        default=None,
        description="When tool execution completed",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "tool": "time_now",
                    "ok": True,
                    "output": {"local_time": "2024-01-15 10:30", "timezone": "America/Chicago"},
                    "started_at": "2024-01-15T10:30:00Z",
                    "finished_at": "2024-01-15T10:30:01Z",
                }
            ]
        }
    }


class Approval(BaseModel):
    """An approval request and its resolution.

    Approvals gate high-risk actions, requiring human review before
    execution proceeds.
    """

    approval_id: str = Field(
        ...,
        description="Unique identifier for this approval",
    )
    status: Literal["pending", "approved", "denied", "expired"] = Field(
        ...,
        description="Current status of the approval",
    )
    reason: str | None = Field(
        default=None,
        description="Reason for the approval decision",
    )
    requested_by: str | None = Field(
        default=None,
        description="Who requested this approval",
    )
    decided_by: str | None = Field(
        default=None,
        description="Who made the approval decision",
    )
    requested_at: datetime = Field(
        default_factory=_utc_now,
        description="When the approval was requested",
    )
    decided_at: datetime | None = Field(
        default=None,
        description="When the approval decision was made",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "approval_id": "apr_001",
                    "status": "approved",
                    "reason": "Routine maintenance action",
                    "requested_by": "scheduler",
                    "decided_by": "james",
                    "requested_at": "2024-01-15T10:30:00Z",
                    "decided_at": "2024-01-15T10:35:00Z",
                }
            ]
        }
    }


class Action(BaseModel):
    """A discrete action within a task.

    Actions represent individual steps that Rex takes, such as tool calls,
    approval requests, or other operations. Each action tracks its risk
    level and associated data.
    """

    action_id: str = Field(
        ...,
        description="Unique identifier for this action",
    )
    task_id: str | None = Field(
        default=None,
        description="ID of the parent task, if any",
    )
    kind: str = Field(
        ...,
        description="Type of action (e.g., 'tool_call', 'approval_request', 'notification')",
    )
    risk: Literal["low", "medium", "high"] = Field(
        default="low",
        description="Risk level of this action",
    )
    tool_call: ToolCall | None = Field(
        default=None,
        description="Tool call details if this is a tool action",
    )
    tool_result: ToolResult | None = Field(
        default=None,
        description="Tool result if the tool has been executed",
    )
    approval: Approval | None = Field(
        default=None,
        description="Approval details if this action requires/required approval",
    )
    created_at: datetime = Field(
        default_factory=_utc_now,
        description="When this action was created",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "action_id": "act_001",
                    "task_id": "task_001",
                    "kind": "tool_call",
                    "risk": "low",
                    "tool_call": {
                        "tool": "time_now",
                        "args": {"location": "Dallas, TX"},
                        "created_at": "2024-01-15T10:30:00Z",
                    },
                    "created_at": "2024-01-15T10:30:00Z",
                }
            ]
        }
    }


class Task(BaseModel):
    """A task representing a unit of work in Rex.

    Tasks group related actions together and track overall progress.
    They can be created by users, the AI, or scheduled jobs.
    """

    task_id: str = Field(
        ...,
        description="Unique identifier for this task",
    )
    title: str = Field(
        ...,
        description="Human-readable title describing the task",
    )
    status: Literal["queued", "running", "blocked", "completed", "failed", "canceled"] = Field(
        default="queued",
        description="Current status of the task",
    )
    requested_by: str | None = Field(
        default=None,
        description="Who/what created this task",
    )
    created_at: datetime = Field(
        default_factory=_utc_now,
        description="When the task was created",
    )
    updated_at: datetime = Field(
        default_factory=_utc_now,
        description="When the task was last updated",
    )
    actions: list[Action] = Field(
        default_factory=list,
        description="Actions that are part of this task",
    )
    summary: str | None = Field(
        default=None,
        description="Optional summary or notes about the task outcome",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "task_id": "task_001",
                    "title": "Check current time in Dallas",
                    "status": "completed",
                    "requested_by": "user:james",
                    "created_at": "2024-01-15T10:30:00Z",
                    "updated_at": "2024-01-15T10:30:05Z",
                    "summary": "Successfully retrieved local time",
                }
            ]
        }
    }


class Notification(BaseModel):
    """A notification to be delivered to users.

    Notifications inform users about task status, required approvals,
    system events, or other important information.
    """

    notification_id: str = Field(
        ...,
        description="Unique identifier for this notification",
    )
    channel: Literal["dashboard", "push", "email", "ha_tts", "sms", "other"] = Field(
        ...,
        description="Delivery channel for the notification",
    )
    priority: Literal["urgent", "normal", "digest"] = Field(
        default="normal",
        description="Priority level affecting delivery timing",
    )
    title: str = Field(
        ...,
        description="Notification title/subject",
    )
    body: str = Field(
        ...,
        description="Notification body content",
    )
    created_at: datetime = Field(
        default_factory=_utc_now,
        description="When the notification was created",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional metadata for the notification",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "notification_id": "notif_001",
                    "channel": "dashboard",
                    "priority": "normal",
                    "title": "Task Completed",
                    "body": "Your time check request has been completed.",
                    "created_at": "2024-01-15T10:30:05Z",
                    "metadata": {"task_id": "task_001"},
                }
            ]
        }
    }


# List of all model classes for schema export
ALL_MODELS: list[type[BaseModel]] = [
    EvidenceRef,
    ToolCall,
    ToolResult,
    Approval,
    Action,
    Task,
    Notification,
]
