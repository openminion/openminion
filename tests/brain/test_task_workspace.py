"""Task ownership, lifecycle, durable evidence and stale-result boundaries."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from openminion.modules.brain.adapters.session.runtime import SessctlAdapter
from openminion.modules.brain.execution.task_binding import (
    binding_matches, capture_binding, discard_stale_jobs, finish_subtask,
    inherit_child_task, register_job_binding, register_subtask,
)
from openminion.modules.brain.loop.context.task_context import task_context_message
from openminion.modules.brain.loop.context.task_entry import apply_entry_task_context, entry_task_preflight
from openminion.modules.brain.loop.context.task_journal import committed_checkpoints, history_excerpts
from openminion.modules.brain.loop.context.task_ownership import visible_condition_keys
from openminion.modules.brain.loop.context.task_projection import workspace_message
from openminion.modules.brain.loop.context.task_workspace import workspace_entry_update
from openminion.modules.brain.schemas import JobHandle
from openminion.modules.brain.schemas.task_workspace import TaskWorkspace
from tests.brain.test_unified_turn_entry import _state, _tool_response


def update(state, text, *, op="update", objective=None, conditions=(), **extra):
    return dict(operation=op, expected_revision=state.resolved_task_context.revision if state.resolved_task_context else 0,
                user_quote=text, objective=objective, set_conditions=list(conditions), **extra)


def commit(state, text, api=None, *, op="update", objective=None, conditions=(), **extra):
    patch = update(state, text, op=op, objective=objective, conditions=conditions, **extra)
    response = _tool_response("respond", {"answer": "OK", "task_update": patch})
    task, workspace, operation = workspace_entry_update(state, response, user_input=text, clarify=False, recovery_only=False, session_api=api)
    state.resolved_task_context, state.task_workspace = task, workspace
    return task


def new_state():
    state = _state()
    commit(state, "Build report JSON", op="start", objective="Build report", conditions=[dict(key="format", quote="JSON")])
    return state


def test_old_working_states_get_isolated_defaults():
    state = new_state()
    raw = state.model_dump()
    for key in ("task_workspace", "task_execution_bindings", "inherited_task_conditions", "inherited_task_binding"):
        raw.pop(key)
    for key in ("task_id", "lifecycle", "condition_owners", "subtasks", "history_available", "history_context"):
        raw["resolved_task_context"].pop(key)
    restored = type(state).model_validate(raw)
    assert restored.resolved_task_context.lifecycle == "active"
    assert restored.task_workspace == TaskWorkspace()
    assert visible_condition_keys(restored.resolved_task_context) == {"format"}


@pytest.mark.parametrize("scope,owner", [("conversation", None), ("subtask", "unknown")])
def test_unproven_scope_or_unknown_owner_rejected_atomically(scope, owner):
    state = new_state()
    before = state.model_dump()
    with pytest.raises(ValueError):
        commit(state, "Keep JSON", conditions=[dict(key="format", quote="JSON", scope=scope, owner_id=owner)])
    assert state.model_dump() == before


def test_global_survives_replacement_but_local_format_does_not():
    state = new_state()
    commit(state, "Throughout this conversation speak Russian", conditions=[dict(key="language", quote="speak Russian", scope="conversation", scope_quote="Throughout this conversation")])
    first_id = state.resolved_task_context.task_id
    second = commit(state, "Now build CSV", op="replace", objective="Build CSV", conditions=[dict(key="format", quote="CSV")])
    assert second.task_id != first_id
    assert second.conditions == {"language": "speak Russian", "format": "CSV"}
    assert state.task_workspace.archived[first_id].lifecycle == "paused"
    assert "TASK WORKSPACE" in workspace_message(state).content


@pytest.mark.parametrize("status", ["pause", "complete", "cancel"])
def test_lifecycle_then_new_task_and_restore(status):
    state = new_state()
    task_id = state.resolved_task_context.task_id
    commit(state, status, op=status)
    target = state.resolved_task_context or state.task_workspace.archived[task_id]
    target_revision = target.revision
    if status != "cancel":
        assert entry_task_preflight(state, has_new_input=False).reason_code == "task_inactive"
    commit(state, "Make CSV", op="replace" if state.resolved_task_context else "start", objective="Make CSV")
    restored = commit(state, "Return to report", op="resume", target_task_id=task_id, target_revision=target_revision)
    assert restored.task_id == task_id and restored.lifecycle == "active"
    assert restored.revision == target_revision + 1
    assert restored.conditions == {"format": "JSON"}


@pytest.mark.parametrize("target,revision", [("missing", 1), (None, 999)])
def test_resume_unknown_or_wrong_revision_is_atomic(target, revision):
    state = new_state()
    before = state.model_dump()
    with pytest.raises(ValueError):
        commit(state, "Resume", op="resume", target_task_id=target or state.resolved_task_context.task_id, target_revision=revision)
    assert state.model_dump() == before


def test_restoring_old_task_uses_current_conversation_rules():
    state = new_state()
    commit(state, "All conversation: Russian", conditions=[dict(key="lang", quote="Russian", scope="conversation", scope_quote="All conversation")])
    first = state.resolved_task_context.model_copy(deep=True)
    commit(state, "Make CSV", op="replace", objective="Make CSV")
    commit(state, "All conversation: English", conditions=[dict(key="lang", quote="English")])
    restored = commit(state, "Resume report", op="resume", target_task_id=first.task_id, target_revision=first.revision)
    assert restored.conditions["lang"] == "English"
    assert restored.conditions["format"] == "JSON"


def test_removal_of_global_requires_current_scope_quote():
    state = new_state()
    commit(state, "Every task: Russian", conditions=[dict(key="lang", quote="Russian", scope="conversation", scope_quote="Every task")])
    with pytest.raises(ValueError):
        commit(state, "Remove language", remove_conditions=["lang"])
    commit(state, "Remove language for every task", remove_conditions=["lang"], conversation_scope_quote="for every task")
    assert not state.task_workspace.conversation_conditions


def test_subtasks_receive_shared_and_own_conditions_only():
    state = new_state()
    state.resolved_task_context.subtasks = {"a": "pending", "b": "pending"}
    commit(state, "Parser A only; UI B only; do not change input", conditions=[
        dict(key="a-rule", quote="Parser A only", scope="subtask", owner_id="a"),
        dict(key="b-rule", quote="UI B only", scope="subtask", owner_id="b"),
        dict(key="shared", quote="do not change input"),
    ])
    child = state.model_copy(deep=True)
    inherit_child_task(child, state, "a")
    assert set(child.inherited_task_conditions) == {"format", "shared", "a-rule"}
    assert not child.task_workspace.archived and not child.task_execution_bindings
    binding = register_subtask(state, "a")
    assert finish_subtask(state, binding, completed=True)
    assert "a-rule" not in visible_condition_keys(state.resolved_task_context)
    assert "a-rule" not in task_context_message(state.resolved_task_context).content
    assert "shared" in visible_condition_keys(state.resolved_task_context)
    assert state.resolved_task_context.conditions["a-rule"] == "Parser A only"


@pytest.mark.parametrize("change", ["replace", "update", "pause", "complete", "cancel"])
def test_old_binding_cannot_accept_result_after_task_transition(change):
    state = new_state()
    binding = capture_binding(state)
    state.pending_jobs = [JobHandle(task_id="old-job", command_id="old-command", provider="tool", status="pending")]
    register_job_binding(state, "old-job")
    if change == "cancel":
        with pytest.raises(ValueError):
            commit(state, "Cancel", op="cancel")
        state.pending_jobs = []
    commit(state, "Change CSV", op=change, objective="Make CSV" if change == "replace" else None,
           conditions=[dict(key="format", quote="CSV")] if change == "update" else [])
    before = (state.goal, state.cursor, deepcopy(state.last_result))
    assert not binding_matches(state, binding)
    assert not finish_subtask(state, binding, completed=True)
    discard_stale_jobs(state)
    assert not state.pending_jobs and not state.task_execution_bindings
    assert (state.goal, state.cursor, state.last_result) == before


def test_identical_replacement_text_has_new_identity():
    state = new_state()
    old = capture_binding(state)
    commit(state, "Build report JSON", op="replace", objective="Build report", conditions=[dict(key="format", quote="JSON")])
    assert not binding_matches(state, old)


def test_real_sqlite_history_survives_long_chain_restart_and_reads_exact_revision(tmp_path):
    api = SessctlAdapter(tmp_path / "sessions.db")
    state = _state()
    commit(state, "Build JSON", api, op="start", objective="Build report", conditions=[dict(key="format", quote="JSON")])
    task_id = state.resolved_task_context.task_id
    for i in range(150):
        value = f"format-{i}-" + "x" * 80
        commit(state, value, api, conditions=[dict(key="format", quote=value)])
    assert state.resolved_task_context.revision == 151
    assert len(state.resolved_task_context.condition_history["format"]) <= 2
    restored = type(state).model_validate_json(state.model_dump_json())
    api.close()
    api = SessctlAdapter(tmp_path / "sessions.db")
    try:
        records = committed_checkpoints(api, restored)
        assert len(records) == 151
        assert records[-1]["user_input"] == "Build JSON"
        excerpts = history_excerpts(api, restored, task_id=task_id, revisions=[1, 51])
        assert '"format":"JSON"' in excerpts["1"]
        assert "format-49-" in excerpts["51"]
        commit(restored, "Show earlier formats", api, history_revisions=[1, 51])
        assert restored.resolved_task_context.history_context == excerpts
        assert "format-149-" in restored.resolved_task_context.conditions["format"]
        api.append_event(state.session_id, "task.context.checkpoint", {"version": 1, "previous_event": state.task_workspace.journal_head, "after": None})
        assert len(committed_checkpoints(api, state)) == 151
    finally:
        api.close()


def test_journal_failure_blocks_entry_execution_and_keeps_committed_state():
    state = new_state()
    before = state.model_dump()
    api = SimpleNamespace(append_event=Mock(side_effect=OSError("disk full")), list_events=Mock(return_value=[]))
    response = _tool_response("tool", {"name": "file.write", "task_update": update(state, "CSV", conditions=[dict(key="format", quote="CSV")])})
    error = apply_entry_task_context(state, response, user_input="CSV", clarify=False, logger=Mock(), session_api=api)
    assert error.reason_code == "invalid_task_context_update"
    assert state.resolved_task_context.model_dump() == before["resolved_task_context"]
    assert state.task_workspace.model_dump() == before["task_workspace"]


def test_legacy_store_without_event_id_preserves_full_history():
    state = new_state()
    api = SimpleNamespace(append_event=lambda *args, **kwargs: None)
    for i in range(4):
        commit(state, f"CSV{i}", api, conditions=[dict(key="format", quote=f"CSV{i}")])
    assert len(state.resolved_task_context.condition_history["format"]) == 4
    assert not state.resolved_task_context.history_available


def test_real_reconciler_ignores_old_job_without_polling_or_advancing_plan():
    from openminion.modules.brain.execution.runtime.turn.jobs import reconcile_pending_jobs
    state = new_state()
    job = JobHandle(task_id="old", command_id="old-command", provider="tool", status="pending")
    state.pending_jobs = [job]
    register_job_binding(state, job.task_id)
    commit(state, "Create CSV", op="replace", objective="Create CSV")
    state.status = "job_pending"
    before = (state.cursor, state.goal, state.last_result)
    runner = Mock()
    assert reconcile_pending_jobs(runner, state=state, logger=Mock()) is None
    runner.tool_api.poll_task.assert_not_called()
    assert (state.cursor, state.goal, state.last_result) == before


def test_entry_pause_commits_and_detaches_execution():
    state = new_state()
    response = _tool_response("respond", {"answer": "Paused", "task_update": update(state, "Pause", op="pause")})
    decision = apply_entry_task_context(state, response, user_input="Pause", clarify=False, logger=Mock())
    assert decision.reason_code == "entry_task_pause"
    assert state.resolved_task_context.lifecycle == "paused" and state.plan is None


def test_history_lookup_cannot_execute_before_reading_it(tmp_path):
    state = new_state()
    response = _tool_response("tool", {"name": "file.write", "task_update": update(state, "Use first format", history_revisions=[1])})
    before = state.resolved_task_context.model_dump()
    decision = apply_entry_task_context(state, response, user_input="Use first format", clarify=False, logger=Mock())
    assert decision.reason_code == "invalid_task_context_update"
    assert state.resolved_task_context.model_dump() == before


def test_archive_capacity_refuses_transition_without_eviction():
    state = new_state()
    for i in range(64):
        commit(state, f"Task {i}", op="replace", objective=f"Task {i}")
    before = state.model_dump()
    with pytest.raises(ValueError):
        commit(state, "One too many", op="replace", objective="One too many")
    assert state.model_dump() == before


def test_child_background_does_not_leak_other_subtask_history():
    from openminion.modules.brain.execution.task_binding import parent_task_background
    state = new_state()
    state.resolved_task_context.subtasks = {"a": "active", "b": "active"}
    commit(state, "UI rule", conditions=[dict(key="b", quote="UI rule", scope="subtask", owner_id="b")])
    state.resolved_task_context.history_context = {"1": '{"b":"old UI secret"}'}
    background = parent_task_background(state, "a")
    assert "UI rule" not in background and "old UI secret" not in background
    assert "Build report" in background and "JSON" in background


def test_detached_jobs_keep_their_owner_and_external_handle():
    state = new_state()
    old_id = state.resolved_task_context.task_id
    job = JobHandle(task_id="external-1", command_id="command-1", provider="tool", status="running")
    state.pending_jobs = [job]
    register_job_binding(state, job.task_id)
    commit(state, "New task", op="replace", objective="New task")
    discard_stale_jobs(state)
    saved = type(state).model_validate_json(state.model_dump_json())
    parked = saved.task_workspace.detached_jobs[job.task_id]
    assert parked.binding.task_id == old_id
    assert parked.handle == job.model_dump(mode="json")
    assert not saved.pending_jobs


def test_resume_applies_amendments_to_target_without_new_task_leakage():
    state = new_state()
    target = state.resolved_task_context.model_copy(deep=True)
    commit(state, "Make CSV with UI", op="replace", objective="Make CSV", conditions=[dict(key="ui", quote="UI")])
    restored = commit(state, "Return to report, now XML", op="resume", target_task_id=target.task_id,
                      target_revision=target.revision, conditions=[dict(key="format", quote="XML")])
    assert restored.conditions == {"format": "XML"}
    assert restored.task_id == target.task_id and restored.revision == target.revision + 1


def test_invalid_resume_amendment_does_not_move_archive():
    state = new_state()
    target = state.resolved_task_context.model_copy(deep=True)
    commit(state, "Make CSV", op="replace", objective="Make CSV")
    before = state.model_dump()
    with pytest.raises(ValueError):
        commit(state, "Return to report", op="resume", target_task_id=target.task_id,
               target_revision=target.revision, conditions=[dict(key="format", quote="invented XML")])
    assert state.model_dump() == before
