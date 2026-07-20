from __future__ import annotations

import argparse
from collections.abc import Callable
import logging
from pathlib import Path
from typing import Any

from openminion.base.config import resolve_config_path
from openminion.cli.config import resolve_cli_roots
from openminion.modules.cli_common import has_tty
from openminion.services.bootstrap.onboarding import (
    OnboardingRequestedMode,
    OnboardingStatus,
    build_inline_setup_args,
    resolve_surface_onboarding_route,
)


def _silence_logging_for_interactive(args: Any) -> str:
    roots = resolve_cli_roots(
        config_path=getattr(args, "config", None),
        home_root=getattr(args, "home_root", None),
        data_root=getattr(args, "data_root", None),
    )
    log_dir = roots.data_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = str((log_dir / "interactive.log").resolve(strict=False))
    root = logging.getLogger()
    for handler in root.handlers[:]:
        root.removeHandler(handler)
    handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root.addHandler(handler)
    return log_path


def _inspect_interactive_onboarding(args: Any) -> OnboardingStatus:
    roots = resolve_cli_roots(
        config_path=getattr(args, "config", None),
        home_root=getattr(args, "home_root", None),
        data_root=getattr(args, "data_root", None),
    )
    config_path = resolve_config_path(
        getattr(args, "config", None), home_root=roots.home_root
    )
    return resolve_surface_onboarding_route(
        config_path=config_path,
        home_root=roots.home_root,
        data_root=roots.data_root,
        config_arg=getattr(args, "config", None),
        agent_id=str(getattr(args, "agent", "") or "").strip() or None,
        requested_mode=(
            OnboardingRequestedMode.DEMO
            if bool(getattr(args, "demo", False))
            else OnboardingRequestedMode.AUTO
        ),
        has_tty=has_tty(),
        no_interactive=bool(getattr(args, "no_interactive", False)),
        env=roots.env,
    ).status


def _run_inline_setup(args: Any) -> int:
    from openminion.cli.commands.setup import run_setup

    return int(
        run_setup(
            build_inline_setup_args(
                config=getattr(args, "config", None),
                home_root=getattr(args, "home_root", None),
                data_root=getattr(args, "data_root", None),
                no_chat=True,
                agent=getattr(args, "agent", None),
            )
        )
        or 0
    )


def _resolve_interactive_backend(args: argparse.Namespace) -> str:
    if bool(getattr(args, "rich", False)):
        return "textual"
    return "terminal"


def _resolve_plain_spinner(args: argparse.Namespace) -> bool:
    return _resolve_focus_progress(args) in ("minimal", "off")


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

    onboarding_status = _inspect_interactive_onboarding(args)
    if onboarding_status.action == OnboardingAction.FAIL_FAST:
        import sys

        from openminion.services.bootstrap.onboarding import format_fail_fast_message

        print(
            format_fail_fast_message(
                surface="openminion",
                status=onboarding_status,
            ),
            file=sys.stderr,
        )
        return 2, args
    if onboarding_status.action == OnboardingAction.LAUNCH_SETUP:
        if _run_inline_setup(args) != 0:
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
        "openminion --rich: the Textual shell "
        "requires an interactive terminal (TTY) on both "
        "stdin and stdout. Run from an interactive shell, use the default "
        "terminal renderer, or pipe a prompt to bare `openminion` for "
        "one-shot execution.",
        file=_sys.stderr,
    )
    return 2


def _launch_terminal_focus(
    args: argparse.Namespace, runtime, *, working_dir: str
) -> int:
    from openminion.cli.interactive.project_context import resolve_project_context
    from openminion.cli.interactive.runtime import OpenMinionRuntime
    from openminion.cli.interactive.terminal import run_terminal_focus

    requested_session = str(getattr(args, "session", "") or "").strip() or None
    terminal_runtime = OpenMinionRuntime(
        runtime,
        target="focus",
        agent_id=str(getattr(args, "agent", "") or "").strip() or None,
        working_dir=working_dir,
        bind_immediately=False,
        session_id=requested_session,
    )
    if requested_session is None:
        terminal_runtime.create_new_session()
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
    from openminion.cli.interactive import FocusApp
    from openminion.cli.interactive.project_context import resolve_project_context
    from openminion.cli.interactive.runtime import OpenMinionRuntime
    from openminion.cli.presentation.animation import resolve_focus_animation

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


def run_interactive(args: argparse.Namespace) -> int:
    from openminion.api.runtime import APIRuntime
    from openminion.cli.status.surface import record_surface_event

    gate_exit, args = _handle_focus_onboarding_gate(args)
    if gate_exit is not None:
        return gate_exit

    backend = _resolve_interactive_backend(args)
    _silence_logging_for_interactive(args)
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
        record_surface_event(runtime)
        if backend == "terminal":
            return _launch_terminal_focus(args, runtime, working_dir=working_dir)
        _maybe_print_update_notice(args)
        return _launch_textual_focus(args, runtime, working_dir=working_dir)
    except Exception as exc:
        import sys

        print(f"openminion: interactive startup error: {exc}", file=sys.stderr)
        return 1
    finally:
        if runtime is not None:
            runtime.close()
