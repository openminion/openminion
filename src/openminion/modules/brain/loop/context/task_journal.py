"""Accepted task history in the existing session log, with a committed-head chain."""

import json
from typing import Any

from openminion.modules.brain.schemas.task_context import ResolvedTaskContext, TaskConditionEvidence

TASK_CHECKPOINT_EVENT = "task.context.checkpoint"


def append_checkpoint(api: Any, state: Any, workspace: Any, *, before: ResolvedTaskContext | None, after: ResolvedTaskContext | None, operation: str, user_input: str) -> bool:
    append = getattr(api, "append_event", None)
    if not callable(append) or not callable(getattr(api, "list_events", None)):
        return False
    event_id = append(
        state.session_id, TASK_CHECKPOINT_EVENT,
        {"version": 1, "previous_event": workspace.journal_head,
         "operation": operation, "user_input": user_input,
         "before": before.model_dump(mode="json") if before else None,
         "after": after.model_dump(mode="json") if after else None},
        trace_id=state.trace_id,
    )
    if not isinstance(event_id, str) or not event_id:
        return False
    workspace.journal_head = event_id
    return True


def committed_checkpoints(api: Any, state: Any) -> list[dict[str, Any]]:
    read = getattr(api, "list_events", None)
    if not callable(read):
        return []
    records = read(state.session_id, event_type=TASK_CHECKPOINT_EVENT, limit=None)
    if not isinstance(records, list):
        return []
    index = {str(item.get("event_id")): item.get("payload", {}) for item in records if isinstance(item, dict)}
    head = state.task_workspace.journal_head
    result = []
    seen = set()
    while head and head not in seen:
        seen.add(head)
        record = index.get(head)
        if not isinstance(record, dict):
            raise ValueError("Committed task history is unavailable")
        result.append(record)
        head = record.get("previous_event")
    if head:
        raise ValueError("Committed task history contains a cycle")
    return result


def history_excerpts(api: Any, state: Any, *, task_id: str, revisions: list[int]) -> dict[str, str]:
    if len(set(revisions)) != len(revisions) or any(revision < 1 for revision in revisions):
        raise ValueError("History revisions must be unique positive revisions")
    wanted = set(revisions)
    result = {}
    for record in committed_checkpoints(api, state):
        for field in ("after", "before"):
            snapshot = record.get(field)
            if not isinstance(snapshot, dict) or snapshot.get("task_id") != task_id:
                continue
            revision = snapshot.get("revision")
            if revision in wanted and str(revision) not in result:
                result[str(revision)] = json.dumps({
                    "revision": revision, "conditions": snapshot.get("conditions", {}),
                    "answer_context": snapshot.get("condition_questions", {}),
                    "objective": snapshot.get("objective"),
                }, ensure_ascii=False, separators=(",", ":"))
    if len(result) != len(wanted):
        raise ValueError("Requested task history revision is unavailable")
    return result


def reference_cache(before: ResolvedTaskContext | None, after: ResolvedTaskContext) -> ResolvedTaskContext:
    """Small reference cache only; complete changes remain in the committed log."""
    if before is None or before.task_id != after.task_id:
        return after
    candidates = {key: list(items) for key, items in before.condition_history.items() if key in after.conditions}
    for key, value in before.conditions.items():
        if key in after.conditions and (value != after.conditions[key] or before.condition_questions.get(key) != after.condition_questions.get(key)):
            candidates.setdefault(key, []).append(TaskConditionEvidence(
                quote=value, question_quote=before.condition_questions.get(key),
                source=before.sources.get(key, before.consumed_input),
            ))
    result = after.model_copy(update={"condition_history": {}})
    remaining = min(2000, 12000 - _active_size(result))
    cache: dict[str, list[TaskConditionEvidence]] = {}
    for key, items in candidates.items():
        selected = items[:1] + (items[-1:] if len(items) > 1 and items[-1] != items[0] else [])
        size = len(key) + sum(len(item.quote) + len(item.question_quote or "") for item in selected)
        if size <= remaining:
            cache[key] = selected
            remaining -= size
    return ResolvedTaskContext.model_validate(result.model_dump() | {"condition_history": cache})


def _active_size(task: ResolvedTaskContext) -> int:
    return (len(task.objective) + sum(len(key) + len(value) for key, value in task.conditions.items())
            + sum(len(value) for value in task.condition_questions.values())
            + sum(len(owner.scope_quote or "") for owner in task.condition_owners.values())
            + sum(len(key) + len(value) for key, value in task.history_context.items()))
