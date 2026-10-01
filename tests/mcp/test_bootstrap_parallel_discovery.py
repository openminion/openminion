from __future__ import annotations

import sys
from pathlib import Path

import pytest

from openminion.base.config.mcp import MCPServerConfig
from openminion.base.config.runtime import RuntimeConfig
from openminion.modules.tool.bootstrap import build_runtime_bootstrap
from openminion.modules.tool.errors import ToolRuntimeError


FIXTURE_SERVER_PATH = (
    Path(__file__).resolve().parent / "fixtures" / "mock_mcp_server.py"
)


def _runtime_config() -> RuntimeConfig:
    return RuntimeConfig(
        mcp_servers=[
            MCPServerConfig(
                name="FastA",
                transport="stdio",
                trusted=True,
                command=[sys.executable, str(FIXTURE_SERVER_PATH)],
                request_timeout_seconds=1.0,
                startup_timeout_seconds=1.0,
            ),
            MCPServerConfig(
                name="FastB",
                transport="stdio",
                trusted=True,
                command=[sys.executable, str(FIXTURE_SERVER_PATH)],
                request_timeout_seconds=1.0,
                startup_timeout_seconds=1.0,
            ),
            MCPServerConfig(
                name="Slow",
                transport="stdio",
                trusted=True,
                command=[sys.executable, str(FIXTURE_SERVER_PATH)],
                env={"MOCK_MCP_TOOLS_LIST_DELAY_SECONDS": "2.0"},
                stdio_sandbox={"env_allowlist": ["MOCK_MCP_TOOLS_LIST_DELAY_SECONDS"]},
                request_timeout_seconds=1.0,
                startup_timeout_seconds=1.0,
            ),
        ]
    )


def _close_bootstrap(bootstrap) -> None:
    manager = getattr(bootstrap, "mcp_manager", None)
    if manager is not None:
        manager.close()


def test_bootstrap_parallel_discovery_isolates_slow_server() -> None:
    bootstrap = build_runtime_bootstrap(config=_runtime_config(), strict=True)
    try:
        manager = bootstrap.mcp_manager
        assert manager is not None
        failed = manager.failed_servers
        assert "slow" in failed
        assert failed["slow"].reason_code == "mcp_timeout"

        tool_names = set(bootstrap.registry.list().keys())
        assert "mcp.fasta.echo_text" in tool_names
        assert "mcp.fastb.echo_text" in tool_names
        assert "mcp.slow.echo_text" not in tool_names

        mcp_records = [
            record
            for record in (bootstrap.bootstrap_records or [])
            if record.label == "MCP"
        ]
        assert len(mcp_records) == 1
        record_error = str(mcp_records[0].error or "")
        assert "failed_mcp_servers=slow:mcp_timeout" in record_error
    finally:
        _close_bootstrap(bootstrap)


def test_required_mcp_server_failure_aborts_strict_bootstrap() -> None:
    config = RuntimeConfig(
        mcp_servers=[
            MCPServerConfig(
                name="Required",
                transport="stdio",
                trusted=True,
                required=True,
                command=[sys.executable, "-c", "raise SystemExit(1)"],
                request_timeout_seconds=1.0,
                startup_timeout_seconds=1.0,
            )
        ]
    )

    with pytest.raises(ToolRuntimeError) as excinfo:
        build_runtime_bootstrap(config=config, strict=True)

    assert excinfo.value.code == "MCP_REQUIRED_SERVER_UNAVAILABLE"
    assert excinfo.value.details == {"failed_servers": ["required"]}


def test_disabled_required_mcp_server_is_not_started() -> None:
    config = RuntimeConfig(
        mcp_servers=[
            MCPServerConfig(
                name="Disabled",
                transport="stdio",
                enabled=False,
                required=True,
                command=[sys.executable, "-c", "raise SystemExit(1)"],
            )
        ]
    )

    bootstrap = build_runtime_bootstrap(config=config, strict=True)
    try:
        assert bootstrap.mcp_manager is None
        assert not any(
            record.label == "MCP" and record.status == "failed"
            for record in (bootstrap.bootstrap_records or [])
        )
    finally:
        _close_bootstrap(bootstrap)
