from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import secrets
import sys
from typing import TYPE_CHECKING, Any
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from openminion.cli.presentation.json_output import print_json_payload

if TYPE_CHECKING:
    from openminion.base.config.mcp import MCPServerConfig

_SECRET_KEY_TOKENS = ("token", "secret", "password", "key", "authorization")


def run_mcp(args: argparse.Namespace) -> int:
    command = str(getattr(args, "mcp_command", "") or "").strip().lower()
    handler = {
        "import": _mcp_import,
        "list": _mcp_list,
        "validate": _mcp_validate,
        "test": _mcp_test,
        "login": _mcp_login,
        "browse": _mcp_browse,
        "prompt": _mcp_prompt,
        "resource": _mcp_resource,
        "registry-search": _mcp_registry_search,
        "serve": _mcp_serve,
    }.get(command)
    if handler is None:
        raise RuntimeError("Unknown mcp command")
    return handler(args)


def _mcp_import(args: argparse.Namespace) -> int:
    from openminion.base.config.io import load_config, save_config

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
    from openminion.base.config.io import load_config

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
    from openminion.base.config.io import load_config
    from openminion.base.config.mcp import coerce_mcp_server_configs

    config = load_config(getattr(args, "config", None))
    servers = coerce_mcp_server_configs(config.runtime.mcp_servers)
    issues = []
    for server in servers:
        if not server.enabled:
            continue
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
    from openminion.base.config.io import load_config
    from openminion.tools.mcp.manager import MCPFleetManager

    config = load_config(getattr(args, "config", None))
    servers = [
        server
        for server in _filter_servers(
            config.runtime.mcp_servers, getattr(args, "name", "")
        )
        if server.enabled
    ]
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


def _mcp_login(args: argparse.Namespace) -> int:
    from openminion.base.config.manager import ConfigManager
    from openminion.modules.secret.factory import build_secret_service
    from openminion.tools.mcp.auth import (
        MCPOAuthPKCEChallenge,
        SecretServiceMCPTokenStore,
        build_authorization_url,
        build_pkce_challenge,
        discover_oauth_metadata,
        exchange_authorization_code,
    )

    manager = ConfigManager.load(getattr(args, "config", None))
    servers = _filter_servers(manager.base_config.runtime.mcp_servers, args.name)
    if len(servers) != 1 or servers[0].authorization.mode != "oauth_pkce":
        raise RuntimeError("mcp login requires one oauth_pkce server")
    server = servers[0]
    metadata = discover_oauth_metadata(server.authorization)
    code = str(getattr(args, "code", "") or "").strip()
    if not code:
        challenge = build_pkce_challenge()
        state = secrets.token_urlsafe(24)
        print_json_payload(
            {
                "ok": True,
                "server": server.name,
                "authorization_url": build_authorization_url(
                    config=server.authorization,
                    metadata=metadata,
                    challenge=challenge,
                    state=state,
                    resource=server.url,
                ),
                "code_verifier": challenge.code_verifier,
                "state": state,
                "client_registration": (
                    "cimd"
                    if metadata.client_id_metadata_document_supported
                    and server.authorization.client_id.startswith("https://")
                    else "pre_registered"
                ),
            }
        )
        return 0

    verifier = str(getattr(args, "verifier", "") or "").strip()
    if not verifier:
        raise RuntimeError("mcp login --code requires --verifier")
    token_state = exchange_authorization_code(
        config=server.authorization,
        metadata=metadata,
        code=code,
        challenge=MCPOAuthPKCEChallenge(
            code_verifier=verifier,
            code_challenge="",
        ),
        authorization_issuer=str(getattr(args, "issuer", "") or ""),
        resource=server.url,
    )
    if not server.authorization.access_token_ref:
        raise RuntimeError("oauth_pkce server requires access_token_ref for mcp login")
    secret_service = build_secret_service(data_root=manager.data_root, env=manager.env)
    if secret_service is None:
        raise RuntimeError("mcp login requires OPENMINION_SECRET_KEY")
    token_store = SecretServiceMCPTokenStore(secret_service)
    try:
        token_store.set(server.authorization.access_token_ref, token_state.access_token)
        if token_state.refresh_token and server.authorization.refresh_token_ref:
            token_store.set(
                server.authorization.refresh_token_ref,
                token_state.refresh_token,
            )
    finally:
        token_store.close()
    print_json_payload(
        {
            "ok": True,
            "server": server.name,
            "access_token": "<stored>",
            "refresh_token": "<stored>" if token_state.refresh_token else "",
            "scope": token_state.scope,
        }
    )
    return 0


def _mcp_browse(args: argparse.Namespace) -> int:
    manager = _configured_manager(args)
    try:
        payload = {
            "tools": [asdict(item) for item in manager.discover_tools(parallel=True)],
            "prompts": [
                asdict(item) for item in manager.discover_prompts(parallel=True)
            ],
            "resources": [
                asdict(item) for item in manager.discover_resources(parallel=True)
            ],
            "resource_templates": [
                asdict(item)
                for item in manager.discover_resource_templates(parallel=True)
            ],
        }
        print_json_payload({"ok": not manager.failed_servers, **payload})
        return 0 if not manager.failed_servers else 1
    finally:
        manager.close()


def _mcp_prompt(args: argparse.Namespace) -> int:
    manager = _configured_manager(args)
    try:
        result = manager.get_prompt(
            server_name=args.name,
            remote_name=args.prompt_name,
            arguments=_json_object(args.arguments),
        )
        print_json_payload({"ok": True, "result": result})
        return 0
    finally:
        manager.close()


def _mcp_resource(args: argparse.Namespace) -> int:
    manager = _configured_manager(args)
    try:
        result = manager.read_resource(
            server_name=args.name,
            resource_uri=args.uri,
        )
        print_json_payload(
            {
                "ok": True,
                "text_fallback": args.uri.startswith("ui://"),
                "result": result,
            }
        )
        return 0
    finally:
        manager.close()


def _configured_manager(args: argparse.Namespace) -> Any:
    from openminion.base.config.io import load_config
    from openminion.tools.mcp.manager import MCPFleetManager
    from openminion.tools.mcp.auth import build_runtime_mcp_token_store

    config = load_config(getattr(args, "config", None))
    servers = [
        server
        for server in _filter_servers(config.runtime.mcp_servers, args.name)
        if server.enabled
    ]
    return MCPFleetManager(
        servers,
        token_store=build_runtime_mcp_token_store(config.runtime),
    )


def _json_object(raw: str) -> dict[str, Any]:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise RuntimeError("MCP arguments must be a JSON object")
    return value


def _mcp_registry_search(args: argparse.Namespace) -> int:
    query = urllib_parse.urlencode({"search": args.query, "limit": args.limit})
    request = urllib_request.Request(
        f"https://registry.modelcontextprotocol.io/v0.1/servers?{query}",
        headers={"Accept": "application/json"},
    )
    with urllib_request.urlopen(request, timeout=10.0) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("servers"), list):
        raise RuntimeError("MCP registry returned an invalid server list")
    print_json_payload({"ok": True, "servers": payload["servers"]})
    return 0


def _mcp_serve(args: argparse.Namespace) -> int:
    from openminion.api.runtime import APIRuntime
    from openminion.tools.mcp.server import (
        build_runtime_published_tools,
        serve_published_stdio,
    )

    runtime = APIRuntime.from_config_path(getattr(args, "config", None))
    try:
        tools = build_runtime_published_tools(runtime)
        if not tools:
            raise RuntimeError("runtime.mcp_publish must be enabled with visible tools")
        serve_published_stdio(
            tools,
            input_stream=sys.stdin,
            output_stream=sys.stdout,
        )
        return 0
    finally:
        runtime.close()


def _servers_from_external_payload(payload: Any) -> list[MCPServerConfig]:
    from openminion.base.config.mcp import (
        MCPPackageMetadataConfig,
        MCPServerConfig,
    )

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
                enabled=raw_config.get("enabled", True),
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
        "enabled": server.enabled,
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

    login_parser = mcp_sub.add_parser("login", help="Authorize a remote MCP server")
    login_parser.add_argument("name", help="Configured MCP server name")
    login_parser.add_argument("--code", default="", help="Authorization code")
    login_parser.add_argument("--verifier", default="", help="PKCE code verifier")
    login_parser.add_argument("--issuer", default="", help="Authorization issuer")
    login_parser.set_defaults(handler=run_mcp)

    browse_parser = mcp_sub.add_parser("browse", help="List server capabilities")
    browse_parser.add_argument("name", nargs="?", default="", help="Server name")
    browse_parser.set_defaults(handler=run_mcp)

    prompt_parser = mcp_sub.add_parser("prompt", help="Get an MCP prompt")
    prompt_parser.add_argument("name", help="Server name")
    prompt_parser.add_argument("prompt_name", help="Prompt name")
    prompt_parser.add_argument("--arguments", default="{}", help="JSON arguments")
    prompt_parser.set_defaults(handler=run_mcp)

    resource_parser = mcp_sub.add_parser("resource", help="Read an MCP resource")
    resource_parser.add_argument("name", help="Server name")
    resource_parser.add_argument("uri", help="Resource URI")
    resource_parser.set_defaults(handler=run_mcp)

    registry_parser = mcp_sub.add_parser(
        "registry-search", help="Search the official MCP registry"
    )
    registry_parser.add_argument("query", help="Server-name search query")
    registry_parser.add_argument("--limit", type=int, choices=range(1, 101), default=20)
    registry_parser.set_defaults(handler=run_mcp)

    serve_parser = mcp_sub.add_parser("serve", help="Publish tools over MCP stdio")
    serve_parser.set_defaults(handler=run_mcp)
