from __future__ import annotations

import sys
from pathlib import Path

from openminion.base.config import OpenMinionConfig
from openminion.base.config.mcp import MCPExposureConfig, MCPServerConfig
from openminion.base.config.runtime import RuntimeConfig
from openminion.modules.llm.providers.base import ProviderToolCall
from openminion.modules.tool.base import ToolExecutionContext
from openminion.modules.tool.bootstrap import build_runtime_bootstrap
from openminion.services.tool.schema import ToolSchemaService
from openminion.tools.mcp.exposure import (
    build_mcp_exposure_report,
    scoped_mcp_registry_view,
)


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
            ),
            MCPServerConfig(
                name="Other",
                transport="stdio",
                command=[sys.executable, str(FIXTURE_SERVER_PATH)],
                request_timeout_seconds=5.0,
                startup_timeout_seconds=5.0,
            ),
        ]
    )


def _close_bootstrap(bootstrap) -> None:
    manager = getattr(bootstrap, "mcp_manager", None)
    if manager is not None:
        manager.close()


def test_agent_profile_parses_mcp_exposure_filters() -> None:
    config = OpenMinionConfig.from_dict(
        {
            "runtime": {
                "mcp_servers": [
                    {
                        "name": "Fixture",
                        "transport": "stdio",
                        "command": [sys.executable, str(FIXTURE_SERVER_PATH)],
                    }
                ]
            },
            "agents": {
                "scoped": {
                    "provider": "echo",
                    "mcp_exposure": {
                        "include_servers": ["Fixture"],
                        "exclude_tools": ["mcp.fixture.sleep_tool"],
                    },
                }
            },
            "default_agent": "scoped",
        }
    )

    profile = config.agents["scoped"]
    assert profile.mcp_exposure.include_servers == ["fixture"]
    assert profile.mcp_exposure.exclude_tools == ["mcp.fixture.sleep_tool"]
    assert profile.to_dict()["mcp_exposure"] == {
        "include_servers": ["fixture"],
        "exclude_tools": ["mcp.fixture.sleep_tool"],
    }


def test_agent_mcp_exposure_filters_schema_and_execution() -> None:
    runtime_config = _runtime_config()
    bootstrap = build_runtime_bootstrap(config=runtime_config, strict=True)
    try:
        fixture_view = scoped_mcp_registry_view(
            bootstrap.registry,
            MCPExposureConfig(
                include_servers=["fixture"],
                exclude_tools=["mcp.fixture.sleep_tool"],
            ),
        )
        other_view = scoped_mcp_registry_view(
            bootstrap.registry,
            MCPExposureConfig(include_servers=["other"]),
        )

        fixture_schema_names = {
            str(item.get("name", ""))
            for item in ToolSchemaService().collect_execution_tool_schemas(
                registry=fixture_view
            )
        }
        other_schema_names = {
            str(item.get("name", ""))
            for item in ToolSchemaService().collect_execution_tool_schemas(
                registry=other_view
            )
        }

        assert "mcp.fixture.echo_text" in fixture_schema_names
        assert "mcp.fixture.sleep_tool" not in fixture_schema_names
        assert "mcp.other.echo_text" not in fixture_schema_names
        assert "mcp.other.echo_text" in other_schema_names
        assert "mcp.fixture.echo_text" not in other_schema_names

        ok_batch = fixture_view.execute_calls(
            [
                ProviderToolCall(
                    name="mcp.fixture.echo_text",
                    arguments={"text": "scoped"},
                    source="native",
                )
            ],
            context=ToolExecutionContext(
                channel="console",
                target="unit-test",
                session_id="session-scope-ok",
                metadata={"tool_call_origin": "model"},
            ),
        )
        assert ok_batch.results[0].ok is True

        denied_batch = fixture_view.execute_calls(
            [
                ProviderToolCall(
                    name="mcp.other.echo_text",
                    arguments={"text": "blocked"},
                    source="native",
                )
            ],
            context=ToolExecutionContext(
                channel="console",
                target="unit-test",
                session_id="session-scope-denied",
                metadata={"tool_call_origin": "model"},
            ),
        )
        assert denied_batch.results[0].ok is False
        assert denied_batch.results[0].data["error_code"] == "unknown_tool_name"
    finally:
        _close_bootstrap(bootstrap)


def test_agent_mcp_exposure_report_shows_configured_exposed_filtered() -> None:
    runtime_config = _runtime_config()
    bootstrap = build_runtime_bootstrap(config=runtime_config, strict=True)
    try:
        report = build_mcp_exposure_report(
            registry=bootstrap.registry,
            server_configs=list(runtime_config.mcp_servers),
            exposure=MCPExposureConfig(
                include_servers=["fixture"],
                exclude_tools=["mcp.fixture.sleep_tool"],
            ),
        )

        assert report["configured_servers"] == ["fixture", "other"]
        assert report["configured_server_count"] == 2
        assert "mcp.fixture.echo_text" in report["exposed_runtime_tools"]
        assert "mcp.fixture.sleep_tool" in report["filtered_runtime_tools"]
        assert "mcp.other.echo_text" in report["filtered_runtime_tools"]
        assert report["exposed_servers"] == ["fixture"]
        assert report["filtered_runtime_tool_count"] > 0
    finally:
        _close_bootstrap(bootstrap)
