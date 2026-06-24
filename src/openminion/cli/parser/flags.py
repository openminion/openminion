from __future__ import annotations

import argparse

from openminion.cli.constants import (
    CLI_HELP_MACHINE_READABLE_OUTPUT,
    CLI_HELP_SESSION_ID_FOR_TOOL_LOGS,
)


def add_json_output_flag(
    parser: argparse.ArgumentParser,
    *,
    dest: str = "json",
    help_text: str = CLI_HELP_MACHINE_READABLE_OUTPUT,
) -> None:
    parser.add_argument("--json", dest=dest, action="store_true", help=help_text)


def add_tool_session_arg(
    parser: argparse.ArgumentParser,
    *,
    default: str,
    help_text: str = CLI_HELP_SESSION_ID_FOR_TOOL_LOGS,
) -> None:
    parser.add_argument("--session", default=default, help=help_text)


__all__ = ["add_json_output_flag", "add_tool_session_arg"]
