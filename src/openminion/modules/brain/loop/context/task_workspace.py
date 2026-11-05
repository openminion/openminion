"""Apply one foreground transition together with conversation and archive state."""

from copy import deepcopy
from typing import Any

from openminion.modules.brain.schemas.task_context import (
    ResolvedTaskContext,
    TaskConditionEvidence,
    TaskConditionOwner,
    TaskContextUpdate,
)
from openminion.modules.brain.schemas.task_workspace import TaskWorkspace
from .task_context import input_digest, resolve_entry_task_update, updated_task_context
from .task_journal import append_checkpoint, history_excerpts, reference_cache
from .task_ownership import owner_for
from .task_restore import restore_task_conditions


def _workspace(state: Any) -> TaskWorkspace:
    return getattr(state, "task_workspace", TaskWorkspace()).model_copy(deep=True)


def _archive(
    workspace: TaskWorkspace, task: ResolvedTaskContext, *, lifecycle: str | None = None
) -> None:
    snapshot = task.model_copy(deep=True)
    if lifecycle is not None:
        snapshot.lifecycle = lifecycle
    workspace.archived[task.task_id] = snapshot


def _conversation_snapshot(
    task: ResolvedTaskContext | None, workspace: TaskWorkspace
) -> ResolvedTaskContext | None:
    if task is None:
        return None
    data = task.model_dump()
    for key in list(data["conditions"]):
        if owner_for(task, key).scope == "conversation":
            for field in (
                "conditions",
                "condition_questions",
                "condition_owners",
                "condition_history",
                "sources",
                "restored_from",
            ):
                data[field].pop(key, None)
    for key, item in workspace.conversation_conditions.items():
        if key in data["conditions"]:
            raise ValueError(
                "Conversation and task condition keys conflict; use distinct keys"
            )
        data["conditions"][key] = item.quote
        data["sources"][key] = item.source
        data["condition_owners"][key] = TaskConditionOwner(
            scope="conversation"
        ).model_dump()
        if item.question_quote:
            data["condition_questions"][key] = item.question_quote
    return ResolvedTaskContext.model_validate(data)


def _sync_conversation(workspace: TaskWorkspace, task: ResolvedTaskContext) -> None:
    workspace.conversation_conditions = {
        key: TaskConditionEvidence(
            quote=value,
            question_quote=task.condition_questions.get(key),
            source=task.sources.get(key, task.consumed_input),
        )
        for key, value in task.conditions.items()
        if owner_for(task, key).scope == "conversation"
    }


def selected_subtasks(state: Any, response: Any) -> dict[str, Any]:
    task = state.resolved_task_context
    known = dict(task.subtasks) if task else {}
    for key in getattr(state, "child_tasks", {}):
        known.setdefault(key, "active")
    for call in response.tool_calls or []:
        if call.name == "decompose":
            for subtask in call.arguments.get("subtasks", []):
                if isinstance(subtask, dict) and isinstance(subtask.get("id"), str):
                    known[subtask["id"]] = "pending"
    return known


def workspace_entry_update(
    state: Any,
    response: Any,
    *,
    user_input: str | None,
    clarify: bool,
    recovery_only: bool,
    session_api: Any,
    history_reads: dict[str, Any] | None = None,
) -> tuple[ResolvedTaskContext | None, TaskWorkspace, str]:
    workspace = _workspace(state)
    before = state.resolved_task_context
    original_response = deepcopy(response)
    patches = [
        call.arguments["task_update"]
        for call in response.tool_calls or []
        if "task_update" in call.arguments
    ]
    patch = TaskContextUpdate.model_validate(patches[0]) if len(patches) == 1 else None
    operation = patch.operation if patch else "update"
    shadow = deepcopy(state)
    shadow.task_workspace = workspace
    journal_available = callable(
        getattr(session_api, "append_event", None)
    ) and callable(getattr(session_api, "list_events", None))
    if patch and operation == "resume":
        if recovery_only:
            raise ValueError("Cannot resume while the foreground context is withheld")
        result = _resume_task(
            state,
            workspace,
            patch,
            user_input=str(user_input or ""),
            retain_history=not journal_available,
        )
        if len(response.tool_calls) != 1:
            raise ValueError("Resume must select one entry action")
        response.tool_calls[0].arguments = {
            key: value
            for key, value in response.tool_calls[0].arguments.items()
            if key != "task_update"
        }
        response.tool_calls[0].raw_arguments = None
    else:
        result = resolve_entry_task_update(
            shadow,
            response,
            user_input=user_input,
            clarify=clarify,
            recovery_only=recovery_only,
            known_subtasks=selected_subtasks(state, response),
            retain_history=not journal_available,
            allow_restore=True,
        )
        if result is not None:
            if before is None and workspace.conversation_conditions:
                # New task keys must not silently shadow session-wide requirements.
                if set(result.conditions) & set(workspace.conversation_conditions):
                    raise ValueError(
                        "Conversation condition key conflicts with the new task"
                    )
                result = _conversation_snapshot(result, workspace)
            _sync_conversation(workspace, result)
        if before and (result is None or result.task_id != before.task_id):
            _archive(
                workspace,
                before,
                lifecycle="cancelled"
                if result is None
                else ("paused" if before.lifecycle == "active" else before.lifecycle),
            )
    result = restore_task_conditions(
        state, result, patch, history_reads or {}, str(user_input or "")
    )
    if result is not None:
        _sync_conversation(workspace, result)
    if (
        result is not None
        and result.lifecycle != "active"
        and any(call.name not in {"respond", "clarify"} for call in response.tool_calls)
    ):
        raise ValueError("An inactive task must be resumed or amended before execution")
    result = _with_requested_history(state, result, patch, response, session_api)
    result, workspace = _persist_entry_update(
        state,
        workspace,
        result,
        before=before,
        operation=operation,
        user_input=user_input,
        session_api=session_api,
        shadow=shadow,
        original_response=original_response,
        clarify=clarify,
        recovery_only=recovery_only,
        patch=patch,
        history_reads=history_reads,
    )
    return result, workspace, operation


def _with_requested_history(
    state: Any,
    result: ResolvedTaskContext | None,
    patch: TaskContextUpdate | None,
    response: Any,
    session_api: Any,
) -> ResolvedTaskContext | None:
    if patch and patch.target_task_id is not None and patch.operation != "resume":
        raise ValueError("Only resume takes a target task")
    if patch and patch.history_revisions:
        if any(call.name not in {"respond", "clarify"} for call in response.tool_calls):
            raise ValueError("Read requested history before selecting execution")
        if result is None:
            raise ValueError("Cannot attach history to an absent task")
        result = result.model_copy(
            update={
                "history_context": history_excerpts(
                    session_api,
                    state,
                    task_id=result.task_id,
                    revisions=patch.history_revisions,
                )
            }
        )
        result = ResolvedTaskContext.model_validate(result.model_dump())
    return result


def _persist_entry_update(
    state: Any,
    workspace: TaskWorkspace,
    result: ResolvedTaskContext | None,
    *,
    before: ResolvedTaskContext | None,
    operation: str,
    user_input: str | None,
    session_api: Any,
    shadow: Any,
    original_response: Any,
    clarify: bool,
    recovery_only: bool,
    patch: TaskContextUpdate | None,
    history_reads: dict[str, Any] | None,
) -> tuple[ResolvedTaskContext | None, TaskWorkspace]:
    from openminion.modules.brain.execution.task_binding import plan_job_detachment

    workspace = TaskWorkspace.model_validate(workspace.model_dump())
    workspace, _ = plan_job_detachment(state, workspace, result)
    try:
        recorded = bool(user_input) and append_checkpoint(
            session_api,
            state,
            workspace,
            before=before,
            after=result,
            operation=operation,
            user_input=str(user_input),
        )
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        raise ValueError("Task history could not be persisted") from exc
    if recorded:
        if result is not None:
            result = reference_cache(before, result).model_copy(
                update={"history_available": True}
            )
    elif user_input and operation != "resume":
        # Legacy stores must retain all evidence in bounded state or refuse overflow.
        result = resolve_entry_task_update(
            shadow,
            original_response,
            user_input=user_input,
            clarify=clarify,
            recovery_only=recovery_only,
            known_subtasks=selected_subtasks(state, original_response),
            retain_history=True,
            allow_restore=True,
        )
        result = restore_task_conditions(
            state, result, patch, history_reads or {}, str(user_input or "")
        )
        if before is None and result is not None and workspace.conversation_conditions:
            result = _conversation_snapshot(result, workspace)
    return result, workspace


def _resume_task(
    state: Any,
    workspace: TaskWorkspace,
    patch: TaskContextUpdate,
    *,
    user_input: str,
    retain_history: bool,
) -> ResolvedTaskContext:
    from openminion.modules.brain.execution.task_binding import (
        require_task_transition_owner,
    )

    require_task_transition_owner(state, "resume")
    current = state.resolved_task_context
    if (
        patch.expected_revision != (current.revision if current else 0)
        or not patch.user_quote.strip()
        or patch.user_quote not in user_input
    ):
        raise ValueError(
            "Resume must match the current revision and quote the current user"
        )
    if not patch.target_task_id:
        raise ValueError("Resume requires a task identity")
    target = (
        current
        if current and current.task_id == patch.target_task_id
        else workspace.archived.get(patch.target_task_id)
    )
    if target is None or target.revision != patch.target_revision:
        raise ValueError("Resume target or target revision does not match")
    workspace.archived.pop(target.task_id, None)
    if current and current.task_id != target.task_id:
        _archive(
            workspace,
            current,
            lifecycle="paused" if current.lifecycle == "active" else current.lifecycle,
        )
    restored = _conversation_snapshot(target, workspace)
    assert restored is not None
    restored = restored.model_copy(
        update={"lifecycle": "active", "history_context": {}}
    )
    if patch.objective or patch.set_conditions or patch.remove_conditions:
        amendments = patch.model_copy(
            update={
                "operation": "update",
                "expected_revision": target.revision,
                "target_task_id": None,
                "target_revision": None,
                "history_revisions": [],
            }
        )
        result = updated_task_context(
            restored,
            amendments.model_dump(),
            user_input=user_input,
            retain_history=retain_history,
            clarification_question=str(
                getattr(
                    getattr(state, "pending_llm_clarify_context", None),
                    "clarify_question",
                    "",
                )
                or ""
            ),
            allow_restore=True,
        )
        assert result is not None
        _sync_conversation(workspace, result)
        return result
    return restored.model_copy(
        update={
            "revision": target.revision + 1,
            "consumed_input": input_digest(user_input),
        }
    )
