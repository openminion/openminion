import json
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import typer

from openminion.base.config.env import EnvironmentConfig
from openminion.tools.browser.providers.pinchtab.daemon import (
    build_daemon_config as build_pinchtab_daemon_config,
)
from openminion.tools.config import resolve_tool_data_root, resolve_tool_env
from openminion.tools.browser.providers.pinchtab.constants import (
    DEFAULT_PINCHTAB_BASE_URL,
    DEFAULT_PINCHTAB_RUNTIME_SUBPATH,
    PINCHTAB_LAUNCH_CMD_ENV,
    PINCHTAB_URL_ENV,
)

from ..contracts.schemas import ResultEnvelope


def is_unknown_browser_tool_error(env: ResultEnvelope) -> bool:
    if env.error is None:
        return False
    if env.error.code != "NOT_FOUND":
        return False
    return "Unknown tool: browser" in env.error.message


_PINCHTAB_PROVIDER = "pinchtab"


def _map_instance_start(src: Dict[str, Any]) -> Dict[str, Any]:
    call_args: Dict[str, Any] = {"op": "instance.start", "provider": _PINCHTAB_PROVIDER}
    if src.get("instance_id") is not None:
        call_args["instance_id"] = src.get("instance_id")
    if src.get("profile_id") is not None:
        call_args["profile"] = src.get("profile_id")
    if src.get("mode") is not None:
        call_args["mode"] = src.get("mode")
    if src.get("port") is not None:
        call_args["port"] = src.get("port")
    return call_args


def _map_snapshot(src: Dict[str, Any]) -> Dict[str, Any]:
    call_args: Dict[str, Any] = {
        "op": "tab.snapshot",
        "provider": _PINCHTAB_PROVIDER,
        "tab_id": src.get("tab_id"),
    }
    snapshot_options: Dict[str, Any] = {}
    if src.get("mode") is not None:
        snapshot_options["mode"] = src.get("mode")
    if snapshot_options:
        call_args["snapshot"] = snapshot_options
    return call_args


def _map_text(src: Dict[str, Any]) -> Dict[str, Any]:
    call_args: Dict[str, Any] = {
        "op": "tab.text",
        "provider": _PINCHTAB_PROVIDER,
        "tab_id": src.get("tab_id"),
    }
    text_options: Dict[str, Any] = {}
    if src.get("mode") is not None:
        text_options["mode"] = src.get("mode")
    if src.get("include_text") is not None:
        text_options["include_text"] = bool(src.get("include_text"))
    if text_options:
        call_args["text"] = text_options
    return call_args


def _map_action(tool: str, src: Dict[str, Any]) -> Dict[str, Any]:
    action_kind_by_tool = {
        "browser.pinchtab.click": "click",
        "browser.pinchtab.fill": "fill",
        "browser.pinchtab.type": "type",
        "browser.pinchtab.press": "press",
        "browser.pinchtab.hover": "hover",
        "browser.pinchtab.select": "select",
        "browser.pinchtab.scroll": "scroll",
        "browser.pinchtab.action": str(src.get("kind", "")).strip().lower() or "click",
    }
    action: Dict[str, Any] = {"kind": action_kind_by_tool[tool]}
    ref = src.get("ref")
    if ref is not None:
        action["target"] = {"ref": ref}
    if src.get("text") is not None:
        action["text"] = src.get("text")
    if src.get("key") is not None:
        action["key"] = src.get("key")
    if src.get("option") is not None:
        action["option"] = src.get("option")
    if src.get("delta") is not None:
        action["delta"] = src.get("delta")
    return {
        "op": "tab.action",
        "provider": _PINCHTAB_PROVIDER,
        "tab_id": src.get("tab_id"),
        "action": action,
    }


_SIMPLE_PINCHTAB_TOOL_MAP: Dict[str, tuple[str, tuple[str, ...]]] = {
    "browser.pinchtab.health": ("daemon.ensure", ()),
    "browser.pinchtab.instance_list": ("instance.list", ()),
    "browser.pinchtab.instance_stop": ("instance.stop", ("instance_id",)),
    "browser.pinchtab.instance_kill": ("instance.kill", ("instance_id",)),
    "browser.pinchtab.tab_open": ("tab.new", ("instance_id", "url")),
    "browser.pinchtab.tabs_list": ("tab.list", ("instance_id",)),
    "browser.pinchtab.tab_close": ("tab.close", ("tab_id",)),
    "browser.pinchtab.navigate": ("tab.navigate", ("tab_id", "url")),
    "browser.pinchtab.screenshot": ("tab.screenshot", ("tab_id",)),
    "browser.pinchtab.pdf": ("tab.pdf", ("tab_id",)),
}

_ACTION_PINCHTAB_TOOLS: frozenset[str] = frozenset(
    {
        "browser.pinchtab.click",
        "browser.pinchtab.fill",
        "browser.pinchtab.type",
        "browser.pinchtab.press",
        "browser.pinchtab.hover",
        "browser.pinchtab.select",
        "browser.pinchtab.scroll",
        "browser.pinchtab.action",
    }
)


def _map_simple(
    op: str, fields: tuple[str, ...], src: Dict[str, Any]
) -> Dict[str, Any]:
    call_args: Dict[str, Any] = {"op": op, "provider": _PINCHTAB_PROVIDER}
    for field_name in fields:
        call_args[field_name] = src.get(field_name)
    return call_args


def map_pinchtab_to_browser_call(
    *, tool: str, args: Dict[str, Any]
) -> tuple[str, Dict[str, Any]]:
    src = dict(args)
    simple = _SIMPLE_PINCHTAB_TOOL_MAP.get(tool)
    if simple is not None:
        op, fields = simple
        return "browser", _map_simple(op, fields, src)
    if tool == "browser.pinchtab.instance_start":
        return "browser", _map_instance_start(src)
    if tool == "browser.pinchtab.snapshot":
        return "browser", _map_snapshot(src)
    if tool == "browser.pinchtab.text":
        return "browser", _map_text(src)
    if tool in _ACTION_PINCHTAB_TOOLS:
        return "browser", _map_action(tool, src)
    return tool, src


def invoke_pinchtab_tool(
    *,
    tool: str,
    args: Dict[str, Any],
    policy: Path,
    workspace: Optional[Path],
    scope: Optional[str],
    confirm: bool,
    timeout_sec: Optional[int],
    execute_call_payload_fn: Callable[..., tuple[ResultEnvelope, int]],
) -> tuple[ResultEnvelope, int]:
    canonical_tool, canonical_args = map_pinchtab_to_browser_call(tool=tool, args=args)
    payload = json.dumps(
        {"tool": canonical_tool, "args": canonical_args}, ensure_ascii=True
    )
    env, exit_code = execute_call_payload_fn(
        payload=payload,
        policy=policy,
        workspace=workspace,
        scope=scope,
        confirm=confirm,
        timeout_sec=timeout_sec,
    )
    if exit_code == 0:
        return env, exit_code

    if canonical_tool == "browser" and is_unknown_browser_tool_error(env):
        legacy_payload = json.dumps({"tool": tool, "args": args}, ensure_ascii=True)
        return execute_call_payload_fn(
            payload=legacy_payload,
            policy=policy,
            workspace=workspace,
            scope=scope,
            confirm=confirm,
            timeout_sec=timeout_sec,
        )
    return env, exit_code


def pinchtab_daemon_config(
    *,
    base_url: Optional[str] = None,
    launch_cmd: Optional[str] = None,
    launch_timeout_s: int = 20,
    launch_env: Optional[str] = None,
    env: EnvironmentConfig | Dict[str, str] | None = None,
) -> Any:
    env_config = resolve_tool_env(env=env)
    data_root = resolve_tool_data_root(env=env_config)
    runtime_dir = data_root / DEFAULT_PINCHTAB_RUNTIME_SUBPATH
    resolved_base = str(
        base_url or env_config.get(PINCHTAB_URL_ENV, DEFAULT_PINCHTAB_BASE_URL)
    ).strip()
    env_pairs: Dict[str, str] = {}
    if launch_env:
        for chunk in str(launch_env).split(","):
            chunk = chunk.strip()
            if not chunk or "=" not in chunk:
                continue
            key, value = chunk.split("=", 1)
            key = key.strip()
            if key:
                env_pairs[key] = value.strip()
    return build_pinchtab_daemon_config(
        base_url=resolved_base,
        runtime_dir=runtime_dir,
        launch_cmd=launch_cmd or env_config.get(PINCHTAB_LAUNCH_CMD_ENV, "") or None,
        launch_timeout_s=launch_timeout_s,
        env=env_pairs,
    )


def parse_env_pairs(values: list[str]) -> Dict[str, str]:
    parsed: Dict[str, str] = {}
    for raw in values:
        token = str(raw)
        if "=" not in token:
            raise typer.BadParameter(
                f"Invalid --env value '{token}'; expected KEY=VALUE"
            )
        key, value = token.split("=", 1)
        key = key.strip()
        if not key:
            raise typer.BadParameter("Environment key cannot be empty")
        parsed[key] = value
    return parsed


def invoke_exec_tool(
    *,
    tool: str,
    args: Dict[str, Any],
    policy: Path,
    workspace: Optional[Path],
    scope: Optional[str],
    confirm: bool,
    timeout_sec: Optional[int],
    execute_call_payload_fn: Callable[..., tuple[ResultEnvelope, int]],
) -> tuple[ResultEnvelope, int]:
    payload = json.dumps({"tool": tool, "args": args}, ensure_ascii=True)
    return execute_call_payload_fn(
        payload=payload,
        policy=policy,
        workspace=workspace,
        scope=scope,
        confirm=confirm,
        timeout_sec=timeout_sec,
    )


def finalize_cli_call(
    env: ResultEnvelope,
    exit_code: int,
    json_out: bool,
    print_envelope_fn: Callable[[ResultEnvelope, bool], None],
) -> None:
    print_envelope_fn(env, json_out)
    if exit_code:
        raise typer.Exit(code=exit_code)
