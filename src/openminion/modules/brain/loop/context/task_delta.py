"""Validate atomic changes to current task conditions."""

from typing import Any
from openminion.modules.brain.schemas.task_context import (
    ResolvedTaskContext,
    TaskContextUpdate,
)


def apply_condition_delta(
    current: ResolvedTaskContext | None,
    patch: TaskContextUpdate,
    *,
    user_input: str,
    question: str,
    digest: str,
) -> dict[str, Any]:
    keep = current is not None and patch.operation == "update"
    data = {
        name: dict(getattr(current, name)) if keep else {}
        for name in ("conditions", "sources", "condition_questions")
    }
    keys = [item.key for item in patch.set_conditions]
    if len(keys) != len(set(keys)) or len(patch.remove_conditions) != len(
        set(patch.remove_conditions)
    ):
        raise ValueError("Task update contains duplicate condition keys")
    if set(keys) & set(patch.remove_conditions):
        raise ValueError("A condition cannot be set and removed together")
    verbatim = (
        current.objective
        if current is not None and keep and current.objective_kind == "verbatim"
        else ""
    )
    for key in patch.remove_conditions:
        if key not in data["conditions"]:
            raise ValueError("Cannot remove an unknown condition")
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
        data["conditions"][item.key] = item.quote
        data["condition_questions"].pop(item.key, None)
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
    return data
