from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from openminion.modules.brain.checkpoint import SimpleCheckpointMixin
from openminion.modules.brain.constants import (
    BRAIN_INTERNAL_MODE_LOOP_PHASE_EVAL,
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
from openminion.modules.brain.constants import STATE_KEY_TASK_BACKED_RESUME
from .child_execution import (
    build_child_state,
    execute_child_goal,
    normalized_text,
    plan_objective_fallback,
    split_budget_evenly,
)

EVAL_MODE = BRAIN_INTERNAL_MODE_LOOP_PHASE_EVAL

_VERDICT_ALIASES = {
    "ok": "pass",
    "okay": "pass",
    "pass": "pass",
    "passed": "pass",
    "success": "pass",
    "succeeded": "pass",
    "true": "pass",
    "yes": "pass",
    "fail": "fail",
    "failed": "fail",
    "failure": "fail",
    "error": "fail",
    "errors": "fail",
    "false": "fail",
    "no": "fail",
    "partial": "partial",
    "partially": "partial",
    "mixed": "partial",
    "warn": "partial",
    "warning": "partial",
    "warnings": "partial",
    "unknown": "partial",
    "unclear": "partial",
    "inconclusive": "partial",
    "na": "partial",
    "n/a": "partial",
    "acceptable-partial": "partial",
    "acceptable_partial": "partial",
}


def _normalize_verdict(value: object) -> str:
    text = str(value or "").strip().lower().replace("_", "-")
    return _VERDICT_ALIASES.get(text, "partial")


class EvalPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    eval_target: str = Field(..., min_length=1)
    eval_criteria: list[str] = Field(default_factory=list)


class EvalCriterion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1)
    description: str = ""
    verdict: str = Field(...)
    evidence: str = ""
    notes: str = ""

    @field_validator("verdict", mode="before")
    @classmethod
    def _normalize_criterion_verdict(cls, value: object) -> str:
        return _normalize_verdict(value)


class EvalJudgment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str
    criteria: list[EvalCriterion] = Field(default_factory=list)
    overall_verdict: str = Field(default="partial")
    summary: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @field_validator("overall_verdict", mode="before")
    @classmethod
    def _normalize_overall_verdict(cls, value: object) -> str:
        return _normalize_verdict(value)


class _EvalJudgmentPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str = ""
    criteria: list[EvalCriterion] = Field(default_factory=list)
    overall_verdict: str = "partial"
    summary: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class EvalMode(SimpleCheckpointMixin):
    CHECKPOINT_VERSION = 1
    mode_name = EVAL_MODE
    mode_description = (
        "evaluate an artifact against criteria — gathers evidence by reading "
        "files, running tests, or fetching content via a bounded child-mode "
        "execution, then produces a structured judgment with per-criterion "
        "pass/fail/partial verdicts and an overall verdict. Use for code review, "
        "spec validation, output quality checks, or any task that requires "
        "reading and judging rather than acting."
    )
    mode_category = "assessment"
    has_prepare = True
    has_validate = True
    has_resume = True
    priority_hint = 65
    mode_thinking_policy = {
        "default_reasoning_profile": "detailed",
        "allowed_reasoning_profiles": ("minimal", "detailed"),
        "allow_request_override": True,
    }
    decision_payload_fields = {
        "eval_target": (str, Field(default="", description="What to evaluate")),
        "eval_criteria": (
            list[str],
            Field(
                default_factory=list,
                description="Criteria to evaluate against",
            ),
        ),
    }

    def __init__(self) -> None:
        self._cached_evidence = ""

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
            feedback="Eval mode requires a non-empty eval_target.",
            code="missing_eval_target",
        )

    def execute(self, ctx: ExecutionContext) -> ExecutionResult:
        target = self._target_from_context(ctx)
        if not target:
            return ExecutionResult.from_step_output(
                ctx.respond(
                    message="Eval mode needs a concrete eval_target before it can run.",
                    status=BRAIN_STATE_WAITING_USER,
                )
            )

        self._init_checkpoint(ctx)
        criteria = self._criteria_from_context(ctx)
        resume_state = {
            key: value
            for key, value in dict(
                getattr(ctx.state, STATE_KEY_TASK_BACKED_RESUME, {}) or {}
            ).items()
            if not key.startswith("_checkpoint_")
        }
        if resume_state:
            self.restore_state(resume_state)
        evidence = self._cached_evidence or self._gather_evidence(
            ctx, target=target, criteria=criteria
        )
        self._cached_evidence = evidence
        self._save_checkpoint(ctx, cursor=1)
        ctx.state.budgets_remaining.ticks = max(
            0, int(ctx.state.budgets_remaining.ticks or 0) - 1
        )
        if int(ctx.state.budgets_remaining.ticks or 0) <= 0:
            self._finalize_checkpoint(ctx, terminal=False, cursor=1)
            return ExecutionResult.from_step_output(
                ctx.respond(
                    message=(
                        "Evidence gathered. Continue in a new turn to complete the evaluation."
                    ),
                    status=BRAIN_STATE_WAITING_USER,
                )
            )
        judgment = self._judge(ctx, target=target, criteria=criteria, evidence=evidence)
        message = self._format_judgment(judgment)

        self._finalize_checkpoint(ctx, terminal=True, cursor=1)
        transition(ctx.state, "task_completed", logger=ctx.logger)
        return ExecutionResult.from_step_output(
            ctx.respond(message=message, status=BRAIN_STATE_DONE)
        )

    def snapshot_state(self) -> dict[str, Any]:
        return {"evidence": self._cached_evidence}

    def restore_state(self, payload: dict[str, Any]) -> None:
        self._cached_evidence = normalized_text(dict(payload or {}).get("evidence"))

    def _target_from_context(self, ctx: ExecutionContext) -> str:
        return (
            normalized_text(getattr(ctx.decision, "eval_target", "") or "")
            or normalized_text(getattr(ctx.decision, "objective", "") or "")
            or normalized_text(getattr(ctx.state, "goal", "") or "")
            or normalized_text(ctx.user_input or "")
        )

    def _criteria_from_context(self, ctx: ExecutionContext) -> list[str]:
        raw = getattr(ctx.decision, "eval_criteria", None)
        if isinstance(raw, list):
            return [str(c) for c in raw if str(c).strip()]
        return []

    def _gather_evidence(
        self,
        ctx: ExecutionContext,
        *,
        target: str,
        criteria: list[str],
    ) -> str:
        criteria_text = ", ".join(criteria) if criteria else "general quality"
        child_goal = (
            f"Examine {target!r} and report findings relevant to: {criteria_text}. "
            "Read the artifact, run any applicable checks, and summarize observations."
        )

        child_state = build_child_state(
            parent_state=ctx.state,
            child_budget=self._evidence_budget(ctx),
            goal=child_goal,
        )
        content = execute_child_goal(
            ctx,
            child_goal=child_goal,
            child_state=child_state,
            blocked_mode_name=EVAL_MODE,
            fallback_reason_code="eval_evidence_fallback",
        )
        return content or plan_objective_fallback(
            ctx,
            child_goal=child_goal,
            default=f"Evidence gathered for {target!r}.",
        )

    def _evidence_budget(self, ctx: ExecutionContext) -> BudgetCounters:
        return split_budget_evenly(
            budgets=ctx.state.budgets_remaining,
            divisor=2,
        )

    def _judge(
        self,
        ctx: ExecutionContext,
        *,
        target: str,
        criteria: list[str],
        evidence: str,
    ) -> EvalJudgment:
        criteria_text = (
            "\n".join(f"- {c}" for c in criteria)
            if criteria
            else "Infer appropriate criteria from the artifact and evidence."
        )
        prompt = (
            f"Evaluate {target!r} against the following criteria:\n"
            f"{criteria_text}\n\n"
            f"Evidence gathered:\n{evidence}\n\n"
            "Produce a structured judgment as JSON matching this schema:\n"
            '{"target": "...", "criteria": [{"name": "...", "description": "...", '
            '"verdict": "pass|fail|partial", "evidence": "...", "notes": "..."}], '
            '"overall_verdict": "pass|fail|partial", "summary": "...", '
            '"confidence": 0.0}\n'
            "Set overall_verdict to pass only if all criteria pass or are acceptable-partial."
        )

        try:
            structured = structured_mode_response(
                ctx,
                prompt=prompt,
                schema=_EvalJudgmentPayload,
                purpose="reflect",
            )
            if structured is None:
                raise ValueError("empty response")
            return EvalJudgment(
                target=target,
                criteria=list(structured.criteria),
                overall_verdict=structured.overall_verdict,
                summary=structured.summary,
                confidence=structured.confidence,
            )
        except Exception:
            return EvalJudgment(
                target=target,
                overall_verdict="partial",
                summary="Could not produce structured judgment.",
                confidence=0.0,
            )

    def _format_judgment(self, judgment: EvalJudgment) -> str:
        lines = [f"Evaluation of {judgment.target!r}", ""]
        if judgment.criteria:
            lines.append("Criteria:")
            for c in judgment.criteria:
                verdict_tag = c.verdict.upper()
                lines.append(f"  [{verdict_tag}] {c.name}")
                if c.evidence:
                    lines.append(f"         {c.evidence}")
            lines.append("")
        lines.append(f"Overall verdict: {judgment.overall_verdict.upper()}")
        if judgment.summary:
            lines.append(f"Summary: {judgment.summary}")
        lines.append(f"Confidence: {judgment.confidence:.0%}")
        return "\n".join(lines)
