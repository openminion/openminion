from __future__ import annotations

import argparse
from dataclasses import asdict, replace
import json
from pathlib import Path
import secrets
import sys
import time
import webbrowser
from typing import TYPE_CHECKING, Any
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from openminion.cli.presentation.json_output import print_json_payload

if TYPE_CHECKING:
    from openminion.base.config.mcp import MCPServerConfig


def run_mcp(args: argparse.Namespace) -> int:
    command = str(getattr(args, "mcp_command", "") or "").strip().lower()
    handler = {
        "add": _mcp_add,
        "get": _mcp_get,
        "remove": _mcp_remove,
        "enable": _mcp_enable,
        "disable": _mcp_disable,
        "trust": _mcp_trust,
        "untrust": _mcp_untrust,
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


def _mcp_add(args: argparse.Namespace) -> int:
    from openminion.base.config.io import save_config
    from openminion.base.config.mcp import (
        MCPApprovalConfig,
        MCPAuthorizationConfig,
        MCPServerConfig,
        MCPStdioSandboxConfig,
    )

    manager = _load_writable_config_manager(args)
    config = manager.base_config
    command = list(getattr(args, "command", []) or [])
    if command[:1] == ["--"]:
        command = command[1:]
    url = str(getattr(args, "url", "") or "").strip()
    if bool(command) == bool(url):
        raise RuntimeError("mcp add requires either --url or --command")

    authorization_kwargs: dict[str, Any] = {
        "mode": str(getattr(args, "auth", "none") or "none").replace("-", "_"),
        "bearer_token_ref": getattr(args, "bearer_token_ref", ""),
        "client_id": getattr(args, "client_id", ""),
        "authorization_server_metadata_url": getattr(
            args, "authorization_metadata_url", ""
        ),
        "authorization_endpoint": getattr(args, "authorization_endpoint", ""),
        "token_endpoint": getattr(args, "token_endpoint", ""),
        "registration_endpoint": getattr(args, "registration_endpoint", ""),
        "revocation_endpoint": getattr(args, "revocation_endpoint", ""),
        "redirect_uri": getattr(args, "redirect_uri", ""),
        "scope": getattr(args, "scope", ""),
        "access_token_ref": getattr(args, "access_token_ref", ""),
        "refresh_token_ref": getattr(args, "refresh_token_ref", ""),
    }
    client_issuer = str(getattr(args, "client_issuer", "") or "").strip()
    if client_issuer:
        authorization_kwargs["client_issuer"] = client_issuer

    server = MCPServerConfig(
        name=args.name,
        enabled=not bool(getattr(args, "disabled", False)),
        required=bool(getattr(args, "required", False)),
        transport="streamable_http" if url else "stdio",
        command=command,
        url=url,
        authorization=MCPAuthorizationConfig(**authorization_kwargs),
        env=dict(getattr(args, "env", []) or []),
        env_secret_refs=dict(getattr(args, "env_secret", []) or []),
        cwd=str(getattr(args, "cwd", "") or ""),
        startup_timeout_seconds=getattr(args, "startup_timeout", 15.0),
        request_timeout_seconds=getattr(args, "request_timeout", 30.0),
        stderr_buffer_bytes=getattr(args, "stderr_buffer_bytes", 65536),
        tool_risk_overrides=list(getattr(args, "risk_override", []) or []),
        approval=MCPApprovalConfig(
            mode=getattr(args, "approval_mode", "never"),
            tool_patterns=list(getattr(args, "approval_tool", []) or []),
            risk_levels=list(getattr(args, "approval_risk", []) or []),
        ),
        trusted=bool(getattr(args, "trusted", False)),
        stdio_sandbox=MCPStdioSandboxConfig(
            require_trust=getattr(args, "sandbox_require_trust", True),
            cwd_allowlist=list(getattr(args, "sandbox_cwd", []) or []),
            env_allowlist=list(getattr(args, "sandbox_env", []) or []),
            inherit_env_allowlist=list(getattr(args, "sandbox_inherit_env", []) or []),
            package_name=getattr(args, "sandbox_package_name", ""),
            package_version=getattr(args, "sandbox_package_version", ""),
            trust_reason=getattr(args, "sandbox_trust_reason", ""),
        ),
    )
    if any(item.name == server.name for item in config.runtime.mcp_servers):
        raise RuntimeError(f"MCP server already exists: {server.name}")
    config.runtime.mcp_servers.append(server)
    written_path = save_config(
        config, str(manager.config_path), home_root=manager.home_root
    )
    print_json_payload(
        {
            "ok": True,
            "server": _redacted_server_payload(server),
            "applies": "next_startup",
            "config_path": str(written_path),
        }
    )
    return 0


def _mcp_get(args: argparse.Namespace) -> int:
    from openminion.cli.config import load_cli_manager_from_args

    config = load_cli_manager_from_args(args).base_config
    server = _require_server(config.runtime.mcp_servers, args.name)
    print_json_payload({"ok": True, "server": _redacted_server_payload(server)})
    return 0


def _mcp_remove(args: argparse.Namespace) -> int:
    from openminion.base.config.io import save_config
    from openminion.cli.config import load_cli_manager_from_args

    manager = load_cli_manager_from_args(args)
    config = manager.base_config
    server = _require_server(config.runtime.mcp_servers, args.name)
    config.runtime.mcp_servers = [
        item for item in config.runtime.mcp_servers if item.name != server.name
    ]
    written_path = save_config(
        config, str(manager.config_path), home_root=manager.home_root
    )
    print_json_payload(
        {
            "ok": True,
            "removed": server.name,
            "applies": "next_startup",
            "config_path": str(written_path),
        }
    )
    return 0


def _mcp_enable(args: argparse.Namespace) -> int:
    return _mcp_set_enabled(args, enabled=True)


def _mcp_disable(args: argparse.Namespace) -> int:
    return _mcp_set_enabled(args, enabled=False)


def _mcp_trust(args: argparse.Namespace) -> int:
    return _mcp_set_trusted(args, trusted=True)


def _mcp_untrust(args: argparse.Namespace) -> int:
    return _mcp_set_trusted(args, trusted=False)


def _mcp_set_enabled(args: argparse.Namespace, *, enabled: bool) -> int:
    from openminion.base.config.io import save_config
    from openminion.cli.config import load_cli_manager_from_args

    manager = load_cli_manager_from_args(args)
    config = manager.base_config
    server = _require_server(config.runtime.mcp_servers, args.name)
    updated = replace(server, enabled=enabled)
    config.runtime.mcp_servers = [
        updated if item.name == server.name else item
        for item in config.runtime.mcp_servers
    ]
    written_path = save_config(
        config, str(manager.config_path), home_root=manager.home_root
    )
    print_json_payload(
        {
            "ok": True,
            "server": _redacted_server_payload(updated),
            "applies": "next_startup",
            "config_path": str(written_path),
        }
    )
    return 0


def _mcp_set_trusted(args: argparse.Namespace, *, trusted: bool) -> int:
    from openminion.base.config.io import save_config
    from openminion.cli.config import load_cli_manager_from_args

    manager = load_cli_manager_from_args(args)
    config = manager.base_config
    server = _require_server(config.runtime.mcp_servers, args.name)
    if server.transport != "stdio":
        raise RuntimeError("mcp trust/untrust applies only to stdio servers")
    updated = replace(server, trusted=trusted)
    config.runtime.mcp_servers = [
        updated if item.name == server.name else item
        for item in config.runtime.mcp_servers
    ]
    written_path = save_config(
        config, str(manager.config_path), home_root=manager.home_root
    )
    print_json_payload(
        {
            "ok": True,
            "server": _redacted_server_payload(updated),
            "applies": "next_startup",
            "config_path": str(written_path),
        }
    )
    return 0


def _mcp_import(args: argparse.Namespace) -> int:
    from openminion.base.config.io import save_config

    source_raw = str(getattr(args, "source", "") or "").strip()
    source_path = Path(source_raw).expanduser() if source_raw else None

    if not source_path or not source_path.exists():
        print_json_payload(
            {
                "ok": False,
                "error": {
                    "code": "SOURCE_NOT_FOUND",
                    "message": "Source file does not exist",
                    "details": {"path": str(source_path or "")},
                },
            }
        )
        return 1

    if not source_path.is_file():
        print_json_payload(
            {
                "ok": False,
                "error": {
                    "code": "INVALID_SOURCE",
                    "message": "Source path is not a file",
                    "details": {"path": str(source_path)},
                },
            }
        )
        return 1

    try:
        content = source_path.read_text(encoding="utf-8")
    except OSError as exc:
        print_json_payload(
            {
                "ok": False,
                "error": {
                    "code": "SOURCE_READ_ERROR",
                    "message": str(exc),
                    "details": {"path": str(source_path)},
                },
            }
        )
        return 1

    try:
        payload = json.loads(content)
    except (json.JSONDecodeError, ValueError) as exc:
        print_json_payload(
            {
                "ok": False,
                "error": {
                    "code": "INVALID_SOURCE",
                    "message": f"Malformed JSON: {exc}",
                    "details": {"path": str(source_path)},
                },
            }
        )
        return 1

    if not isinstance(payload, dict):
        print_json_payload(
            {
                "ok": False,
                "error": {
                    "code": "INVALID_SOURCE",
                    "message": "MCP import payload must be a JSON object",
                    "details": {"path": str(source_path)},
                },
            }
        )
        return 1

    try:
        imported = _servers_from_external_payload(payload)
    except Exception as exc:
        print_json_payload(
            {
                "ok": False,
                "error": {
                    "code": "INVALID_SOURCE",
                    "message": str(exc),
                    "details": {"path": str(source_path)},
                },
            }
        )
        return 1

    write = bool(getattr(args, "write", False))
    written_path: Path | None = None
    if write:
        manager = _load_writable_config_manager(args)
        config = manager.base_config
        existing = {server.name: server for server in config.runtime.mcp_servers}
        for server in imported:
            existing[server.name] = server
        config.runtime.mcp_servers = list(existing.values())
        written_path = save_config(
            config, str(manager.config_path), home_root=manager.home_root
        )
    output = {
        "ok": True,
        "imported": [_redacted_server_payload(server) for server in imported],
        "written": write,
    }
    if written_path is not None:
        output["config_path"] = str(written_path)
    print_json_payload(output)
    return 0


def _mcp_list(args: argparse.Namespace) -> int:
    from openminion.cli.config import load_cli_manager_from_args

    config = load_cli_manager_from_args(args).base_config
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
    from openminion.base.config.mcp import coerce_mcp_server_configs
    from openminion.cli.config import load_cli_manager_from_args

    config = load_cli_manager_from_args(args).base_config
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
    manager = _configured_manager(args)
    try:
        tools = manager.discover_tools(parallel=True)
        failures = manager.failed_servers
        prompts = manager.discover_prompts(parallel=True)
        failures.update(manager.failed_servers)
        resources = manager.discover_resources(parallel=True)
        failures.update(manager.failed_servers)
        templates = manager.discover_resource_templates(parallel=True)
        failures.update(manager.failed_servers)
        status = manager.server_status_snapshot()
        print_json_payload(
            {
                "ok": not failures,
                "servers": {
                    name: {
                        "protocol": item["protocol_version"],
                        "identity": item["server_info"],
                    }
                    for name, item in status.items()
                },
                "tools": [asdict(item) for item in tools],
                "prompts": [asdict(item) for item in prompts],
                "resources": [asdict(item) for item in resources],
                "resource_templates": [asdict(item) for item in templates],
                "failed_servers": {
                    key: {
                        "reason_code": value.reason_code,
                        "message": value.message,
                        "details": value.details,
                    }
                    for key, value in failures.items()
                },
            }
        )
        return 0 if not failures else 1
    finally:
        manager.close()


def _mcp_login(args: argparse.Namespace) -> int:
    from openminion.cli.config import load_cli_manager_from_args
    from openminion.modules.secret.factory import build_secret_service
    from openminion.tools.mcp.auth import (
        SecretServiceMCPTokenStore,
        discover_oauth_metadata,
    )
    from openminion.tools.mcp.transport import probe_oauth_challenge

    manager = load_cli_manager_from_args(args)
    servers = _filter_servers(manager.base_config.runtime.mcp_servers, args.name)
    if len(servers) != 1 or servers[0].authorization.mode != "oauth_pkce":
        raise RuntimeError("mcp login requires one oauth_pkce server")
    server = servers[0]
    if not server.authorization.access_token_ref:
        raise RuntimeError("oauth_pkce server requires access_token_ref for mcp login")
    secret_service = build_secret_service(data_root=manager.data_root, env=manager.env)
    if secret_service is None:
        raise RuntimeError("mcp login requires OPENMINION_SECRET_KEY")
    token_store = SecretServiceMCPTokenStore(secret_service)
    callback = None
    try:
        code = str(getattr(args, "code", "") or "").strip()
        if code:
            return _complete_mcp_login(args, server, token_store, code)
        auth_challenge = probe_oauth_challenge(server)
        metadata = discover_oauth_metadata(
            server.authorization,
            resource=server.url,
            resource_metadata_url=auth_challenge["resource_metadata"],
        )
        manual = bool(getattr(args, "manual", False))
        if manual:
            if not server.authorization.redirect_uri:
                raise RuntimeError("manual MCP login requires configured redirect_uri")
            redirect_uri = server.authorization.redirect_uri
        else:
            from openminion.tools.mcp.oauth_callback import MCPOAuthCallbackServer

            callback = MCPOAuthCallbackServer()
            redirect_uri = callback.redirect_uri
        server, registration = _prepare_mcp_login_client(
            manager,
            server,
            metadata,
            redirect_uri=redirect_uri,
        )
        payload = _start_mcp_login(
            server,
            token_store,
            metadata,
            auth_challenge=auth_challenge,
            client_registration=registration,
        )
        if manual:
            print_json_payload(payload)
            return 0
        assert callback is not None
        if not webbrowser.open(payload["authorization_url"]):
            print_json_payload(payload)
        result = callback.wait(
            expected_state=payload["state"],
            timeout_seconds=float(getattr(args, "callback_timeout", 180.0)),
        )
        if result.error:
            from openminion.tools.mcp.auth import validate_authorization_issuer

            validate_authorization_issuer(
                expected=metadata.issuer,
                received=result.issuer,
                required=metadata.authorization_response_iss_parameter_supported,
            )
            raise RuntimeError(f"OAuth authorization failed: {result.error}")
        completion_args = argparse.Namespace(
            state=payload["state"], issuer=result.issuer
        )
        return _complete_mcp_login(
            completion_args,
            server,
            token_store,
            result.code,
        )
    finally:
        if callback is not None:
            callback.close()
        token_store.close()


def _prepare_mcp_login_client(
    manager: Any,
    server: Any,
    metadata: Any,
    *,
    redirect_uri: str,
) -> tuple[Any, str]:
    from openminion.services.bootstrap.provider_setup import atomic_save_setup_config
    from openminion.tools.mcp.auth import register_oauth_client, select_oauth_client

    selection = select_oauth_client(server.authorization, metadata)
    client_id = selection.client_id
    if selection.method == "dcr":
        registration = register_oauth_client(
            metadata=metadata,
            client_name="OpenMinion",
            redirect_uris=[redirect_uri],
        )
        client_id = str(registration["client_id"])
    client_issuer = (
        server.authorization.client_issuer
        if selection.method == "cimd"
        else selection.issuer
    )
    authorization = replace(
        server.authorization,
        client_id=client_id,
        client_issuer=client_issuer,
        redirect_uri=redirect_uri,
    )
    updated = replace(server, authorization=authorization)
    if (
        client_id != server.authorization.client_id
        or client_issuer != server.authorization.client_issuer
    ):
        persisted = replace(
            server,
            authorization=replace(
                server.authorization,
                client_id=client_id,
                client_issuer=client_issuer,
            ),
        )
        manager.base_config.runtime.mcp_servers = [
            persisted if item.name == server.name else item
            for item in manager.base_config.runtime.mcp_servers
        ]
        atomic_save_setup_config(manager.base_config, manager.config_path)
    return updated, selection.method


def _start_mcp_login(
    server: Any,
    token_store: Any,
    metadata: Any,
    *,
    auth_challenge: dict[str, str],
    client_registration: str,
) -> dict[str, Any]:
    from openminion.tools.mcp.auth import (
        MCPOAuthTransaction,
        build_authorization_url,
        build_pkce_challenge,
        store_oauth_transaction,
    )

    challenge = build_pkce_challenge()
    state = secrets.token_urlsafe(24)
    store_oauth_transaction(
        token_store,
        MCPOAuthTransaction(
            server_name=server.name,
            resource=server.url,
            redirect_uri=server.authorization.redirect_uri,
            state=state,
            code_verifier=challenge.code_verifier,
            issuer=metadata.issuer,
            issuer_required=metadata.authorization_response_iss_parameter_supported,
            authorization_endpoint=metadata.authorization_endpoint,
            token_endpoint=metadata.token_endpoint,
            expires_at=time.time() + 600,
            resource_metadata_url=auth_challenge["resource_metadata"],
            scope=auth_challenge.get("scope", ""),
        ),
    )
    return {
        "ok": True,
        "server": server.name,
        "authorization_url": build_authorization_url(
            config=server.authorization,
            metadata=metadata,
            challenge=challenge,
            state=state,
            resource=server.url,
            scope=auth_challenge.get("scope", ""),
        ),
        "state": state,
        "issuer_required": metadata.authorization_response_iss_parameter_supported,
        "client_registration": client_registration,
    }


def _complete_mcp_login(
    args: argparse.Namespace,
    server: Any,
    token_store: Any,
    code: str,
) -> int:
    from openminion.tools.mcp.auth import (
        MCPOAuthPKCEChallenge,
        consume_oauth_transaction,
        exchange_authorization_code,
        discover_oauth_metadata,
        store_issuer_bound_token,
    )

    returned_state = str(getattr(args, "state", "") or "").strip()
    if not returned_state:
        raise RuntimeError("mcp login --code requires --state")
    transaction = consume_oauth_transaction(
        token_store,
        server_name=server.name,
        state=returned_state,
    )
    metadata = discover_oauth_metadata(
        server.authorization,
        resource=server.url,
        resource_metadata_url=transaction.resource_metadata_url,
    )
    if not _oauth_transaction_matches(transaction, server, metadata):
        raise RuntimeError("mcp login metadata changed since authorization")
    token_state = exchange_authorization_code(
        config=server.authorization,
        metadata=metadata,
        code=code,
        challenge=MCPOAuthPKCEChallenge(
            code_verifier=transaction.code_verifier,
            code_challenge="",
        ),
        authorization_issuer=str(getattr(args, "issuer", "") or ""),
        resource=server.url,
        scope=transaction.scope,
    )
    store_issuer_bound_token(
        token_store,
        server.authorization.access_token_ref,
        token_state.access_token,
        metadata.issuer,
    )
    if token_state.refresh_token and server.authorization.refresh_token_ref:
        store_issuer_bound_token(
            token_store,
            server.authorization.refresh_token_ref,
            token_state.refresh_token,
            metadata.issuer,
        )
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


def _oauth_transaction_matches(transaction: Any, server: Any, metadata: Any) -> bool:
    return bool(
        transaction.resource == server.url
        and transaction.redirect_uri == server.authorization.redirect_uri
        and transaction.issuer == metadata.issuer
        and transaction.issuer_required
        == metadata.authorization_response_iss_parameter_supported
        and transaction.authorization_endpoint == metadata.authorization_endpoint
        and transaction.token_endpoint == metadata.token_endpoint
    )


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
    from openminion.cli.config import load_cli_manager_from_args
    from openminion.tools.mcp.manager import MCPFleetManager
    from openminion.tools.mcp.auth import build_runtime_mcp_token_store

    config = load_cli_manager_from_args(args).base_config
    configured_servers = list(config.runtime.mcp_servers)
    name = str(getattr(args, "name", "") or "").strip()
    if name:
        selected = _require_server(configured_servers, name)
        if not selected.enabled:
            raise RuntimeError(f"MCP server is disabled: {selected.name}")
        servers = [selected]
    else:
        servers = [server for server in configured_servers if server.enabled]
    return MCPFleetManager(
        servers,
        token_store=build_runtime_mcp_token_store(config.runtime),
    )


def _load_writable_config_manager(args: argparse.Namespace) -> Any:
    from openminion.base.config import ConfigManager, OpenMinionConfig
    from openminion.base.config.io import resolve_config_path
    from openminion.cli.config import (
        load_cli_manager_from_args,
        resolve_cli_roots,
    )

    roots = resolve_cli_roots(
        config_path=getattr(args, "config", None),
        home_root=getattr(args, "home_root", None),
        data_root=getattr(args, "data_root", None),
    )
    config_path = resolve_config_path(
        getattr(args, "config", None), home_root=roots.home_root
    )
    if config_path.exists():
        return load_cli_manager_from_args(args)
    return ConfigManager(
        base_config=OpenMinionConfig(),
        home_root=roots.home_root,
        data_root=roots.data_root,
        config_path=config_path,
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
    from openminion.cli.config import load_cli_manager_from_args
    from openminion.tools.mcp.server import (
        build_runtime_published_tools,
        serve_published_stdio,
    )

    runtime = APIRuntime.from_manager(load_cli_manager_from_args(args))
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
                trusted=False,
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


def _require_server(servers: list[MCPServerConfig], name: object) -> MCPServerConfig:
    from openminion.base.config.mcp import normalize_mcp_server_name

    normalized = normalize_mcp_server_name(name)
    for server in servers:
        if server.name == normalized:
            return server
    raise RuntimeError(f"MCP server not found: {normalized}")


def _redacted_server_payload(server: MCPServerConfig) -> dict[str, Any]:
    return {
        "name": server.name,
        "enabled": server.enabled,
        "required": server.required,
        "transport": server.transport,
        "command": list(server.command),
        "url": server.url,
        "authorization": server.authorization.redacted_dict(),
        "env": {key: "<redacted>" for key in server.env},
        "env_secret_refs": dict(server.env_secret_refs),
        "cwd": server.cwd,
        "startup_timeout_seconds": server.startup_timeout_seconds,
        "request_timeout_seconds": server.request_timeout_seconds,
        "stderr_buffer_bytes": server.stderr_buffer_bytes,
        "tool_risk_overrides": [
            asdict(override) for override in server.tool_risk_overrides
        ],
        "trusted": server.trusted,
        "stdio_sandbox": asdict(server.stdio_sandbox),
        "package_metadata": server.package_metadata.to_dict(),
        "approval": {
            "mode": server.approval.mode,
            "tool_patterns": list(server.approval.tool_patterns),
            "risk_levels": list(server.approval.risk_levels),
        },
    }


def register(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    from .mcp_parser import register as register_parser

    register_parser(subparsers, handler=run_mcp)
