"""Typer app wiring for the tool CLI."""

import sys as _sys
from pathlib import Path
from typing import Any, Optional

import typer

from openminion.base.config import resolve_data_root, resolve_home_root
from openminion.base.config.env import resolve_environment_config
from openminion.base.generated_paths import resolve_generated_config_path
from openminion.modules.cli_common import (
    DATA_ROOT_OPTION_HELP,
    HOME_ROOT_OPTION_HELP,
    apply_home_data_root_env,
)

from ..constants import DEFAULT_POLICY_FILENAME, OPENMINION_POLICY_PATH_ENV
from ..contracts.schemas import CallRequest, ResultEnvelope, Scope
from ..registry import ToolRegistry
from ..runtime import create_run_root
from ..runtime.policy import Policy
from . import runtime as cli_runtime
from .core_commands import register_core_commands
from .exec_commands import register_exec_commands
from .pinchtab_commands import register_pinchtab_commands


_CLI_PKG_NAME = "openminion.modules.tool.cli"


def _via_cli(name: str):
    return getattr(_sys.modules[_CLI_PKG_NAME], name)


__all__ = [
    "app",
    "tool_app",
    "policy_app",
    "plugins_app",
    "browser_app",
    "pinchtab_app",
    "pinchtab_instance_app",
    "pinchtab_tab_app",
    "pinchtab_daemon_app",
    "exec_app",
    "DEFAULT_POLICY_PATH",
    "create_run_root",  # re-exported so tests can monkeypatch via `cli`
    "main",
    "_print_obj",
    "_parse_call_payload",
    "_build_registry",
    "_effective_scope",
    "_write_run_meta",
    "_raise_if_denied",
    "_print_envelope",
    "_execute_call_payload",
    "_invoke_pinchtab_tool",
    "_pinchtab_daemon_config",
    "_is_unknown_browser_tool_error",
    "_map_pinchtab_to_browser_call",
    "_parse_env_pairs",
    "_invoke_exec_tool",
    "_finalize_cli_call",
]


app = typer.Typer(add_completion=False, no_args_is_help=True)
tool_app = typer.Typer(add_completion=False, no_args_is_help=True)
policy_app = typer.Typer(add_completion=False, no_args_is_help=True)
plugins_app = typer.Typer(add_completion=False, no_args_is_help=True)
browser_app = typer.Typer(add_completion=False, no_args_is_help=True)
pinchtab_app = typer.Typer(add_completion=False, no_args_is_help=True)
pinchtab_instance_app = typer.Typer(add_completion=False, no_args_is_help=True)
pinchtab_tab_app = typer.Typer(add_completion=False, no_args_is_help=True)
pinchtab_daemon_app = typer.Typer(add_completion=False, no_args_is_help=True)
exec_app = typer.Typer(add_completion=False, no_args_is_help=True)

app.add_typer(tool_app, name="tool")
app.add_typer(policy_app, name="policy")
app.add_typer(plugins_app, name="plugins")
app.add_typer(browser_app, name="browser")
app.add_typer(exec_app, name="exec")
browser_app.add_typer(pinchtab_app, name="pinchtab")
pinchtab_app.add_typer(pinchtab_instance_app, name="instance")
pinchtab_app.add_typer(pinchtab_tab_app, name="tab")
pinchtab_app.add_typer(pinchtab_daemon_app, name="daemon")


@app.callback()
def _global_options(
    home_root: Optional[Path] = typer.Option(
        None,
        "--home-root",
        help=HOME_ROOT_OPTION_HELP,
    ),
    data_root: Optional[Path] = typer.Option(
        None,
        "--data-root",
        help=DATA_ROOT_OPTION_HELP,
    ),
) -> None:
    apply_home_data_root_env(home_root=home_root, data_root=data_root)


_POLICY_OVERRIDE = (
    resolve_environment_config().get(OPENMINION_POLICY_PATH_ENV, "").strip()
)
DEFAULT_POLICY_PATH = (
    Path(_POLICY_OVERRIDE).expanduser()
    if _POLICY_OVERRIDE
    else resolve_generated_config_path(DEFAULT_POLICY_FILENAME)
)


def _print_obj(obj: dict[str, Any], json_out: bool = True) -> None:
    cli_runtime.print_obj(obj, json_out=json_out)


def _parse_call_payload(payload: Optional[str]) -> CallRequest:
    return cli_runtime.parse_call_payload(payload)


def _build_registry(policy: Policy) -> tuple[ToolRegistry, list[dict[str, Any]]]:
    return cli_runtime.build_registry(policy)


def _effective_scope(policy: Policy, scope: Optional[str]) -> Scope:
    return cli_runtime.effective_scope(policy, scope)


def _write_run_meta(
    run_root: Path,
    request: CallRequest,
    effective_scope: Scope,
    policy_path: Path,
    plugin_statuses: list[dict[str, Any]],
) -> None:
    cli_runtime.write_run_meta(
        run_root,
        request,
        effective_scope,
        policy_path,
        plugin_statuses,
    )


def _raise_if_denied(
    stage: str, code: str, reason: str, details: dict[str, Any]
) -> None:
    cli_runtime.raise_if_denied(stage, code, reason, details)


def _print_envelope(env: ResultEnvelope, json_out: bool) -> None:
    cli_runtime.print_envelope(env, json_out=json_out)


def _execute_call_payload(
    *,
    payload: Optional[str],
    policy: Path,
    workspace: Optional[Path],
    scope: Optional[str],
    confirm: bool,
    timeout_sec: Optional[int],
) -> tuple[ResultEnvelope, int]:
    return cli_runtime.execute_call_payload(
        payload=payload,
        policy=policy,
        workspace=workspace,
        scope=scope,
        confirm=confirm,
        timeout_sec=timeout_sec,
        build_registry_fn=lambda policy_obj: _via_cli("_build_registry")(policy_obj),
        create_run_root_fn=lambda *args, **kwargs: _via_cli("create_run_root")(
            *args, **kwargs
        ),
        resolve_home_root_fn=lambda: resolve_home_root(),
        resolve_data_root_fn=lambda home_root, data_root=None: resolve_data_root(
            home_root, data_root=data_root
        ),
    )


def _invoke_pinchtab_tool(
    *,
    tool: str,
    args: dict[str, Any],
    policy: Path,
    workspace: Optional[Path],
    scope: Optional[str],
    confirm: bool,
    timeout_sec: Optional[int],
) -> tuple[ResultEnvelope, int]:
    return cli_runtime.invoke_pinchtab_tool(
        tool=tool,
        args=args,
        policy=policy,
        workspace=workspace,
        scope=scope,
        confirm=confirm,
        timeout_sec=timeout_sec,
        execute_call_payload_fn=lambda **kwargs: _via_cli("_execute_call_payload")(
            **kwargs
        ),
    )


def _pinchtab_daemon_config(
    *,
    base_url: Optional[str] = None,
    launch_cmd: Optional[str] = None,
    launch_timeout_s: int = 20,
    launch_env: Optional[str] = None,
) -> Any:
    return cli_runtime.pinchtab_daemon_config(
        base_url=base_url,
        launch_cmd=launch_cmd,
        launch_timeout_s=launch_timeout_s,
        launch_env=launch_env,
    )


def _is_unknown_browser_tool_error(env: ResultEnvelope) -> bool:
    return cli_runtime.is_unknown_browser_tool_error(env)


def _map_pinchtab_to_browser_call(
    *, tool: str, args: dict[str, Any]
) -> tuple[str, dict[str, Any]]:
    return cli_runtime.map_pinchtab_to_browser_call(tool=tool, args=args)


def _parse_env_pairs(values: list[str]) -> dict[str, str]:
    return cli_runtime.parse_env_pairs(values)


def _invoke_exec_tool(
    *,
    tool: str,
    args: dict[str, Any],
    policy: Path,
    workspace: Optional[Path],
    scope: Optional[str],
    confirm: bool,
    timeout_sec: Optional[int],
) -> tuple[ResultEnvelope, int]:
    return cli_runtime.invoke_exec_tool(
        tool=tool,
        args=args,
        policy=policy,
        workspace=workspace,
        scope=scope,
        confirm=confirm,
        timeout_sec=timeout_sec,
        execute_call_payload_fn=lambda **kwargs: _via_cli("_execute_call_payload")(
            **kwargs
        ),
    )


def _finalize_cli_call(env: ResultEnvelope, exit_code: int, json_out: bool) -> None:
    cli_runtime.finalize_cli_call(
        env,
        exit_code,
        json_out,
        print_envelope_fn=lambda envelope, out: _print_envelope(envelope, out),
    )


register_core_commands(
    app=app,
    tool_app=tool_app,
    policy_app=policy_app,
    plugins_app=plugins_app,
    default_policy_path=DEFAULT_POLICY_PATH,
    print_obj=lambda obj, json_out: _print_obj(obj, json_out),
    parse_call_payload=lambda payload: _parse_call_payload(payload),
    build_registry=lambda policy_obj: _via_cli("_build_registry")(policy_obj),
    effective_scope=lambda policy_obj, scope: _effective_scope(policy_obj, scope),
    execute_call_payload=lambda **kwargs: _via_cli("_execute_call_payload")(**kwargs),
    finalize_cli_call=lambda env, exit_code, json_out: _finalize_cli_call(
        env, exit_code, json_out
    ),
)

register_pinchtab_commands(
    pinchtab_app=pinchtab_app,
    pinchtab_instance_app=pinchtab_instance_app,
    pinchtab_tab_app=pinchtab_tab_app,
    pinchtab_daemon_app=pinchtab_daemon_app,
    default_policy_path=DEFAULT_POLICY_PATH,
    invoke_pinchtab_tool=lambda **kwargs: _via_cli("_invoke_pinchtab_tool")(**kwargs),
    finalize_cli_call=lambda env, exit_code, json_out: _finalize_cli_call(
        env, exit_code, json_out
    ),
    pinchtab_daemon_config=lambda **kwargs: _pinchtab_daemon_config(**kwargs),
    print_obj=lambda obj, json_out: _print_obj(obj, json_out),
)

register_exec_commands(
    exec_app=exec_app,
    default_policy_path=DEFAULT_POLICY_PATH,
    parse_env_pairs=lambda values: _parse_env_pairs(values),
    invoke_exec_tool=lambda **kwargs: _via_cli("_invoke_exec_tool")(**kwargs),
    finalize_cli_call=lambda env, exit_code, json_out: _finalize_cli_call(
        env, exit_code, json_out
    ),
)


def _exit_code(value: object) -> int:
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    return 1


def main(argv: list[str] | None = None) -> int:
    try:
        app(args=argv, standalone_mode=False)
        return 0
    except typer.Exit as exc:
        return _exit_code(exc.exit_code)
    except SystemExit as exc:  # pragma: no cover - defensive compatibility
        return _exit_code(exc.code)
