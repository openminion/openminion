from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from openminion.modules.brain.execution.task_context import (
    execution_messages,
)
from openminion.modules.brain.execution.task_binding import inherit_child_task
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


def test_repeated_corrections_keep_current_values_and_durable_evidence(tmp_path):
    from openminion.modules.brain.adapters.session.runtime import SessctlAdapter
    from openminion.modules.brain.loop.context.task_journal import committed_checkpoints
    from tests.brain.test_task_workspace import commit

    state = _state()
    path = tmp_path / "sessions.db"
    api = SessctlAdapter(path)
    try:
        commit(
            state,
            "Export JSON",
            api,
            op="start",
            objective="Export",
            conditions=[{"key": "format", "quote": "JSON"}],
        )
        for i in range(150):
            text = f"filename=report-{i}.json"
            commit(state, text, api, conditions=[{"key": "filename", "quote": text}])
        restored = WorkingState.model_validate_json(state.model_dump_json())
    finally:
        api.close()
    api = SessctlAdapter(path)
    try:
        assert restored.resolved_task_context.conditions == {
            "format": "JSON",
            "filename": "filename=report-149.json",
        }
        assert restored.resolved_task_context.revision == 151
        assert len(restored.resolved_task_context.condition_history["filename"]) <= 2
        assert len(committed_checkpoints(api, restored)) == 151
        specs, error = entry_task_tool_specs(
            [clarify_tool_spec()], restored, has_new_input=True
        )
        assert error is None and [spec.name for spec in specs] == [
            "clarify",
            "task.history.search",
        ]
        assert (
            not restored.task_workspace.archived
            and not restored.task_workspace.detached_jobs
        )
    finally:
        api.close()


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
    from openminion.modules.brain.execution.child_tasks import SubtaskSpec
    from openminion.modules.brain.execution.orchestrate.strategies import (
        SummaryInheritancePolicy,
    )

    prompt = (
        SummaryInheritancePolicy()
        .build_child_context(
            parent_state=child,
            subtask=SubtaskSpec(goal="Check parser"),
        )
        .prompt
    )
    messages = execution_messages(
        SimpleNamespace(state=grandchild, user_input=prompt, logger=Mock())
    )
    text = messages[-1].content
    assert text.count("JSON") == 1 and text.count("Width 120 cm?") == 1
    assert all(
        value in text for value in ["Check parser", "JSON", "yes", "Width 120 cm?"]
    )
    assert (
        child.resolved_task_context is None and grandchild.resolved_task_context is None
    )
    assert grandchild.inherited_task_binding == child.inherited_task_binding
    assert grandchild.inherited_task_binding is not child.inherited_task_binding
    revision = child.inherited_task_binding.revision
    grandchild.inherited_task_binding.revision += 1
    assert child.inherited_task_binding.revision == revision
    grandchild.inherited_task_conditions.clear()
    assert child.inherited_task_conditions and parent.model_dump() == before


@pytest.mark.parametrize("nested", [False, True])
def test_phase_child_uses_the_same_single_condition_rendering(monkeypatch, nested):
    from openminion.modules.brain.execution.loop_contracts import ExecutionResult
    from openminion.modules.brain.loop.tools.phases import child_execution
    from openminion.modules.brain.loop import orchestration

    parent = _state()
    parent.resolved_task_context = initial_task()
    if nested:
        root = parent
        parent = root.model_copy(deep=True)
        inherit_child_task(parent, root)
        parent.goal = "Parent parser work"
    before = parent.model_dump()
    child = child_execution.build_child_state(
        parent_state=parent,
        child_budget=parent.budgets_remaining,
        goal="Check parser",
    )
    runner = SimpleNamespace(context_api=None, session_api=None)
    ctx = SimpleNamespace(
        state=parent, logger=Mock(), _services=SimpleNamespace(runner=runner)
    )
    monkeypatch.setattr(
        orchestration,
        "decide",
        lambda *args, **kwargs: SimpleNamespace(route="act_adaptive"),
    )
    captured = []

    def invoke(runner, **kwargs):
        captured.extend(
            execution_messages(
                SimpleNamespace(
                    state=kwargs["state"],
                    user_input=kwargs["user_input"],
                    logger=Mock(),
                    _services=SimpleNamespace(runner=runner),
                )
            )
        )
        return ExecutionResult(
            status="done", working_state=kwargs["state"], message="checked"
        )

    monkeypatch.setattr(child_execution, "invoke_decision_direct", invoke)
    assert (
        child_execution.execute_child_goal(
            ctx,
            child_goal="Check parser",
            child_state=child,
            blocked_mode_name="refine",
        )
        == "checked"
    )
    text = captured[-1].content
    assert text.count("JSON") == 1 and text.count("Check parser") == 1
    assert ("Parent parser work" if nested else "Export") in text
    assert child.goal == "Check parser" and parent.model_dump() == before


def test_persisted_core_task_upgrades_without_inventing_prior_history(tmp_path):
    from openminion.modules.brain.adapters.session.runtime import SessctlAdapter
    from openminion.modules.brain.loop.context.task_search import search_task_history
    from tests.brain.test_task_workspace import commit

    data = _state().model_dump(mode="json")
    for field in [
        "task_workspace",
        "task_execution_bindings",
        "inherited_task_binding",
    ]:
        data.pop(field, None)
    original = initial_task()
    data["resolved_task_context"] = {
        key: value
        for key, value in original.model_dump(mode="json").items()
        if key
        in {
            "revision",
            "task_id",
            "objective",
            "objective_kind",
            "conditions",
            "condition_questions",
            "sources",
            "consumed_input",
        }
    }
    data["resolved_task_context"]["revision"] = 7
    state = WorkingState.model_validate(data)
    assert state.resolved_task_context.conditions == {"format": "JSON"}
    assert state.resolved_task_context.lifecycle == "active"
    assert (
        not state.task_workspace.archived and state.task_workspace.journal_head is None
    )
    api = SessctlAdapter(tmp_path / "sessions.db")
    try:
        commit(state, "Use CSV", api, conditions=[{"key": "format", "quote": "CSV"}])
        current = WorkingState.model_validate_json(state.model_dump_json())
        prior = search_task_history(
            api, current, {"key": "format", "selection": "revision", "revision": 7}
        )
        assert prior["entries"][0]["quote"] == "JSON"
        unknown = search_task_history(
            api, current, {"key": "format", "selection": "first"}
        )
        assert not unknown["complete_history"] and unknown["status"] == "unavailable"
        assert current.resolved_task_context.conditions == {"format": "CSV"}
    finally:
        api.close()


def test_upgrade_keeps_the_core_answer_context_owner_invariant():
    from pydantic import ValidationError

    data = initial_task().model_dump()
    data["condition_questions"] = {"missing-condition": "Use width 120?"}
    with pytest.raises(ValidationError, match="Answer context has no condition"):
        type(initial_task()).model_validate(data)


@pytest.mark.parametrize(
    "operation", ["update", "replace", "cancel", "pause", "complete", "resume"]
)
def test_legacy_pending_job_without_binding_keeps_existing_owner(operation):
    from openminion.modules.brain.schemas import JobHandle
    from tests.brain.test_task_workspace import commit

    state = _state()
    state.resolved_task_context = initial_task()
    state.pending_jobs = [
        JobHandle(
            task_id="legacy", command_id="command", provider="tool", status="pending"
        )
    ]
    before = state.model_dump()
    with pytest.raises(ValueError, match="task owner"):
        commit(
            state,
            "Change task",
            op=operation,
            objective="Replacement" if operation == "replace" else None,
            target_task_id="archived" if operation == "resume" else None,
            target_revision=1 if operation == "resume" else None,
        )
    assert state.model_dump() == before


@pytest.mark.parametrize(
    "operation", ["replace", "cancel", "pause", "complete", "resume"]
)
def test_task_backed_lifecycle_transition_keeps_existing_owner(operation):
    from tests.brain.test_task_workspace import commit

    state = _state()
    state.resolved_task_context = initial_task()
    state.task_backed_task_id = "external-task"
    before = state.model_dump()
    with pytest.raises(ValueError, match="task owner"):
        commit(
            state,
            "Change task",
            op=operation,
            objective="Replacement" if operation == "replace" else None,
            target_task_id="archived" if operation == "resume" else None,
            target_revision=1 if operation == "resume" else None,
        )
    assert state.model_dump() == before


def test_nested_task_keeps_ancestor_conditions_and_renders_overrides_once():
    from openminion.modules.brain.execution.child_tasks import SubtaskSpec
    from openminion.modules.brain.execution.orchestrate.strategies import (
        SummaryInheritancePolicy,
    )

    root = _state()
    root.resolved_task_context = updated_task_context(
        initial_task(),
        delta(1, "source.csv", conditions=[("source", "source.csv")]),
        user_input="source.csv",
    )
    child = root.model_copy(deep=True)
    inherit_child_task(child, root)
    child.goal = "Prepare intermediate"
    child.resolved_task_context = updated_task_context(
        None,
        delta(
            0,
            "Output CSV",
            operation="start",
            objective="Prepare intermediate",
            conditions=[("format", "CSV")],
        ),
        user_input="Output CSV",
    )
    before = child.model_dump()
    grandchild = child.model_copy(deep=True)
    inherit_child_task(grandchild, child)
    prompt = (
        SummaryInheritancePolicy()
        .build_child_context(
            parent_state=child, subtask=SubtaskSpec(goal="Check intermediate")
        )
        .prompt
    )
    messages = execution_messages(
        SimpleNamespace(state=grandchild, user_input=prompt, logger=Mock())
    )
    text = messages[-1].content
    assert (
        text.count("CSV") == 1 and text.count("source.csv") == 1 and "JSON" not in text
    )
    assert "Check intermediate" in text and "Prepare intermediate" in text
    assert grandchild.inherited_task_conditions == {
        "format": "CSV",
        "source": "source.csv",
    }
    assert child.model_dump() == before
