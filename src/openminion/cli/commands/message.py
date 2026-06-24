from __future__ import annotations

import argparse
from typing import Any

from openminion.base.types import Message
from openminion.cli.parser.flags import add_json_output_flag
from openminion.cli.presentation.json_output import print_json_payload


def send_message(args: Any, app: Any) -> int:
    outbound = Message(channel=args.channel, target=args.target, body=args.message)
    app.channels.get(args.channel).send(outbound)

    if args.json:
        print_json_payload(
            {
                "id": outbound.id,
                "channel": outbound.channel,
                "target": outbound.target,
                "body": outbound.body,
            }
        )
    return 0


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    message = subparsers.add_parser("message", help="Message operations")
    message_subcommands = message.add_subparsers(dest="message_command")
    message_send = message_subcommands.add_parser(
        "send", help="Send a message through a channel"
    )
    message_send.add_argument("--message", required=True, help="Message body")
    message_send.add_argument("--target", required=True, help="Message target")
    message_send.add_argument("--channel", default="console", help="Channel name")
    add_json_output_flag(message_send)
    message_send.set_defaults(handler=send_message, needs_app=True)
