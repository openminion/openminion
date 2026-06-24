from __future__ import annotations

import sys
from pathlib import Path

import pytest

from openminion.api.queries.runtime_reports import _build_mcp_section
from openminion.base.config.mcp import MCPServerConfig
from openminion.base.config.runtime import RuntimeConfig
from openminion.cli.tui.mcp_status import MCPServerStatusRow, render_mcp_status_report
from openminion.modules.tool.bootstrap import build_runtime_bootstrap
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.tools.mcp.manager import MCPFleetManager, MCPServerSession


FIXTURE_SERVER_PATH = (
    Path(__file__).resolve().parent / "fixtures" / "mock_mcp_server.py"
)


def _runtime_config(
    *, stderr_buffer_bytes: int = 65536, stderr_banner: str = ""
) -> RuntimeConfig:
    env: dict[str, str] = {}
    if stderr_banner:
        env["MOCK_MCP_STDERR_BANNER"] = stderr_banner
    return RuntimeConfig(
        mcp_servers=[
            MCPServerConfig(
                name="Fixture",
                transport="stdio",
                command=[sys.executable, str(FIXTURE_SERVER_PATH)],
                env=env,
                request_timeout_seconds=5.0,
                startup_timeout_seconds=5.0,
                stderr_buffer_bytes=stderr_buffer_bytes,
            )
        ]
    )


def _close_bootstrap(bootstrap) -> None:
    manager = getattr(bootstrap, "mcp_manager", None)
    if manager is not None:
        manager.close()


def test_stderr_buffer_config_bounds_are_enforced() -> None:
    low = MCPServerConfig(name="Low", command=["echo"], stderr_buffer_bytes=1)
    high = MCPServerConfig(
        name="High",
        command=["echo"],
        stderr_buffer_bytes=10_000_000,
    )

    assert low.stderr_buffer_bytes == 1024
    assert high.stderr_buffer_bytes == 1_048_576


def test_stderr_tail_is_attached_to_tool_runtime_error_details() -> None:
    bootstrap = build_runtime_bootstrap(
        config=_runtime_config(stderr_banner="stderr boom"),
        strict=True,
    )
    try:
        tool = bootstrap.registry.list()["mcp.fixture.stderr_error_tool"]
        with pytest.raises(ToolRuntimeError) as excinfo:
            tool.handler({}, None)

        assert excinfo.value.details["reason_code"] == "mcp_upstream_error"
        assert "stderr boom" in excinfo.value.details["mcp_stderr_tail"]
    finally:
        _close_bootstrap(bootstrap)


def test_ring_buffer_truncates_large_stderr_output() -> None:
    banner = "X" * 5000
    bootstrap = build_runtime_bootstrap(
        config=_runtime_config(stderr_buffer_bytes=1024, stderr_banner=banner),
        strict=True,
    )
    try:
        tool = bootstrap.registry.list()["mcp.fixture.stderr_error_tool"]
        with pytest.raises(ToolRuntimeError) as excinfo:
            tool.handler({}, None)
        tail = str(excinfo.value.details.get("mcp_stderr_tail", "") or "")
        assert tail
        assert len(tail.encode("utf-8")) <= 4096
        assert len(tail.encode("utf-8")) < len(banner.encode("utf-8"))
    finally:
        _close_bootstrap(bootstrap)


def test_mcp_server_metrics_and_runtime_report_surface_success_failure_and_restart() -> (
    None
):
    bootstrap = build_runtime_bootstrap(
        config=_runtime_config(stderr_banner="ops"), strict=True
    )
    try:
        manager = bootstrap.mcp_manager
        assert manager is not None

        success = manager.call_tool(
            server_name="fixture",
            remote_name="echo-text",
            arguments={"text": "hello"},
        )
        assert success["ok"] is True

        bootstrap.registry.mcp_manager.close_server("fixture")
        recovered = manager.call_tool(
            server_name="fixture",
            remote_name="echo-text",
            arguments={"text": "after restart"},
        )
        assert recovered["ok"] is True

        tool = bootstrap.registry.list()["mcp.fixture.stderr_error_tool"]
        with pytest.raises(ToolRuntimeError):
            tool.handler({}, None)

        metrics = manager.mcp_server_metrics()
        assert metrics["fixture"]["call_total"] >= 3
        assert metrics["fixture"]["call_error_total"] >= 1
        assert metrics["fixture"]["call_latency_ms_p50"] >= 0
        assert metrics["fixture"]["call_latency_ms_p95"] >= 0
        assert metrics["fixture"]["restart_total"] >= 1

        report = _build_mcp_section(
            type("Runtime", (), {"tools": bootstrap.registry})()
        )
        assert report["enabled"] is True
        assert report["server_metrics"]["fixture"]["call_total"] >= 3
    finally:
        _close_bootstrap(bootstrap)


class _LoggingTransport:
    def __init__(self) -> None:
        self.requests: list[tuple[str, dict]] = []

    def is_running(self) -> bool:
        return True

    def request(
        self,
        *,
        method: str,
        params: dict | None = None,
        timeout_seconds: float,
        server_request_handler=None,
    ) -> dict:
        del timeout_seconds, server_request_handler
        self.requests.append((method, dict(params or {})))
        return {}


def test_mcp_logging_set_level_notifications_report_and_tui_rendering() -> None:
    session = MCPServerSession(
        MCPServerConfig(name="Fixture", command=[sys.executable, "-m", "fixture"])
    )
    transport = _LoggingTransport()
    session._transport = transport  # noqa: SLF001

    session.set_log_level("DEBUG")
    session._handle_server_notification(  # noqa: SLF001
        method="notifications/message",
        params={
            "level": "warning",
            "logger": "fixture.logger",
            "message": "careful now",
            "data": {"code": "fixture_warning"},
        },
    )

    assert transport.requests == [("logging/setLevel", {"level": "debug"})]
    latest = session.recent_log_messages(limit=1)[0]
    assert latest.level == "warning"
    assert latest.message == "careful now"
    assert latest.logger == "fixture.logger"
    assert latest.data == {"code": "fixture_warning"}

    manager = MCPFleetManager(servers=[])
    manager._sessions = {"fixture": session}  # noqa: SLF001
    tools = type("Tools", (), {"mcp_manager": manager})()
    report = _build_mcp_section(type("Runtime", (), {"tools": tools})())
    assert report["server_logs"]["fixture"][0]["message"] == "careful now"

    rendered = render_mcp_status_report(
        [
            MCPServerStatusRow(
                name="fixture",
                transport="stdio",
                status="ready",
                recent_log="warning: careful now",
            )
        ]
    )
    assert "recent log: warning: careful now" in rendered
