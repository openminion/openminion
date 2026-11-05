"""Apply only historical values read in the same user-bound entry decision."""

from typing import Any
from openminion.modules.brain.schemas.task_context import ResolvedTaskContext, TaskConditionOwner
from .task_context import input_digest


def restore_task_conditions(state: Any, result: ResolvedTaskContext | None, patch: Any, reads: dict[str, Any], user_input: str) -> ResolvedTaskContext | None:
    if not patch or not patch.restore_conditions:
        return result
    if result is None or patch.operation not in {"update", "resume"}:
        raise ValueError("History restoration requires an existing or resumed task")
    keys = [item.key for item in patch.restore_conditions]
    if len(set(keys)) != len(keys) or set(keys) & ({item.key for item in patch.set_conditions} | set(patch.remove_conditions)):
        raise ValueError("Restored conditions conflict with other amendments")
    before = state.resolved_task_context
    binding = (before.task_id, before.revision) if before else None
    data = result.model_dump()
    for selection in patch.restore_conditions:
        receipt = reads.get(selection.reference)
        if not isinstance(receipt, dict) or receipt.get("input") != input_digest(user_input) or receipt.get("binding") != binding:
            raise ValueError("Historical evidence was not read for this user decision")
        item = receipt["entry"]
        if item["task_id"] != result.task_id or item["key"] != selection.key:
            raise ValueError("Historical evidence belongs to another task or condition")
        owner = result.condition_owners.get(selection.key)
        if owner is None:
            owner = TaskConditionOwner.model_validate(item["owner"])
            if owner.scope == "conversation":
                if not patch.conversation_scope_quote or patch.conversation_scope_quote not in user_input:
                    raise ValueError("Reinstating a removed conversation rule requires scope evidence")
                owner = owner.model_copy(update={"scope_quote": patch.conversation_scope_quote})
        if owner.scope == "subtask" and result.subtasks.get(str(owner.owner_id)) not in {"pending", "active"}:
            raise ValueError("Cannot reactivate a closed subtask condition")
        data["conditions"][selection.key] = item["quote"]
        data["sources"][selection.key] = item["source"]
        data["condition_owners"][selection.key] = owner.model_dump()
        data["condition_questions"].pop(selection.key, None)
        if item["question_quote"]:
            data["condition_questions"][selection.key] = item["question_quote"]
        data["restored_from"][selection.key] = item["revision"]
    data["lifecycle"] = "active"
    return ResolvedTaskContext.model_validate(data)
