from __future__ import annotations

import argparse
from collections.abc import Callable


def register_telegram_subcommands(
    channel_subcommands: argparse._SubParsersAction[argparse.ArgumentParser],
    *,
    handler: Callable[[argparse.Namespace], int],
) -> None:
    telegram = channel_subcommands.add_parser(
        "telegram", help="Telegram channel setup, pairing, and status"
    )
    commands = telegram.add_subparsers(dest="telegram_command", required=True)

    setup = commands.add_parser("setup", help="Connect a Telegram bot to this profile")
    _add_config_arg(setup)
    setup.add_argument(
        "--bot-token-stdin",
        action="store_true",
        help="Read the bot token securely from standard input",
    )
    setup.add_argument(
        "--bot-token-file", default=None, help="Read the bot token from a file"
    )
    setup.add_argument(
        "--bot-token-ref",
        default=None,
        help="Store a token reference such as env:TELEGRAM_BOT_TOKEN",
    )
    setup.add_argument(
        "--unsafe-bot-token",
        default=None,
        help="Pass a raw token on the command line (unsafe: visible in shell history)",
    )
    setup.add_argument(
        "--allow-tracked-secret",
        action="store_true",
        help="Allow a raw token in a git-tracked config file",
    )
    setup.set_defaults(handler=handler, needs_app=False)

    doctor = commands.add_parser("doctor", help="Check Telegram setup")
    _add_config_arg(doctor)
    doctor.add_argument("--json", action="store_true", help="Print JSON output")
    _add_scope_args(doctor)
    doctor.set_defaults(handler=handler, needs_app=False)

    identify = commands.add_parser("identify", help="Find the Telegram chat to pair")
    _add_config_arg(identify)
    identify.add_argument(
        "--timeout-seconds",
        type=int,
        default=30,
        help="How long to wait for a new Telegram message",
    )
    identify.set_defaults(handler=handler, needs_app=False)

    pair = commands.add_parser(
        "pair", help="Pair a Telegram chat", description="Pair a Telegram chat"
    )
    _add_config_arg(pair)
    pair.add_argument(
        "--user-id", type=int, default=None, help="Telegram user ID to authorize"
    )
    pair.add_argument(
        "--chat-id", type=int, default=None, help="Telegram chat ID to authorize"
    )
    pair.add_argument(
        "--ttl-seconds", type=int, default=None, help="Pairing-token lifetime"
    )
    pair.add_argument("--scopes", default=None, help="Comma-separated access scopes")
    pair.add_argument(
        "--wait",
        action="store_true",
        help="Wait for a new Telegram message to identify the chat",
    )
    pair.add_argument(
        "--timeout-seconds",
        type=int,
        default=30,
        help="How long --wait should look for a new message",
    )
    pair.set_defaults(handler=handler, needs_app=False)

    run = commands.add_parser("run", help="Run the Telegram listener in this terminal")
    _add_config_arg(run)
    run.add_argument(
        "--once", action="store_true", help="Poll once and exit (testing only)"
    )
    run.set_defaults(handler=handler, needs_app=False)

    status = commands.add_parser("status", help="Check pairing and listener status")
    _add_config_arg(status)
    status.add_argument("--json", action="store_true", help="Print JSON output")
    _add_scope_args(status)
    status.set_defaults(handler=handler, needs_app=False)

    commands_sync = commands.add_parser(
        "commands-sync", help="Add OpenMinion commands to the Telegram bot menu"
    )
    _add_config_arg(commands_sync)
    commands_sync.set_defaults(handler=handler, needs_app=False)


def _add_config_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default=None, help="Config file path")


def _add_scope_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--user-id", type=int, default=None, help="Telegram user ID to inspect"
    )
    parser.add_argument(
        "--chat-id", type=int, default=None, help="Telegram chat ID to inspect"
    )
    parser.add_argument(
        "--topic-id", type=int, default=None, help="Telegram topic ID to inspect"
    )


__all__ = ["register_telegram_subcommands"]
