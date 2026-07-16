"""Verification-flow helpers for the coding strategy handler."""

import json
from typing import Any

from openminion.modules.brain.constants import (
    BRAIN_ACT_PROFILE_CODING,
    BRAIN_DECISION_ROUTE_ACT,
    CODING_PUBLIC_TAG as _CODING_PUBLIC_TAG,
)
from openminion.modules.brain.execution.loop_contracts import (
    ExecutionContext,
    ExecutionResult,
)
from openminion.modules.brain.loop.tools import AdaptiveToolLoopOutcome
from openminion.modules.brain.schemas import ActionResult, Goal, ToolCommand
from openminion.modules.llm.schemas import Message

from .contracts import (
    CODING_TERM_FINAL_TEXT,
    CODING_TERM_TOOL_FAILURE,
    CODING_TERM_VERIFY_CAP_EXCEEDED,
)
from .runtime import _build_error_result, _is_budget_exhausted
from .verification import (
    CODING_VERIFIER_VERDICT_BUDGET_EXHAUSTED,
    CODING_VERIFIER_VERDICT_COMPLETE,
    evaluate_coding_verifier,
    load_verifier_candidate,
    serialize_verifier_candidate,
)


class CodingVerificationMixin:
    _VERIFIER_CANDIDATE_TOOLS = frozenset({"exec.run", "file.read"})

    def _latest_tool_failure_summary(self: Any) -> str:
        for message in reversed(self._loop_state.messages):
            if message.role != "tool":
                continue
            try:
                payload = json.loads(str(message.content or ""))
            except json.JSONDecodeError:
                return ""
            if str(payload.get("status", "") or "").strip() != "success":
                summary = str(payload.get("summary", "") or "").strip()
                if summary:
                    return summary
                error = payload.get("error")
                if isinstance(error, dict):
                    return str(error.get("message", "") or "").strip()
            return ""
        return ""

    def _record_verifier_candidate(
        self: Any,
        command: Any,
        action_result: ActionResult,
    ) -> None:
        if not isinstance(command, ToolCommand):
            return
        if (
            str(command.tool_name or "").strip().lower()
            not in self._VERIFIER_CANDIDATE_TOOLS
        ):
            return
        self._loop_state.scratchpad["coding.last_verifier_candidate"] = (
            serialize_verifier_candidate(command=command, action_result=action_result)
        )
        self._last_verifier_candidate_payload = dict(
            self._loop_state.scratchpad["coding.last_verifier_candidate"]
        )

    def _resolve_verifier_goal(
        self: Any,
        ctx: ExecutionContext,
    ) -> tuple[Goal | None, str]:
        if (
            self._coding_plan is not None
            and self._coding_plan.verifier_goal is not None
        ):
            return self._coding_plan.verifier_goal, "coding_plan.verifier_goal"
        raw_goal = getattr(ctx.state, "goal", None)
        if isinstance(raw_goal, Goal):
            return raw_goal, "state.goal"
        if isinstance(raw_goal, dict):
            try:
                return Goal.model_validate(raw_goal), "state.goal"
            except Exception:
                return None, ""
        return None, ""

    def _verifier_failure_summary(self: Any, *, reasons: list[str]) -> str:
        compact = [
            str(reason or "").strip() for reason in reasons if str(reason or "").strip()
        ]
        if compact:
            return "Typed verifier did not confirm coding completion: " + "; ".join(
                compact[:3]
            )
        return "Typed verifier did not confirm coding completion."

    def _emit_verifier_status(
        self: Any,
        ctx: ExecutionContext,
        *,
        mode_state: str,
        detail_text: str,
        extra_payload: dict[str, Any],
    ) -> None:
        payload = {
            "act.profile": BRAIN_ACT_PROFILE_CODING,
            **extra_payload,
            **self._resume_marker_payload(ctx),
        }
        ctx.emit_status(
            source_phase="coding.verifier",
            detail_text=detail_text,
            mode=BRAIN_DECISION_ROUTE_ACT,
            mode_state=mode_state,
            payload=payload,
        )

    def _exit_verification_unbound(
        self: Any,
        ctx: ExecutionContext,
        *,
        allowed_tools: frozenset[str],
        reason: str,
    ) -> ExecutionResult:
        count = (
            int(
                self._loop_state.scratchpad.get("coding.verifier_unbound_count", 0) or 0
            )
            + 1
        )
        self._loop_state.scratchpad["coding.verifier_unbound_count"] = count
        self._loop_state.scratchpad["coding.verifier_verdict"] = "verification_unbound"
        self._loop_state.scratchpad["coding.verify_gate_reason"] = (
            "verification_unbound"
        )
        self._loop_state.scratchpad["coding.last_failure_summary"] = str(
            reason or ""
        ).strip()
        self._emit_verifier_status(
            ctx,
            mode_state="verification_unbound",
            detail_text=f"{_CODING_PUBLIC_TAG} verifier unavailable: {reason}",
            extra_payload={
                "coding.verifier_unbound_count": count,
                "coding.verify_gate_reason": "verification_unbound",
            },
        )
        return self._exit_autonomous_blocked(
            ctx,
            reason_code="verification_unbound",
            failure_summary=str(reason or "").strip(),
            allowed_tools=allowed_tools,
        )

    def _maybe_finalize_verify_phase_with_verifier(
        self: Any,
        ctx: ExecutionContext,
        *,
        outcome: AdaptiveToolLoopOutcome,
        allowed_tools: frozenset[str],
    ) -> ExecutionResult | None:
        if (
            outcome.termination_reason != CODING_TERM_FINAL_TEXT
            or self._coding_plan is None
            or self._coding_plan.current_phase != "verify"
            or self._coding_plan.next_phase_name() is not None
        ):
            return None

        verifier_goal, goal_source = self._resolve_verifier_goal(ctx)
        if verifier_goal is None:
            if self._coding_plan_requires_file_change():
                return self._exit_verification_unbound(
                    ctx,
                    allowed_tools=allowed_tools,
                    reason=(
                        "No typed verifier goal was bound for the coding verify "
                        "phase."
                    ),
                )
            return None

        candidate = load_verifier_candidate(
            self._loop_state.scratchpad.get("coding.last_verifier_candidate")
        )
        if candidate is None:
            return self._exit_verification_unbound(
                ctx,
                allowed_tools=allowed_tools,
                reason=(
                    "No verification candidate was captured for the coding "
                    "verify phase."
                ),
            )
        command, action_result = candidate
        evaluation = evaluate_coding_verifier(
            goal=verifier_goal,
            command=command,
            action_result=action_result,
            state=ctx.state,
            logger=ctx.logger,
            budget_exhausted=_is_budget_exhausted(ctx, self._loop_state),
        )
        self._loop_state.scratchpad["coding.verifier_goal_id"] = verifier_goal.goal_id
        self._loop_state.scratchpad["coding.verifier_verdict"] = evaluation.verdict
        self._loop_state.scratchpad["coding.verifier_result_count"] = len(
            evaluation.results
        )
        self._emit_verifier_status(
            ctx,
            mode_state=evaluation.verdict,
            detail_text=(
                f"{_CODING_PUBLIC_TAG} verifier verdict: {evaluation.verdict}"
            ),
            extra_payload={
                "coding.verifier_goal_id": verifier_goal.goal_id,
                "coding.verifier_goal_source": goal_source,
                "coding.verifier_verdict": evaluation.verdict,
                "coding.verifier_result_count": len(evaluation.results),
            },
        )
        if evaluation.verdict == CODING_VERIFIER_VERDICT_COMPLETE:
            return None
        if evaluation.verdict == CODING_VERIFIER_VERDICT_BUDGET_EXHAUSTED:
            return self._exit_budget_exhausted(
                ctx,
                self._loop_state,
                allowed_tools,
            )

        failed_reasons = [
            reason
            for result in evaluation.results
            if not result.passed
            for reason in list(result.reasons)
        ]
        failure_summary = self._verifier_failure_summary(reasons=failed_reasons)
        synthetic_outcome = outcome.__class__(
            profile_name=outcome.profile_name,
            mode_name=outcome.mode_name,
            termination_reason=CODING_TERM_TOOL_FAILURE,
            state=outcome.state,
            allowed_tools=outcome.allowed_tools,
            final_text=outcome.final_text,
            action_result=_build_error_result(
                failure_summary,
                "coding_verifier_incomplete",
            ),
            error_message=failure_summary,
        )
        return self._result_from_outcome(
            ctx,
            outcome=synthetic_outcome,
            allowed_tools=allowed_tools,
        )

    def _advance_plan_after_phase(
        self: Any,
        ctx: ExecutionContext,
        *,
        outcome: AdaptiveToolLoopOutcome,
    ) -> bool:
        if self._coding_plan is None:
            return True
        current_phase = self._coding_plan.current_phase
        next_phase = self._coding_plan.next_phase_name()
        verifier_goal_bound = self._coding_plan.verifier_goal is not None
        if current_phase == "implement" and next_phase == "verify":
            failure_summary = self._latest_tool_failure_summary()
            if failure_summary:
                attempted = int(
                    self._loop_state.scratchpad.get("coding.self_corrections", 0) or 0
                )
                if attempted >= self._max_self_corrections:
                    self._loop_state.termination_reason = "blocked_cap"
                    self._sync_plan_telemetry()
                    self._emit_phase_status(ctx)
                    return False
                self._coding_plan.record_open_issue(failure_summary)
                self._record_autonomous_correction(
                    ctx,
                    failure_summary=failure_summary,
                )
                self._sync_plan_telemetry()
                self._loop_state.messages.append(
                    Message(
                        role="user",
                        content=(
                            "Stay in implement. Fix this failure and run "
                            f"exec.run again: {failure_summary}"
                        ),
                    )
                )
                self._emit_phase_status(ctx)
                return False
            if (
                self._coding_plan_requires_file_change()
                and not self._has_successful_mutating_file_result()
            ):
                failure_summary = (
                    "Run a mutating implementation tool (`file.write` or "
                    "`code.patch`) before verify."
                )
                self._coding_plan.record_open_issue(failure_summary)
                attempt = self._record_verify_gate_block(
                    ctx,
                    failure_summary=failure_summary,
                    reason="missing_implementation_write",
                    required_tool="file.write or code.patch",
                )
                if attempt >= self._max_self_corrections:
                    self._loop_state.termination_reason = (
                        CODING_TERM_VERIFY_CAP_EXCEEDED
                    )
                    self._sync_plan_telemetry()
                    self._emit_phase_status(ctx)
                    return False
                self._sync_plan_telemetry()
                self._loop_state.messages.append(
                    Message(
                        role="user",
                        content=(
                            "Stay in implement and use a mutating implementation "
                            "tool (`file.write` or `code.patch`) before moving "
                            "to verify."
                        ),
                    )
                )
                self._emit_phase_status(ctx)
                return False
            candidate_payload = self._loop_state.scratchpad.get(
                "coding.last_verifier_candidate"
            )
            if verifier_goal_bound and not isinstance(candidate_payload, dict):
                failure_summary = (
                    "Run at least one verification readback step (`file.read` or "
                    "`exec.run`) before verify."
                )
                self._coding_plan.record_open_issue(failure_summary)
                attempt = self._record_verify_gate_block(
                    ctx,
                    failure_summary=failure_summary,
                )
                if attempt >= self._max_self_corrections:
                    self._loop_state.termination_reason = (
                        CODING_TERM_VERIFY_CAP_EXCEEDED
                    )
                    self._sync_plan_telemetry()
                    self._emit_phase_status(ctx)
                    return False
                self._sync_plan_telemetry()
                self._loop_state.messages.append(
                    Message(
                        role="user",
                        content=(
                            "Stay in implement and run at least one verification "
                            "readback step (`file.read` or `exec.run`) before "
                            "moving to verify."
                        ),
                    )
                )
                self._emit_phase_status(ctx)
                return False

        current_output = outcome.final_text or ""
        advanced = self._coding_plan.advance_to_next_phase(output=current_output)
        executed = list(
            self._loop_state.scratchpad.get("coding.plan_phases_executed", []) or []
        )
        if self._coding_plan.current_phase not in executed:
            executed.append(self._coding_plan.current_phase)
        self._loop_state.scratchpad["coding.plan_phases_executed"] = executed
        self._sync_plan_telemetry()
        self._emit_phase_status(ctx)
        return advanced

    def _coding_plan_requires_file_change(self: Any) -> bool:
        return bool(
            getattr(self._coding_plan, "requires_file_change", False)
            or self._loop_state.scratchpad.get("coding.requires_file_change")
        )

    def _record_verify_gate_block(
        self: Any,
        ctx: ExecutionContext,
        *,
        failure_summary: str,
        reason: str = "missing_exec_run",
        required_tool: str = "exec.run",
    ) -> int:
        count = (
            int(self._loop_state.scratchpad.get("coding.verify_gate_blocks", 0) or 0)
            + 1
        )
        self._loop_state.scratchpad["coding.verify_gate_blocks"] = count
        self._loop_state.scratchpad["coding.verify_gate_reason"] = reason
        self._loop_state.scratchpad["coding.verify_gate_required_tool"] = required_tool
        self._loop_state.scratchpad["coding.last_failure_summary"] = str(
            failure_summary or ""
        ).strip()
        ctx.emit_status(
            source_phase="coding.verify_gate",
            detail_text=(
                f"{_CODING_PUBLIC_TAG} verify gate awaiting {required_tool}: "
                f"attempt {count}/{self._max_self_corrections}"
            ),
            mode=BRAIN_DECISION_ROUTE_ACT,
            mode_state="verify_gate_blocked",
            payload={
                "act.profile": BRAIN_ACT_PROFILE_CODING,
                "coding.verify_gate_blocks": count,
                "coding.verify_gate_required_tool": required_tool,
                "coding.verify_gate_reason": reason,
                **self._resume_marker_payload(ctx),
            },
        )
        return count

    def _record_autonomous_correction(
        self: Any,
        ctx: ExecutionContext,
        *,
        failure_summary: str,
    ) -> None:
        self._loop_state.seen_signatures = []
        self._loop_state.termination_reason = ""
        self._loop_state.scratchpad["coding.self_corrections"] = (
            int(self._loop_state.scratchpad.get("coding.self_corrections", 0) or 0) + 1
        )
        self._loop_state.scratchpad["coding.autonomous_iterations"] = (
            int(self._loop_state.scratchpad.get("coding.autonomous_iterations", 0) or 0)
            + 1
        )
        self._loop_state.scratchpad["coding.last_failure_summary"] = str(
            failure_summary or ""
        ).strip()
        self._loop_state.scratchpad["coding.pending_continue"] = True
        attempt = int(
            self._loop_state.scratchpad.get("coding.self_corrections", 0) or 0
        )
        ctx.emit_status(
            source_phase="coding.autonomy",
            detail_text=(
                f"{_CODING_PUBLIC_TAG} self-correcting: attempt {attempt}/"
                f"{self._max_self_corrections}"
            ),
            mode=BRAIN_DECISION_ROUTE_ACT,
            mode_state="self_correcting",
            payload={
                "act.profile": BRAIN_ACT_PROFILE_CODING,
                "coding.self_corrections": attempt,
                "coding.autonomous_iterations": int(
                    self._loop_state.scratchpad.get("coding.autonomous_iterations", 0)
                    or 0
                ),
                "coding.failure_summary": str(failure_summary or "").strip(),
                **self._resume_marker_payload(ctx),
            },
        )
