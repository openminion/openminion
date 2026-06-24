from __future__ import annotations

import argparse
import json

from openminion.cli.commands.daemon import ensure_daemon_running
from openminion.cli.transport.daemon_client import daemon_request
from openminion.cli.bootstrap.loader import load_config
from openminion.cli.parser.flags import add_tool_session_arg
from openminion.cli.presentation.json_output import print_json_payload
from openminion.modules.llm.providers.base import ProviderToolCall
from openminion.api.runtime import APIRuntime
from openminion.modules.tool.base import ToolExecutionContext
from openminion.modules.tool.refs import (
    tool_result_artifact_refs as _tool_result_artifact_refs,
)
from openminion.services.security.blast_radius.wiring import (
    SEAM_CLI_TOOLS,
    build_default_composition_boundary_adapter,
)
from openminion.services.tool.selection import ToolSelectionService
from openminion.modules.tool.runtime.routing import build_runtime_tool_routing_metadata


def run_tools(args) -> int:
    action = str(getattr(args, "tools_command", "")).strip().lower()
    if action == "list":
        return _tools_list(args)
    if action == "schema":
        return _tools_schema(args)
    if action == "run":
        return _tools_run(args)
    raise RuntimeError("Unknown tools command")


def _tools_list(args) -> int:
    verbose = getattr(args, "verbose", False)
    filter_available = getattr(args, "available", False)
    filter_blocked = getattr(args, "blocked", False)
    filter_disabled = getattr(args, "disabled", False)

    payload = _from_daemon_or_inproc(
        args,
        daemon_call=lambda endpoint: daemon_request(
            endpoint=endpoint,
            method="GET",
            path="/v1/tools",
            timeout_s=10,
        ),
        inproc_call=lambda: {
            "ok": True,
            "tools": _inproc_tool_specs(args.config),
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
        daemon_call=lambda endpoint: daemon_request(
            endpoint=endpoint,
            method="GET",
            path=f"/v1/tools/{tool_name}/schema",
            timeout_s=10,
        ),
        inproc_call=lambda: _inproc_tool_schema(args.config, tool_name=tool_name),
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

    request_payload = {
        "arguments": arguments,
        "session_id": str(getattr(args, "session", "") or "").strip() or "tools",
        "channel": "console",
        "target": "cli-tools",
    }

    payload = _from_daemon_or_inproc(
        args,
        daemon_call=lambda endpoint: daemon_request(
            endpoint=endpoint,
            method="POST",
            path=f"/v1/tools/{tool_name}/run",
            payload=request_payload,
            timeout_s=30,
        ),
        inproc_call=lambda: _inproc_tool_run(
            args.config,
            tool_name=tool_name,
            arguments=arguments,
            session_id=request_payload["session_id"],
        ),
    )
    print_json_payload(payload)
    return 0 if payload.get("ok", False) else 1


def _from_daemon_or_inproc(args, *, daemon_call, inproc_call) -> dict:
    config = load_config(args.config)
    auto_start = bool(config.runtime.daemon_auto_start)
    try:
        endpoint = ensure_daemon_running(args.config, auto_start=auto_start)
        status, payload = daemon_call(endpoint)
        if status < 400:
            return payload
    except RuntimeError:
        pass
    return inproc_call()


def _inproc_tool_specs(config_path: str | None) -> list[dict]:
    runtime = APIRuntime.from_config_path(config_path)
    try:
        return runtime.tool_inventory_report()
    finally:
        runtime.close()


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
    tools_list.set_defaults(handler=run_tools, needs_app=False)

    tools_schema = tools_subcommands.add_parser("schema", help="Show tool schema")
    tools_schema.add_argument("tool", help="Tool name")
    tools_schema.set_defaults(handler=run_tools, needs_app=False)

    tools_run = tools_subcommands.add_parser("run", help="Execute one tool call")
    tools_run.add_argument("tool", help="Tool name")
    tools_run.add_argument(
        "--json",
        dest="json_payload",
        default="{}",
        help="Tool argument payload as JSON object",
    )
    add_tool_session_arg(tools_run, default="tools")
    tools_run.set_defaults(handler=run_tools, needs_app=False)


def _inproc_tool_schema(config_path: str | None, *, tool_name: str) -> dict:
    runtime = APIRuntime.from_config_path(config_path)
    try:
        schema = runtime.tool_schema_report(tool_name=tool_name)
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
    finally:
        runtime.close()


def _inproc_tool_run(
    config_path: str | None,
    *,
    tool_name: str,
    arguments: dict,
    session_id: str,
) -> dict:
    runtime = APIRuntime.from_config_path(config_path)
    try:
        provider_spec = None
        if callable(getattr(runtime.tools, "provider_spec_for_name", None)):
            provider_spec = runtime.tools.provider_spec_for_name(tool_name)
        if provider_spec is None:
            return {
                "ok": False,
                "error": {
                    "code": "tool_not_found",
                    "message": f"Unknown tool: {tool_name}",
                },
            }
        batch = runtime.tools.execute_calls(
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
                authored_tools_api=getattr(runtime, "authored_tools", None),
                metadata={
                    "origin": "openminion.tools.inproc",
                    "runtime_env": dict(
                        getattr(
                            getattr(runtime.config, "runtime", None),
                            "env",
                            {},
                        )
                        or {}
                    ),
                    **build_runtime_tool_routing_metadata(runtime.config.runtime.tools),
                    **ToolSelectionService(
                        runtime.config.runtime.tool_selection,
                        runtime.tools,
                    ).runtime_binding_policy_metadata(),
                },
                blast_radius_adapter=build_default_composition_boundary_adapter(
                    seam_id=SEAM_CLI_TOOLS,
                ),
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
    finally:
        runtime.close()
