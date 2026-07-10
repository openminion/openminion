"""Phase-oriented closure gate runtime helpers."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from openminion.base.constants import STATE_KEY_FINALIZATION_STATUS

from ....constants import (
    BRAIN_ACTION_STATUS_SUCCESS,
    BRAIN_DISPOSITION_CLOSE,
    BRAIN_DISPOSITION_CONTINUE,
    BRAIN_DISPOSITION_REPLAN,
    BRAIN_MISSION_JUDGMENT_ASK_USER,
    BRAIN_MISSION_JUDGMENT_CONTINUE,
    BRAIN_MISSION_ROUTE_FINISH,
    BRAIN_STATE_DONE,
)
from ....diagnostics.events import CanonicalEventLogger
from ...mission import continue_message, mission_is_active
from ....runtime.reconciliation import (
    apply_plan_reconciliation_to_judgment,
    evaluate_plan_reconciliation,
)
from ....runtime.verification.policy import (
    build_freshness_failure_message,
    verify_freshness_answer,
)
from ....runtime.review.observation import (
    apply_review_to_judgment,
    observe_review_invocation,
)
from ....runtime.verification.probe import (
    apply_verification_to_judgment,
    evaluate_verification,
)
from ....schemas import ActionResult, MissionJudgment, WorkingState, new_uuid
from ....schemas.closure import ClosureJudgment
from ... import closure as closure_api
from ...closure.checks import (
    _can_continue_for_freshness,
    _closure_action_outputs,
    _closure_finalization_status,
    _has_successful_mutation_tool_evidence,
)
from .final_answer import repair_missing_final_answer_if_needed
from .mission import apply_mission_completion_gate
from ...judgment_context import (
    build_live_state_overlay as _build_live_state_overlay,
    intent_execution_payload as _intent_execution_payload,
)
from ...delegation import _runner_delegate

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ....runner import BrainRunner


@dataclass(slots=True)
class ClosureGateContext:
    closure_goal: str
    mission: Any | None
    finish_requested: str
    llm_call_id: str
    model: str
    emit_phase_status: Any | None


def evaluate_turn_closure(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    action_result: ActionResult | None,
    logger: CanonicalEventLogger,
    completion_reason: str,
) -> ClosureJudgment:
    context_or_judgment = _prepare_closure_gate(
        runner,
        state=state,
        logger=logger,
        completion_reason=completion_reason,
    )
    if isinstance(context_or_judgment, ClosureJudgment):
        return context_or_judgment
    context = context_or_judgment
    try:
        judgment = _run_closure_judge(
            runner,
            state=state,
            action_result=action_result,
            logger=logger,
            completion_reason=completion_reason,
            context=context,
        )
        _apply_closure_guards(
            runner,
            state=state,
            action_result=action_result,
            logger=logger,
            completion_reason=completion_reason,
            closure_goal=context.closure_goal,
            judgment=judgment,
        )
        active_plan = _active_plan_at_closure(runner=runner, state=state)
        _apply_runtime_probes(
            state=state,
            action_result=action_result,
            judgment=judgment,
            active_plan=active_plan,
        )
        _emit_closure_completed(
            state=state,
            action_result=action_result,
            logger=logger,
            context=context,
            judgment=judgment,
            active_plan=active_plan,
        )
        return apply_mission_completion_gate(
            runner,
            state=state,
            action_result=action_result,
            logger=logger,
            completion_reason=completion_reason,
            context=context,
            judgment=judgment,
        )
    except Exception as exc:  # noqa: BLE001
        return _fail_closed_closure_judgment(
            runner,
            state=state,
            action_result=action_result,
            logger=logger,
            context=context,
            exc=exc,
        )


def _prepare_closure_gate(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    logger: CanonicalEventLogger,
    completion_reason: str,
) -> ClosureGateContext | ClosureJudgment:
    mission = getattr(state, "mission", None)
    finish_requested = (
        str(getattr(mission, "latest_route_action", "") or "").strip()
        if mission is not None
        else ""
    )
    if state.status != BRAIN_STATE_DONE:
        return ClosureJudgment(
            satisfied=True,
            reason="closure_gate_skipped_non_done_state",
            next_action=BRAIN_DISPOSITION_CLOSE,
        )
    if runner.llm_api is None or runner.context_api is None:
        return _missing_llm_or_context_judgment(
            state=state,
            logger=logger,
            mission=mission,
            finish_requested=finish_requested,
        )
    closure_goal = _resolve_closure_goal(state)
    if not closure_goal:
        logger.emit(
            "brain.closure_gate.missing_context",
            {
                "missing_fields": ["goal_or_plan_objective"],
                "completion_reason": completion_reason,
            },
            trace_id=state.trace_id,
            status="warning",
        )
        return ClosureJudgment(
            satisfied=False,
            reason="closure_gate_missing_goal_context",
            next_action=BRAIN_DISPOSITION_REPLAN,
        )
    return ClosureGateContext(
        closure_goal=closure_goal,
        mission=mission,
        finish_requested=finish_requested,
        llm_call_id=new_uuid(),
        model=runner.profile.llm_profiles.reflect_model,
        emit_phase_status=getattr(runner, "_emit_phase_status", None),
    )


def _resolve_closure_goal(state: WorkingState) -> str:
    goal = str(getattr(state, "goal", "") or "").strip()
    if goal:
        return goal
    objective = str(
        getattr(getattr(state, "plan", None), "objective", "") or ""
    ).strip()
    if objective:
        return objective
    return str(getattr(state, "last_user_input", "") or "").strip()


def _missing_llm_or_context_judgment(
    *,
    state: WorkingState,
    logger: CanonicalEventLogger,
    mission: Any | None,
    finish_requested: str,
) -> ClosureJudgment:
    if mission is not None and mission_is_active(state):
        if finish_requested == BRAIN_MISSION_ROUTE_FINISH:
            mission.latest_judgment = MissionJudgment(
                outcome=BRAIN_MISSION_JUDGMENT_ASK_USER,
                reason=(
                    "I could not safely confirm that the mission is complete because "
                    "mission judgment is unavailable in this runtime."
                ),
            )
        else:
            mission.latest_judgment = MissionJudgment(
                outcome=BRAIN_MISSION_JUDGMENT_CONTINUE,
                reason=continue_message(mission),
            )
        logger.emit(
            "brain.mission_judge.completed",
            {
                "mission_id": mission.mission_id,
                "outcome": mission.latest_judgment.outcome,
                "finish_requested": (finish_requested == BRAIN_MISSION_ROUTE_FINISH),
                "fallback": "missing_llm_or_context",
            },
            trace_id=state.trace_id,
            status="warning",
        )
    return ClosureJudgment(
        satisfied=True,
        reason="closure_gate_skipped_missing_llm_or_context",
        next_action=BRAIN_DISPOSITION_CLOSE,
    )


def _run_closure_judge(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    action_result: ActionResult | None,
    logger: CanonicalEventLogger,
    completion_reason: str,
    context: ClosureGateContext,
) -> ClosureJudgment:
    logger.emit(
        "brain.closure_gate.started",
        {
            "llm_call_id": context.llm_call_id,
            "model": context.model,
            "completion_reason": completion_reason,
        },
        trace_id=state.trace_id,
    )
    if callable(context.emit_phase_status):
        context.emit_phase_status(
            state=state,
            source_event="brain.closure_gate.started",
            payload={
                "llm_call_id": context.llm_call_id,
                "model": context.model,
                "completion_reason": completion_reason,
            },
        )
    try:
        raw = closure_api.call_structured_with_retry(
            runner.llm_api,
            model=context.model,
            purpose="judge",
            context=_runner_delegate(
                "_build_context",
                runner,
                state=state,
                purpose="judge",
                budget={"max_tokens": min(1200, state.budgets_remaining.tokens)},
                hints=_build_closure_hints(
                    state=state,
                    action_result=action_result,
                    completion_reason=completion_reason,
                    context=context,
                ),
                logger=logger,
            ),
            schema=ClosureJudgment,
        )
    except RuntimeError as exc:
        logger.emit(
            "brain.closure_gate.invalid_structured_output",
            {
                "error": str(exc),
                "completion_reason": completion_reason,
            },
            trace_id=state.trace_id,
            status="warning",
        )
        return ClosureJudgment(
            satisfied=False,
            reason="closure_gate_invalid_structured_output",
            next_action=BRAIN_DISPOSITION_CONTINUE,
        )
    state.llm_calls_used += 1
    if isinstance(raw, dict):
        _runner_delegate("_debit_tokens", runner, state, raw, logger)
    return _normalize_closure_judgment(raw)


def _build_closure_hints(
    *,
    state: WorkingState,
    action_result: ActionResult | None,
    completion_reason: str,
    context: ClosureGateContext,
) -> dict[str, Any]:
    hints = {
        "_llm_call_id": context.llm_call_id,
        "current_datetime": state.last_result.created_at
        if getattr(state, "last_result", None) is not None
        and getattr(state.last_result, "created_at", None)
        else "",
        "user_input": context.closure_goal,
        "closure_candidate_reason": completion_reason,
        "closure_action_summary": str(
            getattr(action_result, "summary", "") or ""
        ).strip(),
        "closure_sub_intents": list(getattr(state, "decision_sub_intents", []) or []),
        "closure_success_criteria": dict(
            getattr(state, "decision_success_criteria", {}) or {}
        ),
        "closure_action_outputs": _closure_action_outputs(action_result),
        "closure_intent_outcomes": _intent_execution_payload(state),
        "live_state_overlay": _build_live_state_overlay(state=state),
        "style_overrides": {
            "closure_gate_contract": (
                "You are the turn-closure gate. Decide if the original user goal is fully "
                "satisfied by the available execution results. Return structured fields: "
                "satisfied (bool), reason (string), next_action (close|continue|replan), "
                "final_answer (string|null), mutation_claimed (bool), and optional "
                "post_completion_critique "
                "(intent_id, summary, lessons, next_time_action). "
                "If post_completion_critique is present, its intent_id must exactly "
                "match one of the typed intent outcomes for this turn. When "
                "next_action='close', final_answer should be a concise user-facing "
                "answer grounded in the result. When not closing, final_answer may be "
                "null. Use next_action='replan' when sub-intents or success criteria "
                "are not met. Use closure_action_outputs and any recent tool_results as "
                "load-bearing execution facts. Do not treat one successful file write, "
                "partial scaffold, or missing verification run as full completion when "
                "the goal requires additional artifacts or validation steps not present "
                "in the execution facts. If the original user requested an explicit "
                "final-answer format, titled sections, or required headings, the "
                "final_answer must preserve that requested shape. Do not close with a "
                "progress note, future-work narration, raw tool transcript, or a prose "
                "summary that omits required headings. Set mutation_claimed=true only "
                "when final_answer says that files or other workspace artifacts changed."
            )
        },
    }
    if state.freshness_contract is not None:
        hints["freshness_contract"] = state.freshness_contract.model_dump(mode="json")
    if state.freshness_obligations is not None:
        hints["freshness_obligations"] = state.freshness_obligations.model_dump(
            mode="json"
        )
    if state.freshness_diagnostics is not None:
        hints["freshness_diagnostics"] = state.freshness_diagnostics.model_dump(
            mode="json"
        )
    return hints


def _normalize_closure_judgment(raw: Any) -> ClosureJudgment:
    if not isinstance(raw, dict):
        raise TypeError("judge output must be an object")
    if "satisfied" not in raw or "next_action" not in raw:
        raise ValueError("judge output missing required fields")
    if not isinstance(raw.get("satisfied"), bool):
        raise TypeError("judge.satisfied must be boolean")
    next_action = str(raw.get("next_action", "")).strip()
    if next_action not in {
        BRAIN_DISPOSITION_CLOSE,
        BRAIN_DISPOSITION_CONTINUE,
        BRAIN_DISPOSITION_REPLAN,
    }:
        raise ValueError("judge.next_action must be close|continue|replan")
    reason = raw.get("reason", "")
    if reason is None:
        reason = ""
    if not isinstance(reason, str):
        raise TypeError("judge.reason must be string")
    final_answer = raw.get("final_answer")
    if final_answer is None:
        normalized_final_answer = None
    else:
        if not isinstance(final_answer, str):
            raise TypeError("judge.final_answer must be string|null")
        normalized_final_answer = str(final_answer).strip() or None
    normalized = dict(raw)
    normalized["reason"] = reason
    normalized["final_answer"] = normalized_final_answer
    return ClosureJudgment.model_validate(normalized)


def _apply_closure_guards(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    action_result: ActionResult | None,
    logger: CanonicalEventLogger,
    completion_reason: str,
    closure_goal: str,
    judgment: ClosureJudgment,
) -> None:
    _enforce_consistency_guards(judgment=judgment, action_result=action_result)
    repair_missing_final_answer_if_needed(
        runner,
        state=state,
        action_result=action_result,
        logger=logger,
        completion_reason=completion_reason,
        closure_goal=closure_goal,
        judgment=judgment,
    )
    _apply_freshness_guard(state=state, action_result=action_result, judgment=judgment)


def _enforce_consistency_guards(
    *,
    judgment: ClosureJudgment,
    action_result: ActionResult | None,
) -> None:
    if not judgment.satisfied and judgment.next_action == BRAIN_DISPOSITION_CLOSE:
        judgment.next_action = BRAIN_DISPOSITION_CONTINUE
        judgment.final_answer = None
        judgment.reason = (
            f"{judgment.reason}; inconsistent_unsatisfied_close"
            if judgment.reason
            else "inconsistent_unsatisfied_close"
        )
    status = _closure_finalization_status(action_result)
    closes = judgment.satisfied and judgment.next_action == BRAIN_DISPOSITION_CLOSE
    if status is not None and status.status == "final_answer" and not closes:
        judgment.reason = (
            f"{judgment.reason}; finalization_status_conflict"
            if judgment.reason
            else "finalization_status_conflict"
        )
    elif status is not None and status.status != "final_answer" and closes:
        judgment.satisfied = False
        judgment.next_action = BRAIN_DISPOSITION_CONTINUE
        judgment.final_answer = None
        suffix = f"finalization_status_{status.status}"
        judgment.reason = f"{judgment.reason}; {suffix}" if judgment.reason else suffix
        closes = False
    if (
        closes
        and judgment.mutation_claimed
        and not _has_successful_mutation_tool_evidence(action_result)
    ):
        judgment.satisfied = False
        judgment.next_action = BRAIN_DISPOSITION_CONTINUE
        judgment.final_answer = None
        suffix = "mutation_claim_without_tool_evidence"
        judgment.reason = f"{judgment.reason}; {suffix}" if judgment.reason else suffix


def _apply_freshness_guard(
    *,
    state: WorkingState,
    action_result: ActionResult | None,
    judgment: ClosureJudgment,
) -> None:
    freshness_reasons = verify_freshness_answer(
        contract=state.freshness_contract,
        obligations=state.freshness_obligations,
        answer=str(judgment.final_answer or "").strip(),
        action_result=action_result,
    )
    if not freshness_reasons:
        return
    if _can_continue_for_freshness(state):
        judgment.satisfied = False
        judgment.next_action = BRAIN_DISPOSITION_CONTINUE
        judgment.final_answer = None
    else:
        judgment.final_answer = build_freshness_failure_message(
            contract=state.freshness_contract,
            reasons=freshness_reasons,
        )
    judgment.reason = (
        f"{judgment.reason}; freshness_verifier_blocked"
        if judgment.reason
        else "freshness_verifier_blocked"
    )
    if state.freshness_diagnostics is not None:
        state.freshness_diagnostics.verifier_notes.extend(freshness_reasons)


def _apply_runtime_probes(
    *,
    state: WorkingState,
    action_result: ActionResult | None,
    judgment: ClosureJudgment,
    active_plan: dict[str, Any] | None,
) -> None:
    plan_reconciliation_fact = evaluate_plan_reconciliation(active_plan)
    apply_plan_reconciliation_to_judgment(
        judgment, plan_reconciliation_fact, state=state
    )
    tool_results = list(
        (getattr(action_result, "outputs", {}) or {}).get("tool_results", []) or []
    )
    verification_fact = evaluate_verification(tool_results=tool_results)
    apply_verification_to_judgment(judgment, verification_fact, state=state)
    review_fact = observe_review_invocation(tool_results)
    apply_review_to_judgment(judgment, review_fact, state=state)


def _emit_closure_completed(
    *,
    state: WorkingState,
    action_result: ActionResult | None,
    logger: CanonicalEventLogger,
    context: ClosureGateContext,
    judgment: ClosureJudgment,
    active_plan: dict[str, Any] | None,
) -> None:
    plan_reconciliation_fact = evaluate_plan_reconciliation(active_plan)
    tool_results = list(
        (getattr(action_result, "outputs", {}) or {}).get("tool_results", []) or []
    )
    verification_fact = evaluate_verification(tool_results=tool_results)
    review_fact = observe_review_invocation(tool_results)
    finalization_status = _closure_finalization_status(action_result)
    freshness_verified = not bool(
        verify_freshness_answer(
            contract=state.freshness_contract,
            obligations=state.freshness_obligations,
            answer=str(judgment.final_answer or "").strip(),
            action_result=action_result,
        )
    )
    logger.emit(
        "brain.closure_gate.completed",
        {
            "llm_call_id": context.llm_call_id,
            "satisfied": judgment.satisfied,
            "next_action": judgment.next_action,
            "reason": judgment.reason,
            "final_answer_present": bool(judgment.final_answer),
            "mutation_claimed": judgment.mutation_claimed,
            STATE_KEY_FINALIZATION_STATUS: finalization_status.status
            if finalization_status is not None
            else None,
            "freshness_verified": freshness_verified,
            "plan_reconciliation": plan_reconciliation_fact.model_dump(mode="json")
            if plan_reconciliation_fact is not None
            else None,
            "verification": verification_fact.model_dump(mode="json")
            if verification_fact is not None
            else None,
            "review": review_fact.model_dump(mode="json")
            if review_fact is not None
            else None,
        },
        trace_id=state.trace_id,
    )
    if callable(context.emit_phase_status):
        context.emit_phase_status(
            state=state,
            source_event="brain.closure_gate.completed",
            payload={
                "llm_call_id": context.llm_call_id,
                "satisfied": judgment.satisfied,
                "next_action": judgment.next_action,
                "reason": judgment.reason,
                "final_answer_present": bool(judgment.final_answer),
                "mutation_claimed": judgment.mutation_claimed,
            },
        )


def _fail_closed_closure_judgment(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    action_result: ActionResult | None,
    logger: CanonicalEventLogger,
    context: ClosureGateContext,
    exc: Exception,
) -> ClosureJudgment:
    fallback_action = BRAIN_DISPOSITION_REPLAN
    fallback_reason = "closure_gate_failed_fallback_replan"
    fallback_final_answer = None
    decision_reason_code = str(getattr(state, "decision_reason_code", "") or "").strip()
    explicit_success_reasons = {
        "explicit_tool_command",
        "explicit_agent_command",
        "forced_tool_command",
    }
    single_step_success = (
        action_result is not None
        and str(getattr(action_result, "status", "") or "").strip().lower()
        == BRAIN_ACTION_STATUS_SUCCESS
        and bool(getattr(state, "plan", None) is not None)
        and len(getattr(getattr(state, "plan", None), "steps", []) or []) == 1
    )
    if decision_reason_code in explicit_success_reasons and single_step_success:
        fallback_action = BRAIN_DISPOSITION_CLOSE
        fallback_reason = "closure_gate_failed_explicit_command_fallback_close"
        fallback_final_answer = (
            str(getattr(action_result, "summary", "") or "").strip() or None
        )
    logger.emit(
        "brain.fail_closed.judge_invalid_output",
        {
            "phase": "judge",
            "fallback_action": fallback_action,
            "reason": type(exc).__name__,
            "llm_call_id": context.llm_call_id,
            "decision_reason_code": decision_reason_code or None,
        },
        trace_id=state.trace_id,
        status="warning",
    )
    logger.emit(
        "brain.closure_gate.failed",
        {"llm_call_id": context.llm_call_id, "error": str(exc)},
        trace_id=state.trace_id,
        status="warning",
    )
    if callable(context.emit_phase_status):
        context.emit_phase_status(
            state=state,
            source_event="brain.closure_gate.failed",
            payload={"llm_call_id": context.llm_call_id, "error": str(exc)},
        )
    return ClosureJudgment(
        satisfied=(fallback_action == BRAIN_DISPOSITION_CLOSE),
        reason=fallback_reason,
        next_action=fallback_action,
        final_answer=fallback_final_answer,
    )


def _active_plan_at_closure(
    runner: "BrainRunner",
    *,
    state: WorkingState,
) -> dict[str, Any] | None:
    session_api = getattr(runner, "session_api", None)
    if session_api is None:
        return None
    session_id = str(getattr(state, "session_id", "") or "").strip()
    if not session_id:
        return None
    store = getattr(session_api, "store", None) or session_api
    get_active = getattr(store, "get_active_task_plan", None)
    if callable(get_active):
        try:
            active = get_active(session_id)
        except Exception:  # noqa: BLE001
            active = None
        if isinstance(active, dict):
            return dict(active)
    get_slice = getattr(store, "get_slice", None)
    if not callable(get_slice):
        return None
    try:
        raw = get_slice(session_id, "decide", {"max_turns": 1, "max_tool_events": 0})
    except TypeError:
        try:
            raw = get_slice(
                session_id=session_id,
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
