"""Turn-entry runtime implementation for execution dispatch."""

from dataclasses import dataclass
from typing import Any

from ....config import fixed_act_profile_from_profile
from ....constants import (
    BRAIN_ACTION_STATUS_FAILED,
    BRAIN_DECISION_ROUTE_ACT,
    BRAIN_STATE_ACTIVE,
    BRAIN_STATE_DONE,
    BRAIN_STATE_ERROR,
    BRAIN_STATE_WAITING_USER,
)
from ....diagnostics.events import CanonicalEventLogger
from ....diagnostics.telemetry import emit_request_readiness_operation
from ....diagnostics.transitions import transition
from ....loop.clarify import sync_llm_clarify_context_from_decision
from ....loop.context.pending_turn import sync_pending_turn_context_from_decision
from ....runner.tick.context import _runner_delegate
from ....schemas import ActDecision, ActionError, ActionResult
from ...decide_contract import BRAIN_DECIDE_BLOCKER_FAMILY_INTERNAL_FAILURE
from ...dispatch import _decision_route_name
from ...intent_state import (
    clear_working_route,
    record_decision_metadata,
    record_working_route,
)
from ...lifecycle import set_phase
from ...preflight import ValidationResult
from openminion.modules.prompting.continuation import (
    build_plan_checkpoint_continuation_message,
)


@dataclass(slots=True)
class DispatchRuntimeContext:
    effective_user_input: str | None
    fixed_act_profile: str | None


def dispatch(*, runner: Any, state: Any, logger: CanonicalEventLogger, request: Any):
    try:
        validation_attempts = 0
        context = DispatchRuntimeContext(
            effective_user_input=None
            if request.consume_user_input_for_command
            else request.user_input,
            fixed_act_profile=fixed_act_profile_from_profile(
                getattr(runner, "profile", None)
            ),
        )
        disabled_wait = _disabled_handoff_wait_response(
            runner=runner,
            state=state,
            logger=logger,
            user_input=context.effective_user_input,
        )
        if disabled_wait is not None:
            return disabled_wait
        while True:
            if not request.skip_decide:
                request.decision = _run_decide_phase(
                    runner=runner,
                    state=state,
                    logger=logger,
                    request=request,
                    user_input=context.effective_user_input,
                )
            raw_entry_act_profile = (
                str(getattr(request.decision, "act_profile", "") or "").strip() or None
            )
            _bootstrap_act_route(
                runner=runner,
                state=state,
                logger=logger,
                request=request,
                user_input=context.effective_user_input,
                fixed_act_profile=context.fixed_act_profile,
            )
            _emit_entry_event(
                state=state,
                logger=logger,
                decision=request.decision,
                raw_act_profile=raw_entry_act_profile,
            )
            override = _maybe_return_meta_override(
                runner=runner,
                state=state,
                logger=logger,
                request=request,
                user_input=context.effective_user_input,
            )
            if override is not None:
                return override
            validation_decision = _decision_for_validation(
                state=state,
                request=request,
                user_input=context.effective_user_input,
                fixed_act_profile=context.fixed_act_profile,
            )
            accepted = _accept_or_redecide(
                runner=runner,
                state=state,
                logger=logger,
                request=request,
                user_input=context.effective_user_input,
                validation_decision=validation_decision,
                validation_attempts=validation_attempts,
            )
            if accepted is True:
                break
            if isinstance(accepted, int):
                validation_attempts = accepted
                continue
            return accepted
        return _invoke_and_finalize(
            runner=runner,
            state=state,
            logger=logger,
            request=request,
        )
    except Exception as exc:  # noqa: BLE001
        return _dispatch_runtime_error(
            runner=runner,
            state=state,
            logger=logger,
            user_input=context.effective_user_input,
            exc=exc,
        )


def _disabled_handoff_wait_response(
    *,
    runner: Any,
    state: Any,
    logger: CanonicalEventLogger,
    user_input: str | None,
) -> Any | None:
    if bool(getattr(getattr(runner, "options", None), "request_handoff_enabled", False)):
        return None
    if str(user_input or "").strip():
        return None
    readiness = getattr(state, "request_readiness", None)
    readiness_state = str(getattr(readiness, "state", "") or "").strip()
    if readiness_state not in {
        "needs_user",
        "needs_plan_review",
        "needs_operation_approval",
        "blocked",
    }:
        return None
    logger.emit(
        "brain.request_handoff.disabled_wait",
        {"state": readiness_state},
        trace_id=state.trace_id,
        status="warning",
    )
    status = (
        BRAIN_STATE_ERROR
        if readiness_state == "blocked"
        else BRAIN_STATE_WAITING_USER
    )
    return _runner_delegate(
        "_respond_with_meta",
        runner,
        state=state,
        logger=logger,
        message="Request handoff is disabled for this in-flight waiting state.",
        status=status,
    )


def _run_decide_phase(
    *,
    runner: Any,
    state: Any,
    logger: CanonicalEventLogger,
    request: Any,
    user_input: str | None,
) -> Any:
    set_phase(runner, state=state, phase="DECIDE")
    return _runner_delegate(
        "_decide",
        runner,
        state=state,
        user_input=user_input,
        logger=logger,
        forced_tools=request.forced_tools,
        capability_category=request.capability_category,
    )


def _bootstrap_act_route(
    *,
    runner: Any,
    state: Any,
    logger: CanonicalEventLogger,
    request: Any,
    user_input: str | None,
    fixed_act_profile: str | None,
) -> None:
    entry_barrel = _entry_barrel()
    raw_act_profile = str(getattr(request.decision, "act_profile", "") or "").strip()
    raw_execution_target_kind = str(
        getattr(getattr(request.decision, "execution_target", None), "kind", "") or ""
    ).strip()
    if _decision_route_name(request.decision) == BRAIN_DECISION_ROUTE_ACT:
        route = getattr(
            request.decision, "_pre_resolved_act_route", None
        ) or entry_barrel.resolve_working_act_route(
            decision=request.decision,
            state=state,
            default_act_profile=fixed_act_profile,
            has_new_user_input=bool(str(user_input or "").strip()),
        )
        request.decision = entry_barrel.apply_resolved_act_route(
            decision=request.decision, route=route
        )
        record_working_route(
            state=state,
            act_profile=route.act_profile,
            execution_target_kind=getattr(route.execution_target, "kind", None),
            source=route.source,
        )
        logger.emit(
            "brain.act.bootstrap",
            {
                "raw_act_profile": raw_act_profile or None,
                "raw_execution_target_kind": raw_execution_target_kind or None,
                "resolved_act_profile": route.act_profile,
                "resolved_execution_target_kind": str(
                    getattr(route.execution_target, "kind", "") or ""
                ).strip()
                or None,
                "source": route.source,
            },
            trace_id=state.trace_id,
        )
        return
    clear_working_route(state=state)


def _emit_entry_event(
    *,
    state: Any,
    logger: CanonicalEventLogger,
    decision: Any,
    raw_act_profile: str | None = None,
) -> None:
    logger.emit(
        "brain.entry",
        {
            "route": _decision_route_name(decision),
            "confidence": getattr(decision, "confidence", 0.5),
            "reason_code": str(getattr(decision, "reason_code", "") or "").strip()
            or None,
            "act_profile": raw_act_profile,
            "resolved_act_profile": str(
                getattr(state, "working_act_profile", "") or ""
            ).strip()
            or None,
        },
        trace_id=state.trace_id,
    )


def _maybe_return_meta_override(
    *,
    runner: Any,
    state: Any,
    logger: CanonicalEventLogger,
    request: Any,
    user_input: str | None,
):
    meta_before_plan = _runner_delegate(
        "_evaluate_meta",
        runner,
        state=state,
        logger=logger,
        hook="before_plan",
        user_input=user_input,
        decision=request.decision,
    )
    if meta_before_plan is None:
        return None
    _runner_delegate(
        "_apply_meta_directive",
        runner,
        state=state,
        directive=meta_before_plan.directive,
        logger=logger,
        hook="before_plan",
        meta_state=meta_before_plan.meta_state.value,
    )
    return _runner_delegate(
        "_meta_override_response",
        runner,
        state=state,
        logger=logger,
        directive=meta_before_plan.directive,
        fallback_message="I need clarification before planning.",
    )


def _decision_for_validation(
    *, state: Any, request: Any, user_input: str | None, fixed_act_profile: str | None
) -> Any | None:
    entry_barrel = _entry_barrel()
    decision = (
        request.decision
        if hasattr(request.decision, "route") or hasattr(request.decision, "mode")
        else None
    )
    if (
        decision is not None
        and decision.route == BRAIN_DECISION_ROUTE_ACT
        and str(getattr(decision, "reason_code", "") or "").strip()
        in {
            "resume_existing_plan",
            "plan_continuation_after_deny",
            "confirmation_replay_validation",
        }
        and list(getattr(decision, "_seeded_commands", []) or [])
    ):
        seeded_commands = entry_barrel._copied_seeded_commands(
            list(getattr(decision, "_seeded_commands", []) or [])
        )
        current_sub_intent_ids = entry_barrel._seeded_sub_intent_ids(seeded_commands)
        if current_sub_intent_ids:
            replay_decision = ActDecision(
                confidence=float(getattr(decision, "confidence", 1.0) or 1.0),
                reason_code=str(
                    getattr(decision, "reason_code", "") or "resume_existing_plan"
                ).strip(),
                sub_intents=list(current_sub_intent_ids),
                rationale=str(getattr(decision, "rationale", "") or "").strip(),
            )
            replay_decision._seeded_commands = seeded_commands
            decision = replay_decision
    if (
        decision is None
        and request.skip_decide
        and state.plan is not None
        and 0 <= state.cursor < len(state.plan.steps)
    ):
        try:
            remaining_commands = entry_barrel._copied_seeded_commands(
                list(state.plan.steps[state.cursor :]) or []
            )
            replay_decision = ActDecision(
                confidence=1.0,
                reason_code="confirmation_replay_validation",
                sub_intents=list(getattr(state, "decision_sub_intents", []) or []),
                rationale=str(getattr(state, "decision_rationale", "") or "").strip(),
            )
            replay_decision._seeded_commands = remaining_commands
            decision = replay_decision
        except Exception:
            decision = None
    if (
        decision is not None
        and _decision_route_name(decision) == BRAIN_DECISION_ROUTE_ACT
    ):
        validation_route = entry_barrel.resolve_working_act_route(
            decision=decision,
            state=state,
            default_act_profile=fixed_act_profile,
            has_new_user_input=bool(str(user_input or "").strip()),
        )
        decision = entry_barrel.apply_resolved_act_route(
            decision=decision, route=validation_route
        )
    return decision


def _accept_or_redecide(
    *,
    runner: Any,
    state: Any,
    logger: CanonicalEventLogger,
    request: Any,
    user_input: str | None,
    validation_decision: Any | None,
    validation_attempts: int,
):
    entry_barrel = _entry_barrel()
    validation_result = entry_barrel._validate_decision_readiness(
        state=state, decision=validation_decision
    )
    mode_preparation = None
    if validation_result is None:
        mode_preparation = entry_barrel.prepare_decision_direct(
            runner,
            state=state,
            decision=request.decision,
            user_input=user_input,
            logger=logger,
            emit_status_updates=False,
        )
        if mode_preparation is not None:
            if mode_preparation.mode_result is not None:
                return mode_preparation.mode_result.to_step_output()
            request.consume_user_input_for_command = (
                request.consume_user_input_for_command
                or mode_preparation.consume_user_input_for_command
            )
    if validation_decision is not None and validation_result is None:
        validation_result = entry_barrel.validate_decision_direct(
            runner,
            state=state,
            decision=validation_decision,
            user_input=user_input,
            logger=logger,
            preparation=mode_preparation,
        )
    if validation_result is None or validation_result.passed:
        _record_accepted_decision(
            runner=runner,
            state=state,
            logger=logger,
            request=request,
            user_input=user_input,
        )
        return True
    logger.emit(
        "brain.entry.validation_failed",
        {
            "blocker_family": BRAIN_DECIDE_BLOCKER_FAMILY_INTERNAL_FAILURE,
            "code": validation_result.code,
            "message": validation_result.feedback,
            "details": validation_result.details,
        },
        trace_id=state.trace_id,
        status="warning",
    )
    if validation_result.code == "repeated_continuation_command":
        state.plan = None
        state.cursor = 0
    if request.skip_decide or validation_attempts >= 1:
        return _runner_delegate(
            "_respond_with_meta",
            runner,
            state=state,
            logger=logger,
            message=(
                "I couldn't produce a valid execution plan for your request on this turn. "
                f"{validation_result.feedback}"
            ),
            status=BRAIN_STATE_DONE,
        )
    _redecide_after_validation_failure(
        runner=runner,
        state=state,
        logger=logger,
        request=request,
        user_input=user_input,
        validation_result=validation_result,
        validation_attempts=validation_attempts + 1,
    )
    return validation_attempts + 1


def _record_accepted_decision(
    *,
    runner: Any,
    state: Any,
    logger: CanonicalEventLogger,
    request: Any,
    user_input: str | None,
) -> None:
    entry_barrel = _entry_barrel()
    if hasattr(request.decision, "route"):
        decision_plan = (
            state.plan
            if str(getattr(request.decision, "reason_code", "") or "").strip()
            in {
                "resume_existing_plan",
                "confirmation_replay",
                "plan_continuation_after_deny",
            }
            else None
        )
        record_decision_metadata(
            state=state,
            decision=request.decision,
            plan=decision_plan,
            capability_category=request.capability_category,
        )
        try:
            emit_request_readiness_operation(
                telemetryctl=getattr(runner, "telemetryctl", None),
                session_id=str(getattr(state, "session_id", "") or ""),
                turn_id=str(getattr(state, "trace_id", "") or ""),
                readiness=getattr(request.decision, "request_readiness", None),
            )
        except RuntimeError as exc:  # pragma: no cover - defensive telemetry guard
            logger.emit(
                "brain.request_readiness.telemetry_failed",
                {"error_type": type(exc).__name__},
                trace_id=getattr(state, "trace_id", None),
                status="warning",
            )
        sync_llm_clarify_context_from_decision(
            state=state,
            decision=request.decision,
            user_input=user_input,
            logger=logger,
        )
        sync_pending_turn_context_from_decision(
            state=state,
            decision=request.decision,
            user_input=user_input,
        )
        entry_barrel._sync_typed_decision_signals(
            runner=runner, state=state, decision=request.decision
        )
        decision_memory_refs = entry_barrel.write_decision_memory(
            runner,
            state=state,
            decision=request.decision,
            logger=logger,
        )
        if decision_memory_refs:
            existing_refs = set(state.decision_memory_refs)
            state.decision_memory_refs.extend(
                ref for ref in decision_memory_refs if ref not in existing_refs
            )


def _redecide_after_validation_failure(
    *,
    runner: Any,
    state: Any,
    logger: CanonicalEventLogger,
    request: Any,
    user_input: str | None,
    validation_result: ValidationResult,
    validation_attempts: int,
) -> None:
    feedback_constraint = "DECISION_VALIDATION_FEEDBACK: " + str(
        validation_result.feedback or "Decision validation failed."
    )
    state.constraints.append(feedback_constraint)
    try:
        request.skip_decide = False
        request.decision = _run_decide_phase(
            runner=runner,
            state=state,
            logger=logger,
            request=request,
            user_input=user_input,
        )
    finally:
        if feedback_constraint in state.constraints:
            state.constraints.remove(feedback_constraint)
    request.skip_decide = True
    logger.emit(
        "brain.entry.validation_redecide",
        {"attempt": validation_attempts, "feedback": validation_result.feedback},
        trace_id=state.trace_id,
    )


def _invoke_and_finalize(
    *, runner: Any, state: Any, logger: CanonicalEventLogger, request: Any
):
    entry_barrel = _entry_barrel()
    result = entry_barrel.invoke_decision_direct(
        runner,
        state=state,
        decision=request.decision,
        user_input=None
        if request.consume_user_input_for_command
        else request.user_input,
        logger=logger,
    ).to_step_output()
    replay_reason_code = str(getattr(request.decision, "reason_code", "") or "").strip()
    checkpoint_interval = max(
        0, int(getattr(runner.options, "plan_checkpoint_interval", 0) or 0)
    )
    state_mode = str(getattr(state, "mode", "") or "").strip().lower()
    if hasattr(getattr(state, "mode", None), "value"):
        state_mode = str(getattr(state.mode, "value", "") or "").strip().lower()
    if _should_pause_for_checkpoint(
        state=state,
        result=result,
        replay_reason_code=replay_reason_code,
        checkpoint_interval=checkpoint_interval,
        state_mode=state_mode,
    ):
        checkpoint_output = _runner_delegate(
            "_respond_with_meta",
            runner,
            state=state,
            logger=logger,
            message=build_plan_checkpoint_continuation_message(
                cursor=state.cursor,
                total_steps=len(state.plan.steps),
            ),
            status=BRAIN_STATE_WAITING_USER,
            action_result=result.action_result,
        )
        if request.masked_resume_cursor is not None:
            checkpoint_output = checkpoint_output.model_copy(deep=True)
            checkpoint_output.working_state = (
                checkpoint_output.working_state.model_copy(deep=True)
            )
            checkpoint_output.working_state.cursor = request.masked_resume_cursor
        return checkpoint_output
    if (
        request.mask_pending_confirmation_in_output
        and result.status == BRAIN_STATE_WAITING_USER
        and getattr(result.working_state, "pending_confirmation_command", None)
        is not None
    ):
        result = result.model_copy(deep=True)
        result.working_state.pending_confirmation_command = None
    replay_result = _normalize_confirmation_replay_result(
        runner=runner,
        state=state,
        logger=logger,
        result=result,
        replay_reason_code=replay_reason_code,
    )
    if replay_result is not None:
        return replay_result
    return result


def _normalize_confirmation_replay_result(
    *,
    runner: Any,
    state: Any,
    logger: CanonicalEventLogger,
    result: Any,
    replay_reason_code: str,
) -> Any | None:
    if replay_reason_code != "confirmation_replay" or result.status != BRAIN_STATE_ACTIVE:
        return None
    action_result = getattr(result, "action_result", None)
    outputs = getattr(action_result, "outputs", None)
    outputs = outputs if isinstance(outputs, dict) else {}
    completed_intent_ids = list(outputs.get("completed_intent_ids") or [])
    remaining_intent_ids = list(outputs.get("remaining_intent_ids") or [])
    if completed_intent_ids and not remaining_intent_ids:
        return _runner_delegate(
            "_respond_with_meta",
            runner,
            state=state,
            logger=logger,
            message=str(getattr(action_result, "summary", "") or "").strip() or None,
            status=BRAIN_STATE_DONE,
            action_result=action_result,
        )
    if not completed_intent_ids and not remaining_intent_ids:
        return _runner_delegate(
            "_respond_with_meta",
            runner,
            state=state,
            logger=logger,
            message=(
                "The confirmed command finished, but I could not safely determine "
                "the next step from the replay metadata. Please tell me how to continue."
            ),
            status=BRAIN_STATE_WAITING_USER,
            action_result=action_result,
        )
    return None


def _should_pause_for_checkpoint(
    *,
    state: Any,
    result: Any,
    replay_reason_code: str,
    checkpoint_interval: int,
    state_mode: str,
) -> bool:
    return (
        replay_reason_code
        in {
            "resume_existing_plan",
            "confirmation_replay",
            "plan_continuation_after_deny",
        }
        and result.status == BRAIN_STATE_ACTIVE
        and state.plan is not None
        and checkpoint_interval > 0
        and state_mode == "guided"
        and 0 < state.cursor < len(state.plan.steps)
        and state.cursor % checkpoint_interval == 0
        and state.cursor != state.last_checkpoint_cursor
    )


def _dispatch_runtime_error(
    *,
    runner: Any,
    state: Any,
    logger: CanonicalEventLogger,
    user_input: str | None,
    exc: Exception,
):
    transition(state, "fatal_error", logger=logger)
    if user_input:
        logger.emit(
            "brain.clarify.llm.failed",
            {
                "strategy": "llm",
                "phase": state.phase,
                "error": str(exc),
            },
            trace_id=state.trace_id,
            status=BRAIN_STATE_ERROR,
            error={"code": "CLARIFY_LLM_ROUTING_FAILED", "message": str(exc)},
        )
    logger.emit(
        "brain.done",
        {"reason": "error"},
        trace_id=state.trace_id,
        status=BRAIN_STATE_ERROR,
        error={"code": "RUNTIME_ERROR", "message": str(exc)},
    )
    return _runner_delegate(
        "_respond_with_meta",
        runner,
        state=state,
        logger=logger,
        message=f"State machine error: {exc}",
        status=BRAIN_STATE_ERROR,
        action_result=ActionResult(
            command_id=state.last_command_id or "n/a",
            status=BRAIN_ACTION_STATUS_FAILED,
            summary="runtime_error",
            error=ActionError(code="RUNTIME_ERROR", message=str(exc)),
        ),
    )


def _entry_barrel() -> Any:
    from ... import entry as entry_module

    return entry_module
