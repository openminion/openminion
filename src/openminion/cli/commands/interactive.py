import argparse
from collections.abc import Callable
import logging
from pathlib import Path
import subprocess
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


def _resolve_focus_verbosity(args: argparse.Namespace) -> str:
    from openminion.cli.ux.verbosity import resolve_verbosity

    return resolve_verbosity(args)


def _resolve_focus_progress(args: argparse.Namespace) -> str:
    from openminion.cli.ux.verbosity import resolve_progress

    return resolve_progress(args, default="full")


def _resolve_workspace_access(
    args: argparse.Namespace,
) -> tuple[str, bool, tuple[str, ...]]:
    explicit = getattr(args, "dir", None) is not None
    workspace = Path(getattr(args, "dir", None) or ".").expanduser().resolve()
    if not workspace.is_dir():
        raise ValueError(f"workspace directory does not exist: {workspace}")
    if not explicit and workspace in {Path.home().resolve(), Path(workspace.anchor)}:
        raise ValueError("choose a project workspace with --dir PATH")

    read_only = False
    if not explicit:
        try:
            probe = subprocess.run(
                ["git", "-C", str(workspace), "rev-parse", "--is-inside-work-tree"],
                check=False,
                capture_output=True,
                text=True,
            )
            read_only = probe.returncode != 0 or probe.stdout.strip() != "true"
        except OSError:
            read_only = True

    added_roots: list[str] = []
    for value in getattr(args, "add_dir", []) or []:
        root = Path(value).expanduser().resolve()
        if not root.is_dir():
            raise ValueError(f"added directory does not exist: {value}")
        if str(root) not in added_roots:
            added_roots.append(str(root))
    return str(workspace), read_only, tuple(added_roots)


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


def _launch_terminal_focus(
    args: argparse.Namespace,
    runtime,
    *,
    working_dir: str,
    read_only: bool = False,
    added_roots: tuple[str, ...] = (),
) -> int:
    from openminion.cli.interactive.project_context import resolve_project_context
    from openminion.cli.interactive.runtime import OpenMinionRuntime
    from openminion.cli.interactive.terminal import run_terminal_focus
    from openminion.cli.presentation.animation import resolve_focus_animation

    requested_agent = str(getattr(args, "agent", "") or "").strip() or None
    requested_session = str(getattr(args, "session", "") or "").strip() or None
    runtime_kwargs: dict[str, Any] = {
        "target": "focus",
        "agent_id": requested_agent,
        "working_dir": working_dir,
        "bind_immediately": False,
        "session_id": requested_session,
    }
    if added_roots:
        runtime_kwargs["added_workspace_roots"] = added_roots
    terminal_runtime = OpenMinionRuntime(runtime, **runtime_kwargs)
    if read_only:
        terminal_runtime.set_read_only_mode(True)
    if requested_session is None:
        terminal_runtime.create_new_session()
    if not bool(getattr(args, "no_context", False)):
        terminal_runtime.set_project_context(resolve_project_context(working_dir))
    progress = _resolve_focus_progress(args)
    return run_terminal_focus(
        terminal_runtime,
        working_dir=working_dir,
        agent=requested_agent,
        session=requested_session,
        plain_spinner=progress in ("minimal", "off"),
        verbosity=_resolve_focus_verbosity(args),
        progress=progress,
        animation=resolve_focus_animation(args),
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
        return "" if result is None else result.render_notice()
    except Exception:
        return ""


def _build_update_notice_resolver(
    args: argparse.Namespace,
) -> Callable[[], str] | None:
    if bool(getattr(args, "no_update_check", False)):
        return None

    return lambda: _resolve_update_notice(args)


def run_interactive(args: argparse.Namespace) -> int:
    from openminion.cli.presentation.styles import set_color_mode

    set_color_mode(getattr(args, "color", None))

    gate_exit, args = _handle_focus_onboarding_gate(args)
    if gate_exit is not None:
        return gate_exit

    _silence_logging_for_interactive(args)

    runtime = None
    try:
        working_dir, read_only, added_roots = _resolve_workspace_access(args)

        from openminion.api.runtime import APIRuntime
        from openminion.cli.status.surface import record_surface_event

        runtime = APIRuntime.from_config_path(
            getattr(args, "config", None),
            home_root=getattr(args, "home_root", None),
            data_root=getattr(args, "data_root", None),
            logging_mode="interactive",
        )
        record_surface_event(runtime)
        return _launch_terminal_focus(
            args,
            runtime,
            working_dir=working_dir,
            read_only=read_only,
            added_roots=added_roots,
        )
    except Exception as exc:
        import sys

        print(f"openminion: interactive startup error: {exc}", file=sys.stderr)
        return 1
    finally:
        if runtime is not None:
            runtime.close()
