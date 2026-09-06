"""Developer/operator controls for the persistent Rex background runtime."""

from __future__ import annotations

import argparse


def cmd_background(args: argparse.Namespace) -> int:
    """Delegate operator controls to the internal packaged runtime CLI."""

    from rex.background.cli import main as background_main

    argv = [args.background_command, "--runtime-root", args.runtime_root]
    if args.background_command == "start":
        argv[0] = "supervisor"
        argv.extend(("--user", args.user, "--activation-mode", args.activation_mode))
        if args.origin_device_id:
            argv.extend(("--origin-device-id", args.origin_device_id))
    return background_main(argv)


def _add_runtime_root(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--runtime-root",
        required=True,
        help="Absolute AskRex runtime root containing background state",
    )


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register developer/operator background runtime controls."""

    parser = subparsers.add_parser(
        "background",
        help="Control the persistent Rex background runtime",
    )
    commands = parser.add_subparsers(
        title="background commands",
        dest="background_command",
        metavar="COMMAND",
        required=True,
    )

    start = commands.add_parser("start", help="Run the background supervisor")
    _add_runtime_root(start)
    start.add_argument("--user", required=True)
    start.add_argument(
        "--activation-mode",
        choices=("hold-to-talk", "wake-word"),
        default="wake-word",
    )
    start.add_argument("--origin-device-id")
    start.set_defaults(func=cmd_background)

    status = commands.add_parser("status", help="Print content-free runtime health JSON")
    _add_runtime_root(status)
    status.set_defaults(func=cmd_background)

    stop = commands.add_parser("stop", help="Request orderly background shutdown")
    _add_runtime_root(stop)
    stop.set_defaults(func=cmd_background)


__all__ = ["cmd_background", "register"]
