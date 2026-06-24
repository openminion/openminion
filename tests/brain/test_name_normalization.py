from __future__ import annotations

import sys
from pathlib import Path

from openminion.base.config.mcp import MCPServerConfig
from openminion.base.config.runtime import RuntimeConfig
from openminion.modules.brain.tools.parser import normalize_tool_name_for_brain
from openminion.modules.tool.bootstrap import build_runtime_bootstrap


FIXTURE_SERVER_PATH = (
    Path(__file__).resolve().parents[1] / "mcp" / "fixtures" / "mock_mcp_server.py"
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


def test_normalize_two_part_unknown_name_returns_none() -> None:
    assert normalize_tool_name_for_brain("foo.bar") is None


def test_normalize_canonical_model_tool_id_passthrough() -> None:
    assert normalize_tool_name_for_brain("weather") == "weather"
    assert normalize_tool_name_for_brain("tool.list") == "tool.list"
    assert normalize_tool_name_for_brain("tool.web.search") == "web.search"


def test_normalize_runtime_candidate_names_no_longer_succeed_implicitly() -> None:
    assert normalize_tool_name_for_brain("fetch.get") is None
    assert normalize_tool_name_for_brain("time.now") is None
    assert normalize_tool_name_for_brain("location.get") is None
    assert normalize_tool_name_for_brain("weather.openmeteo.current") is None


def test_normalize_dynamic_mcp_model_tool_id_after_bootstrap() -> None:
    bootstrap = build_runtime_bootstrap(config=_runtime_config(), strict=True)
    try:
        assert normalize_tool_name_for_brain("mcp.fixture.echo_text") == (
            "mcp.fixture.echo_text"
        )
    finally:
        manager = getattr(bootstrap, "mcp_manager", None)
        if manager is not None:
            manager.close()
