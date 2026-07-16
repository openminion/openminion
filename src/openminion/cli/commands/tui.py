from __future__ import annotations

import argparse
import sys

from openminion.cli.commands.interactive import (
    add_interactive_arguments,
    run_interactive,
)
from openminion.cli.ux.deprecation import print_deprecation_notice

_DASHBOARD_NOTICE = (
    "openminion dashboard was retired.\n"
    "Use bare `openminion` for the interactive CLI.\n"
    "Use `openminion status`, `sessions`, `tasks`, `cron`, `memory`, `tools`, "
    "`agent`, or `api` for operator workflows."
)
_TUI_NOTICE = (
    "openminion tui is a compatibility alias; use bare `openminion`. "
    "Suppress this notice with OPENMINION_TUI_NO_DEPRECATION=1."
)


def dashboard_deprecation_message() -> str:
    return _DASHBOARD_NOTICE


def _run_alias(
    args: argparse.Namespace,
    *,
    surface: str,
    notice: str,
    suppression_env: str,
) -> int:
    forwarded = argparse.Namespace(**vars(args))
    forwarded.surface = surface
    forwarded.deprecation_notice_shown = print_deprecation_notice(
        notice,
        suppression_env=suppression_env,
    )
    return run_interactive(forwarded)


def run_dashboard(args: argparse.Namespace) -> int:
    del args
    print(_DASHBOARD_NOTICE, file=sys.stderr)
    return 0


def run_tui(args: argparse.Namespace) -> int:
    return _run_alias(
        args,
        surface="tui",
        notice=_TUI_NOTICE,
        suppression_env="OPENMINION_TUI_NO_DEPRECATION",
    )


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    dashboard = subparsers.add_parser("dashboard", help=argparse.SUPPRESS)
    add_interactive_arguments(dashboard)
    dashboard.set_defaults(handler=run_dashboard, needs_app=False)

    tui = subparsers.add_parser("tui", help=argparse.SUPPRESS)
    add_interactive_arguments(tui)
    tui.set_defaults(handler=run_tui, needs_app=False)


run_tui_entry = run_tui
