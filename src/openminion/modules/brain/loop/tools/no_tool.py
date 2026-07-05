# mypy: disable-error-code="attr-defined,has-type,no-any-return"

from __future__ import annotations

from typing import Any

from openminion.base.constants import STATE_KEY_FINALIZATION_STATUS
from openminion.modules.brain.schemas import FinalizationStatus
from openminion.modules.llm.schemas import Message

from .contracts import (
    ADAPTIVE_TERM_FINALIZATION_CONTRACT_MISSING,
    ADAPTIVE_TERM_LLM_ERROR,
    ADAPTIVE_TERM_REQUESTED_TOOL_NOT_EXECUTED,
    AdaptiveToolLoopOutcome,
)
from .budget_control import _is_internal_failure_final_text
from .direct_tool import (
    _direct_tool_turn_active,
    _remaining_direct_tool_name_sequence,
)
from .postprocess.rules import (
    _final_answer_references_unbacked_source_urls,
    _final_text_parrots_policy_denial,
    _looks_like_execution_preface_draft,
    _looks_like_pre_tool_draft_echo,
    _looks_like_structured_final_answer,
    _looks_like_structured_status_payload,
    _looks_like_unexecutable_tool_payload_text,
    _raw_tool_payload_retry_allowed,
)
from .iteration.helpers import (
    _count_substantive_non_control_tool_results,
    _requires_typed_finalization_contract,
)
from .iteration.termination import build_no_tool_outcome
from .response_payloads import (
    _FINALIZATION_STATUS_SALVAGE_GUIDANCE,
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
from .runtime import _extract_visible_response_text
from .status import emit_adaptive_status


class AdaptiveLoopRunnerNoToolMixin:
    def _build_response_payloads(self, response: Any) -> dict[str, Any]:
        finalization_status = _finalization_status_payload(response)
        final_text = _extract_visible_response_text(response)
        salvage_text = _pending_finalization_salvage_text(self.loop_state)
        if (
            salvage_text is not None
            and finalization_status is not None
            and not str(final_text or "").strip()
        ):
            final_text = salvage_text
        if salvage_text is not None and finalization_status is not None:
            self.loop_state.scratchpad.pop(
                "typed_finalization_status_salvage_text", None
            )
        if (
            salvage_text is not None
            and finalization_status is None
            and not str(final_text or "").strip()
        ):
            final_text = str(salvage_text).strip()
        return {
            "confident_complete": _confident_complete_payload(response),
            STATE_KEY_FINALIZATION_STATUS: finalization_status,
            "pending_turn_context": _pending_turn_context_payload(response),
            "meta_rule_preference": _meta_rule_preference_payload(response),
            "memory_consolidation": _memory_consolidation_payload(response),
            "session_work_summary": _session_work_summary_payload(response),
            "goal_declaration": _goal_declaration_payload(response),
            "goal_revision": _goal_revision_payload(response),
            "delegation_context": _delegation_context_payload(response),
            "delegation_result_summary": _delegation_result_summary_payload(response),
            "watch_outcome": _watch_outcome_payload(response),
            "task_plan": _task_plan_payload(response),
            "task_plan_step_completed": _task_plan_step_completed_payload(response),
            "task_plan_step_blocked": _task_plan_step_blocked_payload(response),
            "task_plan_revision": _task_plan_revision_payload(response),
            "task_plan_abandoned": _task_plan_abandoned_payload(response),
            "task_plan_completed": _task_plan_completed_payload(response),
            "final_text": final_text,
            "salvage_text": salvage_text,
        }

    def _retry_with_system_message(
        self,
        message: str,
        *,
        discard_assistant_text: str | None = None,
    ) -> tuple[bool, None]:
        discard_token = str(discard_assistant_text or "").strip()
        if discard_token:
            messages = list(getattr(self.loop_state, "messages", []) or [])
            if messages:
                last = messages[-1]
                if (
                    getattr(last, "role", "") == "assistant"
                    and str(getattr(last, "content", "") or "").strip() == discard_token
                ):
                    self.loop_state.messages = messages[:-1]
        self.loop_state.messages.append(Message(role="system", content=message))
        return True, None

    def _handle_no_tool_calls(
        self,
        *,
        prepared: Any,
        payloads: dict[str, Any],
    ) -> tuple[bool, AdaptiveToolLoopOutcome | None]:
        requires_finalization_status = _requires_typed_finalization_contract(
            profile=self.profile,
            loop_state=self.loop_state,
        )
        finalization_status = payloads[STATE_KEY_FINALIZATION_STATUS]
        final_text = payloads["final_text"]
        salvage_text = payloads["salvage_text"]
        confident_complete = payloads["confident_complete"]
        normalized_final_text = str(final_text or "").strip()
        if (
            normalized_final_text
            and _is_internal_failure_final_text(normalized_final_text)
            and _count_substantive_non_control_tool_results(self.loop_state) > 0
        ):
            retry_key = "provider_fallback_final_answer_retry_used"
            if not bool(self.loop_state.scratchpad.get(retry_key, False)):
                self.loop_state.scratchpad[retry_key] = True
                return self._retry_with_system_message(
                    "Your previous reply was a provider recovery/fallback message, "
                    "not the actual final answer. Use the completed tool results "
                    "already in context and return the final user-facing answer "
                    "now. Do not say the response was empty or ask the user to "
                    "retry unless you still lack evidence after using the existing "
                    "tool results.",
                    discard_assistant_text=normalized_final_text,
                )
            self.loop_state.termination_reason = ADAPTIVE_TERM_LLM_ERROR
            emit_adaptive_status(
                self.loop_ctx,
                profile=self.profile,
                loop_state=self.loop_state,
                detail_text=f"{self.public_mode_tag} provider fallback final answer",
                mode_state="llm_error",
                termination_reason=ADAPTIVE_TERM_LLM_ERROR,
            )
            return False, AdaptiveToolLoopOutcome(
                profile_name=self.profile.profile_name,
                mode_name=self.profile.mode_name,
                termination_reason=ADAPTIVE_TERM_LLM_ERROR,
                state=self.loop_state,
                allowed_tools=self.allowed_tools,
                error_message=(
                    "Model returned provider recovery text instead of a real final answer."
                ),
            )
        if (
            normalized_final_text
            and _looks_like_unexecutable_tool_payload_text(normalized_final_text)
            and _raw_tool_payload_retry_allowed(
                self.loop_state,
                text=normalized_final_text,
            )
        ):
            return self._retry_with_system_message(
                "Your previous reply emitted raw tool markup, a raw tool-result "
                "JSON envelope, or an unexecutable tool envelope. Use existing "
                "tool results and return only the final plain-text answer.",
                discard_assistant_text=normalized_final_text,
            )
        if (
            normalized_final_text
            and _looks_like_structured_status_payload(normalized_final_text)
            and _count_substantive_non_control_tool_results(self.loop_state) > 0
            and not bool(
                self.loop_state.scratchpad.get(
                    "structured_status_final_answer_retry_used", False
                )
            )
        ):
            self.loop_state.scratchpad["structured_status_final_answer_retry_used"] = (
                True
            )
            return self._retry_with_system_message(
                "Your previous reply was a structured status payload, not the "
                "user-facing final answer. Return the final answer text now. "
                "Preserve any exact final-answer format, headings, section "
                "titles, and ordering the user requested.",
                discard_assistant_text=normalized_final_text,
            )
        if (
            normalized_final_text
            and (
                _looks_like_pre_tool_draft_echo(
                    self.loop_state,
                    text=normalized_final_text,
                )
                or _looks_like_execution_preface_draft(normalized_final_text)
            )
            and _count_substantive_non_control_tool_results(self.loop_state) > 0
            and not bool(
                self.loop_state.scratchpad.get("pre_tool_draft_echo_retry_used", False)
            )
        ):
            self.loop_state.scratchpad["pre_tool_draft_echo_retry_used"] = True
            return self._retry_with_system_message(
                "Your previous reply repeated the pre-tool draft instead of the "
                "final answer. Use the completed tool results already in context "
                "and return the actual final user-facing answer now. Do not "
                "repeat planning or execution-preface text like 'Now executing'. "
                "If the turn requires typed finalization, append the required "
                "finalization_status trailer after the answer.",
                discard_assistant_text=normalized_final_text,
            )
        if (
            normalized_final_text
            and _count_substantive_non_control_tool_results(self.loop_state) > 0
            and _final_answer_references_unbacked_source_urls(
                self.loop_state,
                text=normalized_final_text,
            )
            and not bool(
                self.loop_state.scratchpad.get("unbacked_source_url_retry_used", False)
            )
        ):
            self.loop_state.scratchpad["unbacked_source_url_retry_used"] = True
            return self._retry_with_system_message(
                "Your previous reply cited source URLs that do not appear in the "
                "successful tool results for this turn. Do not claim a URL was "
                "fetched, read, or verified unless the successful tool results "
                "already contain that URL. Continue with the missing tool calls or "
                "return a truthful incomplete/blocked answer from the evidence you "
                "actually gathered.",
                discard_assistant_text=normalized_final_text,
            )
        if (
            normalized_final_text
            and _final_text_parrots_policy_denial(
                self.loop_state,
                text=normalized_final_text,
            )
            and not bool(
                self.loop_state.scratchpad.get("policy_denial_parrot_retry_used", False)
            )
        ):
            self.loop_state.scratchpad["policy_denial_parrot_retry_used"] = True
            return self._retry_with_system_message(
                "Your previous reply only repeated the exec.run policy denial. Do "
                "not stop there. Apply the suggested policy fix from the tool "
                "result, run the allowed direct verification command next, and then "
                "finish the task from actual tool results.",
                discard_assistant_text=normalized_final_text,
            )
        if _direct_tool_turn_active(self.loop_state) and not bool(
            getattr(self.loop_state, "direct_tool_requested_batch_satisfied", False)
        ):
            requested_tools = _remaining_direct_tool_name_sequence(self.loop_state)
            retry_key = tuple(requested_tools)
            if not retry_key:
                requested_tools = tuple(
                    getattr(
                        getattr(self.loop_state, "direct_tool_turn", None),
                        "requested_tool_names",
                        (),
                    )
                    or ()
                )
                retry_key = tuple(requested_tools)
            retry_counts = dict(
                self.loop_state.scratchpad.get("direct_tool_zero_call_retry_counts", {})
                or {}
            )
            retry_count = int(retry_counts.get(retry_key, 0) or 0)
            if retry_count < 1:
                rendered_tools = (
                    ", ".join(requested_tools)
                    if requested_tools
                    else "the requested tool"
                )
                retry_counts[retry_key] = retry_count + 1
                self.loop_state.scratchpad["direct_tool_zero_call_retry_counts"] = (
                    retry_counts
                )
                return self._retry_with_system_message(
                    f"This is an explicit tool command for {rendered_tools}. "
                    f"The remaining required tool sequence is exactly: {rendered_tools}. "
                    "Call that sequence next and do not call other tools before it. "
                    "Do not emit submit_output yet. Do not stop to argue that more "
                    "context would help if the required tools are available. Use the "
                    "available prompt context and prior tool results, complete the "
                    "required tool sequence, and only then continue to later "
                    "verification or final-answer steps.",
                    discard_assistant_text=normalized_final_text,
                )
        if _direct_tool_turn_active(self.loop_state) and not bool(
            getattr(self.loop_state, "direct_tool_requested_batch_satisfied", False)
        ):
            self.loop_state.termination_reason = (
                ADAPTIVE_TERM_REQUESTED_TOOL_NOT_EXECUTED
            )
            emit_adaptive_status(
                self.loop_ctx,
                profile=self.profile,
                loop_state=self.loop_state,
                detail_text=f"{self.public_mode_tag} requested tool not executed",
                mode_state="requested_tool_not_executed",
                termination_reason=ADAPTIVE_TERM_REQUESTED_TOOL_NOT_EXECUTED,
            )
            return False, AdaptiveToolLoopOutcome(
                profile_name=self.profile.profile_name,
                mode_name=self.profile.mode_name,
                termination_reason=ADAPTIVE_TERM_REQUESTED_TOOL_NOT_EXECUTED,
                state=self.loop_state,
                allowed_tools=self.allowed_tools,
                error_message=(
                    "The requested tool was not executed, so I cannot truthfully "
                    "claim it succeeded."
                ),
            )
        if (
            requires_finalization_status
            and finalization_status is None
            and _looks_like_structured_final_answer(str(final_text or ""))
            and _count_substantive_non_control_tool_results(self.loop_state) > 0
        ):
            finalization_status = FinalizationStatus(
                status="final_answer",
                reasoning=(
                    "Accepted structured final answer when the typed "
                    "finalization trailer was omitted."
                ),
            )
        if (
            requires_finalization_status
            and finalization_status is None
            and not bool(
                self.loop_state.scratchpad.get(
                    "typed_finalization_status_retry_used", False
                )
            )
        ):
            self.loop_state.scratchpad["typed_finalization_status_retry_used"] = True
            return self._retry_with_system_message(
                "This act turn is ending through a route that requires typed "
                "finalization. Return final answer text and finalization_status "
                "status=final_answer, status=incomplete, or status=blocked. If "
                "environment work is unfinished, resume with the required tool "
                "calls instead. Preserve any exact final-answer format, headings, "
                "section titles, and ordering the user requested.",
                discard_assistant_text=normalized_final_text,
            )
        if (
            requires_finalization_status
            and finalization_status is None
            and salvage_text is None
            and str(final_text or "").strip()
            and bool(
                self.loop_state.scratchpad.get(
                    "typed_finalization_status_retry_used", False
                )
            )
        ):
            self.loop_state.scratchpad["typed_finalization_status_salvage_text"] = (
                final_text
            )
            self.loop_state.messages.append(
                Message(role="system", content=_FINALIZATION_STATUS_SALVAGE_GUIDANCE)
            )
            return True, None
        if (
            requires_finalization_status
            and finalization_status is not None
            and not str(final_text or "").strip()
        ):
            return self._retry_with_system_message(
                "You emitted finalization_status without a user-facing answer. "
                "Provide the answer text before the finalization_status signal.",
                discard_assistant_text=normalized_final_text,
            )
        if (
            requires_finalization_status
            and finalization_status is None
            and _looks_like_structured_final_answer(str(final_text or ""))
            and _count_substantive_non_control_tool_results(self.loop_state) > 0
        ):
            finalization_status = FinalizationStatus(
                status="final_answer",
                reasoning=(
                    "Accepted structured final answer after typed finalization "
                    "retry failed to emit the trailer."
                ),
            )
        if (
            confident_complete is not None
            and confident_complete.complete
            and not str(final_text or "").strip()
        ):
            return self._retry_with_system_message(
                "You emitted confident_complete without a final answer. Provide "
                "the user-visible final answer text before the trailer."
            )
        if requires_finalization_status and finalization_status is None:
            self.loop_state.termination_reason = (
                ADAPTIVE_TERM_FINALIZATION_CONTRACT_MISSING
            )
            emit_adaptive_status(
                self.loop_ctx,
                profile=self.profile,
                loop_state=self.loop_state,
                detail_text=f"{self.public_mode_tag} finalization contract missing",
                mode_state="finalization_contract_missing",
                termination_reason=ADAPTIVE_TERM_FINALIZATION_CONTRACT_MISSING,
            )
            return False, AdaptiveToolLoopOutcome(
                profile_name=self.profile.profile_name,
                mode_name=self.profile.mode_name,
                termination_reason=ADAPTIVE_TERM_FINALIZATION_CONTRACT_MISSING,
                state=self.loop_state,
                allowed_tools=self.allowed_tools,
                error_message=(
                    "General act work ended without the required typed "
                    "finalization_status contract."
                ),
            )
        outcome_payloads = dict(payloads)
        outcome_payloads.pop("salvage_text", None)
        outcome_payloads["final_text"] = final_text
        outcome_payloads[STATE_KEY_FINALIZATION_STATUS] = finalization_status
        return False, build_no_tool_outcome(
            self.loop_ctx,
            profile=self.profile,
            loop_state=self.loop_state,
            allowed_tools=self.allowed_tools,
            llm_duration_ms=prepared.iter_llm_duration_ms,
            tokens_used=prepared.iter_input_tokens + prepared.iter_output_tokens,
            finalizer=self.finalizer,
            **outcome_payloads,
        )
