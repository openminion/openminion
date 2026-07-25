from __future__ import annotations

import argparse
from typing import Any

from openminion.cli.parser.flags import add_json_output_flag
from openminion.cli.presentation.browser import browser_command_payload, render_browser_command
from openminion.cli.presentation.json_output import print_json_payload


def run_browser(args: Any) -> int:
    action = str(getattr(args, "browser_command", "") or "status").strip()
    command_args = _command_args(args, action=action)
    if bool(getattr(args, "json", False)):
        print_json_payload(browser_command_payload(command_args))
    else:
        print(render_browser_command(command_args))
    return 0


def _command_args(args: Any, *, action: str) -> str:
    parts = [action]
    for key in ("url", "provider", "tab", "instance", "instance_id"):
        value = str(getattr(args, key, "") or "").strip()
        if value:
            parts.append(f"{key}={value}")
    if bool(getattr(args, "sidecar", False)):
        parts.append("sidecar=1")
    elif action == "stop":
        parts.append("sidecar=0")
    if bool(getattr(args, "kill", False)):
        parts.append("kill=1")
    return " ".join(parts)


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    browser = subparsers.add_parser("browser", help="Browser status and controls")
    add_json_output_flag(browser)
    browser_subcommands = browser.add_subparsers(dest="browser_command")

    status = browser_subcommands.add_parser("status", help="Show browser status")
    add_json_output_flag(status)
    status.set_defaults(handler=run_browser, needs_app=False)

    tabs = browser_subcommands.add_parser("tabs", help="List browser tabs")
    tabs.add_argument("--provider", default="", help="Browser provider")
    add_json_output_flag(tabs)
    tabs.set_defaults(handler=run_browser, needs_app=False)

    navigate = browser_subcommands.add_parser("navigate", help="Navigate a tab")
    navigate.add_argument("url", help="URL to open")
    navigate.add_argument("--provider", default="", help="Browser provider")
    navigate.add_argument("--tab", default="", help="Existing tab id")
    add_json_output_flag(navigate)
    navigate.set_defaults(handler=run_browser, needs_app=False)

    stop = browser_subcommands.add_parser("stop", help="Stop browser resources")
    stop.add_argument("--provider", default="", help="Browser provider")
    stop.add_argument("--instance", default="", help="Browser instance id")
    stop.add_argument(
        "--sidecar",
        action="store_true",
        help="Stop the PinchTab sidecar instead of an instance",
    )
    stop.add_argument("--kill", action="store_true", help="Force kill when supported")
    add_json_output_flag(stop)
    stop.set_defaults(handler=run_browser, needs_app=False)

