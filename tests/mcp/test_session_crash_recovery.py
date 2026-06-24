from __future__ import annotations

import sys
from pathlib import Path

import pytest

from openminion.base.config.mcp import MCPServerConfig
from openminion.base.config.runtime import RuntimeConfig
from openminion.tools.mcp.manager import (
    MCPFleetManager,
    MCPServerSession,
)
from openminion.tools.mcp.transport import MCPServerUnavailableError


FIXTURE_SERVER_PATH = (
    Path(__file__).resolve().parent / "fixtures" / "mock_mcp_server.py"
)


def _runtime_config() -> RuntimeConfig:
    return RuntimeConfig(
        mcp_servers=[
            MCPServerConfig(
                name="Fixture",
                transport="stdio",
                command=[sys.executable, str(FIXTURE_SERVER_PATH)],
                request_timeout_seconds=5.0,
                startup_timeout_seconds=5.0,
            )
        ]
    )


def test_transparent_restart_recovers_once_after_process_exit() -> None:
    manager = MCPFleetManager.from_runtime_config(_runtime_config())
    try:
        first = manager.call_tool(
            server_name="fixture",
            remote_name="echo-text",
            arguments={"text": "before crash"},
        )
        assert first["ok"] is True

        session = manager._sessions["fixture"]
        manager.close_server("fixture")
        recovered = manager.call_tool(
            server_name="fixture",
            remote_name="echo-text",
            arguments={"text": "after crash"},
        )

        assert recovered["ok"] is True
        assert recovered["content"] == "echo: after crash"
        assert session._restart_total == 1
        assert len(session._restart_history) == 1
    finally:
        manager.close()


def test_fourth_crash_inside_window_raises_unrecoverable_error() -> None:
    manager = MCPFleetManager.from_runtime_config(_runtime_config())
    try:
        session = manager._sessions["fixture"]
        manager.call_tool(
            server_name="fixture",
            remote_name="echo-text",
            arguments={"text": "prime"},
        )

        for _ in range(3):
            manager.close_server("fixture")
            result = manager.call_tool(
                server_name="fixture",
                remote_name="echo-text",
                arguments={"text": "recover"},
            )
            assert result["ok"] is True

        manager.close_server("fixture")
        with pytest.raises(MCPServerUnavailableError) as excinfo:
            manager.call_tool(
                server_name="fixture",
                remote_name="echo-text",
                arguments={"text": "boom"},
            )

        assert excinfo.value.reason_code == "mcp_server_crashed_unrecoverable"
        assert "unrecoverable" in str(excinfo.value).lower()
        assert len(session._restart_history) == 3
        assert session._restart_total == 3
    finally:
        manager.close()


def test_initialize_crash_raises_without_retry_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _runtime_config()
    session = MCPServerSession(config.mcp_servers[0])
    call_count = {"request": 0}

    def _explode(**kwargs):
        del kwargs
        call_count["request"] += 1
        raise MCPServerUnavailableError(
            "initialize died",
            reason_code="mcp_server_unavailable",
        )

    monkeypatch.setattr(session._transport, "start", lambda: None)
    monkeypatch.setattr(session._transport, "close", lambda: None)
    monkeypatch.setattr(session._transport, "request", _explode)

    with pytest.raises(MCPServerUnavailableError) as excinfo:
        session.call_tool(remote_name="echo-text", arguments={"text": "x"})

    assert excinfo.value.reason_code == "mcp_server_unavailable"
    assert call_count["request"] == 1
    assert session._restart_total == 0
    assert len(session._restart_history) == 0
