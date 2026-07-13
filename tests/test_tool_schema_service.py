from __future__ import annotations

from types import SimpleNamespace

from pydantic import BaseModel

from openminion.modules.tool.schema_service import ToolSchemaService


def test_build_prompt_tool_schemas_preserves_registry_order_without_query_ranking() -> (
    None
):
    service = ToolSchemaService()
    tool_schemas = [
        {"name": "web.search", "description": "Search", "parameters": {}},
        {"name": "weather", "description": "Weather", "parameters": {}},
        {"name": "time", "description": "Time", "parameters": {}},
        {"name": "file.read", "description": "Read", "parameters": {}},
        {"name": "tavily.web.search", "description": "Legacy", "parameters": {}},
    ]

    stubs = service.build_prompt_tool_schemas(query="test", tool_schemas=tool_schemas)

    assert [item["name"] for item in stubs] == [
        "web.search",
        "weather",
        "time",
        "file.read",
        "tavily.web.search",
    ]


def test_build_system_tools_submit_output_is_non_dispatchable() -> None:
    class _DummySchema(BaseModel):
        value: int

    service = ToolSchemaService()
    tools = service.build_system_tools(structured_schema=_DummySchema)

    assert len(tools) == 1
    assert tools[0]["name"] == "submit_output"
    assert tools[0]["tool_lane"] == "system"
    assert tools[0]["dispatchable"] is False
    assert "value" in tools[0]["parameters"].get("properties", {})


def test_build_prompt_tool_schemas_ignores_query_text_for_stub_selection() -> None:
    service = ToolSchemaService()
    tool_schemas = [
        {"name": "time", "description": "Time operations", "parameters": {}},
        {
            "name": "location",
            "description": "Approximate current location",
            "parameters": {},
        },
        {"name": "weather", "description": "Weather lookup", "parameters": {}},
    ]

    stubs = service.build_prompt_tool_schemas(
        query="arbitrary user text",
        tool_schemas=tool_schemas,
    )

    assert [item["name"] for item in stubs] == ["time", "location", "weather"]


def test_build_prompt_tool_schemas_preserves_exact_tool_id_mentions() -> None:
    service = ToolSchemaService()
    tool_schemas = [
        {"name": "browser", "description": "Browser", "parameters": {}},
        {"name": "exec.run", "description": "Exec", "parameters": {}},
        {"name": "file.read", "description": "Read", "parameters": {}},
        {"name": "weather", "description": "Weather", "parameters": {}},
        {"name": "task.schedule", "description": "Schedule task", "parameters": {}},
    ]

    stubs = service.build_prompt_tool_schemas(
        query="Use task.schedule to create a cron task.",
        tool_schemas=tool_schemas,
        stub_limit=3,
    )

    assert [item["name"] for item in stubs] == [
        "task.schedule",
        "browser",
        "exec.run",
    ]


def test_build_prompt_tool_schemas_does_not_match_partial_tool_ids() -> None:
    service = ToolSchemaService()
    tool_schemas = [
        {"name": "weather", "description": "Weather", "parameters": {}},
        {"name": "task.schedule", "description": "Schedule task", "parameters": {}},
    ]

    stubs = service.build_prompt_tool_schemas(
        query="Use task.schedule_extra if it exists.",
        tool_schemas=tool_schemas,
        stub_limit=1,
    )

    assert [item["name"] for item in stubs] == ["weather"]


def test_collect_execution_tool_schemas_from_model_provider_specs() -> None:
    service = ToolSchemaService()
    registry = SimpleNamespace(
        model_provider_specs=lambda: [
            SimpleNamespace(
                name="web.search",
                description="Search web",
                parameters={"type": "object", "properties": {}},
            ),
            SimpleNamespace(
                name="web.search",
                description="duplicate",
                parameters={"type": "object", "properties": {}},
            ),
        ]
    )

    schemas = service.collect_execution_tool_schemas(registry=registry)
    assert len(schemas) == 1
    assert schemas[0]["name"] == "web.search"
    assert schemas[0]["tool_lane"] == "execution"
    assert schemas[0]["dispatchable"] is True


def test_tool_stub_surfaces_optional_fields_when_no_required_args_exist() -> None:
    service = ToolSchemaService()
    stub = service.tool_stub(
        {
            "name": "weather",
            "description": "Get current weather conditions",
            "parameters": {
                "type": "object",
                "properties": {
                    "provider": {"type": "string"},
                    "location": {"type": "string", "description": "Location query"},
                    "city": {"type": "string"},
                    "query": {"type": "string"},
                    "place": {"type": "string"},
                    "latitude": {"type": "number"},
                    "longitude": {"type": "number"},
                },
                "additionalProperties": True,
            },
        }
    )

    props = stub["parameters"]["properties"]
    assert "location" in props
    assert "query" in props
    assert "city" in props
    assert "required" not in stub["parameters"]


def test_get_tools_for_purpose_llm_request_never_returns_prompt_stubs() -> None:
    service = ToolSchemaService()

    bundle = service.get_tools_for_purpose(
        purpose="decide",
        query="what's the weather?",
        caller_context="llm_request",
        execution_tools=[
            {"name": "weather", "description": "Weather", "parameters": {}}
        ],
        structured_schema=None,
        prompt_schemas_enabled=True,
    )

    assert bundle.execution_tools
    assert bundle.prompt_tool_stubs == ()


def test_get_tools_for_purpose_context_build_returns_prompt_stubs_for_structured_phase() -> (
    None
):
    service = ToolSchemaService()

    bundle = service.get_tools_for_purpose(
        purpose="decide",
        query="what's the weather?",
        caller_context="context_build",
        execution_tools=[
            {"name": "weather", "description": "Weather", "parameters": {}}
        ],
        structured_schema=None,
        prompt_schemas_enabled=False,
    )

    assert bundle.execution_tools
    assert bundle.prompt_tool_stubs


def test_get_tools_for_purpose_validate_context_build_returns_prompt_stubs() -> None:
    service = ToolSchemaService()

    bundle = service.get_tools_for_purpose(
        purpose="validate",
        query="check feasibility",
        caller_context="context_build",
        execution_tools=[
            {
                "name": "weather",
                "description": "Weather",
                "parameters": {},
                "capability_tags": ["weather"],
                "feasibility_descriptors": ["weather", "forecast"],
                "metadata_complete": True,
            }
        ],
        structured_schema=None,
        prompt_schemas_enabled=False,
    )

    assert bundle.execution_tools
    assert bundle.prompt_tool_stubs


def test_tool_metadata_enrichment_marks_missing_metadata_incomplete() -> None:
    service = ToolSchemaService()
    registry = SimpleNamespace(
        model_provider_specs=lambda: [
            SimpleNamespace(
                name="web.search",
                description="Search web",
                parameters={"type": "object", "properties": {}},
            )
        ],
        _tools={},
    )

    schemas = service.collect_execution_tool_schemas(registry=registry)

    assert schemas[0]["metadata_complete"] is False
    assert "missing_capability_tags" in schemas[0]["metadata_warnings"]
    assert "missing_feasibility_descriptors" in schemas[0]["metadata_warnings"]
