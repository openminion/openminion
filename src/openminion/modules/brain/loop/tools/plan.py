"""Task-plan runtime state, event, and goal-sync helpers."""

from __future__ import annotations

from typing import Any

from openminion.modules.brain.loop.services import runner_from_context
from openminion.modules.brain.schemas.base import new_uuid
from openminion.modules.brain.schemas.state import ActionError, ActionResult
from openminion.modules.task.schemas import TaskOps
from openminion.modules.task.plan import TaskPlan

from .task_ops import (
    PLAN_TASK_OPS_OUTPUT_KEY,
    PLAN_TASK_OPS_TOUCHED_TASK_IDS_OUTPUT_KEY,
)

_PLAN_TOOL_EVENT_SOURCE = "plan_tool"
_CANONICAL_TO_PROGRESS_KIND: dict[str, str] = {
    "task_plan.declared": "task_plan",
    "task_plan.step_completed": "task_plan_step_completed",
    "task_plan.step_blocked": "task_plan_step_blocked",
    "task_plan.revised": "task_plan_revision",
    "task_plan.completed": "task_plan_completed",
    "task_plan.abandoned": "task_plan_completed",
}


def _payload_is_active(
    loop_ctx: Any,
    *,
    event_type: str,
    plan_id: str,
    step_id: str,
    require_step: bool,
    payload: dict[str, Any],
) -> bool:
    active_plan = _current_active_plan(loop_ctx)
    active_plan_id = _active_plan_id(active_plan)
    if not active_plan_id or str(plan_id or "").strip() != active_plan_id:
        _append_invalid_task_plan_event(
            loop_ctx,
            trailer_type=event_type,
            reason="plan_id_mismatch",
            payload=payload,
        )
        return False
    if require_step and str(step_id or "").strip() not in _active_step_ids(active_plan):
        _append_invalid_task_plan_event(
            loop_ctx,
            trailer_type=event_type,
            reason="unknown_step_id",
            payload=payload,
        )
        return False
    return True


def _current_active_plan(loop_ctx: Any) -> dict[str, Any] | None:
    override = _get_active_plan_override(loop_ctx)
    if isinstance(override, dict):
        return dict(override)
    session_api = _resolve_session_api(loop_ctx)
    store = getattr(session_api, "store", None)
    get_active_task_plan = getattr(store or session_api, "get_active_task_plan", None)
    if callable(get_active_task_plan):
        try:
            active = get_active_task_plan(_session_id(loop_ctx))
        except Exception:  # noqa: BLE001
            active = None
        if isinstance(active, dict):
            return dict(active)
    get_slice = getattr(store or session_api, "get_slice", None)
    if not callable(get_slice):
        return None
    try:
        raw = get_slice(
            _session_id(loop_ctx),
            purpose="decide",
            limits={"max_turns": 1, "max_tool_events": 0},
        )
    except TypeError:
        try:
            raw = get_slice(
                session_id=_session_id(loop_ctx),
                purpose="decide",
                limits={"max_turns": 1, "max_tool_events": 0},
            )
        except Exception:  # noqa: BLE001
            return None
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(raw, dict):
        return None
    active = raw.get("active_task_plan")
    return dict(active) if isinstance(active, dict) else None


def _get_active_plan_override(loop_ctx: Any) -> dict[str, Any] | None:
    override = getattr(loop_ctx, "_plan_tool_active_plan_override", None)
    return dict(override) if isinstance(override, dict) else None


def _set_active_plan_override(loop_ctx: Any, plan: dict[str, Any]) -> None:
    setattr(loop_ctx, "_plan_tool_active_plan_override", dict(plan))


def _clear_active_plan_override(loop_ctx: Any) -> None:
    if hasattr(loop_ctx, "_plan_tool_active_plan_override"):
        setattr(loop_ctx, "_plan_tool_active_plan_override", None)


def _update_active_plan_step_status(
    loop_ctx: Any,
    *,
    plan_id: str,
    step_id: str,
    status: str,
    output_summary: str | None = None,
    blocker_type: str | None = None,
    blocker_details: str | None = None,
) -> None:
    active_plan = _get_active_plan_override(loop_ctx) or _current_active_plan(loop_ctx)
    if not isinstance(active_plan, dict):
        return
    if str(active_plan.get("plan_id", "") or "").strip() != str(plan_id or "").strip():
        return
    steps = list(active_plan.get("steps") or [])
    updated = False
    next_steps: list[dict[str, Any]] = []
    for raw_step in steps:
        step = dict(raw_step) if isinstance(raw_step, dict) else {}
        if str(step.get("step_id", "") or "").strip() == str(step_id or "").strip():
            step["status"] = status
            if output_summary is not None:
                step["output_summary"] = output_summary
            if blocker_type is not None:
                step["blocker_type"] = blocker_type
            if blocker_details is not None:
                step["blocker_details"] = blocker_details
            updated = True
        next_steps.append(step)
    if not updated:
        return
    active_plan["steps"] = next_steps
    _set_active_plan_override(loop_ctx, active_plan)


def _append_invalid_task_plan_event(
    loop_ctx: Any,
    *,
    trailer_type: str,
    reason: str,
    payload: dict[str, Any] | None,
) -> None:
    _append_task_plan_event(
        loop_ctx,
        event_type="task_plan.invalid_trailer",
        payload={
            "trailer_type": trailer_type,
            "reason": reason,
            "payload": dict(payload or {}),
        },
    )


def _append_task_plan_event(
    loop_ctx: Any,
    *,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    session_api = _resolve_session_api(loop_ctx)
    append_event = getattr(session_api, "append_event", None) if session_api else None
    if callable(append_event):
        trace_id = _trace_id(loop_ctx)
        try:
            append_event(
                _session_id(loop_ctx),
                event_type,
                {"source": _PLAN_TOOL_EVENT_SOURCE, **payload},
                actor_type="agent",
                actor_id=_agent_id(loop_ctx),
                trace={"trace_id": trace_id} if trace_id else None,
                importance=2,
                redaction="none",
                status="ok",
            )
        except Exception:  # noqa: BLE001
            pass
    _emit_task_plan_progress_event(loop_ctx, event_type=event_type, payload=payload)


def _emit_task_plan_progress_event(
    loop_ctx: Any,
    *,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    runner = runner_from_context(loop_ctx) or getattr(loop_ctx, "_runner", None)
    if runner is None:
        return
    callback = getattr(runner, "_progress_callback", None)
    if not callable(callback):
        return
    kind = _CANONICAL_TO_PROGRESS_KIND.get(event_type)
    if not kind:
        return
    progress_payload: dict[str, Any] = {"kind": kind}
    if kind in {"task_plan", "task_plan_completed"}:
        plan = payload.get("plan")
        if isinstance(plan, dict):
            progress_payload["plan"] = dict(plan)
    elif kind == "task_plan_step_completed":
        progress_payload["step_text"] = str(payload.get("step_id", "") or "")
        note = str(payload.get("output_summary", "") or "").strip()
        if note:
            progress_payload["note"] = note
    elif kind == "task_plan_step_blocked":
        progress_payload["step_text"] = str(payload.get("step_id", "") or "")
        blocker_details = payload.get("blocker_details")
        if isinstance(blocker_details, dict):
            blocker_type = str(payload.get("blocker_type", "") or "").strip()
            detail_value = ""
            for value in blocker_details.values():
                detail_value = str(value or "").strip()
                if detail_value:
                    break
            reason_parts = [part for part in (blocker_type, detail_value) if part]
            if reason_parts:
                progress_payload["reason"] = ": ".join(reason_parts)
        else:
            blocker_type = str(payload.get("blocker_type", "") or "").strip()
            if blocker_type:
                progress_payload["reason"] = blocker_type
    elif kind == "task_plan_revision":
        progress_payload["plan_id"] = str(payload.get("plan_id", "") or "")
    try:
        callback(progress_payload)
    except Exception:  # noqa: BLE001
        return


def _resolve_session_api(loop_ctx: Any) -> Any | None:
    session_api = getattr(loop_ctx, "session_api", None)
    if session_api is not None:
        return session_api
    runner = runner_from_context(loop_ctx) or getattr(loop_ctx, "_runner", None)
    return getattr(runner, "session_api", None) if runner is not None else None


def _resolve_skill_api(loop_ctx: Any) -> Any | None:
    skill_api = getattr(loop_ctx, "skill_api", None)
    if skill_api is not None:
        return skill_api
    runner = runner_from_context(loop_ctx) or getattr(loop_ctx, "_runner", None)
    return getattr(runner, "skill_api", None) if runner is not None else None


def _state(loop_ctx: Any) -> Any:
    return getattr(loop_ctx, "state", None)


def _session_id(loop_ctx: Any) -> str:
    return str(getattr(_state(loop_ctx), "session_id", "") or "").strip()


def _agent_id(loop_ctx: Any) -> str:
    return str(getattr(_state(loop_ctx), "agent_id", "") or "").strip()


def _trace_id(loop_ctx: Any) -> str:
    return str(getattr(_state(loop_ctx), "trace_id", "") or "").strip()


def _active_plan_id(active_plan: dict[str, Any] | None) -> str:
    if not isinstance(active_plan, dict):
        return ""
    return str(active_plan.get("plan_id") or "").strip()


def _active_plan_workflow_id(active_plan: dict[str, Any] | None) -> str | None:
    if not isinstance(active_plan, dict):
        return None
    workflow_id = str(active_plan.get("workflow_id") or "").strip()
    return workflow_id or None


def _active_step_ids(active_plan: dict[str, Any] | None) -> set[str]:
    if not isinstance(active_plan, dict):
        return set()
    steps = active_plan.get("steps")
    if not isinstance(steps, list):
        return set()
    return {
        str(step.get("step_id") or "").strip()
        for step in steps
        if isinstance(step, dict) and str(step.get("step_id") or "").strip()
    }


def _merge_redeclared_active_plan(
    active_plan: dict[str, Any] | None, declared: TaskPlan
) -> TaskPlan:
    if not isinstance(active_plan, dict):
        return declared
    existing_steps = {
        str(step.get("step_id") or "").strip(): dict(step)
        for step in list(active_plan.get("steps") or [])
        if isinstance(step, dict) and str(step.get("step_id") or "").strip()
    }
    merged_payload = declared.model_dump(mode="json")
    merged_steps: list[dict[str, Any]] = []
    for raw_step in merged_payload.get("steps") or []:
        step = dict(raw_step) if isinstance(raw_step, dict) else {}
        step_id = str(step.get("step_id") or "").strip()
        existing = existing_steps.get(step_id)
        if existing and str(existing.get("status") or "") in {"completed", "blocked"}:
            for key in (
                "status",
                "output_summary",
                "blocker_type",
                "blocker_details",
            ):
                if key in existing:
                    step[key] = existing[key]
        merged_steps.append(step)
    merged_payload["steps"] = merged_steps
    if bool(active_plan.get("continue_plan_autonomously")):
        merged_payload["continue_plan_autonomously"] = True
    return TaskPlan.model_validate(merged_payload)


def _active_plan_continues_after_step(
    active_plan: dict[str, Any] | None,
    *,
    plan_id: str,
    step_id: str,
) -> bool:
    if not isinstance(active_plan, dict):
        return False
    if str(active_plan.get("plan_id") or "").strip() != str(plan_id or "").strip():
        return False
    if not bool(active_plan.get("continue_plan_autonomously")):
        return False
    steps = [
        dict(step)
        for step in list(active_plan.get("steps") or [])
        if isinstance(step, dict)
    ]
    if not steps:
        return False
    has_remaining = False
    for step in steps:
        current_step_id = str(step.get("step_id") or "").strip()
        status = str(step.get("status") or "pending").strip()
        if current_step_id == str(step_id or "").strip():
            status = "completed"
        if status not in {"completed", "blocked"}:
            has_remaining = True
            break
    return has_remaining


def _validate_workflow_id(
    loop_ctx: Any,
    *,
    workflow_id: str | None,
) -> ActionResult | None:
    workflow_id = str(workflow_id or "").strip() or None
    if workflow_id is None:
        return None
    skill_api = _resolve_skill_api(loop_ctx)
    if skill_api is None or not hasattr(skill_api, "get_workflow"):
        return _failed_result(
            code="PLAN_WORKFLOW_CATALOG_UNAVAILABLE",
            summary="workflow_id requires an available workflow catalog lookup owner.",
            details={"workflow_id": workflow_id},
        )
    try:
        skill_api.get_workflow(workflow_id, agent_id=_agent_id(loop_ctx) or None)
    except Exception:
        return _failed_result(
            code="PLAN_WORKFLOW_NOT_FOUND",
            summary="workflow_id did not resolve to a reusable workflow.",
            details={"workflow_id": workflow_id},
        )
    return None


def _pae_schedule_idle_tick(*, loop_ctx: Any, plan_id: str) -> None:
    try:
        from openminion.modules.brain.loop.proactive_entrypoint import (
            maybe_schedule_idle_tick,
        )

        runner = runner_from_context(loop_ctx) or getattr(loop_ctx, "_runner", None)
        if runner is None:
            return
        cron_api = getattr(runner, "cron_api", None)
        if cron_api is None:
            return
        maybe_schedule_idle_tick(
            cron_store=cron_api,
            session_api=_resolve_session_api(loop_ctx),
            runner=runner,
            session_id=_session_id(loop_ctx),
            agent_id=_agent_id(loop_ctx),
            plan_id=str(plan_id or "").strip(),
            trace_id=_trace_id(loop_ctx) or None,
        )
    except Exception:  # noqa: BLE001
        return


def _pae_cancel_idle_tick(*, loop_ctx: Any, plan_id: str, reason: str) -> None:
    try:
        from openminion.modules.brain.loop.proactive_entrypoint import (
            cancel_idle_tick,
        )

        runner = runner_from_context(loop_ctx) or getattr(loop_ctx, "_runner", None)
        if runner is None:
            return
        cron_api = getattr(runner, "cron_api", None)
        if cron_api is None:
            return
        cancel_idle_tick(
            cron_store=cron_api,
            session_api=_resolve_session_api(loop_ctx),
            session_id=_session_id(loop_ctx),
            agent_id=_agent_id(loop_ctx),
            plan_id=str(plan_id or "").strip(),
            reason=reason,
            trace_id=_trace_id(loop_ctx) or None,
        )
    except Exception:  # noqa: BLE001
        return


def _task_ops_outputs(loop_ctx: Any, task_ops: TaskOps) -> dict[str, Any]:
    outputs: dict[str, Any] = {
        PLAN_TASK_OPS_OUTPUT_KEY: task_ops.model_dump(mode="json")
    }
    task_ctl = _resolve_task_ctl(loop_ctx)
    apply_ops = getattr(task_ctl, "apply_ops", None) if task_ctl is not None else None
    if not callable(apply_ops):
        return outputs
    touched = apply_ops(task_ops, trace_id=_trace_id(loop_ctx) or None)
    outputs[PLAN_TASK_OPS_TOUCHED_TASK_IDS_OUTPUT_KEY] = [str(item) for item in touched]
    return outputs


def _resolve_task_ctl(loop_ctx: Any) -> Any | None:
    direct = getattr(loop_ctx, "task_ctl", None) or getattr(
        loop_ctx, "task_service", None
    )
    if direct is not None:
        return direct
    runner = runner_from_context(loop_ctx)
    if runner is None:
        return None
    return (
        getattr(runner, "task_ctl", None)
        or getattr(runner, "task_service", None)
        or getattr(getattr(runner, "agent", None), "task_ctl", None)
    )


def _sync_goal_plan_declare(loop_ctx: Any, *, plan: TaskPlan) -> None:
    runner = runner_from_context(loop_ctx) or getattr(loop_ctx, "_runner", None)
    goal_runtime = getattr(runner, "goal_runtime", None) if runner is not None else None
    if goal_runtime is None:
        return
    sync = getattr(goal_runtime, "apply_task_plan_signal", None)
    if not callable(sync):
        return
    sync(
        plan_id=plan.plan_id,
        root_goal_id=plan.root_goal_id,
        terminal_status="active",
        reason="task_plan_declared",
    )


def _sync_goal_plan_step(
    loop_ctx: Any,
    *,
    plan_id: str,
    terminal_status: str,
) -> None:
    runner = runner_from_context(loop_ctx) or getattr(loop_ctx, "_runner", None)
    goal_runtime = getattr(runner, "goal_runtime", None) if runner is not None else None
    if goal_runtime is None:
        return
    active_plan = _current_active_plan(loop_ctx)
    root_goal_id = None
    if isinstance(active_plan, dict):
        root_goal_id = str(active_plan.get("root_goal_id") or "").strip() or None
    sync = getattr(goal_runtime, "apply_task_plan_signal", None)
    if not callable(sync):
        return
    sync(
        plan_id=plan_id,
        root_goal_id=root_goal_id,
        terminal_status=terminal_status,
        reason=f"task_plan_{terminal_status}",
    )


def _success_result(*, summary: str, outputs: dict[str, Any]) -> ActionResult:
    return ActionResult(
        command_id=new_uuid(),
        status="success",
        summary=summary,
        outputs=outputs,
    )


def _failed_result(
    *, code: str, summary: str, details: dict[str, Any] | None = None
) -> ActionResult:
    return ActionResult(
        command_id=new_uuid(),
        status="failed",
        summary=summary,
        error=ActionError(
            code=code,
            message=summary,
            details=dict(details or {}),
        ),
    )
