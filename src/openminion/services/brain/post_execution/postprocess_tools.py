from __future__ import annotations

from typing import Any

from openminion.base.constants import STATE_KEY_WORKING
from openminion.base.types import Message

from .postprocess_sources import (
    _action_result_termination_reason,
    _tool_result_response_text,
    _tool_results_from_action_outputs,
)


def _resolve_command(*, step_out: Any) -> dict[str, Any] | None:
    action_result = getattr(step_out, "action_result", None)
    if action_result is None:
        return None
    command_id = str(getattr(action_result, "command_id", "")).strip()
    if not command_id:
        return None
    working_state = getattr(step_out, STATE_KEY_WORKING, None)
    plan = getattr(working_state, "plan", None)
    steps = getattr(plan, "steps", None)
    if not isinstance(steps, list):
        return None
    for step in steps:
        step_id = str(getattr(step, "command_id", "")).strip()
        if step_id == command_id and hasattr(step, "model_dump"):
            return step.model_dump(mode="json")
    return None


def _active_mode_name_from_step(step_out: Any) -> str | None:
    return (
        str(
            getattr(getattr(step_out, STATE_KEY_WORKING, None), "active_mode_name", "")
            or ""
        )
        .strip()
        .lower()
        or None
    )


async def _apply_tool_result_postprocess(
    self,
    *,
    step_out: Any,
    message: Message,
    session_id: str,
    turn_id: str,
    active_mode_name: str | None,
    response_text: str,
    termination_reason: str,
) -> tuple[str, str, list[dict[str, Any]]]:
    action_result = getattr(step_out, "action_result", None)
    explicit_termination_reason = _action_result_termination_reason(action_result)
    if action_result is not None:
        aggregated_tool_results = _tool_results_from_action_outputs(
            action_result=action_result
        )
        if aggregated_tool_results:
            if self._telemetryctl:
                for item in aggregated_tool_results:
                    tool_name = (
                        str(item.get("tool_name", "") or "").strip() or "unknown"
                    )
                    await self._telemetryctl.emit_tool_call(
                        session_id,
                        turn_id,
                        tool_name,
                        bool(item.get("ok")),
                        active_mode_name,
                    )
            if not all(bool(item.get("ok")) for item in aggregated_tool_results):
                termination_reason = explicit_termination_reason or "tool_no_success"
            response_text = _tool_result_response_text(
                response_text=response_text,
                tool_results_payload=aggregated_tool_results,
            )
            return response_text, termination_reason, aggregated_tool_results
    command = self._resolve_command(step_out=step_out)
    if not (
        action_result is not None
        and isinstance(command, dict)
        and command.get("kind") == "tool"
    ):
        return response_text, termination_reason, []

    tool_result = self._tool_result_from_action(
        command=command,
        action_result=action_result,
    )
    tool_results_payload = [tool_result]

    if self._telemetryctl:
        tool_name = command.get("tool_name", "unknown")
        await self._telemetryctl.emit_tool_call(
            session_id,
            turn_id,
            tool_name,
            bool(tool_result.get("ok")),
            active_mode_name,
        )

    if bool(tool_result.get("ok")):
        termination_reason = "tool_final"
    else:
        termination_reason = explicit_termination_reason or "tool_no_success"
    response_text = _tool_result_response_text(
        response_text=response_text,
        tool_results_payload=tool_results_payload,
    )
    return response_text, termination_reason, tool_results_payload
