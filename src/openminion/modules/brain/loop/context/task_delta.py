"""Atomic condition-delta validation shared by task lifecycle operations."""

from typing import Any

from openminion.modules.brain.schemas.task_context import (
    ResolvedTaskContext,
    TaskConditionEvidence,
    TaskContextUpdate,
)
from .task_ownership import condition_owner, owner_for


def apply_condition_delta(
    current: ResolvedTaskContext | None,
    patch: TaskContextUpdate,
    *,
    user_input: str,
    question: str,
    digest: str,
    subtasks: dict[str, Any],
    retain_history: bool,
) -> dict[str, Any]:
    is_new = patch.operation in {"start", "replace"}
    data = _condition_data(current, is_new=is_new, retain_history=retain_history)
    keys = [item.key for item in patch.set_conditions]
    if len(keys) != len(set(keys)) or len(patch.remove_conditions) != len(
        set(patch.remove_conditions)
    ):
        raise ValueError("Task update contains duplicate condition keys")
    if set(keys) & set(patch.remove_conditions):
        raise ValueError("A condition cannot be set and removed together")
    verbatim = (
        current.objective
        if current is not None
        and current.objective_kind == "verbatim"
        and patch.operation == "update"
        else ""
    )
    for key in patch.remove_conditions:
        if key not in data["conditions"]:
            raise ValueError("Cannot remove an unknown condition")
        if current is not None and owner_for(current, key).scope == "conversation":
            if (
                not patch.conversation_scope_quote
                or patch.conversation_scope_quote not in user_input
            ):
                raise ValueError(
                    "Withdrawing a conversation condition requires current-user evidence"
                )
        for field in data.values():
            field.pop(key, None)
    for item in patch.set_conditions:
        if not item.quote.strip() or (
            item.quote not in user_input and item.quote not in verbatim
        ):
            raise ValueError("A condition must quote the latest user input")
        if item.question_quote is not None and item.question_quote != question:
            raise ValueError(
                "Answer context must match the pending clarification question"
            )
        owner = condition_owner(
            item,
            data["condition_owners"].get(item.key),
            user_input=user_input,
            subtasks=subtasks,
        )
        previous = data["conditions"].get(item.key)
        previous_question = data["condition_questions"].pop(item.key, None)
        if (
            retain_history
            and previous is not None
            and (previous != item.quote or previous_question != item.question_quote)
        ):
            data["condition_history"].setdefault(item.key, []).append(
                TaskConditionEvidence(
                    quote=previous,
                    question_quote=previous_question,
                    source=data["sources"].get(
                        item.key, current.consumed_input if current else digest
                    ),
                )
            )
        data["restored_from"].pop(item.key, None)
        data["condition_owners"][item.key] = owner
        data["conditions"][item.key] = item.quote
        if item.question_quote:
            data["condition_questions"][item.key] = item.question_quote
        data["sources"][item.key] = (
            digest
            if item.quote in user_input
            else (
                current.sources.get("$objective", current.consumed_input)
                if current
                else digest
            )
        )
    if patch.objective:
        data["sources"]["$objective"] = digest
    elif current is not None and not is_new and "$objective" in current.sources:
        data["sources"]["$objective"] = current.sources["$objective"]
    return data


def _condition_data(
    current: ResolvedTaskContext | None, *, is_new: bool, retain_history: bool
) -> dict[str, Any]:
    keep = set(current.conditions) if current is not None and not is_new else set()
    if current is not None and is_new:
        keep = {
            key
            for key in current.conditions
            if owner_for(current, key).scope == "conversation"
        }
    data: dict[str, Any] = {
        name: {
            key: value for key, value in getattr(current, name).items() if key in keep
        }
        if current is not None
        else {}
        for name in (
            "conditions",
            "sources",
            "condition_questions",
            "condition_owners",
            "restored_from",
        )
    }
    data["condition_history"] = (
        {
            key: list(items)
            for key, items in current.condition_history.items()
            if key in keep
        }
        if current is not None and retain_history
        else {}
    )
    return data
