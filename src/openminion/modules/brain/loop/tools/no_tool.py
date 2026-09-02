# mypy: disable-error-code="attr-defined,has-type,no-any-return"

from __future__ import annotations

from typing import Any

from openminion.base.constants import STATE_KEY_FINALIZATION_STATUS
from openminion.modules.brain.loop.constants import (
    RECOVERABLE_TOOL_ARGUMENT_FAILURE_KEY,
    RECOVERABLE_TOOL_ARGUMENT_RETRY_USED_KEY,
)
from openminion.modules.brain.schemas import FinalizationStatus
from openminion.modules.llm.schemas import Message
from openminion.modules.llm import ProviderError

from .budget_finalization import (
    _recover_budget_finalization_status,
    _recover_finalized_answer,
)
from .contracts import (
    ADAPTIVE_TERM_FINALIZATION_CONTRACT_MISSING,
    ADAPTIVE_TERM_REQUESTED_TOOL_NOT_EXECUTED,
    ADAPTIVE_TERM_TOOL_FAILURE_NO_RECOVERY,
    AdaptiveToolLoopOutcome,
)
from .direct_tool import (
    _direct_tool_turn_active,
    _remaining_direct_tool_name_sequence,
)
from .evidence import (
    _has_unresolved_tool_failure,
    _successful_substantive_tool_results,
)
from .postprocess.rules import (
    _final_answer_references_unbacked_source_urls,
    _looks_like_unexecutable_tool_payload_text,
    _raw_tool_payload_retry_allowed,
)
from .postprocess.evidence_closeout import (
    MUTATING_FILE_CLOSEOUT_KEY,
    mutating_file_evidence_fallback_text,
    tool_evidence_closeout_text,
)
from .plan_control import (
    PLAN_CLOSEOUT_SALVAGE_TEXT_SCRATCHPAD_KEY,
    completable_active_plan_id,
    unresolved_active_plan_step_ids,
)
from .iteration.helpers import (
    _count_substantive_non_control_tool_results,
    _requires_typed_finalization_contract,
)
from .iteration.termination import build_no_tool_outcome
from .response_payloads import (
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


def _retry_empty_final_after_tool_results(
    runner: Any,
    *,
    finalization_status: Any,
    final_text: Any,
    normalized_final_text: str,
) -> tuple[bool, None] | None:
    if finalization_status is not None or str(final_text or "").strip():
        return None
    if _count_substantive_non_control_tool_results(runner.loop_state) <= 0:
        return None
    scratchpad = runner.loop_state.scratchpad
    if not bool(scratchpad.get("empty_final_after_tool_results_retry_used", False)):
        scratchpad["empty_final_after_tool_results_retry_used"] = True
        return runner._retry_with_system_message(
            "The previous reply ended without a user-facing answer after "
            "successful tool results. Do not call more tools unless the evidence "
            "is genuinely insufficient. Use the completed tool results already "
            "in context and return the final answer now. If the turn requires "
            "typed finalization, append finalization_status status=final_answer, "
            "status=incomplete, or status=blocked after the answer.",
            discard_assistant_text=normalized_final_text,
        )
    if not bool(
        scratchpad.get("empty_final_after_tool_results_final_retry_used", False)
    ):
        scratchpad["empty_final_after_tool_results_final_retry_used"] = True
        return runner._retry_with_system_message(
            "The previous reply was still empty after successful tool results. "
            "Do not call more tools. Return the final user-facing answer now "
            "from the successful tool results already in context. If the "
            "evidence is insufficient, say what is incomplete or blocked "
            "instead of returning an empty answer.",
            discard_assistant_text=normalized_final_text,
        )
    return None


def _retry_empty_typed_finalization_after_tool_results(
    runner: Any,
    *,
    requires_finalization_status: bool,
    finalization_status: Any,
    final_text: Any,
    normalized_final_text: str,
) -> tuple[bool, None] | None:
    if not requires_finalization_status or finalization_status is not None:
        return None
    if str(final_text or "").strip():
        return None
    if _count_substantive_non_control_tool_results(runner.loop_state) <= 0:
        return None
    scratchpad = runner.loop_state.scratchpad
    if not bool(scratchpad.get("typed_finalization_status_retry_used", False)):
        return None
    if bool(scratchpad.get("typed_finalization_answer_only_retry_used", False)):
        return None
    scratchpad["typed_finalization_answer_only_retry_used"] = True
    return runner._retry_with_system_message(
        "The previous reply still ended without user-facing answer text "
        "or finalization_status. Do not call more tools. Use the successful "
        "tool results already in context and return the final user-facing "
        "answer now, then append finalization_status status=final_answer, "
        "status=incomplete, or status=blocked. Preserve any exact final-answer "
        "format, headings, section titles, and ordering the user requested.",
        discard_assistant_text=normalized_final_text,
    )


def _argument_retry(
    runner: Any,
    finalization_status: Any,
    normalized_final_text: str,
) -> tuple[bool, None] | None:
    scratchpad = runner.loop_state.scratchpad
    pending = scratchpad.get(RECOVERABLE_TOOL_ARGUMENT_FAILURE_KEY)
    if not isinstance(pending, str) or not pending.strip():
        return None
    if isinstance(finalization_status, dict) and finalization_status.get("status") == (
        "blocked"
    ):
        return None
    if bool(scratchpad.get(RECOVERABLE_TOOL_ARGUMENT_RETRY_USED_KEY, False)):
        return None
    scratchpad[RECOVERABLE_TOOL_ARGUMENT_RETRY_USED_KEY] = True
    tool_name = pending.strip()
    return runner._retry_with_system_message(
        f"The prior {tool_name} call failed because its structured arguments were "
        "invalid, and the correction guidance is already in context. Make one "
        "corrected tool call now. Do not repeat the same arguments and do not "
        "guess or rewrite paths in prose. If no valid correction is possible, "
        "return a truthful finalization_status with status=blocked.",
        discard_assistant_text=normalized_final_text,
    )


def _failed_exec_retry(
    runner: Any,
    finalization_status: Any,
    normalized_final_text: str,
) -> tuple[bool, AdaptiveToolLoopOutcome | None] | None:
    if str(getattr(runner.profile, "profile_name", "") or "").strip() != (
        "general_adaptive_v1"
    ):
        return None
    if not _has_unresolved_tool_failure(runner.loop_state, tool_name="exec.run"):
        return None
    status = str(getattr(finalization_status, "status", "") or "").strip()
    if isinstance(finalization_status, dict):
        status = str(finalization_status.get("status", "") or "").strip()
    if status in {"blocked", "incomplete"}:
        return None
    scratchpad = runner.loop_state.scratchpad
    retry_key = "unresolved_exec_failure_retry_used"
    if not bool(scratchpad.get(retry_key, False)):
        scratchpad[retry_key] = True
        return runner._retry_with_system_message(
            "A prior exec.run call failed and no later exec.run call has succeeded. "
            "Use the failure facts already in context, make any needed correction, "
            "and rerun the verifier. Do not return finalization_status "
            "status=final_answer while that failure remains unresolved. If the work "
            "cannot continue, return status=incomplete or status=blocked instead.",
            discard_assistant_text=normalized_final_text,
        )
    runner.loop_state.termination_reason = ADAPTIVE_TERM_TOOL_FAILURE_NO_RECOVERY
    emit_adaptive_status(
        runner.loop_ctx,
        profile=runner.profile,
        loop_state=runner.loop_state,
        detail_text=f"{runner.public_mode_tag} unresolved exec failure",
        mode_state="tool_failure_no_recovery",
        termination_reason=ADAPTIVE_TERM_TOOL_FAILURE_NO_RECOVERY,
    )
    return False, AdaptiveToolLoopOutcome(
        profile_name=runner.profile.profile_name,
        mode_name=runner.profile.mode_name,
        termination_reason=ADAPTIVE_TERM_TOOL_FAILURE_NO_RECOVERY,
        state=runner.loop_state,
        allowed_tools=runner.allowed_tools,
        error_message=(
            "General act work ended while the latest exec.run result was still failed."
        ),
    )


def _requested_direct_tool_not_executed_outcome(
    runner: Any,
) -> tuple[bool, AdaptiveToolLoopOutcome | None]:
    runner.loop_state.termination_reason = ADAPTIVE_TERM_REQUESTED_TOOL_NOT_EXECUTED
    emit_adaptive_status(
        runner.loop_ctx,
        profile=runner.profile,
        loop_state=runner.loop_state,
        detail_text=f"{runner.public_mode_tag} requested tool not executed",
        mode_state="requested_tool_not_executed",
        termination_reason=ADAPTIVE_TERM_REQUESTED_TOOL_NOT_EXECUTED,
    )
    return False, AdaptiveToolLoopOutcome(
        profile_name=runner.profile.profile_name,
        mode_name=runner.profile.mode_name,
        termination_reason=ADAPTIVE_TERM_REQUESTED_TOOL_NOT_EXECUTED,
        state=runner.loop_state,
        allowed_tools=runner.allowed_tools,
        error_message=(
            "The requested tool was not executed, so I cannot truthfully claim "
            "it succeeded."
        ),
    )


def _retry_confident_complete_without_answer(
    runner: Any,
    *,
    confident_complete: Any,
    final_text: Any,
) -> tuple[bool, None] | None:
    if confident_complete is None or not confident_complete.complete:
        return None
    if str(final_text or "").strip():
        return None
    return runner._retry_with_system_message(
        "You emitted confident_complete without a final answer. Provide "
        "the user-visible final answer text before the trailer."
    )


def _unresolved_plan_retry(
    runner: Any,
    *,
    finalization_status: Any,
    normalized_final_text: str,
) -> tuple[bool, None] | None:
    status = str(getattr(finalization_status, "status", "") or "").strip()
    if isinstance(finalization_status, dict):
        status = str(finalization_status.get("status", "") or "").strip()
    if status in {"blocked", "incomplete"} or not normalized_final_text:
        return None
    unresolved_step_ids = unresolved_active_plan_step_ids(runner.loop_ctx)
    if unresolved_step_ids:
        return runner._retry_with_system_message(
            "The active task plan still has unresolved typed steps: "
            f"{', '.join(unresolved_step_ids)}. Continue the work and update those "
            "steps through the plan tool before returning a success answer. If the "
            "work cannot continue, return typed status=incomplete or status=blocked.",
            discard_assistant_text=normalized_final_text,
        )
    plan_id = completable_active_plan_id(runner.loop_ctx)
    if not plan_id:
        return None
    runner.loop_state.scratchpad[PLAN_CLOSEOUT_SALVAGE_TEXT_SCRATCHPAD_KEY] = (
        normalized_final_text
    )
    return runner._retry_with_system_message(
        "Every typed step in the active task plan is completed, but the plan is "
        f"still active. Call the plan tool with action=complete and plan_id={plan_id} "
        "before returning the success answer.",
        discard_assistant_text=normalized_final_text,
    )


def _no_tool_retry(
    runner: Any,
    finalization_status: Any,
    normalized_final_text: str,
) -> tuple[bool, AdaptiveToolLoopOutcome | None] | None:
    return (
        _argument_retry(runner, finalization_status, normalized_final_text)
        or _failed_exec_retry(runner, finalization_status, normalized_final_text)
        or _unresolved_plan_retry(
            runner,
            finalization_status=finalization_status,
            normalized_final_text=normalized_final_text,
        )
    )


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

    def _recovered_finalization_outcome(
        self,
        *,
        prepared: Any,
        payloads: dict[str, Any],
        final_text: str,
        finalization_status: dict[str, Any],
    ) -> tuple[bool, AdaptiveToolLoopOutcome]:
        outcome_payloads = dict(payloads)
        outcome_payloads.pop("salvage_text", None)
        outcome_payloads["final_text"] = final_text
        outcome_payloads[STATE_KEY_FINALIZATION_STATUS] = (
            FinalizationStatus.model_validate(finalization_status)
        )
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

    def _repair_raw_tool_payload_final_text(
        self, normalized_final_text: str
    ) -> tuple[bool, AdaptiveToolLoopOutcome | None] | str | None:
        if not (
            normalized_final_text
            and _looks_like_unexecutable_tool_payload_text(normalized_final_text)
        ):
            return None
        fallback_text = tool_evidence_closeout_text(
            self.loop_state,
            reason=(
                "the model emitted raw tool markup after successful tool "
                "results, so preserved evidence is returned."
            ),
        )
        if _raw_tool_payload_retry_allowed(
            self.loop_state,
            text=normalized_final_text,
        ):
            return self._retry_with_system_message(
                "Your previous reply emitted raw tool markup, a raw tool-result "
                "JSON envelope, or an unexecutable tool envelope. Use existing "
                "tool results and return only the final plain-text answer.",
                discard_assistant_text=normalized_final_text,
            )
        if bool(self.loop_state.scratchpad.get(MUTATING_FILE_CLOSEOUT_KEY, False)):
            fallback_text = mutating_file_evidence_fallback_text(self.loop_state)
            if fallback_text:
                self.loop_state.scratchpad[
                    "mutating_file_closeout_used_evidence_fallback"
                ] = True
                return fallback_text
        if not fallback_text:
            return None
        self.loop_state.scratchpad["raw_tool_payload_used_evidence_fallback"] = True
        return fallback_text

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
        salvage_text = payloads["salvage_text"]
        confident_complete = payloads["confident_complete"]
        final_text = payloads["final_text"]
        normalized_final_text = str(final_text or "").strip()
        if getattr(prepared.response, "empty_payload_recovered", False) is True:
            retry_key = "empty_payload_recovery_retry_count"
            retry_count = int(self.loop_state.scratchpad.get(retry_key, 0) or 0)
            max_retries = self.loop_ctx.provider_retry_max_attempts - 1
            if retry_count < max_retries:
                self.loop_state.scratchpad[retry_key] = retry_count + 1
                return self._retry_with_system_message(
                    "The previous provider response contained no usable answer or "
                    "tool call. Continue from the structured context already "
                    "available and return a usable answer or canonical tool call.",
                    discard_assistant_text=normalized_final_text,
                )
            raise ProviderError(
                "Provider returned no usable response after configured retries",
                code="EMPTY_PROVIDER_RESPONSE",
            )
        self.loop_state.scratchpad.pop("empty_payload_recovery_retry_count", None)
        retry = _no_tool_retry(self, finalization_status, normalized_final_text)
        if retry is not None:
            return retry
        raw_payload_repair = self._repair_raw_tool_payload_final_text(
            normalized_final_text
        )
        if isinstance(raw_payload_repair, tuple):
            return raw_payload_repair
        if raw_payload_repair:
            final_text = raw_payload_repair
            normalized_final_text = raw_payload_repair
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
            return _requested_direct_tool_not_executed_outcome(self)
        if (
            requires_finalization_status
            and finalization_status is None
            and not normalized_final_text
            and _count_substantive_non_control_tool_results(self.loop_state) > 0
        ):
            recovered_answer = _recover_finalized_answer(
                loop_ctx=self.loop_ctx,
                profile=self.profile,
                loop_state=self.loop_state,
                runtime=self.runtime,
                model=self.model,
                max_output_tokens=self.max_output_tokens,
                metadata=self.metadata,
                public_mode_tag=self.public_mode_tag,
            )
            if recovered_answer is not None:
                recovered_status = recovered_answer.model_dump(
                    mode="python", exclude={"final_answer"}
                )
                return self._recovered_finalization_outcome(
                    prepared=prepared,
                    payloads=payloads,
                    final_text=recovered_answer.final_answer,
                    finalization_status=recovered_status,
                )
        empty_final_retry = _retry_empty_final_after_tool_results(
            self,
            finalization_status=finalization_status,
            final_text=final_text,
            normalized_final_text=normalized_final_text,
        )
        if empty_final_retry is not None:
            return empty_final_retry
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
            recovered_status = _recover_budget_finalization_status(
                loop_ctx=self.loop_ctx,
                profile=self.profile,
                loop_state=self.loop_state,
                runtime=self.runtime,
                model=self.model,
                max_output_tokens=self.max_output_tokens,
                metadata=self.metadata,
                final_text=normalized_final_text,
                public_mode_tag=self.public_mode_tag,
            )
            if recovered_status is not None:
                return self._recovered_finalization_outcome(
                    prepared=prepared,
                    payloads=payloads,
                    final_text=normalized_final_text,
                    finalization_status=recovered_status,
                )
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
        confident_retry = _retry_confident_complete_without_answer(
            self,
            confident_complete=confident_complete,
            final_text=final_text,
        )
        if confident_retry is not None:
            return confident_retry
        empty_typed_retry = _retry_empty_typed_finalization_after_tool_results(
            self,
            requires_finalization_status=requires_finalization_status,
            finalization_status=finalization_status,
            final_text=final_text,
            normalized_final_text=normalized_final_text,
        )
        if empty_typed_retry is not None:
            return empty_typed_retry
        if requires_finalization_status and finalization_status is None:
            if normalized_final_text and _successful_substantive_tool_results(
                self.loop_state
            ):
                finalization_status = FinalizationStatus(
                    status="incomplete",
                    reasoning=(
                        "The model-authored answer was preserved after typed "
                        "finalization recovery was exhausted."
                    ),
                    remaining_work=(
                        "Confirm the answer's completion status in a later turn."
                    ),
                )
                self.loop_state.scratchpad[
                    "typed_finalization_status_conservative_fallback"
                ] = True
            else:
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
