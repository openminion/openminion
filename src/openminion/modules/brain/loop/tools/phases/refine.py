from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from openminion.modules.brain.checkpoint import CheckpointMixin
from openminion.modules.brain.constants import (
    BRAIN_INTERNAL_MODE_LOOP_PHASE_REFINE,
    BRAIN_STATE_DONE,
    BRAIN_STATE_WAITING_USER,
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

REFINE_MODE = BRAIN_INTERNAL_MODE_LOOP_PHASE_REFINE


class RefinePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refine_target: str = Field(..., min_length=1)
    refine_criteria: list[str] = Field(default_factory=list)


class RefinementRound(BaseModel):
    model_config = ConfigDict(extra="forbid")

    iteration: int = Field(..., ge=1)
    action_taken: str = ""
    quality_assessment: str = ""
    remaining_issues: list[str] = Field(default_factory=list)
    passed_gate: bool = False


class _RefinementAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_taken: str = ""
    quality_assessment: str = ""
    remaining_issues: list[str] = Field(default_factory=list)
    passed_gate: bool = False


class RefineMode(CheckpointMixin, WorkflowMode):
    CHECKPOINT_VERSION = 1
    mode_name = REFINE_MODE
    mode_description = (
        "iteratively improve an artifact through rounds of modification and "
        "quality evaluation — read current state, apply improvement via a "
        "bounded child-mode execution, evaluate against criteria, repeat "
        "until quality gate passes or iteration cap reached. Use for code "
        "cleanup, text polish, output improvement, or any task that requires "
        "repeated modify-evaluate cycles rather than a fixed plan."
    )
    mode_category = "workflow"
    has_prepare = True
    has_validate = True
    has_resume = True
    priority_hint = 70
    mode_thinking_policy = {
        "default_reasoning_profile": "detailed",
        "allowed_reasoning_profiles": ("minimal", "detailed"),
        "allow_request_override": True,
    }
    default_config = {"max_refine_iterations": 3, "checkpoint_interval": 1}
    decision_payload_fields = {
        "refine_target": (str, Field(default="", description="What to refine")),
        "refine_criteria": (
            list[str],
            Field(
                default_factory=list,
                description="Quality criteria to evaluate against",
            ),
        ),
    }

    def __init__(self) -> None:
        self._round_history: list[RefinementRound] = []
        self._max_refine_iterations = int(
            (getattr(self, "default_config", None) or {}).get(
                "max_refine_iterations", 3
            )
        )
        self._checkpoint_interval = int(
            (getattr(self, "default_config", None) or {}).get("checkpoint_interval", 1)
        )
        self._termination_reason: str | None = None

    def apply_mode_config(self, *, config, runner, profile) -> None:
        del runner, profile
        if config is None:
            self._max_refine_iterations = int(
                (getattr(self, "default_config", None) or {}).get(
                    "max_refine_iterations", 3
                )
            )
            return
        value = getattr(config, "max_refine_iterations", None)
        if value is None and isinstance(config, dict):
            value = config.get("max_refine_iterations")
        if value is None:
            value = (getattr(self, "default_config", None) or {}).get(
                "max_refine_iterations", 3
            )
        self._max_refine_iterations = int(value)
        checkpoint_interval = getattr(config, "checkpoint_interval", None)
        if checkpoint_interval is None and isinstance(config, dict):
            checkpoint_interval = config.get("checkpoint_interval")
        self._checkpoint_interval = int(checkpoint_interval or 1)

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
        if target:
            return ValidationResult(passed=True)
        return ValidationResult(
            passed=False,
            feedback="Refine mode requires a non-empty target (after fallback chain).",
            code="missing_refine_target",
        )

    def initialize(self, ctx: ExecutionContext) -> WorkflowPlan:
        self._init_checkpoint(ctx)
        if not self._checkpoint_resuming:
            self._round_history = []
        self._termination_reason = None
        steps = [
            f"refine_iteration_{i + 1}" for i in range(self._max_refine_iterations)
        ]
        return WorkflowPlan(steps=steps)

    def execute_step(self, ctx: ExecutionContext, step: WorkflowStep) -> StepResult:
        target = self._target_from_context(ctx)
        criteria = self._criteria_from_context(ctx)
        criteria_text = ", ".join(criteria) if criteria else "general quality"

        history_text = ""
        if self._round_history:
            history_lines = []
            for r in self._round_history:
                history_lines.append(
                    f"Round {r.iteration}: {r.action_taken} "
                    f"(issues: {', '.join(r.remaining_issues) or 'none'})"
                )
            history_text = " Prior rounds: " + "; ".join(history_lines) + "."

        child_goal = (
            f"Improve {target!r} addressing: {criteria_text}."
            f"{history_text} Apply one focused improvement."
        )

        content = self._dispatch_child(ctx, child_goal=child_goal)

        return StepResult(
            step=step,
            metadata={"improvement": content},
        )

    def judge_step(
        self,
        ctx: ExecutionContext,
        step: WorkflowStep,
        result: StepResult,
    ) -> StepJudgment:
        target = self._target_from_context(ctx)
        criteria = self._criteria_from_context(ctx)
        improvement = str(result.metadata.get("improvement", ""))
        iteration = step.index + 1

        rnd = self._assess_quality(
            ctx,
            target=target,
            criteria=criteria,
            improvement=improvement,
            iteration=iteration,
        )
        self._round_history.append(rnd)
        self._save_checkpoint(ctx, cursor=step.index + 1)

        if rnd.passed_gate:
            self._termination_reason = "passed_gate"
            return StepJudgment(disposition="close")

        if len(self._round_history) >= 2:
            prev = self._round_history[-2]
            if rnd.remaining_issues == prev.remaining_issues:
                self._termination_reason = "stall"
                return StepJudgment(
                    disposition="close",
                    metadata={"stall_detected": True},
                )

        pause_result = self._pause_after_round(ctx, completed_rounds=step.index + 1)
        if pause_result is not None:
            return StepJudgment(disposition="continue", mode_result=pause_result)
        return StepJudgment(disposition="continue")

    def finalize(self, ctx: ExecutionContext) -> ExecutionResult:
        summary = self._build_summary()
        self._finalize_checkpoint(ctx, terminal=True, cursor=len(self._round_history))
        transition(ctx.state, "task_completed", logger=ctx.logger)
        return ExecutionResult.from_step_output(
            ctx.respond(message=summary, status=BRAIN_STATE_DONE)
        )

    def snapshot_state(self) -> dict[str, Any]:
        return {
            "round_history": [
                round_.model_dump(mode="python") for round_ in self._round_history
            ]
        }

    def restore_state(self, payload: dict[str, Any]) -> None:
        self._round_history = [
            RefinementRound.model_validate(item)
            for item in list(dict(payload or {}).get("round_history", []) or [])
        ]

    def _target_from_context(self, ctx: ExecutionContext) -> str:
        return (
            normalized_text(getattr(ctx.decision, "refine_target", "") or "")
            or normalized_text(getattr(ctx.decision, "objective", "") or "")
            or normalized_text(getattr(ctx.state, "goal", "") or "")
            or normalized_text(ctx.user_input or "")
        )

    def _criteria_from_context(self, ctx: ExecutionContext) -> list[str]:
        raw = getattr(ctx.decision, "refine_criteria", None)
        if isinstance(raw, list):
            return [str(c) for c in raw if str(c).strip()]
        return []

    def _dispatch_child(self, ctx: ExecutionContext, *, child_goal: str) -> str:
        child_state = build_child_state(
            parent_state=ctx.state,
            child_budget=self._iteration_budget(ctx),
            goal=child_goal,
        )
        content = execute_child_goal(
            ctx,
            child_goal=child_goal,
            child_state=child_state,
            blocked_mode_name=REFINE_MODE,
            fallback_reason_code="refine_recursion_fallback",
        )
        return content or plan_objective_fallback(
            ctx,
            child_goal=child_goal,
            default=f"Improvement applied to {self._target_from_context(ctx)!r}.",
        )

    def _iteration_budget(self, ctx: ExecutionContext) -> BudgetCounters:
        return split_budget_evenly(
            budgets=ctx.state.budgets_remaining,
            divisor=max(1, self._max_refine_iterations - len(self._round_history)),
        )

    def _assess_quality(
        self,
        ctx: ExecutionContext,
        *,
        target: str,
        criteria: list[str],
        improvement: str,
        iteration: int,
    ) -> RefinementRound:
        criteria_text = (
            "\n".join(f"- {c}" for c in criteria)
            if criteria
            else "Assess general quality of the artifact."
        )
        history_text = ""
        if self._round_history:
            history_lines = []
            for r in self._round_history:
                history_lines.append(
                    f"Round {r.iteration}: action={r.action_taken!r}, "
                    f"issues={r.remaining_issues!r}, passed={r.passed_gate}"
                )
            history_text = "\n\nPrior rounds:\n" + "\n".join(history_lines)

        prompt = (
            f"Assess the quality of {target!r} after this improvement:\n"
            f"{improvement}\n\n"
            f"Criteria:\n{criteria_text}{history_text}\n\n"
            "Produce a JSON object with exactly these fields:\n"
            '{"action_taken": "...", "quality_assessment": "...", '
            '"remaining_issues": ["..."], "passed_gate": true/false}\n'
            "Set passed_gate to true only if all criteria are satisfied."
        )

        try:
            structured = structured_mode_response(
                ctx,
                prompt=prompt,
                schema=_RefinementAssessment,
                purpose="reflect",
            )
            if structured is None:
                raise ValueError("empty response")
            return RefinementRound(
                iteration=iteration,
                action_taken=structured.action_taken,
                quality_assessment=structured.quality_assessment,
                remaining_issues=list(structured.remaining_issues),
                passed_gate=structured.passed_gate,
            )
        except Exception:
            return RefinementRound(
                iteration=iteration,
                passed_gate=False,
                quality_assessment="Could not assess quality.",
            )

    def _build_summary(self) -> str:
        if not self._round_history:
            return "No refinement rounds were completed."

        lines = [f"Refinement complete — {len(self._round_history)} round(s).", ""]
        for r in self._round_history:
            status = "PASSED" if r.passed_gate else "CONTINUED"
            lines.append(f"Round {r.iteration} [{status}]: {r.action_taken}")
            if r.quality_assessment:
                lines.append(f"  Assessment: {r.quality_assessment}")
            if r.remaining_issues:
                lines.append(f"  Remaining: {', '.join(r.remaining_issues)}")
            lines.append("")

        final = self._round_history[-1]
        if final.passed_gate:
            lines.append("Quality gate passed.")
        elif self._termination_reason == "stall":
            lines.append(
                "Refinement stalled: remaining issues repeated across consecutive rounds."
            )
            if final.remaining_issues:
                lines.append(f"Outstanding issues: {', '.join(final.remaining_issues)}")
        elif final.remaining_issues:
            lines.append(
                f"Iteration cap reached. Outstanding issues: "
                f"{', '.join(final.remaining_issues)}"
            )
        else:
            lines.append("Iteration cap reached.")

        return "\n".join(lines)

    def _pause_after_round(
        self,
        ctx: ExecutionContext,
        *,
        completed_rounds: int,
    ) -> ExecutionResult | None:
        budgets = getattr(ctx.state, "budgets_remaining", None)
        if budgets is None:
            return None
        budgets.ticks = max(0, int(getattr(budgets, "ticks", 0) or 0) - 1)
        if (
            completed_rounds >= self._max_refine_iterations
            or int(budgets.ticks or 0) > 0
        ):
            return None
        self._finalize_checkpoint(ctx, terminal=False, cursor=completed_rounds)
        transition(ctx.state, "checkpoint_reached", logger=ctx.logger)
        return ExecutionResult.from_step_output(
            ctx.respond(
                message=(
                    f"Refinement paused after round {completed_rounds}. "
                    "Continue in a new turn to resume."
                ),
                status=BRAIN_STATE_WAITING_USER,
            )
        )
