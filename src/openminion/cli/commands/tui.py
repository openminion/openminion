from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Any

from openminion.base.config import resolve_config_path
from openminion.cli.config import load_cli_config, resolve_cli_roots
from openminion.cli.identity.sync import sync_cli_identity_profiles
from openminion.cli.ux.deprecation import (
    deprecation_suppressed,
    print_deprecation_notice,
)
from openminion.modules.cli_common import has_tty
from openminion.services.bootstrap.onboarding import (
    OnboardingAction,
    OnboardingRequestedMode,
    OnboardingState,
    OnboardingStatus,
    build_inline_setup_args,
    format_fail_fast_message,
    resolve_surface_onboarding_route,
)

_DASHBOARD_NOTICE = (
    "openminion dashboard is deprecated and remains available for migration. "
    "Use bare `openminion` for interactive work and the documented CLI/API "
    "surfaces for operations. Suppress this notice with "
    "OPENMINION_DASHBOARD_NO_DEPRECATION=1."
)
_TUI_NOTICE = (
    "openminion tui is a compatibility alias; use bare `openminion` or "
    "`openminion focus`. Suppress this notice with "
    "OPENMINION_TUI_NO_DEPRECATION=1."
)


def dashboard_deprecation_message() -> str:
    """Return the dashboard migration notice unless the operator suppressed it."""
    if deprecation_suppressed("OPENMINION_DASHBOARD_NO_DEPRECATION"):
        return ""
    return _DASHBOARD_NOTICE


def launch_dashboard(
    *,
    app_runtime: Any | None,
    providers: Any,
    no_picker: bool = False,
    initial_tab: str | None = None,
    theme: Any = None,
    onboarding_request: dict | None = None,
    owns_runtime: bool,
    close_runtime: Any = None,
) -> int:
    from openminion.cli.tui.app import OpenMinionApp

    kwargs: dict[str, Any] = {"providers": providers}
    if app_runtime is not None:
        kwargs["runtime"] = app_runtime
    if no_picker:
        kwargs["no_picker"] = True
    if initial_tab is not None:
        kwargs["initial_tab"] = initial_tab
    if theme is not None:
        kwargs["theme"] = theme
    if onboarding_request is not None:
        kwargs["onboarding_request"] = onboarding_request
    try:
        result = OpenMinionApp(**kwargs).run()
        return int(result) if isinstance(result, int) else 0
    finally:
        if owns_runtime and close_runtime is not None:
            try:
                close_runtime()
            except Exception:
                # Bounded fallback — close failure must not mask
                import sys as _sys

                print(
                    "launch_dashboard: close_runtime() raised; ignored.",
                    file=_sys.stderr,
                )


def _normalized_agent(args: Any) -> str:
    return str(getattr(args, "agent", "") or "").strip()


def _silence_logging_for_tui(args: Any) -> str | None:
    roots = resolve_cli_roots(
        config_path=getattr(args, "config", None),
        home_root=getattr(args, "home_root", None),
        data_root=getattr(args, "data_root", None),
    )
    log_dir = roots.data_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = str((Path(log_dir) / "tui.log").resolve(strict=False))

    root = logging.getLogger()
    for handler in root.handlers[:]:
        root.removeHandler(handler)
    fh = logging.FileHandler(log_path, mode="a", encoding="utf-8")
    fh.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root.addHandler(fh)
    return log_path


def _inspect_tui_onboarding(args: Any) -> OnboardingStatus:
    roots = resolve_cli_roots(
        config_path=getattr(args, "config", None),
        home_root=getattr(args, "home_root", None),
        data_root=getattr(args, "data_root", None),
    )
    config_path = resolve_config_path(
        getattr(args, "config", None),
        home_root=roots.home_root,
    )
    route = resolve_surface_onboarding_route(
        config_path=config_path,
        home_root=roots.home_root,
        data_root=roots.data_root,
        config_arg=getattr(args, "config", None),
        agent_id=_normalized_agent(args) or None,
        requested_mode=(
            OnboardingRequestedMode.DEMO
            if bool(getattr(args, "demo", False))
            else OnboardingRequestedMode.AUTO
        ),
        has_tty=has_tty(),
        no_interactive=bool(getattr(args, "no_interactive", False)),
        env=roots.env,
    )
    return route.status


def _run_inline_setup_for_tui(args: Any) -> int:
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


def _should_soften_unknown_agent_failure_for_tui(
    *,
    requested_agent: str,
    onboarding_status: Any,
) -> bool:
    if not requested_agent:
        return False
    if getattr(onboarding_status, "action", None) != OnboardingAction.FAIL_FAST:
        return False
    if getattr(onboarding_status, "state", None) != OnboardingState.CONFIG_ERROR:
        return False
    reason = str(getattr(onboarding_status, "reason", "") or "").strip().lower()
    return reason.startswith("unknown agent profile ")


def _resolve_tui_theme(args):
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
        # Bounded failure: fall back to default rather than block startup.
        return resolve_theme(cli_flag=cli_theme)


def _launch_tui_demo(requested_agent: str, *, resolved_theme) -> int:
    from openminion.cli.parser.contracts import ProviderBundle
    from openminion.cli.tui.app import DemoRuntime

    runtime = DemoRuntime()
    if requested_agent:
        runtime.switch_agent(requested_agent)
    return launch_dashboard(
        app_runtime=runtime,
        providers=ProviderBundle.all_demo(),
        no_picker=True,
        theme=resolved_theme,
        owns_runtime=True,
        close_runtime=None,
    )


def _handle_tui_onboarding_gate(
    args, *, requested_agent: str
) -> tuple[int | None, str, str, "argparse.Namespace"]:
    onboarding_status = _inspect_tui_onboarding(args)
    ignored_requested_agent = ""
    softened_unknown_agent = False
    if _should_soften_unknown_agent_failure_for_tui(
        requested_agent=requested_agent,
        onboarding_status=onboarding_status,
    ):
        ignored_requested_agent = requested_agent
        requested_agent = ""
        softened_unknown_agent = True
    if (
        onboarding_status.action == OnboardingAction.FAIL_FAST
        and not softened_unknown_agent
    ):
        import sys

        print(
            format_fail_fast_message(
                surface="openminion tui",
                status=onboarding_status,
            ),
            file=sys.stderr,
        )
        return 2, requested_agent, ignored_requested_agent, args
    if onboarding_status.action == OnboardingAction.LAUNCH_SETUP:
        if _run_inline_setup_for_tui(args) != 0:
            return 1, requested_agent, ignored_requested_agent, args
        args = argparse.Namespace(**vars(args))
        args.no_interactive = False
    return None, requested_agent, ignored_requested_agent, args


def _bootstrap_live_runtime(args):
    from openminion.api.runtime import APIRuntime

    sync_cli_identity_profiles(
        enabled=bool(getattr(args, "sync_identity", False)),
        config=load_cli_config(
            getattr(args, "config", None),
            home_root=getattr(args, "home_root", None),
            data_root=getattr(args, "data_root", None),
        ),
        roots=resolve_cli_roots(
            config_path=getattr(args, "config", None),
            home_root=getattr(args, "home_root", None),
            data_root=getattr(args, "data_root", None),
        ),
    )
    return APIRuntime.from_config_path(
        getattr(args, "config", None),
        home_root=getattr(args, "home_root", None),
        data_root=getattr(args, "data_root", None),
    )


def _resolve_initial_tab(
    runtime, *, requested_agent: str, ignored_requested_agent: str
) -> str | None:
    if ignored_requested_agent:
        return "tab-agents"
    if requested_agent:
        return None
    registered = []
    if callable(getattr(runtime, "list_registered_agents", None)):
        registered = runtime.list_registered_agents()
    if len(registered) > 1:
        return "tab-agents"
    return None


def run_tui(args) -> int:
    from openminion.cli.status.surface import record_surface_event

    notice_shown = print_deprecation_notice(
        _DASHBOARD_NOTICE,
        suppression_env="OPENMINION_DASHBOARD_NO_DEPRECATION",
    )
    requested_agent = _normalized_agent(args)
    no_picker = bool(getattr(args, "no_picker", False))
    resolved_theme = _resolve_tui_theme(args)

    if bool(getattr(args, "demo", False)):
        return _launch_tui_demo(requested_agent, resolved_theme=resolved_theme)

    gate_exit, requested_agent, ignored_requested_agent, args = (
        _handle_tui_onboarding_gate(args, requested_agent=requested_agent)
    )
    if gate_exit is not None:
        return gate_exit

    _silence_logging_for_tui(args)

    from openminion.cli.parser.contracts import ProviderBundle
    from openminion.cli.tui.providers import OpenMinionRuntime

    try:
        runtime = _bootstrap_live_runtime(args)
    except Exception as exc:
        import sys

        print(f"openminion tui: startup error — {exc}", file=sys.stderr)
        return 1

    try:
        surface = str(getattr(args, "surface", "dashboard") or "dashboard")
        record_surface_event(runtime, surface=surface, action="launch")
        if notice_shown:
            record_surface_event(runtime, surface=surface, action="deprecation")
        tui_runtime = OpenMinionRuntime(runtime, prompt_on_resume=True)
        if requested_agent and requested_agent != tui_runtime.agent_id:
            tui_runtime.switch_agent(requested_agent)
        bundle = ProviderBundle.from_api_runtime(runtime)
        initial_tab = _resolve_initial_tab(
            runtime,
            requested_agent=requested_agent,
            ignored_requested_agent=ignored_requested_agent,
        )
        return launch_dashboard(
            app_runtime=tui_runtime,
            providers=bundle,
            no_picker=no_picker,
            initial_tab=initial_tab,
            theme=resolved_theme,
            owns_runtime=True,
            close_runtime=lambda: runtime.close(),
        )
    except Exception as exc:
        import sys

        print(f"openminion tui: error — {exc}", file=sys.stderr)
        return 1
    finally:
        try:
            runtime.close()
        except Exception:
            pass


def run_tui_entry(args) -> int:
    """Compatibility `tui` entrypoint.

    `openminion tui` now follows the primary interactive surface and launches
    focus mode. The monitoring dashboard remains available through the
    canonical `dashboard` command and the explicit `tui --dashboard` flag.
    """
    if bool(getattr(args, "dashboard", False)):
        args.surface = "tui-dashboard"
        return run_tui(args)
    from openminion.cli.commands.focus import run_focus

    args.surface = "tui"
    args.deprecation_notice_shown = print_deprecation_notice(
        _TUI_NOTICE,
        suppression_env="OPENMINION_TUI_NO_DEPRECATION",
    )
    return int(run_focus(args) or 0)


def _register_dashboard_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Run dashboard with demo runtime/providers (no live runtime wiring)",
    )
    parser.add_argument(
        "--agent",
        default=None,
        help="Agent id to activate when opening a session from the dashboard",
    )
    parser.add_argument(
        "--no-picker",
        dest="no_picker",
        action="store_true",
        help="Skip the session picker at launch and open the chat overview directly",
    )
    parser.add_argument(
        "--sync-identity",
        action="store_true",
        help="Refresh YAML-backed identity profiles into SQLite and regenerate generated markdown sidecars before dashboard startup",
    )
    parser.add_argument(
        "--no-interactive",
        action="store_true",
        help="Disable inline first-run setup and fail fast with remediation",
    )
    parser.add_argument(
        "--theme",
        default=None,
        help=(
            "Theme variant override (e.g. light, dark). "
            "Top precedence — beats env and persisted preference."
        ),
    )


def _register_tui_focus_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--dashboard",
        action="store_true",
        help="Launch the monitoring / overview dashboard instead of focus mode",
    )
    parser.add_argument(
        "--session",
        default=None,
        help="Existing focus session id to resume",
    )
    parser.add_argument(
        "--dir",
        default=None,
        help="Working directory to bind the focus session to",
    )
    parser.add_argument(
        "--no-context",
        action="store_true",
        help="Do not auto-load OPENMINION.md/AGENTS.md/CLAUDE.md project context",
    )
    parser.add_argument(
        "--no-update-check",
        action="store_true",
        help="Disable the cached startup update-available notification.",
    )
    backend = parser.add_mutually_exclusive_group()
    backend.add_argument(
        "--rich",
        action="store_true",
        help="Use the Textual rich focus shell",
    )
    backend.add_argument(
        "--terminal",
        action="store_true",
        help="Use the terminal-flow focus shell",
    )
    from openminion.cli.ux.verbosity import (
        add_progress_flag,
        add_verbosity_flag,
    )

    add_verbosity_flag(parser)
    add_progress_flag(parser, include_aliases=True)


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    dashboard = subparsers.add_parser(
        "dashboard",
        help="Launch the deprecated monitoring dashboard during migration",
        description=(
            "Launch the deprecated full-screen OpenMinion dashboard. Use bare "
            "`openminion` for the default focus shell; use this command for "
            "monitoring and overview across chats, sessions, agents, tools, "
            "memory, cron, and system state."
        ),
    )
    _register_dashboard_args(dashboard)
    dashboard.set_defaults(handler=run_tui, needs_app=False)

    tui = subparsers.add_parser(
        "tui",
        help="Compatibility alias for focus; --dashboard opens the deprecated dashboard",
        description=(
            "`openminion tui` launches the default focus shell for one-agent "
            "work. Use `openminion tui --dashboard` or `openminion dashboard` "
            "for the monitoring / overview UI across chats and sessions."
        ),
    )
    _register_dashboard_args(tui)
    _register_tui_focus_args(tui)
    tui.set_defaults(handler=run_tui_entry, needs_app=False)
