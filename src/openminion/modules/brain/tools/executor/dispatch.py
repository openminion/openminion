from typing import TYPE_CHECKING, Any

from ...diagnostics.events import CanonicalEventLogger
from ...constants import BRAIN_COMMAND_KIND_TOOL, STATE_KEY_MODULE_STATE
from ...execution.public_taxonomy import public_surface_payload_for_state
from ...schemas import (
    ActionResult,
    Command,
    JobHandle,
    WorkingState,
)
from .think import execute_think

# ``execute_action_dispatch`` (cross-kind routing body)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ...runner import BrainRunner


def _command_lineage_payload(
    *,
    state: WorkingState,
    command: Any,
) -> dict[str, Any]:
    """Build the typed lineage payload emitted alongside executor events."""
    plan = getattr(state, "plan", None)
    step_total = len(getattr(plan, "steps", []) or [])
    step_index: int | None = None
    if step_total > 0:
        step_index = min(max(0, int(getattr(state, "cursor", 0) or 0)) + 1, step_total)
    public_surface = public_surface_payload_for_state(state)
    public_mode_name = str(public_surface.pop("mode_name", "") or "").strip() or None
    payload = {
        "runtime_session_id": str(
            getattr(state, "runtime_session_id", "") or ""
        ).strip()
        or None,
        "decision_mode": public_mode_name,
        "mode_name": public_mode_name,
        "workflow_name": str(getattr(state, "active_workflow_name", "") or "").strip()
        or None,
        "workflow_kind": str(getattr(state, "active_workflow_kind", "") or "").strip()
        or None,
        "step_index": step_index,
        "step_total": step_total or None,
        "command_id": str(getattr(command, "command_id", "") or "").strip() or None,
        "command_kind": str(getattr(command, "kind", "") or "").strip() or None,
        "task_backed_task_id": str(
            getattr(state, "task_backed_task_id", "") or ""
        ).strip()
        or None,
    }
    if getattr(command, "kind", "") == BRAIN_COMMAND_KIND_TOOL:
        payload["tool_name"] = (
            str(getattr(command, "tool_name", "") or "").strip() or None
        )
    payload.update(
        {
            key: value
            for key, value in public_surface.items()
            if value is not None and str(value).strip() != ""
        }
    )
    return {key: value for key, value in payload.items() if value is not None}


def _inject_runtime_tool_metadata(
    payload: dict[str, Any],
    *,
    state: WorkingState,
    lineage: dict[str, Any],
) -> None:
    """Carry runtime-owned child isolation context across worker threads."""
    meta = payload.get("meta")
    if not isinstance(meta, dict):
        meta = {}
        payload["meta"] = meta
    meta["orchestration"] = dict(lineage)
    module_state = getattr(state, STATE_KEY_MODULE_STATE, None)
    worktree_state = (
        module_state.get("worktree_children")
        if isinstance(module_state, dict)
        else None
    )
    child_state = (
        worktree_state.get("worktree_child")
        if isinstance(worktree_state, dict)
        else None
    )
    workspace_root = (
        str(child_state.get("workspace") or "").strip()
        if isinstance(child_state, dict)
        else ""
    )
    if not workspace_root:
        return
    meta["runtime_execution"] = {"workspace_root": workspace_root}


def execute_action(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    command: Command,
    logger: CanonicalEventLogger,
) -> tuple[ActionResult, JobHandle | None]:
    """Cross-kind executor entry point."""
    if command.kind == "think":
        return execute_think(
            runner,
            state=state,
            command=command,
            logger=logger,
        )
    # Lazy imports to break the load-time cycle documented at module top.
    from ..action_dispatch import execute_action_dispatch
    from .tool import sanitize_tool_command_args

    return execute_action_dispatch(
        runner,
        state=state,
        command=command,
        logger=logger,
        sanitize_tool_command_args=sanitize_tool_command_args,
        execute_action_fn=execute_action,
    )


__all__ = [
    "_command_lineage_payload",
    "_inject_runtime_tool_metadata",
    "execute_action",
]
