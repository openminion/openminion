from __future__ import annotations

import argparse
from collections.abc import Iterator
from contextlib import contextmanager, suppress
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlencode

from openminion.api.queries.runtime_reports import (
    build_tool_inventory_report,
    build_tool_schema_report,
)
from openminion.cli.config import load_cli_config_from_args
from openminion.cli.parser.flags import add_runtime_source_flag, add_tool_session_arg
from openminion.cli.presentation.json_output import print_json_payload
from openminion.cli.transport.runtime_source import call_daemon_or_inproc, daemon_get
from openminion.modules.llm.providers.base import ProviderToolCall
from openminion.api.runtime import APIRuntime
from openminion.modules.tool.base import ToolExecutionContext
from openminion.modules.tool.refs import (
    tool_result_artifact_refs as _tool_result_artifact_refs,
)
from openminion.modules.tool.facade import build_default_tool_registry
from openminion.modules.policy.adapters.composition import (
    SEAM_CLI_TOOLS,
    build_default_composition_boundary_adapter,
)
from openminion.modules.tool.selection import ToolSelectionService
from openminion.modules.tool.runtime.routing import build_runtime_tool_routing_metadata
from openminion.services.runtime.sidecars import ensure_sidecar_autostart


def run_tools(args) -> int:
    action = str(getattr(args, "tools_command", "")).strip().lower()
    if action == "list":
        return _tools_list(args)
    if action == "schema":
        return _tools_schema(args)
    if action == "run":
        return _tools_run(args)
    if action == "exposure":
        return _tools_exposure(args)
    raise RuntimeError("Unknown tools command")


def _tools_list(args) -> int:
    verbose = getattr(args, "verbose", False)
    filter_available = getattr(args, "available", False)
    filter_blocked = getattr(args, "blocked", False)
    filter_disabled = getattr(args, "disabled", False)

    payload = _from_daemon_or_inproc(
        args,
        daemon_call=lambda endpoint: daemon_get(
            endpoint, path="/v1/tools", timeout_s=10
        ),
        inproc_call=lambda: {
            "ok": True,
            "tools": _inproc_tool_specs(args),
        },
    )

    if not payload.get("ok", False):
        print_json_payload(payload)
        return 1

    tools = payload.get("tools", [])

    if filter_available or filter_blocked or filter_disabled:
        filtered = []
        for t in tools:
            if not isinstance(t, dict):
                continue
            enabled = t.get("enabled", True)
            blocked = not t.get("policy_allowed", True)
            if filter_available and enabled and not blocked:
                filtered.append(t)
            elif filter_blocked and blocked:
                filtered.append(t)
            elif filter_disabled and not enabled:
                filtered.append(t)
        tools = filtered

    if verbose:
        _print_tools_verbose(tools)
    else:
        print_json_payload({"ok": True, "tools": tools})
    return 0


def _print_tools_verbose(tools: list) -> None:
    if not tools:
        print("(no tools available)")
        return

    tool_infos = []
    for item in tools:
        if isinstance(item, dict):
            tool_infos.append(
                {
                    "name": item.get("name", "unknown"),
                    "description": item.get("description", ""),
                    "source": item.get("source", "core"),
                    "enabled": item.get("enabled", True),
                    "runtime_binding_id": item.get("runtime_binding_id", ""),
                    "runtime_tool_name": item.get("runtime_tool_name", ""),
                }
            )

    tool_infos.sort(key=lambda x: (x["source"], x["name"]))

    print(f"Available tools ({len(tool_infos)}):")

    by_source: dict[str, list] = {}
    for t in tool_infos:
        src = t["source"] or "core"
        by_source.setdefault(src, []).append(t)

    for source, items in by_source.items():
        print(f"\n[{source.upper()}]")
        for t in items:
            status = "✓" if t["enabled"] else "✗"
            print(f"  {status} {t['name']}")
            if t["description"]:
                desc = t["description"]
                if len(desc) > 60:
                    desc = desc[:57] + "..."
                print(f"      {desc}")
            runtime_tool = str(t.get("runtime_tool_name", "") or "").strip()
            runtime_binding = str(t.get("runtime_binding_id", "") or "").strip()
            if runtime_tool or runtime_binding:
                print(
                    f"      -> runtime: {runtime_tool or '(unresolved)'}"
                    + (f" [{runtime_binding}]" if runtime_binding else "")
                )


def _tools_schema(args) -> int:
    tool_name = str(getattr(args, "tool", "") or "").strip()
    if not tool_name:
        raise RuntimeError("tool name is required")

    payload = _from_daemon_or_inproc(
        args,
        daemon_call=lambda endpoint: daemon_get(
            endpoint,
            path=f"/v1/tools/{tool_name}/schema",
            timeout_s=10,
        ),
        inproc_call=lambda: _inproc_tool_schema(args, tool_name=tool_name),
    )
    print_json_payload(payload)
    return 0 if payload.get("ok", False) else 1


def _tools_run(args) -> int:
    tool_name = str(getattr(args, "tool", "") or "").strip()
    if not tool_name:
        raise RuntimeError("tool name is required")

    json_payload_raw = str(getattr(args, "json_payload", "{}") or "{}")
    try:
        arguments = json.loads(json_payload_raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid --json payload: {exc}") from exc
    if not isinstance(arguments, dict):
        raise RuntimeError("--json payload must be a JSON object")

    session_id = str(getattr(args, "session", "") or "").strip() or "tools"
    confirm = bool(getattr(args, "confirm", False))
    request_payload = {
        "arguments": arguments,
        "session_id": session_id,
        "channel": "console",
        "target": "cli-tools",
        "confirm": confirm,
    }

    payload = _from_daemon_or_inproc(
        args,
        daemon_call=lambda endpoint: _daemon_post(
            endpoint=endpoint,
            path=f"/v1/tools/{tool_name}/run",
            payload=request_payload,
            timeout_s=30,
        ),
        inproc_call=lambda: _inproc_tool_run(
            args,
            tool_name=tool_name,
            arguments=arguments,
            session_id=session_id,
            confirm=confirm,
        ),
    )
    print_json_payload(payload)
    return 0 if payload.get("ok", False) else 1


def _exposure_payload(args) -> dict[str, object]:
    return {
        "profile_id": str(getattr(args, "profile", "") or "").strip(),
        "session_id": str(getattr(args, "session", "") or "").strip() or "tools",
        "task_id": str(getattr(args, "task", "") or "").strip(),
        "target_id": str(getattr(args, "target", "") or "").strip(),
    }


def _tools_exposure(args) -> int:
    action = str(getattr(args, "exposure_command", "") or "status").strip().lower()
    payload = _exposure_payload(args)
    if action == "status":
        query = urlencode(
            {
                key: value
                for key, value in payload.items()
                if key != "profile_id" and value
            }
        )
        result = _from_daemon_or_inproc(
            args,
            daemon_call=lambda endpoint: daemon_get(
                endpoint=endpoint,
                path=f"/v1/tools/exposure?{query}",
                timeout_s=10,
            ),
            inproc_call=lambda: _inproc_exposure_status(args, payload),
        )
    elif action == "activate":
        payload.update(
            {
                "target_kind": str(getattr(args, "target_kind", "") or "").strip(),
                "credential_scopes": list(getattr(args, "credential_scope", ()) or ()),
                "dependencies": list(getattr(args, "dependency", ()) or ()),
                "approved": bool(getattr(args, "approved", False)),
                "ttl_seconds": getattr(args, "ttl", None),
                "activation_reason": getattr(args, "reason", ""),
                "approved_by": getattr(args, "approved_by", ""),
                "policy_source": getattr(args, "policy_source", ""),
            }
        )
        result = _from_daemon_or_inproc(
            args,
            daemon_call=lambda endpoint: _daemon_post(
                endpoint=endpoint,
                path="/v1/tools/exposure/activate",
                payload=payload,
                timeout_s=10,
            ),
            inproc_call=lambda: _inproc_exposure_activate(args, payload),
        )
    elif action == "deactivate":
        result = _from_daemon_or_inproc(
            args,
            daemon_call=lambda endpoint: _daemon_post(
                endpoint=endpoint,
                path="/v1/tools/exposure/deactivate",
                payload=payload,
                timeout_s=10,
            ),
            inproc_call=lambda: _inproc_exposure_deactivate(args, payload),
        )
    else:
        raise RuntimeError("Unknown tools exposure command")
    print_json_payload(result)
    return 0 if result.get("ok", False) else 1


def _from_daemon_or_inproc(args, *, daemon_call, inproc_call) -> dict:
    config = load_cli_config_from_args(args)
    result = call_daemon_or_inproc(
        args=args,
        auto_start=bool(config.runtime.daemon_auto_start),
        daemon_call=daemon_call,
        inproc_call=inproc_call,
    )
    payload = dict(result.payload)
    payload.setdefault("runtime_source", result.source)
    if result.fallback_reason:
        payload.setdefault("runtime_fallback_reason", result.fallback_reason)
    return payload


def _daemon_post(
    *,
    endpoint: object,
    path: str,
    payload: dict,
    timeout_s: float,
) -> tuple[int, dict[str, Any]]:
    from openminion.cli.transport.daemon_client import daemon_request

    return daemon_request(
        endpoint=endpoint,
        method="POST",
        path=path,
        payload=payload,
        timeout_s=timeout_s,
    )


def _runtime_from_args(args_or_config_path: object) -> APIRuntime:
    if isinstance(args_or_config_path, (str, type(None))):
        return APIRuntime.from_config_path(args_or_config_path)
    return APIRuntime.from_config_path(
        getattr(args_or_config_path, "config", None),
        home_root=getattr(args_or_config_path, "home_root", None),
        data_root=getattr(args_or_config_path, "data_root", None),
    )


@contextmanager
def _local_tool_runtime(args_or_config_path: object) -> Iterator[tuple[Any, Any]]:
    config = load_cli_config_from_args(args_or_config_path)
    configured_workspace = str(config.runtime.tool_workspace_root or "").strip()
    workspace_root = (
        Path(configured_workspace).expanduser() if configured_workspace else Path.cwd()
    )
    tools = build_default_tool_registry(
        config=config.runtime,
        workspace_root=workspace_root,
        strict=False,
    )
    tools.bind_sidecar_autostart(ensure_sidecar_autostart)
    try:
        yield config, tools
    finally:
        manager = getattr(tools, "mcp_manager", None)
        close = getattr(manager, "close", None)
        if callable(close):
            with suppress(Exception):
                close()


def _inproc_tool_specs(args_or_config_path: object) -> list[dict]:
    with _local_tool_runtime(args_or_config_path) as (_config, tools):
        return build_tool_inventory_report(SimpleNamespace(tools=tools))


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    tools = subparsers.add_parser("tools", help="Tool catalog and invocation")
    tools_subcommands = tools.add_subparsers(dest="tools_command")

    tools_list = tools_subcommands.add_parser("list", help="List available tools")
    tools_list.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show detailed output with source grouping",
    )
    tools_list.add_argument(
        "--available", action="store_true", help="Show only available (enabled) tools"
    )
    tools_list.add_argument(
        "--blocked", action="store_true", help="Show tools blocked by policy"
    )
    tools_list.add_argument(
        "--disabled", action="store_true", help="Show disabled tools"
    )
    add_runtime_source_flag(tools_list)
    tools_list.set_defaults(handler=run_tools, needs_app=False)

    tools_schema = tools_subcommands.add_parser("schema", help="Show tool schema")
    tools_schema.add_argument("tool", help="Tool name")
    add_runtime_source_flag(tools_schema)
    tools_schema.set_defaults(handler=run_tools, needs_app=False)

    tools_run = tools_subcommands.add_parser("run", help="Execute one tool call")
    tools_run.add_argument("tool", help="Tool name")
    tools_run.add_argument(
        "--json",
        dest="json_payload",
        default="{}",
        help="Tool argument payload as JSON object",
    )
    tools_run.add_argument(
        "--confirm",
        action="store_true",
        help="Confirm a policy-gated tool call such as a write or admin operation",
    )
    add_tool_session_arg(tools_run, default="tools")
    add_runtime_source_flag(tools_run)
    tools_run.set_defaults(handler=run_tools, needs_app=False)

    exposure = tools_subcommands.add_parser(
        "exposure",
        help="Inspect or change explicit tool exposure profiles",
    )
    exposure_subcommands = exposure.add_subparsers(dest="exposure_command")
    exposure_status = exposure_subcommands.add_parser("status", help="Show profiles")
    _add_exposure_scope_args(exposure_status, include_profile=False)
    add_runtime_source_flag(exposure_status)
    exposure_status.set_defaults(handler=run_tools, needs_app=False)

    exposure_activate = exposure_subcommands.add_parser(
        "activate", help="Activate one profile"
    )
    _add_exposure_scope_args(exposure_activate)
    exposure_activate.add_argument("--target-kind", default="")
    exposure_activate.add_argument("--credential-scope", action="append", default=[])
    exposure_activate.add_argument("--dependency", action="append", default=[])
    exposure_activate.add_argument("--approved", action="store_true")
    exposure_activate.add_argument("--ttl", type=float, default=None)
    exposure_activate.add_argument("--reason", default="")
    exposure_activate.add_argument("--approved-by", default="")
    exposure_activate.add_argument("--policy-source", default="")
    add_runtime_source_flag(exposure_activate)
    exposure_activate.set_defaults(handler=run_tools, needs_app=False)

    exposure_deactivate = exposure_subcommands.add_parser(
        "deactivate", help="Deactivate one profile"
    )
    _add_exposure_scope_args(exposure_deactivate)
    add_runtime_source_flag(exposure_deactivate)
    exposure_deactivate.set_defaults(handler=run_tools, needs_app=False)


def _add_exposure_scope_args(parser, *, include_profile: bool = True) -> None:
    if include_profile:
        parser.add_argument("profile", help="Exposure profile id")
    parser.add_argument("--session", default="tools")
    parser.add_argument("--task", default="")
    parser.add_argument("--target", default="")


def _inproc_tool_schema(args_or_config_path: object, *, tool_name: str) -> dict:
    with _local_tool_runtime(args_or_config_path) as (_config, tools):
        schema = build_tool_schema_report(
            SimpleNamespace(tools=tools),
            tool_name=tool_name,
        )
        if schema is None:
            return {
                "ok": False,
                "error": {
                    "code": "tool_not_found",
                    "message": f"Unknown tool: {tool_name}",
                },
            }
        return {
            "ok": True,
            "tool": schema,
        }


def _inproc_tool_run(
    args_or_config_path: object,
    *,
    tool_name: str,
    arguments: dict,
    session_id: str,
    confirm: bool = False,
) -> dict:
    with _local_tool_runtime(args_or_config_path) as (config, tools):
        provider_spec = tools.provider_spec_for_name(tool_name)
        if provider_spec is None:
            return {
                "ok": False,
                "error": {
                    "code": "tool_not_found",
                    "message": f"Unknown tool: {tool_name}",
                },
            }
        batch = tools.execute_calls(
            [
                ProviderToolCall(
                    name=tool_name,
                    arguments=arguments,
                    source="inproc",
                )
            ],
            context=ToolExecutionContext(
                channel="console",
                target="cli-tools",
                session_id=session_id,
                metadata={
                    "session_id": session_id,
                    "origin": "openminion.tools.inproc",
                    "runtime_env": dict(config.runtime.env or {}),
                    **build_runtime_tool_routing_metadata(config.runtime.tools),
                    **ToolSelectionService(
                        config.runtime.tool_selection,
                        tools,
                    ).runtime_binding_policy_metadata(),
                },
                blast_radius_adapter=build_default_composition_boundary_adapter(
                    seam_id=SEAM_CLI_TOOLS,
                ),
                confirm=bool(confirm),
            ),
        )
        result = batch.results[0]
        return {
            "ok": bool(result.ok),
            "tool": {
                "name": result.tool_name,
                "ok": bool(result.ok),
                "verified": bool(result.verified),
                "content": result.content,
                "error": result.error,
                "data": dict(result.data or {}),
            },
            "artifact_refs": _tool_result_artifact_refs(
                session_id=session_id,
                tool_name=tool_name,
                result=result,
            ),
        }


def _inproc_exposure_status(args_or_config_path: object, payload: dict) -> dict:
    runtime = _runtime_from_args(args_or_config_path)
    try:
        return {
            "ok": True,
            "exposure": runtime.tool_exposure_status(
                session_id=str(payload.get("session_id", "")),
                task_id=str(payload.get("task_id", "")),
                target_id=str(payload.get("target_id", "")),
            ),
        }
    finally:
        runtime.close()


def _inproc_exposure_activate(args_or_config_path: object, payload: dict) -> dict:
    runtime = _runtime_from_args(args_or_config_path)
    try:
        try:
            activation = runtime.activate_tool_profile(
                str(payload.get("profile_id", "")),
                session_id=str(payload.get("session_id", "")),
                task_id=str(payload.get("task_id", "")),
                target_id=str(payload.get("target_id", "")),
                target_kind=str(payload.get("target_kind", "")),
                credential_scopes=tuple(payload.get("credential_scopes", ()) or ()),
                dependencies=tuple(payload.get("dependencies", ()) or ()),
                approved=bool(payload.get("approved", False)),
                ttl_seconds=payload.get("ttl_seconds"),
                activation_reason=str(payload.get("activation_reason", "")),
                approved_by=str(payload.get("approved_by", "")),
                policy_source=str(payload.get("policy_source", "")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "activation": activation}
    finally:
        runtime.close()


def _inproc_exposure_deactivate(args_or_config_path: object, payload: dict) -> dict:
    runtime = _runtime_from_args(args_or_config_path)
    try:
        return {
            "ok": True,
            "deactivated": runtime.deactivate_tool_profile(
                str(payload.get("profile_id", "")),
                session_id=str(payload.get("session_id", "")),
                task_id=str(payload.get("task_id", "")),
                target_id=str(payload.get("target_id", "")),
            ),
        }
    finally:
        runtime.close()
