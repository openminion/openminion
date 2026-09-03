from __future__ import annotations

from typing import Any

from openminion.modules.brain.schemas.state import ActionResult
from openminion.modules.context.schemas import TASK_PLAN_TOOL_FAMILIES
from openminion.modules.llm.schemas import Message, ToolSpec
from openminion.modules.task.plan import (
    TaskPlan,
    TaskPlanRevision,
    TaskPlanStepBlocked,
    TaskPlanStepCompleted,
    TaskPlanTerminalSignal,
)

from .task_ops import (
    task_ops_for_plan_declare,
    task_ops_for_step_blocked,
    task_ops_for_step_completed,
)
from .plan import (
    _active_plan_continues_after_step,
    _active_plan_id,
    _active_plan_workflow_id,
    _active_plan_workflow_version_hash,
    _append_invalid_task_plan_event,
    _append_task_plan_event,
    _clear_active_plan_override,
    _current_active_plan,
    _failed_result,
    _merge_redeclared_active_plan,
    _pae_cancel_idle_tick,
    _pae_schedule_idle_tick,
    _payload_is_active,
    _resolve_session_api,
    _set_active_plan_override,
    _success_result,
    _sync_goal_plan_declare,
    _sync_goal_plan_step,
    _task_ops_outputs,
    _update_active_plan_step_status,
    _validate_workflow_id,
)

PLAN_TOOL_NAME = "plan"
PLAN_ACTION_DECLARE = "declare"
PLAN_ACTION_STEP_COMPLETED = "step_completed"
PLAN_ACTION_STEP_BLOCKED = "step_blocked"
PLAN_ACTION_REVISE = "revise"
PLAN_ACTION_ABANDON = "abandon"
PLAN_ACTION_COMPLETE = "complete"
PLAN_TOOL_ATTEMPTED_SCRATCHPAD_KEY = "plan_tool.attempted"
PLAN_TOOL_USED_SCRATCHPAD_KEY = "plan_tool.used"
PLAN_TOOL_ACTIONS_SCRATCHPAD_KEY = "plan_tool.actions"
PLAN_CLOSEOUT_SALVAGE_TEXT_SCRATCHPAD_KEY = "plan_tool.closeout_salvage_text"
PLAN_CONTINUE_AUTONOMOUSLY_OUTPUT_KEY = "plan.continue_plan_autonomously"
PLAN_ACTIONS_ELIGIBLE_FOR_CONTINUATION = frozenset(
    {
        PLAN_ACTION_DECLARE,
        PLAN_ACTION_STEP_COMPLETED,
        PLAN_ACTION_REVISE,
    }
)
PLAN_TOOL_ACTIONS = frozenset(
    {
        PLAN_ACTION_DECLARE,
        PLAN_ACTION_STEP_COMPLETED,
        PLAN_ACTION_STEP_BLOCKED,
        PLAN_ACTION_REVISE,
        PLAN_ACTION_ABANDON,
        PLAN_ACTION_COMPLETE,
    }
)
PLAN_STEP_STATUSES = ("pending", "in_progress", "completed", "blocked")
PLAN_STEP_DIFFICULTIES = ("low", "medium", "high")


def is_plan_family_tool_name(tool_name: Any) -> bool:
    name = str(tool_name or "").strip()
    if not name:
        return False
    return name == PLAN_TOOL_NAME or name.split(".", 1)[0] == PLAN_TOOL_NAME


def _plan_step_input_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "step_id": {
                "type": "string",
                "description": "Stable id for this step within the plan.",
            },
            "description": {
                "type": "string",
                "description": "Short model-authored description of the step.",
            },
            "status": {
                "type": "string",
                "enum": list(PLAN_STEP_STATUSES),
                "description": "Current step status.",
            },
            "estimated_difficulty": {
                "type": "string",
                "enum": list(PLAN_STEP_DIFFICULTIES),
                "description": "Coarse effort estimate for this step.",
            },
            "depends_on": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Step ids that must be completed first.",
            },
            "tool_families": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": sorted(TASK_PLAN_TOOL_FAMILIES),
                },
                "description": "Bounded tool-family hints, not concrete tool ids.",
            },
            "output_summary": {
                "type": "string",
                "description": "Bounded summary of completed step output.",
            },
            "blocker_type": {"type": "string"},
            "blocker_details": {"type": "string"},
        },
        "required": ["step_id", "description"],
        "additionalProperties": False,
    }


def build_plan_tool_spec() -> ToolSpec:
    """Loop-control plan tool spec.

    This is not a normal runtime tool. The adaptive loop handles it locally
    and records canonical `task_plan.*` session events.
    """

    step_schema = _plan_step_input_schema()
    return ToolSpec(
        name=PLAN_TOOL_NAME,
        description=(
            "Record or update the session task plan. Use this loop-control tool "
            "for multi-step work instead of writing task-plan control trailers."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": sorted(PLAN_TOOL_ACTIONS),
                    "description": "Plan lifecycle action to record.",
                },
                "plan_id": {"type": "string"},
                "objective": {
                    "type": "string",
                    "description": (
                        "Optional human-readable objective. When omitted, the "
                        "runtime falls back to plan_id."
                    ),
                },
                "workflow_id": {
                    "type": "string",
                    "description": (
                        "Optional reusable workflow id from the skill-owned "
                        "workflow catalog."
                    ),
                },
                "workflow_version_hash": {
                    "type": "string",
                    "description": (
                        "Optional active skill version hash that pins workflow_id."
                    ),
                },
                "root_goal_id": {
                    "type": "string",
                    "description": (
                        "Optional reverse link to the root goal that owns "
                        "this task plan."
                    ),
                },
                "criterion_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "steps": {
                    "type": "array",
                    "items": step_schema,
                    "description": "Required for action=declare.",
                },
                "step_id": {"type": "string"},
                "outcome": {"type": "string"},
                "output_summary": {"type": "string"},
                "blocker_type": {"type": "string"},
                "blocker_details": {"type": "string"},
                "reason": {"type": "string"},
                "revision_id": {"type": "string"},
                "predecessor_revision_id": {"type": "string"},
                "verifier_refs": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "revised_steps": {
                    "type": "array",
                    "items": step_schema,
                    "description": "Required for action=revise.",
                },
                "continue_plan_autonomously": {
                    "type": "boolean",
                    "description": (
                        "Request a capped follow-up turn after declare, "
                        "step_completed, or revise. Ignored for terminal actions."
                    ),
                },
            },
            "required": ["action", "plan_id"],
            "additionalProperties": False,
        },
    )


def with_plan_tool_spec(tool_specs: list[Any] | tuple[Any, ...]) -> list[Any]:
    specs = [
        spec
        for spec in list(tool_specs or [])
        if str(getattr(spec, "name", "") or "").strip() != PLAN_TOOL_NAME
    ]
    specs.append(build_plan_tool_spec())
    return specs


def plan_tool_enabled(profile: Any) -> bool:
    # `plan` is a loop-local control surface, not a provider-dispatched tool.
    # It remains available when ordinary runtime tools are suppressed.
    return bool(getattr(profile, "allow_plan_tool", True))


def plan_tool_call_advances_active_plan(
    loop_ctx: Any,
    arguments: dict[str, Any],
) -> bool:
    action = str(arguments.get("action", "") or "").strip()
    if action not in {
        PLAN_ACTION_STEP_COMPLETED,
        PLAN_ACTION_STEP_BLOCKED,
        PLAN_ACTION_ABANDON,
        PLAN_ACTION_COMPLETE,
    }:
        return False
    active_plan = _current_active_plan(loop_ctx)
    plan_id = str(arguments.get("plan_id", "") or "").strip()
    if _active_plan_id(active_plan) != plan_id:
        return False
    if action == PLAN_ACTION_ABANDON:
        return True
    if action == PLAN_ACTION_COMPLETE:
        return not _unresolved_step_ids(active_plan)
    step_id = str(arguments.get("step_id", "") or "").strip()
    for step in list((active_plan or {}).get("steps") or []):
        if not isinstance(step, dict):
            continue
        if str(step.get("step_id", "") or "").strip() != step_id:
            continue
        return str(step.get("status", "pending") or "pending").strip() not in {
            "blocked",
            "completed",
        }
    return False


def unresolved_active_plan_step_ids(loop_ctx: Any) -> tuple[str, ...]:
    return tuple(_unresolved_step_ids(_current_active_plan(loop_ctx)))


def completable_active_plan_id(loop_ctx: Any) -> str:
    active_plan = _current_active_plan(loop_ctx)
    plan_id = _active_plan_id(active_plan)
    if not plan_id or _unresolved_step_ids(active_plan):
        return ""
    return plan_id


def complete_active_plan_if_ready(loop_ctx: Any) -> dict[str, str] | None:
    plan_id = completable_active_plan_id(loop_ctx)
    if not plan_id:
        return None
    payload = {
        "plan_id": plan_id,
        "reason": "all_steps_completed_at_success",
    }
    result = _handle_terminal(
        loop_ctx=loop_ctx,
        arguments=payload,
        event_type="task_plan.completed",
    )
    if result.status != "success":
        raise RuntimeError(result.summary)
    return payload


def append_plan_closeout_guidance(
    loop_state: Any,
    arguments: dict[str, Any],
    action_result: Any,
) -> None:
    if (
        str(getattr(action_result, "status", "") or "") != "success"
        or str(arguments.get("action", "") or "").strip() != PLAN_ACTION_COMPLETE
    ):
        return
    closeout_text = str(
        loop_state.scratchpad.pop(PLAN_CLOSEOUT_SALVAGE_TEXT_SCRATCHPAD_KEY, "") or ""
    ).strip()
    original_request = next(
        (
            str(message.content or "").strip()
            for message in loop_state.messages
            if message.role == "user" and str(message.content or "").strip()
        ),
        "",
    )
    if closeout_text:
        loop_state.scratchpad["typed_finalization_status_salvage_text"] = closeout_text
        guidance = (
            "The active task plan is now complete and the full user-facing answer "
            "is already preserved. Return only the structured finalization_status "
            "signal now. Do not repeat the answer or call more tools."
        )
    else:
        guidance = (
            "The active task plan is now complete. Return the final user-facing "
            "answer now. Preserve every "
            "user-specified answer format, ordering, and exact-response constraint. "
            "Include the structured finalization_status signal and do not call more "
            f"tools.\n\nOriginal request:\n{original_request}"
        )
    loop_state.messages.append(Message(role="system", content=guidance))


def with_enabled_plan_tool_spec(
    profile: Any,
    tool_specs: list[Any] | tuple[Any, ...],
) -> list[Any]:
    if not plan_tool_enabled(profile):
        return list(tool_specs or [])
    return with_plan_tool_spec(tool_specs)


def handle_plan_tool_call(
    *,
    loop_ctx: Any,
    arguments: dict[str, Any],
) -> ActionResult:
    action = str(arguments.get("action", "") or "").strip()
    if action not in PLAN_TOOL_ACTIONS:
        return _failed_result(
            code="PLAN_ACTION_UNSUPPORTED",
            summary=f"Unsupported plan action: {action or '<missing>'}",
            details={"action": action},
        )
    session_api = _resolve_session_api(loop_ctx)
    append_event = getattr(session_api, "append_event", None) if session_api else None
    if not callable(append_event):
        return _failed_result(
            code="PLAN_SESSION_UNAVAILABLE",
            summary="Plan tool could not access the session event store.",
        )
    try:
        if action == PLAN_ACTION_DECLARE:
            return _handle_declare(loop_ctx=loop_ctx, arguments=arguments)
        if action == PLAN_ACTION_STEP_COMPLETED:
            return _handle_step_completed(loop_ctx=loop_ctx, arguments=arguments)
        if action == PLAN_ACTION_STEP_BLOCKED:
            return _handle_step_blocked(loop_ctx=loop_ctx, arguments=arguments)
        if action == PLAN_ACTION_REVISE:
            return _handle_revise(loop_ctx=loop_ctx, arguments=arguments)
        if action == PLAN_ACTION_ABANDON:
            return _handle_terminal(
                loop_ctx=loop_ctx,
                arguments=arguments,
                event_type="task_plan.abandoned",
            )
        if action == PLAN_ACTION_COMPLETE:
            return _handle_terminal(
                loop_ctx=loop_ctx,
                arguments=arguments,
                event_type="task_plan.completed",
            )
    except Exception as exc:  # noqa: BLE001
        _append_invalid_task_plan_event(
            loop_ctx,
            trailer_type=f"plan.{action}",
            reason="validation_error",
            payload=arguments,
        )
        return _failed_result(
            code="PLAN_VALIDATION_FAILED",
            summary=f"Plan action validation failed: {exc}",
            details={"action": action},
        )
    return _failed_result(
        code="PLAN_ACTION_UNHANDLED",
        summary=f"Plan action was not handled: {action}",
    )


def _handle_declare(*, loop_ctx: Any, arguments: dict[str, Any]) -> ActionResult:
    plan = TaskPlan.model_validate(
        {
            "plan_id": arguments.get("plan_id"),
            "objective": arguments.get("objective") or arguments.get("plan_id"),
            "workflow_id": arguments.get("workflow_id"),
            "workflow_version_hash": arguments.get("workflow_version_hash"),
            "root_goal_id": arguments.get("root_goal_id"),
            "criterion_ids": list(arguments.get("criterion_ids") or []),
            "status": "active",
            "steps": list(arguments.get("steps") or []),
            "continue_plan_autonomously": bool(
                arguments.get("continue_plan_autonomously") or False
            ),
        }
    )
    workflow_failure = _validate_workflow_id(
        loop_ctx,
        workflow_id=plan.workflow_id,
        workflow_version_hash=plan.workflow_version_hash,
    )
    if workflow_failure is not None:
        return workflow_failure
    active_plan = _current_active_plan(loop_ctx)
    active_plan_id = _active_plan_id(active_plan)
    if active_plan_id and active_plan_id != plan.plan_id:
        _append_task_plan_event(
            loop_ctx,
            event_type="task_plan.abandoned",
            payload={
                "plan_id": active_plan_id,
                "reason": "replaced_by_new_task_plan",
            },
        )
        _pae_cancel_idle_tick(
            loop_ctx=loop_ctx,
            plan_id=active_plan_id,
            reason="plan_replaced",
        )
    elif active_plan_id == plan.plan_id:
        plan = _merge_redeclared_active_plan(active_plan, plan)
    _append_task_plan_event(
        loop_ctx,
        event_type="task_plan.declared",
        payload={"plan": plan.model_dump(mode="json")},
    )
    _set_active_plan_override(loop_ctx, plan.model_dump(mode="json"))
    _sync_goal_plan_declare(loop_ctx, plan=plan)
    task_ops = task_ops_for_plan_declare(plan)
    outputs: dict[str, Any] = {
        "action": PLAN_ACTION_DECLARE,
        "plan_id": plan.plan_id,
        "task_plan": plan.model_dump(mode="json"),
        **_task_ops_outputs(loop_ctx, task_ops),
    }
    if plan.continue_plan_autonomously:
        outputs[PLAN_CONTINUE_AUTONOMOUSLY_OUTPUT_KEY] = True
        _pae_schedule_idle_tick(loop_ctx=loop_ctx, plan_id=plan.plan_id)
    return _success_result(
        summary=f"Recorded task plan: {plan.plan_id}",
        outputs=outputs,
    )


def _handle_step_completed(*, loop_ctx: Any, arguments: dict[str, Any]) -> ActionResult:
    completed = TaskPlanStepCompleted.model_validate(arguments)
    active_plan = _current_active_plan(loop_ctx)
    if not _payload_is_active(
        loop_ctx,
        event_type="task_plan.step_completed",
        plan_id=completed.plan_id,
        step_id=completed.step_id,
        require_step=True,
        payload=completed.model_dump(mode="json"),
    ):
        return _failed_result(
            code="PLAN_STEP_NOT_ACTIVE",
            summary="Step completion did not match the active task plan.",
            details={"plan_id": completed.plan_id, "step_id": completed.step_id},
        )
    payload = completed.model_dump(mode="json")
    effective_continue = _active_plan_continues_after_step(
        active_plan,
        plan_id=completed.plan_id,
        step_id=completed.step_id,
        continue_requested=completed.continue_plan_autonomously,
    )
    payload["continue_plan_autonomously"] = effective_continue
    task_ops = task_ops_for_step_completed(completed)
    _append_task_plan_event(
        loop_ctx, event_type="task_plan.step_completed", payload=payload
    )
    _update_active_plan_step_status(
        loop_ctx,
        plan_id=completed.plan_id,
        step_id=completed.step_id,
        status="completed",
        output_summary=completed.output_summary,
    )
    _sync_goal_plan_step(
        loop_ctx, plan_id=completed.plan_id, terminal_status="completed"
    )
    outputs: dict[str, Any] = {
        "action": PLAN_ACTION_STEP_COMPLETED,
        **payload,
        **_task_ops_outputs(loop_ctx, task_ops),
    }
    if effective_continue:
        outputs[PLAN_CONTINUE_AUTONOMOUSLY_OUTPUT_KEY] = True
    return _success_result(
        summary=f"Recorded completed step: {completed.step_id}",
        outputs=outputs,
    )


def _handle_step_blocked(*, loop_ctx: Any, arguments: dict[str, Any]) -> ActionResult:
    blocked = TaskPlanStepBlocked.model_validate(arguments)
    if not _payload_is_active(
        loop_ctx,
        event_type="task_plan.step_blocked",
        plan_id=blocked.plan_id,
        step_id=blocked.step_id,
        require_step=True,
        payload=blocked.model_dump(mode="json"),
    ):
        return _failed_result(
            code="PLAN_STEP_NOT_ACTIVE",
            summary="Step block did not match the active task plan.",
            details={"plan_id": blocked.plan_id, "step_id": blocked.step_id},
        )
    payload = blocked.model_dump(mode="json")
    task_ops = task_ops_for_step_blocked(blocked)
    _append_task_plan_event(
        loop_ctx, event_type="task_plan.step_blocked", payload=payload
    )
    _update_active_plan_step_status(
        loop_ctx,
        plan_id=blocked.plan_id,
        step_id=blocked.step_id,
        status="blocked",
        blocker_type=blocked.blocker_type,
        blocker_details=blocked.blocker_details,
    )
    _sync_goal_plan_step(loop_ctx, plan_id=blocked.plan_id, terminal_status="blocked")
    _pae_cancel_idle_tick(
        loop_ctx=loop_ctx,
        plan_id=blocked.plan_id,
        reason="step_blocked",
    )
    return _success_result(
        summary=f"Recorded blocked step: {blocked.step_id}",
        outputs={
            "action": PLAN_ACTION_STEP_BLOCKED,
            **payload,
            **_task_ops_outputs(loop_ctx, task_ops),
        },
    )


def _handle_revise(*, loop_ctx: Any, arguments: dict[str, Any]) -> ActionResult:
    revision = TaskPlanRevision.model_validate(arguments)
    active_plan = _current_active_plan(loop_ctx)
    if not _payload_is_active(
        loop_ctx,
        event_type="task_plan.revised",
        plan_id=revision.plan_id,
        step_id="",
        require_step=False,
        payload=revision.model_dump(mode="json"),
    ):
        return _failed_result(
            code="PLAN_REVISION_NOT_ACTIVE",
            summary="Plan revision did not match the active task plan.",
            details={"plan_id": revision.plan_id},
        )
    full_plan = revision.to_task_plan(
        fallback_objective=str((active_plan or {}).get("objective") or ""),
        fallback_workflow_id=_active_plan_workflow_id(active_plan),
        fallback_workflow_version_hash=_active_plan_workflow_version_hash(active_plan),
        fallback_criterion_ids=list((active_plan or {}).get("criterion_ids") or []),
    )
    workflow_failure = _validate_workflow_id(
        loop_ctx,
        workflow_id=full_plan.workflow_id,
        workflow_version_hash=full_plan.workflow_version_hash,
    )
    if workflow_failure is not None:
        return workflow_failure
    payload = {
        "plan": full_plan.model_dump(mode="json"),
        "revision": revision.model_dump(mode="json"),
        "reason": revision.reason,
    }
    _append_task_plan_event(loop_ctx, event_type="task_plan.revised", payload=payload)
    _set_active_plan_override(loop_ctx, full_plan.model_dump(mode="json"))
    outputs: dict[str, Any] = {
        "action": PLAN_ACTION_REVISE,
        "plan_id": revision.plan_id,
        "task_plan.revision": revision.model_dump(mode="json"),
    }
    if revision.continue_plan_autonomously:
        outputs[PLAN_CONTINUE_AUTONOMOUSLY_OUTPUT_KEY] = True
        _pae_schedule_idle_tick(loop_ctx=loop_ctx, plan_id=revision.plan_id)
    return _success_result(
        summary=f"Recorded plan revision: {revision.plan_id}",
        outputs=outputs,
    )


def _handle_terminal(
    *, loop_ctx: Any, arguments: dict[str, Any], event_type: str
) -> ActionResult:
    signal = TaskPlanTerminalSignal.model_validate(arguments)
    if not _payload_is_active(
        loop_ctx,
        event_type=event_type,
        plan_id=signal.plan_id,
        step_id="",
        require_step=False,
        payload=signal.model_dump(mode="json"),
    ):
        return _failed_result(
            code="PLAN_TERMINAL_NOT_ACTIVE",
            summary="Terminal plan signal did not match the active task plan.",
            details={"plan_id": signal.plan_id},
        )
    payload = signal.model_dump(mode="json")
    if event_type == "task_plan.completed":
        unresolved_step_ids = _unresolved_step_ids(_current_active_plan(loop_ctx))
        if unresolved_step_ids:
            return _failed_result(
                code="PLAN_STEPS_UNRESOLVED",
                summary="Task plan still has unresolved steps.",
                details={"plan_id": signal.plan_id, "step_ids": unresolved_step_ids},
            )
        _sync_goal_plan_step(
            loop_ctx,
            plan_id=signal.plan_id,
            terminal_status="completed",
        )
    _append_task_plan_event(loop_ctx, event_type=event_type, payload=payload)
    _clear_active_plan_override(loop_ctx)
    _pae_cancel_idle_tick(
        loop_ctx=loop_ctx,
        plan_id=signal.plan_id,
        reason=event_type.removeprefix("task_plan."),
    )
    return _success_result(
        summary=f"Recorded {event_type}: {signal.plan_id}",
        outputs={"action": event_type.removeprefix("task_plan."), **payload},
    )


def _unresolved_step_ids(active_plan: dict[str, Any] | None) -> list[str]:
    return [
        str(step.get("step_id") or "<unknown>").strip()
        for step in list((active_plan or {}).get("steps") or [])
        if isinstance(step, dict)
        and str(step.get("status") or "pending").strip().lower() != "completed"
    ]
