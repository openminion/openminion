"""Typed contracts and conservative policy for online brain self-improvement."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SELF_IMPROVEMENT_POLICY_DISABLED: Literal["never"] = "never"

SelfImprovementAction = Literal[
    "ignore",
    "retry_now",
    "replan_now",
    "ask_user",
    "stage_lesson",
    "stage_candidate",
]
SelfImprovementMemoryKind = Literal[
    "lesson",
    "procedure",
    "preference",
    "goal_revision",
    "failure_pattern",
    "strategy_outcome",
    "none",
]
SelfImprovementOutcomeStatus = Literal[
    "success", "failure", "partial", "blocked", "other"
]
SelfImprovementProgressDelta = Literal["positive", "flat", "negative", "unknown"]
LoopPolicyPromotionVerdictValue = Literal["promote", "hold", "rollback", "suppress"]


class OnlineImprovementEval(BaseModel):
    """One typed evaluation of a single live-loop attempt or step.

    Produced by the runtime (BSIL-02) when a non-``never`` policy is active.
    Carries only structural signals (status, anomaly score, progress delta,
    evidence refs) — never free-form prose used as a routing key.
    """

    model_config = ConfigDict(extra="forbid")

    attempt_id: str
    trace_id: str
    mode_name: str
    tool_name: str = ""
    iteration: int = Field(default=0, ge=0)
    anomaly_score: float = Field(default=0.0, ge=0.0)
    outcome_status: SelfImprovementOutcomeStatus
    failure_reason_code: str = ""
    progress_delta: SelfImprovementProgressDelta = "unknown"
    evidence_refs: list[str] = Field(default_factory=list)


class ImprovementDecision(BaseModel):
    """Typed decision derived from an :class:`OnlineImprovementEval`.

    ``retry_now``/``replan_now``/``ask_user`` stay inside the live loop;
    ``stage_lesson``/``stage_candidate`` flow through the memory module
    bridge (BSIL-03). ``ignore`` is the conservative default action so an
    unconfigured or low-confidence path is a no-op.
    """

    model_config = ConfigDict(extra="forbid")

    action: SelfImprovementAction = "ignore"
    rationale_code: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    # `memory_kind` is the OpenMinion-side durable-memory handoff vocabulary; it
    # must stay aligned with the sibling durable-memory lifecycle contract.
    # `none` means the live loop made no durable-memory recommendation. Note
    # that `stage_candidate` is an `action` (a staging verb), not a memory kind.
    memory_kind: SelfImprovementMemoryKind = "none"
    tags: list[str] = Field(default_factory=list)


class SelfImprovementPolicy(BaseModel):
    """Bounded operator policy for online self-improvement.

    All defaults are conservative: ``policy="never"`` disables the online
    evaluator entirely, ``review_mode="review_first"`` keeps even an enabled
    policy from auto-promoting, and the numeric budgets default to the
    smallest safe values. No field authorizes live code mutation.
    """

    model_config = ConfigDict(extra="forbid")

    policy: Literal["never", "anomaly", "checkpoint", "post_run"] = "never"
    reserved_llm_calls: int = Field(default=0, ge=0)
    max_staged_items_per_run: int = Field(default=0, ge=0)
    review_mode: Literal["automatic", "review_first"] = "review_first"
    min_external_signal_count: int = Field(default=1, ge=0)

    @property
    def is_enabled(self) -> bool:
        """True only when opted into a non-``never`` online policy."""
        return self.policy != SELF_IMPROVEMENT_POLICY_DISABLED


class SelfImprovementReplayBundle(BaseModel):
    """Replay/eval evidence bundle for comparing loop-policy candidates."""

    model_config = ConfigDict(extra="forbid")

    bundle_id: str
    trace_ids: list[str] = Field(default_factory=list)
    eval_rows: list[dict[str, Any]] = Field(default_factory=list)
    candidate_ids: list[str] = Field(default_factory=list)
    baseline_metrics: dict[str, float] = Field(default_factory=dict)
    challenger_metrics: dict[str, float] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)


class LoopPolicyPromotionVerdict(BaseModel):
    """OpenMinion-owned verdict for prompt/routing/loop-policy candidates."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    verdict: LoopPolicyPromotionVerdictValue
    reason_code: str
    supporting_metrics: dict[str, float] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)


__all__ = [
    "SELF_IMPROVEMENT_POLICY_DISABLED",
    "ImprovementDecision",
    "LoopPolicyPromotionVerdict",
    "LoopPolicyPromotionVerdictValue",
    "OnlineImprovementEval",
    "SelfImprovementAction",
    "SelfImprovementMemoryKind",
    "SelfImprovementOutcomeStatus",
    "SelfImprovementPolicy",
    "SelfImprovementProgressDelta",
    "SelfImprovementReplayBundle",
]
