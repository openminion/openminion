"""Verification-flow helpers for the coding strategy handler."""

import json
from typing import Any

from openminion.modules.brain.constants import (
    BRAIN_ACTION_STATUS_SUCCESS,
    BRAIN_ACT_PROFILE_CODING,
    BRAIN_DECISION_ROUTE_ACT,
    CODING_PUBLIC_TAG as _CODING_PUBLIC_TAG,
)
from openminion.modules.brain.execution.loop_contracts import (
    ExecutionContext,
    ExecutionResult,
)
from openminion.modules.brain.loop.tools import (
    AdaptiveToolLoopOutcome,
    DirectToolTurnContext,
)
from openminion.modules.brain.schemas import ActionResult, Goal, ToolCommand
from openminion.modules.llm.schemas import Message

from .contracts import (
    CODING_ALLOWED_TOOLS,
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
    _VERIFIER_CANDIDATE_TOOLS = frozenset(
        {
            "exec.poll",
            "exec.run",
            "file.list_dir",
            "file.read",
            "file.read_range",
            "file.write",
        }
    )

    def _written_deliverable_id(self: Any, command: ToolCommand) -> str:
        if command.tool_name != "file.write" or self._coding_plan is None:
            return ""
        goal = getattr(self._coding_plan, "verifier_goal", None)
        path = str(command.args.get("path", "") or "").replace("\\", "/").rstrip("/")
        if goal is None or not path:
            return ""
        matches = [
            deliverable.deliverable_id
            for deliverable in goal.deliverables
            if path == deliverable.deliverable_id
            or path.endswith(f"/{deliverable.deliverable_id}")
        ]
        return matches[0] if len(matches) == 1 else ""

    def _sole_unbound_verification_target(
        self: Any,
        scratchpad: dict[str, Any],
    ) -> tuple[str, str] | None:
        if self._coding_plan is None or self._coding_plan.verifier_goal is None:
            return None
        goal = self._coding_plan.verifier_goal
        targets = [
            *(('criterion', item.criterion_id) for item in goal.success_criteria),
            *(('deliverable', item.deliverable_id) for item in goal.deliverables),
        ]
        bound = set(dict(scratchpad.get("coding.verifier_candidates", {}) or {}))
        remaining = [target for target in targets if f"{target[0]}:{target[1]}" not in bound]
        return remaining[0] if len(remaining) == 1 else None

    @staticmethod
    def _replaces_unresolved_failure(
        command: ToolCommand,
        failed_command: ToolCommand,
    ) -> bool:
        target = (
            str(command.verification_target_kind or "").strip(),
            str(command.verification_target_id or "").strip(),
        )
        failed_target = (
            str(failed_command.verification_target_kind or "").strip(),
            str(failed_command.verification_target_id or "").strip(),
        )
        return command.tool_name == failed_command.tool_name or (
            all(target) and target == failed_target
        )

    def _verification_targets(
        self: Any,
        ctx: ExecutionContext,
    ) -> dict[str, tuple[str, ...]]:
        goal, _source = self._resolve_verifier_goal(ctx)
        if goal is None:
            return {}
        return {
            "criterion": tuple(item.criterion_id for item in goal.success_criteria),
            "deliverable": tuple(item.deliverable_id for item in goal.deliverables),
        }

    def _verification_target_guidance(self: Any, ctx: ExecutionContext) -> str:
        targets = self._verification_targets(ctx)
        rendered = [
            f"{kind}:{target_id}"
            for kind in ("criterion", "deliverable")
            for target_id in targets.get(kind, ())
        ]
        if not rendered:
            return ""
        return (
            "Verification targets: "
            + ", ".join(rendered)
            + ". Bind each verification call to exactly one listed target."
        )

    def _bound_verifier_candidates(
        self: Any,
    ) -> tuple[tuple[ToolCommand, ActionResult], ...]:
        payloads = self._loop_state.scratchpad.get("coding.verifier_candidates", {})
        if not isinstance(payloads, dict):
            return ()
        return tuple(
            candidate
            for payload in payloads.values()
            if (candidate := load_verifier_candidate(payload)) is not None
        )

    def _with_mutation_artifacts(
        self: Any,
        candidates: tuple[tuple[ToolCommand, ActionResult], ...],
    ) -> tuple[tuple[ToolCommand, ActionResult], ...]:
        mutation_refs = self._successful_mutating_artifact_refs()
        if not mutation_refs:
            return candidates
        return tuple(
            (
                command,
                action_result.model_copy(
                    update={
                        "artifact_refs": list(
                            {
                                ref.ref: ref
                                for ref in [
                                    *action_result.artifact_refs,
                                    *mutation_refs,
                                ]
                            }.values()
                        )
                    },
                    deep=True,
                ),
            )
            for command, action_result in candidates
        )

    def _stage_required_write_direct_tool(self: Any) -> None:
        if getattr(self._loop_state, "direct_tool_turn", None) is not None:
            return
        requested_name = (
            "file.write" if "file.write" in CODING_ALLOWED_TOOLS else "code.patch"
        )
        self._loop_state.direct_tool_turn = DirectToolTurnContext(
            requested_tool_names=(requested_name,),
            requested_batch_signature="",
            match_by_name_only=True,
        )
        self._loop_state.scratchpad["coding.required_write_direct_tool"] = (
            requested_name
        )

    def _latest_tool_failure_summary(self: Any) -> str:
        unresolved = load_verifier_candidate(
            self._loop_state.scratchpad.get("coding.unresolved_verifier_failure")
        )
        if unresolved is not None:
            if self._has_mutation_after_latest_failure():
                return ""
            _command, action_result = unresolved
            return str(action_result.summary or "Verification failed.").strip()
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

    def _has_mutation_after_latest_failure(self: Any) -> bool:
        repaired = False
        for item in reversed(
            self._loop_state.scratchpad.get("adaptive.tool_results", []) or []
        ):
            if not isinstance(item, dict):
                continue
            if not bool(item.get("ok")):
                return repaired
            if str(item.get("tool_name", "") or "").strip() in {
                "code.patch",
                "file.write",
            }:
                repaired = True
        return False

    def _record_verifier_candidate(
        self: Any,
        command: Any,
        action_result: ActionResult,
        *,
        scratchpad: dict[str, Any] | None = None,
    ) -> None:
        if not isinstance(command, ToolCommand) or (
            str(command.tool_name or "").strip().lower()
            not in self._VERIFIER_CANDIDATE_TOOLS
        ):
            return
        scratchpad = self._loop_state.scratchpad if scratchpad is None else scratchpad
        tool_name = str(command.tool_name or "").strip().lower()
        if not command.verification_target_id:
            deliverable_id = self._written_deliverable_id(command)
            if deliverable_id:
                command = command.model_copy(
                    update={
                        "verification_target_kind": "deliverable",
                        "verification_target_id": deliverable_id,
                    },
                    deep=True,
                )
            elif target := self._sole_unbound_verification_target(scratchpad):
                command = command.model_copy(
                    update={
                        "verification_target_kind": target[0],
                        "verification_target_id": target[1],
                    },
                    deep=True,
                )
        execution_status = (
            str(action_result.outputs.get("status", "") or "").strip().lower()
        )
        session_id = str(action_result.outputs.get("session_id", "") or "").strip()
        pending_sessions = dict(
            scratchpad.get("coding.pending_verifier_sessions", {}) or {}
        )
        if tool_name in {"exec.poll", "exec.run"} and execution_status == "running":
            target_kind = str(command.verification_target_kind or "").strip()
            target_id = str(command.verification_target_id or "").strip()
            if tool_name == "exec.run" and session_id and target_kind and target_id:
                pending_sessions[session_id] = {
                    "verification_target_kind": target_kind,
                    "verification_target_id": target_id,
                }
                scratchpad["coding.pending_verifier_sessions"] = pending_sessions
            return
        if tool_name == "exec.poll":
            session_id = (
                session_id or str(command.args.get("session_id", "") or "").strip()
            )
            binding = pending_sessions.get(session_id)
            update = {
                "verification_target_kind": None,
                "verification_target_id": None,
            }
            if isinstance(binding, dict):
                update = {
                    "verification_target_kind": binding.get("verification_target_kind"),
                    "verification_target_id": binding.get("verification_target_id"),
                }
            command = command.model_copy(update=update, deep=True)
            if execution_status in {"exited", "killed"}:
                pending_sessions.pop(session_id, None)
                scratchpad["coding.pending_verifier_sessions"] = pending_sessions
        payload = serialize_verifier_candidate(
            command=command,
            action_result=action_result,
        )
        self._record_verifier_failure_state(
            command=command,
            action_result=action_result,
            payload=payload,
            scratchpad=scratchpad,
        )
        scratchpad["coding.last_verifier_candidate"] = payload
        self._last_verifier_candidate_payload = dict(payload)
        target_kind = str(command.verification_target_kind or "").strip()
        target_id = str(command.verification_target_id or "").strip()
        if target_kind and target_id:
            candidates = dict(scratchpad.get("coding.verifier_candidates", {}) or {})
            candidates[f"{target_kind}:{target_id}"] = payload
            scratchpad["coding.verifier_candidates"] = candidates

    def _record_verifier_failure_state(
        self: Any,
        *,
        command: ToolCommand,
        action_result: ActionResult,
        payload: dict[str, Any],
        scratchpad: dict[str, Any],
    ) -> None:
        correction_generation = int(scratchpad.get("coding.self_corrections", 0) or 0)
        if action_result.status != BRAIN_ACTION_STATUS_SUCCESS:
            raw_failure_generation = scratchpad.get(
                "coding.unresolved_verifier_failure_generation"
            )
            failure_generation = (
                int(raw_failure_generation)
                if raw_failure_generation is not None
                else -1
            )
            if (
                "coding.unresolved_verifier_failure" not in scratchpad
                or correction_generation > failure_generation
            ):
                scratchpad["coding.unresolved_verifier_failure"] = payload
                scratchpad["coding.unresolved_verifier_failure_generation"] = (
                    correction_generation
                )
        else:
            failure_generation = int(
                scratchpad.get(
                    "coding.unresolved_verifier_failure_generation",
                    correction_generation,
                )
                or 0
            )
            unresolved = load_verifier_candidate(
                scratchpad.get("coding.unresolved_verifier_failure")
            )
            if (
                correction_generation > failure_generation
                and unresolved is not None
                and self._replaces_unresolved_failure(command, unresolved[0])
            ):
                scratchpad.pop("coding.unresolved_verifier_failure", None)
                scratchpad.pop(
                    "coding.unresolved_verifier_failure_generation",
                    None,
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

    def _record_verifier_evaluation(
        self: Any,
        ctx: ExecutionContext,
        *,
        verdict: str,
        result_count: int,
        verifier_goal_id: str,
        goal_source: str,
    ) -> None:
        self._loop_state.scratchpad["coding.verifier_goal_id"] = verifier_goal_id
        self._loop_state.scratchpad["coding.verifier_verdict"] = verdict
        self._loop_state.scratchpad["coding.verifier_result_count"] = result_count
        self._emit_verifier_status(
            ctx,
            mode_state=verdict,
            detail_text=f"{_CODING_PUBLIC_TAG} verifier verdict: {verdict}",
            extra_payload={
                "coding.verifier_goal_id": verifier_goal_id,
                "coding.verifier_goal_source": goal_source,
                "coding.verifier_verdict": verdict,
                "coding.verifier_result_count": result_count,
            },
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
                        "No typed verifier goal was bound for the coding verify phase."
                    ),
                )
            return None

        candidates = self._bound_verifier_candidates()
        if not candidates:
            return self._exit_verification_unbound(
                ctx,
                allowed_tools=allowed_tools,
                reason=(
                    "No verification candidate was captured for the coding "
                    "verify phase."
                ),
            )
        candidates = self._with_mutation_artifacts(candidates)
        evaluation = evaluate_coding_verifier(
            goal=verifier_goal,
            candidates=candidates,
            state=ctx.state,
            logger=ctx.logger,
            budget_exhausted=_is_budget_exhausted(ctx, self._loop_state),
        )
        self._record_verifier_evaluation(
            ctx,
            verdict=evaluation.verdict,
            result_count=len(evaluation.results),
            verifier_goal_id=verifier_goal.goal_id,
            goal_source=goal_source,
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
        failed_reasons.extend(
            f"No bound verification evidence for {target}."
            for target in evaluation.missing_targets
        )
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
        if current_phase == "implement" and next_phase == "verify":
            if not self._prepare_verify_transition(ctx):
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

    def _prepare_verify_transition(self: Any, ctx: ExecutionContext) -> bool:
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
            self._record_autonomous_correction(ctx, failure_summary=failure_summary)
            instruction = (
                "Stay in implement. Fix this failure and run "
                f"exec.run again: {failure_summary}"
            )
        elif (
            self._coding_plan_requires_file_change()
            and not self._has_successful_mutating_file_result()
        ):
            failure_summary = (
                "Run a mutating implementation tool (`file.write` or `code.patch`) "
                "before verify."
            )
            self._coding_plan.record_open_issue(failure_summary)
            self._coding_plan.current_phase = "implement"
            self._stage_required_write_direct_tool()
            attempt = self._record_verify_gate_block(
                ctx,
                failure_summary=failure_summary,
                reason="missing_implementation_write",
                required_tool="file.write or code.patch",
            )
            correction_cap = max(1, int(getattr(self, "_max_self_corrections", 0) or 0))
            if attempt > correction_cap:
                self._loop_state.termination_reason = CODING_TERM_VERIFY_CAP_EXCEEDED
            instruction = (
                "Stay in implement and use a mutating implementation tool "
                "(`file.write` or `code.patch`) before moving to verify."
            )
        else:
            return True

        self._sync_plan_telemetry()
        if self._loop_state.termination_reason not in {
            "blocked_cap",
            CODING_TERM_VERIFY_CAP_EXCEEDED,
        }:
            self._reset_loop_for_continuation()
            self._loop_state.messages.append(Message(role="user", content=instruction))
        self._emit_phase_status(ctx)
        return False

    def _coding_plan_requires_file_change(self: Any) -> bool:
        plan_requires_change = getattr(self._coding_plan, "requires_file_change", False)
        return bool(
            plan_requires_change
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
