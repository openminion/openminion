from __future__ import annotations

import sys
from pathlib import Path

import pytest

from openminion.base.config.mcp import MCPServerConfig
from openminion.base.config.runtime import RuntimeConfig
from openminion.modules.llm.providers.base import ProviderToolCall
from openminion.modules.tool.base import ToolExecutionContext
from openminion.modules.tool.bootstrap import build_runtime_bootstrap
from openminion.tools.mcp.schemas import (
    MCPArgumentValidationError,
    MCPUnsupportedSchemaError,
    prepare_mcp_registration_schema,
    validate_mcp_arguments,
)


FIXTURE_SERVER_PATH = (
    Path(__file__).resolve().parent / "fixtures" / "mock_mcp_server.py"
)


NULLABLE_ANYOF_SCHEMA = {
    "type": "object",
    "properties": {
        "nickname": {
            "anyOf": [{"type": "string"}, {"type": "null"}],
        }
    },
    "additionalProperties": False,
}

TAGGED_UNION_SCHEMA = {
    "type": "object",
    "properties": {
        "payload": {
            "anyOf": [
                {
                    "type": "object",
                    "properties": {
                        "kind": {"const": "text"},
                        "text": {"type": "string"},
                    },
                    "required": ["kind", "text"],
                    "additionalProperties": False,
                },
                {
                    "type": "object",
                    "properties": {
                        "kind": {"const": "count"},
                        "count": {"type": "integer"},
                    },
                    "required": ["kind", "count"],
                    "additionalProperties": False,
                },
            ]
        }
    },
    "required": ["payload"],
    "additionalProperties": False,
}

CONFLICTING_TAGGED_UNION_SCHEMA = {
    "type": "object",
    "properties": {
        "payload": {
            "anyOf": [
                {
                    "type": "object",
                    "properties": {
                        "kind": {"const": "text"},
                        "text": {"type": "string"},
                    },
                    "required": ["kind", "text"],
                    "additionalProperties": False,
                },
                {
                    "type": "object",
                    "properties": {
                        "kind": {"const": 7},
                        "count": {"type": "integer"},
                    },
                    "required": ["kind", "count"],
                    "additionalProperties": False,
                },
            ]
        }
    },
    "required": ["payload"],
    "additionalProperties": False,
}

UNSUPPORTED_ANYOF_SCHEMA = {
    "type": "object",
    "properties": {
        "payload": {
            "anyOf": [{"type": "string"}, {"type": "integer"}],
        }
    },
}


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


def test_nullable_anyof_schema_is_supported_and_validated() -> None:
    prepared = prepare_mcp_registration_schema(NULLABLE_ANYOF_SCHEMA)
    assert prepared.mode == "strict"

    assert validate_mcp_arguments(
        schema=NULLABLE_ANYOF_SCHEMA,
        arguments={"nickname": None},
    ) == {"nickname": None}
    assert validate_mcp_arguments(
        schema=NULLABLE_ANYOF_SCHEMA,
        arguments={"nickname": "Taylor"},
    ) == {"nickname": "Taylor"}


def test_tagged_union_schema_is_supported_and_validated() -> None:
    prepared = prepare_mcp_registration_schema(TAGGED_UNION_SCHEMA)
    assert prepared.mode == "strict"

    payload = {"payload": {"kind": "count", "count": 3}}
    assert (
        validate_mcp_arguments(schema=TAGGED_UNION_SCHEMA, arguments=payload) == payload
    )


def test_composed_schema_accepts_mixed_discriminator_types() -> None:
    prepared = prepare_mcp_registration_schema(CONFLICTING_TAGGED_UNION_SCHEMA)
    assert prepared.mode == "strict"
    payload = {"payload": {"kind": 7, "count": 2}}
    assert (
        validate_mcp_arguments(
            schema=CONFLICTING_TAGGED_UNION_SCHEMA,
            arguments=payload,
        )
        == payload
    )


def test_anyof_schema_is_strictly_validated() -> None:
    prepared = prepare_mcp_registration_schema(UNSUPPORTED_ANYOF_SCHEMA)
    assert prepared.mode == "strict"

    payload = {"payload": 7}
    assert (
        validate_mcp_arguments(
            schema=UNSUPPORTED_ANYOF_SCHEMA,
            arguments=payload,
        )
        == payload
    )
    with pytest.raises(
        MCPArgumentValidationError,
        match="not valid under any of the given schemas",
    ):
        validate_mcp_arguments(
            schema=UNSUPPORTED_ANYOF_SCHEMA,
            arguments={"payload": []},
        )


def test_invalid_json_schema_is_rejected_at_registration() -> None:
    with pytest.raises(MCPUnsupportedSchemaError, match="JSON Schema 2020-12"):
        prepare_mcp_registration_schema({"type": "not-a-json-schema-type"})


def test_json_schema_2020_12_references_and_composition_are_supported() -> None:
    schema = {
        "$defs": {
            "label": {"type": "string", "minLength": 2},
        },
        "type": "object",
        "properties": {"label": {"$ref": "#/$defs/label"}},
        "allOf": [{"required": ["label"]}],
        "additionalProperties": {"type": "integer"},
    }

    payload = {"label": "ok", "count": 2}
    assert validate_mcp_arguments(schema=schema, arguments=payload) == payload
    with pytest.raises(MCPArgumentValidationError, match="is too short"):
        validate_mcp_arguments(schema=schema, arguments={"label": "x"})


def test_registered_schema_variants_execute_through_bootstrap() -> None:
    bootstrap = build_runtime_bootstrap(config=_runtime_config(), strict=True)
    try:
        batch = bootstrap.registry.execute_calls(
            [
                ProviderToolCall(
                    name="mcp.fixture.nullable_anyof",
                    arguments={"nickname": None},
                    source="native",
                ),
                ProviderToolCall(
                    name="mcp.fixture.tagged_union_simple",
                    arguments={"payload": {"kind": "text", "text": "hi"}},
                    source="native",
                ),
                ProviderToolCall(
                    name="mcp.fixture.unsupported_anyof",
                    arguments={"payload": 9},
                    source="native",
                ),
            ],
            context=ToolExecutionContext(
                channel="console",
                target="unit-test",
                session_id="session-schema-subset",
                metadata={"tool_call_origin": "model"},
            ),
        )

        assert [result.ok for result in batch.results] == [True, True, True]
        assert '"nickname": null' in batch.results[0].content
        assert '"kind": "text"' in batch.results[1].content
        assert batch.results[2].content.startswith("passthrough:")
    finally:
        _close_bootstrap(bootstrap)
