"""Small task index and explicit conversation conditions for the entry model."""

import json
from typing import Any
from openminion.modules.llm.schemas import Message
from .task_context import task_context_message


def workspace_message(state: Any) -> Message:
    task = state.resolved_task_context
    text = (
        str(task_context_message(task).content)
        if task is not None
        else "CURRENT TASK: absent (revision 0)"
    )
    from openminion.modules.brain.schemas.task_workspace import TaskWorkspace

    workspace = getattr(state, "task_workspace", TaskWorkspace())
    data = {
        "archived": [
            {
                "task_id": item.task_id,
                "revision": item.revision,
                "lifecycle": item.lifecycle,
                "objective": item.objective[:160],
            }
            for item in workspace.archived.values()
        ],
        "detached_jobs": {
            key: {"task_id": item.binding.task_id, "revision": item.binding.revision}
            for key, item in workspace.detached_jobs.items()
        },
        "conversation_conditions": {
            key: item.quote for key, item in workspace.conversation_conditions.items()
        },
    }
    if (
        workspace.archived
        or workspace.conversation_conditions
        or workspace.detached_jobs
    ):
        text += "\nTASK WORKSPACE (user data):\n" + json.dumps(
            data, ensure_ascii=False, separators=(",", ":")
        )
    return Message(role="user", content=text, meta={"block_kind": "resolved_task"})
