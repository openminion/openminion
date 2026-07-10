# mypy: disable-error-code="attr-defined,has-type,no-any-return"

from __future__ import annotations

import time
from typing import Any

from openminion.modules.llm.schemas import Message
from ..budget import (
    _debit_llm_usage,
    _profile_budget_exhausted,
    _remaining_budget_fraction,
    _tool_call_budget_exhausted,
    _token_budget_exhausted,
)
from ..budget_control import (
    _answer_only_finalization_messages,
    _effective_cap,
    _force_budget_answer_only_finalization,
    _maybe_extend_iteration_budget,
    _is_internal_failure_final_text,
)
from ..contracts import (
    ADAPTIVE_TERM_BUDGET_EXHAUSTED,
    ADAPTIVE_TERM_DIRECT_TOOL_CLOSURE_FAILED,
    ADAPTIVE_TERM_DISALLOWED_TOOL,
    ADAPTIVE_TERM_DUPLICATE_TOOL_CALLS,
    ADAPTIVE_TERM_FINAL_TEXT,
    ADAPTIVE_TERM_LLM_ERROR,
    AdaptiveToolLoopOutcome,
)
from ..correction import build_correction_history_summary
from ..direct_tool import (
    _build_direct_tool_closure_message,
    _should_force_direct_tool_closure,
    _visible_tool_specs_for_direct_tool_turn,
)
from ..engine_closure import AdaptiveLoopRunnerClosureMixin
from ..no_tool import AdaptiveLoopRunnerNoToolMixin
from ..events import IterationToolCallRecord
from ..evidence import _successful_substantive_tool_results
from ..iteration.helpers import (
    _build_intent_execution_state_message,
    _set_turn_progress,
)
from ..messages import format_blocking_tool_message
from .rules import (
    _looks_like_execution_preface_draft,
    _looks_like_unexecutable_tool_payload_text,
)
from ..response_payloads import _pending_finalization_salvage_text
from ..runtime import _extract_visible_response_text
from ..seeded import _run_seeded_command_step
from ..status import emit_adaptive_status


class AdaptiveLoopRunnerPostprocessMixin(
    AdaptiveLoopRunnerClosureMixin,
    AdaptiveLoopRunnerNoToolMixin,
):
    def _retry_pending_duplicate_batch_closeout(self) -> AdaptiveToolLoopOutcome:
        compact_closeout = self._force_compact_answer_only_closeout()
        if compact_closeout is not None:
            return compact_closeout
        self.loop_state.termination_reason = ADAPTIVE_TERM_DUPLICATE_TOOL_CALLS
        emit_adaptive_status(
            self.loop_ctx,
            profile=self.profile,
            loop_state=self.loop_state,
            detail_text=f"{self.public_mode_tag} repeated tool batch",
            mode_state="duplicate_tool_calls",
            termination_reason=ADAPTIVE_TERM_DUPLICATE_TOOL_CALLS,
        )
        return AdaptiveToolLoopOutcome(
            profile_name=self.profile.profile_name,
            mode_name=self.profile.mode_name,
            termination_reason=ADAPTIVE_TERM_DUPLICATE_TOOL_CALLS,
            state=self.loop_state,
            allowed_tools=self.allowed_tools,
            error_message="Answer-only closure returned more tool calls.",
        )

    def _force_compact_answer_only_closeout(self) -> AdaptiveToolLoopOutcome | None:
        tool_results = _successful_substantive_tool_results(self.loop_state)
        if not tool_results:
            return None
        self.loop_state.scratchpad[
            "tool_choice_none_compact_answer_only_retry_used"
        ] = True
        finalization_messages = _answer_only_finalization_messages(
            loop_ctx=self.loop_ctx,
            loop_state=self.loop_state,
            tool_results=tool_results,
            reason=(
                "The previous answer-only closeout still returned tool calls. "
                "Do not call more tools. Use only the successful tool evidence "
                "already gathered and return the final user-facing answer now."
            ),
        )
        emit_adaptive_status(
            self.loop_ctx,
            profile=self.profile,
            loop_state=self.loop_state,
            detail_text=f"{self.public_mode_tag} compact answer-only closeout",
            mode_state="answer_only_compact_closeout",
        )
        try:
            response = self.runtime.complete(
                messages=finalization_messages,
                tools=[],
                model=self.model,
                tool_choice="none",
                max_output_tokens=self.max_output_tokens,
                metadata=self.metadata,
            )
        except Exception:  # noqa: BLE001
            return None
        _debit_llm_usage(self.loop_ctx, response)
        self.loop_state.llm_calls += 1
        for assistant_message in list(
            getattr(response, "assistant_messages", []) or []
        ):
            self.loop_state.messages.append(assistant_message)
        if not bool(getattr(response, "ok", False)):
            return None
        final_text = _extract_visible_response_text(response)
        if (
            final_text
            and not _is_internal_failure_final_text(final_text)
            and not _looks_like_execution_preface_draft(final_text)
        ):
            self.loop_state.termination_reason = ADAPTIVE_TERM_FINAL_TEXT
            return AdaptiveToolLoopOutcome(
                profile_name=self.profile.profile_name,
                mode_name=self.profile.mode_name,
                termination_reason=ADAPTIVE_TERM_FINAL_TEXT,
                state=self.loop_state,
                allowed_tools=self.allowed_tools,
                final_text=final_text,
            )
        if list(getattr(response, "tool_calls", []) or []):
            return None
        if not final_text or _is_internal_failure_final_text(final_text):
            return None
        self.loop_state.termination_reason = ADAPTIVE_TERM_FINAL_TEXT
        return AdaptiveToolLoopOutcome(
            profile_name=self.profile.profile_name,
            mode_name=self.profile.mode_name,
            termination_reason=ADAPTIVE_TERM_FINAL_TEXT,
            state=self.loop_state,
            allowed_tools=self.allowed_tools,
            final_text=final_text,
        )

    def _handle_iteration_cap(self) -> tuple[str, AdaptiveToolLoopOutcome | None]:
        profile = self.profile
        loop_state = self.loop_state
        if loop_state.iteration >= _effective_cap(profile, loop_state):
            extension_result = _maybe_extend_iteration_budget(
                loop_ctx=self.loop_ctx,
                profile=profile,
                loop_state=loop_state,
                allowed_tools=self.allowed_tools,
                public_mode_tag=self.public_mode_tag,
            )
            if extension_result is True:
                return "continue", None
            if extension_result is False:
                return "break", None
            return "return", extension_result
        return "proceed", None

    def _handle_seeded_and_budget(self) -> tuple[str, AdaptiveToolLoopOutcome | None]:
        handled_seeded, seeded_outcome = _run_seeded_command_step(
            loop_ctx=self.loop_ctx,
            profile=self.profile,
            loop_state=self.loop_state,
            seeded_queue=self.seeded_queue,
            allowed_tools=self.allowed_tools,
            public_mode_tag=self.public_mode_tag,
            finalizer=self.finalizer,
            on_tool_result=self.on_tool_result,
            build_missing_action_result=self._build_missing_action_result,
            append_tool_result_payload=self._append_tool_result_payload,
            token_budget_exhausted=_token_budget_exhausted,
            profile_budget_exhausted=_profile_budget_exhausted,
        )
        if handled_seeded:
            return (
                ("return", seeded_outcome)
                if seeded_outcome is not None
                else ("continue", None)
            )
        if not (
            _token_budget_exhausted(self.loop_ctx, self.loop_state)
            or _profile_budget_exhausted(profile=self.profile, state=self.loop_state)
        ):
            return "proceed", None
        if _tool_call_budget_exhausted(self.loop_ctx, self.loop_state):
            budget_outcome = _force_budget_answer_only_finalization(
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
            if budget_outcome is not None:
                return "return", budget_outcome
            self.loop_state.termination_reason = ADAPTIVE_TERM_BUDGET_EXHAUSTED
            emit_adaptive_status(
                self.loop_ctx,
                profile=self.profile,
                loop_state=self.loop_state,
                detail_text=f"{self.public_mode_tag} tool-call budget exhausted",
                mode_state="budget_exhausted",
                termination_reason=ADAPTIVE_TERM_BUDGET_EXHAUSTED,
            )
            return "return", AdaptiveToolLoopOutcome(
                profile_name=self.profile.profile_name,
                mode_name=self.profile.mode_name,
                termination_reason=ADAPTIVE_TERM_BUDGET_EXHAUSTED,
                state=self.loop_state,
                allowed_tools=self.allowed_tools,
            )
        budget_outcome = _force_budget_answer_only_finalization(
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
        if budget_outcome is not None:
            return "return", budget_outcome
        self.loop_state.termination_reason = ADAPTIVE_TERM_BUDGET_EXHAUSTED
        emit_adaptive_status(
            self.loop_ctx,
            profile=self.profile,
            loop_state=self.loop_state,
            detail_text=f"{self.public_mode_tag} budget exhausted",
            mode_state="budget_exhausted",
            termination_reason=ADAPTIVE_TERM_BUDGET_EXHAUSTED,
        )
        return "return", AdaptiveToolLoopOutcome(
            profile_name=self.profile.profile_name,
            mode_name=self.profile.mode_name,
            termination_reason=ADAPTIVE_TERM_BUDGET_EXHAUSTED,
            state=self.loop_state,
            allowed_tools=self.allowed_tools,
        )

    def _prepare_llm_response(self) -> Any:
        profile = self.profile
        loop_state = self.loop_state
        self.loop_state.iteration += 1
        emit_adaptive_status(
            self.loop_ctx,
            profile=self.profile,
            loop_state=self.loop_state,
            detail_text=f"{self.public_mode_tag} llm step {self.loop_state.iteration}",
            mode_state="llm_step",
            extra={"adaptive.current_model": self.model},
        )
        if not self.budget_hint_injected and _remaining_budget_fraction(
            self.loop_ctx, self.profile, self.loop_state
        ) < float(self.profile.budget_conserve_threshold):
            self.budget_hint_injected = True
            self.loop_state.scratchpad["budget_hint_injected"] = True
            self.loop_state.messages.append(
                Message(
                    role="system",
                    content=(
                        "Budget is running low. Prefer targeted short-output tools,"
                        " avoid broad directory listings or full-file reads,"
                        " and aim for a final answer within two more iterations."
                    ),
                )
            )
        correction_summary = build_correction_history_summary(
            self.loop_state.scratchpad
        )
        if correction_summary is not None:
            self.loop_state.messages.append(
                Message(role="system", content=correction_summary)
            )
        intent_state_message = _build_intent_execution_state_message(self.loop_ctx)
        if intent_state_message is not None:
            self.loop_state.messages.append(intent_state_message)
        _set_turn_progress(
            loop_state,
            llm_call_count=self.loop_state.llm_calls + 1,
            llm_call_limit=_effective_cap(profile, loop_state),
            progress_phase="thinking...",
            tool_name="",
        )
        emit_adaptive_status(
            self.loop_ctx,
            profile=self.profile,
            loop_state=self.loop_state,
            detail_text=f"{self.public_mode_tag} thinking",
            mode_state="llm_call",
        )
        llm_start = time.monotonic()
        llm_tools = _visible_tool_specs_for_direct_tool_turn(
            self.loop_state,
            self.active_tool_specs,
        )
        llm_tool_choice = self.profile.tool_choice
        response_was_tool_suppressed = llm_tool_choice == "none"
        if _pending_finalization_salvage_text(self.loop_state):
            llm_tools = []
            llm_tool_choice = "none"
            response_was_tool_suppressed = True
        elif bool(
            self.loop_state.scratchpad.get(
                "duplicate_batch_answer_only_closure_pending", False
            )
        ):
            llm_tools = []
            llm_tool_choice = "none"
            response_was_tool_suppressed = True
        elif _should_force_direct_tool_closure(self.loop_state):
            self.loop_state.direct_tool_closure_consumed = True
            self.loop_state.scratchpad["direct_tool_closure_forced"] = True
            self.loop_state.messages.append(
                _build_direct_tool_closure_message(self.loop_state)
            )
            llm_tools = []
            llm_tool_choice = "none"
            response_was_tool_suppressed = True
        elif bool(
            getattr(self.loop_state, "direct_tool_requested_batch_satisfied", False)
        ) and bool(getattr(self.loop_state, "direct_tool_closure_consumed", False)):
            llm_tools = []
            llm_tool_choice = "none"
            response_was_tool_suppressed = True

        if self.pending_response is not None:
            response = self.pending_response
            self.pending_response = None
        else:
            try:
                response = self.runtime.complete(
                    messages=self.loop_state.messages,
                    tools=llm_tools,
                    model=self.model,
                    tool_choice=llm_tool_choice,
                    max_output_tokens=self.max_output_tokens,
                    metadata=self.metadata,
                )
            except Exception as exc:
                self.loop_state.termination_reason = ADAPTIVE_TERM_LLM_ERROR
                emit_adaptive_status(
                    self.loop_ctx,
                    profile=self.profile,
                    loop_state=self.loop_state,
                    detail_text=f"{self.public_mode_tag} LLM call failed",
                    mode_state="llm_error",
                    termination_reason=ADAPTIVE_TERM_LLM_ERROR,
                )
                return AdaptiveToolLoopOutcome(
                    profile_name=self.profile.profile_name,
                    mode_name=self.profile.mode_name,
                    termination_reason=ADAPTIVE_TERM_LLM_ERROR,
                    state=self.loop_state,
                    allowed_tools=self.allowed_tools,
                    error_message=str(exc),
                )
        iter_llm_duration_ms = int((time.monotonic() - llm_start) * 1000)
        iter_tool_records: list[IterationToolCallRecord] = []
        iter_input_tokens = 0
        iter_output_tokens = 0
        usage = getattr(response, "usage", None)
        if usage is not None:
            iter_input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
            iter_output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        _debit_llm_usage(self.loop_ctx, response)
        self.loop_state.llm_calls += 1
        _set_turn_progress(
            loop_state,
            llm_call_count=self.loop_state.llm_calls,
            llm_call_limit=_effective_cap(profile, loop_state),
            input_tokens_delta=iter_input_tokens,
            output_tokens_delta=iter_output_tokens,
            progress_phase="composing answer",
            tool_name="",
        )
        tool_calls_present = bool(getattr(response, "tool_calls", []) or [])
        appended_assistant_messages = 0
        for assistant_message in list(
            getattr(response, "assistant_messages", []) or []
        ):
            assistant_text = getattr(assistant_message, "content", "")
            if _looks_like_unexecutable_tool_payload_text(assistant_text):
                continue
            if tool_calls_present and _looks_like_execution_preface_draft(
                assistant_text
            ):
                continue
            self.loop_state.messages.append(assistant_message)
            appended_assistant_messages += 1
        if appended_assistant_messages == 0:
            fallback_output_text = str(
                getattr(response, "output_text", "") or ""
            ).strip()
            if (
                fallback_output_text
                and not _looks_like_unexecutable_tool_payload_text(fallback_output_text)
                and not (
                    tool_calls_present
                    and _looks_like_execution_preface_draft(fallback_output_text)
                )
            ):
                self.loop_state.messages.append(
                    Message(role="assistant", content=fallback_output_text)
                )
        if not bool(getattr(response, "ok", False)):
            error = getattr(response, "error", None)
            error_message = str(getattr(error, "message", "") or "LLM returned not-ok")
            self.loop_state.termination_reason = ADAPTIVE_TERM_LLM_ERROR
            emit_adaptive_status(
                self.loop_ctx,
                profile=self.profile,
                loop_state=self.loop_state,
                detail_text=f"{self.public_mode_tag} LLM error",
                mode_state="llm_error",
                termination_reason=ADAPTIVE_TERM_LLM_ERROR,
            )
            return AdaptiveToolLoopOutcome(
                profile_name=self.profile.profile_name,
                mode_name=self.profile.mode_name,
                termination_reason=ADAPTIVE_TERM_LLM_ERROR,
                state=self.loop_state,
                allowed_tools=self.allowed_tools,
                error_message=error_message,
            )
        return self._PreparedLoopResponse(
            response=response,
            response_was_tool_suppressed=response_was_tool_suppressed,
            iter_llm_duration_ms=iter_llm_duration_ms,
            iter_tool_records=iter_tool_records,
            iter_input_tokens=iter_input_tokens,
            iter_output_tokens=iter_output_tokens,
        )

    def _handle_tool_suppressed_retry(
        self,
        *,
        tool_calls: list[Any],
        response_was_tool_suppressed: bool,
    ) -> tuple[bool, AdaptiveToolLoopOutcome | None]:
        if not response_was_tool_suppressed or not tool_calls:
            return False, None
        if bool(
            self.loop_state.scratchpad.get(
                "duplicate_batch_answer_only_closure_pending", False
            )
        ):
            return False, self._retry_pending_duplicate_batch_closeout()
        retry_key = "tool_choice_none_retry_used"
        direct_tool_closure_active = bool(
            self.loop_state.scratchpad.get("direct_tool_closure_forced", False)
        ) or bool(
            getattr(self.loop_state, "direct_tool_requested_batch_satisfied", False)
        )
        pending_finalization_text = _pending_finalization_salvage_text(self.loop_state)
        budget_answer_only_active = bool(
            self.loop_state.scratchpad.get(
                "budget_answer_only_finalization_forced", False
            )
        )
        final_answer_reserve_active = bool(
            self.loop_state.scratchpad.get("coding.final_answer_reserve_used", False)
        )
        if not bool(self.loop_state.scratchpad.get(retry_key, False)):
            self.loop_state.scratchpad[retry_key] = True
            if direct_tool_closure_active:
                message = (
                    "The requested tool batch already completed successfully. "
                    "Do not call any more tools. Return only the final "
                    "user-facing answer now."
                )
            elif pending_finalization_text is not None:
                message = (
                    "Do not call tools. Return only the structured "
                    "finalization_status signal now."
                )
            elif final_answer_reserve_active or budget_answer_only_active:
                message = (
                    "Do not call tools. Return only the final user-facing "
                    "answer now. Preserve any explicit labels, result markers, "
                    "headings, validation notes, files-changed summaries, and "
                    "other requested final-output constraints."
                )
            else:
                message = (
                    "This turn cannot call tools. Return a user-facing "
                    "answer without any tool calls."
                )
            self.loop_state.messages.append(Message(role="system", content=message))
            return True, None
        if (
            pending_finalization_text is not None
            or budget_answer_only_active
            or final_answer_reserve_active
        ):
            compact_closeout = self._force_compact_answer_only_closeout()
            if compact_closeout is not None:
                return False, compact_closeout
            termination_reason = ADAPTIVE_TERM_BUDGET_EXHAUSTED
            error_message = "Answer-only finalization kept returning tool calls."
        else:
            termination_reason = (
                ADAPTIVE_TERM_DIRECT_TOOL_CLOSURE_FAILED
                if direct_tool_closure_active
                else ADAPTIVE_TERM_LLM_ERROR
            )
            error_message = (
                "Model returned tool calls after tool_choice=none was enforced."
            )
        self.loop_state.termination_reason = termination_reason
        emit_adaptive_status(
            self.loop_ctx,
            profile=self.profile,
            loop_state=self.loop_state,
            detail_text=f"{self.public_mode_tag} tool-suppressed call retry failed",
            mode_state="llm_error",
            termination_reason=termination_reason,
        )
        return False, AdaptiveToolLoopOutcome(
            profile_name=self.profile.profile_name,
            mode_name=self.profile.mode_name,
            termination_reason=termination_reason,
            state=self.loop_state,
            allowed_tools=self.allowed_tools,
            error_message=error_message,
        )

    def _validate_tool_calls(
        self,
        tool_calls: list[Any],
    ) -> AdaptiveToolLoopOutcome | None:
        for tool_call in tool_calls:
            tool_name = str(getattr(tool_call, "name", "") or "").strip()
            if tool_name in self.allowed_tools:
                continue
            message = (
                f"{self.profile.mode_name} does not allow tool {tool_name!r}. "
                f"Allowed: {sorted(self.allowed_tools)}"
            )
            self.loop_state.messages.append(
                format_blocking_tool_message(
                    tool_name=tool_name,
                    reason=message,
                    termination_reason=ADAPTIVE_TERM_DISALLOWED_TOOL,
                )
            )
            self.loop_state.termination_reason = ADAPTIVE_TERM_DISALLOWED_TOOL
            emit_adaptive_status(
                self.loop_ctx,
                profile=self.profile,
                loop_state=self.loop_state,
                detail_text=f"{self.public_mode_tag} disallowed tool: {tool_name}",
                mode_state="disallowed_tool",
                termination_reason=ADAPTIVE_TERM_DISALLOWED_TOOL,
                extra={"tool_name": tool_name},
            )
            return AdaptiveToolLoopOutcome(
                profile_name=self.profile.profile_name,
                mode_name=self.profile.mode_name,
                termination_reason=ADAPTIVE_TERM_DISALLOWED_TOOL,
                state=self.loop_state,
                allowed_tools=self.allowed_tools,
                error_message=message,
                tool_name=tool_name,
            )
        return None
