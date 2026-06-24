from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class PostCompletionCritique(BaseModel):
    """Typed model-authored critique linked to one completed intent."""

    model_config = ConfigDict(extra="forbid")

    intent_id: str = Field(min_length=1)
    summary: Annotated[str, Field(min_length=1, max_length=200)]
    lessons: list[Annotated[str, Field(min_length=1, max_length=120)]] = Field(
        default_factory=list
    )
    next_time_action: Annotated[str | None, Field(max_length=120)] = None


class ReviewFact(BaseModel):
    """Review summary facts."""

    model_config = ConfigDict(extra="forbid")

    invoked: bool = False
    findings_count: int = Field(default=0, ge=0)
    severity: Literal["ok", "warn", "block", "unavailable"] = "unavailable"


class VerificationFact(BaseModel):
    """Verification summary facts."""

    model_config = ConfigDict(extra="forbid")

    signal: Literal["tests", "types", "build", "user", "unavailable"] = "unavailable"
    exit_code: int | None = None
    ok: bool = True
    probed_tool: str = ""


class PlanReconciliationFact(BaseModel):
    """Plan reconciliation summary facts."""

    model_config = ConfigDict(extra="forbid")

    state: Literal["complete", "incomplete"] = "complete"
    unreconciled_items: int = Field(default=0, ge=0)
    unreconciled_step_ids: tuple[str, ...] = Field(default_factory=tuple)


class ClosureJudgment(BaseModel):
    model_config = ConfigDict(extra="ignore")

    satisfied: bool = True
    reason: str = ""
    next_action: Literal["close", "continue", "replan"] = "close"
    final_answer: str | None = None
    post_completion_critique: PostCompletionCritique | None = None
    plan_reconciliation: PlanReconciliationFact | None = None
    verification: VerificationFact | None = None
    review: ReviewFact | None = None
