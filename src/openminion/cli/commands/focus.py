from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from openminion.cli.commands import tui as _tui_commands


class _ForwardedTuiCommand:
    def __init__(self, name: str) -> None:
        self._name = name

    def __call__(self, args: Any) -> Any:
        return getattr(_tui_commands, self._name)(args)


_inspect_tui_onboarding = _ForwardedTuiCommand("_inspect_tui_onboarding")
_run_inline_setup_for_tui = _ForwardedTuiCommand("_run_inline_setup_for_tui")
_silence_logging_for_tui = _ForwardedTuiCommand("_silence_logging_for_tui")


def _legacy_terminal_requested(args: argparse.Namespace) -> bool:
    from openminion.base.config.env import EnvironmentConfig

    if bool(getattr(args, "terminal", False)):
        return True
    env = EnvironmentConfig.from_sources()
    env_value = str(env.get("OPENMINION_FOCUS_BACKEND", "") or "").strip().lower()
    return env_value in ("terminal", "flow", "terminal-flow")


def _resolve_focus_verbosity(args: argparse.Namespace) -> str:
    from openminion.cli.ux.verbosity import resolve_verbosity

    return resolve_verbosity(args)


def _resolve_focus_progress(args: argparse.Namespace) -> str:
    from openminion.cli.ux.verbosity import resolve_progress

    return resolve_progress(args, default="full")


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
        "openminion focus: the Textual shell "
        "requires an interactive terminal (TTY) on both "
        "stdin and stdout. Run from an interactive shell, or pipe a prompt "
        "to bare `openminion` for one-shot execution.",
        file=_sys.stderr,
    )
    return 2


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
    from openminion.cli.tui.project_context import resolve_project_context
    from openminion.cli.interactive import FocusApp
    from openminion.cli.presentation.animation import resolve_focus_animation
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
    animation = resolve_focus_animation(args)
    FocusApp(
        runtime=focus_runtime,
        working_dir=working_dir,
        agent=str(getattr(args, "agent", "") or "").strip() or None,
        session=str(getattr(args, "session", "") or "").strip() or None,
        theme=resolved_theme,
        verbosity=_resolve_focus_verbosity(args),
        progress=_resolve_focus_progress(args),
        animation=animation,
    ).run()
    return 0


def run_focus(args: argparse.Namespace) -> int:
    from openminion.api.runtime import APIRuntime
    from openminion.cli.status.surface import record_surface_event

    if _legacy_terminal_requested(args):
        import sys

        print(
            "openminion focus: the legacy terminal-flow renderer has been "
            "retired. Remove --terminal or OPENMINION_FOCUS_BACKEND=terminal "
            "to use the canonical Textual Focus shell.",
            file=sys.stderr,
        )
        return 2

    gate_exit, args = _handle_focus_onboarding_gate(args)
    if gate_exit is not None:
        return gate_exit

    _silence_logging_for_tui(args)
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
        surface = str(getattr(args, "surface", "focus") or "focus")
        record_surface_event(runtime, surface=surface, action="launch")
        if bool(getattr(args, "deprecation_notice_shown", False)):
            record_surface_event(runtime, surface=surface, action="deprecation")
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
            "Launch the OpenMinion interactive CLI. Bare `openminion` uses "
            "this Textual surface by default."
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
    focus.add_argument(
        "--animation-provider",
        default=None,
        help="Activity animation provider id (default: openminion).",
    )
    focus.add_argument(
        "--animation",
        default=None,
        help="Activity animation preset, or provider:preset shorthand.",
    )
    backend = focus.add_mutually_exclusive_group()
    backend.add_argument(
        "--rich",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    backend.add_argument(
        "--terminal",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    from openminion.cli.ux.verbosity import (
        add_progress_flag,
        add_verbosity_flag,
    )

    add_verbosity_flag(focus)
    add_progress_flag(focus, include_aliases=True)
    focus.set_defaults(handler=run_focus, needs_app=False)
