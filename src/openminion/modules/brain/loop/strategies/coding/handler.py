from typing import Any

from openminion.modules.brain.checkpoint import SimpleCheckpointMixin
from openminion.modules.brain.loop.tools import (
    ADAPTIVE_CLOSURE_MODE_OWNED,
    AdaptiveToolLoopOutcome,
    AdaptiveToolLoopProfile,
    AdaptiveToolLoopState,
    build_loop_thinking_metadata,
    run_adaptive_tool_loop,
)
from openminion.modules.brain.constants import (
    BRAIN_ACT_PROFILE_CODING,
    BRAIN_DECISION_ROUTE_ACT,
    BRAIN_INTERNAL_MODE_ACT_CODING,
    BRAIN_STATE_DONE,
    BRAIN_STATE_ERROR,
    CODING_PUBLIC_TAG as _CODING_PUBLIC_TAG,
    STATE_KEY_TASK_BACKED_RESUME,
)
from openminion.modules.brain.config import (
    CODING_MAX_ITERATIONS,
    CODING_MAX_SELF_CORRECTIONS,
)
from openminion.modules.brain.execution.loop_contracts import (
    ExecutionContext,
    ExecutionResult,
)
from openminion.modules.brain.execution.dispatch import invoke_decision_direct
from openminion.modules.brain.execution.preflight import ModePreparation
from openminion.modules.brain.execution.workflow import (
    StepJudgment,
    StepResult,
    WorkflowMode,
    WorkflowPlan,
    WorkflowStep,
)
from openminion.modules.brain.runtime.budget.strategy import (
    resolve_coding_budget_settings,
)
from openminion.modules.llm.schemas import Message

from .contracts import (
    CODING_ALLOWED_TOOLS,
    CODING_TERM_FINAL_TEXT,
    CODING_TERM_VERIFY_CAP_EXCEEDED,
    CodingRuntimeUnavailableError,
)
from .context_adapter import _CodingLoopContextAdapter
from .llm import DefaultCodingLLMRuntime
from .loop_state import CodingLoopState
from .plan import CodingPlan, coding_plan_from_payload
from .planning_flow import CodingPlanningMixin
from .reserves import CodingReserveMixin
from .resume import CodingResumeMixin
from .runtime import (
    _build_blocked_result,
    _build_error_result,
    _build_tool_specs,
    _coding_mode_config_from_context,
    _is_budget_exhausted,  # noqa: F401 - compatibility re-export for tests
    _resolve_model,
    _runner_and_profile_from_context,
)
from . import subtasks as _subtasks_module
from .results import (
    _exit_autonomous_blocked as _results_exit_autonomous_blocked,
    _exit_budget_exhausted as _results_exit_budget_exhausted,
    _exit_continue as _results_exit_continue,
    _exit_final_text as _results_exit_final_text,
    _maybe_continue_after_tool_failure as _results_maybe_continue_after_tool_failure,
    _result_from_outcome as _results_from_outcome,
)
from .subtasks import _dispatch_subtasks_if_needed as _subtasks_dispatch_if_needed
from .verification_flow import CodingVerificationMixin


def _configured_coding_profile_runner(ctx: ExecutionContext) -> "CodingProfileRunner":
    runner, profile = _runner_and_profile_from_context(ctx)
    helper = CodingProfileRunner()
    config = _coding_mode_config_from_context(ctx)
    if config is not None:
        helper.apply_mode_config(config=config, runner=runner, profile=profile)
    return helper


def prepare_coding_profile(
    ctx: ExecutionContext,
    *,
    emit_status_updates: bool = False,
) -> ModePreparation:
    return _configured_coding_profile_runner(ctx).prepare(
        ctx,
        emit_status_updates=emit_status_updates,
    )


def execute_coding_profile(ctx: ExecutionContext) -> ExecutionResult:
    return _configured_coding_profile_runner(ctx).execute(ctx)


def _first_non_empty_string(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _context_workspace_hint(ctx: ExecutionContext) -> str:
    state = getattr(ctx, "state", None)
    decision = getattr(ctx, "decision", None)
    options = getattr(ctx, "options", None)
    return _first_non_empty_string(
        getattr(ctx, "workspace_root", None),
        getattr(ctx, "cwd", None),
        getattr(ctx, "workdir", None),
        getattr(state, "workspace_root", None),
        getattr(state, "cwd", None),
        getattr(state, "workdir", None),
        getattr(state, "working_directory", None),
        getattr(decision, "workspace_root", None),
        getattr(decision, "cwd", None),
        getattr(decision, "workdir", None),
        getattr(options, "workspace_root", None),
        getattr(options, "cwd", None),
        getattr(options, "workdir", None),
    )


class CodingProfileRunner(
    CodingReserveMixin,
    CodingResumeMixin,
    CodingPlanningMixin,
    CodingVerificationMixin,
    SimpleCheckpointMixin,
):
    CHECKPOINT_VERSION = 1
    mode_name = BRAIN_INTERNAL_MODE_ACT_CODING

    def __init__(self) -> None:
        self._loop_state = CodingLoopState()
        self._coding_plan: CodingPlan | None = None
        self._max_iterations = CODING_MAX_ITERATIONS
        self._max_self_corrections = CODING_MAX_SELF_CORRECTIONS
        self._resume_count = 0
        self._last_checkpoint_id: str | None = None
        self._resume_prepared = False
        self._last_verifier_candidate_payload: dict[str, Any] | None = None

    def apply_mode_config(self, *, config, runner, profile) -> None:
        del runner, profile
        settings = resolve_coding_budget_settings(
            config=config,
            default_max_adaptive_iterations=CODING_MAX_ITERATIONS,
            default_max_self_corrections=CODING_MAX_SELF_CORRECTIONS,
        )
        self._max_iterations = settings.max_adaptive_iterations
        self._max_self_corrections = settings.max_self_corrections

    def initialize(self, ctx: ExecutionContext) -> WorkflowPlan:
        del ctx
        return WorkflowPlan(steps=[BRAIN_INTERNAL_MODE_ACT_CODING], cursor=0)

    def execute_step(self, ctx: ExecutionContext, step: WorkflowStep) -> StepResult:
        return StepResult(
            step=step,
            mode_result=self._execute_coding_loop(ctx),
        )

    def judge_step(
        self,
        ctx: ExecutionContext,
        step: WorkflowStep,
        result: StepResult,
    ) -> StepJudgment:
        del ctx, step
        return StepJudgment(
            disposition="close",
            mode_result=result.mode_result,
        )

    def finalize(self, ctx: ExecutionContext) -> ExecutionResult:
        return ExecutionResult.from_step_output(
            ctx.respond(
                message=f"{_CODING_PUBLIC_TAG} no pending work.",
                status=BRAIN_STATE_DONE,
            )
        )

    def resume(
        self,
        ctx: ExecutionContext,
        checkpoint_id: str | None = None,
    ) -> WorkflowPlan | dict[str, Any] | None:
        if checkpoint_id is not None:
            payload = SimpleCheckpointMixin.resume(self, ctx, checkpoint_id)
            module_payload = self._coding_module_state_payload(ctx)
            if module_payload and not payload:
                payload = dict(module_payload)
            if payload:
                return self._prepare_resume_state(
                    ctx,
                    payload=dict(payload),
                    checkpoint_id=checkpoint_id,
                )
            return payload

        resume_state = {
            key: value
            for key, value in dict(
                getattr(ctx.state, STATE_KEY_TASK_BACKED_RESUME, {}) or {}
            ).items()
            if not key.startswith("_checkpoint_")
        }
        if not resume_state:
            resume_state = self._coding_module_state_payload(ctx)
        if not resume_state:
            return None
        self._prepare_resume_state(
            ctx,
            payload=dict(resume_state),
            checkpoint_id=getattr(ctx.state, "task_backed_checkpoint_id", None),
        )
        return self.initialize(ctx)

    def cancel(self, ctx: ExecutionContext, reason: str) -> ExecutionResult:
        self._clear_coding_module_state(ctx)
        self._coding_plan = None
        self._loop_state = CodingLoopState()
        return super().cancel(
            ctx,
            reason=reason or "Coding task cancelled.",
        )

    def prepare(  # type: ignore[override]
        self,
        ctx: ExecutionContext,
        *,
        emit_status_updates: bool = False,
    ) -> ModePreparation:
        del emit_status_updates
        try:
            DefaultCodingLLMRuntime.from_adapter(ctx.llm_adapter)
        except CodingRuntimeUnavailableError as exc:
            ctx.emit_status(
                source_phase="coding.prepare",
                detail_text=f"{_CODING_PUBLIC_TAG} raw LLM runtime unavailable",
                mode=BRAIN_DECISION_ROUTE_ACT,
                mode_state="prepare_failed",
                payload={
                    "act.profile": BRAIN_ACT_PROFILE_CODING,
                },
            )
            return ModePreparation(
                mode_result=ExecutionResult(
                    status=BRAIN_STATE_ERROR,
                    working_state=ctx.state,
                    message=str(exc),
                    action_result=_build_error_result(
                        str(exc), "coding_runtime_unavailable"
                    ),
                ),
                consume_user_input_for_command=False,
            )

        ctx.emit_status(
            source_phase="coding.prepare",
            detail_text=f"{_CODING_PUBLIC_TAG} started",
            mode=BRAIN_DECISION_ROUTE_ACT,
            mode_state="prepare",
            payload={
                "act.profile": BRAIN_ACT_PROFILE_CODING,
                "act.allowed_tools": sorted(CODING_ALLOWED_TOOLS),
            },
        )
        return ModePreparation(
            mode_result=None,
            consume_user_input_for_command=False,
        )

    def execute(self, ctx: ExecutionContext) -> ExecutionResult:
        return self._execute_coding_loop(ctx)

    def _execute_coding_loop(self, ctx: ExecutionContext) -> ExecutionResult:
        try:
            runtime = DefaultCodingLLMRuntime.from_adapter(ctx.llm_adapter)
        except CodingRuntimeUnavailableError as exc:
            return ExecutionResult(
                status=BRAIN_STATE_ERROR,
                working_state=ctx.state,
                message=str(exc),
                action_result=_build_error_result(
                    str(exc), "coding_runtime_unavailable"
                ),
            )

        allowed_tools = CODING_ALLOWED_TOOLS
        model = _resolve_model(ctx)
        prepared = self._prepare_execution_state(
            ctx,
            runtime=runtime,
            model=model,
        )
        if isinstance(prepared, ExecutionResult):
            return prepared
        tool_specs, seed_response = prepared

        while True:
            self._sync_plan_telemetry()
            self._dispatch_subtasks_if_needed(ctx)
            loop = self._as_adaptive_state(self._loop_state)
            iteration_allowed_tools = self._allowed_tools_for_current_phase(
                default_allowed_tools=allowed_tools
            )
            required_write_tool = str(
                loop.scratchpad.get("coding.required_write_direct_tool", "") or ""
            ).strip()
            if required_write_tool:
                iteration_allowed_tools = frozenset({required_write_tool})
            iteration_tool_specs = tool_specs
            iteration_tool_choice: str | dict[str, Any] = "auto"
            if bool(
                self._loop_state.scratchpad.get("coding.final_answer_reserve_used")
            ):
                iteration_allowed_tools = frozenset()
                iteration_tool_specs = []
                iteration_tool_choice = "none"
            elif bool(
                self._loop_state.scratchpad.get("coding.verification_reserve_used")
            ):
                iteration_allowed_tools = self._verification_reserve_allowed_tools()
                iteration_tool_specs = _build_tool_specs(
                    iteration_allowed_tools,
                    ctx=ctx,
                )
            elif iteration_allowed_tools != allowed_tools:
                iteration_tool_specs = _build_tool_specs(
                    iteration_allowed_tools,
                    ctx=ctx,
                )
            profile = AdaptiveToolLoopProfile(
                profile_name="coding_v1",
                mode_name=BRAIN_INTERNAL_MODE_ACT_CODING,
                allowed_tools=iteration_allowed_tools,
                provider_parallel_tool_capacity=2,
                max_iterations=self._max_iterations,
                reflection_policy="never",
                max_macro_corrections=3,
                macro_correction_cooldown=2,
                reflection_model=None,
                allow_llm_recovery_after_tool_failure=True,
                tool_choice=iteration_tool_choice,
                llm_request_overrides={
                    "metadata": build_loop_thinking_metadata(ctx, purpose="act")
                },
                final_closure_policy=ADAPTIVE_CLOSURE_MODE_OWNED,
            )
            outcome = run_adaptive_tool_loop(
                _CodingLoopContextAdapter(
                    ctx,
                    on_command_result=self._record_verifier_candidate,
                ),
                profile=profile,
                runtime=runtime,
                model=model,
                initial_messages=list(loop.messages),
                initial_state=loop,
                tool_specs=iteration_tool_specs,
                on_tool_result=lambda adaptive_state: self._checkpoint_loop_state(
                    ctx,
                    adaptive_state=adaptive_state,
                ),
                seed_response=seed_response,
            )
            seed_response = None
            result = self._handle_iteration_outcome(
                ctx,
                outcome=outcome,
                allowed_tools=allowed_tools,
            )
            if result is not None:
                return result

    def _prepare_execution_state(
        self,
        ctx: ExecutionContext,
        *,
        runtime: DefaultCodingLLMRuntime,
        model: str,
    ) -> tuple[list[Any], Any | None] | ExecutionResult:
        tool_specs = _build_tool_specs(CODING_ALLOWED_TOOLS, ctx=ctx)
        self._init_checkpoint(ctx)
        seed_response: Any | None = getattr(ctx.decision, "_entry_response", None)
        resume_state = {
            key: value
            for key, value in dict(
                getattr(ctx.state, STATE_KEY_TASK_BACKED_RESUME, {}) or {}
            ).items()
            if not key.startswith("_checkpoint_")
        }
        if not resume_state:
            resume_state = self._coding_module_state_payload(ctx)
        if resume_state:
            if self._resume_prepared:
                self.restore_state(dict(resume_state))
            else:
                resume_state = self._prepare_resume_state(
                    ctx,
                    payload=dict(resume_state),
                    checkpoint_id=getattr(ctx.state, "task_backed_checkpoint_id", None),
                )
            had_pending_confirmation = (
                ctx.state.pending_confirmation_command is not None
            )
            confirmation_result = self._consume_pending_confirmation_reply(ctx)
            if confirmation_result is not None:
                return confirmation_result
            if (
                not had_pending_confirmation
                and ctx.state.pending_confirmation_command is None
            ):
                self._apply_resume_input(ctx)
            if self._coding_plan is None:
                self._coding_plan = CodingPlan.fallback(
                    str(ctx.state.goal or ctx.user_input or "")
                )
            self._sync_coding_context(ctx)
        else:
            self._loop_state = CodingLoopState()
            self._last_verifier_candidate_payload = None
            if ctx.user_input:
                self._loop_state.messages.append(
                    Message(role="user", content=ctx.user_input)
                )
            if seed_response is not None:
                goal = (
                    str(
                        ctx.user_input
                        or ctx.state.goal
                        or getattr(ctx.decision, "objective", "")
                        or ""
                    ).strip()
                    or "Complete the coding task."
                )
                self._coding_plan = CodingPlan.fallback(goal)
                self._apply_plan_to_scratchpad(self._coding_plan)
            else:
                self._coding_plan, seed_response = self._initialize_plan(
                    ctx,
                    runtime=runtime,
                    model=model,
                )
            self._sync_coding_context(ctx)
            self._sync_coding_module_state(ctx)
        seeded_replay_result = self._consume_seeded_confirmation_replay(ctx)
        if seeded_replay_result is not None:
            return seeded_replay_result
        if self._coding_plan is not None:
            self._emit_phase_status(ctx)
        return tool_specs, seed_response

    def _handle_iteration_outcome(
        self,
        ctx: ExecutionContext,
        *,
        outcome: AdaptiveToolLoopOutcome,
        allowed_tools: frozenset[str],
    ) -> ExecutionResult | None:
        self._sync_loop_state(outcome.state)
        if self._has_successful_mutating_file_result():
            self._loop_state.scratchpad.pop("coding.required_write_direct_tool", None)
        if self._last_verifier_candidate_payload is not None:
            self._loop_state.scratchpad["coding.last_verifier_candidate"] = dict(
                self._last_verifier_candidate_payload
            )
        self._sync_coding_module_state(ctx)

        if outcome.termination_reason != CODING_TERM_FINAL_TEXT or self._coding_plan is None:
            if self._maybe_continue_with_verify_closeout_reserve(ctx, outcome=outcome):
                self._sync_coding_module_state(ctx)
                return None
            if self._maybe_continue_with_final_answer_reserve(ctx, outcome=outcome):
                self._sync_coding_module_state(ctx)
                return None
            if self._maybe_continue_with_verification_reserve(ctx, outcome=outcome):
                self._sync_coding_module_state(ctx)
                return None
            return self._result_from_outcome(
                ctx,
                outcome=outcome,
                allowed_tools=allowed_tools,
            )
        if self._coding_plan.next_phase_name() is None:
            if self._maybe_continue_with_final_answer_reserve(ctx, outcome=outcome):
                self._sync_coding_module_state(ctx)
                return None
            verifier_result = self._maybe_finalize_verify_phase_with_verifier(
                ctx,
                outcome=outcome,
                allowed_tools=allowed_tools,
            )
            if verifier_result is not None:
                return verifier_result
            return self._result_from_outcome(
                ctx,
                outcome=outcome,
                allowed_tools=allowed_tools,
            )

        if not self._advance_plan_after_phase(ctx, outcome=outcome):
            self._sync_coding_module_state(ctx)
            if self._loop_state.termination_reason == CODING_TERM_VERIFY_CAP_EXCEEDED:
                return self._exit_autonomous_blocked(
                    ctx,
                    reason_code=CODING_TERM_VERIFY_CAP_EXCEEDED,
                    failure_summary=str(
                        self._loop_state.scratchpad.get(
                            "coding.last_failure_summary",
                            "Verify gate did not observe exec.run.",
                        )
                        or "Verify gate did not observe exec.run."
                    ),
                    allowed_tools=allowed_tools,
                )
            if self._loop_state.termination_reason in {
                "blocked_cap",
                "blocked_novel_failure",
            }:
                return self._exit_autonomous_blocked(
                    ctx,
                    reason_code=self._loop_state.termination_reason,
                    failure_summary=str(
                        self._loop_state.scratchpad.get(
                            "coding.last_failure_summary",
                            "verification failed",
                        )
                        or "verification failed"
                    ),
                    allowed_tools=allowed_tools,
                )
            if bool(
                self._loop_state.scratchpad.pop("coding.pending_continue", False)
            ):
                return self._exit_continue(
                    ctx,
                    allowed_tools=allowed_tools,
                )
            return None
        self._append_phase_instruction()
        self._sync_coding_module_state(ctx)
        return None

    def snapshot_state(self) -> dict[str, Any]:
        return {
            "messages": [
                message.model_dump(mode="python")
                for message in self._loop_state.messages
            ],
            "iteration": self._loop_state.iteration,
            "llm_calls": self._loop_state.llm_calls,
            "tool_calls_made": list(self._loop_state.tool_calls_made),
            "total_tool_calls": self._loop_state.total_tool_calls,
            "termination_reason": self._loop_state.termination_reason,
            "scratchpad": dict(self._loop_state.scratchpad),
            "seen_signatures": list(self._loop_state.seen_signatures),
            "coding_plan": self._coding_plan.to_payload()
            if self._coding_plan is not None
            else None,
            "resume_count": int(self._resume_count),
            "last_checkpoint_id": self._last_checkpoint_id,
        }

    def restore_state(self, payload: dict[str, Any]) -> None:
        state = dict(payload or {})
        self._loop_state = CodingLoopState(
            messages=[
                Message.model_validate(item)
                for item in list(state.get("messages", []) or [])
            ],
            iteration=int(state.get("iteration", 0) or 0),
            llm_calls=int(state.get("llm_calls", 0) or 0),
            tool_calls_made=list(state.get("tool_calls_made", []) or []),
            total_tool_calls=int(state.get("total_tool_calls", 0) or 0),
            termination_reason=str(state.get("termination_reason", "") or ""),
            scratchpad=dict(state.get("scratchpad", {}) or {}),
            seen_signatures=list(state.get("seen_signatures", []) or []),
        )
        raw_plan = state.get("coding_plan")
        self._coding_plan = (
            coding_plan_from_payload(raw_plan, goal="")
            if isinstance(raw_plan, dict)
            else None
        )
        self._resume_count = int(state.get("resume_count", 0) or 0)
        checkpoint_id = str(state.get("last_checkpoint_id", "") or "").strip()
        self._last_checkpoint_id = checkpoint_id or None
        candidate_payload = self._loop_state.scratchpad.get(
            "coding.last_verifier_candidate"
        )
        self._last_verifier_candidate_payload = (
            dict(candidate_payload) if isinstance(candidate_payload, dict) else None
        )

    def _dispatch_subtasks_if_needed(self, ctx: ExecutionContext) -> None:
        # that still monkeypatch coding.handler.invoke_decision_direct.
        _subtasks_module.invoke_decision_direct = invoke_decision_direct
        _subtasks_dispatch_if_needed(self, ctx)

    def _sync_coding_context(self, ctx: ExecutionContext) -> None:
        workspace_hint = _context_workspace_hint(ctx)
        if workspace_hint:
            self._loop_state.scratchpad["coding.cwd"] = workspace_hint
        if self._coding_plan is not None:
            self._loop_state.scratchpad["coding.requires_file_change"] = bool(
                self._coding_plan.requires_file_change
            )

    def _as_adaptive_state(self, loop_state: CodingLoopState) -> AdaptiveToolLoopState:
        return AdaptiveToolLoopState(
            messages=list(loop_state.messages),
            iteration=int(loop_state.iteration or 0),
            llm_calls=int(loop_state.llm_calls or 0),
            tool_calls_made=list(loop_state.tool_calls_made),
            total_tool_calls=int(loop_state.total_tool_calls or 0),
            termination_reason=str(loop_state.termination_reason or ""),
            direct_tool_turn=loop_state.direct_tool_turn,
            scratchpad=dict(loop_state.scratchpad),
            seen_signatures=list(loop_state.seen_signatures),
        )

    def _sync_loop_state(self, adaptive_state: AdaptiveToolLoopState) -> None:
        scratchpad = dict(adaptive_state.scratchpad)
        parallel_calls = int(scratchpad.get("loop.tool_calls_parallel", 0) or 0)
        if "coding.parallel_fan_out_count" not in scratchpad:
            scratchpad["coding.parallel_fan_out_count"] = parallel_calls
        if "coding.tool_calls_parallel" not in scratchpad:
            scratchpad["coding.tool_calls_parallel"] = parallel_calls
        if "coding.tool_calls_sequential" not in scratchpad:
            scratchpad["coding.tool_calls_sequential"] = int(
                scratchpad.get("loop.tool_calls_sequential", 0) or 0
            )
        self._loop_state = CodingLoopState(
            messages=list(adaptive_state.messages),
            iteration=int(adaptive_state.iteration or 0),
            llm_calls=int(adaptive_state.llm_calls or 0),
            tool_calls_made=list(adaptive_state.tool_calls_made),
            total_tool_calls=int(adaptive_state.total_tool_calls or 0),
            termination_reason=str(adaptive_state.termination_reason or ""),
            direct_tool_turn=adaptive_state.direct_tool_turn,
            scratchpad=scratchpad,
            seen_signatures=list(adaptive_state.seen_signatures),
        )

    def _checkpoint_loop_state(
        self,
        ctx: ExecutionContext,
        *,
        adaptive_state: AdaptiveToolLoopState,
    ) -> None:
        self._sync_loop_state(adaptive_state)
        self._save_checkpoint(ctx, cursor=adaptive_state.iteration)

    def _result_from_outcome(
        self,
        ctx: ExecutionContext,
        *,
        outcome: AdaptiveToolLoopOutcome,
        allowed_tools: frozenset[str],
    ) -> ExecutionResult:
        return _results_from_outcome(
            self,
            ctx,
            outcome=outcome,
            allowed_tools=allowed_tools,
            build_error_result=_build_error_result,
            build_blocked_result=_build_blocked_result,
        )

    def _maybe_continue_after_tool_failure(
        self,
        ctx: ExecutionContext,
        *,
        loop: CodingLoopState,
        outcome: AdaptiveToolLoopOutcome,
    ) -> bool:
        return _results_maybe_continue_after_tool_failure(
            self,
            ctx,
            loop=loop,
            outcome=outcome,
        )

    def _exit_continue(
        self,
        ctx: ExecutionContext,
        *,
        allowed_tools: frozenset[str],
    ) -> ExecutionResult:
        return _results_exit_continue(self, ctx, allowed_tools=allowed_tools)

    def _exit_autonomous_blocked(
        self,
        ctx: ExecutionContext,
        *,
        reason_code: str,
        failure_summary: str,
        allowed_tools: frozenset[str],
    ) -> ExecutionResult:
        return _results_exit_autonomous_blocked(
            self,
            ctx,
            reason_code=reason_code,
            failure_summary=failure_summary,
            allowed_tools=allowed_tools,
            build_blocked_result=_build_blocked_result,
        )

    def _exit_final_text(
        self,
        ctx: ExecutionContext,
        loop: CodingLoopState,
        output_text: str,
        allowed_tools: frozenset[str],
    ) -> ExecutionResult:
        return _results_exit_final_text(
            self,
            ctx,
            loop,
            output_text,
            allowed_tools,
            build_blocked_result=_build_blocked_result,
        )

    def _exit_budget_exhausted(
        self,
        ctx: ExecutionContext,
        loop: CodingLoopState,
        allowed_tools: frozenset[str],
    ) -> ExecutionResult:
        return _results_exit_budget_exhausted(
            self,
            ctx,
            loop,
            allowed_tools,
            build_blocked_result=_build_blocked_result,
        )


class CodingMode(CodingProfileRunner, WorkflowMode):
    mode_name = BRAIN_INTERNAL_MODE_ACT_CODING
    mode_description = (
        "inspect, edit, and verify code in a workspace using a lean tool loop. "
        "Use for repo navigation, targeted edits, test/fix cycles, and "
        "code-focused implementation work."
    )
    mode_category = "workflow"
    has_prepare = True
    has_validate = False
    has_resume = True
    has_cancel = True
    priority_hint = 58
    mode_thinking_policy = {
        "default_reasoning_profile": "minimal",
        "allowed_reasoning_profiles": ("minimal", "detailed"),
        "allow_request_override": True,
    }
    decision_payload_fields: dict[str, Any] = {}
    default_config: dict[str, Any] = {
        "max_depth": 1,
        "max_adaptive_iterations": CODING_MAX_ITERATIONS,
        "max_self_corrections": CODING_MAX_SELF_CORRECTIONS,
    }
