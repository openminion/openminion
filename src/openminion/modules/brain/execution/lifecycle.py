from typing import Any, Callable

from ..constants import (
    BRAIN_STATE_ACTIVE,
    BRAIN_STATE_CONTINUE,
    BRAIN_STATE_DONE,
    BRAIN_STATE_ERROR,
    BRAIN_STATE_FAILED,
    BRAIN_STATE_JOB_PENDING,
    BRAIN_STATE_STOPPED,
    BRAIN_STATE_WAITING_USER,
)
from ..execution.public_taxonomy import public_mode_name_for_mode_name
from ..schemas import ActionResult, WorkingState
from ..diagnostics.status import normalize_phase_status
from .loop_contracts import ExecutionContext, ExecutionResult

_EXIT_STATE_MAP: dict[str, tuple[str, bool] | None] = {
    BRAIN_STATE_DONE: ("exited", True),
    BRAIN_STATE_WAITING_USER: ("exited_waiting", False),
    BRAIN_STATE_JOB_PENDING: ("exited_waiting", False),
    BRAIN_STATE_STOPPED: ("cancelled", True),
    BRAIN_STATE_ERROR: ("failed", True),
    BRAIN_STATE_FAILED: ("failed", True),
    BRAIN_STATE_ACTIVE: None,
    BRAIN_STATE_CONTINUE: None,
}


def dispatch_execution(
    handler: Any,
    ctx: ExecutionContext,
) -> ExecutionResult:
    mode_name = str(getattr(handler, "mode_name", "") or "").strip()
    if not mode_name:
        raise ValueError("Execution handler must define mode_name")
    return dispatch_execution_call(ctx, mode_name=mode_name, execute=handler.execute)


def dispatch_execution_call(
    ctx: ExecutionContext,
    *,
    mode_name: str,
    execute: Callable[[ExecutionContext], ExecutionResult],
) -> ExecutionResult:
    ctx.state.active_mode_name = mode_name
    public_mode_name = public_mode_name_for_mode_name(mode_name) or mode_name
    emit_mode_status(
        ctx,
        state=ctx.state,
        logger=ctx.logger,
        source_event="brain.execution.entered",
        mode=mode_name,
        mode_state="entered",
        mode_label=f"Entering {public_mode_name}",
    )
    try:
        result = execute(ctx)
    except Exception:
        emit_mode_status(
            ctx,
            state=ctx.state,
            logger=ctx.logger,
            source_event="brain.execution.failed",
            runtime_status=BRAIN_STATE_ERROR,
            terminal=True,
            mode=mode_name,
            mode_state="failed",
            mode_label=f"{public_mode_name} failed",
        )
        raise
    exit_mapping = _EXIT_STATE_MAP.get(str(result.status or "").strip())
    if exit_mapping is not None:
        exit_state, terminal = exit_mapping
        emit_mode_status(
            ctx,
            state=result.working_state,
            logger=ctx.logger,
            source_event="brain.execution.exited",
            runtime_status=result.status,
            terminal=terminal,
            mode=mode_name,
            mode_state=exit_state,
            mode_label=f"{public_mode_name} {exit_state.replace('_', ' ')}",
        )
    return result


def active_mode_result(
    *,
    host: Any,
    state: WorkingState,
    action_result: ActionResult | None = None,
) -> ExecutionResult:
    save_state = getattr(host, "save_state", None)
    if callable(save_state):
        save_state(state)
    else:
        host._save_state(state)
    return ExecutionResult(
        status=state.status,
        working_state=state,
        message=None,
        action_result=action_result,
    )


def _normalized_mode_name(mode: Any) -> str | None:
    if hasattr(mode, "value"):
        mode = getattr(mode, "value", "")
    return str(mode or "").strip().lower() or None


def emit_mode_status(
    host: Any,
    *,
    state: Any,
    logger: Any | None = None,
    source_phase: str | None = None,
    source_event: str | None = None,
    payload: dict[str, Any] | None = None,
    runtime_status: str | None = None,
    detail_text: str | None = None,
    terminal: bool | None = None,
    mode: Any = None,
    mode_state: str | None = None,
    mode_label: str | None = None,
    mode_step_index: int | None = None,
    mode_step_total: int | None = None,
    event_type: str = "brain.execution_status",
) -> None:
    normalized_mode = _normalized_mode_name(mode)
    emit_phase_status = getattr(host, "emit_status", None)
    if callable(emit_phase_status):
        emit_phase_status(
            state=state,
            source_phase=source_phase,
            source_event=source_event,
            payload=payload,
            runtime_status=runtime_status,
            detail_text=detail_text,
            terminal=terminal,
            mode=normalized_mode,
            mode_state=mode_state,
            mode_label=mode_label,
            mode_step_index=mode_step_index,
            mode_step_total=mode_step_total,
            log_event=False,
        )
    else:
        emit_phase_status = getattr(host, "_emit_phase_status", None)
        if callable(emit_phase_status):
            emit_phase_status(
                state=state,
                source_phase=source_phase,
                source_event=source_event,
                payload=payload,
                runtime_status=runtime_status,
                detail_text=detail_text,
                terminal=terminal,
                mode=normalized_mode,
                mode_state=mode_state,
                mode_label=mode_label,
                mode_step_index=mode_step_index,
                mode_step_total=mode_step_total,
                log_event=False,
            )
    if logger is None:
        return
    trace_id = str(
        getattr(state, "trace_id", "") or getattr(host, "_trace_id", "") or ""
    ).strip()
    normalized = normalize_phase_status(
        trace_id=trace_id or "execution-status",
        source_phase=source_phase,
        source_event=source_event,
        payload=payload,
        runtime_status=runtime_status,
        detail_text=detail_text,
        terminal=terminal,
        mode=normalized_mode,
        mode_state=mode_state,
        mode_label=mode_label,
        mode_step_index=mode_step_index,
        mode_step_total=mode_step_total,
    )
    event_payload = dict(payload or {})
    event_payload.update(normalized.model_dump(mode="json", exclude_none=True))
    logger.emit(
        event_type,
        event_payload,
        trace_id=normalized.trace_id,
        status=normalized.status_key,
    )


def emit_mode_entered(
    host: Any,
    *,
    state: Any,
    logger: Any,
    source_phase: str,
    mode: Any,
    mode_label: str | None = None,
) -> None:
    emit_mode_status(
        host,
        state=state,
        logger=logger,
        source_phase=source_phase,
        mode=mode,
        mode_state="entered",
        mode_label=mode_label or "Starting execution",
    )


def set_phase(
    host: Any,
    *,
    state: Any,
    phase: str,
    logger: Any | None = None,
    mode: Any = None,
    mode_state: str | None = None,
    mode_label: str | None = None,
    payload: dict[str, Any] | None = None,
    detail_text: str | None = None,
    mode_step_index: int | None = None,
    mode_step_total: int | None = None,
) -> None:
    state.phase = phase
    normalized_mode = _normalized_mode_name(mode)
    if (
        normalized_mode is not None
        or mode_state is not None
        or mode_label is not None
        or logger is not None
        or mode_step_index is not None
        or mode_step_total is not None
        or payload
    ):
        emit_mode_status(
            host,
            state=state,
            logger=logger,
            source_phase=phase,
            payload=payload,
            detail_text=detail_text,
            mode=normalized_mode,
            mode_state=mode_state,
            mode_label=mode_label,
            mode_step_index=mode_step_index,
            mode_step_total=mode_step_total,
        )
        return
    emit_phase_status = getattr(host, "emit_status", None)
    if callable(emit_phase_status):
        emit_phase_status(state=state, source_phase=phase)
        return
    emit_phase_status = getattr(host, "_emit_phase_status", None)
    if callable(emit_phase_status):
        emit_phase_status(state=state, source_phase=phase)


__all__ = [
    "_EXIT_STATE_MAP",
    "_normalized_mode_name",
    "active_mode_result",
    "dispatch_execution",
    "dispatch_execution_call",
    "emit_mode_entered",
    "emit_mode_status",
    "set_phase",
]
