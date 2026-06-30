from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path

from openminion.cli.commands.tui import (
    _inspect_tui_onboarding,
    _run_inline_setup_for_tui,
    _silence_logging_for_tui,
)
from openminion.cli.tui.project_context import resolve_project_context


def _resolve_focus_backend(args: argparse.Namespace) -> str:
    from openminion.base.config.env import EnvironmentConfig

    if bool(getattr(args, "terminal", False)):
        return "terminal"
    if bool(getattr(args, "rich", False)):
        return "textual"
    env = EnvironmentConfig.from_sources()
    env_value = str(env.get("OPENMINION_FOCUS_BACKEND", "") or "").strip().lower()
    if env_value in ("textual", "rich"):
        return "textual"
    if env_value in ("terminal", "flow", "terminal-flow"):
        return "terminal"
    return "terminal"


def _resolve_plain_spinner(args: argparse.Namespace) -> bool:
    from openminion.cli.ux.verbosity import resolve_progress

    progress = resolve_progress(args, default="full")
    return progress in ("minimal", "off")


def _resolve_focus_verbosity(args: argparse.Namespace) -> str:
    from openminion.cli.ux.verbosity import resolve_verbosity

    return resolve_verbosity(args)


def _handle_focus_onboarding_gate(
    args: argparse.Namespace,
) -> tuple[int | None, argparse.Namespace]:
    from openminion.services.bootstrap.onboarding import OnboardingAction

    onboarding_status = _inspect_tui_onboarding(args)
    if onboarding_status.action == OnboardingAction.FAIL_FAST:
        import sys

        from openminion.services.bootstrap.onboarding import format_fail_fast_message

        print(
            format_fail_fast_message(
                surface="openminion focus",
                status=onboarding_status,
            ),
            file=sys.stderr,
        )
        return 2, args
    if onboarding_status.action == OnboardingAction.LAUNCH_SETUP:
        if _run_inline_setup_for_tui(args) != 0:
            return 1, args
        args = argparse.Namespace(**vars(args))
        args.no_interactive = False
    return None, args


def _enforce_textual_tty_requirement() -> int | None:
    import sys as _sys

    stdin_tty = bool(getattr(_sys.stdin, "isatty", lambda: False)())
    stdout_tty = bool(getattr(_sys.stdout, "isatty", lambda: False)())
    if stdin_tty and stdout_tty:
        return None
    print(
        "openminion focus --rich: the Textual rich shell "
        "requires an interactive terminal (TTY) on both "
        "stdin and stdout. Either run from an interactive "
        "shell, or use --terminal for the terminal-flow shell "
        "which supports piped stdin "
        "(e.g. `cat prompt.md | openminion`).",
        file=_sys.stderr,
    )
    return 2


def _launch_terminal_focus(
    args: argparse.Namespace, runtime, *, working_dir: str
) -> int:
    from openminion.cli.tui.terminal import run_terminal_focus
    from openminion.cli.tui.providers import OpenMinionRuntime

    terminal_runtime = OpenMinionRuntime(
        runtime,
        target="focus",
        agent_id=str(getattr(args, "agent", "") or "").strip() or None,
        working_dir=working_dir,
        bind_immediately=True,
        session_id=str(getattr(args, "session", "") or "").strip() or None,
    )
    if not bool(getattr(args, "no_context", False)):
        terminal_runtime.set_project_context(resolve_project_context(working_dir))
    return run_terminal_focus(
        terminal_runtime,
        working_dir=working_dir,
        agent=str(getattr(args, "agent", "") or "").strip() or None,
        session=str(getattr(args, "session", "") or "").strip() or None,
        plain_spinner=_resolve_plain_spinner(args),
        verbosity=_resolve_focus_verbosity(args),
        startup_notice=_build_update_notice_resolver(args),
    )


def _resolve_update_notice(args: argparse.Namespace) -> str:
    if bool(getattr(args, "no_update_check", False)):
        return ""
    try:
        from openminion import __version__
        from openminion.base.config.env import EnvironmentConfig
        from openminion.cli.config import resolve_cli_roots
        from openminion.cli.bootstrap.update import (
            check_update_available,
            default_update_cache_path,
        )

        roots = resolve_cli_roots(
            config_path=getattr(args, "config", None),
            home_root=getattr(args, "home_root", None),
            data_root=getattr(args, "data_root", None),
        )
        env = EnvironmentConfig.from_sources()
        result = check_update_available(
            current_version=__version__,
            cache_path=default_update_cache_path(data_root=Path(roots.data_root)),
            env={
                key: str(env.get(key, "") or "")
                for key in (
                    "OPENMINION_UPDATE_CHECK",
                    "OPENMINION_NO_UPDATE_CHECK",
                )
            },
        )
        notice = "" if result is None else result.render_notice()
        return notice
    except Exception:
        return ""


def _build_update_notice_resolver(
    args: argparse.Namespace,
) -> Callable[[], str] | None:
    if bool(getattr(args, "no_update_check", False)):
        return None

    def _resolve() -> str:
        return _resolve_update_notice(args)

    return _resolve


def _maybe_print_update_notice(args: argparse.Namespace) -> None:
    notice = _resolve_update_notice(args)
    if not notice:
        return
    print(notice)
    print()


def _resolve_focus_theme(args: argparse.Namespace):
    from openminion.cli.config import resolve_cli_roots
    from openminion.cli.theme import resolve_theme

    cli_theme = str(getattr(args, "theme", "") or "").strip() or None
    try:
        cli_roots = resolve_cli_roots(
            config_path=getattr(args, "config", None),
            home_root=getattr(args, "home_root", None),
            data_root=getattr(args, "data_root", None),
        )
        return resolve_theme(
            cli_flag=cli_theme,
            data_root=Path(cli_roots.data_root),
        )
    except Exception:
        return resolve_theme(cli_flag=cli_theme)


def _launch_textual_focus(
    args: argparse.Namespace, runtime, *, working_dir: str
) -> int:
    from openminion.cli.tui.focus import FocusApp
    from openminion.cli.tui.providers import OpenMinionRuntime

    focus_runtime = OpenMinionRuntime(
        runtime,
        target="focus",
        agent_id=str(getattr(args, "agent", "") or "").strip() or None,
        working_dir=working_dir,
        bind_immediately=False,
        session_id=str(getattr(args, "session", "") or "").strip() or None,
    )
    if not bool(getattr(args, "no_context", False)):
        focus_runtime.set_project_context(resolve_project_context(working_dir))
    resolved_theme = _resolve_focus_theme(args)
    FocusApp(
        runtime=focus_runtime,
        working_dir=working_dir,
        agent=str(getattr(args, "agent", "") or "").strip() or None,
        session=str(getattr(args, "session", "") or "").strip() or None,
        theme=resolved_theme,
        verbosity=_resolve_focus_verbosity(args),
    ).run()
    return 0


def run_focus(args: argparse.Namespace) -> int:
    from openminion.api.runtime import APIRuntime

    gate_exit, args = _handle_focus_onboarding_gate(args)
    if gate_exit is not None:
        return gate_exit

    backend = _resolve_focus_backend(args)
    _silence_logging_for_tui(args)
    if backend == "textual":
        tty_exit = _enforce_textual_tty_requirement()
        if tty_exit is not None:
            return tty_exit

    runtime = None
    try:
        runtime = APIRuntime.from_config_path(
            getattr(args, "config", None),
            home_root=getattr(args, "home_root", None),
            data_root=getattr(args, "data_root", None),
            logging_mode="interactive",
        )
        working_dir = str(
            Path(getattr(args, "dir", None) or ".").expanduser().resolve(strict=False)
        )
        if backend == "terminal":
            return _launch_terminal_focus(args, runtime, working_dir=working_dir)
        _maybe_print_update_notice(args)
        return _launch_textual_focus(args, runtime, working_dir=working_dir)
    except Exception as exc:
        import sys

        print(f"openminion focus: error — {exc}", file=sys.stderr)
        return 1
    finally:
        if runtime is not None:
            runtime.close()


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    focus = subparsers.add_parser(
        "focus",
        help="Launch the focused single-agent shell",
        description=(
            "Launch the focused single-agent shell. Bare `openminion` uses "
            "this surface by default; use `openminion dashboard` for the "
            "monitoring / overview UI across chats and sessions."
        ),
    )
    focus.add_argument(
        "--agent",
        default=None,
        help="Agent id to activate for the focus session",
    )
    focus.add_argument(
        "--session",
        default=None,
        help="Existing focus session id to resume",
    )
    focus.add_argument(
        "--dir",
        default=None,
        help="Working directory to bind the focus session to",
    )
    focus.add_argument(
        "--theme",
        default=None,
        help=(
            "Theme variant override (e.g. light, dark). "
            "Top precedence — beats env and persisted preference."
        ),
    )
    focus.add_argument(
        "--no-interactive",
        action="store_true",
        help="Disable inline first-run setup and fail fast with remediation",
    )
    focus.add_argument(
        "--no-context",
        action="store_true",
        help="Do not auto-load OPENMINION.md/AGENTS.md/CLAUDE.md project context",
    )
    focus.add_argument(
        "--no-update-check",
        action="store_true",
        help="Disable the cached startup update-available notification.",
    )
    backend = focus.add_mutually_exclusive_group()
    backend.add_argument(
        "--rich",
        action="store_true",
        help=(
            "Use the Textual rich shell. This is the default for interactive "
            "TTY sessions; the flag is kept for explicitness."
        ),
    )
    backend.add_argument(
        "--terminal",
        action="store_true",
        help=(
            "Use the terminal-flow shell. Useful for piped stdin, minimal "
            "terminals, or debugging the terminal renderer."
        ),
    )
    from openminion.cli.ux.verbosity import (
        add_progress_flag,
        add_verbosity_flag,
    )

    add_verbosity_flag(focus)
    add_progress_flag(focus, include_aliases=True)
    focus.set_defaults(handler=run_focus, needs_app=False)
