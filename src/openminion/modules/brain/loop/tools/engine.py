from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from openminion.modules.llm.schemas import Message
from .contracts import (
    ADAPTIVE_CLOSURE_ENGINE_SINGLE_PASS,
    ADAPTIVE_TERM_CIRCULAR_PATTERN,
    ADAPTIVE_TERM_FINAL_TEXT,
    AdaptiveToolLoopContext,
    AdaptiveToolLoopOutcome,
    AdaptiveToolLoopProfile,
    AdaptiveToolLoopState,
    semantic_batch_signature,
)
from .correction import (
    dispatch_correction_plan,
    trigger_macro_correction,
)
from .events import IterationToolCallRecord
from .budget import (
    _debit_tool_budget,
    _profile_budget_exhausted,
)
from .direct_tool import (
    _clamp_direct_tool_batch_to_requested_call,
    _direct_tool_batch_completed_successfully,
    _direct_tool_turn_active,
    _force_direct_tool_answer_only_closure,
)
from .status import emit_adaptive_status
from .postprocess.engine import (
    AdaptiveLoopRunnerPostprocessMixin,
)
from .postprocess.rules import _is_empty_plan_lookup_diversion
from .decompose import (  # noqa: F401
    _DECOMPOSE_TOOL_NAME,
    _decompose_decline_result,
    _decompose_invalid_outcome,
    _decompose_tool_calls,
    _subtasks_from_decompose_control,
)

from .response_payloads import (  # noqa: F401
    _CONFIDENT_COMPLETE_GUIDANCE,
    _DELEGATION_RESULT_SUMMARY_GUIDANCE,
    _FINALIZATION_STATUS_GUIDANCE,
    _FINALIZATION_STATUS_SALVAGE_GUIDANCE,
    _GOAL_DECLARATION_GUIDANCE,
    _GOAL_REVISION_GUIDANCE,
    _MEMORY_CONSOLIDATION_GUIDANCE,
    _META_RULE_PREFERENCE_GUIDANCE,
    _PENDING_TURN_CONTEXT_GUIDANCE,
    _SESSION_WORK_SUMMARY_GUIDANCE,
    _TASK_PLAN_GUIDANCE,
    _TASK_PLAN_PROGRESS_GUIDANCE,
    _WATCH_ACTION_GUIDANCE,
    _WATCH_OUTCOME_GUIDANCE,
    _confident_complete_payload,
    _delegation_context_payload,
    _delegation_result_summary_payload,
    _finalization_status_payload,
    _goal_declaration_payload,
    _goal_revision_payload,
    _memory_consolidation_payload,
    _meta_rule_preference_payload,
    _pending_finalization_salvage_text,
    _pending_turn_context_payload,
    _session_work_summary_payload,
    _task_plan_abandoned_payload,
    _task_plan_completed_payload,
    _task_plan_payload,
    _task_plan_revision_payload,
    _task_plan_step_blocked_payload,
    _task_plan_step_completed_payload,
    _watch_outcome_payload,
)
from .budget_control import (  # noqa: F401
    _active_work_summary_from_state,
    _adaptive_budget_config,
    _budget_stop_outcome,
    _effective_cap,
    _emit_budget_event,
    _emit_budget_progress,
    _emit_high_watermark_if_needed,
    _event_type_for_budget_stop,
    _force_circular_pattern_answer_only_finalization,
    _force_budget_answer_only_finalization,
    _general_profile_name,
    _has_tool_evidence_for_answer_only,
    _llm_budget_available_for_answer_only,
    _max_steps_hint_from_state,
    _maybe_extend_iteration_budget,
    _step_summaries_from_state,
    _tool_budget_exhausted_for_answer_only,
)

from .iteration.setup import (  # noqa: F401
    _delegated_child_context,
    _delegated_child_context_message,
    _memory_consolidation_context,
    _memory_consolidation_context_message,
    _tool_efficiency_guidance,
    prepare_loop_frame,
)
from .iteration.dispatch import prepare_iteration_dispatch
from .iteration.execution import execute_iteration_results
from .postprocess.loop import finalize_iteration_state
from .iteration.termination import finalize_iteration_cap_exit
from .iteration.helpers import (
    _append_tool_result_payload,
    _build_enrichment_message,
    _build_intent_execution_state_message,
    _build_tool_failure_recovery_message,
    _count_substantive_non_control_tool_results,
    _explicit_calendar_years,
    _loop_has_non_success_tool_result,
    _loop_tool_result_payloads,
    _repair_stale_exact_date_search_args,
    _requires_typed_finalization_contract,
    _set_turn_progress,
    _stale_exact_date_query_reason,
    _tool_result_payload_from_action,
)
from .dispatch import _tool_request_result  # noqa: F401
from .duplicate_batch import (  # noqa: F401
    _action_result_has_retry_or_poll_signal,
    _build_duplicate_batch_answer_only_closure_message,
    _build_missing_action_result,
    _duplicate_batch_execution_facts,
    _duplicate_batch_recovery_message,
    _duplicate_batch_retry_counts,
    _eligible_duplicate_batch_execution_facts,
    _force_duplicate_batch_answer_only_closure,
    _record_duplicate_batch_execution_facts,
)


MICRO_CORRECTION_ANOMALY_THRESHOLD = 0.5


def run_adaptive_tool_loop(
    loop_ctx: AdaptiveToolLoopContext,
    *,
    profile: AdaptiveToolLoopProfile,
    runtime: Any,
    model: str,
    initial_messages: list[Any],
    tool_specs: list[Any],
    requestable_tool_specs: list[Any] | tuple[Any, ...] | None = None,
    initial_state: AdaptiveToolLoopState | None = None,
    finalizer: Callable[[AdaptiveToolLoopOutcome], Any] | None = None,
    on_tool_result: Callable[[AdaptiveToolLoopState], None] | None = None,
    tool_batch_runner: Callable[..., list[tuple[Any, Any]]] | None = None,
    seed_response: Any | None = None,
    seeded_commands: list[Any] | tuple[Any, ...] | None = None,
) -> AdaptiveToolLoopOutcome:
    frame = prepare_loop_frame(
        loop_ctx,
        profile=profile,
        model=model,
        initial_messages=initial_messages,
        tool_specs=tool_specs,
        requestable_tool_specs=requestable_tool_specs,
        initial_state=initial_state,
        seed_response=seed_response,
        seeded_commands=seeded_commands,
    )
    return _AdaptiveLoopRunner.from_frame(
        frame=frame,
        loop_ctx=loop_ctx,
        profile=profile,
        runtime=runtime,
        model=model,
        finalizer=finalizer,
        on_tool_result=on_tool_result,
        tool_batch_runner=tool_batch_runner,
    ).run()


@dataclass
class _PreparedLoopResponse:
    response: Any
    response_was_tool_suppressed: bool
    iter_llm_duration_ms: int
    iter_tool_records: list[IterationToolCallRecord]
    iter_input_tokens: int
    iter_output_tokens: int


@dataclass
class _AdaptiveLoopRunner(AdaptiveLoopRunnerPostprocessMixin):
    _PreparedLoopResponse = _PreparedLoopResponse
    _append_tool_result_payload = staticmethod(_append_tool_result_payload)
    _build_missing_action_result = staticmethod(_build_missing_action_result)
    _force_direct_tool_answer_only_closure = staticmethod(
        _force_direct_tool_answer_only_closure
    )

    loop_ctx: AdaptiveToolLoopContext
    profile: AdaptiveToolLoopProfile
    runtime: Any
    model: str
    finalizer: Callable[[AdaptiveToolLoopOutcome], Any] | None
    on_tool_result: Callable[[AdaptiveToolLoopState], None] | None
    tool_batch_runner: Callable[..., list[tuple[Any, Any]]] | None
    public_mode_name: str
    public_mode_tag: str
    tool_request_enabled: bool
    requestable_specs: list[Any]
    requestable_specs_by_name: dict[str, Any]
    active_tool_specs: list[Any]
    active_tool_names: list[str]
    allowed_tools: frozenset[str]
    seeded_queue: list[Any]
    loop_state: AdaptiveToolLoopState
    max_output_tokens: int | None
    metadata: dict[str, Any]
    turn_scope_id: str | None
    pending_response: Any | None
    loop_cache: Any
    budget_hint_injected: bool
    iteration_tool_sequences: list[tuple[str, ...]]
    loop_profiler: Any
    prefetch_predictor: Any
    prefetch_pending: Any

    @classmethod
    def from_frame(
        cls,
        *,
        frame: Any,
        loop_ctx: AdaptiveToolLoopContext,
        profile: AdaptiveToolLoopProfile,
        runtime: Any,
        model: str,
        finalizer: Callable[[AdaptiveToolLoopOutcome], Any] | None,
        on_tool_result: Callable[[AdaptiveToolLoopState], None] | None,
        tool_batch_runner: Callable[..., list[tuple[Any, Any]]] | None,
    ) -> "_AdaptiveLoopRunner":
        runtime_state = frame.runtime_state
        return cls(
            loop_ctx=loop_ctx,
            profile=profile,
            runtime=runtime,
            model=model,
            finalizer=finalizer,
            on_tool_result=on_tool_result,
            tool_batch_runner=tool_batch_runner,
            public_mode_name=frame.public_mode_name,
            public_mode_tag=frame.public_mode_tag,
            tool_request_enabled=frame.tool_request_enabled,
            requestable_specs=frame.requestable_specs,
            requestable_specs_by_name=frame.requestable_specs_by_name,
            active_tool_specs=frame.active_tool_specs,
            active_tool_names=frame.active_tool_names,
            allowed_tools=frame.allowed_tools,
            seeded_queue=frame.seeded_queue,
            loop_state=frame.loop_state,
            max_output_tokens=frame.max_output_tokens,
            metadata=frame.metadata,
            turn_scope_id=frame.turn_scope_id,
            pending_response=frame.pending_response,
            loop_cache=runtime_state.loop_cache,
            budget_hint_injected=runtime_state.budget_hint_injected,
            iteration_tool_sequences=runtime_state.iteration_tool_sequences,
            loop_profiler=runtime_state.loop_profiler,
            prefetch_predictor=runtime_state.prefetch_predictor,
            prefetch_pending=runtime_state.prefetch_pending,
        )

    def run(self) -> AdaptiveToolLoopOutcome:
        while True:
            action, outcome = self._handle_iteration_cap()
            if action == "continue":
                continue
            if action == "break":
                break
            if outcome is not None:
                return outcome

            action, outcome = self._handle_seeded_and_budget()
            if action == "continue":
                continue
            if outcome is not None:
                return outcome

            prepared = self._prepare_llm_response()
            if isinstance(prepared, AdaptiveToolLoopOutcome):
                return prepared

            tool_calls = list(getattr(prepared.response, "tool_calls", []) or [])
            continue_loop, outcome = self._handle_tool_suppressed_retry(
                tool_calls=tool_calls,
                response_was_tool_suppressed=prepared.response_was_tool_suppressed,
            )
            if continue_loop:
                continue
            if outcome is not None:
                return outcome

            tool_calls = _clamp_direct_tool_batch_to_requested_call(
                self.loop_state, tool_calls
            )
            selected_tool_name = (
                str(getattr(tool_calls[0], "name", "") or "").strip()
                if tool_calls
                else ""
            )
            if selected_tool_name:
                profile = self.profile
                loop_state = self.loop_state
                _set_turn_progress(
                    loop_state,
                    llm_call_count=self.loop_state.llm_calls,
                    llm_call_limit=_effective_cap(profile, loop_state),
                    progress_phase="thinking...",
                    tool_name=selected_tool_name,
                )
            emit_adaptive_status(
                self.loop_ctx,
                profile=self.profile,
                loop_state=self.loop_state,
                detail_text=(
                    f"{self.public_mode_tag} tool {selected_tool_name}"
                    if selected_tool_name
                    else f"{self.public_mode_tag} composing answer"
                ),
                mode_state="llm_progress",
            )

            payloads = self._build_response_payloads(prepared.response)
            if not tool_calls:
                continue_loop, outcome = self._handle_no_tool_calls(
                    prepared=prepared,
                    payloads=payloads,
                )
                if continue_loop:
                    continue
                assert outcome is not None
                return outcome

            pre_tool_draft = str(
                payloads["final_text"]
                or getattr(prepared.response, "output_text", "")
                or ""
            ).strip()
            if pre_tool_draft:
                self.loop_state.scratchpad["last_pre_tool_draft_text"] = pre_tool_draft

            outcome = self._maybe_force_direct_closure(
                prepared=prepared,
                allowed_tool_calls=tool_calls,
            )
            if outcome is not None:
                return outcome

            signature = semantic_batch_signature(tool_calls)
            continue_loop, outcome = self._handle_duplicate_signature(
                signature=signature,
                tool_calls=tool_calls,
                prepared=prepared,
            )
            if continue_loop:
                continue
            if outcome is not None:
                return outcome

            self.iteration_tool_sequences.append(
                tuple(
                    str(getattr(item, "name", "") or "").strip() for item in tool_calls
                )
            )
            if (
                len(self.iteration_tool_sequences) >= 3
                and self.iteration_tool_sequences[-1]
                == self.iteration_tool_sequences[-2]
                == self.iteration_tool_sequences[-3]
            ):
                circular_outcome = _force_circular_pattern_answer_only_finalization(
                    loop_ctx=self.loop_ctx,
                    profile=self.profile,
                    loop_state=self.loop_state,
                    runtime=self.runtime,
                    model=self.model,
                    max_output_tokens=self.max_output_tokens,
                    metadata=self.metadata,
                    allowed_tools=self.allowed_tools,
                    public_mode_tag=self.public_mode_tag,
                )
                if circular_outcome is not None:
                    if (
                        circular_outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
                        and self.profile.final_closure_policy
                        == ADAPTIVE_CLOSURE_ENGINE_SINGLE_PASS
                        and self.finalizer is not None
                    ):
                        circular_outcome.mode_result = self.finalizer(circular_outcome)
                    return circular_outcome
                self.loop_state.termination_reason = ADAPTIVE_TERM_CIRCULAR_PATTERN
                emit_adaptive_status(
                    self.loop_ctx,
                    profile=self.profile,
                    loop_state=self.loop_state,
                    detail_text=f"{self.public_mode_tag} circular tool pattern detected",
                    mode_state="circular_pattern",
                    termination_reason=ADAPTIVE_TERM_CIRCULAR_PATTERN,
                )
                return AdaptiveToolLoopOutcome(
                    profile_name=self.profile.profile_name,
                    mode_name=self.profile.mode_name,
                    termination_reason=ADAPTIVE_TERM_CIRCULAR_PATTERN,
                    state=self.loop_state,
                    allowed_tools=self.allowed_tools,
                )

            if _is_empty_plan_lookup_diversion(
                self.loop_ctx,
                self.loop_state,
                tool_calls,
            ):
                self.loop_state.scratchpad["empty_plan_lookup_diversion_count"] = (
                    int(
                        self.loop_state.scratchpad.get(
                            "empty_plan_lookup_diversion_count", 0
                        )
                        or 0
                    )
                    + 1
                )
                self.loop_state.messages.append(
                    Message(
                        role="system",
                        content=(
                            "The plan-list lookup would not advance the current "
                            "task because no active plan exists and prior tool "
                            "results for the same task are already available. "
                            "Do not call plan.list. Continue the original user "
                            "request from the existing tool results: either call "
                            "a task-relevant tool, or return the completed final "
                            "answer."
                        ),
                    )
                )
                continue

            outcome = self._validate_tool_calls(tool_calls)
            if outcome is not None:
                return outcome

            dispatch_phase = prepare_iteration_dispatch(
                self.loop_ctx,
                profile=self.profile,
                loop_state=self.loop_state,
                runtime=self.runtime,
                model=self.model,
                tool_calls=tool_calls,
                signature=signature,
                allowed_tools=self.allowed_tools,
                public_mode_tag=self.public_mode_tag,
                active_tool_specs=self.active_tool_specs,
                active_tool_names=set(self.active_tool_names),
                requestable_specs=self.requestable_specs,
                requestable_specs_by_name=self.requestable_specs_by_name,
                tool_request_enabled=self.tool_request_enabled,
                iter_tool_records=prepared.iter_tool_records,
                iter_llm_duration_ms=prepared.iter_llm_duration_ms,
                iter_input_tokens=prepared.iter_input_tokens,
                iter_output_tokens=prepared.iter_output_tokens,
                tool_batch_runner=self.tool_batch_runner,
                loop_cache=self.loop_cache,
                on_tool_result=self.on_tool_result,
                append_tool_result_payload=_append_tool_result_payload,
                set_turn_progress=_set_turn_progress,
                repair_stale_exact_date_search_args=_repair_stale_exact_date_search_args,
                stale_exact_date_query_reason=_stale_exact_date_query_reason,
            )
            if dispatch_phase.outcome is not None:
                return dispatch_phase.outcome
            if dispatch_phase.continue_loop:
                continue

            execution_phase = execute_iteration_results(
                self.loop_ctx,
                profile=self.profile,
                loop_state=self.loop_state,
                runtime=self.runtime,
                model=self.model,
                max_output_tokens=self.max_output_tokens,
                metadata=self.metadata,
                allowed_tools=self.allowed_tools,
                public_mode_tag=self.public_mode_tag,
                signature=signature,
                ordered_tool_results=dispatch_phase.ordered_tool_results,
                cached_indices=dispatch_phase.cached_indices,
                iter_batch_parallel_count=dispatch_phase.iter_batch_parallel_count,
                dispatch_budget_managed=dispatch_phase.dispatch_budget_managed,
                initial_batch_had_progress=dispatch_phase.batch_had_progress,
                loop_cache=self.loop_cache,
                loop_profiler=self.loop_profiler,
                on_tool_result=self.on_tool_result,
                iter_tool_records=prepared.iter_tool_records,
                append_tool_result_payload=_append_tool_result_payload,
                set_turn_progress=_set_turn_progress,
                effective_cap=_effective_cap,
                debit_tool_budget=_debit_tool_budget,
                profile_budget_exhausted=_profile_budget_exhausted,
                tool_budget_exhausted_for_answer_only=_tool_budget_exhausted_for_answer_only,
                force_budget_answer_only_finalization=_force_budget_answer_only_finalization,
                build_missing_action_result=_build_missing_action_result,
                build_tool_failure_recovery_message=_build_tool_failure_recovery_message,
                build_enrichment_message=_build_enrichment_message,
                direct_tool_turn_active=_direct_tool_turn_active,
                trigger_macro_correction=trigger_macro_correction,
                dispatch_correction_plan=dispatch_correction_plan,
            )
            if execution_phase.outcome is not None:
                return execution_phase.outcome

            self.prefetch_pending = finalize_iteration_state(
                self.loop_ctx,
                profile=self.profile,
                loop_state=self.loop_state,
                batch_had_progress=execution_phase.batch_had_progress,
                signature=signature,
                ordered_tool_results=dispatch_phase.ordered_tool_results,
                tool_calls=dispatch_phase.tool_calls,
                prefetch_predictor=self.prefetch_predictor,
                prefetch_pending=self.prefetch_pending,
                loop_cache=self.loop_cache,
                loop_profiler=self.loop_profiler,
                iter_llm_duration_ms=prepared.iter_llm_duration_ms,
                iter_tool_records=prepared.iter_tool_records,
                iter_input_tokens=prepared.iter_input_tokens,
                iter_output_tokens=prepared.iter_output_tokens,
                turn_scope_id=self.turn_scope_id,
                model=self.model,
                public_mode_name=self.public_mode_name,
                record_duplicate_batch_execution_facts=_record_duplicate_batch_execution_facts,
                direct_tool_batch_completed_successfully=_direct_tool_batch_completed_successfully,
            )

        return finalize_iteration_cap_exit(
            self.loop_ctx,
            profile=self.profile,
            loop_state=self.loop_state,
            runtime=self.runtime,
            model=self.model,
            allowed_tools=self.allowed_tools,
            public_mode_name=self.public_mode_name,
            public_mode_tag=self.public_mode_tag,
            max_output_tokens=self.max_output_tokens,
            metadata=self.metadata,
            loop_profiler=self.loop_profiler,
            trigger_macro_correction=trigger_macro_correction,
            dispatch_correction_plan=dispatch_correction_plan,
        )


__all__ = [
    "_build_intent_execution_state_message",
    "_count_substantive_non_control_tool_results",
    "_explicit_calendar_years",
    "_loop_has_non_success_tool_result",
    "_loop_tool_result_payloads",
    "_requires_typed_finalization_contract",
    "_tool_request_result",
    "_tool_result_payload_from_action",
    "run_adaptive_tool_loop",
]
