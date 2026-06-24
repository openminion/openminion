from __future__ import annotations


from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast

from openminion.modules.brain.constants import (
    BRAIN_ACT_PROFILE_GENERAL,
    BRAIN_DISPOSITION_CLOSE,
    BRAIN_DISPOSITION_CONTINUE,
    BRAIN_DISPOSITION_REPLAN,
    BRAIN_DECISION_ROUTE_ACT,
    BRAIN_STATE_DONE,
    BRAIN_STATE_ERROR,
    BRAIN_STATE_JOB_PENDING,
    BRAIN_STATE_WAITING_USER,
    MEMORY_CONSOLIDATION_MODULE_STATE_KEY,
    STATE_KEY_MODULE_STATE,
)
from openminion.modules.brain.execution.closure import final_close_message
from openminion.modules.brain.execution.loop_contracts import (
    ExecutionContext,
    ExecutionResult,
)
from openminion.modules.brain.execution.intent_state import (
    build_partial_success_summary,
)
from openminion.modules.brain.schemas.base import new_uuid
from openminion.modules.brain.schemas.decisions import (
    ActDecision,
    GoalDeclaration,
    GoalRevision,
    MetaRulePreference,
    PendingTurnContext,
)
from openminion.modules.brain.schemas.state import ActionError, ActionResult
from openminion.modules.brain.loop.tools import (
    ADAPTIVE_TERM_BUDGET_EXHAUSTED,
    ADAPTIVE_TERM_CORRECTION_BUDGET_EXHAUSTED,
    ADAPTIVE_TERM_CIRCULAR_PATTERN,
    ADAPTIVE_TERM_CONFIDENT_COMPLETE,
    ADAPTIVE_TERM_DECOMPOSE_INVALID,
    ADAPTIVE_TERM_DECOMPOSE_REQUESTED,
    ADAPTIVE_TERM_DIRECT_TOOL_CLOSURE_FAILED,
    ADAPTIVE_TERM_DISALLOWED_TOOL,
    ADAPTIVE_TERM_DUPLICATE_TOOL_CALLS,
    ADAPTIVE_TERM_FINALIZATION_BLOCKED,
    ADAPTIVE_TERM_FINALIZATION_CONTRACT_MISSING,
    ADAPTIVE_TERM_FINALIZATION_INCOMPLETE,
    ADAPTIVE_TERM_FINAL_TEXT,
    ADAPTIVE_TERM_ITERATION_CAP,
    ADAPTIVE_TERM_JOB_PENDING,
    ADAPTIVE_TERM_LLM_ERROR,
    ADAPTIVE_TERM_NEEDS_USER,
    ADAPTIVE_TERM_REQUESTED_TOOL_NOT_EXECUTED,
    ADAPTIVE_TERM_TOOL_FAILURE_NO_RECOVERY,
    AdaptiveToolLoopOutcome,
)
from openminion.modules.tool.contracts.model_ids import (
    MODEL_CODE_PATCH,
    MODEL_FILE_EDIT,
    MODEL_FILE_WRITE,
)

from ..services import runner_from_context

from .context import (
    _adaptive_loop_metadata,
    _sync_adaptive_intent_tracking,
)
from .events import (
    _postprocess_adaptive_response_trailers,
    _stage_task_plan_events,
)
from .termination import (
    _append_partial_success,
    _build_blocked_result,
    _build_error_result,
    _single_failed_tool_result_action,
)

_SEEDED_REPLAY_ARTIFACT_MUTATION_TOOLS = frozenset(
    {
        MODEL_CODE_PATCH,
        MODEL_FILE_EDIT,
        MODEL_FILE_WRITE,
    }
)
_CONTROL_RESTRICTED_REASON_CODES = frozenset(
    {
        "confirmation_replay",
        "confirmation_replay_recovery",
        "research_iteration_fallback",
    }
)


from . import modes as _adaptive_modes  # noqa: E402

adaptive_modes: Any = _adaptive_modes
from .tool_scope import (  # noqa: E402
    _public_act_tag,
)
from ..tools.iteration.helpers import (  # noqa: E402
    _requires_typed_finalization_contract,
)


class ActLoopFinalizationMixin:
    def _finalize_success(
        self: Any,
        ctx: ExecutionContext,
        *,
        loop_outcome: AdaptiveToolLoopOutcome,
        runtime: Any | None = None,
        model: str = "",
    ) -> ExecutionResult:
        final_text = loop_outcome.final_text or ""
        requires_typed_finalization = _requires_typed_finalization_contract(
            profile=SimpleNamespace(profile_name=loop_outcome.profile_name),
            loop_state=loop_outcome.state,
        )
        if requires_typed_finalization and not isinstance(
            loop_outcome.finalization_status, dict
        ):
            message = (
                "General act work ended without the required typed "
                "finalization_status contract."
            )
            return ExecutionResult(
                status=BRAIN_STATE_ERROR,
                working_state=ctx.state,
                message=message,
                action_result=_build_error_result(
                    message,
                    "act_finalization_contract_missing",
                ),
            )
        completed_ids, remaining_ids = _sync_adaptive_intent_tracking(
            ctx=ctx,
            loop_state=loop_outcome.state,
        )
        telemetry_payload = loop_outcome.telemetry_payload()
        telemetry_payload.update(
            {
                "completed_intent_ids": list(completed_ids),
                "remaining_intent_ids": list(remaining_ids),
            }
        )
        _stage_task_plan_events(ctx, loop_outcome)
        _postprocess_adaptive_response_trailers(
            ctx,
            loop_outcome,
            request_metadata=_adaptive_loop_metadata(ctx, purpose="act"),
        )
        final_text = _append_partial_success(
            message=final_text,
            summary=build_partial_success_summary(
                list(getattr(ctx.state, "intent_execution_states", []) or []),
            ),
        )
        if isinstance(loop_outcome.pending_turn_context, dict):
            ctx.state.pending_turn_context = PendingTurnContext.model_validate(
                loop_outcome.pending_turn_context
            )
            ctx.state.pending_turn_context_stale_turns = 0
        if str(loop_outcome.session_work_summary or "").strip():
            ctx.state.session_work_summary = str(
                loop_outcome.session_work_summary or ""
            ).strip()
        if isinstance(loop_outcome.meta_rule_preference, dict):
            runner = runner_from_context(ctx)
            if runner is not None and getattr(runner, "memory_api", None) is not None:
                preference_result = adaptive_modes.stage_meta_rule_preference(
                    runner,
                    state=ctx.state,
                    preference=MetaRulePreference.model_validate(
                        loop_outcome.meta_rule_preference
                    ),
                )
                candidate_id = preference_result.get("candidate_id")
                if candidate_id:
                    telemetry_payload["meta_rule_preference.candidate_id"] = str(
                        candidate_id
                    )
                skipped_reason = preference_result.get("skipped_reason")
                if skipped_reason:
                    telemetry_payload["meta_rule_preference.skipped_reason"] = str(
                        skipped_reason
                    )
        if isinstance(loop_outcome.goal_declaration, dict):
            runner = runner_from_context(ctx)
            if runner is not None and getattr(runner, "memory_api", None) is not None:
                try:
                    goal_payload = GoalDeclaration.model_validate(
                        loop_outcome.goal_declaration
                    )
                except Exception as exc:
                    telemetry_payload["goal_declaration.skipped_reason"] = (
                        f"validation_error:{type(exc).__name__}"
                    )
                else:
                    goal_result = adaptive_modes.stage_declared_goal(
                        runner,
                        state=ctx.state,
                        goal=goal_payload,
                    )
                    candidate_id = goal_result.get("candidate_id")
                    if candidate_id:
                        telemetry_payload["goal_declaration.candidate_id"] = str(
                            candidate_id
                        )
                    skipped_reason = goal_result.get("skipped_reason")
                    if skipped_reason:
                        telemetry_payload["goal_declaration.skipped_reason"] = str(
                            skipped_reason
                        )
                    from openminion.modules.brain.runtime.goal.policy import (
                        authorize_goal_action,
                    )

                    auth = authorize_goal_action(
                        profile_policy=getattr(
                            runner.profile, "goal_execution_policy", None
                        ),
                        action_type=goal_payload.action_type,
                    )
                    telemetry_payload["goal_declaration.policy_verdict"] = auth.reason
                    telemetry_payload["goal_declaration.policy_allowed"] = bool(
                        auth.allowed
                    )
                    telemetry_payload["goal_declaration.requires_user_confirm"] = bool(
                        auth.requires_user_confirm
                    )
            else:
                telemetry_payload["goal_declaration.skipped_reason"] = (
                    "memory_api_unavailable"
                )
        if isinstance(loop_outcome.goal_revision, dict):
            runner = runner_from_context(ctx)
            if runner is not None and getattr(runner, "memory_api", None) is not None:
                try:
                    revision_payload = GoalRevision.model_validate(
                        loop_outcome.goal_revision
                    )
                except Exception as exc:
                    telemetry_payload["goal_revision.skipped_reason"] = (
                        f"validation_error:{type(exc).__name__}"
                    )
                else:
                    revision_result = adaptive_modes.stage_goal_revision(
                        runner,
                        state=ctx.state,
                        goal_revision=revision_payload,
                    )
                    record_id = revision_result.get("record_id")
                    if record_id:
                        telemetry_payload["goal_revision.record_id"] = str(record_id)
                    skipped_reason = revision_result.get("skipped_reason")
                    if skipped_reason:
                        telemetry_payload["goal_revision.skipped_reason"] = str(
                            skipped_reason
                        )
                    if revision_result.get("policy_verdict") is not None:
                        telemetry_payload["goal_revision.policy_verdict"] = str(
                            revision_result["policy_verdict"]
                        )
                    if revision_result.get("policy_allowed") is not None:
                        telemetry_payload["goal_revision.policy_allowed"] = bool(
                            revision_result["policy_allowed"]
                        )
                    if revision_result.get("requires_user_confirm") is not None:
                        telemetry_payload["goal_revision.requires_user_confirm"] = bool(
                            revision_result["requires_user_confirm"]
                        )
            else:
                telemetry_payload["goal_revision.skipped_reason"] = (
                    "memory_api_unavailable"
                )
        if loop_outcome.memory_consolidation_decisions:
            runner = runner_from_context(ctx)
            memory_api = (
                getattr(runner, "memory_api", None) if runner is not None else None
            )
            target_scope = ""
            module_state = dict(getattr(ctx.state, STATE_KEY_MODULE_STATE, {}) or {})
            raw = module_state.get(MEMORY_CONSOLIDATION_MODULE_STATE_KEY)
            if isinstance(raw, dict):
                target_scope = str(raw.get("target_scope", "") or "").strip()
            consolidation_result = adaptive_modes.apply_memory_consolidation_decisions(
                memory_api,
                decisions=list(loop_outcome.memory_consolidation_decisions),
                target_scope=target_scope or f"agent:{ctx.state.agent_id}",
            )
            telemetry_payload.update(
                {
                    "memory_consolidation.applied_count": int(
                        consolidation_result.get("applied_count", 0) or 0
                    ),
                    "memory_consolidation.promoted_count": int(
                        consolidation_result.get("promoted_count", 0) or 0
                    ),
                    "memory_consolidation.discarded_count": int(
                        consolidation_result.get("discarded_count", 0) or 0
                    ),
                    "memory_consolidation.deferred_count": int(
                        consolidation_result.get("deferred_count", 0) or 0
                    ),
                }
            )
            errors = list(consolidation_result.get("errors", []) or [])
            if errors:
                telemetry_payload["memory_consolidation.errors"] = errors
        self_compaction_result = None
        if runtime is not None:
            self_compaction_result = self._maybe_run_self_compaction(
                ctx,
                runtime=runtime,
                model=model,
                final_text=final_text,
            )
        if self_compaction_result is not None:
            telemetry_payload["self_compaction.applied"] = bool(
                self_compaction_result.applied
            )
            telemetry_payload["self_compaction.reason_code"] = (
                self_compaction_result.reason_code
            )
            if self_compaction_result.summary_text:
                telemetry_payload["session_work_summary"] = (
                    self_compaction_result.summary_text
                )
            if self_compaction_result.audit_payload:
                telemetry_payload["self_compaction.audit"] = dict(
                    self_compaction_result.audit_payload
                )
        final_action = ActionResult(
            command_id=new_uuid(),
            status="success",
            summary=final_text or f"{_public_act_tag()} done",
            outputs=telemetry_payload,
        )
        completion_reason = (
            "act_adaptive_confident_complete"
            if loop_outcome.termination_reason == ADAPTIVE_TERM_CONFIDENT_COMPLETE
            else "act_adaptive_final_text"
        )
        judgment = None
        disposition = ""
        try:
            judgment = ctx.evaluate_turn_closure(
                action_result=final_action,
                completion_reason=completion_reason,
            )
            disposition = ctx.apply_closure_judgment(judgment=judgment)
        except Exception:  # noqa: BLE001
            judgment = None
            disposition = ""
        closure_final_answer = ""
        if (
            judgment is not None
            and disposition == BRAIN_DISPOSITION_CLOSE
            and str(getattr(judgment, "final_answer", "") or "").strip()
        ):
            closure_final_answer = str(
                getattr(judgment, "final_answer", "") or ""
            ).strip()
        bad_final_text = self._seeded_final_text_is_unexecutable_tool_envelope(
            final_text
        ) or self._seeded_final_text_is_unexecutable_tool_envelope(closure_final_answer)
        if (
            (
                self._seeded_continue_stays_autonomous(ctx)
                and disposition
                in {BRAIN_DISPOSITION_CONTINUE, BRAIN_DISPOSITION_REPLAN}
            )
            or bad_final_text
        ) and self._seeded_final_text_retry_available(ctx):
            if self._seeded_final_text_is_unexecutable_tool_envelope(final_text):
                ctx.state.post_action_user_message = (
                    "Continue from the current task state. Your previous reply "
                    "emitted raw or unexecutable tool markup. Do not answer with "
                    "tool markup, XML, JSON tool envelopes, or a blocked-envelope "
                    "placeholder. If more work remains, call the next required "
                    "native tool now; if the task is complete, return the requested "
                    "final answer format."
                )
            else:
                ctx.state.post_action_user_message = (
                    "Continue from the current task state. Do not answer with a "
                    "progress note. If more work remains, call the next required "
                    "tool now; if the task is complete, return the requested final "
                    "answer format."
                )
            original_goal = (
                str(getattr(ctx.state, "last_user_input", "") or "").strip()
                or str(getattr(ctx.decision, "objective", "") or "").strip()
            )
            if original_goal:
                ctx.state.post_action_user_message += (
                    f" Continue the original task: {original_goal}"
                )
            return cast(
                ExecutionResult,
                self._autonomous_seeded_result(ctx, action_result=final_action),
            )
        if closure_final_answer:
            final_text = closure_final_answer
            final_action.summary = final_text
        ctx.emit_status(
            source_phase="ACT",
            detail_text=f"{_public_act_tag()} done",
            mode=BRAIN_DECISION_ROUTE_ACT,
            mode_state="done",
            terminal=True,
            payload={
                **telemetry_payload,
                "act.profile": BRAIN_ACT_PROFILE_GENERAL,
            },
        )
        step_output = ctx.respond(
            message=final_text,
            status=BRAIN_STATE_DONE,
            action_result=final_action,
        )
        return ExecutionResult.from_step_output(step_output, judgment=judgment)

    def _result_from_outcome(
        self: Any,
        ctx: ExecutionContext,
        *,
        outcome: AdaptiveToolLoopOutcome,
    ) -> ExecutionResult:
        completed_ids, remaining_ids = _sync_adaptive_intent_tracking(
            ctx=ctx,
            loop_state=outcome.state,
        )
        telemetry_payload = outcome.telemetry_payload()
        telemetry_payload.update(
            {
                "completed_intent_ids": list(completed_ids),
                "remaining_intent_ids": list(remaining_ids),
            }
        )
        if outcome.termination_reason in {
            ADAPTIVE_TERM_FINAL_TEXT,
            ADAPTIVE_TERM_CONFIDENT_COMPLETE,
        }:
            return cast(
                ExecutionResult, self._finalize_success(ctx, loop_outcome=outcome)
            )
        if outcome.termination_reason == ADAPTIVE_TERM_DECOMPOSE_REQUESTED:
            subtasks = list(outcome.decompose_subtasks or [])
            if not subtasks:
                subtasks = list(
                    dict(getattr(outcome.state, "scratchpad", {}) or {}).get(
                        "adaptive.decompose_subtasks",
                        [],
                    )
                    or []
                )
            if not subtasks:
                message = "Decompose requested without validated subtasks."
                return ExecutionResult(
                    status=BRAIN_STATE_ERROR,
                    working_state=ctx.state,
                    message=message,
                    action_result=_build_error_result(
                        message,
                        "act_adaptive_decompose_missing_subtasks",
                    ),
                )
            decision = ActDecision(
                confidence=0.5,
                reason_code="mid_loop_decompose_tool_call",
                act_profile="orchestrate",
                subtasks=subtasks,
            )
            ctx.emit_status(
                source_phase="ACT",
                detail_text=f"{_public_act_tag()} decompose handoff",
                mode=BRAIN_DECISION_ROUTE_ACT,
                mode_state="decompose_handoff",
                payload={
                    **telemetry_payload,
                    "act.profile": "orchestrate",
                    "adaptive.decompose_subtask_count": len(subtasks),
                },
            )
            from openminion.modules.brain.execution.orchestrate.handler import (  # noqa: PLC0415
                OrchestrateMode,
            )

            return OrchestrateMode().execute(replace(ctx, decision=decision))
        if outcome.termination_reason == ADAPTIVE_TERM_DECOMPOSE_INVALID:
            message = outcome.error_message or "Invalid decompose tool call."
            return ExecutionResult(
                status=BRAIN_STATE_ERROR,
                working_state=ctx.state,
                message=message,
                action_result=_build_error_result(
                    message,
                    "act_adaptive_decompose_invalid",
                ),
            )
        if outcome.termination_reason == ADAPTIVE_TERM_NEEDS_USER:
            return cast(
                ExecutionResult, self._result_from_needs_user(ctx, outcome=outcome)
            )
        if outcome.termination_reason == ADAPTIVE_TERM_JOB_PENDING:
            return ExecutionResult(
                status=BRAIN_STATE_JOB_PENDING,
                working_state=ctx.state,
                message=f"{_public_act_tag()} async job pending; resume after the job completes.",
                action_result=outcome.action_result,
            )
        if outcome.termination_reason in {
            ADAPTIVE_TERM_BUDGET_EXHAUSTED,
            ADAPTIVE_TERM_ITERATION_CAP,
            ADAPTIVE_TERM_DUPLICATE_TOOL_CALLS,
            ADAPTIVE_TERM_CORRECTION_BUDGET_EXHAUSTED,
            ADAPTIVE_TERM_CIRCULAR_PATTERN,
        }:
            requires_typed_finalization = _requires_typed_finalization_contract(
                profile=SimpleNamespace(profile_name=outcome.profile_name),
                loop_state=outcome.state,
            )
            if (
                outcome.termination_reason == ADAPTIVE_TERM_BUDGET_EXHAUSTED
                and requires_typed_finalization
            ):
                message = (
                    "General act work ended without the required typed "
                    "finalization_status contract."
                )
                return ExecutionResult(
                    status=BRAIN_STATE_ERROR,
                    working_state=ctx.state,
                    message=message,
                    action_result=_build_error_result(
                        message,
                        "act_finalization_contract_missing",
                    ),
                )
            adaptive_modes._extract_failure_memories_for_outcome(ctx, outcome=outcome)
            if outcome.termination_reason == ADAPTIVE_TERM_BUDGET_EXHAUSTED:
                message = (
                    f"{_public_act_tag()} budget exhausted before a final answer. "
                    "Continue in a new turn or narrow the scope."
                )
                code = "act_adaptive_budget_exhausted"
            elif (
                outcome.termination_reason == ADAPTIVE_TERM_CORRECTION_BUDGET_EXHAUSTED
            ):
                message = (
                    f"{_public_act_tag()} exhausted its correction budget without "
                    "reaching a final answer."
                )
                code = "act_adaptive_correction_budget_exhausted"
            elif outcome.termination_reason == ADAPTIVE_TERM_DUPLICATE_TOOL_CALLS:
                message = (
                    f"{_public_act_tag()} repeated identical tool calls detected without "
                    "reaching a final answer."
                )
                code = "act_adaptive_duplicate_tool_calls"
            elif outcome.termination_reason == ADAPTIVE_TERM_CIRCULAR_PATTERN:
                message = (
                    f"{_public_act_tag()} repeated the same tool pattern without "
                    "reaching a final answer."
                )
                code = "act_adaptive_circular_pattern"
            else:
                message = (
                    f"{_public_act_tag()} reached the adaptive iteration cap without a "
                    "final answer."
                )
                code = "act_adaptive_iteration_cap"
            message = _append_partial_success(
                message=message,
                summary=build_partial_success_summary(
                    list(getattr(ctx.state, "intent_execution_states", []) or [])
                ),
            )
            blocked_action = ActionResult(
                command_id=new_uuid(),
                status="blocked",
                summary=message,
                outputs=telemetry_payload,
                error=ActionError(
                    code=code,
                    message=message,
                    details={"reason_code": code},
                ),
            )
            try:
                judgment = ctx.evaluate_turn_closure(
                    action_result=blocked_action,
                    completion_reason=code,
                )
                disposition = ctx.apply_closure_judgment(judgment=judgment)
            except Exception:  # noqa: BLE001
                judgment = None
                disposition = ""
            if (
                judgment is not None
                and disposition == BRAIN_DISPOSITION_CLOSE
                and str(getattr(judgment, "final_answer", "") or "").strip()
            ):
                close_message = final_close_message(
                    state=ctx.state,
                    judgment=judgment,
                    action_result=blocked_action,
                    fallback_message=message,
                )
                resolved_action = blocked_action.model_copy(
                    update={
                        "status": "success",
                        "summary": close_message,
                        "error": None,
                    },
                    deep=True,
                )
                ctx.extract_success_memories(
                    action_result=resolved_action,
                    judgment=judgment,
                )
                ctx.emit_status(
                    source_phase="ACT",
                    detail_text=f"{_public_act_tag()} done",
                    mode=BRAIN_DECISION_ROUTE_ACT,
                    mode_state="done",
                    terminal=True,
                    payload={
                        **telemetry_payload,
                        "act.profile": BRAIN_ACT_PROFILE_GENERAL,
                        "adaptive.exhaustion_closed_by_closure_gate": True,
                        "adaptive.exhaustion_reason": code,
                    },
                )
                return ExecutionResult.from_step_output(
                    ctx.respond(
                        message=close_message,
                        status=BRAIN_STATE_DONE,
                        action_result=resolved_action,
                    ),
                    judgment=judgment,
                )
            return ExecutionResult(
                status=BRAIN_STATE_WAITING_USER,
                working_state=ctx.state,
                message=message,
                action_result=blocked_action,
            )
        if outcome.termination_reason in {
            ADAPTIVE_TERM_FINALIZATION_BLOCKED,
            ADAPTIVE_TERM_FINALIZATION_INCOMPLETE,
        }:
            finalization_status = (
                dict(outcome.finalization_status or {})
                if isinstance(outcome.finalization_status, dict)
                else {}
            )
            message = str(outcome.final_text or "").strip() or (
                "I gathered some evidence, but I could not truthfully finish the "
                "requested final deliverable."
            )
            code = (
                "act_adaptive_finalization_incomplete"
                if outcome.termination_reason == ADAPTIVE_TERM_FINALIZATION_INCOMPLETE
                else "act_adaptive_finalization_blocked"
            )
            return ExecutionResult(
                status=BRAIN_STATE_WAITING_USER,
                working_state=ctx.state,
                message=message,
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="blocked",
                    summary=message,
                    outputs={
                        **telemetry_payload,
                        "adaptive.finalization_status": finalization_status,
                    },
                    error=ActionError(
                        code=code,
                        message=message,
                        details={"reason_code": code},
                    ),
                ),
            )
        if outcome.termination_reason in {
            ADAPTIVE_TERM_REQUESTED_TOOL_NOT_EXECUTED,
            ADAPTIVE_TERM_FINALIZATION_CONTRACT_MISSING,
        }:
            adaptive_modes._extract_failure_memories_for_outcome(ctx, outcome=outcome)
            if (
                outcome.termination_reason
                == ADAPTIVE_TERM_FINALIZATION_CONTRACT_MISSING
            ):
                recovered_tool_failure = _single_failed_tool_result_action(outcome)
                if recovered_tool_failure is not None:
                    return ExecutionResult(
                        status=BRAIN_STATE_ERROR,
                        working_state=ctx.state,
                        message=str(
                            getattr(recovered_tool_failure, "summary", "") or ""
                        ),
                        action_result=recovered_tool_failure,
                    )
            message = (
                outcome.error_message or "Adaptive loop integrity contract failed."
            )
            code = (
                "act_requested_tool_not_executed"
                if outcome.termination_reason
                == ADAPTIVE_TERM_REQUESTED_TOOL_NOT_EXECUTED
                else "act_finalization_contract_missing"
            )
            return ExecutionResult(
                status=BRAIN_STATE_ERROR,
                working_state=ctx.state,
                message=message,
                action_result=_build_error_result(message, code),
            )
        if outcome.termination_reason == ADAPTIVE_TERM_DISALLOWED_TOOL:
            message = outcome.error_message or "Disallowed tool requested."
            return ExecutionResult(
                status=BRAIN_STATE_ERROR,
                working_state=ctx.state,
                message=message,
                action_result=_build_blocked_result(
                    message,
                    "act_adaptive_disallowed_tool",
                ),
            )
        if outcome.termination_reason == ADAPTIVE_TERM_LLM_ERROR:
            message = outcome.error_message or "Adaptive loop LLM error."
            return ExecutionResult(
                status=BRAIN_STATE_ERROR,
                working_state=ctx.state,
                message=message,
                action_result=_build_error_result(
                    message,
                    "act_adaptive_llm_error",
                ),
            )
        if outcome.termination_reason == ADAPTIVE_TERM_TOOL_FAILURE_NO_RECOVERY:
            adaptive_modes._extract_failure_memories_for_outcome(ctx, outcome=outcome)
            message = (
                getattr(outcome.action_result, "summary", "")
                or outcome.error_message
                or "Tool failure without recovery."
            )
            return ExecutionResult(
                status=BRAIN_STATE_ERROR,
                working_state=ctx.state,
                message=message,
                action_result=outcome.action_result
                if outcome.action_result is not None
                else _build_error_result(message, "act_adaptive_tool_failure"),
            )
        if outcome.termination_reason == ADAPTIVE_TERM_DIRECT_TOOL_CLOSURE_FAILED:
            adaptive_modes._extract_failure_memories_for_outcome(ctx, outcome=outcome)
            message = (
                outcome.error_message
                or "Direct-tool answer-only closure failed after the requested tool completed."
            )
            return ExecutionResult(
                status=BRAIN_STATE_ERROR,
                working_state=ctx.state,
                message=message,
                action_result=_build_error_result(
                    message,
                    "act_adaptive_direct_tool_closure_failed",
                ),
            )
        adaptive_modes._extract_failure_memories_for_outcome(ctx, outcome=outcome)
        message = outcome.error_message or "Adaptive loop stopped unexpectedly."
        return ExecutionResult(
            status=BRAIN_STATE_ERROR,
            working_state=ctx.state,
            message=message,
            action_result=_build_error_result(message, "act_adaptive_error"),
        )
