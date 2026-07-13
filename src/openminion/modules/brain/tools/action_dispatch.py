from __future__ import annotations

import logging
import time
from typing import Any

from openminion.modules.brain.adapters.tool.permission_mode import (
    canonical_permission_mode,
    effective_permission_mode_for_tool,
    is_tool_blocked_by_readonly,
)
from openminion.modules.brain.constants import (
    BRAIN_ACTION_STATUS_BLOCKED,
    BRAIN_ACTION_STATUS_FAILED,
    BRAIN_ACTION_STATUS_NEEDS_USER,
    BRAIN_ACTION_STATUS_SUCCESS,
    BRAIN_COMMAND_KIND_AGENT,
    BRAIN_COMMAND_KIND_ASK_USER,
    BRAIN_COMMAND_KIND_FINISH,
    BRAIN_COMMAND_KIND_TOOL,
)
from openminion.modules.brain.schemas import (  # type: ignore[attr-defined]
    ActionError,
    ActionResult,
    AgentCommand,
    JobHandle,
    WorkingState,
)
from openminion.modules.tool.contracts.model_ids import MODEL_TASK_DELEGATE
from openminion.modules.brain.tools.lifecycle import (
    LIFECYCLE_EVENT_ON_SUBAGENT_STOP,
    LifecycleEvent,
    fire_lifecycle_event,
)

from .executor.agent import (  # noqa: F401
    _execute_local_self_agent_command,
    _is_local_self_agent_command,
)

from .executor.dispatch import _command_lineage_payload  # noqa: F401

_LOG = logging.getLogger(__name__)


def _record_dispatch_observer_failure(
    *,
    runner: Any | None,
    state: WorkingState,
    observer: str,
    exc: Exception,
) -> None:
    _LOG.warning(
        "brain dispatch observer failed: observer=%s error_type=%s",
        observer,
        type(exc).__name__,
        exc_info=True,
    )
    emit = getattr(runner, "_emit_brain_operation", None)
    if not callable(emit):
        return
    try:
        emit(
            session_id=str(getattr(state, "session_id", "") or ""),
            turn_id=str(getattr(state, "trace_id", "") or ""),
            operation="dispatch_observer_failure",
            status="error",
            extra={
                "observer": observer,
                "error_type": type(exc).__name__,
            },
        )
    except Exception:
        _LOG.debug("brain dispatch observer-failure counter emit failed", exc_info=True)


def _fire_subagent_stop_lifecycle(
    *,
    runner: Any | None = None,
    state: WorkingState,
    command: Any,
    result: ActionResult,
    duration_ms: int,
) -> None:
    """Emit an observe-only lifecycle event after subagent dispatch."""
    try:
        fire_lifecycle_event(
            LifecycleEvent(
                event_type=LIFECYCLE_EVENT_ON_SUBAGENT_STOP,
                trace_id=str(state.trace_id or "").strip(),
                session_id=str(state.session_id or "").strip(),
                agent_id=str(getattr(state, "agent_id", "") or "").strip(),
                subagent_id=str(getattr(command, "target_agent_id", "") or "").strip(),
                tool_call_id=str(getattr(command, "command_id", "") or "").strip(),
                tool_ok=(result.status == BRAIN_ACTION_STATUS_SUCCESS),
                tool_duration_ms=int(duration_ms),
                tool_content=str(getattr(result, "summary", "") or ""),
                source_payload={
                    "method": str(getattr(command, "method", "") or ""),
                    "params": dict(getattr(command, "params", {}) or {}),
                },
            )
        )
    except Exception as exc:
        _record_dispatch_observer_failure(
            runner=runner,
            state=state,
            observer="on_subagent_stop",
            exc=exc,
        )


def execute_action_dispatch(
    runner: Any,
    *,
    state: WorkingState,
    command: Any,
    logger: Any,
    sanitize_tool_command_args: Any,
    execute_action_fn: Any,
) -> tuple[ActionResult, JobHandle | None]:
    if runner.safety_api is not None and not runner.safety_api.is_normal():
        logger.emit(
            "safety.preempted",
            {"state": "non-normal", "command": command.title},
            trace_id=state.trace_id,
        )
        return (
            ActionResult(
                command_id=command.command_id,
                status=BRAIN_ACTION_STATUS_BLOCKED,
                summary="Execution halted by safety service",
                error=ActionError(
                    code="SAFETY_PREEMPTED",
                    message="Safety service is not in normal state.",
                    details={"reason_code": "safety_preempted"},
                ),
            ),
            None,
        )

    state.last_command_id = command.command_id
    if (
        command.kind in {BRAIN_COMMAND_KIND_TOOL, BRAIN_COMMAND_KIND_AGENT}
        and runner.options.idempotency_enabled
    ):
        cached = state.idempotency_cache.get(command.idempotency_key)
        if cached is not None:
            return cached, None
    if command.kind == BRAIN_COMMAND_KIND_FINISH:
        result = ActionResult(
            command_id=command.command_id,
            status=BRAIN_ACTION_STATUS_SUCCESS,
            summary=command.final_message or "Finished.",
        )
        runner._remember_idempotency(state=state, command=command, result=result)
        return result, None
    if command.kind == BRAIN_COMMAND_KIND_ASK_USER:
        return (
            ActionResult(
                command_id=command.command_id,
                status=BRAIN_ACTION_STATUS_NEEDS_USER,
                summary=command.question,
            ),
            None,
        )
    if command.kind == BRAIN_COMMAND_KIND_TOOL:
        global_permission_mode = canonical_permission_mode(
            str(getattr(state, "permission_mode", "default"))
        )
        state.permission_mode = global_permission_mode
        tool_name_for_gate = str(getattr(command, "tool_name", "") or "").strip()
        permission_mode = effective_permission_mode_for_tool(
            global_mode=global_permission_mode,
            permission_overrides=getattr(state, "permission_overrides", {}),
            tool_name=tool_name_for_gate,
        )
        if permission_mode == "readonly":
            if is_tool_blocked_by_readonly(tool_name_for_gate):
                return (
                    ActionResult(
                        command_id=command.command_id,
                        status=BRAIN_ACTION_STATUS_BLOCKED,
                        summary=(
                            f"Tool {tool_name_for_gate!r} blocked by "
                            "readonly permission mode"
                        ),
                        error=ActionError(
                            code="PERMISSION_DENIED_READONLY",
                            message=(
                                f"Cannot execute write-capable tool "
                                f"{tool_name_for_gate!r} in readonly "
                                f"permission mode. Switch to default "
                                f"or bypass mode via shift+tab or "
                                f"/permissions <mode>."
                            ),
                            details={
                                "reason_code": "readonly_blocks_write",
                                "tool_name": tool_name_for_gate,
                                "permission_mode": "readonly",
                            },
                        ),
                    ),
                    None,
                )
        tool_name_for_dispatch = str(getattr(command, "tool_name", "") or "").strip()
        if tool_name_for_dispatch == MODEL_TASK_DELEGATE:
            args = dict(getattr(command, "args", {}) or {})
            target_agent_id = str(args.get("agent_id", "") or "").strip()
            instruction = str(args.get("instruction", "") or "").strip()
            try:
                timeout_seconds = int(args.get("timeout_seconds", 120) or 120)
            except (TypeError, ValueError):
                timeout_seconds = 120
            if not target_agent_id or not instruction:
                result = ActionResult(
                    command_id=str(getattr(command, "command_id", "") or ""),
                    status=BRAIN_ACTION_STATUS_FAILED,
                    summary="Invalid task.delegate arguments",
                    error=ActionError(
                        code="TOOL_ARG_VALIDATION_FAILED",
                        message="task.delegate requires agent_id and instruction.",
                        details={
                            "reason_code": "task_delegate_invalid_args",
                            "missing_fields": [
                                field
                                for field, value in (
                                    ("agent_id", target_agent_id),
                                    ("instruction", instruction),
                                )
                                if not value
                            ],
                        },
                    ),
                )
                runner._remember_idempotency(
                    state=state, command=command, result=result
                )
                return result, None
            command_id = (
                str(getattr(command, "command_id", "") or "").strip() or "task-delegate"
            )
            delegated = AgentCommand(
                command_id=command_id,
                kind=BRAIN_COMMAND_KIND_AGENT,
                title=f"Delegate task to {target_agent_id}",
                target_agent_id=target_agent_id,
                method="delegate",
                params={
                    "instruction": instruction,
                    "timeout_seconds": timeout_seconds,
                },
                success_criteria={"status": "success"},
                idempotency_key=str(getattr(command, "idempotency_key", "") or ""),
                risk_level="med",
            )
            return execute_action_dispatch(
                runner,
                state=state,
                command=delegated,
                logger=logger,
                sanitize_tool_command_args=sanitize_tool_command_args,
                execute_action_fn=execute_action_fn,
            )
        lineage = _command_lineage_payload(state=state, command=command)
        if state.budgets_remaining.tool_calls <= 0:
            return runner._budget_blocked_result(
                command_id=command.command_id, budget_name="tool_calls"
            ), None
        state.budgets_remaining.tool_calls -= 1
        logger.emit(
            "tool.request",
            {
                "kind": command.kind,
                "title": command.title,
                "args": getattr(command, "args", None),
                **lineage,
            },
            trace_id=state.trace_id,
        )
        runner._emit_brain_operation(
            session_id=state.session_id,
            turn_id=str(state.trace_id or "").strip(),
            operation="tool_loop",
            extra={
                "provider": "tool",
                "tool_name": str(getattr(command, "tool_name", "") or "").strip(),
            },
        )
        _emit_tool_progress = getattr(runner, "_emit_tool_progress_event", None)
        if callable(_emit_tool_progress):
            try:
                _emit_tool_progress(
                    kind="tool_started",
                    tool_name=str(getattr(command, "tool_name", "") or ""),
                    args=dict(getattr(command, "args", {}) or {}),
                    call_id=str(getattr(command, "command_id", "") or ""),
                )
            except Exception as exc:
                _record_dispatch_observer_failure(
                    runner=runner,
                    state=state,
                    observer="tool_progress_started",
                    exc=exc,
                )
        try:
            from openminion.modules.brain.tools.lifecycle import (
                LIFECYCLE_EVENT_PRE_TOOL_USE,
                LifecycleEvent,
                fire_lifecycle_event,
            )

            fire_lifecycle_event(
                LifecycleEvent(
                    event_type=LIFECYCLE_EVENT_PRE_TOOL_USE,
                    trace_id=str(state.trace_id or "").strip(),
                    session_id=str(state.session_id or "").strip(),
                    agent_id=str(getattr(state, "agent_id", "") or "").strip(),
                    tool_name=str(getattr(command, "tool_name", "") or ""),
                    tool_args=dict(getattr(command, "args", {}) or {}),
                    tool_call_id=str(getattr(command, "command_id", "") or ""),
                )
            )
        except Exception as exc:
            _record_dispatch_observer_failure(
                runner=runner,
                state=state,
                observer="pre_tool_use",
                exc=exc,
            )
        logger.emit(
            "skill.step",
            {
                "step_index": state.cursor,
                "status": "running",
                "note": f"Starting execution of {command.title}",
            },
            trace_id=state.trace_id,
        )
        if runner.tool_api is None:
            result = ActionResult(
                command_id=command.command_id,
                status=BRAIN_ACTION_STATUS_FAILED,
                summary="Tool API unavailable",
                error=ActionError(
                    code="TOOL_API_UNAVAILABLE",
                    message="Tool API is not configured.",
                    details={"reason_code": "tool_api_unavailable"},
                ),
            )
            runner._remember_idempotency(state=state, command=command, result=result)
            return result, None
        sanitized_args, removed_arg_keys = sanitize_tool_command_args(
            runner,
            command=command,
        )
        if removed_arg_keys:
            logger.emit(
                "tool.args_sanitized",
                {
                    "tool_name": str(getattr(command, "tool_name", "") or ""),
                    "removed_keys": list(removed_arg_keys),
                    "retained_keys": sorted(sanitized_args.keys()),
                    **lineage,
                },
                trace_id=state.trace_id,
            )
        validation_result = runner._validate_tool_args(command=command, state=state)
        if validation_result is not None:
            result = ActionResult(
                command_id=command.command_id,
                status=BRAIN_ACTION_STATUS_FAILED,
                summary=f"Invalid tool arguments: {validation_result['message']}",
                error=ActionError(
                    code="TOOL_ARG_VALIDATION_FAILED",
                    message=validation_result["message"],
                    details={
                        "reason_code": str(
                            validation_result.get("reason_code")
                            or "tool_arg_validation_failed"
                        ),
                        "missing_fields": validation_result.get("missing"),
                        "suggestion": validation_result.get("suggestion"),
                        "source": validation_result.get("source"),
                    },
                ),
            )
            runner._remember_idempotency(state=state, command=command, result=result)
            return result, None
        payload = command.model_dump(mode="json")
        payload_meta = payload.get("meta")
        if not isinstance(payload_meta, dict):
            payload_meta = {}
        payload_meta["orchestration"] = dict(lineage)
        payload["meta"] = payload_meta
        inputs = payload.get("inputs")
        if isinstance(inputs, dict):
            inputs.setdefault("permission_mode", permission_mode)
        else:
            payload["inputs"] = {"permission_mode": permission_mode}
        _tool_started_at = time.monotonic()
        raw = runner.tool_api.execute(
            command=payload,
            session_id=state.session_id,
            trace_id=state.trace_id or "",
        )
        _tool_duration_ms = int((time.monotonic() - _tool_started_at) * 1000)
        normalized, job = runner._normalize_execution_result(
            command_id=command.command_id, raw=raw, provider="tool"
        )
        if job is None:
            logger.emit(
                "tool.completed",
                {"status": normalized.status, "summary": normalized.summary, **lineage},
                trace_id=state.trace_id,
                artifact_refs=[a.ref for a in normalized.artifact_refs],
                memory_refs=normalized.memory_refs,
                status="ok"
                if normalized.status == BRAIN_ACTION_STATUS_SUCCESS
                else "error",
                error=normalized.error.model_dump(mode="json")
                if normalized.error
                else None,
            )
            if callable(_emit_tool_progress):
                try:
                    _emit_tool_progress(
                        kind="tool_completed",
                        tool_name=str(getattr(command, "tool_name", "") or ""),
                        args=dict(getattr(command, "args", {}) or {}),
                        call_id=str(getattr(command, "command_id", "") or ""),
                        duration_ms=_tool_duration_ms,
                        ok=(normalized.status == BRAIN_ACTION_STATUS_SUCCESS),
                        content=str(getattr(normalized, "summary", "") or ""),
                    )
                except Exception as exc:
                    _record_dispatch_observer_failure(
                        runner=runner,
                        state=state,
                        observer="tool_progress_completed",
                        exc=exc,
                    )
            try:
                from openminion.modules.brain.tools.lifecycle import (
                    LIFECYCLE_EVENT_POST_TOOL_USE,
                    LifecycleEvent,
                    fire_lifecycle_event,
                )

                fire_lifecycle_event(
                    LifecycleEvent(
                        event_type=LIFECYCLE_EVENT_POST_TOOL_USE,
                        trace_id=str(state.trace_id or "").strip(),
                        session_id=str(state.session_id or "").strip(),
                        agent_id=str(getattr(state, "agent_id", "") or "").strip(),
                        tool_name=str(getattr(command, "tool_name", "") or ""),
                        tool_args=dict(getattr(command, "args", {}) or {}),
                        tool_call_id=str(getattr(command, "command_id", "") or ""),
                        tool_ok=(normalized.status == BRAIN_ACTION_STATUS_SUCCESS),
                        tool_duration_ms=_tool_duration_ms,
                        tool_content=str(getattr(normalized, "summary", "") or ""),
                    )
                )
            except Exception as exc:
                _record_dispatch_observer_failure(
                    runner=runner,
                    state=state,
                    observer="post_tool_use",
                    exc=exc,
                )
            runner._remember_idempotency(
                state=state, command=command, result=normalized
            )
        return normalized, job
    if command.kind == BRAIN_COMMAND_KIND_AGENT:
        if _is_local_self_agent_command(runner, state=state, command=command):
            return _execute_local_self_agent_command(
                runner,
                state=state,
                command=command,
                logger=logger,
                execute_action_fn=execute_action_fn,
            )
        lineage = _command_lineage_payload(state=state, command=command)
        if state.budgets_remaining.a2a_calls <= 0:
            return runner._budget_blocked_result(
                command_id=command.command_id, budget_name="a2a_calls"
            ), None
        state.budgets_remaining.a2a_calls -= 1
        logger.emit(
            "a2a.request",
            {
                "target_agent_id": command.target_agent_id,
                "method": command.method,
                "params": command.params,
                **lineage,
            },
            trace_id=state.trace_id,
        )
        runner._emit_brain_operation(
            session_id=state.session_id,
            turn_id=str(state.trace_id or "").strip(),
            operation="tool_loop",
            extra={
                "provider": "a2a",
                "target_agent_id": str(
                    getattr(command, "target_agent_id", "") or ""
                ).strip(),
            },
        )
        logger.emit(
            "skill.step",
            {
                "step_index": state.cursor,
                "status": "running",
                "note": f"Starting A2A execution of {command.title}",
            },
            trace_id=state.trace_id,
        )
        if runner.a2a_api is None:
            result = ActionResult(
                command_id=command.command_id,
                status=BRAIN_ACTION_STATUS_FAILED,
                summary="A2A API unavailable",
                error=ActionError(
                    code="A2A_API_UNAVAILABLE",
                    message="A2A API is not configured.",
                    details={"reason_code": "a2a_api_unavailable"},
                ),
            )
            _fire_subagent_stop_lifecycle(
                runner=runner,
                state=state,
                command=command,
                result=result,
                duration_ms=0,
            )
            runner._remember_idempotency(state=state, command=command, result=result)
            return result, None
        _a2a_started_at = time.monotonic()
        raw = runner.a2a_api.call(
            command=command.model_dump(mode="json"),
            session_id=state.session_id,
            trace_id=state.trace_id or "",
        )
        _a2a_duration_ms = int((time.monotonic() - _a2a_started_at) * 1000)
        normalized, job = runner._normalize_execution_result(
            command_id=command.command_id, raw=raw, provider="a2actl"
        )
        if job is None:
            logger.emit(
                "a2a.completed",
                {"status": normalized.status, "summary": normalized.summary, **lineage},
                trace_id=state.trace_id,
                artifact_refs=[a.ref for a in normalized.artifact_refs],
                memory_refs=normalized.memory_refs,
                status="ok"
                if normalized.status == BRAIN_ACTION_STATUS_SUCCESS
                else "error",
                error=normalized.error.model_dump(mode="json")
                if normalized.error
                else None,
            )
            _fire_subagent_stop_lifecycle(
                runner=runner,
                state=state,
                command=command,
                result=normalized,
                duration_ms=_a2a_duration_ms,
            )
            runner._remember_idempotency(
                state=state, command=command, result=normalized
            )
        return normalized, job
    return (
        ActionResult(
            command_id=command.command_id,
            status=BRAIN_ACTION_STATUS_FAILED,
            summary=f"Unsupported command kind: {command.kind}",
            error=ActionError(
                code="UNSUPPORTED_COMMAND_KIND",
                message=f"Unsupported command kind: {command.kind}",
                details={"reason_code": "unsupported_command_kind"},
            ),
        ),
        None,
    )
