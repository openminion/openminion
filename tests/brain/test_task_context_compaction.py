import json
from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator

from openminion.modules.brain.loop.context.task_context import (
    _validation_schema,
    with_task_update_schema,
)
from openminion.modules.brain.schemas.task_context import (
    TaskContextUpdate,
)
from openminion.modules.llm.schemas import ToolSpec


@pytest.mark.parametrize(
    "change",
    [
        {},
        {"operation": "invent"},
        {"expected_revision": -1},
        {"user_quote": ""},
        {"unexpected": True},
        {"set_conditions": [{"key": "bad key", "quote": "USD"}]},
        {"set_conditions": [{"key": "currency", "quote": ""}]},
        {"set_conditions": [{"key": "currency", "quote": "USD", "extra": True}]},
        {"remove_conditions": ["x"] * 129},
        {"objective": None},
        {"objective": "x" * 2049},
    ],
)
def test_compact_schema_has_same_validation_contract(change):
    original = TaskContextUpdate.model_json_schema()
    compact = _validation_schema(original)
    value = dict(
        operation="start",
        expected_revision=0,
        user_quote="USD",
        objective="Export",
        set_conditions=[dict(key="currency", quote="USD")],
    )
    value.update(change)
    Draft202012Validator.check_schema(compact)
    assert Draft202012Validator(original).is_valid(value) == Draft202012Validator(
        compact
    ).is_valid(value)
    assert len(json.dumps(compact)) < len(json.dumps(original))


def test_runtime_tool_schema_annotations_are_unchanged():
    original = {
        "type": "object",
        "title": "Runtime",
        "properties": {
            "title": {
                "type": "string",
                "default": "Keep",
                "description": "Runtime title",
            }
        },
    }
    spec = ToolSpec(name="runtime", description="Test", input_schema=original)
    before = deepcopy(spec.input_schema)
    result = with_task_update_schema(spec, required=True).input_schema
    assert spec.input_schema == before
    assert result["title"] == before["title"]
    assert result["properties"]["title"] == before["properties"]["title"]
    assert _validation_schema(
        {"properties": {"title": {"type": "string", "title": "Annotation"}}}
    ) == {"properties": {"title": {"type": "string"}}}
