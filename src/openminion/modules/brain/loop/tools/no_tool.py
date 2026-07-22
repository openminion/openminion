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
from .postprocess.evidence_closeout import (
    MUTATING_FILE_CLOSEOUT_KEY,
    missing_requested_closeout_markers,
    mutating_file_evidence_fallback_text,
    tool_evidence_closeout_text,
)
from .iteration.helpers import (
    _MUTATING_FILE_TOOLS,
    _count_substantive_non_control_tool_results,
    _requires_typed_finalization_contract,
)
from .evidence import _loop_tool_result_payloads
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


def _final_text_from_successful_tool_evidence(loop_state: Any) -> str:
    return tool_evidence_closeout_text(
        loop_state,
        reason="the tool-backed work reached a finalization guard.",
    )


def _evidence_fallback_for_draft_final_text(loop_state: Any) -> str:
    fallback_final_text = _final_text_from_successful_tool_evidence(loop_state)
    if not fallback_final_text:
        return ""
    loop_state.scratchpad["pre_tool_draft_echo_used_evidence_fallback"] = True
    return fallback_final_text


def _retry_missing_requested_markers_after_tool_results(
    runner: Any,
    *,
    normalized_final_text: str,
) -> tuple[bool, None] | None:
    if _count_substantive_non_control_tool_results(runner.loop_state) <= 0:
        return None
    missing = missing_requested_closeout_markers(
        runner.loop_state,
        normalized_final_text,
    )
    if not missing:
        return None
    scratchpad = runner.loop_state.scratchpad
    if bool(scratchpad.get("missing_requested_closeout_markers_retry_used", False)):
        return None
    scratchpad["missing_requested_closeout_markers_retry_used"] = True
    rendered = ", ".join(f"{marker}:" for marker in missing)
    return runner._retry_with_system_message(
        "Your previous reply omitted requested final-output labels after "
        f"successful tool results. Return the final answer now with these labels: "
        f"{rendered}. Do not call more tools unless the evidence is genuinely "
        "insufficient.",
        discard_assistant_text=normalized_final_text,
    )


def _fallback_missing_requested_markers_after_retry(
    runner: Any,
    *,
    normalized_final_text: str,
) -> str:
    if not bool(
        runner.loop_state.scratchpad.get(
            "missing_requested_closeout_markers_retry_used",
            False,
        )
    ):
        return ""
    if not missing_requested_closeout_markers(
        runner.loop_state,
        normalized_final_text,
    ):
        return ""
    fallback = tool_evidence_closeout_text(
        runner.loop_state,
        reason=(
            "the model omitted requested final-output labels after successful "
            "tool results, so preserved evidence is returned."
        ),
    )
    if fallback:
        runner.loop_state.scratchpad[
            "missing_requested_closeout_markers_used_evidence_fallback"
        ] = True
    return fallback


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


def _fallback_missing_typed_finalization_after_tool_results(
    runner: Any,
    *,
    requires_finalization_status: bool,
    finalization_status: Any,
) -> str:
    if not requires_finalization_status or finalization_status is not None:
        return ""
    if _count_substantive_non_control_tool_results(runner.loop_state) <= 0:
        return ""
    if not bool(
        runner.loop_state.scratchpad.get("typed_finalization_status_retry_used", False)
    ):
        return ""
    fallback = tool_evidence_closeout_text(
        runner.loop_state,
        reason=(
            "the model omitted the required typed finalization contract after "
            "successful tool results, so preserved evidence is returned."
        ),
    )
    if fallback:
        runner.loop_state.scratchpad[
            "typed_finalization_contract_used_evidence_fallback"
        ] = True
    return fallback


def _typed_finalization_fallback_status(
    runner: Any,
    *,
    requires_finalization_status: bool,
    finalization_status: Any,
) -> tuple[str, FinalizationStatus] | None:
    fallback = _fallback_missing_typed_finalization_after_tool_results(
        runner,
        requires_finalization_status=requires_finalization_status,
        finalization_status=finalization_status,
    )
    if not fallback:
        return None
    return fallback, FinalizationStatus(
        status="final_answer",
        reasoning=(
            "Synthesized final answer from successful tool evidence after the "
            "model omitted typed finalization."
        ),
    )


def _retry_or_fail_internal_failure_final_text(
    runner: Any,
    *,
    normalized_final_text: str,
) -> tuple[bool, AdaptiveToolLoopOutcome | None] | None:
    if not (
        normalized_final_text
        and _is_internal_failure_final_text(normalized_final_text)
        and _count_substantive_non_control_tool_results(runner.loop_state) > 0
    ):
        return None
    retry_key = "provider_fallback_final_answer_retry_used"
    if not bool(runner.loop_state.scratchpad.get(retry_key, False)):
        runner.loop_state.scratchpad[retry_key] = True
        return runner._retry_with_system_message(
            "Your previous reply was a provider recovery/fallback message, "
            "not the actual final answer. Use the completed tool results "
            "already in context and return the final user-facing answer "
            "now. Do not say the response was empty or ask the user to "
            "retry unless you still lack evidence after using the existing "
            "tool results.",
            discard_assistant_text=normalized_final_text,
        )
    runner.loop_state.termination_reason = ADAPTIVE_TERM_LLM_ERROR
    emit_adaptive_status(
        runner.loop_ctx,
        profile=runner.profile,
        loop_state=runner.loop_state,
        detail_text=f"{runner.public_mode_tag} provider fallback final answer",
        mode_state="llm_error",
        termination_reason=ADAPTIVE_TERM_LLM_ERROR,
    )
    return False, AdaptiveToolLoopOutcome(
        profile_name=runner.profile.profile_name,
        mode_name=runner.profile.mode_name,
        termination_reason=ADAPTIVE_TERM_LLM_ERROR,
        state=runner.loop_state,
        allowed_tools=runner.allowed_tools,
        error_message="Model returned provider recovery text instead of a real final answer.",
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


def _user_requested_file_artifact_tooling(loop_state: Any) -> bool:
    user_text = "\n".join(
        str(getattr(message, "content", "") or "")
        for message in list(getattr(loop_state, "messages", []) or [])
        if str(getattr(message, "role", "") or "").strip().lower() == "user"
    ).lower()
    if not user_text:
        return False
    explicit_tooling = "file.write" in user_text or "do not only show code" in user_text
    artifact_intent = any(
        token in user_text
        for token in (
            "build",
            "create",
            "implement",
            "write",
            "project",
            "module",
            "readme",
            "files",
        )
    )
    return explicit_tooling and artifact_intent


def _has_successful_file_mutation(loop_state: Any) -> bool:
    return any(
        bool(item.get("ok"))
        and str(item.get("tool_name", "") or "").strip() in _MUTATING_FILE_TOOLS
        for item in _loop_tool_result_payloads(loop_state)
    )


def _has_file_mutation_attempt(loop_state: Any) -> bool:
    return any(
        str(item.get("tool_name", "") or "").strip() in _MUTATING_FILE_TOOLS
        for item in _loop_tool_result_payloads(loop_state)
    )


def _retry_snippet_only_file_artifact_answer(
    runner: Any,
    *,
    normalized_final_text: str,
) -> tuple[bool, AdaptiveToolLoopOutcome | None] | None:
    if _has_successful_file_mutation(runner.loop_state):
        return None
    if not _user_requested_file_artifact_tooling(runner.loop_state):
        return None
    if _has_file_mutation_attempt(runner.loop_state):
        return _requested_direct_tool_not_executed_outcome(runner)
    if not normalized_final_text:
        return None
    retry_key = "snippet_only_file_artifact_retry_used"
    if not bool(runner.loop_state.scratchpad.get(retry_key, False)):
        runner.loop_state.scratchpad[retry_key] = True
        return runner._retry_with_system_message(
            "Your previous reply described file contents instead of creating the "
            "requested file artifacts. Use file.write for the required files now. "
            "Do not only show code snippets, and do not claim files exist until "
            "the file.write tool succeeds.",
            discard_assistant_text=normalized_final_text,
        )
    return _requested_direct_tool_not_executed_outcome(runner)


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

    def _repair_raw_tool_payload_final_text(
        self, normalized_final_text: str
    ) -> tuple[bool, AdaptiveToolLoopOutcome | None] | str | None:
        if not (
            normalized_final_text
            and _looks_like_unexecutable_tool_payload_text(normalized_final_text)
        ):
            return None
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
        fallback_text = tool_evidence_closeout_text(
            self.loop_state,
            reason=(
                "the model emitted raw tool markup after successful tool "
                "results, so preserved evidence is returned."
            ),
        )
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
        final_text = payloads["final_text"]
        salvage_text = payloads["salvage_text"]
        confident_complete = payloads["confident_complete"]
        normalized_final_text = str(final_text or "").strip()
        internal_failure_retry = _retry_or_fail_internal_failure_final_text(
            self,
            normalized_final_text=normalized_final_text,
        )
        if internal_failure_retry is not None:
            return internal_failure_retry
        raw_payload_repair = self._repair_raw_tool_payload_final_text(
            normalized_final_text
        )
        if isinstance(raw_payload_repair, tuple):
            return raw_payload_repair
        if raw_payload_repair:
            final_text = raw_payload_repair
            normalized_final_text = raw_payload_repair
        snippet_only_retry = _retry_snippet_only_file_artifact_answer(
            self,
            normalized_final_text=normalized_final_text,
        )
        if snippet_only_retry is not None:
            return snippet_only_retry
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
        draft_like_final_text = bool(
            normalized_final_text
            and (
                _looks_like_pre_tool_draft_echo(
                    self.loop_state,
                    text=normalized_final_text,
                )
                or _looks_like_execution_preface_draft(normalized_final_text)
            )
            and _count_substantive_non_control_tool_results(self.loop_state) > 0
        )
        if draft_like_final_text and not bool(
            self.loop_state.scratchpad.get("pre_tool_draft_echo_retry_used", False)
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
        if draft_like_final_text:
            fallback_final_text = _evidence_fallback_for_draft_final_text(
                self.loop_state
            )
            if fallback_final_text:
                final_text = fallback_final_text
                normalized_final_text = fallback_final_text
        missing_marker_retry = _retry_missing_requested_markers_after_tool_results(
            self,
            normalized_final_text=normalized_final_text,
        )
        if missing_marker_retry is not None:
            return missing_marker_retry
        missing_marker_fallback = _fallback_missing_requested_markers_after_retry(
            self,
            normalized_final_text=normalized_final_text,
        )
        if missing_marker_fallback:
            final_text = missing_marker_fallback
            normalized_final_text = missing_marker_fallback
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
            return _requested_direct_tool_not_executed_outcome(self)
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
        typed_fallback = _typed_finalization_fallback_status(
            self,
            requires_finalization_status=requires_finalization_status,
            finalization_status=finalization_status,
        )
        if typed_fallback is not None:
            final_text, finalization_status = typed_fallback
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
