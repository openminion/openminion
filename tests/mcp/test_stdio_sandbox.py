from __future__ import annotations

from pathlib import Path

import pytest

from openminion.base.config.mcp import (
    MCPServerConfig,
    MCPStdioSandboxConfig,
    resolve_mcp_server_env,
)
from openminion.cli.tui.mcp_status import MCPServerStatusRow, render_mcp_status_report
from openminion.tools.mcp.transport import MCPServerUnavailableError, StdioMCPTransport


def _server(**kwargs) -> MCPServerConfig:
    return MCPServerConfig(
        name="Fixture",
        command=["python", "-m", "fixture"],
        **kwargs,
    )


def test_stdio_sandbox_blocks_untrusted_server_when_trust_required() -> None:
    transport = StdioMCPTransport(
        _server(stdio_sandbox=MCPStdioSandboxConfig(require_trust=True))
    )

    with pytest.raises(MCPServerUnavailableError) as excinfo:
        transport.start()

    assert excinfo.value.reason_code == "mcp_stdio_untrusted"


def test_stdio_sandbox_blocks_cwd_outside_allowlist(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    denied = tmp_path / "denied"
    allowed.mkdir()
    denied.mkdir()
    transport = StdioMCPTransport(
        _server(
            trusted=True,
            cwd=str(denied),
            stdio_sandbox=MCPStdioSandboxConfig(cwd_allowlist=[str(allowed)]),
        )
    )

    with pytest.raises(MCPServerUnavailableError) as excinfo:
        transport.start()

    assert excinfo.value.reason_code == "mcp_stdio_cwd_denied"


def test_stdio_sandbox_env_allowlist_prevents_configured_env_leakage() -> None:
    transport = StdioMCPTransport(
        _server(
            env={"ALLOWED_TOKEN": "ok", "SECRET_TOKEN": "leak"},
            stdio_sandbox=MCPStdioSandboxConfig(env_allowlist=["ALLOWED_TOKEN"]),
        )
    )

    env = transport._build_stdio_env()  # noqa: SLF001

    assert env["ALLOWED_TOKEN"] == "ok"
    assert "SECRET_TOKEN" not in env


def test_mcp_env_secret_refs_resolve_without_stdout_secret_storage() -> None:
    server = _server(
        env={"SAFE_FLAG": "1"},
        env_secret_refs={"API_TOKEN": "secret://mcp/api-token"},
    )

    env = resolve_mcp_server_env(
        server,
        secret_resolver=lambda ref: {"secret://mcp/api-token": "resolved-token"}[ref],
    )

    assert env == {"API_TOKEN": "resolved-token", "SAFE_FLAG": "1"}


def test_mcp_env_interpolation_is_blocked_before_stdio_start() -> None:
    transport = StdioMCPTransport(_server(env={"API_TOKEN": "${TOKEN}"}))

    with pytest.raises(MCPServerUnavailableError) as excinfo:
        transport._build_stdio_env()  # noqa: SLF001

    assert excinfo.value.reason_code == "mcp_stdio_env_denied"


def test_mcp_status_renders_stdio_trust_and_sandbox_state() -> None:
    rendered = render_mcp_status_report(
        [
            MCPServerStatusRow(
                name="fixture",
                transport="stdio",
                status="configured",
                trust_state="trusted",
                sandbox_state="enforced",
            )
        ]
    )

    assert "security: trust=trusted sandbox=enforced" in rendered
