from __future__ import annotations

import argparse
import importlib
import sys
from collections.abc import Sequence
from dataclasses import dataclass

from openminion.base.config import DEFAULT_CONFIG_PATH


@dataclass(frozen=True)
class CommandSpec:
    name: str
    module: str
    help: str


COMMAND_SPECS = (
    CommandSpec("config", "openminion.cli.commands.config", "Config operations"),
    CommandSpec("api", "openminion.cli.commands.api", "HTTP API controls"),
    CommandSpec("autonomy", "openminion.cli.commands.autonomy", "Autonomy runs"),
    CommandSpec("data", "openminion.cli.commands.data", "Data root operations"),
    CommandSpec(
        "daemon", "openminion.cli.commands.daemon", "Daemon lifecycle controls"
    ),
    CommandSpec("run", "openminion.cli.commands.run", "Run a prompt"),
    CommandSpec(
        "room", "openminion.cli.commands.room", "Create and manage room sessions"
    ),
    CommandSpec(
        "channel", "openminion.cli.commands.channel", "Channel setup and operations"
    ),
    CommandSpec("chat", "openminion.cli.commands.aliases", argparse.SUPPRESS),
    CommandSpec(
        "dashboard",
        "openminion.cli.commands.aliases",
        argparse.SUPPRESS,
    ),
    CommandSpec("tui", "openminion.cli.commands.aliases", argparse.SUPPRESS),
    CommandSpec("sessions", "openminion.cli.commands.sessions", "Session operations"),
    CommandSpec(
        "sidecar", "openminion.cli.commands.sidecar", "Sidecar lifecycle controls"
    ),
    CommandSpec(
        "tools", "openminion.cli.commands.tools", "Tool catalog and invocation"
    ),
    CommandSpec(
        "toolctl",
        "openminion.cli.commands.tool_control",
        "Operator controls for authored tools",
    ),
    CommandSpec("time", "openminion.cli.commands.time", "Trusted time helpers"),
    CommandSpec(
        "gateway", "openminion.cli.commands.gateway", "Gateway runtime controls"
    ),
    CommandSpec(
        "agent",
        "openminion.cli.commands.agent",
        "Run an agent turn or manage agent runtimes",
    ),
    CommandSpec(
        "agent-check", "openminion.cli.commands.agent_check", "Run an agent check"
    ),
    CommandSpec("agent-ctl", "openminion.cli.commands.agents", argparse.SUPPRESS),
    CommandSpec("message", "openminion.cli.commands.message", "Message operations"),
    CommandSpec("plugins", "openminion.cli.commands.plugins", "Plugin operations"),
    CommandSpec("doctor", "openminion.cli.commands.doctor", "Run diagnostics"),
    CommandSpec(
        "status", "openminion.cli.commands.status", "Inspect run/task lifecycle status"
    ),
    CommandSpec(
        "tasks", "openminion.cli.commands.tasks", "Task inventory and controls"
    ),
    CommandSpec("replay", "openminion.cli.commands.replay", "Replay/checkpoint controls"),
    CommandSpec("checkpoint", "openminion.cli.commands.replay", "List task checkpoints"),
    CommandSpec("rewind", "openminion.cli.commands.replay", "Create a rewind branch"),
    CommandSpec("branch", "openminion.cli.commands.replay", "Create a checkpoint branch"),
    CommandSpec("export", "openminion.cli.commands.export", "Export commands"),
    CommandSpec("focus", "openminion.cli.commands.interactive", argparse.SUPPRESS),
    CommandSpec("setup", "openminion.cli.commands.setup", "Configure OpenMinion"),
    CommandSpec(
        "storage", "openminion.cli.commands.storage", "Shared storage-core operations"
    ),
    CommandSpec(
        "verify", "openminion.cli.commands.verify", "Verify runtime configuration"
    ),
    CommandSpec("version", "openminion.cli.commands.version", "Show package version"),
    CommandSpec(
        "scaffold", "openminion.cli.commands.scaffold", "Scaffold package assets"
    ),
    CommandSpec("cron", "openminion.cli.commands.cron", "Cron operations"),
    CommandSpec(
        "debug", "openminion.cli.commands.debug.cli", "Debug module diagnostics"
    ),
    CommandSpec(
        "skill", "openminion.cli.commands.skill", "Skill management operations"
    ),
    CommandSpec(
        "identity", "openminion.cli.commands.identity", "Identity profile management"
    ),
    CommandSpec("memory", "openminion.cli.commands.memory", "Memory operations"),
    CommandSpec("mcp", "openminion.cli.commands.mcp", "Manage MCP servers"),
)
COMMAND_MODULES = tuple(dict.fromkeys(spec.module for spec in COMMAND_SPECS))
COMMAND_NAMES = frozenset(spec.name for spec in COMMAND_SPECS)
_ROOT_OPTIONS_WITH_VALUES = frozenset(
    {
        "--home-root",
        "--data-root",
        "--generated-root",
        "--config",
    }
)


class LazyCommandArgumentParser(argparse.ArgumentParser):
    def parse_args(
        self,
        args: Sequence[str] | None = None,
        namespace: argparse.Namespace | None = None,
    ) -> argparse.Namespace:
        selected_command = _selected_command(args)
        if selected_command is not None:
            return build_parser(selected_command=selected_command).parse_args(
                args,
                namespace,
            )
        return super().parse_args(args, namespace)

    def parse_known_args(
        self,
        args: Sequence[str] | None = None,
        namespace: argparse.Namespace | None = None,
    ) -> tuple[argparse.Namespace, list[str]]:
        selected_command = _selected_command(args)
        if selected_command is not None:
            return build_parser(selected_command=selected_command).parse_known_args(
                args,
                namespace,
            )
        return super().parse_known_args(args, namespace)


def _selected_command(args: Sequence[str] | None) -> str | None:
    tokens = list(sys.argv[1:] if args is None else args)
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            return None
        if token in ("-h", "--help"):
            return None
        if token.startswith("--"):
            option = token.split("=", 1)[0]
            if option in _ROOT_OPTIONS_WITH_VALUES and "=" not in token:
                index += 2
            else:
                index += 1
            continue
        return token if token in COMMAND_NAMES else None
    return None


def _register_command_modules(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    *,
    selected_command: str | None,
) -> None:
    if selected_command is None:
        for spec in COMMAND_SPECS:
            subparsers.add_parser(spec.name, help=spec.help)
        return

    modules = [spec.module for spec in COMMAND_SPECS if spec.name == selected_command]
    for module_name in dict.fromkeys(modules):
        module = importlib.import_module(module_name)
        module.register(subparsers)


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


def build_parser(*, selected_command: str | None = None) -> argparse.ArgumentParser:
    parser_class = (
        argparse.ArgumentParser if selected_command else LazyCommandArgumentParser
    )
    parser = parser_class(
        prog="openminion",
        description=(
            "Python-first OpenMinion runtime. Bare `openminion` opens the "
            "interactive CLI with its default terminal renderer; piped input and "
            "`openminion run` execute "
            "one-shot requests."
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
    from openminion.cli.parser.flags import add_interactive_session_flags

    add_interactive_session_flags(parser)
    backend = parser.add_mutually_exclusive_group()
    backend.add_argument(
        "--rich",
        action="store_true",
        help="Use the optional Textual renderer instead of the default terminal renderer.",
    )
    backend.add_argument(
        "--terminal",
        action="store_true",
        help=argparse.SUPPRESS,
    )

    subparsers = parser.add_subparsers(dest="command")
    _register_command_modules(subparsers, selected_command=selected_command)
    _hide_suppressed_subcommands(parser)
    return parser
