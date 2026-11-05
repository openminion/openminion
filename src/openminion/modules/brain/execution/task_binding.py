"""Execution provenance and isolated child projections of task conditions."""

from functools import wraps
from typing import Any
from openminion.modules.brain.schemas.task_workspace import TaskExecutionBinding, TaskWorkspace, DetachedTaskExecution
from openminion.modules.brain.loop.context.task_ownership import scoped_task, visible_condition_keys


def capture_binding(state: Any, subtask_id: str | None = None) -> TaskExecutionBinding | None:
    task = getattr(state, "resolved_task_context", None)
    if task is None:
        return None
    return TaskExecutionBinding(task_id=task.task_id, revision=task.revision, subtask_id=subtask_id)


def binding_matches(state: Any, binding: TaskExecutionBinding | None) -> bool:
    if binding is None:
        return state.resolved_task_context is None
    task = getattr(state, "resolved_task_context", None)
    return bool(task is not None and task.task_id == binding.task_id and task.revision == binding.revision and task.lifecycle == "active")


def inherit_child_task(child: Any, parent: Any, subtask_id: str | None = None) -> None:
    task = getattr(parent, "resolved_task_context", None)
    child.resolved_task_context = None
    child.task_context_needs_update = False
    child.task_workspace = TaskWorkspace()
    child.task_execution_bindings = {}
    child.inherited_task_binding = capture_binding(parent, subtask_id)
    child.inherited_task_conditions = {}
    if task is not None:
        # An unregistered/unknown child receives shared conditions only.
        keys = visible_condition_keys(task, subtask_id=subtask_id or "__unassigned__")
        child.inherited_task_conditions = {key: task.conditions[key] for key in task.conditions if key in keys}


def parent_task_background(parent: Any, subtask_id: str) -> str:
    from openminion.modules.brain.loop.context.task_context import task_context_message
    task = getattr(parent, "resolved_task_context", None)
    if task is None:
        return str(parent.goal or "")
    return str(task_context_message(scoped_task(task, subtask_id)).content)


def register_subtask(state: Any, subtask_id: str) -> TaskExecutionBinding | None:
    task = getattr(state, "resolved_task_context", None)
    if task is not None:
        data = task.model_dump()
        data["subtasks"][subtask_id] = "active"
        state.resolved_task_context = type(task).model_validate(data)
    return capture_binding(state, subtask_id)


def finish_subtask(state: Any, binding: TaskExecutionBinding | None, *, completed: bool) -> bool:
    if not binding_matches(state, binding):
        return False
    task = getattr(state, "resolved_task_context", None)
    if task is not None and binding is not None and binding.subtask_id:
        task.subtasks[binding.subtask_id] = "completed" if completed else "cancelled"
    return True


def bound_subtask(method: Any) -> Any:
    @wraps(method)
    def run(self: Any, *, ctx: Any, subtask: Any, **kwargs: Any) -> Any:
        binding = register_subtask(ctx.state, subtask.subtask_id)
        result = method(self, ctx=ctx, subtask=subtask, **kwargs)
        if not finish_subtask(ctx.state, binding, completed=result.result.status == "completed"):
            result = result.model_copy(update={"result": result.result.model_copy(update={"status": "cancelled", "output": "", "error": "Task changed before the child result was accepted"})})
        return result
    return run


def register_job_binding(state: Any, job_id: str) -> None:
    binding = capture_binding(state)
    if binding is not None:
        state.task_execution_bindings[job_id] = binding


def discard_stale_jobs(state: Any) -> None:
    stale = {job.task_id for job in state.pending_jobs if job.task_id in state.task_execution_bindings and not binding_matches(state, state.task_execution_bindings[job.task_id])}
    workspace = state.task_workspace.model_copy(deep=True)
    for job in state.pending_jobs:
        if job.task_id in stale:
            workspace.detached_jobs[job.task_id] = DetachedTaskExecution(binding=state.task_execution_bindings[job.task_id], handle=job.model_dump(mode="json"))
    workspace = TaskWorkspace.model_validate(workspace.model_dump())
    state.task_workspace = workspace
    state.pending_jobs = [job for job in state.pending_jobs if job.task_id not in stale]
    stale.update(set(state.task_execution_bindings) - {job.task_id for job in state.pending_jobs})
    for job_id in stale:
        state.task_execution_bindings.pop(job_id, None)


def complete_task(state: Any) -> None:
    task = getattr(state, "resolved_task_context", None)
    if task is not None and task.lifecycle == "active":
        state.resolved_task_context = task.model_copy(update={"lifecycle": "completed"})
