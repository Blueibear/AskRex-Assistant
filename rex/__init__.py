"""Public package surface for Rex.

The package root stays intentionally side-effect free. In particular, importing
``rex.background.cli`` must not load runtime configuration before the packaged
background entrypoint has made its explicit runtime root authoritative.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "settings": ("rex.config", "settings"),
    "reload_settings": ("rex.config", "reload_settings"),
    "configure_logging": ("rex.logging_utils", "configure_logging"),
    "Credential": ("rex.credentials", "Credential"),
    "CredentialManager": ("rex.credentials", "CredentialManager"),
    "CredentialRefreshError": ("rex.credentials", "CredentialRefreshError"),
    "get_credential_manager": ("rex.credentials", "get_credential_manager"),
    "set_credential_manager": ("rex.credentials", "set_credential_manager"),
    "mask_token": ("rex.credentials", "mask_token"),
    "ToolMeta": ("rex.openclaw.tool_registry", "ToolMeta"),
    "ToolRegistry": ("rex.openclaw.tool_registry", "ToolRegistry"),
    "ToolNotFoundError": ("rex.openclaw.tool_registry", "ToolNotFoundError"),
    "MissingCredentialError": ("rex.openclaw.tool_registry", "MissingCredentialError"),
    "get_tool_registry": ("rex.openclaw.tool_registry", "get_tool_registry"),
    "set_tool_registry": ("rex.openclaw.tool_registry", "set_tool_registry"),
    "register_tool": ("rex.openclaw.tool_registry", "register_tool"),
    "ScheduledJob": ("rex.scheduler", "ScheduledJob"),
    "Scheduler": ("rex.scheduler", "Scheduler"),
    "get_scheduler": ("rex.scheduler", "get_scheduler"),
    "set_scheduler": ("rex.scheduler", "set_scheduler"),
    "Event": ("rex.openclaw.event_bus", "Event"),
    "EventBus": ("rex.openclaw.event_bus", "EventBus"),
    "get_event_bus": ("rex.openclaw.event_bus", "get_event_bus"),
    "set_event_bus": ("rex.openclaw.event_bus", "set_event_bus"),
    "EmailSummary": ("rex.email_service", "EmailSummary"),
    "EmailService": ("rex.email_service", "EmailService"),
    "get_email_service": ("rex.email_service", "get_email_service"),
    "set_email_service": ("rex.email_service", "set_email_service"),
    "CalendarEvent": ("rex.calendar_service", "CalendarEvent"),
    "CalendarService": ("rex.calendar_service", "CalendarService"),
    "get_calendar_service": ("rex.calendar_service", "get_calendar_service"),
    "set_calendar_service": ("rex.calendar_service", "set_calendar_service"),
    "NotificationRequest": ("rex.notification", "NotificationRequest"),
    "Notifier": ("rex.notification", "Notifier"),
    "EscalationManager": ("rex.notification", "EscalationManager"),
    "get_escalation_manager": ("rex.notification", "get_escalation_manager"),
    "get_notifier": ("rex.notification", "get_notifier"),
    "set_escalation_manager": ("rex.notification", "set_escalation_manager"),
    "set_notifier": ("rex.notification", "set_notifier"),
}

__all__ = list(_LAZY_EXPORTS)


def __getattr__(name: str) -> Any:
    """Resolve legacy package-root exports only when a caller asks for them."""

    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute_name = target
    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
