from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
import json
from pathlib import Path
from types import SimpleNamespace
from urllib import parse as urllib_parse

import pytest

from openminion.base.config.core import OpenMinionConfig
from openminion.base.config.io import load_config, save_config
from openminion.base.config.mcp import MCPAuthorizationConfig, MCPServerConfig
from openminion.cli.commands.mcp import run_mcp
from openminion.cli.parser.base import build_parser
from openminion.tools.mcp.auth import (
    MCPOAuthMetadata,
    MCPOAuthTokenState,
    MCPOAuthTransaction,
    SecretServiceMCPTokenStore,
    store_oauth_transaction,
)
from openminion.tools.mcp.oauth_callback import MCPOAuthCallbackResult


def _args(mcp_command: str, **kwargs):
    payload = {"mcp_command": mcp_command, "config": kwargs.pop("config", None)}
    payload.update(kwargs)
    return argparse.Namespace(**payload)


def _write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
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
                    "trusted": True,
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
    assert payload["config_path"] == str(config.resolve())
    assert payload["imported"][0]["enabled"] is False
    assert payload["imported"][0]["env"]["API_TOKEN"] == "<redacted>"
    assert payload["imported"][0]["env"]["SAFE_FLAG"] == "<redacted>"
    assert payload["imported"][0]["env_secret_refs"] == {
        "SERVICE_TOKEN": "secret://service/token"
    }
    assert payload["imported"][0]["package_metadata"]["version"] == "1.2.3"
    assert payload["imported"][0]["trusted"] is False
    assert load_config(str(config)).runtime.mcp_servers[0].trusted is False


def test_mcp_import_missing_source_fails_with_structured_error(
    tmp_path: Path, capsys
) -> None:
    missing = tmp_path / "nonexistent.json"
    config = tmp_path / "openminion.json"

    assert (
        run_mcp(_args("import", config=str(config), source=str(missing), write=True))
        == 1
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "SOURCE_NOT_FOUND"
    assert payload["error"]["details"]["path"] == str(missing)
    assert not config.exists()


def test_mcp_import_directory_source_fails_with_structured_error(
    tmp_path: Path, capsys
) -> None:
    directory = tmp_path / "somedir"
    directory.mkdir()
    config = tmp_path / "openminion.json"

    assert (
        run_mcp(_args("import", config=str(config), source=str(directory), write=True))
        == 1
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "SOURCE_READ_ERROR"
    assert payload["error"]["details"]["path"] == str(directory)
    assert not config.exists()


def test_mcp_import_malformed_json_fails_with_structured_error(
    tmp_path: Path, capsys
) -> None:
    broken = tmp_path / "broken.json"
    broken.write_text('{"mcpServers": {', encoding="utf-8")
    config = tmp_path / "openminion.json"

    assert (
        run_mcp(_args("import", config=str(config), source=str(broken), write=True))
        == 1
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "INVALID_SOURCE"
    assert payload["error"]["details"]["path"] == str(broken)
    assert not config.exists()


def test_mcp_import_whitespace_bearing_path(tmp_path: Path, capsys) -> None:
    spaced = tmp_path / "spaced file.json "
    spaced.write_text('{"mcpServers": {}}', encoding="utf-8")
    assert run_mcp(_args("import", source=str(spaced))) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["imported"] == []


def test_mcp_import_read_decode_error(tmp_path: Path, capsys) -> None:
    bad_utf8 = tmp_path / "bad_utf8.json"
    bad_utf8.write_bytes(b"\x80abc")
    assert run_mcp(_args("import", source=str(bad_utf8))) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "SOURCE_READ_ERROR"
    assert payload["error"]["details"]["path"] == str(bad_utf8)


def test_mcp_import_failure_leaves_existing_config_unchanged(
    tmp_path: Path, capsys
) -> None:
    config = tmp_path / "openminion.json"
    config_content = '{"version": 1, "runtime": {"mcp_servers": []}}\n'
    config.write_text(config_content, encoding="utf-8")
    missing = tmp_path / "missing.json"

    assert (
        run_mcp(_args("import", config=str(config), source=str(missing), write=True))
        == 1
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "SOURCE_NOT_FOUND"
    assert config.read_text(encoding="utf-8") == config_content


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


def test_mcp_default_config_matches_sdk_project_scope(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    project = tmp_path / "project"
    fake_home = tmp_path / "home"
    _write_json(
        project / ".openminion" / "agents.json",
        _runtime_config(
            {
                "name": "Project Marker",
                "transport": "stdio",
                "command": ["python", "project.py"],
                "trusted": True,
            }
        ),
    )
    _write_json(
        fake_home / ".openminion" / "agents.json",
        _runtime_config(
            {
                "name": "Home Marker",
                "transport": "stdio",
                "command": ["python", "home.py"],
                "trusted": True,
            }
        ),
    )
    monkeypatch.chdir(project)
    monkeypatch.delenv("OPENMINION_HOME", raising=False)
    monkeypatch.delenv("OPENMINION_CONFIG_ROOT", raising=False)
    monkeypatch.delenv("OPENMINION_DATA_ROOT", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    assert run_mcp(_args("list")) == 0

    listed = json.loads(capsys.readouterr().out)
    assert [server["name"] for server in listed["servers"]] == ["project_marker"]


def test_mcp_explicit_config_write_does_not_mutate_default_config(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    project = tmp_path / "project"
    default_path = _write_json(
        project / ".openminion" / "agents.json",
        _runtime_config(
            {
                "name": "Default Marker",
                "transport": "stdio",
                "command": ["python", "default.py"],
                "trusted": True,
            }
        ),
    )
    explicit_path = _write_json(
        tmp_path / "explicit.json",
        {
            "runtime": {"mcp_servers": []},
            "agents": {"default": {"provider": "echo"}},
            "default_agent": "default",
        },
    )
    before = default_path.read_bytes()
    monkeypatch.chdir(project)
    monkeypatch.delenv("OPENMINION_HOME", raising=False)
    monkeypatch.delenv("OPENMINION_CONFIG_ROOT", raising=False)
    monkeypatch.delenv("OPENMINION_DATA_ROOT", raising=False)

    assert (
        run_mcp(
            _args(
                "add",
                config=str(explicit_path),
                name="Explicit Marker",
                command=["python", "explicit.py"],
                url="",
                cwd="",
                trusted=True,
                disabled=False,
            )
        )
        == 0
    )
    capsys.readouterr()

    assert default_path.read_bytes() == before
    assert [
        server.name for server in load_config(str(explicit_path)).runtime.mcp_servers
    ] == ["explicit_marker"]


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


def test_mcp_validate_allows_untrusted_stdio_when_trust_is_not_required(
    tmp_path: Path, capsys
) -> None:
    config = _write_json(
        tmp_path / "openminion.json",
        _runtime_config(
            {
                "name": "Fixture",
                "transport": "stdio",
                "command": ["python", "server.py"],
                "stdio_sandbox": {"require_trust": False},
            }
        ),
    )

    assert run_mcp(_args("validate", config=str(config))) == 0
    assert json.loads(capsys.readouterr().out)["issues"] == []


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


def test_mcp_parser_accepts_complete_add_configuration() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "mcp",
            "add",
            "local",
            "--startup-timeout",
            "20",
            "--request-timeout",
            "45",
            "--stderr-buffer-bytes",
            "32768",
            "--env",
            "MODE=first",
            "--env",
            "MODE=last",
            "--env-secret",
            "TOKEN=secret://mcp/token",
            "--auth",
            "oauth-pkce",
            "--client-id",
            "openminion-cli",
            "--client-issuer",
            "https://auth.example",
            "--authorization-metadata-url",
            "https://auth.example/.well-known/oauth-authorization-server",
            "--authorization-endpoint",
            "https://auth.example/authorize",
            "--token-endpoint",
            "https://auth.example/token",
            "--registration-endpoint",
            "https://auth.example/register",
            "--revocation-endpoint",
            "https://auth.example/revoke",
            "--redirect-uri",
            "http://127.0.0.1/callback",
            "--scope",
            "mcp.read mcp.write",
            "--access-token-ref",
            "mcp-access",
            "--refresh-token-ref",
            "mcp-refresh",
            "--approval-mode",
            "matching",
            "--approval-tool",
            "files.*",
            "--approval-risk",
            "high",
            "--no-sandbox-require-trust",
            "--sandbox-cwd",
            "/workspace",
            "--sandbox-env",
            "MODE",
            "--sandbox-inherit-env",
            "PATH",
            "--sandbox-package-name",
            "fixture",
            "--sandbox-package-version",
            "1.2.3",
            "--sandbox-trust-reason",
            "operator reviewed",
            "--risk-override",
            '{"pattern":"files.write","min_scope":"POWER_USER","dangerous":true}',
            "--trusted",
            "--required",
            "--command",
            "python",
            "server.py",
        ]
    )

    assert args.env == [("MODE", "first"), ("MODE", "last")]
    assert args.env_secret == [("TOKEN", "secret://mcp/token")]
    assert args.auth == "oauth-pkce"
    assert args.client_issuer == "https://auth.example"
    assert args.sandbox_require_trust is False
    assert args.risk_override[0].pattern == "files.write"
    assert args.command == ["python", "server.py"]


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("--env", "MISSING_SEPARATOR"),
        ("--env-secret", "TOKEN="),
        ("--risk-override", "not-json"),
        ("--risk-override", "[]"),
    ],
)
def test_mcp_parser_rejects_malformed_add_values(option: str, value: str) -> None:
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(
            ["mcp", "add", "local", option, value, "--command", "fixture"]
        )


@pytest.mark.parametrize(
    "option",
    (
        "--bearer-token",
        "--access-token",
        "--client-secret",
        "--client-secret-ref",
        "--package-metadata",
    ),
)
def test_mcp_parser_rejects_plaintext_and_provenance_add_flags(option: str) -> None:
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(
            ["mcp", "add", "local", option, "private", "--command", "fixture"]
        )


def test_mcp_add_round_trips_complete_stdio_configuration(
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
                name="Local Fixture",
                command=["python", "server.py"],
                url="",
                cwd=str(tmp_path),
                trusted=True,
                required=True,
                disabled=False,
                startup_timeout=20.0,
                request_timeout=45.0,
                stderr_buffer_bytes=32768,
                env=[("MODE", "first"), ("MODE", "last")],
                env_secret=[("TOKEN", "secret://mcp/token")],
                auth="oauth-pkce",
                bearer_token_ref="",
                client_id="openminion-cli",
                client_issuer="",
                authorization_metadata_url=(
                    "https://auth.example/.well-known/oauth-authorization-server"
                ),
                authorization_endpoint="https://auth.example/authorize",
                token_endpoint="https://auth.example/token",
                registration_endpoint="https://auth.example/register",
                revocation_endpoint="https://auth.example/revoke",
                redirect_uri="http://127.0.0.1/callback",
                scope="mcp.read mcp.write",
                access_token_ref="mcp-access",
                refresh_token_ref="mcp-refresh",
                approval_mode="matching",
                approval_tool=["files.*"],
                approval_risk=["high"],
                sandbox_require_trust=False,
                sandbox_cwd=[str(tmp_path)],
                sandbox_env=["MODE"],
                sandbox_inherit_env=["PATH"],
                sandbox_package_name="fixture",
                sandbox_package_version="1.2.3",
                sandbox_trust_reason="operator reviewed",
                risk_override=[
                    {
                        "pattern": "files.write",
                        "min_scope": "POWER_USER",
                        "dangerous": True,
                        "idempotent": False,
                    }
                ],
            )
        )
        == 0
    )

    output = json.loads(capsys.readouterr().out)
    assert output["server"]["startup_timeout_seconds"] == 20.0
    assert output["server"]["request_timeout_seconds"] == 45.0
    assert output["server"]["stderr_buffer_bytes"] == 32768
    assert output["server"]["env"] == {"MODE": "<redacted>"}
    assert output["server"]["approval"] == {
        "mode": "matching",
        "risk_levels": ["high"],
        "tool_patterns": ["files.*"],
    }
    assert output["server"]["stdio_sandbox"] == {
        "cwd_allowlist": [str(tmp_path)],
        "env_allowlist": ["MODE"],
        "inherit_env_allowlist": ["PATH"],
        "package_name": "fixture",
        "package_version": "1.2.3",
        "require_trust": False,
        "trust_reason": "operator reviewed",
    }
    assert output["server"]["tool_risk_overrides"] == [
        {
            "dangerous": True,
            "idempotent": False,
            "min_scope": "POWER_USER",
            "pattern": "files.write",
        }
    ]

    server = load_config(str(config)).runtime.mcp_servers[0]
    assert server.required is True
    assert server.env == {"MODE": "last"}
    assert server.env_secret_refs == {"TOKEN": "secret://mcp/token"}
    assert server.authorization.mode == "oauth_pkce"
    assert server.authorization.authorization_server_metadata_url == (
        "https://auth.example/.well-known/oauth-authorization-server"
    )
    assert server.authorization.authorization_endpoint == (
        "https://auth.example/authorize"
    )
    assert server.authorization.token_endpoint == "https://auth.example/token"
    assert server.authorization.registration_endpoint == (
        "https://auth.example/register"
    )
    assert server.authorization.revocation_endpoint == "https://auth.example/revoke"
    assert server.authorization.redirect_uri == "http://127.0.0.1/callback"
    assert server.authorization.scope == "mcp.read mcp.write"
    assert server.authorization.access_token_ref == "mcp-access"
    assert server.authorization.refresh_token_ref == "mcp-refresh"
    assert server.approval.tool_patterns == ["files.*"]
    assert server.stdio_sandbox.package_name == "fixture"
    assert server.tool_risk_overrides[0].pattern == "files.write"
    assert server.package_metadata.to_dict() == {
        "install_command": [],
        "origin": "",
        "trust_state": "",
        "version": "",
    }


def test_mcp_add_round_trips_bearer_reference(tmp_path: Path, capsys) -> None:
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
                name="Remote Fixture",
                command=[],
                url="https://mcp.example/mcp",
                cwd="",
                trusted=False,
                disabled=False,
                auth="bearer",
                bearer_token_ref="secret://mcp/bearer",
            )
        )
        == 0
    )

    output = json.loads(capsys.readouterr().out)
    assert output["server"]["authorization"] == {
        "bearer_token": "",
        "bearer_token_ref": "secret://mcp/bearer",
        "mode": "bearer",
    }
    authorization = load_config(str(config)).runtime.mcp_servers[0].authorization
    assert authorization.bearer_token == ""
    assert authorization.bearer_token_ref == "secret://mcp/bearer"


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
    assert added["config_path"] == str(config.resolve())
    assert added["server"]["name"] == "local_fixture"
    persisted = load_config(str(config))
    assert persisted.runtime.env["KEEP"] == "yes"
    assert persisted.agents["default"].provider == "echo"

    assert run_mcp(_args("get", config=str(config), name="Local Fixture")) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["server"]["command"] == ["python", "server.py"]

    assert run_mcp(_args("disable", config=str(config), name="local_fixture")) == 0
    disabled = json.loads(capsys.readouterr().out)
    assert disabled["server"]["enabled"] is False
    assert disabled["config_path"] == str(config.resolve())
    assert load_config(str(config)).runtime.mcp_servers[0].enabled is False

    assert run_mcp(_args("enable", config=str(config), name="local_fixture")) == 0
    enabled = json.loads(capsys.readouterr().out)
    assert enabled["server"]["enabled"] is True
    assert enabled["config_path"] == str(config.resolve())
    assert load_config(str(config)).runtime.mcp_servers[0].enabled is True

    assert run_mcp(_args("untrust", config=str(config), name="local_fixture")) == 0
    untrusted = json.loads(capsys.readouterr().out)
    assert untrusted["server"]["trusted"] is False
    assert untrusted["config_path"] == str(config.resolve())
    assert load_config(str(config)).runtime.mcp_servers[0].trusted is False

    assert run_mcp(_args("trust", config=str(config), name="local_fixture")) == 0
    trusted = json.loads(capsys.readouterr().out)
    assert trusted["server"]["trusted"] is True
    assert trusted["config_path"] == str(config.resolve())
    assert load_config(str(config)).runtime.mcp_servers[0].trusted is True

    assert run_mcp(_args("remove", config=str(config), name="local_fixture")) == 0
    assert json.loads(capsys.readouterr().out) == {
        "applies": "next_startup",
        "config_path": str(config.resolve()),
        "ok": True,
        "removed": "local_fixture",
    }
    assert load_config(str(config)).runtime.mcp_servers == []


def test_mcp_add_http_and_duplicate_or_missing_names(tmp_path: Path, capsys) -> None:
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
            details={"status_code": 503},
        )
    }
    monkeypatch.setattr(
        "openminion.cli.commands.mcp._configured_manager", lambda _args: fleet
    )

    assert run_mcp(_args("test", name="fixture")) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["failed_servers"]["fixture"] == {
        "details": {"status_code": 503},
        "message": "fixture stopped",
        "reason_code": "mcp_server_unavailable",
    }


@pytest.mark.parametrize("command", ("test", "browse"))
def test_mcp_discovery_commands_reject_unknown_server(
    command: str, tmp_path: Path
) -> None:
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

    with pytest.raises(RuntimeError, match="MCP server not found: missing"):
        run_mcp(_args(command, config=str(config), name="missing"))


@pytest.mark.parametrize("command", ("test", "browse"))
def test_mcp_discovery_commands_reject_disabled_server(
    command: str, tmp_path: Path
) -> None:
    config = _write_json(
        tmp_path / "openminion.json",
        _runtime_config(
            {
                "name": "Fixture",
                "enabled": False,
                "transport": "stdio",
                "command": ["python", "server.py"],
            }
        ),
    )

    with pytest.raises(RuntimeError, match="MCP server is disabled: fixture"):
        run_mcp(_args(command, config=str(config), name="fixture"))


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
    base_server = _oauth_server()
    server = replace(
        base_server,
        authorization=replace(
            base_server.authorization,
            client_issuer="https://auth.example",
        ),
    )
    secrets = _FakeSecretService()
    config_manager = SimpleNamespace(
        base_config=SimpleNamespace(runtime=SimpleNamespace(mcp_servers=[server])),
        data_root=Path("/tmp/openminion-test"),
        env=object(),
    )
    monkeypatch.setattr(
        "openminion.cli.config.load_cli_manager_from_args",
        lambda _args: config_manager,
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
    monkeypatch.setattr(
        "openminion.tools.mcp.transport.probe_oauth_challenge",
        lambda _server: {
            "scheme": "Bearer",
            "resource_metadata": "https://mcp.example/auth-resource",
            "scope": "mcp.read",
        },
    )
    monkeypatch.setattr(
        "openminion.modules.secret.factory.build_secret_service",
        lambda **_kwargs: secrets,
    )

    assert run_mcp(_args("login", name="remote", code="", issuer="", manual=True)) == 0
    payload = json.loads(capsys.readouterr().out)
    query = urllib_parse.parse_qs(
        urllib_parse.urlparse(payload["authorization_url"]).query
    )
    assert payload["client_registration"] == "cimd"
    assert server.authorization.client_issuer == "https://auth.example"
    assert payload["issuer_required"] is True
    assert "code_verifier" not in payload
    assert query["resource"] == ["https://mcp.example/mcp"]
    assert query["scope"] == ["mcp.read"]
    assert query["code_challenge_method"] == ["S256"]
    transaction_payload = next(iter(secrets.values.values()))
    assert "private-verifier" not in json.dumps(payload)
    assert "code_verifier" in transaction_payload
    assert "https://mcp.example/auth-resource" in transaction_payload


def test_mcp_login_automates_dcr_loopback_and_persists_public_client(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    server = MCPServerConfig(
        name="Remote",
        transport="streamable_http",
        url="https://mcp.example/mcp",
        authorization=MCPAuthorizationConfig(
            mode="oauth_pkce",
            access_token_ref="remote-access",
        ),
    )
    config = OpenMinionConfig()
    config.runtime.mcp_servers = [server]
    config_path = tmp_path / "config.json"
    save_config(config, str(config_path), home_root=tmp_path)
    secrets = _FakeSecretService()
    config_manager = SimpleNamespace(
        base_config=config,
        config_path=config_path,
        home_root=tmp_path,
        data_root=tmp_path,
        env=object(),
    )
    metadata = MCPOAuthMetadata(
        authorization_endpoint="https://auth.example/authorize",
        token_endpoint="https://auth.example/token",
        registration_endpoint="https://auth.example/register",
        issuer="https://auth.example",
        code_challenge_methods_supported=("S256",),
    )
    opened: list[str] = []

    class _Callback:
        redirect_uri = "http://127.0.0.1:43123/callback"

        def __init__(self, _redirect_uri: str = "") -> None:
            pass

        def wait(self, *, expected_state: str, timeout_seconds: float):
            assert expected_state
            assert timeout_seconds == 7
            return MCPOAuthCallbackResult(code="authorization-code")

        def close(self) -> None:
            pass

    monkeypatch.setattr(
        "openminion.cli.config.load_cli_manager_from_args",
        lambda _args: config_manager,
    )
    monkeypatch.setattr(
        "openminion.modules.secret.factory.build_secret_service",
        lambda **_kwargs: secrets,
    )
    monkeypatch.setattr(
        "openminion.tools.mcp.transport.probe_oauth_challenge",
        lambda _server: {
            "resource_metadata": "https://mcp.example/auth-resource",
            "scope": "mcp.read",
        },
    )
    monkeypatch.setattr(
        "openminion.tools.mcp.auth.discover_oauth_metadata",
        lambda _config, **_kwargs: metadata,
    )
    monkeypatch.setattr(
        "openminion.tools.mcp.auth.register_oauth_client",
        lambda **_kwargs: {
            "client_id": "generated-public-client",
            "token_endpoint_auth_method": "none",
        },
    )
    monkeypatch.setattr(
        "openminion.tools.mcp.auth.exchange_authorization_code",
        lambda **_kwargs: MCPOAuthTokenState(access_token="access-secret"),
    )
    monkeypatch.setattr(
        "openminion.tools.mcp.oauth_callback.MCPOAuthCallbackServer",
        _Callback,
    )
    monkeypatch.setattr(
        "openminion.cli.commands.mcp.webbrowser.open",
        lambda url: opened.append(url) or False,
    )

    assert (
        run_mcp(
            _args(
                "login",
                name="remote",
                code="",
                issuer="",
                manual=False,
                callback_timeout=7,
            )
        )
        == 0
    )

    persisted = load_config(str(config_path), home_root=tmp_path)
    authorization = persisted.runtime.mcp_servers[0].authorization
    assert authorization.client_id == "generated-public-client"
    assert authorization.client_issuer == "https://auth.example"
    assert authorization.redirect_uri == ""
    assert opened and "generated-public-client" in opened[0]
    output = capsys.readouterr().out
    assert "authorization_url" in output
    assert "authorization-code" not in output


class _FakeSecretService:
    def __init__(self) -> None:
        self.values = {}
        self.closed = False

    async def set_secret(self, name, value, *, namespace):
        self.values[(namespace, name)] = value

    def get_secret_sync(self, name, *, namespace):
        return self.values.get((namespace, name), "")

    def consume_secret_sync(self, name, *, namespace):
        key = (namespace, name)
        if key not in self.values:
            from openminion.modules.secret.schemas import SecretNotFoundError

            raise SecretNotFoundError(name)
        return self.values.pop(key)

    async def delete_secret(self, name, *, namespace):
        self.values.pop((namespace, name), None)

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
    discovery: list[dict[str, object]] = []
    exchange: list[dict[str, object]] = []
    monkeypatch.setattr(
        "openminion.cli.config.load_cli_manager_from_args",
        lambda _args: config_manager,
    )

    def discover(_config, **kwargs):
        discovery.append(kwargs)
        return MCPOAuthMetadata(
            authorization_endpoint="https://auth.example/authorize",
            token_endpoint="https://auth.example/token",
            issuer="https://auth.example",
        )

    def exchange_code(**kwargs):
        exchange.append(kwargs)
        return MCPOAuthTokenState(
            access_token="access-secret",
            refresh_token="refresh-secret",
        )

    monkeypatch.setattr("openminion.tools.mcp.auth.discover_oauth_metadata", discover)
    monkeypatch.setattr(
        "openminion.tools.mcp.auth.exchange_authorization_code", exchange_code
    )
    monkeypatch.setattr(
        "openminion.modules.secret.factory.build_secret_service",
        lambda **_kwargs: secrets,
    )
    store_oauth_transaction(
        SecretServiceMCPTokenStore(secrets),
        MCPOAuthTransaction(
            server_name="remote",
            resource="https://mcp.example/mcp",
            redirect_uri="http://127.0.0.1/callback",
            state="state",
            code_verifier="verifier",
            issuer="https://auth.example",
            issuer_required=False,
            authorization_endpoint="https://auth.example/authorize",
            token_endpoint="https://auth.example/token",
            expires_at=10**12,
            resource_metadata_url="https://mcp.example/auth-resource",
            scope="mcp.read",
        ),
    )

    assert (
        run_mcp(
            _args(
                "login",
                name="remote",
                code="code",
                issuer="",
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
    assert discovery[0]["resource_metadata_url"] == (
        "https://mcp.example/auth-resource"
    )
    assert exchange[0]["scope"] == "mcp.read"


def test_mcp_login_rejects_metadata_drift_before_token_exchange(monkeypatch) -> None:
    server = _oauth_server()
    config_manager = SimpleNamespace(
        base_config=SimpleNamespace(runtime=SimpleNamespace(mcp_servers=[server])),
        data_root=Path("/tmp/openminion-test"),
        env=object(),
    )
    secrets = _FakeSecretService()
    exchanged = {"called": False}
    monkeypatch.setattr(
        "openminion.cli.config.load_cli_manager_from_args",
        lambda _args: config_manager,
    )
    monkeypatch.setattr(
        "openminion.tools.mcp.auth.discover_oauth_metadata",
        lambda _config, **_kwargs: MCPOAuthMetadata(
            authorization_endpoint="https://auth.example/authorize",
            token_endpoint="https://auth.example/new-token",
            issuer="https://auth.example",
        ),
    )

    def _exchange(**_kwargs):
        exchanged["called"] = True
        return MCPOAuthTokenState(access_token="should-not-exist")

    monkeypatch.setattr(
        "openminion.tools.mcp.auth.exchange_authorization_code",
        _exchange,
    )
    monkeypatch.setattr(
        "openminion.modules.secret.factory.build_secret_service",
        lambda **_kwargs: secrets,
    )
    store_oauth_transaction(
        SecretServiceMCPTokenStore(secrets),
        MCPOAuthTransaction(
            server_name="remote",
            resource="https://mcp.example/mcp",
            redirect_uri="http://127.0.0.1/callback",
            state="state",
            code_verifier="verifier",
            issuer="https://auth.example",
            issuer_required=False,
            authorization_endpoint="https://auth.example/authorize",
            token_endpoint="https://auth.example/token",
            expires_at=10**12,
        ),
    )

    with pytest.raises(RuntimeError, match="metadata changed"):
        run_mcp(
            _args(
                "login",
                name="remote",
                code="code",
                issuer="",
                state="state",
            )
        )

    assert exchanged["called"] is False
    assert ("mcp", "oauth_transaction.remote.state") not in secrets.values
    assert secrets.closed is True


def test_mcp_login_consumes_state_when_metadata_discovery_fails(monkeypatch) -> None:
    server = _oauth_server()
    secrets = _FakeSecretService()
    config_manager = SimpleNamespace(
        base_config=SimpleNamespace(runtime=SimpleNamespace(mcp_servers=[server])),
        data_root=Path("/tmp/openminion-test"),
        env=object(),
    )
    monkeypatch.setattr(
        "openminion.cli.config.load_cli_manager_from_args",
        lambda _args: config_manager,
    )
    monkeypatch.setattr(
        "openminion.modules.secret.factory.build_secret_service",
        lambda **_kwargs: secrets,
    )
    monkeypatch.setattr(
        "openminion.tools.mcp.auth.discover_oauth_metadata",
        lambda _config, **_kwargs: (_ for _ in ()).throw(ValueError("offline")),
    )
    store_oauth_transaction(
        SecretServiceMCPTokenStore(secrets),
        MCPOAuthTransaction(
            server_name="remote",
            resource=server.url,
            redirect_uri=server.authorization.redirect_uri,
            state="state",
            code_verifier="verifier",
            issuer="https://auth.example",
            issuer_required=False,
            authorization_endpoint="https://auth.example/authorize",
            token_endpoint="https://auth.example/token",
            expires_at=10**12,
        ),
    )

    with pytest.raises(ValueError, match="offline"):
        run_mcp(
            _args(
                "login",
                name="remote",
                code="code",
                issuer="",
                state="state",
            )
        )

    assert ("mcp", "oauth_transaction.remote.state") not in secrets.values
    assert secrets.closed is True
