"""Explicit condition ownership and projections; no importance or wording guesses."""

from typing import Any

from openminion.modules.brain.schemas.task_context import (
    ResolvedTaskContext, TaskCondition, TaskConditionOwner,
)


def owner_for(task: ResolvedTaskContext, key: str) -> TaskConditionOwner:
    return task.condition_owners.get(key, TaskConditionOwner())


def condition_owner(item: TaskCondition, previous: TaskConditionOwner | None, *, user_input: str, subtasks: dict[str, Any]) -> TaskConditionOwner:
    scope = item.scope or (previous.scope if previous else "task")
    owner_id = item.owner_id if item.scope is not None else (item.owner_id or (previous.owner_id if previous else None))
    quote = item.scope_quote or (previous.scope_quote if previous else None)
    if scope == "conversation" and (previous is None or previous.scope != scope):
        if not item.scope_quote or item.scope_quote not in user_input:
            raise ValueError("Conversation scope requires explicit current-user scope evidence")
    if previous is not None and previous.scope == "conversation" and scope != previous.scope:
        if not item.scope_quote or item.scope_quote not in user_input:
            raise ValueError("Changing conversation scope requires current-user evidence")
    if item.scope_quote and item.scope_quote not in user_input:
        raise ValueError("Scope evidence must quote the current user")
    if scope == "subtask" and owner_id not in subtasks:
        raise ValueError("A subtask condition requires a registered owner")
    if scope != "subtask" and owner_id is not None:
        raise ValueError("Only subtask scope takes owner_id")
    return TaskConditionOwner(scope=scope, owner_id=owner_id, scope_quote=quote)


def visible_condition_keys(task: ResolvedTaskContext, *, subtask_id: str | None = None) -> set[str]:
    keys = set()
    for key in task.conditions:
        owner = owner_for(task, key)
        if owner.scope != "subtask":
            keys.add(key)
        elif task.subtasks.get(str(owner.owner_id)) in {"pending", "active"} and (
            subtask_id is None or owner.owner_id == subtask_id
        ):
            keys.add(key)
    return keys


def scoped_task(task: ResolvedTaskContext, subtask_id: str) -> ResolvedTaskContext:
    keys = visible_condition_keys(task, subtask_id=subtask_id)
    return task.model_copy(update={
        "conditions": {key: value for key, value in task.conditions.items() if key in keys},
        "condition_questions": {key: value for key, value in task.condition_questions.items() if key in keys},
        "condition_history": {key: value for key, value in task.condition_history.items() if key in keys},
        "condition_owners": {key: value for key, value in task.condition_owners.items() if key in keys},
        "sources": {key: value for key, value in task.sources.items() if key in keys or key == "$objective"},
        "history_context": {},
        "restored_from": {key: value for key, value in task.restored_from.items() if key in keys},
    })
