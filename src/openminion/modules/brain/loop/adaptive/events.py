from __future__ import annotations

from typing import Any

from openminion.modules.brain.execution.loop_contracts import (
    ExecutionContext,
)
from openminion.modules.brain.loop.tools import (
    AdaptiveToolLoopOutcome,
    PLAN_TOOL_ATTEMPTED_SCRATCHPAD_KEY,
    PLAN_TOOL_USED_SCRATCHPAD_KEY,
)
from openminion.modules.brain.trailers import (
    TrailerPostprocessService,
)

from ..services import runner_from_context


def _append_task_plan_event(
    ctx: ExecutionContext,
    *,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    runner = runner_from_context(ctx)
    session_api = getattr(runner, "session_api", None) if runner is not None else None
    append_event = getattr(session_api, "append_event", None)
    if not callable(append_event):
        return
    try:
        append_event(
            ctx.state.session_id,
            event_type,
            {"source": "trailer", **payload},
            actor_type="agent",
            actor_id=ctx.state.agent_id,
            trace={"trace_id": ctx.state.trace_id}
            if str(ctx.state.trace_id or "").strip()
            else None,
            importance=2,
            redaction="none",
            status="ok",
        )
    except Exception:  # noqa: BLE001
        return


def _current_active_plan(ctx: ExecutionContext) -> dict[str, Any] | None:
    runner = runner_from_context(ctx)
    session_api = getattr(runner, "session_api", None) if runner is not None else None
    store = getattr(session_api, "store", None)
    get_slice = getattr(store or session_api, "get_slice", None)
    if not callable(get_slice):
        return None
    try:
        raw = get_slice(
            ctx.state.session_id,
            purpose="decide",
            limits={"max_turns": 1, "max_tool_events": 0},
        )
    except TypeError:
        try:
            raw = get_slice(
                session_id=ctx.state.session_id,
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
    if not isinstance(active, dict):
        return None
    return dict(active)


def _active_plan_id(active_plan: dict[str, Any] | None) -> str:
    if not isinstance(active_plan, dict):
        return ""
    return str(active_plan.get("plan_id") or "").strip()


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


def _append_invalid_task_plan_event(
    ctx: ExecutionContext,
    *,
    trailer_type: str,
    reason: str,
    payload: dict[str, Any] | None,
) -> None:
    _append_task_plan_event(
        ctx,
        event_type="task_plan.invalid_trailer",
        payload={
            "trailer_type": trailer_type,
            "reason": reason,
            "payload": dict(payload or {}),
        },
    )


def _progress_payload_is_active(
    ctx: ExecutionContext,
    *,
    trailer_type: str,
    payload: dict[str, Any],
    active_plan: dict[str, Any] | None,
    require_step: bool,
) -> bool:
    active_plan_id = _active_plan_id(active_plan)
    payload_plan_id = str(payload.get("plan_id") or "").strip()
    if not active_plan_id or payload_plan_id != active_plan_id:
        _append_invalid_task_plan_event(
            ctx,
            trailer_type=trailer_type,
            reason="plan_id_mismatch",
            payload=payload,
        )
        return False
    if require_step:
        step_id = str(payload.get("step_id") or "").strip()
        if step_id not in _active_step_ids(active_plan):
            _append_invalid_task_plan_event(
                ctx,
                trailer_type=trailer_type,
                reason="unknown_step_id",
                payload=payload,
            )
            return False
    return True


def _postprocess_adaptive_response_trailers(
    ctx: ExecutionContext,
    loop_outcome: AdaptiveToolLoopOutcome,
    *,
    request_metadata: dict[str, Any] | None = None,
) -> None:
    """Emit trailer.expected / trailer.emitted events for the adaptive final.

    Route-independent trailer postprocess for the adaptive loop path.
    The direct respond path calls the same service from orchestration.
    """
    runner = runner_from_context(ctx)
    session_api = getattr(runner, "session_api", None) if runner is not None else None
    if session_api is None:
        return
    payloads = {
        "task_plan": getattr(loop_outcome, "task_plan", None),
        "task_plan_step_completed": getattr(
            loop_outcome, "task_plan_step_completed", None
        ),
        "task_plan_step_blocked": getattr(loop_outcome, "task_plan_step_blocked", None),
        "task_plan_revision": getattr(loop_outcome, "task_plan_revision", None),
        "task_plan_abandoned": getattr(loop_outcome, "task_plan_abandoned", None),
        "task_plan_completed": getattr(loop_outcome, "task_plan_completed", None),
        "confident_complete": getattr(loop_outcome, "confident_complete", None),
        "session_work_summary": getattr(loop_outcome, "session_work_summary", None),
    }
    service = TrailerPostprocessService()
    service.process(
        emitted_payloads=payloads,
        session_api=session_api,
        session_id=ctx.state.session_id,
        agent_id=ctx.state.agent_id,
        trace_id=str(getattr(ctx.state, "trace_id", "") or ""),
        route="adaptive_final",
        request_metadata=request_metadata,
    )


def _stage_task_plan_events(
    ctx: ExecutionContext,
    loop_outcome: AdaptiveToolLoopOutcome,
) -> None:
    # Structured plan tool calls are already provider-validated and emitted.
    if bool(
        loop_outcome.state.scratchpad.get(PLAN_TOOL_ATTEMPTED_SCRATCHPAD_KEY)
        or loop_outcome.state.scratchpad.get(PLAN_TOOL_USED_SCRATCHPAD_KEY)
    ):
        return
    task_plan = loop_outcome.task_plan
    if isinstance(task_plan, dict) and task_plan:
        new_plan_id = str(task_plan.get("plan_id") or "").strip()
        active_plan_id = _active_plan_id(_current_active_plan(ctx))
        if active_plan_id and active_plan_id != new_plan_id:
            _append_task_plan_event(
                ctx,
                event_type="task_plan.abandoned",
                payload={
                    "plan_id": active_plan_id,
                    "reason": "replaced_by_new_task_plan",
                },
            )
        _append_task_plan_event(
            ctx,
            event_type="task_plan.declared",
            payload={"plan": dict(task_plan)},
        )
    active_plan = _current_active_plan(ctx)
    for event_type, payload, require_step in (
        (
            "task_plan.step_completed",
            loop_outcome.task_plan_step_completed,
            True,
        ),
        ("task_plan.step_blocked", loop_outcome.task_plan_step_blocked, True),
        ("task_plan.abandoned", loop_outcome.task_plan_abandoned, False),
        ("task_plan.completed", loop_outcome.task_plan_completed, False),
    ):
        if not isinstance(payload, dict) or not payload:
            continue
        if _progress_payload_is_active(
            ctx,
            trailer_type=event_type,
            payload=payload,
            active_plan=active_plan,
            require_step=require_step,
        ):
            _append_task_plan_event(ctx, event_type=event_type, payload=payload)
    if isinstance(loop_outcome.task_plan_revision, dict):
        revision = loop_outcome.task_plan_revision
        if _progress_payload_is_active(
            ctx,
            trailer_type="task_plan.revised",
            payload=revision,
            active_plan=active_plan,
            require_step=False,
        ):
            objective = str(
                revision.get("objective") or (active_plan or {}).get("objective") or ""
            ).strip()
            full_plan = {
                "plan_id": str(revision.get("plan_id") or "").strip(),
                "objective": objective,
                "status": "active",
                "steps": list(revision.get("revised_steps") or []),
            }
            _append_task_plan_event(
                ctx,
                event_type="task_plan.revised",
                payload={
                    "plan": full_plan,
                    "reason": str(revision.get("reason") or "").strip(),
                },
            )
