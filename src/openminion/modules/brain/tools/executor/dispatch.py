from typing import TYPE_CHECKING, Any

from ...diagnostics.events import CanonicalEventLogger
from ...constants import BRAIN_COMMAND_KIND_TOOL
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
    "execute_action",
]
