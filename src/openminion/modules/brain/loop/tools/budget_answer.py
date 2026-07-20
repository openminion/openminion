from __future__ import annotations

from typing import Any

from openminion.base.constants import STATE_KEY_FINALIZATION_STATUS

from .budget_finalization import (
    _recover_budget_finalization_status,
    _reject_invalid_answer_only_final_text,
    _termination_reason_for_status,
)
from .contracts import (
    ADAPTIVE_TERM_BUDGET_EXHAUSTED,
    ADAPTIVE_TERM_FINAL_TEXT,
    ADAPTIVE_TERM_FINALIZATION_CONTRACT_MISSING,
    AdaptiveToolLoopContext,
    AdaptiveToolLoopOutcome,
    AdaptiveToolLoopProfile,
    AdaptiveToolLoopState,
)
from .postprocess.evidence_closeout import tool_evidence_closeout_outcome


def budget_evidence_outcome(
    *,
    profile: AdaptiveToolLoopProfile,
    loop_state: AdaptiveToolLoopState,
    allowed_tools: frozenset[str],
    reason: str,
) -> AdaptiveToolLoopOutcome | None:
    return tool_evidence_closeout_outcome(
        profile=profile,
        loop_state=loop_state,
        allowed_tools=allowed_tools,
        reason=reason,
        scratchpad_key="budget_used_evidence_fallback",
    )


def _recover_contract_outcome(
    *,
    loop_ctx: AdaptiveToolLoopContext,
    profile: AdaptiveToolLoopProfile,
    loop_state: AdaptiveToolLoopState,
    runtime: Any,
    model: str,
    max_output_tokens: int | None,
    metadata: dict[str, Any] | None,
    allowed_tools: frozenset[str],
    public_mode_tag: str,
    final_text: str,
    response: Any,
) -> AdaptiveToolLoopOutcome | None:
    if not final_text or list(getattr(response, "tool_calls", []) or []):
        return None
    recovered_status = _recover_budget_finalization_status(
        loop_ctx=loop_ctx,
        profile=profile,
        loop_state=loop_state,
        runtime=runtime,
        model=model,
        max_output_tokens=max_output_tokens,
        metadata=metadata,
        final_text=final_text,
        public_mode_tag=public_mode_tag,
    )
    if recovered_status is None:
        return None
    status = str(recovered_status.get("status", "") or "")
    loop_state.termination_reason = _termination_reason_for_status(status)
    return AdaptiveToolLoopOutcome(
        profile_name=profile.profile_name,
        mode_name=profile.mode_name,
        termination_reason=loop_state.termination_reason,
        state=loop_state,
        allowed_tools=allowed_tools,
        final_text=final_text,
        finalization_status=recovered_status,
    )


def _visible_text_outcome(
    *,
    profile: AdaptiveToolLoopProfile,
    loop_state: AdaptiveToolLoopState,
    allowed_tools: frozenset[str],
    final_text: str,
    finalization_status: dict[str, Any] | None,
) -> AdaptiveToolLoopOutcome:
    loop_state.termination_reason = (
        _termination_reason_for_status(str(finalization_status.get("status", "") or ""))
        if finalization_status is not None
        else ADAPTIVE_TERM_FINAL_TEXT
    )
    return AdaptiveToolLoopOutcome(
        profile_name=profile.profile_name,
        mode_name=profile.mode_name,
        termination_reason=loop_state.termination_reason,
        state=loop_state,
        allowed_tools=allowed_tools,
        final_text=final_text,
        finalization_status=finalization_status,
    )


def answer_only_final_text_outcome(
    *,
    loop_ctx: AdaptiveToolLoopContext,
    profile: AdaptiveToolLoopProfile,
    loop_state: AdaptiveToolLoopState,
    runtime: Any,
    model: str,
    max_output_tokens: int | None,
    metadata: dict[str, Any] | None,
    allowed_tools: frozenset[str],
    public_mode_tag: str,
    response: Any,
    final_text: str,
    finalization_status: dict[str, Any] | None,
    has_tool_evidence: bool,
    contract_requested: bool,
) -> AdaptiveToolLoopOutcome:
    has_contract = finalization_status is not None or (
        f"<{STATE_KEY_FINALIZATION_STATUS}>" in final_text
        and f"</{STATE_KEY_FINALIZATION_STATUS}>" in final_text
    )
    if contract_requested and not has_contract:
        recovered_outcome = _recover_contract_outcome(
            loop_ctx=loop_ctx,
            profile=profile,
            loop_state=loop_state,
            runtime=runtime,
            model=model,
            max_output_tokens=max_output_tokens,
            metadata=metadata,
            allowed_tools=allowed_tools,
            public_mode_tag=public_mode_tag,
            final_text=final_text,
            response=response,
        )
        if recovered_outcome is not None:
            return recovered_outcome
        loop_state.termination_reason = ADAPTIVE_TERM_FINALIZATION_CONTRACT_MISSING
        return AdaptiveToolLoopOutcome(
            profile_name=profile.profile_name,
            mode_name=profile.mode_name,
            termination_reason=ADAPTIVE_TERM_FINALIZATION_CONTRACT_MISSING,
            state=loop_state,
            allowed_tools=allowed_tools,
            error_message="This act turn required typed finalization_status contract.",
        )
    rejected_outcome = _reject_invalid_answer_only_final_text(
        final_text=final_text,
        response=response,
        profile=profile,
        loop_state=loop_state,
        allowed_tools=allowed_tools,
        has_tool_evidence=has_tool_evidence,
    )
    if rejected_outcome is not None:
        fallback_outcome = budget_evidence_outcome(
            profile=profile,
            loop_state=loop_state,
            allowed_tools=allowed_tools,
            reason=(
                "answer-only budget finalization returned non-user-facing text, "
                "so preserved tool evidence is returned."
            ),
        )
        return fallback_outcome or rejected_outcome
    if final_text and not list(getattr(response, "tool_calls", []) or []):
        return _visible_text_outcome(
            profile=profile,
            loop_state=loop_state,
            allowed_tools=allowed_tools,
            final_text=final_text,
            finalization_status=finalization_status,
        )
    loop_state.termination_reason = ADAPTIVE_TERM_BUDGET_EXHAUSTED
    if has_tool_evidence:
        fallback_outcome = budget_evidence_outcome(
            profile=profile,
            loop_state=loop_state,
            allowed_tools=allowed_tools,
            reason=(
                "answer-only budget finalization did not produce final text, "
                "so preserved tool evidence is returned."
            ),
        )
        if fallback_outcome is not None:
            return fallback_outcome
    return AdaptiveToolLoopOutcome(
        profile_name=profile.profile_name,
        mode_name=profile.mode_name,
        termination_reason=ADAPTIVE_TERM_BUDGET_EXHAUSTED,
        state=loop_state,
        allowed_tools=allowed_tools,
        error_message="Answer-only budget finalization did not produce final text.",
    )
