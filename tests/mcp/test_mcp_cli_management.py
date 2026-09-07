from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
from types import SimpleNamespace
from urllib import parse as urllib_parse

import pytest

from openminion.base.config.io import load_config
from openminion.base.config.mcp import MCPAuthorizationConfig, MCPServerConfig
from openminion.cli.commands.mcp import run_mcp
from openminion.cli.parser.base import build_parser
from openminion.tools.mcp.auth import MCPOAuthMetadata, MCPOAuthTokenState


def _args(mcp_command: str, **kwargs):
    payload = {"mcp_command": mcp_command, "config": kwargs.pop("config", None)}
    payload.update(kwargs)
    return argparse.Namespace(**payload)


def _write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _runtime_config(server: dict[str, object]) -> dict[str, object]:
    return {
        "runtime": {"mcp_servers": [server]},
        "agents": {"default": {"provider": "echo"}},
        "default_agent": "default",
    }


def test_mcp_import_redacts_secret_stdout(tmp_path: Path, capsys) -> None:
    source = _write_json(
        tmp_path / "claude.json",
        {
            "mcpServers": {
                "Fixture": {
                    "enabled": False,
                    "command": "node",
                    "args": ["server.js"],
                    "env": {"API_TOKEN": "raw-secret", "SAFE_FLAG": "1"},
                    "env_secret_refs": {"SERVICE_TOKEN": "secret://service/token"},
                    "package_metadata": {
                        "origin": "https://example.invalid/mcp-fixture",
                        "version": "1.2.3",
                        "install_command": ["npm", "install", "fixture"],
                        "trust_state": "trusted",
                    },
                }
            }
        },
    )
    config = tmp_path / "openminion.json"

    assert (
        run_mcp(_args("import", config=str(config), source=str(source), write=True))
        == 0
    )
    output = capsys.readouterr().out
    assert "raw-secret" not in output
    payload = json.loads(output)
    assert payload["imported"][0]["enabled"] is False
    assert payload["imported"][0]["env"]["API_TOKEN"] == "<redacted>"
    assert payload["imported"][0]["env"]["SAFE_FLAG"] == "1"
    assert payload["imported"][0]["env_secret_refs"] == {
        "SERVICE_TOKEN": "secret://service/token"
    }
    assert payload["imported"][0]["package_metadata"]["version"] == "1.2.3"


def test_mcp_list_and_validate_config(tmp_path: Path, capsys) -> None:
    config = _write_json(
        tmp_path / "openminion.json",
        _runtime_config(
            {
                "name": "Fixture",
                "transport": "stdio",
                "command": ["python", "server.py"],
                "trusted": True,
            }
        ),
    )

    assert run_mcp(_args("list", config=str(config))) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["servers"][0]["name"] == "fixture"

    assert run_mcp(_args("validate", config=str(config))) == 0
    validated = json.loads(capsys.readouterr().out)
    assert validated == {"issues": [], "ok": True, "server_count": 1}


def test_mcp_validate_reports_untrusted_stdio(tmp_path: Path, capsys) -> None:
    config = _write_json(
        tmp_path / "openminion.json",
        _runtime_config(
            {
                "name": "Fixture",
                "transport": "stdio",
                "command": ["python", "server.py"],
                "stdio_sandbox": {"require_trust": True},
            }
        ),
    )

    assert run_mcp(_args("validate", config=str(config))) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["issues"][0]["reason_code"] == "mcp_stdio_untrusted"


def test_mcp_parser_rejects_unimplemented_lifecycle_commands() -> None:
    parser = build_parser()
    for command in ("restart", "logs"):
        with pytest.raises(SystemExit):
            parser.parse_args(["mcp", command])


def test_mcp_parser_accepts_stdio_and_http_add_forms() -> None:
    parser = build_parser()
    stdio = parser.parse_args(
        ["mcp", "add", "local", "--trusted", "--command", "python", "server.py"]
    )
    remote = parser.parse_args(
        ["mcp", "add", "remote", "--url", "https://mcp.example/mcp"]
    )

    assert stdio.command == ["python", "server.py"]
    assert stdio.trusted is True
    assert remote.url == "https://mcp.example/mcp"


def test_mcp_config_lifecycle_preserves_unrelated_config(
    tmp_path: Path, capsys
) -> None:
    config = _write_json(
        tmp_path / "openminion.json",
        {
            "runtime": {"env": {"KEEP": "yes"}, "mcp_servers": []},
            "agents": {"default": {"provider": "echo"}},
            "default_agent": "default",
        },
    )

    assert (
        run_mcp(
            _args(
                "add",
                config=str(config),
                name="Local Fixture",
                command=["python", "server.py"],
                url="",
                cwd=str(tmp_path),
                trusted=True,
                disabled=False,
            )
        )
        == 0
    )
    added = json.loads(capsys.readouterr().out)
    assert added["applies"] == "next_startup"
    assert added["server"]["name"] == "local_fixture"
    persisted = load_config(str(config))
    assert persisted.runtime.env["KEEP"] == "yes"
    assert persisted.agents["default"].provider == "echo"

    assert run_mcp(_args("get", config=str(config), name="Local Fixture")) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["server"]["command"] == ["python", "server.py"]

    assert run_mcp(_args("disable", config=str(config), name="local_fixture")) == 0
    assert json.loads(capsys.readouterr().out)["server"]["enabled"] is False
    assert load_config(str(config)).runtime.mcp_servers[0].enabled is False

    assert run_mcp(_args("enable", config=str(config), name="local_fixture")) == 0
    assert json.loads(capsys.readouterr().out)["server"]["enabled"] is True
    assert load_config(str(config)).runtime.mcp_servers[0].enabled is True

    assert run_mcp(_args("remove", config=str(config), name="local_fixture")) == 0
    assert json.loads(capsys.readouterr().out) == {
        "applies": "next_startup",
        "ok": True,
        "removed": "local_fixture",
    }
    assert load_config(str(config)).runtime.mcp_servers == []


def test_mcp_add_http_and_duplicate_or_missing_names(
    tmp_path: Path, capsys
) -> None:
    config = _write_json(
        tmp_path / "openminion.json",
        {
            "runtime": {"mcp_servers": []},
            "agents": {"default": {"provider": "echo"}},
            "default_agent": "default",
        },
    )

    assert (
        run_mcp(
            _args(
                "add",
                config=str(config),
                name="Remote",
                command=[],
                url="https://mcp.example/mcp",
                cwd="",
                trusted=False,
                disabled=False,
            )
        )
        == 0
    )
    added = json.loads(capsys.readouterr().out)
    assert added["server"]["transport"] == "streamable_http"

    with pytest.raises(RuntimeError, match="already exists"):
        run_mcp(
            _args(
                "add",
                config=str(config),
                name="Remote",
                command=[],
                url="https://other.example/mcp",
                cwd="",
                trusted=False,
                disabled=False,
            )
        )
    with pytest.raises(RuntimeError, match="not found"):
        run_mcp(_args("get", config=str(config), name="missing"))

    assert run_mcp(_args("get", config=str(config), name="remote")) == 0
    assert json.loads(capsys.readouterr().out)["server"]["url"] == (
        "https://mcp.example/mcp"
    )


def test_mcp_get_redacts_configured_secrets(tmp_path: Path, capsys) -> None:
    config = _write_json(
        tmp_path / "openminion.json",
        _runtime_config(
            {
                "name": "remote",
                "transport": "streamable_http",
                "url": "https://mcp.example/mcp",
                "authorization": {
                    "mode": "bearer",
                    "bearer_token": "private-token",
                },
                "env": {"API_TOKEN": "private-env"},
            }
        ),
    )

    assert run_mcp(_args("get", config=str(config), name="remote")) == 0
    output = capsys.readouterr().out
    assert "private-token" not in output
    assert "private-env" not in output
    payload = json.loads(output)
    assert payload["server"]["authorization"]["bearer_token"] == "<redacted>"
    assert payload["server"]["env"]["API_TOKEN"] == "<redacted>"


class _FakeFleet:
    failed_servers: dict = {}

    def discover_tools(self, *, parallel: bool = False):
        del parallel
        return [_ListedTool(server_name="fixture", remote_name="echo")]

    def discover_prompts(self, *, parallel: bool = False):
        del parallel
        return [_ListedPrompt(server_name="fixture", remote_name="daily")]

    def discover_resources(self, *, parallel: bool = False):
        del parallel
        return [_ListedResource(server_name="fixture", resource_uri="file:///one")]

    def discover_resource_templates(self, *, parallel: bool = False):
        del parallel
        return [
            _ListedResourceTemplate(
                server_name="fixture", uri_template="file:///{path}"
            )
        ]

    def server_status_snapshot(self):
        return {
            "fixture": {
                "protocol_version": "2026-07-28",
                "server_info": {"name": "fixture", "version": "1.0.0"},
            }
        }

    def get_prompt(self, **kwargs):
        return {"description": kwargs["remote_name"]}

    def read_resource(self, **kwargs):
        return {"contents": [{"uri": kwargs["resource_uri"], "text": "hello"}]}

    def close(self) -> None:
        return None


@dataclass
class _ListedTool:
    server_name: str
    remote_name: str


@dataclass
class _ListedPrompt:
    server_name: str
    remote_name: str


@dataclass
class _ListedResource:
    server_name: str
    resource_uri: str


@dataclass
class _ListedResourceTemplate:
    server_name: str
    uri_template: str


def test_mcp_browse_prompt_and_resource_commands(monkeypatch, capsys) -> None:
    fleet = _FakeFleet()
    monkeypatch.setattr(
        "openminion.cli.commands.mcp._configured_manager", lambda _args: fleet
    )

    assert run_mcp(_args("browse", name="")) == 0
    browse = json.loads(capsys.readouterr().out)
    assert browse["tools"][0]["remote_name"] == "echo"

    assert (
        run_mcp(_args("prompt", name="fixture", prompt_name="daily", arguments="{}"))
        == 0
    )
    prompt = json.loads(capsys.readouterr().out)
    assert prompt["result"]["description"] == "daily"

    assert run_mcp(_args("resource", name="fixture", uri="ui://card")) == 0
    resource = json.loads(capsys.readouterr().out)
    assert resource["text_fallback"] is True
    assert resource["result"]["contents"][0]["text"] == "hello"


def test_mcp_test_reports_full_diagnostics(monkeypatch, capsys) -> None:
    fleet = _FakeFleet()
    monkeypatch.setattr(
        "openminion.cli.commands.mcp._configured_manager", lambda _args: fleet
    )

    assert run_mcp(_args("test", name="")) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["servers"]["fixture"] == {
        "identity": {"name": "fixture", "version": "1.0.0"},
        "protocol": "2026-07-28",
    }
    assert payload["tools"][0]["remote_name"] == "echo"
    assert payload["prompts"][0]["remote_name"] == "daily"
    assert payload["resources"][0]["resource_uri"] == "file:///one"
    assert payload["resource_templates"][0]["uri_template"] == "file:///{path}"
    assert payload["failed_servers"] == {}


def test_mcp_test_reports_failed_server(monkeypatch, capsys) -> None:
    fleet = _FakeFleet()
    fleet.failed_servers = {
        "fixture": SimpleNamespace(
            reason_code="mcp_server_unavailable",
            message="fixture stopped",
        )
    }
    monkeypatch.setattr(
        "openminion.cli.commands.mcp._configured_manager", lambda _args: fleet
    )

    assert run_mcp(_args("test", name="fixture")) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["failed_servers"]["fixture"] == {
        "message": "fixture stopped",
        "reason_code": "mcp_server_unavailable",
    }


class _RegistryResponse:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self) -> bytes:
        return json.dumps(
            {
                "servers": [
                    {"name": "io.modelcontextprotocol/filesystem", "version": "1.0.0"}
                ]
            }
        ).encode()


def test_mcp_registry_search_is_read_only(monkeypatch, capsys) -> None:
    requests = []

    def open_registry(request, *, timeout):
        requests.append((request, timeout))
        return _RegistryResponse()

    monkeypatch.setattr(
        "openminion.cli.commands.mcp.urllib_request.urlopen", open_registry
    )

    assert run_mcp(_args("registry-search", query="filesystem", limit=5)) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["servers"][0]["name"] == "io.modelcontextprotocol/filesystem"
    request, timeout = requests[0]
    assert request.get_method() == "GET"
    assert timeout == 10.0
    assert urllib_parse.parse_qs(urllib_parse.urlparse(request.full_url).query) == {
        "search": ["filesystem"],
        "limit": ["5"],
    }


def _oauth_server() -> MCPServerConfig:
    return MCPServerConfig(
        name="Remote",
        transport="streamable_http",
        url="https://mcp.example/mcp",
        authorization=MCPAuthorizationConfig(
            mode="oauth_pkce",
            client_id="https://client.example/openminion.json",
            authorization_endpoint="https://auth.example/authorize",
            token_endpoint="https://auth.example/token",
            redirect_uri="http://127.0.0.1/callback",
            access_token_ref="remote-access",
            refresh_token_ref="remote-refresh",
        ),
    )


def test_mcp_login_starts_cimd_pkce_flow(monkeypatch, capsys) -> None:
    server = _oauth_server()
    config_manager = SimpleNamespace(
        base_config=SimpleNamespace(runtime=SimpleNamespace(mcp_servers=[server]))
    )
    monkeypatch.setattr(
        "openminion.base.config.manager.ConfigManager.load",
        lambda _path: config_manager,
    )
    monkeypatch.setattr(
        "openminion.tools.mcp.auth.discover_oauth_metadata",
        lambda _config, **_kwargs: MCPOAuthMetadata(
            authorization_endpoint="https://auth.example/authorize",
            token_endpoint="https://auth.example/token",
            issuer="https://auth.example",
            code_challenge_methods_supported=("S256",),
            client_id_metadata_document_supported=True,
            authorization_response_iss_parameter_supported=True,
        ),
    )

    assert run_mcp(_args("login", name="remote", code="", verifier="", issuer="")) == 0
    payload = json.loads(capsys.readouterr().out)
    query = urllib_parse.parse_qs(
        urllib_parse.urlparse(payload["authorization_url"]).query
    )
    assert payload["client_registration"] == "cimd"
    assert payload["expected_issuer"] == "https://auth.example"
    assert payload["issuer_required"] is True
    assert query["resource"] == ["https://mcp.example/mcp"]
    assert query["code_challenge_method"] == ["S256"]


class _FakeSecretService:
    def __init__(self) -> None:
        self.values = {}
        self.closed = False

    async def set_secret(self, name, value, *, namespace):
        self.values[(namespace, name)] = value

    def close_sync(self) -> None:
        self.closed = True


def test_mcp_login_exchanges_and_redacts_tokens(monkeypatch, capsys) -> None:
    server = _oauth_server()
    config_manager = SimpleNamespace(
        base_config=SimpleNamespace(runtime=SimpleNamespace(mcp_servers=[server])),
        data_root=Path("/tmp/openminion-test"),
        env=object(),
    )
    secrets = _FakeSecretService()
    monkeypatch.setattr(
        "openminion.base.config.manager.ConfigManager.load",
        lambda _path: config_manager,
    )
    monkeypatch.setattr(
        "openminion.tools.mcp.auth.discover_oauth_metadata",
        lambda _config, **_kwargs: MCPOAuthMetadata(
            authorization_endpoint="https://auth.example/authorize",
            token_endpoint="https://auth.example/token",
            issuer="https://auth.example",
        ),
    )
    monkeypatch.setattr(
        "openminion.tools.mcp.auth.exchange_authorization_code",
        lambda **_kwargs: MCPOAuthTokenState(
            access_token="access-secret",
            refresh_token="refresh-secret",
        ),
    )
    monkeypatch.setattr(
        "openminion.modules.secret.factory.build_secret_service",
        lambda **_kwargs: secrets,
    )

    assert (
        run_mcp(
            _args(
                "login",
                name="remote",
                code="code",
                verifier="verifier",
                issuer="",
                expected_issuer="https://auth.example",
                expected_state="state",
                state="state",
            )
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "access-secret" not in output
    assert "refresh-secret" not in output
    assert secrets.values == {
        ("mcp", "remote-access"): "access-secret",
        ("mcp", "remote-access.issuer"): "https://auth.example",
        ("mcp", "remote-refresh"): "refresh-secret",
        ("mcp", "remote-refresh.issuer"): "https://auth.example",
    }
    assert secrets.closed is True
