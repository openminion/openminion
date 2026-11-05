import json
from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator

from openminion.modules.brain.loop.context.task_context import (
    _validation_schema,
    task_context_message,
    with_task_update_schema,
)
from openminion.modules.brain.loop.context.task_history import history_fields
from openminion.modules.brain.schemas.task_context import (
    ResolvedTaskContext,
    TaskConditionEvidence,
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


def test_history_pool_preserves_questions_events_order_and_persistent_state():
    events = [
        TaskConditionEvidence(
            quote="yes",
            question_quote=f"Use width {i % 2 + 120}?",
            source=str(i % 10) * 64,
        )
        for i in range(30)
    ]
    task = ResolvedTaskContext(
        revision=31,
        objective="Draw a rectangle",
        conditions={"width": "Keep width"},
        condition_history={"width": events},
        consumed_input="f" * 64,
    )
    before = task.model_dump()
    text = task_context_message(task).content
    payload = json.loads(text.splitlines()[1])
    encoded = payload["condition_history"]["width"]
    decoded = [encoded["evidence"][index] for index in encoded["sequence"]]
    expected = [
        event.model_dump(exclude={"source"}, exclude_none=True) for event in events
    ]
    assert decoded == expected
    assert len(encoded["evidence"]) == 2
    assert len(json.dumps(encoded)) < len(json.dumps(expected))
    assert "indexes evidence from zero" in payload["history_rule"]
    assert task.model_dump() == before


def test_small_history_keeps_simple_shape_and_omits_null_question():
    item = TaskConditionEvidence(quote="excluding archived rows", source="f" * 64)
    fields = history_fields({"archive": [item]})
    assert fields["condition_history"] == {"archive": [{"quote": item.quote}]}
    assert "pooled" not in fields["history_rule"]
    assert history_fields({}) == {}
