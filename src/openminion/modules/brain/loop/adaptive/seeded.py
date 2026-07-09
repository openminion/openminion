from dataclasses import replace
import re
from typing import Any, cast

from openminion.modules.brain.constants import (
    BRAIN_INTERNAL_MODE_ACT_ADAPTIVE,
    BRAIN_DISPOSITION_CLOSE,
    BRAIN_DISPOSITION_CONTINUE,
    BRAIN_DISPOSITION_REPLAN,
    BRAIN_STATE_ACTIVE,
    BRAIN_STATE_DONE,
    BRAIN_STATE_ERROR,
    BRAIN_STATE_WAITING_USER,
    STATE_KEY_MODULE_STATE,
)
from openminion.modules.brain.config import ADAPTIVE_BUDGET_HARD_CAP
from openminion.modules.context.compress.eligibility import CompactionBudgetState
from openminion.modules.brain.execution.continuation import continuation_choice_message
from openminion.modules.brain.execution.closure import final_close_message
from openminion.modules.brain.execution.loop_contracts import (
    ExecutionContext,
    ExecutionResult,
)
from openminion.modules.brain.execution.lifecycle import active_mode_result
from openminion.modules.brain.tools.parser import normalize_tool_name_for_brain
from openminion.modules.brain.schemas.base import new_uuid
from openminion.modules.brain.schemas.state import ActionResult
from openminion.modules.brain.loop.tools import (
    AdaptiveToolLoopOutcome,
    AdaptiveToolLoopProfile,
    AdaptiveToolLoopRuntimeUnavailableError,
    AdaptiveToolLoopState,
    DefaultAdaptiveToolLoopLLMRuntime,
    build_runtime_tool_specs,
    resolve_loop_model,
    run_adaptive_tool_loop,
)
from openminion.modules.llm.schemas import Message
from openminion.modules.brain.loop.self_compaction import run_self_compaction_step
from openminion.modules.tool.contracts.model_ids import (
    MODEL_CODE_PATCH,
    MODEL_FILE_EDIT,
    MODEL_FILE_WRITE,
)

from ..services import runner_from_context

from .allowed_tools import (
    ACT_ADAPTIVE_ALLOWED_TOOLS,
)
from .context import (
    _AdaptiveLoopContextAdapter,
    _adaptive_loop_metadata,
    _sync_adaptive_intent_tracking,
)
from .events import (
    _postprocess_adaptive_response_trailers,
    _stage_task_plan_events,
)
from .termination import (
    _waiting_without_plan_can_close,
)
from ..tools.postprocess.rules import (
    _looks_like_execution_preface_draft,
    _looks_like_unexecutable_tool_payload_text,
)

_SEEDED_REPLAY_ARTIFACT_MUTATION_TOOLS = frozenset(
    {
        MODEL_CODE_PATCH,
        MODEL_FILE_EDIT,
        MODEL_FILE_WRITE,
    }
)
_CONTROL_RESTRICTED_REASON_CODES = frozenset(
    {
        "confirmation_replay",
        "confirmation_replay_recovery",
        "research_iteration_fallback",
    }
)
_SEEDED_CONTINUATION_FILE_TOKEN_RE = re.compile(
    r"\b[\w.-]+\.(?:py|md|toml|txt|json|yaml|yml|csv|ini|cfg)\b",
    re.IGNORECASE,
)
_SEEDED_CONTINUATION_VERIFICATION_MARKERS = (
    "pytest",
    "tests",
    "verify",
    "verification",
    "validate",
    "validation",
)
_SEEDED_CONTINUATION_FINAL_ANSWER_MARKERS = (
    "sources",
    "changes",
    "final answer",
)


from . import modes as _adaptive_modes  # noqa: E402

adaptive_modes: Any = _adaptive_modes
from .tool_scope import (  # noqa: E402
    _adaptive_public_attr,
    _public_act_tag,
    _without_control_tool_names,
)


class ActLoopSeededMixin:
    def _maybe_run_self_compaction(
        self,
        ctx: ExecutionContext,
        *,
        runtime: Any,
        model: str,
        final_text: str,
    ) -> Any | None:
        runner = runner_from_context(ctx)
        context_api = (
            getattr(runner, "context_api", None) if runner is not None else None
        )
        context_service = getattr(context_api, "service", None)
        if context_service is None:
            return None
        module_state = dict(getattr(ctx.state, STATE_KEY_MODULE_STATE, {}) or {})
        maintenance = module_state.get("memory_context_maintenance")
        if not isinstance(maintenance, dict):
            maintenance = {}
        max_prompt_tokens = max(
            1,
            int(getattr(ctx.state.budgets_remaining, "tokens", 0) or 1),
        )
        prompt_source = "\n".join(
            part
            for part in [
                str(final_text or "").strip(),
                str(getattr(ctx.state, "goal", "") or "").strip(),
            ]
            if str(part or "").strip()
        )
        prompt_token_estimate = (
            max(1, len(prompt_source.split())) if prompt_source else 1
        )
        budget_state = CompactionBudgetState(
            max_prompt_tokens=max_prompt_tokens,
            consolidation_eligible=False,
            consolidation_completed=bool(
                str(maintenance.get("last_consolidation_marker", "") or "").strip()
            ),
        )
        result = run_self_compaction_step(
            working_state=ctx.state,
            runtime=runtime,
            model=model,
            context_service=context_service,
            prompt_token_estimate=prompt_token_estimate,
            budget_state=budget_state,
            session_api=(
                getattr(runner, "session_api", None) if runner is not None else None
            ),
            recent_work=final_text,
            reason="token_pressure",
        )
        maybe_compact_with_state = getattr(
            context_service, "maybe_compact_with_state", None
        )
        if callable(maybe_compact_with_state):
            maybe_compact_with_state(
                ctx.state.session_id,
                working_state=ctx.state,
            )
        return result

    def _act_profile(self, ctx: ExecutionContext) -> str:
        return str(getattr(ctx.decision, "act_profile", "") or "").strip().lower()

    def _seeded_commands(self, ctx: ExecutionContext) -> list[Any]:
        return list(getattr(ctx.decision, "_seeded_commands", []) or [])

    def _seeded_continue_stays_autonomous(self, ctx: ExecutionContext) -> bool:
        reason_code = (
            str(getattr(ctx.decision, "reason_code", "") or "").strip().lower()
        )
        return reason_code in {
            "confirmation_replay",
            "confirmation_replay_validation",
            "entry_tool_call",
        }

    def _seeded_close_disposition_reopens_autonomous(
        self,
        ctx: ExecutionContext,
        *,
        judgment: Any,
    ) -> bool:
        if not self._seeded_continue_stays_autonomous(ctx):
            return False
        if not str(getattr(judgment, "final_answer", "") or "").strip():
            return True
        for command in self._seeded_commands(ctx):
            raw_tool_name = str(getattr(command, "tool_name", "") or "").strip()
            tool_name = (
                normalize_tool_name_for_brain(raw_tool_name) or raw_tool_name
            ).strip()
            if tool_name in _SEEDED_REPLAY_ARTIFACT_MUTATION_TOOLS:
                return True
        return False

    def _seeded_mutation_batch_should_continue_autonomously(
        self,
        ctx: ExecutionContext,
        *,
        action_result: ActionResult,
    ) -> bool:
        if not self._seeded_continue_stays_autonomous(ctx):
            return False
        original_goal = self._seeded_original_goal(ctx)
        if not original_goal:
            return False
        outputs = dict(getattr(action_result, "outputs", {}) or {})
        tool_results = [
            item
            for item in list(outputs.get("tool_results", []) or [])
            if isinstance(item, dict) and bool(item.get("ok"))
        ]
        tool_names = {
            str(item.get("tool_name", "") or "").strip()
            for item in tool_results
            if str(item.get("tool_name", "") or "").strip()
        }
        if not tool_names:
            tool_names = {
                str(getattr(command, "tool_name", "") or "").strip()
                for command in self._seeded_commands(ctx)
                if str(getattr(command, "tool_name", "") or "").strip()
            }
        if not any(
            tool_name in _SEEDED_REPLAY_ARTIFACT_MUTATION_TOOLS
            for tool_name in tool_names
        ):
            return False
        if "exec.run" in tool_names:
            return False
        goal_lower = original_goal.lower()
        mentioned_files = {
            match.group(0)
            for match in _SEEDED_CONTINUATION_FILE_TOKEN_RE.finditer(original_goal)
        }
        needs_verification = any(
            marker in goal_lower for marker in _SEEDED_CONTINUATION_VERIFICATION_MARKERS
        )
        needs_structured_final_answer = any(
            marker in goal_lower for marker in _SEEDED_CONTINUATION_FINAL_ANSWER_MARKERS
        )
        return (
            len(mentioned_files) > 1
            or needs_verification
            or needs_structured_final_answer
        )

    def _seeded_final_text_is_unexecutable_tool_envelope(self, text: str) -> bool:
        return _looks_like_unexecutable_tool_payload_text(
            text
        ) or _looks_like_execution_preface_draft(text)

    def _seeded_final_text_retry_available(
        self,
        ctx: ExecutionContext,
        *,
        max_retries: int = 4,
    ) -> bool:
        module_state = dict(getattr(ctx.state, STATE_KEY_MODULE_STATE, {}) or {})
        retry_state = module_state.get("seeded_final_text_autonomous_retry")
        if not isinstance(retry_state, dict):
            retry_state = {}
        count = int(retry_state.get("count", 0) or 0)
        if count >= max_retries:
            return False
        module_state["seeded_final_text_autonomous_retry"] = {"count": count + 1}
        ctx.state.module_state = module_state
        return True

    def _seeded_replay_initial_state(
        self, ctx: ExecutionContext
    ) -> AdaptiveToolLoopState:
        loop_state = AdaptiveToolLoopState(messages=[], scratchpad={})
        module_state = dict(getattr(ctx.state, STATE_KEY_MODULE_STATE, {}) or {})
        raw_snapshot = module_state.pop("adaptive_loop", None)
        if module_state != dict(getattr(ctx.state, STATE_KEY_MODULE_STATE, {}) or {}):
            ctx.state.module_state = module_state
        if not isinstance(raw_snapshot, dict):
            return loop_state

        from openminion.modules.brain.loop.tools.snapshot import LoopSnapshot

        try:
            snapshot = LoopSnapshot.from_dict(raw_snapshot)
        except Exception:  # noqa: BLE001
            return loop_state

        transcript = [
            Message(
                role=cast(Any, str(item.get("role", "") or "").strip() or "system"),
                content=str(item.get("content", "") or ""),
            )
            for item in list(snapshot.message_transcript or [])
            if isinstance(item, dict)
        ]
        tool_results = [
            item for item in list(snapshot.tool_results or []) if isinstance(item, dict)
        ]
        loop_state.messages = transcript
        if tool_results:
            loop_state.scratchpad["adaptive.tool_results"] = tool_results
        return loop_state

    def _seeded_recoverable_policy_denial_message(
        self,
        *,
        action_result: ActionResult,
    ) -> str | None:
        error_obj = getattr(action_result, "error", None)
        details = getattr(error_obj, "details", None)
        error_code = str(getattr(error_obj, "code", "") or "").strip().upper()
        outputs = getattr(action_result, "outputs", None)
        nested_error = outputs.get("error") if isinstance(outputs, dict) else None
        if error_code != "POLICY_DENIED" and isinstance(nested_error, dict):
            error_code = str(nested_error.get("code", "") or "").strip().upper()
        if not isinstance(details, dict) and isinstance(nested_error, dict):
            nested_details = nested_error.get("details")
            if isinstance(nested_details, dict):
                details = nested_details
        if error_code != "POLICY_DENIED" or not isinstance(details, dict):
            return None
        suggested_tool = str(details.get("suggested_tool", "") or "").strip()
        if not suggested_tool:
            return None
        suggested_fix = str(details.get("suggested_fix", "") or "").strip()
        blocked_tool = str(details.get("tool_name", "") or "").strip() or "tool"
        message = (
            f"The confirmed {blocked_tool} command was blocked by policy. "
            f"Do not repeat it. Retry the same user task using {suggested_tool} "
            "if that structured tool can satisfy the intent."
        )
        return f"{message} {suggested_fix}" if suggested_fix else message

    def _seeded_replay_allowed_tools(self, ctx: ExecutionContext) -> frozenset[str]:
        commands = self._seeded_commands(ctx)
        seeded_tool_names = frozenset(
            str(getattr(command, "tool_name", "") or "").strip()
            for command in commands
            if str(getattr(command, "kind", "") or "").strip() == "tool"
            and str(getattr(command, "tool_name", "") or "").strip()
        )
        if not self._seeded_continue_stays_autonomous(ctx):
            return seeded_tool_names or ACT_ADAPTIVE_ALLOWED_TOOLS
        return frozenset(
            {
                *_without_control_tool_names(ACT_ADAPTIVE_ALLOWED_TOOLS),
                *seeded_tool_names,
            }
        )

    def _seeded_replay_loop_limits(
        self,
        *,
        command_count: int,
        autonomous_recovery: bool,
    ) -> tuple[int, int]:
        replay_floor = max(1, command_count + (2 if autonomous_recovery else 0))
        configured_iterations = max(1, int(getattr(self, "_max_iterations", 1) or 1))
        configured_tool_calls = max(
            1, int(getattr(self, "_max_tool_calls_per_loop", 1) or 1)
        )
        if not autonomous_recovery:
            return min(replay_floor, configured_iterations), replay_floor
        return (
            min(max(configured_iterations, replay_floor), ADAPTIVE_BUDGET_HARD_CAP),
            min(max(configured_tool_calls, replay_floor), ADAPTIVE_BUDGET_HARD_CAP),
        )

    def _autonomous_seeded_result(
        self,
        ctx: ExecutionContext,
        *,
        action_result: ActionResult,
        transition_name: str | None = None,
    ) -> ExecutionResult:
        if transition_name is None:
            transition_name = (
                "user_input_received"
                if ctx.state.status == BRAIN_STATE_WAITING_USER
                else "closure_retry"
            )
        continuation_guidance = str(
            getattr(ctx.state, "post_action_user_message", "") or ""
        )
        adaptive_modes.transition(ctx.state, transition_name, logger=ctx.logger)
        if continuation_guidance:
            ctx.state.post_action_user_message = continuation_guidance
        ctx.state.status = BRAIN_STATE_ACTIVE
        return active_mode_result(
            host=ctx, state=ctx.state, action_result=action_result
        )

    def _seeded_autonomous_continuation_guidance(
        self,
        *,
        ctx: ExecutionContext,
        loop_outcome: AdaptiveToolLoopOutcome,
    ) -> str:
        tool_results = [
            item
            for item in list(
                getattr(getattr(loop_outcome, "state", None), "scratchpad", {}).get(
                    "adaptive.tool_results", []
                )
                or []
            )
            if isinstance(item, dict) and bool(item.get("ok"))
        ]
        successful_tools = [
            str(item.get("tool_name", "") or "").strip()
            for item in tool_results
            if str(item.get("tool_name", "") or "").strip()
        ]
        if not successful_tools:
            successful_tools = [
                str(getattr(command, "tool_name", "") or "").strip()
                for command in self._seeded_commands(ctx)
                if str(getattr(command, "tool_name", "") or "").strip()
            ]
        if not successful_tools:
            return ""
        unique_tools = tuple(dict.fromkeys(successful_tools))
        guidance = (
            "Continue from the current task state. Do not restart from scratch or "
            "repeat already successful tool calls unless you are correcting a "
            "specific mistake."
        )
        if unique_tools == ("file.write",):
            guidance += (
                " Recent progress created or updated files, so inspect or verify the "
                "current project state before claiming success."
            )
        elif unique_tools:
            guidance += (
                " Build on the completed tool work before choosing the next action."
            )
        guidance += " Only give a final answer after the task is actually satisfied."
        original_goal = self._seeded_original_goal(ctx)
        if original_goal:
            guidance += f" Continue the original task: {original_goal}"
        return guidance

    def _seeded_original_goal(self, ctx: ExecutionContext) -> str:
        """Return the user task that seeded a confirmation replay."""
        pending_goal = str(
            getattr(ctx.state, "pending_confirmation_goal", "") or ""
        ).strip()
        pending_input = str(
            getattr(ctx.state, "pending_confirmation_last_user_input", "") or ""
        ).strip()
        return (
            pending_goal
            or pending_input
            or str(getattr(ctx.state, "goal", "") or "").strip()
            or str(getattr(ctx.decision, "objective", "") or "").strip()
            or str(getattr(ctx.state, "last_user_input", "") or "").strip()
        )

    def _execute_seeded_commands(self: Any, ctx: ExecutionContext) -> ExecutionResult:
        commands = self._seeded_commands(ctx)
        if not commands:
            return ExecutionResult(
                status=BRAIN_STATE_WAITING_USER,
                working_state=ctx.state,
                message="No executable commands were produced for this act replay.",
            )
        checkpoint_interval = max(
            0, int(getattr(ctx.options, "plan_checkpoint_interval", 0) or 0)
        )
        state_mode = str(getattr(ctx.state, "mode", "") or "").strip().lower()
        if hasattr(getattr(ctx.state, "mode", None), "value"):
            state_mode = str(getattr(ctx.state.mode, "value", "") or "").strip().lower()
        if (
            checkpoint_interval > 0
            and state_mode == "guided"
            and ctx.state.plan is not None
            and 0 < ctx.state.cursor < len(ctx.state.plan.steps)
            and ctx.state.cursor % checkpoint_interval == 0
            and ctx.state.cursor != ctx.state.last_checkpoint_cursor
        ):
            ctx.state.last_checkpoint_cursor = ctx.state.cursor
            ctx.state.awaiting_continuation_reply = True
            ctx.state.continuation_guard_reason = ""
            return ExecutionResult.from_step_output(
                ctx.respond(
                    message=(
                        f"Completed {ctx.state.cursor}/{len(ctx.state.plan.steps)} "
                        "steps. Reply 'continue' to proceed."
                    ),
                    status=BRAIN_STATE_WAITING_USER,
                )
            )
        autonomous_recovery = self._seeded_continue_stays_autonomous(ctx)
        allowed_tools = self._seeded_replay_allowed_tools(ctx)
        max_iterations, max_tool_calls = self._seeded_replay_loop_limits(
            command_count=len(commands),
            autonomous_recovery=autonomous_recovery,
        )
        runtime = None
        model = ""
        tool_specs = []
        if autonomous_recovery:
            try:
                runtime = DefaultAdaptiveToolLoopLLMRuntime.from_adapter(
                    ctx.llm_adapter
                )
                model = resolve_loop_model(ctx)
                tool_specs = build_runtime_tool_specs(
                    runner_from_context(ctx),
                    allowed_tools=allowed_tools,
                )
            except AdaptiveToolLoopRuntimeUnavailableError:
                runtime = None
                model = ""
                tool_specs = []
        profile = AdaptiveToolLoopProfile(
            profile_name="general_seeded_v1",
            mode_name=BRAIN_INTERNAL_MODE_ACT_ADAPTIVE,
            allowed_tools=allowed_tools,
            provider_parallel_tool_capacity=2,
            max_iterations=max_iterations,
            max_tool_calls_per_loop=max_tool_calls,
            reflection_policy="never",
            max_macro_corrections=0,
            allow_llm_recovery_after_tool_failure=autonomous_recovery
            and runtime is not None,
            final_closure_policy="engine_single_pass",
        )
        run_loop = _adaptive_public_attr(
            "run_adaptive_tool_loop", run_adaptive_tool_loop
        )
        initial_state = self._seeded_replay_initial_state(ctx)
        outcome = run_loop(
            _AdaptiveLoopContextAdapter(ctx),
            profile=profile,
            runtime=runtime,
            model=model,
            initial_messages=list(initial_state.messages),
            initial_state=initial_state,
            tool_specs=tool_specs,
            seeded_commands=commands,
            finalizer=lambda loop_outcome: self._finalize_seeded_success(
                ctx,
                loop_outcome=loop_outcome,
            ),
        )
        if outcome.action_result is not None:
            recovery_message = self._seeded_recoverable_policy_denial_message(
                action_result=outcome.action_result,
            )
            if recovery_message:
                outcome.state.messages.append(
                    Message(role="system", content=recovery_message)
                )
                ctx.state.post_action_user_message = ""
                ctx.state.last_result = outcome.action_result
                return cast(
                    ExecutionResult,
                    self._continue_after_seeded_policy_denial(
                        ctx,
                        action_result=outcome.action_result,
                        recovery_message=recovery_message,
                    ),
                )
        if outcome.mode_result is not None:
            return cast(ExecutionResult, outcome.mode_result)
        return cast(ExecutionResult, self._result_from_outcome(ctx, outcome=outcome))

    def _continue_after_seeded_policy_denial(
        self: Any,
        ctx: ExecutionContext,
        *,
        action_result: ActionResult,
        recovery_message: str,
    ) -> ExecutionResult:
        original_goal = self._seeded_original_goal(ctx)
        follow_up = recovery_message
        if original_goal:
            follow_up = f"{follow_up}\n\nContinue the original task: {original_goal}"
        try:
            ctx.decision._seeded_commands = []
            ctx.decision.reason_code = "confirmation_replay_recovery"
        except Exception:  # noqa: BLE001
            pass
        recovery_ctx = replace(ctx, user_input=follow_up)
        ctx.state.post_action_user_message = ""
        ctx.state.last_result = action_result
        return cast(ExecutionResult, self.execute(recovery_ctx))

    def _finalize_seeded_success(
        self: Any,
        ctx: ExecutionContext,
        *,
        loop_outcome: AdaptiveToolLoopOutcome,
    ) -> ExecutionResult:
        completed_ids, remaining_ids = _sync_adaptive_intent_tracking(
            ctx=ctx,
            loop_state=loop_outcome.state,
        )
        telemetry_payload = loop_outcome.telemetry_payload()
        telemetry_payload.update(
            {
                "completed_intent_ids": list(completed_ids),
                "remaining_intent_ids": list(remaining_ids),
            }
        )
        _stage_task_plan_events(ctx, loop_outcome)
        _postprocess_adaptive_response_trailers(
            ctx,
            loop_outcome,
            request_metadata=_adaptive_loop_metadata(ctx, purpose="act"),
        )
        action_result = loop_outcome.action_result or ActionResult(
            command_id=new_uuid(),
            status="success",
            summary=f"{_public_act_tag()} completed.",
            outputs=telemetry_payload,
        )
        try:
            action_result = action_result.model_copy(
                update={
                    "outputs": {
                        **dict(getattr(action_result, "outputs", {}) or {}),
                        **telemetry_payload,
                    }
                },
                deep=True,
            )
        except Exception:  # noqa: BLE001
            pass
        ctx.state.last_result = action_result
        continuation_guidance = self._seeded_autonomous_continuation_guidance(
            ctx=ctx, loop_outcome=loop_outcome
        )
        if self._seeded_continue_stays_autonomous(
            ctx
        ) and _waiting_without_plan_can_close(ctx=ctx, remaining_ids=remaining_ids):
            ctx.state.post_action_user_message = continuation_guidance
            return cast(
                ExecutionResult,
                self._autonomous_seeded_result(
                    ctx,
                    action_result=action_result,
                ),
            )
        if _waiting_without_plan_can_close(ctx=ctx, remaining_ids=remaining_ids):
            adaptive_modes.transition(ctx.state, "task_completed", logger=ctx.logger)
        if ctx.state.status == BRAIN_STATE_WAITING_USER:
            return ExecutionResult.from_step_output(
                ctx.respond(
                    message=str(
                        getattr(ctx.state, "post_action_user_message", "") or ""
                    ).strip()
                    or action_result.summary
                    or f"{_public_act_tag()} needs guidance.",
                    status=BRAIN_STATE_WAITING_USER,
                    action_result=action_result,
                )
            )
        if ctx.state.status == BRAIN_STATE_ERROR:
            return ExecutionResult.from_step_output(
                ctx.respond(
                    message=action_result.summary or f"{_public_act_tag()} failed.",
                    status=BRAIN_STATE_ERROR,
                    action_result=action_result,
                )
            )
        if ctx.state.status != BRAIN_STATE_DONE:
            if self._seeded_continue_stays_autonomous(ctx):
                ctx.state.post_action_user_message = continuation_guidance
            return active_mode_result(
                host=ctx,
                state=ctx.state,
                action_result=action_result,
            )
        if self._seeded_mutation_batch_should_continue_autonomously(
            ctx,
            action_result=action_result,
        ):
            ctx.state.post_action_user_message = continuation_guidance
            return cast(
                ExecutionResult,
                self._autonomous_seeded_result(ctx, action_result=action_result),
            )
        judgment = ctx.evaluate_turn_closure(
            action_result=action_result,
            completion_reason="act_seeded_commands_completed",
        )
        disposition = ctx.apply_closure_judgment(judgment=judgment)
        if disposition == BRAIN_DISPOSITION_CLOSE:
            if self._seeded_close_disposition_reopens_autonomous(
                ctx,
                judgment=judgment,
            ):
                ctx.state.post_action_user_message = continuation_guidance
                return cast(
                    ExecutionResult,
                    self._autonomous_seeded_result(ctx, action_result=action_result),
                )
            ctx.extract_success_memories(
                action_result=action_result,
                judgment=judgment,
            )
            return ExecutionResult.from_step_output(
                ctx.respond(
                    message=final_close_message(
                        state=ctx.state,
                        judgment=judgment,
                        action_result=action_result,
                        fallback_message=(
                            action_result.summary or f"{_public_act_tag()} done"
                        ),
                    ),
                    status=BRAIN_STATE_DONE,
                    action_result=action_result,
                ),
                judgment=judgment,
            )
        if disposition == BRAIN_DISPOSITION_CONTINUE:
            if self._seeded_continue_stays_autonomous(ctx):
                ctx.state.post_action_user_message = continuation_guidance
                return cast(
                    ExecutionResult,
                    self._autonomous_seeded_result(ctx, action_result=action_result),
                )
            adaptive_modes.transition(
                ctx.state, "checkpoint_reached", logger=ctx.logger
            )
            return ExecutionResult.from_step_output(
                ctx.respond(
                    message=continuation_choice_message(judgment.reason),
                    status=BRAIN_STATE_WAITING_USER,
                    action_result=action_result,
                ),
                judgment=judgment,
            )
        if disposition == BRAIN_DISPOSITION_REPLAN:
            return active_mode_result(
                host=ctx,
                state=ctx.state,
                action_result=action_result,
            )
        if self._seeded_continue_stays_autonomous(ctx):
            ctx.state.post_action_user_message = continuation_guidance
            return cast(
                ExecutionResult,
                self._autonomous_seeded_result(ctx, action_result=action_result),
            )
        adaptive_modes.transition(ctx.state, "judgment_ask_user", logger=ctx.logger)
        return ExecutionResult.from_step_output(
            ctx.respond(
                message=judgment.reason
                or "I need clarification before closing this task.",
                status=BRAIN_STATE_WAITING_USER,
                action_result=action_result,
            ),
            judgment=judgment,
        )
