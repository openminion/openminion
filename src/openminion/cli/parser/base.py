from __future__ import annotations

import argparse

from openminion.base.config import DEFAULT_CONFIG_PATH
from openminion.cli.commands import agent as agent_cmd
from openminion.cli.commands import agent_check as agent_check_cmd
from openminion.cli.commands import agents as agents_cmd
from openminion.cli.commands import api as api_cmd
from openminion.cli.commands import chat as chat_cmd
from openminion.cli.commands import config as config_cmd
from openminion.cli.commands import cron as cron_cmd
from openminion.cli.commands import daemon as daemon_cmd
from openminion.cli.commands import data as data_cmd
from openminion.cli.commands import doctor as doctor_cmd
from openminion.cli.commands import export as export_cmd
from openminion.cli.commands import focus as focus_cmd
from openminion.cli.commands import gateway as gateway_cmd
from openminion.cli.commands import identity as identity_cmd
from openminion.cli.commands import memory as memory_cmd
from openminion.cli.commands import mcp as mcp_cmd
from openminion.cli.commands import message as message_cmd
from openminion.cli.commands import plugins as plugins_cmd
from openminion.cli.commands import room as room_cmd
from openminion.cli.commands import run as run_cmd
from openminion.cli.commands import scaffold as scaffold_cmd
from openminion.cli.commands import sessions as sessions_cmd
from openminion.cli.commands import setup as setup_cmd
from openminion.cli.commands import sidecar as sidecar_cmd
from openminion.cli.commands import skill as skill_cmd
from openminion.cli.commands import status as status_cmd
from openminion.cli.commands import storage as storage_cmd
from openminion.cli.commands import time as time_cmd
from openminion.cli.commands import toolctl as toolctl_cmd
from openminion.cli.commands import tools as tools_cmd
from openminion.cli.commands import tui as tui_cmd
from openminion.cli.commands import verify as verify_cmd
from openminion.cli.commands import version as version_cmd
from openminion.cli.commands.debug import cli as debug_cli_cmd

COMMAND_MODULES = (
    config_cmd,
    api_cmd,
    data_cmd,
    daemon_cmd,
    run_cmd,
    room_cmd,
    chat_cmd,
    tui_cmd,
    sessions_cmd,
    sidecar_cmd,
    tools_cmd,
    toolctl_cmd,
    time_cmd,
    gateway_cmd,
    agent_cmd,
    agent_check_cmd,
    agents_cmd,
    message_cmd,
    plugins_cmd,
    doctor_cmd,
    status_cmd,
    export_cmd,
    focus_cmd,
    setup_cmd,
    storage_cmd,
    verify_cmd,
    version_cmd,
    scaffold_cmd,
    cron_cmd,
    debug_cli_cmd,
    skill_cmd,
    identity_cmd,
    memory_cmd,
    mcp_cmd,
)


def _hide_suppressed_subcommands(parser: argparse.ArgumentParser) -> None:
    for action in parser._actions:
        if not isinstance(action, argparse._SubParsersAction):
            continue
        visible_choice_actions = [
            choice_action
            for choice_action in getattr(action, "_choices_actions", [])
            if getattr(choice_action, "help", None) != argparse.SUPPRESS
        ]
        if visible_choice_actions and len(visible_choice_actions) != len(
            getattr(action, "_choices_actions", [])
        ):
            action._choices_actions = visible_choice_actions
            action.metavar = (
                "{" + ",".join(choice.dest for choice in visible_choice_actions) + "}"
            )
        for child_parser in getattr(action, "choices", {}).values():
            _hide_suppressed_subcommands(child_parser)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="openminion",
        description=(
            "Python-first OpenMinion runtime. Bare `openminion` opens the "
            "default focus shell; `openminion dashboard` opens the monitoring "
            "overview."
        ),
        allow_abbrev=False,
    )
    parser.add_argument(
        "--home-root",
        default=None,
        help=(
            "OpenMinion Home for generated state (anchors .openminion/ and .openminion/runtime/). "
            "Equivalent to setting OPENMINION_HOME."
        ),
    )
    parser.add_argument(
        "--data-root",
        default=None,
        help=(
            "Centralized data root for all runtime outputs (configs, logs, artifacts, DBs). "
            "Equivalent to setting OPENMINION_DATA_ROOT. Default: <OpenMinion Home>/.openminion. "
            "Paths are enforced under this root unless OPENMINION_DATA_ROOT_ENFORCEMENT=soft."
        ),
    )
    parser.add_argument(
        "--generated-root",
        default=None,
        help=(
            "Override generated artifacts root (defaults to <data-root>/runtime). "
            "Equivalent to setting OPENMINION_GENERATED_ROOT. Must resolve under <data-root>/runtime "
            "unless OPENMINION_DATA_ROOT_ENFORCEMENT=soft."
        ),
    )
    parser.add_argument(
        "--config",
        default=None,
        help=f"Config file path (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "--no-interactive",
        action="store_true",
        help="Disable inline first-run setup and fail fast with remediation",
    )
    parser.add_argument(
        "--allow-unsandboxed-exec",
        action="store_true",
        help=(
            "Enable legacy unsandboxed exec tool hosts (gateway/node) for this process. "
            "Equivalent to setting OPENMINION_TOOL_EXEC_ENABLE_HOST_EXEC=1."
        ),
    )

    subparsers = parser.add_subparsers(dest="command")
    for module in COMMAND_MODULES:
        module.register(subparsers)
    _hide_suppressed_subcommands(parser)
    return parser
