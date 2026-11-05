"""Current-task guarantees remain independent of archive and lifecycle extensions."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from openminion.modules.brain.execution.task_context import (
    execution_task_text,
    inherit_child_task,
)
from openminion.modules.brain.loop.context.task_context import updated_task_context
from openminion.modules.brain.loop.context.task_entry import entry_task_tool_specs
from openminion.modules.brain.loop.entry import clarify_tool_spec
from openminion.modules.brain.schemas import WorkingState
from tests.brain.test_resolved_task_context import delta
from tests.brain.test_unified_turn_entry import _state


def initial_task():
    return updated_task_context(
        None,
        delta(
            0,
            "Export JSON",
            operation="start",
            objective="Export",
            conditions=[("format", "JSON")],
        ),
        user_input="Export JSON",
    )


@pytest.mark.parametrize("operation", ["pause", "complete", "resume"])
def test_extended_lifecycle_is_not_part_of_current_task_protocol(operation):
    task = initial_task()
    before = task.model_dump()
    with pytest.raises(ValidationError):
        updated_task_context(
            task, delta(1, "Change task", operation=operation), user_input="Change task"
        )
    assert task.model_dump() == before


@pytest.mark.parametrize(
    "extra",
    [
        {"history_revisions": [1]},
        {"restore_conditions": [{"key": "format", "reference": "0" * 64}]},
        {"target_task_id": "old-task", "target_revision": 1},
        {"conversation_scope_quote": "For the whole conversation"},
        {
            "set_conditions": [
                {"key": "format", "quote": "CSV", "scope": "conversation"}
            ]
        },
    ],
)
def test_extensions_cannot_be_silently_accepted_as_current_amendments(extra):
    task = initial_task()
    before = task.model_dump()
    patch = delta(1, "Use CSV", conditions=[("format", "CSV")])
    patch.update(extra)
    with pytest.raises(ValidationError):
        updated_task_context(task, patch, user_input="Use CSV")
    assert task.model_dump() == before


def test_repeated_corrections_store_only_current_value_and_preserve_other_keys():
    task = initial_task()
    for i in range(150):
        text = f"filename=report-{i}.json"
        task = updated_task_context(
            task,
            delta(task.revision, text, conditions=[("filename", text)]),
            user_input=text,
        )
    state = _state()
    state.resolved_task_context = task
    restored = WorkingState.model_validate(state.model_dump())
    assert restored.resolved_task_context.conditions == {
        "format": "JSON",
        "filename": "filename=report-149.json",
    }
    assert restored.resolved_task_context.revision == 151
    assert "history" not in restored.resolved_task_context.model_dump_json()
    specs, error = entry_task_tool_specs(
        [clarify_tool_spec()], restored, has_new_input=True
    )
    assert error is None and [spec.name for spec in specs] == ["clarify"]
    assert (
        not {"task_workspace", "task_execution_bindings"} & restored.model_dump().keys()
    )


def test_child_and_grandchild_keep_short_answer_context_without_parent_task_identity():
    task = updated_task_context(
        initial_task(),
        {
            **delta(1, "yes", conditions=[("width", "yes")]),
            "set_conditions": [
                {"key": "width", "quote": "yes", "question_quote": "Width 120 cm?"}
            ],
        },
        user_input="yes",
        clarification_question="Width 120 cm?",
    )
    parent = _state()
    parent.resolved_task_context = task
    before = parent.model_dump()
    child = parent.model_copy(deep=True)
    inherit_child_task(child, parent)
    child.goal = "Implement parser"
    grandchild = child.model_copy(deep=True)
    inherit_child_task(grandchild, child)
    grandchild.goal = "Check parser"
    text = execution_task_text(
        SimpleNamespace(state=grandchild, user_input="Check parser", logger=Mock())
    )
    assert all(
        value in text for value in ["Check parser", "JSON", "yes", "Width 120 cm?"]
    )
    assert (
        child.resolved_task_context is None and grandchild.resolved_task_context is None
    )
    grandchild.inherited_task_conditions.clear()
    assert child.inherited_task_conditions and parent.model_dump() == before
