from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from openminion.api.runtime import APIRuntime
from openminion.base.config.io import load_config, save_config
from openminion.cli.presentation.json_output import print_json_payload
from openminion.base.config.mcp import (
    MCPPackageMetadataConfig,
    MCPServerConfig,
    coerce_mcp_server_configs,
)
from openminion.tools.mcp.manager import MCPFleetManager

_SECRET_KEY_TOKENS = ("token", "secret", "password", "key", "authorization")


def run_mcp(args: argparse.Namespace) -> int:
    command = str(getattr(args, "mcp_command", "") or "").strip().lower()
    if command == "import":
        return _mcp_import(args)
    if command == "list":
        return _mcp_list(args)
    if command == "validate":
        return _mcp_validate(args)
    if command == "test":
        return _mcp_test(args)
    if command == "restart":
        return _mcp_restart(args)
    if command == "logs":
        return _mcp_logs(args)
    raise RuntimeError("Unknown mcp command")


def _mcp_import(args: argparse.Namespace) -> int:
    source_path = Path(str(getattr(args, "source", "") or "")).expanduser()
    payload = json.loads(source_path.read_text(encoding="utf-8"))
    imported = _servers_from_external_payload(payload)
    config_path = getattr(args, "config", None)
    if bool(getattr(args, "write", False)):
        config = load_config(config_path)
        existing = {server.name: server for server in config.runtime.mcp_servers}
        for server in imported:
            existing[server.name] = server
        config.runtime.mcp_servers = list(existing.values())
        save_config(config, config_path)
    print_json_payload(
        {
            "ok": True,
            "imported": [_redacted_server_payload(server) for server in imported],
            "written": bool(getattr(args, "write", False)),
        }
    )
    return 0


def _mcp_list(args: argparse.Namespace) -> int:
    config = load_config(getattr(args, "config", None))
    print_json_payload(
        {
            "ok": True,
            "servers": [
                _redacted_server_payload(server)
                for server in config.runtime.mcp_servers
            ],
        }
    )
    return 0


def _mcp_validate(args: argparse.Namespace) -> int:
    config = load_config(getattr(args, "config", None))
    servers = coerce_mcp_server_configs(config.runtime.mcp_servers)
    issues = []
    for server in servers:
        if (
            server.transport == "stdio"
            and server.stdio_sandbox.require_trust
            and not server.trusted
        ):
            issues.append(
                {
                    "server": server.name,
                    "reason_code": "mcp_stdio_untrusted",
                    "message": "stdio server requires explicit trust before startup",
                }
            )
    print_json_payload(
        {"ok": not issues, "server_count": len(servers), "issues": issues}
    )
    return 0 if not issues else 1


def _mcp_test(args: argparse.Namespace) -> int:
    config = load_config(getattr(args, "config", None))
    servers = _filter_servers(config.runtime.mcp_servers, getattr(args, "name", ""))
    manager = MCPFleetManager(servers)
    try:
        tools = manager.discover_tools(parallel=True)
        failed = manager.failed_servers
        print_json_payload(
            {
                "ok": not failed,
                "server_count": len(servers),
                "tool_count": len(tools),
                "failed_servers": {
                    key: {
                        "reason_code": value.reason_code,
                        "message": value.message,
                    }
                    for key, value in failed.items()
                },
            }
        )
        return 0 if not failed else 1
    finally:
        manager.close()


def _mcp_restart(args: argparse.Namespace) -> int:
    runtime = APIRuntime.from_config_path(getattr(args, "config", None))
    try:
        manager = getattr(runtime.tools, "mcp_manager", None)
        if manager is None:
            print_json_payload({"ok": False, "reason_code": "mcp_manager_unavailable"})
            return 1
        name = str(getattr(args, "name", "") or "").strip()
        if name:
            manager.close_server(name)
        else:
            manager.close()
        print_json_payload({"ok": True, "restarted": name or "all"})
        return 0
    finally:
        runtime.close()


def _mcp_logs(args: argparse.Namespace) -> int:
    runtime = APIRuntime.from_config_path(getattr(args, "config", None))
    try:
        manager = getattr(runtime.tools, "mcp_manager", None)
        if manager is None:
            print_json_payload({"ok": True, "logs": {}})
            return 0
        logs = manager.mcp_server_logs(limit=int(getattr(args, "limit", 10) or 10))
        name = str(getattr(args, "name", "") or "").strip()
        payload = {
            server_name: [getattr(item, "__dict__", {}) for item in entries]
            for server_name, entries in logs.items()
            if not name or server_name == name
        }
        print_json_payload({"ok": True, "logs": payload})
        return 0
    finally:
        runtime.close()


def _servers_from_external_payload(payload: Any) -> list[MCPServerConfig]:
    if not isinstance(payload, dict):
        raise RuntimeError("MCP import payload must be a JSON object")
    raw_servers = payload.get("mcpServers") or payload.get("servers") or {}
    if not isinstance(raw_servers, dict):
        raise RuntimeError("MCP import payload must contain an object mcpServers map")
    servers: list[MCPServerConfig] = []
    for raw_name, raw_config in raw_servers.items():
        if not isinstance(raw_config, dict):
            continue
        package_payload = raw_config.get("package_metadata")
        package_metadata = (
            MCPPackageMetadataConfig(
                origin=package_payload.get("origin", ""),
                version=package_payload.get("version", ""),
                install_command=list(package_payload.get("install_command", []) or []),
                trust_state=package_payload.get("trust_state", ""),
            )
            if isinstance(package_payload, dict)
            else MCPPackageMetadataConfig()
        )
        command = str(raw_config.get("command", "") or "").strip()
        args = [
            str(item).strip()
            for item in list(raw_config.get("args", []) or [])
            if str(item).strip()
        ]
        url = str(raw_config.get("url", "") or "").strip()
        transport = "streamable_http" if url else "stdio"
        servers.append(
            MCPServerConfig(
                name=str(raw_name),
                transport=transport,
                command=([command, *args] if command else []),
                url=url,
                env=dict(raw_config.get("env", {}) or {}),
                env_secret_refs=dict(raw_config.get("env_secret_refs", {}) or {}),
                cwd=str(raw_config.get("cwd", "") or ""),
                trusted=bool(raw_config.get("trusted", False)),
                package_metadata=package_metadata,
            )
        )
    return servers


def _filter_servers(
    servers: list[MCPServerConfig], name: object
) -> list[MCPServerConfig]:
    token = str(name or "").strip()
    if not token:
        return list(servers)
    return [server for server in servers if server.name == token]


def _redacted_server_payload(server: MCPServerConfig) -> dict[str, Any]:
    return {
        "name": server.name,
        "transport": server.transport,
        "command": list(server.command),
        "url": server.url,
        "authorization": server.authorization.redacted_dict(),
        "env": {
            key: _redact_env_value(key, value) for key, value in server.env.items()
        },
        "env_secret_refs": dict(server.env_secret_refs),
        "cwd": server.cwd,
        "trusted": server.trusted,
        "package_metadata": server.package_metadata.to_dict(),
        "approval": {
            "mode": server.approval.mode,
            "tool_patterns": list(server.approval.tool_patterns),
            "risk_levels": list(server.approval.risk_levels),
        },
    }


def _redact_env_value(key: str, value: str) -> str:
    lowered = str(key or "").lower()
    if any(token in lowered for token in _SECRET_KEY_TOKENS):
        return "<redacted>"
    return str(value or "")


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("mcp", help="Manage MCP servers")
    mcp_sub = parser.add_subparsers(dest="mcp_command", required=True)

    import_parser = mcp_sub.add_parser("import", help="Import MCP server config")
    import_parser.add_argument(
        "--from", dest="source", required=True, help="Source JSON file"
    )
    import_parser.add_argument(
        "--write",
        action="store_true",
        help="Write imported servers to OpenMinion config",
    )
    import_parser.set_defaults(handler=run_mcp)

    list_parser = mcp_sub.add_parser("list", help="List configured MCP servers")
    list_parser.set_defaults(handler=run_mcp)

    validate_parser = mcp_sub.add_parser("validate", help="Validate MCP server config")
    validate_parser.set_defaults(handler=run_mcp)

    test_parser = mcp_sub.add_parser("test", help="Test MCP server discovery")
    test_parser.add_argument("name", nargs="?", default="", help="Optional server name")
    test_parser.set_defaults(handler=run_mcp)

    restart_parser = mcp_sub.add_parser("restart", help="Restart MCP sessions")
    restart_parser.add_argument(
        "name", nargs="?", default="", help="Optional server name"
    )
    restart_parser.set_defaults(handler=run_mcp)

    logs_parser = mcp_sub.add_parser("logs", help="Show recent MCP protocol logs")
    logs_parser.add_argument("name", nargs="?", default="", help="Optional server name")
    logs_parser.add_argument(
        "--limit", type=int, default=10, help="Max log entries per server"
    )
    logs_parser.set_defaults(handler=run_mcp)
