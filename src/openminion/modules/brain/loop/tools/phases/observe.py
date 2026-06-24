from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from openminion.modules.brain.checkpoint import CheckpointMixin
from openminion.modules.brain.constants import (
    BRAIN_INTERNAL_MODE_LOOP_PHASE_OBSERVE,
    BRAIN_STATE_DONE,
    BRAIN_STATE_WAITING_USER,
)
from openminion.modules.brain.config import (
    OBSERVE_POLL_INTERVAL_SECONDS,
    OBSERVE_TIMEOUT_SECONDS,
)
from openminion.modules.brain.diagnostics.transitions import transition
from openminion.modules.brain.schemas import BudgetCounters
from openminion.modules.brain.execution.loop_contracts import (
    ExecutionContext,
    ExecutionResult,
)
from openminion.modules.brain.execution.preflight import (
    ModePreparation,
    ValidationResult,
)
from openminion.modules.brain.loop.tools.structured_llm import structured_mode_response
from openminion.modules.brain.execution.workflow import (
    StepJudgment,
    StepResult,
    WorkflowMode,
    WorkflowPlan,
    WorkflowStep,
)
from .child_execution import (
    build_child_state,
    execute_child_goal,
    normalized_text,
    plan_objective_fallback,
    split_budget_evenly,
)

OBSERVE_MODE = BRAIN_INTERNAL_MODE_LOOP_PHASE_OBSERVE
_SLEEP_POLL_SECONDS = 1.0


class ObservePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observe_target: str = Field(..., min_length=1)
    observe_condition: str = Field(..., min_length=1)
    observe_check_command: str = ""


class ObservationCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    iteration: int = Field(..., ge=1)
    elapsed_seconds: float = Field(default=0.0, ge=0.0)
    check_output: str = ""
    condition_met: bool = False
    assessment: str = ""


class _InferredCheckCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    check_command: str = Field(default="", min_length=1)


class _ObservationAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    check_output: str = ""
    condition_met: bool = False
    assessment: str = ""


class ObserveMode(CheckpointMixin, WorkflowMode):
    CHECKPOINT_VERSION = 1
    mode_name = OBSERVE_MODE
    mode_description = (
        "poll a target at regular intervals and report when a condition is "
        "met or a timeout is reached — use for monitoring endpoints, waiting "
        "for builds to finish, watching files for changes, or any task where "
        "the agent needs to wait and check rather than act immediately."
    )
    mode_category = "workflow"
    has_prepare = True
    has_validate = True
    has_resume = True
    priority_hint = 75
    mode_thinking_policy = {
        "default_reasoning_profile": "minimal",
        "allowed_reasoning_profiles": ("off", "minimal"),
        "allow_request_override": True,
    }
    default_config = {
        "observe_poll_interval_seconds": OBSERVE_POLL_INTERVAL_SECONDS,
        "observe_timeout_seconds": OBSERVE_TIMEOUT_SECONDS,
        "checkpoint_interval": 1,
    }
    decision_payload_fields = {
        "observe_target": (
            str,
            Field(default="", description="What to observe"),
        ),
        "observe_condition": (
            str,
            Field(default="", description="Condition that triggers exit"),
        ),
        "observe_check_command": (
            str,
            Field(
                default="",
                description=(
                    "How to check (shell cmd, URL, file path); if empty the "
                    "LLM infers the check from the target"
                ),
            ),
        ),
    }

    def __init__(self) -> None:
        self._poll_interval_seconds = OBSERVE_POLL_INTERVAL_SECONDS
        self._timeout_seconds = OBSERVE_TIMEOUT_SECONDS
        self._target = ""
        self._condition = ""
        self._check_command = ""
        self._check_history: list[ObservationCheck] = []
        self._start_monotonic: float = 0.0
        self._elapsed_seconds = 0.0
        self._max_checks = 1
        self._termination_reason: str | None = None
        self._checkpoint_interval = 1

    def apply_mode_config(self, *, config, runner, profile) -> None:
        del runner, profile
        if config is None:
            self._poll_interval_seconds = OBSERVE_POLL_INTERVAL_SECONDS
            self._timeout_seconds = OBSERVE_TIMEOUT_SECONDS
            return
        poll_value = getattr(config, "observe_poll_interval_seconds", None)
        timeout_value = getattr(config, "observe_timeout_seconds", None)
        if isinstance(config, dict):
            if poll_value is None:
                poll_value = config.get("observe_poll_interval_seconds")
            if timeout_value is None:
                timeout_value = config.get("observe_timeout_seconds")
        self._poll_interval_seconds = max(
            1,
            int(poll_value or OBSERVE_POLL_INTERVAL_SECONDS),
        )
        self._timeout_seconds = max(
            1,
            int(timeout_value or OBSERVE_TIMEOUT_SECONDS),
        )
        checkpoint_interval = getattr(config, "checkpoint_interval", None)
        if checkpoint_interval is None and isinstance(config, dict):
            checkpoint_interval = config.get("checkpoint_interval")
        self._checkpoint_interval = max(1, int(checkpoint_interval or 1))

    def prepare(
        self,
        ctx: ExecutionContext,
        *,
        emit_status_updates: bool = False,
    ) -> ModePreparation:
        del ctx, emit_status_updates
        return ModePreparation()

    def validate(
        self,
        ctx: ExecutionContext,
        *,
        preparation: ModePreparation | None = None,
    ) -> ValidationResult | None:
        del preparation
        target = self._target_from_context(ctx)
        if not target:
            return ValidationResult(
                passed=False,
                feedback="Observe mode requires a non-empty observe_target.",
                code="missing_observe_target",
            )
        condition = self._condition_from_context(ctx)
        if not condition:
            return ValidationResult(
                passed=False,
                feedback="Observe mode requires a non-empty observe_condition.",
                code="missing_observe_condition",
            )
        return ValidationResult(passed=True)

    def initialize(self, ctx: ExecutionContext) -> WorkflowPlan:
        self._init_checkpoint(ctx)
        self._target = self._target_from_context(ctx)
        self._condition = self._condition_from_context(ctx)
        self._check_command = normalized_text(
            getattr(ctx.decision, "observe_check_command", "") or ""
        )
        if not self._check_command:
            self._check_command = self._infer_check_command(ctx, target=self._target)
        if not self._checkpoint_resuming:
            self._check_history = []
            self._elapsed_seconds = 0.0
        self._start_monotonic = time.monotonic() - float(self._elapsed_seconds)
        self._termination_reason = None
        self._max_checks = max(
            1, int(self._timeout_seconds // self._poll_interval_seconds)
        )
        steps = [f"observe_check_{idx + 1}" for idx in range(self._max_checks)]
        return WorkflowPlan(steps=steps)

    def execute_step(self, ctx: ExecutionContext, step: WorkflowStep) -> StepResult:
        if step.index > 0:
            budget_exit = self._sleep_until_next_poll(ctx)
            if budget_exit is not None:
                return StepResult(step=step, mode_result=budget_exit)

        child_goal = (
            f"Run this check against {self._target!r}: {self._check_command}. "
            "Report the raw output only."
        )
        output = self._dispatch_check(ctx, child_goal=child_goal)
        return StepResult(step=step, metadata={"check_output": output})

    def judge_step(
        self,
        ctx: ExecutionContext,
        step: WorkflowStep,
        result: StepResult,
    ) -> StepJudgment:
        check_output = normalized_text(result.metadata.get("check_output", "") or "")
        elapsed = max(0.0, time.monotonic() - self._start_monotonic)
        check = self._evaluate_condition(
            ctx,
            iteration=step.index + 1,
            elapsed_seconds=elapsed,
            check_output=check_output,
        )
        self._append_repetition_note(check)
        self._check_history.append(check)
        self._elapsed_seconds = float(check.elapsed_seconds)
        self._save_checkpoint(ctx, cursor=step.index + 1)

        if check.condition_met:
            self._termination_reason = "condition_met"
            return StepJudgment(disposition="close")
        if elapsed >= float(self._timeout_seconds):
            self._termination_reason = "timeout"
            return StepJudgment(disposition="close")
        pause_result = self._pause_after_check(ctx, completed_checks=step.index + 1)
        if pause_result is not None:
            return StepJudgment(disposition="continue", mode_result=pause_result)
        return StepJudgment(disposition="continue")

    def finalize(self, ctx: ExecutionContext) -> ExecutionResult:
        if (
            self._termination_reason is None
            and len(self._check_history) >= self._max_checks
        ):
            self._termination_reason = "iteration_cap"
        summary = self._build_report()
        self._finalize_checkpoint(ctx, terminal=True, cursor=len(self._check_history))
        transition(ctx.state, "task_completed", logger=ctx.logger)
        return ExecutionResult.from_step_output(
            ctx.respond(message=summary, status=BRAIN_STATE_DONE)
        )

    def snapshot_state(self) -> dict[str, Any]:
        return {
            "check_history": [
                item.model_dump(mode="python") for item in self._check_history
            ],
            "elapsed_seconds": float(self._elapsed_seconds),
        }

    def restore_state(self, payload: dict[str, Any]) -> None:
        state = dict(payload or {})
        self._check_history = [
            ObservationCheck.model_validate(item)
            for item in list(state.get("check_history", []) or [])
        ]
        self._elapsed_seconds = float(state.get("elapsed_seconds", 0.0) or 0.0)

    def _target_from_context(self, ctx: ExecutionContext) -> str:
        return (
            normalized_text(getattr(ctx.decision, "observe_target", "") or "")
            or normalized_text(getattr(ctx.decision, "objective", "") or "")
            or normalized_text(getattr(ctx.state, "goal", "") or "")
            or normalized_text(ctx.user_input or "")
        )

    def _condition_from_context(self, ctx: ExecutionContext) -> str:
        return normalized_text(getattr(ctx.decision, "observe_condition", "") or "")

    def _infer_check_command(self, ctx: ExecutionContext, *, target: str) -> str:
        prompt = (
            "Infer a concrete check command for an observation target.\n"
            f"Target: {target}\n"
            "Return a JSON object with a non-empty check_command string that "
            "describes how to check the target and report raw output."
        )
        inferred = structured_mode_response(
            ctx,
            prompt=prompt,
            schema=_InferredCheckCommand,
            purpose="reflect",
            max_tokens=500,
        )
        check_command = normalized_text(
            getattr(inferred, "check_command", "") if inferred is not None else ""
        )
        return check_command or f"Check {target!r} and report the raw output."

    def _sleep_until_next_poll(
        self,
        ctx: ExecutionContext,
    ) -> ExecutionResult | None:
        remaining = float(self._poll_interval_seconds)
        while remaining > 0:
            if self._budget_exhausted(ctx):
                return self._budget_exit(ctx)
            chunk = min(_SLEEP_POLL_SECONDS, remaining)
            time.sleep(chunk)
            remaining -= chunk
        return None

    def _dispatch_check(self, ctx: ExecutionContext, *, child_goal: str) -> str:
        child_state = build_child_state(
            parent_state=ctx.state,
            child_budget=self._check_budget(ctx),
            goal=child_goal,
        )
        content = execute_child_goal(
            ctx,
            child_goal=child_goal,
            child_state=child_state,
            blocked_mode_name=OBSERVE_MODE,
            fallback_reason_code="observe_recursion_fallback",
        )
        return content or plan_objective_fallback(
            ctx,
            child_goal=child_goal,
            default=f"Observed {self._target!r}.",
        )

    def _check_budget(self, ctx: ExecutionContext) -> BudgetCounters:
        return split_budget_evenly(
            budgets=ctx.state.budgets_remaining,
            divisor=max(1, self._max_checks - len(self._check_history)),
        )

    def _evaluate_condition(
        self,
        ctx: ExecutionContext,
        *,
        iteration: int,
        elapsed_seconds: float,
        check_output: str,
    ) -> ObservationCheck:
        history = self._check_history[-3:]
        history_text = (
            "\n".join(
                f"- Iteration {item.iteration}: met={item.condition_met}; "
                f"assessment={item.assessment}; output={item.check_output}"
                for item in history
            )
            if history
            else "- No prior checks."
        )
        prompt = (
            "Evaluate whether the observation condition is satisfied.\n"
            f"Target: {self._target}\n"
            f"Condition: {self._condition}\n"
            f"Current raw output: {check_output}\n"
            f"Recent history:\n{history_text}\n"
            "Return structured JSON with check_output, condition_met, and assessment."
        )
        parsed = structured_mode_response(
            ctx,
            prompt=prompt,
            schema=_ObservationAssessment,
            purpose="reflect",
            max_tokens=700,
        )
        if parsed is None:
            return ObservationCheck(
                iteration=iteration,
                elapsed_seconds=elapsed_seconds,
                check_output=check_output,
                condition_met=False,
                assessment="Condition not met yet (structured evaluation unavailable).",
            )
        return ObservationCheck(
            iteration=iteration,
            elapsed_seconds=elapsed_seconds,
            check_output=normalized_text(parsed.check_output) or check_output,
            condition_met=bool(parsed.condition_met),
            assessment=normalized_text(parsed.assessment),
        )

    def _append_repetition_note(self, check: ObservationCheck) -> None:
        if len(self._check_history) < 2:
            return
        last_two = self._check_history[-2:]
        output = normalized_text(check.check_output)
        if not output:
            return
        if all(normalized_text(item.check_output) == output for item in last_two):
            note = "Observed identical output for 3 consecutive checks."
            if check.assessment:
                check.assessment = f"{check.assessment} {note}".strip()
            else:
                check.assessment = note

    def _build_report(self) -> str:
        total_checks = len(self._check_history)
        elapsed = 0.0
        final_output = ""
        final_assessment = ""
        if self._check_history:
            last = self._check_history[-1]
            elapsed = last.elapsed_seconds
            final_output = normalized_text(last.check_output)
            final_assessment = normalized_text(last.assessment)

        if self._termination_reason == "condition_met":
            outcome = "Condition met."
        elif self._termination_reason == "timeout":
            outcome = "Timed out before the condition was met."
        elif self._termination_reason == "iteration_cap":
            outcome = "Stopped after reaching the observation iteration cap."
        else:
            outcome = "Observation finished."

        lines = [
            outcome,
            f"Target: {self._target}",
            f"Condition: {self._condition}",
            f"Checks performed: {total_checks}",
            f"Elapsed time: {elapsed:.1f}s",
        ]
        if final_output:
            lines.append(f"Final output: {final_output}")
        if final_assessment:
            lines.append(f"Assessment: {final_assessment}")
        return "\n".join(lines)

    def _pause_after_check(
        self,
        ctx: ExecutionContext,
        *,
        completed_checks: int,
    ) -> ExecutionResult | None:
        budgets = getattr(ctx.state, "budgets_remaining", None)
        if budgets is None:
            return None
        budgets.ticks = max(0, int(getattr(budgets, "ticks", 0) or 0) - 1)
        if completed_checks >= self._max_checks or int(budgets.ticks or 0) > 0:
            return None
        self._finalize_checkpoint(ctx, terminal=False, cursor=completed_checks)
        transition(ctx.state, "checkpoint_reached", logger=ctx.logger)
        return ExecutionResult.from_step_output(
            ctx.respond(
                message=(
                    f"Observation paused after check {completed_checks}. "
                    "Continue in a new turn to resume."
                ),
                status=BRAIN_STATE_WAITING_USER,
            )
        )
