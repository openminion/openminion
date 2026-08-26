from __future__ import annotations

from typing import Any, Mapping

from openminion.modules.brain.diagnostics.status import (
    PhaseStatus,
    StatusKey,
    coerce_phase_status,
)

from .tool_calls import format_public_tool_activity


STATUS_MESSAGES_EN: dict[StatusKey, str] = {
    "clarifying": "Making sure I understand...",
    "analyzing": "Reviewing your request...",
    "planning": "Planning the next steps...",
    "awaiting_plan_review": "Ready for you to review the plan.",
    "awaiting_confirmation": "Waiting for your approval...",
    "executing": "Working on it...",
    "replanning": "Adjusting the plan...",
    "reviewing": "Reviewing the results...",
    "verifying": "Checking the result...",
    "evaluating_completion": "Checking whether everything is complete...",
    "saving_context": "Saving your progress...",
    "waiting_for_user": "Waiting for your reply...",
    "completed": "Done.",
    "blocked": "Unable to continue yet.",
    "error": "Something went wrong.",
    "working": "Working on it...",
}

DETAIL_MESSAGES_EN = {
    "plan_checkpoint": "Finished step {step_index} of {step_total}.",
    "preparing_turn": "Getting ready...",
    "loading_memory_context": "Loading relevant context...",
    "loading_session_history": "Reviewing this conversation...",
    "thinking": "Thinking...",
    "composing_answer": "Preparing the answer...",
}

_PRIMARY_ONLY_KEYS = frozenset(
    {
        "awaiting_plan_review",
        "awaiting_confirmation",
        "waiting_for_user",
        "completed",
        "blocked",
        "error",
    }
)


def format_public_status_text(
    status: PhaseStatus | Mapping[str, Any] | None,
) -> str:
    phase_status = coerce_phase_status(status)
    primary = STATUS_MESSAGES_EN.get(phase_status.status_key, "Working on it...")
    if phase_status.status_key in _PRIMARY_ONLY_KEYS:
        return primary

    detail_code = str(phase_status.detail_code or "")
    detail_template = DETAIL_MESSAGES_EN.get(detail_code)
    if detail_template:
        values = {
            "step_index": phase_status.step_index,
            "step_total": phase_status.step_total,
        }
        valid_step = (
            phase_status.step_index is not None
            and phase_status.step_total is not None
            and 1 <= phase_status.step_index <= phase_status.step_total
        )
        if detail_code != "plan_checkpoint" or valid_step:
            return detail_template.format(**values)

    if phase_status.tool_name:
        return format_public_tool_activity(phase_status.tool_name, pending=True)

    if (
        phase_status.status_key == "executing"
        and phase_status.step_index is not None
        and phase_status.step_total is not None
        and 1 <= phase_status.step_index <= phase_status.step_total
    ):
        return (
            f"Working on step {phase_status.step_index} "
            f"of {phase_status.step_total}..."
        )
    return primary


__all__ = [
    "DETAIL_MESSAGES_EN",
    "STATUS_MESSAGES_EN",
    "format_public_status_text",
]
