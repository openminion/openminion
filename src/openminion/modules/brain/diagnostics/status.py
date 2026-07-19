from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from openminion.modules.tool.contracts.display_names import (
    display_name_for_tool_name,
)

from ..constants import (
    BRAIN_STATE_ACTIVE,
    BRAIN_STATE_DONE,
    BRAIN_STATE_ERROR,
    BRAIN_STATE_JOB_PENDING,
    BRAIN_STATE_STOPPED,
    BRAIN_STATE_WAITING_USER,
)

StatusKey = Literal[
    "clarifying",
    "analyzing",
    "planning",
    "awaiting_plan_review",
    "awaiting_confirmation",
    "executing",
    "replanning",
    "reviewing",
    "verifying",
    "evaluating_completion",
    "saving_context",
    "waiting_for_user",
    "completed",
    "blocked",
    "error",
    "working",
]

_STATUS_LABELS: dict[StatusKey, str] = {
    "clarifying": "Clarifying request...",
    "analyzing": "Analyzing request...",
    "planning": "Planning steps...",
    "awaiting_plan_review": "Waiting for plan review...",
    "awaiting_confirmation": "Waiting for confirmation...",
    "executing": "Executing step...",
    "replanning": "Replanning with new evidence...",
    "reviewing": "Reviewing results...",
    "verifying": "Verifying results...",
    "evaluating_completion": "Evaluating results...",
    "saving_context": "Saving context...",
    "waiting_for_user": "Waiting for your reply...",
    "completed": "Completed.",
    "blocked": "Blocked.",
    "error": "Turn failed.",
    "working": "Working...",
}

_PHASE_STATUS_MAP: dict[str, StatusKey] = {
    "CLARIFY": "clarifying",
    "DECIDE": "analyzing",
    "PLAN": "planning",
    "APPROVE": "awaiting_confirmation",
    "ACT": "executing",
    "OBSERVE": "reviewing",
    "REFLECT": "reviewing",
    "IMPROVE": "reviewing",
    "VERIFY": "verifying",
    "COMPACT": "saving_context",
    "RESPOND": "working",
}

_EVENT_STATUS_MAP: dict[str, StatusKey] = {
    "brain.plan_checkpoint": "executing",
}

_EVENT_PREFIX_STATUS_MAP: tuple[tuple[str, StatusKey], ...] = (
    ("brain.clarify.", "clarifying"),
    ("brain.entry", "analyzing"),
    ("brain.closure_gate.", "evaluating_completion"),
)

_RUNTIME_STATUS_MAP: dict[str, StatusKey] = {
    "started": "working",
    "working": "working",
    BRAIN_STATE_WAITING_USER: "waiting_for_user",
    BRAIN_STATE_JOB_PENDING: "working",
    BRAIN_STATE_ACTIVE: "working",
    BRAIN_STATE_STOPPED: "error",
    BRAIN_STATE_DONE: "completed",
    BRAIN_STATE_ERROR: "error",
}


class PhaseStatus(BaseModel):
    """Interface-agnostic phase status emitted by the brain/runtime layer."""

    model_config = ConfigDict(extra="ignore")

    trace_id: str = Field(..., min_length=1)
    status_key: StatusKey
    label: str = ""
    source_phase: str | None = None
    source_event: str | None = None
    route: str | None = None
    mode_state: str | None = None
    mode_label: str | None = None
    step_index: int | None = Field(default=None, ge=0)
    step_total: int | None = Field(default=None, ge=0)
    mode_step_index: int | None = Field(default=None, ge=0)
    mode_step_total: int | None = Field(default=None, ge=0)
    llm_call_count: int | None = Field(default=None, ge=0)
    llm_call_limit: int | None = Field(default=None, ge=0)
    total_input_tokens_used: int | None = Field(default=None, ge=0)
    total_output_tokens_used: int | None = Field(default=None, ge=0)
    total_tokens_used: int | None = Field(default=None, ge=0)
    token_usage_estimated: bool = False
    tool_name: str | None = None
    progress_phase: str | None = None
    detail_code: str | None = None
    detail_text: str | None = None
    terminal: bool = False

    @model_validator(mode="before")
    @classmethod
    def normalize_route_alias(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        normalized = dict(value)
        if "route" not in normalized and "mode" in normalized:
            normalized["route"] = normalized.get("mode")
        return normalized

    @property
    def mode(self) -> str | None:
        return self.route


def coerce_phase_status(status: PhaseStatus | Mapping[str, Any] | None) -> PhaseStatus:
    if isinstance(status, PhaseStatus):
        return status
    if isinstance(status, Mapping):
        try:
            return PhaseStatus.model_validate(dict(status))
        except Exception:
            trace_id = str(status.get("trace_id", "") or "phase-status").strip()
            label = str(status.get("label", "") or "").strip() or "Working..."
            status_key = str(status.get("status_key", "") or "").strip()
            if status_key not in {
                "clarifying",
                "analyzing",
                "planning",
                "awaiting_plan_review",
                "awaiting_confirmation",
                "executing",
                "replanning",
                "reviewing",
                "verifying",
                "evaluating_completion",
                "saving_context",
                "waiting_for_user",
                "completed",
                "blocked",
                "error",
                "working",
            }:
                status_key = "working"
            return PhaseStatus(
                trace_id=trace_id or "phase-status",
                status_key=status_key,  # type: ignore[arg-type]
                label=label,
                route=str(status.get("route", "") or status.get("mode", "") or "")
                .strip()
                .lower()
                or None,
                mode_state=str(status.get("mode_state", "") or "").strip() or None,
                mode_label=str(status.get("mode_label", "") or "").strip() or None,
                total_input_tokens_used=_coerce_int(
                    status.get("total_input_tokens_used")
                ),
                total_output_tokens_used=_coerce_int(
                    status.get("total_output_tokens_used")
                ),
                total_tokens_used=_coerce_int(status.get("total_tokens_used")),
                token_usage_estimated=bool(status.get("token_usage_estimated", False)),
                detail_text=str(status.get("detail_text", "") or "").strip() or None,
                terminal=bool(status.get("terminal", False)),
            )
    return PhaseStatus(
        trace_id="phase-status",
        status_key="working",
        label="Working...",
    )


def _phase_status_mode_text(status: PhaseStatus) -> str:
    mode_label = str(status.mode_label or "").strip()
    if mode_label:
        return mode_label
    mode_state = str(status.mode_state or "").strip()
    if mode_state:
        return mode_state.replace("_", " ")
    route = str(status.route or "").strip()
    if route:
        return route.replace("_", " ")
    return ""


def format_phase_status_text(
    status: PhaseStatus | Mapping[str, Any] | None,
    *,
    fallback_label: str = "Working...",
) -> str:
    """Compose the CLI / display status-line text from a `PhaseStatus`."""
    phase_status = coerce_phase_status(status)
    label = str(phase_status.label or "").strip() or fallback_label
    if phase_status.terminal and phase_status.status_key == "completed":
        return label
    if phase_status.status_key in {"waiting_for_user", "error"}:
        return label

    progress_text = _turn_progress_text(phase_status)
    mode_text = _phase_status_mode_text(phase_status)
    detail_text = _inline_status_detail_text(phase_status.detail_text)
    phase_slot = label
    if mode_text and mode_text not in phase_slot:
        phase_slot = f"{phase_slot} {mode_text}"
    if detail_text and detail_text not in phase_slot:
        phase_slot = f"{phase_slot} {detail_text}"

    if not progress_text:
        return phase_slot
    if phase_status.status_key == "working":
        return progress_text
    return _inject_phase_slot(progress_text, phase_slot)


def _inject_phase_slot(progress_text: str, phase_slot: str) -> str:
    """Insert the phase slot after `LLM N/M` when present."""
    if not progress_text:
        return phase_slot
    if not phase_slot:
        return progress_text
    segments = progress_text.split(" | ")
    if segments and segments[0].startswith("LLM "):
        return " | ".join([segments[0], phase_slot, *segments[1:]])
    return " | ".join([phase_slot, *segments])


def _inline_status_detail_text(value: Any) -> str:
    """Collapse multiline status detail into a single shell-safe summary."""

    raw = str(value or "").strip()
    if not raw:
        return ""
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if not lines:
        return ""
    primary = " ".join(lines[0].split())
    if len(lines) == 1:
        return primary
    return f"{primary}…"


def _coerce_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _format_token_count(tokens: int) -> str:
    normalized = max(0, int(tokens))
    if normalized >= 100:
        rendered = normalized / 1000.0
        if rendered.is_integer():
            return f"{int(rendered)}k"
        return f"{rendered:.1f}k"
    return str(normalized)


def _format_token_breakdown_count(tokens: int) -> str:
    normalized = max(0, int(tokens))
    if normalized >= 1000:
        rendered = normalized / 1000.0
        if rendered.is_integer():
            return f"{int(rendered)}k"
        return f"{rendered:.1f}k"
    return str(normalized)


def _token_breakdown_text(status: PhaseStatus) -> str | None:
    input_tokens = status.total_input_tokens_used
    output_tokens = status.total_output_tokens_used
    if input_tokens is None and output_tokens is None:
        return None
    estimate_prefix = "~" if status.token_usage_estimated else ""
    if output_tokens is None:
        return f"↑{estimate_prefix}{_format_token_breakdown_count(input_tokens or 0)} tokens"
    return (
        f"↑{estimate_prefix}{_format_token_breakdown_count(input_tokens or 0)} "
        f"↓{_format_token_breakdown_count(output_tokens or 0)} tokens"
    )


def _turn_progress_text(status: PhaseStatus) -> str | None:
    segments: list[str] = []
    llm_count = status.llm_call_count
    llm_limit = status.llm_call_limit
    if llm_count is not None and llm_limit is not None and llm_limit > 0:
        segments.append(f"LLM {llm_count}/{llm_limit}")
    elif llm_count is not None and llm_count > 0:
        segments.append(f"LLM {llm_count}")
    token_breakdown = _token_breakdown_text(status)
    if token_breakdown is not None:
        segments.append(token_breakdown)
    elif (total_tokens_used := status.total_tokens_used) is not None:
        segments.append(f"{_format_token_count(total_tokens_used)} tokens")
    tool_name = str(status.tool_name or "").strip()
    if tool_name:
        segments.append(f"tool: {display_name_for_tool_name(tool_name)}")
    else:
        progress_phase = str(status.progress_phase or "").strip()
        if progress_phase:
            segments.append(progress_phase)
    if not segments:
        return None
    return " | ".join(segments)


def _status_for_event(source_event: str) -> StatusKey | None:
    if source_event in _EVENT_STATUS_MAP:
        return _EVENT_STATUS_MAP[source_event]
    for prefix, status_key in _EVENT_PREFIX_STATUS_MAP:
        if source_event.startswith(prefix):
            return status_key
    return None


def _label_for_status(
    *,
    status_key: StatusKey,
    source_event: str | None,
    step_index: int | None,
    step_total: int | None,
) -> str:
    if (
        source_event == "brain.plan_checkpoint"
        and step_index is not None
        and step_total is not None
    ):
        return f"Completed {step_index}/{step_total} steps..."
    if (
        status_key == "executing"
        and step_index is not None
        and step_total is not None
        and step_index > 0
    ):
        return f"Executing step {step_index}/{step_total}..."
    return _STATUS_LABELS[status_key]


def _status_for_request_readiness_state(state: str) -> StatusKey:
    if state == "ready":
        return "executing"
    if state == "needs_user":
        return "waiting_for_user"
    if state == "needs_plan_review":
        return "awaiting_plan_review"
    if state == "needs_operation_approval":
        return "awaiting_confirmation"
    if state == "blocked":
        return "blocked"
    return "working"


def phase_status_from_request_readiness(
    *,
    trace_id: str,
    readiness: Any,
    detail_text: str | None = None,
) -> PhaseStatus:
    readiness_state = str(getattr(readiness, "state", "") or "").strip()
    status_key = _status_for_request_readiness_state(readiness_state)
    posture = str(getattr(readiness, "posture", "") or "").strip() or None
    requested_outcome = (
        str(getattr(readiness, "requested_outcome", "") or "").strip() or None
    )
    return PhaseStatus(
        trace_id=trace_id,
        status_key=status_key,
        label=_STATUS_LABELS[status_key],
        route=requested_outcome,
        mode_state=readiness_state or None,
        mode_label=posture,
        detail_code="request_readiness",
        detail_text=str(detail_text or "").strip() or None,
        terminal=False,
    )


def normalize_phase_status(
    *,
    trace_id: str,
    source_phase: str | None = None,
    source_event: str | None = None,
    payload: dict[str, Any] | None = None,
    runtime_status: str | None = None,
    detail_text: str | None = None,
    terminal: bool | None = None,
    route: str | None = None,
    mode: str | None = None,
    mode_state: str | None = None,
    mode_label: str | None = None,
    mode_step_index: int | None = None,
    mode_step_total: int | None = None,
) -> PhaseStatus:
    """Normalize internal brain/runtime phase surfaces into PhaseStatus."""

    normalized_phase = str(source_phase or "").strip().upper()
    normalized_event = str(source_event or "").strip()
    normalized_runtime_status = str(runtime_status or "").strip().lower()
    payload = payload or {}

    status_key = _status_for_event(normalized_event)
    if status_key is None and normalized_phase:
        status_key = _PHASE_STATUS_MAP.get(normalized_phase)
    if status_key is None and normalized_runtime_status:
        status_key = _RUNTIME_STATUS_MAP.get(normalized_runtime_status)
    if status_key is None:
        status_key = "working"

    step_index = None
    step_total = None
    detail_code = None
    if normalized_event == "brain.plan_checkpoint":
        step_index = _coerce_int(payload.get("cursor"))
        step_total = _coerce_int(payload.get("total_steps"))
        detail_code = "plan_checkpoint"
    else:
        step_index = _coerce_int(payload.get("step_index"))
        step_total = _coerce_int(payload.get("step_total"))
        if normalized_event.startswith("brain.closure_gate."):
            detail_code = "closure_gate"

    label = _label_for_status(
        status_key=status_key,
        source_event=normalized_event or None,
        step_index=step_index,
        step_total=step_total,
    )
    if terminal is None:
        terminal = status_key in {"completed", "error"}

    normalized_route = (
        str(route or mode or payload.get("route", "") or payload.get("mode", "") or "")
        .strip()
        .lower()
        or None
    )
    normalized_mode_state = (
        str(mode_state or payload.get("mode_state", "") or "").strip() or None
    )
    if normalized_event == "brain.execution.exited":
        normalized_route = None
    normalized_mode_label = (
        str(mode_label or payload.get("mode_label", "") or "").strip() or None
    )
    normalized_mode_step_index = (
        _coerce_int(mode_step_index)
        if mode_step_index is not None
        else _coerce_int(payload.get("mode_step_index"))
    )
    normalized_mode_step_total = (
        _coerce_int(mode_step_total)
        if mode_step_total is not None
        else _coerce_int(payload.get("mode_step_total"))
    )
    normalized_llm_call_count = _coerce_int(payload.get("turn.llm_call_count"))
    normalized_llm_call_limit = _coerce_int(payload.get("turn.llm_call_limit"))
    normalized_total_input_tokens_used = _coerce_int(
        payload.get("total_input_tokens_used")
    )
    normalized_total_output_tokens_used = _coerce_int(
        payload.get("total_output_tokens_used")
    )
    normalized_total_tokens_used = _coerce_int(payload.get("total_tokens_used"))
    normalized_token_usage_estimated = bool(payload.get("token_usage_estimated", False))
    normalized_tool_name = str(payload.get("turn.tool_name", "") or "").strip() or None
    normalized_progress_phase = (
        str(payload.get("turn.progress_phase", "") or "").strip() or None
    )

    return PhaseStatus(
        trace_id=trace_id,
        status_key=status_key,
        label=label,
        source_phase=normalized_phase or None,
        source_event=normalized_event or None,
        route=normalized_route,
        mode_state=normalized_mode_state,
        mode_label=normalized_mode_label,
        step_index=step_index,
        step_total=step_total,
        mode_step_index=normalized_mode_step_index,
        mode_step_total=normalized_mode_step_total,
        llm_call_count=normalized_llm_call_count,
        llm_call_limit=normalized_llm_call_limit,
        total_input_tokens_used=normalized_total_input_tokens_used,
        total_output_tokens_used=normalized_total_output_tokens_used,
        total_tokens_used=normalized_total_tokens_used,
        token_usage_estimated=normalized_token_usage_estimated,
        tool_name=normalized_tool_name,
        progress_phase=normalized_progress_phase,
        detail_code=detail_code,
        detail_text=str(detail_text or "").strip() or None,
        terminal=bool(terminal),
    )


def phase_status_from_phase(
    *,
    trace_id: str,
    phase: str,
    payload: dict[str, Any] | None = None,
    detail_text: str | None = None,
) -> PhaseStatus:
    return normalize_phase_status(
        trace_id=trace_id,
        source_phase=phase,
        payload=payload,
        detail_text=detail_text,
    )


def phase_status_from_event(
    *,
    trace_id: str,
    event_type: str,
    payload: dict[str, Any] | None = None,
    detail_text: str | None = None,
) -> PhaseStatus:
    return normalize_phase_status(
        trace_id=trace_id,
        source_event=event_type,
        payload=payload,
        detail_text=detail_text,
    )


def phase_status_from_runtime(
    *,
    trace_id: str,
    runtime_status: str,
    detail_text: str | None = None,
    terminal: bool | None = None,
) -> PhaseStatus:
    return normalize_phase_status(
        trace_id=trace_id,
        runtime_status=runtime_status,
        detail_text=detail_text,
        terminal=terminal,
    )


__all__ = [
    "PhaseStatus",
    "StatusKey",
    "coerce_phase_status",
    "format_phase_status_text",
    "normalize_phase_status",
    "phase_status_from_event",
    "phase_status_from_phase",
    "phase_status_from_request_readiness",
    "phase_status_from_runtime",
]
