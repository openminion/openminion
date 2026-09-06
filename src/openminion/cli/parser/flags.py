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


def add_interactive_session_flags(parser: argparse.ArgumentParser) -> None:
    """Add options for the canonical root interactive surface."""
    parser.add_argument(
        "--profile",
        "--agent",
        dest="agent",
        default=None,
        help="Configured profile id to activate (compat: --agent)",
    )
    parser.add_argument(
        "--session",
        default=None,
        help="Existing interactive session id to resume",
    )
    parser.add_argument(
        "--dir",
        default=None,
        help="Working directory for the interactive session",
    )
    parser.add_argument(
        "--add-dir",
        action="append",
        default=[],
        help="Add an existing directory for read/write access in this process",
    )
    parser.add_argument(
        "--theme",
        default=None,
        help=(
            "Theme variant override (e.g. light, dark). "
            "Top precedence - beats env and persisted preference."
        ),
    )
    parser.add_argument(
        "--color",
        choices=("auto", "always", "never"),
        default=None,
        help=(
            "Color policy for the default interactive terminal: auto, always, "
            "or never. Overrides color environment variables."
        ),
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Use the built-in demo provider without external credentials",
    )
    parser.add_argument(
        "--no-context",
        action="store_true",
        help="Do not auto-load OPENMINION.md/AGENTS.md/CLAUDE.md project context",
    )
    parser.add_argument(
        "--no-update-check",
        action="store_true",
        help="Disable the cached startup update-available notification",
    )
    parser.add_argument(
        "--animation-provider",
        default=None,
        help="Activity animation provider id (default: openminion)",
    )
    parser.add_argument(
        "--animation",
        default=None,
        help="Activity animation preset, or provider:preset shorthand",
    )

    from openminion.cli.ux.verbosity import add_progress_flag, add_verbosity_flag

    add_verbosity_flag(parser)
    add_progress_flag(parser, include_aliases=True)


def add_profile_selector(
    parser: argparse.ArgumentParser,
    *,
    dest: str = "agent",
    default: str | None = None,
    legacy_flags: tuple[str, ...] = ("--agent",),
    help_text: str = "Configured profile id to use",
) -> None:
    parser.add_argument(
        "--profile",
        *legacy_flags,
        dest=dest,
        default=default,
        help=(
            f"{help_text}"
            + (f" (compat: {', '.join(legacy_flags)})" if legacy_flags else "")
        ),
    )


def add_runtime_source_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--runtime-source",
        choices=("auto", "daemon", "inproc"),
        default="auto",
        help=(
            "Runtime source policy: auto tries daemon then reports fallback, "
            "daemon fails closed if unavailable, inproc bypasses daemon."
        ),
    )


__all__ = [
    "add_interactive_session_flags",
    "add_json_output_flag",
    "add_profile_selector",
    "add_runtime_source_flag",
    "add_tool_session_arg",
]
