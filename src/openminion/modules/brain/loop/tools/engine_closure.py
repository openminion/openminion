# mypy: disable-error-code="attr-defined,has-type,no-any-return"

from __future__ import annotations

from typing import Any

from openminion.modules.brain.constants import (
    DUPLICATE_BATCH_RECOVERY_LIMIT as _DUPLICATE_BATCH_RECOVERY_LIMIT,
)

from .contracts import (
    ADAPTIVE_CLOSURE_ENGINE_SINGLE_PASS,
    ADAPTIVE_TERM_DUPLICATE_TOOL_CALLS,
    ADAPTIVE_TERM_FINAL_TEXT,
    AdaptiveToolLoopOutcome,
)
from .duplicate_batch import (
    _build_duplicate_batch_answer_only_closure_message,
    _duplicate_batch_recovery_message,
    _duplicate_batch_retry_counts,
    _eligible_duplicate_batch_execution_facts,
    _force_duplicate_batch_answer_only_closure,
)
from .status import emit_adaptive_status
from .telemetry import _emit_iteration_event


class AdaptiveLoopRunnerClosureMixin:
    def _finalize_answer_only_closure_outcome(
        self,
        *,
        outcome: AdaptiveToolLoopOutcome,
        llm_duration_ms: int,
        tokens_used: int,
    ) -> AdaptiveToolLoopOutcome:
        _emit_iteration_event(
            loop_ctx=self.loop_ctx,
            profile=self.profile,
            loop_state=self.loop_state,
            llm_duration_ms=llm_duration_ms,
            tool_records=[],
            tokens_used=tokens_used,
        )
        if (
            outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
            and self.profile.final_closure_policy == ADAPTIVE_CLOSURE_ENGINE_SINGLE_PASS
            and self.finalizer is not None
        ):
            outcome.mode_result = self.finalizer(outcome)
        return outcome

    def _maybe_force_direct_closure(
        self,
        *,
        prepared: Any,
        allowed_tool_calls: list[Any],
    ) -> AdaptiveToolLoopOutcome | None:
        del allowed_tool_calls
        closure_outcome, closure_duration_ms, closure_tokens_used = (
            self._force_direct_tool_answer_only_closure(
                loop_ctx=self.loop_ctx,
                profile=self.profile,
                loop_state=self.loop_state,
                runtime=self.runtime,
                model=self.model,
                tool_specs=self.active_tool_specs,
                max_output_tokens=self.max_output_tokens,
                metadata=self.metadata,
                allowed_tools=self.allowed_tools,
                public_mode_tag=self.public_mode_tag,
            )
        )
        if closure_outcome is None:
            return None
        return self._finalize_answer_only_closure_outcome(
            outcome=closure_outcome,
            llm_duration_ms=prepared.iter_llm_duration_ms + closure_duration_ms,
            tokens_used=(
                prepared.iter_input_tokens
                + prepared.iter_output_tokens
                + closure_tokens_used
            ),
        )

    def _handle_duplicate_signature(
        self,
        *,
        signature: str,
        tool_calls: list[Any],
        prepared: Any,
    ) -> tuple[bool, AdaptiveToolLoopOutcome | None]:
        if signature not in set(self.loop_state.seen_signatures):
            return False, None
        duplicate_retry_counts = _duplicate_batch_retry_counts(self.loop_state)
        duplicate_retry_count = int(duplicate_retry_counts.get(signature, 0) or 0)
        if duplicate_retry_count < _DUPLICATE_BATCH_RECOVERY_LIMIT:
            duplicate_retry_counts[signature] = duplicate_retry_count + 1
            eligible_facts = _eligible_duplicate_batch_execution_facts(
                self.loop_state,
                signature=signature,
            )
            repeated_tool_names = {
                str(getattr(item, "name", "") or "").strip()
                for item in tool_calls
                if str(getattr(item, "name", "") or "").strip()
            }
            has_alternative_tool = len(self.active_tool_names) <= 5 and bool(
                set(self.active_tool_names) - repeated_tool_names
            )
            if eligible_facts is not None and not has_alternative_tool:
                self.loop_state.scratchpad[
                    "duplicate_batch_answer_only_closure_pending"
                ] = True
                self.loop_state.messages.append(
                    _build_duplicate_batch_answer_only_closure_message(tool_calls)
                )
            else:
                self.loop_state.messages.append(
                    _duplicate_batch_recovery_message(tool_calls)
                )
            self.loop_state.scratchpad["loop.duplicate_signature_retries"] = (
                int(
                    self.loop_state.scratchpad.get(
                        "loop.duplicate_signature_retries", 0
                    )
                    or 0
                )
                + 1
            )
            emit_adaptive_status(
                self.loop_ctx,
                profile=self.profile,
                loop_state=self.loop_state,
                detail_text=f"{self.public_mode_tag} duplicate batch retry",
                mode_state="duplicate_tool_retry",
                extra={
                    "duplicate_signature_retry_count": duplicate_retry_count + 1,
                    "duplicate_tool_names": [
                        str(getattr(item, "name", "") or "").strip()
                        for item in tool_calls
                        if str(getattr(item, "name", "") or "").strip()
                    ],
                },
            )
            return True, None
        duplicate_outcome, duration_ms, duplicate_tokens = (
            _force_duplicate_batch_answer_only_closure(
                loop_ctx=self.loop_ctx,
                profile=self.profile,
                loop_state=self.loop_state,
                runtime=self.runtime,
                model=self.model,
                tool_calls=tool_calls,
                tool_specs=self.active_tool_specs,
                max_output_tokens=self.max_output_tokens,
                metadata=self.metadata,
                allowed_tools=self.allowed_tools,
                public_mode_tag=self.public_mode_tag,
                signature=signature,
            )
        )
        if duplicate_outcome is not None:
            return False, self._finalize_answer_only_closure_outcome(
                outcome=duplicate_outcome,
                llm_duration_ms=prepared.iter_llm_duration_ms + duration_ms,
                tokens_used=(
                    prepared.iter_input_tokens
                    + prepared.iter_output_tokens
                    + duplicate_tokens
                ),
            )
        self.loop_state.termination_reason = ADAPTIVE_TERM_DUPLICATE_TOOL_CALLS
        emit_adaptive_status(
            self.loop_ctx,
            profile=self.profile,
            loop_state=self.loop_state,
            detail_text=f"{self.public_mode_tag} repeated tool batch",
            mode_state="duplicate_tool_calls",
            termination_reason=ADAPTIVE_TERM_DUPLICATE_TOOL_CALLS,
        )
        return False, AdaptiveToolLoopOutcome(
            profile_name=self.profile.profile_name,
            mode_name=self.profile.mode_name,
            termination_reason=ADAPTIVE_TERM_DUPLICATE_TOOL_CALLS,
            state=self.loop_state,
            allowed_tools=self.allowed_tools,
        )
