from __future__ import annotations

from typing import Any

from openminion.modules.brain.constants import BRAIN_ACTION_STATUS_SUCCESS
from openminion.modules.brain.execution.child_tasks import (
    DecomposeControlPayload,
)
from openminion.modules.brain.schemas import ActionResult, new_uuid

from .contracts import (
    ADAPTIVE_TERM_DECOMPOSE_INVALID,
    AdaptiveToolLoopContext,
    AdaptiveToolLoopOutcome,
    AdaptiveToolLoopProfile,
    AdaptiveToolLoopState,
)
from .status import emit_adaptive_status


_DECOMPOSE_TOOL_NAME = "decompose"


def _decompose_tool_calls(tool_calls: list[Any]) -> list[Any]:
    return [
        call
        for call in tool_calls
        if str(getattr(call, "name", "") or "").strip() == _DECOMPOSE_TOOL_NAME
    ]


def _subtasks_from_decompose_control(
    payload: DecomposeControlPayload,
) -> list[dict[str, Any]]:
    return [
        {
            "subtask_id": item.id,
            "goal": item.description,
            "inputs": dict(item.inputs),
            "depends_on": list(item.depends_on),
            "suggested_mode": item.suggested_mode,
            "priority": item.priority,
        }
        for item in payload.subtasks
    ]


def _decompose_invalid_outcome(
    *,
    loop_ctx: AdaptiveToolLoopContext,
    profile: AdaptiveToolLoopProfile,
    loop_state: AdaptiveToolLoopState,
    allowed_tools: frozenset[str],
    public_mode_tag: str,
    reason: str,
    message: str,
) -> AdaptiveToolLoopOutcome:
    scratchpad = dict(loop_state.scratchpad or {})
    scratchpad["adaptive.decompose_error"] = {
        "reason": reason,
        "message": message,
    }
    loop_state.scratchpad = scratchpad
    loop_state.termination_reason = ADAPTIVE_TERM_DECOMPOSE_INVALID
    emit_adaptive_status(
        loop_ctx,
        profile=profile,
        loop_state=loop_state,
        detail_text=f"{public_mode_tag} decompose invalid",
        mode_state="decompose_invalid",
        termination_reason=ADAPTIVE_TERM_DECOMPOSE_INVALID,
        extra={"reason": reason},
    )
    return AdaptiveToolLoopOutcome(
        profile_name=profile.profile_name,
        mode_name=profile.mode_name,
        termination_reason=ADAPTIVE_TERM_DECOMPOSE_INVALID,
        state=loop_state,
        allowed_tools=allowed_tools,
        error_message=message,
        tool_name=_DECOMPOSE_TOOL_NAME,
    )


def _decompose_decline_result() -> ActionResult:
    return ActionResult(
        command_id=new_uuid(),
        status=BRAIN_ACTION_STATUS_SUCCESS,
        summary="decompose declined: no subtasks were provided.",
        outputs={"subtask_count": 0, "declined": True},
    )
