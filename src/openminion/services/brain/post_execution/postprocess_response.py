from __future__ import annotations

import json
import time
from typing import Any

from openminion.base.config.core import resolve_default_agent_id
from openminion.base.types import AgentResponse, Message
from openminion.modules.brain.runner import BrainRunner
from openminion.services.agent.execution.finalization import (
    extract_finalization_status_from_text,
    unwrap_final_answer_envelope,
)

from .postprocess_metadata import (
    _attach_cumulative_tool_result_metadata,
    _attach_delegation_result_metadata,
    _attach_structured_action_output_metadata,
    _attach_watch_outcome_metadata,
)
from .postprocess_sources import _cumulative_tool_results_from_step_output


async def _postprocess_turn(
    self,
    *,
    runner: BrainRunner,
    step_out: Any,
    message: Message,
    history: list[Message] | None,
    session_id: str,
    request_id: str | None,
    turn_id: str,
    turn_start_time: float,
) -> AgentResponse:
    del history
    active_mode_name = self._active_mode_name_from_step(step_out)
    llm_steps = int(getattr(step_out.working_state, "llm_calls_used", 0) or 0)
    is_pae_idle_tick_noop = bool(getattr(step_out, "pae_idle_tick_noop", False))
    (
        response_text,
        termination_reason,
        tool_results_payload,
    ) = await _prepare_response_text_and_tool_results(
        self,
        step_out=step_out,
        message=message,
        session_id=session_id,
        turn_id=turn_id,
        active_mode_name=active_mode_name,
    )
    response_text, finalization_payload = _normalize_final_response_text(
        response_text=response_text,
        is_pae_idle_tick_noop=is_pae_idle_tick_noop,
    )
    elapsed_ms = (time.time() - turn_start_time) * 1000
    if self._telemetryctl:
        await self._telemetryctl.emit_tick(
            session_id,
            turn_id,
            elapsed_ms,
            active_mode_name,
        )
    metadata = _build_postprocess_response_metadata(
        self,
        runner=runner,
        step_out=step_out,
        session_id=session_id,
        request_id=request_id,
        elapsed_ms=elapsed_ms,
        llm_steps=llm_steps,
        termination_reason=termination_reason,
        tool_results_payload=tool_results_payload,
        finalization_payload=finalization_payload,
        response_text=response_text,
        is_pae_idle_tick_noop=is_pae_idle_tick_noop,
    )
    return _agent_response_from_postprocess(
        self,
        message=message,
        metadata=metadata,
        response_text=response_text,
        is_pae_idle_tick_noop=is_pae_idle_tick_noop,
    )


async def _prepare_response_text_and_tool_results(
    self,
    *,
    step_out: Any,
    message: Message,
    session_id: str,
    turn_id: str,
    active_mode_name: str | None,
) -> tuple[str, str, list[dict[str, Any]]]:
    response_text = str(step_out.message or "").strip()
    termination_reason = (
        "brain_completion" if step_out.status == "stopped" else "model_final"
    )
    return await self._apply_tool_result_postprocess(
        step_out=step_out,
        message=message,
        session_id=session_id,
        turn_id=turn_id,
        active_mode_name=active_mode_name,
        response_text=response_text,
        termination_reason=termination_reason,
    )


def _normalize_final_response_text(
    *,
    response_text: str,
    is_pae_idle_tick_noop: bool,
) -> tuple[str, dict[str, Any] | None]:
    if not response_text and not is_pae_idle_tick_noop:
        response_text = "No response generated."
    extracted_finalization = extract_finalization_status_from_text(response_text)
    if extracted_finalization is not None:
        response_text, finalization_payload = extracted_finalization
    else:
        finalization_payload = None
    unwrapped_envelope = unwrap_final_answer_envelope(response_text)
    if unwrapped_envelope is None:
        return response_text, finalization_payload
    response_text, envelope_payload = unwrapped_envelope
    if finalization_payload is None:
        finalization_payload = {
            "status": envelope_payload["status"],
            "reasoning": envelope_payload["summary"],
            "remaining_work": "",
            "blocking_reason": "",
        }
    return response_text, finalization_payload


def _build_postprocess_response_metadata(
    self,
    *,
    runner: BrainRunner,
    step_out: Any,
    session_id: str,
    request_id: str | None,
    elapsed_ms: float,
    llm_steps: int,
    termination_reason: str,
    tool_results_payload: list[dict[str, Any]],
    finalization_payload: dict[str, Any] | None,
    response_text: str,
    is_pae_idle_tick_noop: bool,
) -> dict[str, str]:
    metadata = self._build_turn_response_metadata(
        runner=runner,
        step_out=step_out,
        session_id=session_id,
        request_id=request_id,
        elapsed_ms=elapsed_ms,
        llm_steps=llm_steps,
        termination_reason=termination_reason,
    )
    metadata["respond_kind"] = str(getattr(step_out, "kind", "") or "assistant")
    metadata.update(self._identity_metadata())
    memory_policy_metadata = self._extract_memory_policy_metadata(
        response_text=response_text
    )
    if memory_policy_metadata:
        metadata.update(memory_policy_metadata)
    _attach_finish_reason(
        metadata=metadata,
        step_out=step_out,
        tool_results_payload=tool_results_payload,
        termination_reason=termination_reason,
    )
    self._attach_clarify_request_metadata(
        metadata=metadata,
        clarify_request=self._build_clarify_request_payload(
            step_out=step_out,
            session_id=session_id,
            trace_id=request_id,
        ),
    )
    _attach_telemetry_flag(self, metadata=metadata)
    _attach_postprocess_action_metadata(
        self,
        metadata=metadata,
        step_out=step_out,
        tool_results_payload=tool_results_payload,
        termination_reason=termination_reason,
        finalization_payload=finalization_payload,
    )
    if is_pae_idle_tick_noop:
        metadata["pae_idle_tick_noop"] = "true"
        metadata["finish_reason"] = "pae_idle_tick_noop"
    return metadata


def _attach_finish_reason(
    *,
    metadata: dict[str, str],
    step_out: Any,
    tool_results_payload: list[dict[str, Any]],
    termination_reason: str,
) -> None:
    status_lower = str(getattr(step_out, "status", "")).strip().lower()
    if tool_results_payload and termination_reason == "tool_final":
        metadata["finish_reason"] = "tool_calls"
    elif status_lower in {"error", "failed"}:
        metadata["finish_reason"] = "error"
    else:
        metadata["finish_reason"] = "stop"


def _attach_telemetry_flag(self, *, metadata: dict[str, str]) -> None:
    if self._telemetryctl:
        metadata["telemetry_recorded"] = "true"


def _attach_postprocess_action_metadata(
    self,
    *,
    metadata: dict[str, str],
    step_out: Any,
    tool_results_payload: list[dict[str, Any]],
    termination_reason: str,
    finalization_payload: dict[str, Any] | None,
) -> None:
    self._attach_tool_result_metadata(
        metadata=metadata,
        tool_results_payload=tool_results_payload,
        termination_reason=termination_reason,
    )
    cumulative_tool_results_payload = _cumulative_tool_results_from_step_output(
        step_out=step_out,
        tool_results_payload=tool_results_payload,
    )
    _attach_cumulative_tool_result_metadata(
        metadata=metadata,
        tool_results_payload=cumulative_tool_results_payload,
    )
    _attach_structured_action_output_metadata(
        metadata=metadata,
        action_result=getattr(step_out, "action_result", None),
    )
    if (
        finalization_payload is not None
        and "adaptive.finalization_status" not in metadata
    ):
        metadata["adaptive.finalization_status"] = json.dumps(
            finalization_payload,
            sort_keys=True,
        )
    _attach_watch_outcome_metadata(
        metadata=metadata,
        action_result=getattr(step_out, "action_result", None),
    )
    _attach_delegation_result_metadata(
        metadata=metadata,
        action_result=getattr(step_out, "action_result", None),
    )


def _agent_response_from_postprocess(
    self,
    *,
    message: Message,
    metadata: dict[str, str],
    response_text: str,
    is_pae_idle_tick_noop: bool,
) -> AgentResponse:
    if is_pae_idle_tick_noop:
        response_envelope_text = ""
    else:
        default_agent_id = resolve_default_agent_id(self._config)
        agent_name = self._config.agents[default_agent_id].name or default_agent_id
        response_envelope_text = f"{agent_name}: {response_text}"
    return AgentResponse(
        text=response_envelope_text,
        channel=message.channel,
        target=message.target,
        metadata=metadata,
    )
