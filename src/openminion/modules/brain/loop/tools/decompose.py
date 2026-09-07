from __future__ import annotations

from typing import Any

from openminion.modules.brain.constants import BRAIN_ACTION_STATUS_SUCCESS
from openminion.modules.brain.execution.child_tasks import (
    DecomposeControlPayload,
)
from openminion.modules.brain.schemas import ActionResult, new_uuid
from openminion.modules.llm.schemas import Message

from .contracts import (
    ADAPTIVE_TERM_DECOMPOSE_INVALID,
    AdaptiveToolLoopContext,
    AdaptiveToolLoopOutcome,
    AdaptiveToolLoopProfile,
    AdaptiveToolLoopState,
)
from .status import emit_adaptive_status
from .messages import action_result_to_tool_message
from .transcript import persist_blocked_tool_calls


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
    loop_state.scratchpad["adaptive.decompose_error"] = {
        "reason": reason,
        "message": message,
    }
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


def _handle_invalid_decompose_payload(
    loop_ctx: AdaptiveToolLoopContext,
    *,
    profile: AdaptiveToolLoopProfile,
    loop_state: AdaptiveToolLoopState,
    decompose_calls: list[Any],
    allowed_tools: frozenset[str],
    public_mode_tag: str,
    error_message: str,
) -> AdaptiveToolLoopOutcome | None:
    blocked_results = persist_blocked_tool_calls(
        loop_ctx,
        loop_state=loop_state,
        turn_scope_id=str(getattr(loop_ctx.state, "trace_id", "") or ""),
        tool_calls=decompose_calls,
        code="INVALID_DECOMPOSE_PAYLOAD",
        message=error_message,
    )
    loop_state.messages.extend(
        action_result_to_tool_message(call.id, call.name, result)
        for call, result in zip(decompose_calls, blocked_results, strict=True)
    )
    if bool(loop_state.scratchpad.get("decompose_invalid_retry_used")):
        return _decompose_invalid_outcome(
            loop_ctx=loop_ctx,
            profile=profile,
            loop_state=loop_state,
            allowed_tools=allowed_tools,
            public_mode_tag=public_mode_tag,
            reason="invalid_payload",
            message=error_message,
        )
    loop_state.scratchpad["decompose_invalid_retry_used"] = True
    loop_state.messages.append(
        Message(
            role="system",
            content=(
                "The decompose payload was invalid. Retry decompose once with "
                "either zero subtasks or at least two valid subtasks. "
                f"Validation error: {error_message}"
            ),
        )
    )
    emit_adaptive_status(
        loop_ctx,
        profile=profile,
        loop_state=loop_state,
        detail_text=f"{public_mode_tag} decompose payload retry",
        mode_state="decompose_payload_retry",
    )
    return None


def _decompose_decline_result() -> ActionResult:
    return ActionResult(
        command_id=new_uuid(),
        status=BRAIN_ACTION_STATUS_SUCCESS,
        summary="decompose declined: no subtasks were provided.",
        outputs={"subtask_count": 0, "declined": True},
    )
