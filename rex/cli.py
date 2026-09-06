"""
Command-line interface for Rex AI Assistant.

This module provides the main CLI entry point with subcommands:
    rex doctor       - Run environment diagnostics
    rex chat         - Start interactive chat (default)
    rex version      - Show version information
    rex tools        - List registered tools and their status
    rex run-workflow - Run a workflow from a JSON file
    rex approvals    - List and manage pending approvals
    rex workflows    - List workflows
    rex memory       - Manage working and long-term memory
    rex kb           - Manage knowledge base documents
    rex scheduler    - List and manage scheduler jobs
    rex email        - Manage email
    rex calendar     - Manage calendar
    rex reminders    - Manage reminders
    rex cues         - Manage follow-up cues
    rex browser      - Browser automation
    rex os           - OS automation
    rex gh           - GitHub integration
    rex code         - VS Code operations
    rex msg          - Messaging (SMS)
    rex notify       - Notifications
    rex pc           - Remote Windows computer control (agent API, client-only foundation)
    rex wp           - WordPress site monitoring (read-only)
    rex wc           - WooCommerce monitoring + approval-gated write actions
    rex voice-id     - Voice speaker identity enrollment, calibration, and status
    rex ha           - Home Assistant integration commands (TTS test)
    rex usage        - Show LLM usage summary (total requests, tokens, by model)
    rex background   - Control the persistent background runtime (operator use)

Usage:
    rex [command] [options]

If no command is specified, the chat interface is started.

Command implementations live in ``rex/commands/`` (one module per domain,
see US-REM-027). This module keeps parser registration, the ``main`` entry
point, and backward-compatible re-exports: ``rex.cli.<name>`` remains the
canonical import and monkeypatch target for every command handler, service
getter, and helper.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path  # noqa: F401  (re-export: patched as rex.cli.Path in tests)

from rex.compat.python_compat import (
    DEFAULT_INSTALL_LABEL,
    is_supported_python,
    unsupported_python_message,
)
from rex.exception_handler import wrap_entrypoint
from rex.startup_validation import check_startup_env

if not is_supported_python(sys.version_info):
    raise SystemExit(
        unsupported_python_message(sys.version_info, install_target=DEFAULT_INSTALL_LABEL)
    )

from rex.commands import (  # noqa: E402
    background,
    commerce,
    core,
    dashboard,
    devtools,
    email_calendar,
    ha,
    identity,
    memory,
    messaging,
    mobile,
    pc,
    reminders,
    scheduler,
    workflows,
)
from rex.commands._epilog import CLI_EPILOG  # noqa: E402

# ---------------------------------------------------------------------------
# Backward-compatible re-exports (US-REM-027).
# ``rex.cli.<name>`` is the stable public surface: tests and external code
# import handlers from here and monkeypatch service getters here. Command
# modules resolve these names through ``rex.cli`` at call time.
# ---------------------------------------------------------------------------
from rex.commands._helpers import (  # noqa: E402,F401
    _get_version,
    _load_calendar_resolver_safe,
    _load_email_config_safe,
    _load_email_resolver_safe,
    _parse_datetime_strict,
    _parse_ttl,
    _resolve_cli_user,
)
from rex.commands._services import (  # noqa: E402,F401
    get_browser_service,
    get_calendar_service,
    get_computer_service,
    get_cue_store,
    get_email_service,
    get_github_service,
    get_os_service,
    get_reminder_service,
    get_scheduler,
    get_vscode_service,
    initialize_scheduler_system,
)
from rex.commands.background import cmd_background  # noqa: E402,F401
from rex.commands.commerce import (  # noqa: E402,F401
    _WC_WRITE_HELP,
    _cmd_wc_coupon_create,
    _cmd_wc_coupon_disable,
    _cmd_wc_order_set_status,
    _resolve_wc_initiated_by,
    cmd_wc,
    cmd_wp,
)
from rex.commands.core import (  # noqa: E402,F401
    cmd_chat,
    cmd_doctor,
    cmd_integrations,
    cmd_tools,
    cmd_version,
)
from rex.commands.dashboard import (  # noqa: E402,F401
    cmd_history,
    cmd_quick_actions,
    cmd_shopping,
    cmd_usage,
)
from rex.commands.devtools import cmd_browser, cmd_code, cmd_gh, cmd_os  # noqa: E402,F401
from rex.commands.email_calendar import (  # noqa: E402,F401
    _cmd_calendar_accounts,
    _cmd_calendar_test_connection,
    _cmd_email_accounts,
    _cmd_email_send,
    _cmd_email_test_connection,
    cmd_calendar,
    cmd_email,
)
from rex.commands.ha import (  # noqa: E402,F401
    _cmd_ha_approve,
    _cmd_ha_tts,
    _cmd_ha_tts_test,
    cmd_ha,
)
from rex.commands.identity import cmd_identify, cmd_voice_id, cmd_whoami  # noqa: E402,F401
from rex.commands.memory import cmd_kb, cmd_memory, cmd_remember  # noqa: E402,F401
from rex.commands.messaging import cmd_msg, cmd_notify  # noqa: E402,F401
from rex.commands.mobile import cmd_mobile_api, cmd_mobile_user  # noqa: E402,F401
from rex.commands.pc import cmd_pc  # noqa: E402,F401
from rex.commands.reminders import cmd_cues, cmd_reminders  # noqa: E402,F401
from rex.commands.scheduler import cmd_scheduler  # noqa: E402,F401
from rex.commands.workflows import (  # noqa: E402,F401
    cmd_approvals,
    cmd_executor_resume,
    cmd_plan,
    cmd_run_workflow,
    cmd_workflows,
)


def create_parser() -> argparse.ArgumentParser:
    """Create the argument parser with all subcommands."""
    parser = argparse.ArgumentParser(
        prog="rex",
        description="Rex AI Assistant - Voice-activated AI assistant with speech recognition and synthesis",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=CLI_EPILOG,
    )

    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose output")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable DEBUG logging across all modules (equivalent to REX_DEBUG=1)",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {_get_version()}")

    subparsers = parser.add_subparsers(
        title="commands",
        description="Available commands",
        dest="command",
        metavar="COMMAND",
    )

    # Registration order is preserved from the original single-module parser
    # so that `rex --help` output is unchanged (US-REM-027).
    core.register(subparsers)
    workflows.register(subparsers)
    memory.register(subparsers)
    scheduler.register(subparsers)
    email_calendar.register(subparsers)
    identity.register(subparsers)
    reminders.register(subparsers)
    messaging.register(subparsers)
    devtools.register(subparsers)
    pc.register(subparsers)
    commerce.register(subparsers)
    ha.register(subparsers)
    identity.register_voice_id(subparsers)
    dashboard.register(subparsers)
    mobile.register(subparsers)
    background.register(subparsers)

    return parser


@wrap_entrypoint
def main(argv: Sequence[str] | None = None) -> int:
    """Main entry point for the Rex CLI."""
    check_startup_env()

    # Keep legacy-environment diagnostics on the user-facing CLI path without
    # forcing rex.config to initialize during package/background imports.
    from rex.config_manager import get_legacy_env_warnings

    for warning in get_legacy_env_warnings()[:3]:
        print(warning, file=sys.stderr)

    from rex.first_run import maybe_print_welcome

    maybe_print_welcome()

    parser = create_parser()
    args = parser.parse_args(argv)

    # Apply --debug flag: set env var so load_config() picks it up, then
    # immediately raise the root log level so all subsequent imports log verbosely.
    if getattr(args, "debug", False):
        import os

        os.environ["REX_DEBUG"] = "1"
        from rex.logging_utils import set_global_level

        set_global_level(__import__("logging").DEBUG)

    if args.command is None:
        if getattr(args, "verbose", False):
            parser.print_help()
            return 0
        args.func = cmd_chat
        args.verbose = getattr(args, "verbose", False)

    return args.func(args)  # type: ignore[no-any-return]


if __name__ == "__main__":
    sys.exit(main())
