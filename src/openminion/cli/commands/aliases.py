from __future__ import annotations

import argparse
from pathlib import Path
import sys
from types import SimpleNamespace

from openminion.cli.commands.interactive import (
    add_interactive_arguments,
    run_interactive,
)
from openminion.cli.ux.deprecation import print_deprecation_notice

_CHAT_UNSUPPORTED_OPTIONS = {
    "override_provider": "--override-provider",
    "override_model": "--override-model",
    "override_system_prompt": "--override-system-prompt",
    "session_name": "--session-name",
    "conversation": "--conversation",
    "resume": "--resume",
    "reset_session": "--reset-session",
    "quiet": "--quiet",
    "sync_identity": "--sync-identity",
    "demo": "--demo",
    "no_progress": "--no-progress",
    "no_activity_indicator": "--no-activity-indicator",
}

_CHAT_NOTICE_TEXT = (
    "openminion chat is a compatibility alias; use bare `openminion` for "
    "interactive work. Suppress this notice with "
    "OPENMINION_CHAT_NO_DEPRECATION=1."
)
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


def _unsupported_chat_option(args: argparse.Namespace) -> str:
    for attribute, option in _CHAT_UNSUPPORTED_OPTIONS.items():
        if getattr(args, attribute, None):
            return option
    return ""


def _print_chat_migration_error(option: str) -> int:
    print(
        f"openminion chat: {option} is not supported by the compatibility "
        "alias. Use bare `openminion`; use "
        "`openminion run` for scripted one-shot execution.",
        file=sys.stderr,
    )
    return 2


def _run_chat_interactive_alias(args: argparse.Namespace) -> int:
    return int(
        run_interactive(
            SimpleNamespace(
                config=getattr(args, "config", None),
                home_root=getattr(args, "home_root", None),
                data_root=getattr(args, "data_root", None),
                agent=getattr(args, "agent", None),
                session=getattr(args, "session", None),
                dir=str(Path.cwd()),
                theme=getattr(args, "theme", None),
                no_interactive=bool(getattr(args, "no_interactive", False)),
                no_context=False,
                no_update_check=False,
                rich=False,
                terminal=False,
                surface="chat",
                deprecation_notice_shown=bool(
                    getattr(args, "deprecation_notice_shown", False)
                ),
            )
        )
        or 0
    )


def _run_chat_piped_alias(args: argparse.Namespace, prompt: str) -> int:
    from openminion.cli.commands.run import run_openminion

    return int(
        run_openminion(
            SimpleNamespace(
                config=getattr(args, "config", None),
                prompt=prompt,
                file="",
                agent=getattr(args, "agent", None),
                session=getattr(args, "session", None),
                resume=False,
                reset_session=False,
                purpose="chat-compat-piped-input",
                stream=False,
                json=False,
            )
        )
        or 0
    )


def run_chat(args: argparse.Namespace) -> int:
    notice_shown = print_deprecation_notice(
        _CHAT_NOTICE_TEXT,
        suppression_env="OPENMINION_CHAT_NO_DEPRECATION",
    )
    unsupported = _unsupported_chat_option(args)
    if unsupported:
        return _print_chat_migration_error(unsupported)

    stdin_tty = bool(getattr(sys.stdin, "isatty", lambda: False)())
    stdout_tty = bool(getattr(sys.stdout, "isatty", lambda: False)())
    if stdin_tty and stdout_tty:
        args.deprecation_notice_shown = notice_shown
        return _run_chat_interactive_alias(args)
    if not stdin_tty:
        prompt = sys.stdin.read().strip()
        if prompt:
            return _run_chat_piped_alias(args, prompt)
    print(
        "openminion chat: interactive use requires a TTY. Pipe a prompt to "
        "bare `openminion` or use `openminion run` for one-shot execution.",
        file=sys.stderr,
    )
    return 2


def run_dashboard(args: argparse.Namespace) -> int:
    del args
    print(_DASHBOARD_NOTICE, file=sys.stderr)
    return 0


def run_tui(args: argparse.Namespace) -> int:
    forwarded = argparse.Namespace(**vars(args))
    forwarded.surface = "tui"
    forwarded.deprecation_notice_shown = print_deprecation_notice(
        _TUI_NOTICE,
        suppression_env="OPENMINION_TUI_NO_DEPRECATION",
    )
    return run_interactive(forwarded)


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    _register_chat(subparsers)
    _register_dashboard(subparsers)
    _register_tui(subparsers)


def _register_chat(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    chat = subparsers.add_parser("chat", help=argparse.SUPPRESS)
    chat.add_argument(
        "--profile",
        "--agent",
        dest="agent",
        default=None,
        help=argparse.SUPPRESS,
    )
    chat.add_argument("--session", default=None, help=argparse.SUPPRESS)
    chat.add_argument("--theme", default=None, help=argparse.SUPPRESS)
    chat.add_argument("--no-interactive", action="store_true", help=argparse.SUPPRESS)
    chat.add_argument("--stdin-one-shot", action="store_true", help=argparse.SUPPRESS)
    chat.add_argument("--override-provider", default=None, help=argparse.SUPPRESS)
    chat.add_argument("--override-model", default=None, help=argparse.SUPPRESS)
    chat.add_argument("--override-system-prompt", default=None, help=argparse.SUPPRESS)
    chat.add_argument("--session-name", default=None, help=argparse.SUPPRESS)
    chat.add_argument("--conversation", default=None, help=argparse.SUPPRESS)
    chat.add_argument("--resume", action="store_true", help=argparse.SUPPRESS)
    chat.add_argument("--reset-session", action="store_true", help=argparse.SUPPRESS)
    chat.add_argument("--quiet", action="store_true", help=argparse.SUPPRESS)
    chat.add_argument("--sync-identity", action="store_true", help=argparse.SUPPRESS)
    chat.add_argument("--demo", action="store_true", help=argparse.SUPPRESS)
    chat.add_argument("--no-progress", action="store_true", help=argparse.SUPPRESS)
    chat.add_argument(
        "--no-activity-indicator", action="store_true", help=argparse.SUPPRESS
    )
    chat.set_defaults(handler=run_chat, needs_app=False)


def _register_dashboard(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    dashboard = subparsers.add_parser("dashboard", help=argparse.SUPPRESS)
    add_interactive_arguments(dashboard)
    dashboard.set_defaults(handler=run_dashboard, needs_app=False)


def _register_tui(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    tui = subparsers.add_parser("tui", help=argparse.SUPPRESS)
    add_interactive_arguments(tui)
    tui.set_defaults(handler=run_tui, needs_app=False)
