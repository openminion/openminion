from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
import os
from pathlib import Path
import signal
import sqlite3
import sys
import threading
import argparse
from types import SimpleNamespace
from typing import Any, cast

from openminion.base.config.bootstrap import bootstrap_env, bootstrap_env_strict
from openminion.base.config.env import EnvironmentConfig
from openminion.cli.config import infer_workspace_home_root
from openminion.cli.parser.base import build_parser
from openminion.cli.presentation.json_output import print_json_payload
from openminion.base.config import (
    ConfigError,
    resolve_config_path,
    run_profile_overrides_from_mapping,
)


def resolve_surface_onboarding_route(**kwargs: Any) -> Any:
    from openminion.services.bootstrap.onboarding import (
        resolve_surface_onboarding_route as _resolve_surface_onboarding_route,
    )

    return _resolve_surface_onboarding_route(**kwargs)


def format_fail_fast_message(**kwargs: Any) -> str:
    from openminion.services.bootstrap.onboarding import (
        format_fail_fast_message as _format_fail_fast_message,
    )

    return str(_format_fail_fast_message(**kwargs))


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
        if data_root or generated_root:
            strict_generated_root = generated_root or (
                _default_generated_root_for_data(
                    effective_home_root=effective_home_root,
                    effective_data_root=effective_data_root,
                )
                if data_root and effective_data_root
                else None
            )
            bootstrap_env_strict(
                home_root=effective_home_root,
                data_root=effective_data_root
                or str((Path(effective_home_root) / ".openminion").resolve()),
                generated_root=strict_generated_root,
            )
        else:
            bootstrap_env(
                home_root=effective_home_root,
                data_root=effective_data_root or None,
                generated_root=None,
            )
    if home_root and effective_data_root and not (data_root or generated_root):
        bootstrap_env_strict(
            home_root=home_root,
            data_root=effective_data_root,
            generated_root=None,
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

    setup_args = SimpleNamespace(**vars(args))
    setup_args.home_root = home_root or None
    setup_args.data_root = data_root or None
    setup_args.no_chat = False
    return int(run_setup(setup_args) or 0)


def _default_route_home_root(effective_home_root: str) -> Path:
    if effective_home_root:
        return Path(effective_home_root).expanduser().resolve()
    return Path.home().resolve()


def _default_route_data_root(
    effective_home_root: str, effective_data_root: str
) -> Path:
    if effective_data_root:
        return Path(effective_data_root).expanduser().resolve()
    return (_default_route_home_root(effective_home_root) / ".openminion").resolve()


def _default_generated_root_for_data(
    *, effective_home_root: str, effective_data_root: str
) -> str:
    data_path = Path(effective_data_root)
    if not data_path.is_absolute():
        data_path = _default_route_home_root(effective_home_root) / data_path
    return str((data_path / "runtime").resolve())


def _run_default_interactive(
    args: object,
    home_root: str,
    data_root: str,
    *,
    no_interactive: bool,
) -> int:
    from openminion.cli.commands.interactive import run_interactive

    return int(
        run_interactive(
            argparse.Namespace(
                config=getattr(args, "config", None),
                home_root=home_root or None,
                data_root=data_root or None,
                no_interactive=no_interactive,
                agent=getattr(args, "agent", None),
                session=getattr(args, "session", None),
                resume_name=getattr(args, "resume_name", None),
                dir=getattr(args, "dir", None),
                add_dir=list(getattr(args, "add_dir", []) or []),
                theme=getattr(args, "theme", None),
                color=getattr(args, "color", None),
                demo=bool(getattr(args, "demo", False)),
                no_context=bool(getattr(args, "no_context", False)),
                no_update_check=bool(getattr(args, "no_update_check", False)),
                animation_provider=getattr(args, "animation_provider", None),
                animation=getattr(args, "animation", None),
                verbosity=getattr(args, "verbosity", None),
                progress=getattr(args, "progress", None),
                onboarding_checked=True,
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
                agent=getattr(args, "agent", None),
                session=getattr(args, "session", None),
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
    parser: Any,
    home_root: str,
    data_root: str,
    effective_home_root: str,
    effective_data_root: str,
) -> int:
    from openminion.services.bootstrap.onboarding import OnboardingRequestedMode

    has_tty = bool(getattr(sys.stdin, "isatty", lambda: False)()) and bool(
        getattr(sys.stdout, "isatty", lambda: False)()
    )
    if getattr(args, "add_dir", None) and not has_tty:
        parser.error("--add-dir requires a TTY bare interactive launch")
    config_path = resolve_config_path(
        getattr(args, "config", None),
        home_root=_default_route_home_root(effective_home_root),
    )
    route = resolve_surface_onboarding_route(
        config_path=config_path,
        home_root=_default_route_home_root(effective_home_root),
        data_root=_default_route_data_root(effective_home_root, effective_data_root),
        config_arg=getattr(args, "config", None),
        requested_mode=(
            OnboardingRequestedMode.DEMO
            if bool(getattr(args, "demo", False))
            else OnboardingRequestedMode.AUTO
        ),
        has_tty=has_tty,
        no_interactive=bool(getattr(args, "no_interactive", False)),
        env=EnvironmentConfig.from_sources(),
    )
    status = route.status
    if route.should_launch_setup:
        return _run_setup_from_default_route(
            args,
            str(route.home_root),
            str(route.data_root),
        )
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
        return _run_default_interactive(
            args,
            str(route.home_root),
            str(route.data_root),
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


def main(argv: Sequence[str] | None = None, *, owns_process: bool = False) -> int:
    """Run the CLI.

    ``owns_process`` is set only by the process entry points (the console script and
    ``python -m openminion``). An imported call leaves it false, so a host's signal
    handling and cleanup are never taken over.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "command", None) and getattr(args, "add_dir", None):
        parser.error("--add-dir is only valid with a bare interactive launch")
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
        with _cancel_on_signal(
            owns_process and getattr(args, "command", None) == "run"
        ):
            if getattr(args, "needs_app", False):
                from openminion.api.runtime import APIRuntime

                app = APIRuntime.from_config_path(
                    args.config,
                    home_root=home_root or None,
                    data_root=data_root or None,
                    run_profile_overrides=run_profile_overrides_from_mapping(
                        vars(args)
                    ),
                )
                return int(handler(args, app) or 0)
            return int(handler(args) or 0)
    except (ConfigError, RuntimeError, KeyError) as exc:
        parser.exit(status=2, message=f"openminion: {exc}\n")
        return 2
    except KeyboardInterrupt:
        print("openminion: cancelled", file=sys.stderr)
        return 130
    except _TERMINAL_CLI_ERRORS as exc:
        return _unexpected_error_exit(args, exc)


_ABORT_EXIT_CODES: dict[int, int] = {signal.SIGINT: 130, signal.SIGTERM: 143}
_ABORT_MESSAGES: dict[int, str] = {
    signal.SIGINT: "openminion: cancelled",
    signal.SIGTERM: "openminion: terminated",
}


def _abort_on_signal(signum: int, _frame: object | None = None) -> None:
    """Exit promptly instead of joining in-flight worker threads.

    A cancelled turn leaves the synchronous provider call running in a worker thread;
    graceful shutdown joins it, which is why Ctrl-C appeared to hang for as long as
    the upstream request. ``os._exit`` skips interpreter teardown, so whatever that
    worker would still have written (telemetry, partial artifacts) is lost; the
    stores themselves are crash-safe. This is only installed for commands that own
    the process, never for embedded callers.
    """
    code = _ABORT_EXIT_CODES.get(signum, 1)
    message = _ABORT_MESSAGES.get(signum, "openminion: aborted")
    try:
        print(message, file=sys.stderr, flush=True)
    finally:
        os._exit(code)


@contextmanager
def _cancel_on_signal(owns_process: bool = False) -> Iterator[None]:
    """Install prompt-exit handlers only when this invocation owns the process."""
    if threading.current_thread() is not threading.main_thread() or not owns_process:
        yield
        return
    previous: dict[int, object] = {}
    for signum in _ABORT_EXIT_CODES:
        try:
            previous[signum] = signal.signal(signum, _abort_on_signal)
        except (ValueError, OSError, RuntimeError):
            continue
    try:
        yield
    finally:
        for signum, handler in previous.items():
            try:
                signal.signal(signum, handler)  # type: ignore[arg-type]
            except (ValueError, OSError, RuntimeError):
                continue


def _traceback_requested(args: object) -> bool:
    """True when the documented verbosity sources resolve to ``verbose``."""
    from openminion.cli.ux.verbosity import resolve_verbosity

    level = cast(str, resolve_verbosity(args))
    return level == "verbose"


# Terminal failure families the CLI reports instead of letting them traceback.
# Deliberately not ``Exception``: ``validate.broad_exception`` forbids new blanket
# handlers, and a programming error (TypeError/AttributeError) should keep its
# traceback rather than be dressed up as an operational failure.
_TERMINAL_CLI_ERRORS: tuple[type[BaseException], ...] = (
    LookupError,
    OSError,
    RuntimeError,
    ValueError,
    sqlite3.DatabaseError,
)


def _unexpected_error_exit(args: object, exc: BaseException) -> int:
    """Turn a handler failure into one scriptable line instead of a traceback.

    Covers failures raised inside a dispatched command handler, not startup before
    dispatch or programming errors such as ``TypeError``, which keep their traceback.
    """
    if _traceback_requested(args):
        raise exc
    detail = str(exc).strip()
    message = f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__
    one_line = " ".join(message.splitlines())
    if bool(getattr(args, "json", False)):
        print_json_payload(
            {
                "ok": False,
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": message,
                    "hint": "rerun with --verbosity verbose for a traceback",
                },
            }
        )
    else:
        # The plain renderer is one physical line; JSON keeps the message verbatim.
        print(
            f"openminion: internal error: {one_line} "
            "(rerun with --verbosity verbose for a traceback)",
            file=sys.stderr,
        )
    return 1


def entrypoint() -> int:
    """Process entry point: this invocation owns cancellation for the whole process."""
    return main(sys.argv[1:], owns_process=True)


if __name__ == "__main__":
    raise SystemExit(entrypoint())
