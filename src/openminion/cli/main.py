from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
import sys
from types import SimpleNamespace

from openminion.base.config.bootstrap import bootstrap_env, bootstrap_env_strict
from openminion.base.config.env import EnvironmentConfig
from openminion.cli.config import infer_workspace_home_root
from openminion.cli.parser.base import build_parser
from openminion.base.config import (
    ConfigError,
    resolve_config_path,
    run_profile_overrides_from_mapping,
)


def _prepare_runtime_roots(
    args: object, env_config: EnvironmentConfig
) -> tuple[str, str, str, str, str]:
    home_root = str(getattr(args, "home_root", "") or "").strip()
    if not home_root and not env_config.openminion_home.strip():
        inferred_home_root = infer_workspace_home_root(Path.cwd())
        if inferred_home_root is not None:
            home_root = str(inferred_home_root)
            bootstrap_env_strict(
                home_root=home_root,
                data_root=str(inferred_home_root / ".openminion"),
            )
    data_root = str(getattr(args, "data_root", "") or "").strip()
    generated_root = str(getattr(args, "generated_root", "") or "").strip()
    effective_home_root = home_root or env_config.openminion_home.strip()
    effective_data_root = data_root or env_config.openminion_data_root.strip()
    if effective_home_root:
        bootstrap_env(
            home_root=effective_home_root,
            data_root=effective_data_root or None,
            generated_root=generated_root or None,
        )
    if home_root and effective_data_root:
        bootstrap_env_strict(
            home_root=home_root,
            data_root=effective_data_root,
            generated_root=generated_root or None,
        )
    return (
        home_root,
        data_root,
        generated_root,
        effective_home_root,
        effective_data_root,
    )


def _run_setup_from_default_route(args: object, home_root: str, data_root: str) -> int:
    from openminion.cli.commands.setup import run_setup
    from openminion.services.bootstrap.onboarding import build_inline_setup_args

    return int(
        run_setup(
            build_inline_setup_args(
                config=getattr(args, "config", None),
                home_root=home_root or None,
                data_root=data_root or None,
                no_chat=False,
                agent=None,
            )
        )
        or 0
    )


def _default_route_home_root(effective_home_root: str) -> Path:
    if effective_home_root:
        return Path(effective_home_root).expanduser().resolve()
    return Path.cwd().resolve()


def _default_route_data_root(
    effective_home_root: str, effective_data_root: str
) -> Path:
    if effective_data_root:
        return Path(effective_data_root).expanduser().resolve()
    return (_default_route_home_root(effective_home_root) / ".openminion").resolve()


def _run_default_focus(
    args: object,
    home_root: str,
    data_root: str,
    *,
    no_interactive: bool,
) -> int:
    from openminion.cli.commands.focus import run_focus

    return int(
        run_focus(
            SimpleNamespace(
                config=getattr(args, "config", None),
                home_root=home_root or None,
                data_root=data_root or None,
                no_interactive=no_interactive,
                agent=None,
                session=None,
                working_dir=None,
                no_resume=False,
                rich=True,
            )
        )
        or 0
    )


def _run_piped_prompt(args: object, prompt: str) -> int:
    from openminion.cli.commands.run import run_openminion

    return int(
        run_openminion(
            SimpleNamespace(
                config=getattr(args, "config", None),
                prompt=prompt,
                file="",
                agent=None,
                session=None,
                resume=False,
                reset_session=False,
                purpose="piped-input",
                stream=False,
                json=False,
            )
        )
        or 0
    )


def _run_no_handler(
    args: object,
    parser,
    home_root: str,
    data_root: str,
    effective_home_root: str,
    effective_data_root: str,
) -> int:
    has_tty = bool(getattr(sys.stdin, "isatty", lambda: False)()) and bool(
        getattr(sys.stdout, "isatty", lambda: False)()
    )
    from openminion.services.bootstrap.onboarding import (
        OnboardingRequestedMode,
        format_fail_fast_message,
        resolve_surface_onboarding_route,
    )

    config_path = resolve_config_path(
        getattr(args, "config", None),
        home_root=_default_route_home_root(effective_home_root)
        if effective_home_root
        else None,
    )
    route = resolve_surface_onboarding_route(
        config_path=config_path,
        home_root=_default_route_home_root(effective_home_root),
        data_root=_default_route_data_root(effective_home_root, effective_data_root),
        config_arg=getattr(args, "config", None),
        requested_mode=OnboardingRequestedMode.AUTO,
        has_tty=has_tty,
        no_interactive=bool(getattr(args, "no_interactive", False)),
        env=EnvironmentConfig.from_sources(),
    )
    status = route.status
    if route.should_launch_setup:
        return _run_setup_from_default_route(args, home_root, data_root)
    if route.should_fail_fast:
        parser.exit(
            status=2,
            message=format_fail_fast_message(
                surface="openminion",
                status=status,
            )
            + "\n",
        )
    if has_tty:
        return _run_default_focus(
            args,
            home_root,
            data_root,
            no_interactive=bool(getattr(args, "no_interactive", False)),
        )
    if not sys.stdin.isatty():
        try:
            stdin_text = sys.stdin.read()
        except (OSError, ValueError):
            stdin_text = ""
        if stdin_text.strip():
            return _run_piped_prompt(args, stdin_text.strip())
        parser.print_help()
        return 1
    parser.print_help()
    return 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if bool(getattr(args, "allow_unsandboxed_exec", False)):
        from openminion.services.runtime.env import apply_runtime_environment
        from openminion.tools.exec.constants import EXEC_ENABLE_HOST_EXEC_ENV

        apply_runtime_environment(
            {EXEC_ENABLE_HOST_EXEC_ENV: "1"},
            overwrite=True,
        )
    env_config = EnvironmentConfig.from_sources()
    (
        home_root,
        data_root,
        _generated_root,
        effective_home_root,
        effective_data_root,
    ) = _prepare_runtime_roots(args, env_config)

    handler = getattr(args, "handler", None)
    if handler is None:
        return _run_no_handler(
            args,
            parser,
            home_root,
            data_root,
            effective_home_root,
            effective_data_root,
        )

    try:
        if getattr(args, "needs_app", False):
            from openminion.api.runtime import APIRuntime

            app = APIRuntime.from_config_path(
                args.config,
                home_root=home_root or None,
                data_root=data_root or None,
                run_profile_overrides=run_profile_overrides_from_mapping(vars(args)),
            )
            return int(handler(args, app) or 0)
        return int(handler(args) or 0)
    except (ConfigError, RuntimeError, KeyError) as exc:
        parser.exit(status=2, message=f"openminion: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
