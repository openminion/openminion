from __future__ import annotations

import sys
import time
from pathlib import Path

from openminion.base.config.mcp import MCPServerConfig
from openminion.base.config.runtime import RuntimeConfig
from openminion.modules.tool.bootstrap import build_runtime_bootstrap


FIXTURE_SERVER_PATH = (
    Path(__file__).resolve().parent / "fixtures" / "mock_mcp_server.py"
)


def _runtime_config() -> RuntimeConfig:
    return RuntimeConfig(
        mcp_servers=[
            MCPServerConfig(
                name="FastA",
                transport="stdio",
                command=[sys.executable, str(FIXTURE_SERVER_PATH)],
                request_timeout_seconds=1.0,
                startup_timeout_seconds=1.0,
            ),
            MCPServerConfig(
                name="FastB",
                transport="stdio",
                command=[sys.executable, str(FIXTURE_SERVER_PATH)],
                request_timeout_seconds=1.0,
                startup_timeout_seconds=1.0,
            ),
            MCPServerConfig(
                name="Slow",
                transport="stdio",
                command=[sys.executable, str(FIXTURE_SERVER_PATH)],
                env={"MOCK_MCP_TOOLS_LIST_DELAY_SECONDS": "2.0"},
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
    started = time.monotonic()
    bootstrap = build_runtime_bootstrap(config=_runtime_config(), strict=True)
    elapsed = time.monotonic() - started
    try:
        assert elapsed < 2.5
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
