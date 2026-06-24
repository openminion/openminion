from __future__ import annotations

import sys
from pathlib import Path

from openminion.base.config.mcp import MCPServerConfig
from openminion.base.config.runtime import RuntimeConfig
from openminion.modules.llm.providers.base import ProviderToolCall
from openminion.modules.tool.base import ToolExecutionContext
from openminion.modules.tool.bootstrap import build_runtime_bootstrap
from openminion.services.tool.schema import ToolSchemaService


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


def _close_bootstrap(bootstrap) -> None:
    manager = getattr(bootstrap, "mcp_manager", None)
    if manager is not None:
        manager.close()


def test_build_runtime_bootstrap_registers_mcp_tools() -> None:
    bootstrap = build_runtime_bootstrap(config=_runtime_config(), strict=True)
    try:
        assert bootstrap.mcp_manager is bootstrap.registry.mcp_manager
        assert bootstrap.contract_drift_report is not None
        assert bootstrap.contract_drift_report.has_drift is False
        tool_names = set(bootstrap.registry.list().keys())
        assert "mcp.fixture.echo_text" in tool_names
        assert "mcp.fixture.add_numbers" in tool_names
        assert "mcp.fixture.emit_list_changed" in tool_names
        assert "mcp.fixture.nullable_anyof" in tool_names
        assert "mcp.fixture.sleep_tool" in tool_names
        assert "mcp.fixture.stderr_error_tool" in tool_names
        assert "mcp.fixture.tagged_union_simple" in tool_names
        assert "mcp.fixture.prompt.greet_user" in tool_names
        assert "mcp.fixture.resource.readme_md" in tool_names
        assert "mcp.fixture.resource_template.fixture_doc" in tool_names
        assert "mcp.fixture.unsupported_anyof" in tool_names

        schemas = ToolSchemaService().collect_execution_tool_schemas(
            registry=bootstrap.registry
        )
        schema_names = {str(item.get("name", "")) for item in schemas}
        assert "mcp.fixture.echo_text" in schema_names
        assert "mcp.fixture.add_numbers" in schema_names
        assert "mcp.fixture.nullable_anyof" in schema_names
        assert "mcp.fixture.tagged_union_simple" in schema_names
        assert "mcp.fixture.prompt.greet_user" in schema_names
        assert "mcp.fixture.resource.readme_md" in schema_names
        assert "mcp.fixture.resource_template.fixture_doc" in schema_names

        mcp_records = [
            record
            for record in (bootstrap.bootstrap_records or [])
            if record.label == "MCP"
        ]
        assert len(mcp_records) == 1
        assert mcp_records[0].status == "registered"
        assert "passthrough_mcp_tools=" in str(mcp_records[0].error or "")
    finally:
        _close_bootstrap(bootstrap)


def test_model_origin_execution_supports_prompt_visible_mcp_runtime_tools() -> None:
    bootstrap = build_runtime_bootstrap(config=_runtime_config(), strict=True)
    try:
        batch = bootstrap.registry.execute_calls(
            [
                ProviderToolCall(
                    name="mcp.fixture.echo_text",
                    arguments={"text": "hello mcp"},
                    source="native",
                )
            ],
            context=ToolExecutionContext(
                channel="console",
                target="unit-test",
                session_id="session-1",
                metadata={"tool_call_origin": "model"},
            ),
        )
        assert len(batch.results) == 1
        result = batch.results[0]
        assert result.ok is True
        assert result.content.startswith("echo: hello mcp")
        assert result.data["mcp_server"] == "fixture"
        assert result.data["runtime_binding_id"] == "runtime.mcp.fixture.echo_text"
        assert result.data["runtime_tool_name"] == "mcp.fixture.echo_text"
    finally:
        _close_bootstrap(bootstrap)


def test_model_origin_execution_supports_prompt_visible_mcp_prompt_and_resource_runtime_tools() -> (
    None
):
    bootstrap = build_runtime_bootstrap(config=_runtime_config(), strict=True)
    try:
        batch = bootstrap.registry.execute_calls(
            [
                ProviderToolCall(
                    name="mcp.fixture.prompt.greet_user",
                    arguments={"user_name": "Taylor"},
                    source="native",
                ),
                ProviderToolCall(
                    name="mcp.fixture.resource.readme_md",
                    arguments={},
                    source="native",
                ),
            ],
            context=ToolExecutionContext(
                channel="console",
                target="unit-test",
                session_id="session-prompts-resources",
                metadata={"tool_call_origin": "model"},
            ),
        )
        assert len(batch.results) == 2
        prompt_result = batch.results[0]
        resource_result = batch.results[1]
        assert prompt_result.ok is True
        assert prompt_result.content.startswith("Hello, Taylor!")
        assert prompt_result.data["mcp_remote_prompt_name"] == "greet-user"
        assert resource_result.ok is True
        assert "MCP fixture resource body." in resource_result.content
        assert resource_result.data["mcp_resource_uri"] == "file://fixture/readme.md"
    finally:
        _close_bootstrap(bootstrap)


def test_model_origin_execution_supports_mcp_resource_template_runtime_tools() -> None:
    bootstrap = build_runtime_bootstrap(config=_runtime_config(), strict=True)
    try:
        template_tool = bootstrap.registry.list()[
            "mcp.fixture.resource_template.fixture_doc"
        ]
        assert template_tool.min_scope == "READ_ONLY"
        assert template_tool.idempotent is True
        assert template_tool.parameters_schema == {
            "type": "object",
            "properties": {
                "slug": {
                    "type": "string",
                    "description": "Value for {slug} in the MCP resource URI.",
                }
            },
            "required": ["slug"],
            "additionalProperties": False,
        }

        batch = bootstrap.registry.execute_calls(
            [
                ProviderToolCall(
                    name="mcp.fixture.resource_template.fixture_doc",
                    arguments={"slug": "dynamic"},
                    source="native",
                ),
            ],
            context=ToolExecutionContext(
                channel="console",
                target="unit-test",
                session_id="session-resource-template",
                metadata={"tool_call_origin": "model"},
            ),
        )

        assert len(batch.results) == 1
        result = batch.results[0]
        assert result.ok is True
        assert "MCP fixture template body." in result.content
        assert result.data["mcp_resource_uri"] == "file://fixture/dynamic.md"
    finally:
        _close_bootstrap(bootstrap)


def test_call_time_server_restart_recovers_transparently() -> None:
    bootstrap = build_runtime_bootstrap(config=_runtime_config(), strict=True)
    try:
        manager = bootstrap.mcp_manager
        assert manager is not None
        manager.close_server("fixture")

        batch = bootstrap.registry.execute_calls(
            [
                ProviderToolCall(
                    name="mcp.fixture.echo_text",
                    arguments={"text": "hello again"},
                    source="native",
                )
            ],
            context=ToolExecutionContext(
                channel="console",
                target="unit-test",
                session_id="session-2",
                metadata={"tool_call_origin": "model"},
            ),
        )
        assert len(batch.results) == 1
        result = batch.results[0]
        assert result.ok is True
        assert result.content.startswith("echo: hello again")
    finally:
        _close_bootstrap(bootstrap)
