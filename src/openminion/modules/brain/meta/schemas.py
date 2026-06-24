from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MetaState(str, Enum):
    NORMAL = "NORMAL"
    CAUTIOUS = "CAUTIOUS"
    HIGH_ASSURANCE = "HIGH_ASSURANCE"
    RECOVERY = "RECOVERY"
    PANIC = "PANIC"


class VerificationMode(str, Enum):
    none = "none"
    rule_based = "rule_based"
    second_opinion = "second_opinion"
    panel_judge = "panel_judge"


class MetaMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = ""
    agent_id: str = ""
    trace_id: str = ""

    state: str = ""
    planned_next_state: str = ""
    tier: str = ""

    risk_class: Literal["low", "medium", "high"] = "low"
    risk_score: int = 0
    requires_side_effects: bool = False
    irreversible: bool = False

    intent_confidence: float = 0.7
    grounding_confidence: float = 1.0
    unknown_fields_count: int = 0

    recent_failures: int = 0
    loop_count: int = 0
    replan_count: int = 0
    ticks_without_progress: int = 0
    no_new_facts_streak: int = 0
    steps_completed_recent: int = 0
    contradiction_flags: list[str] = Field(default_factory=list)
    candidate_disagreement_score: float = 0.0
    requires_evidence_only: bool = False

    policy_recent_denies: int = 0
    policy_recent_confirms: int = 0

    budget_remaining: float = 1.0

    last_verify_outcome: Literal["pass", "fail", "skip", ""] = ""

    recent_state_path: list[str] = Field(default_factory=list)

    needs_clarification: bool = False
    user_kill_requested: bool = False
    ambiguity_score: float = 0.0

    tool_success_rate_ewma: float = 1.0
    tool_timeout_count_recent: int = 0
    tool_auth_error_count_recent: int = 0

    llm_calls_used: int = 0
    llm_calls_max: int = 8
    tool_calls_used: int = 0
    tool_calls_max: int = 8
    budget_pressure: float = 0.0

    user_corrected_me_recently: bool = False
    user_requested_thoroughness: bool = False
    user_requested_brevity: bool = False

    @field_validator(
        "intent_confidence",
        "grounding_confidence",
        "budget_remaining",
        "ambiguity_score",
        "tool_success_rate_ewma",
        "candidate_disagreement_score",
        "budget_pressure",
    )
    @classmethod
    def _clamp01(cls, value: float) -> float:
        return max(0.0, min(1.0, float(value)))

    @field_validator("risk_score")
    @classmethod
    def _clamp100(cls, value: int) -> int:
        return max(0, min(100, int(value)))

    @field_validator(
        "recent_failures",
        "loop_count",
        "replan_count",
        "ticks_without_progress",
        "no_new_facts_streak",
        "steps_completed_recent",
        "unknown_fields_count",
        "policy_recent_denies",
        "policy_recent_confirms",
        "tool_timeout_count_recent",
        "tool_auth_error_count_recent",
        "llm_calls_used",
        "llm_calls_max",
        "tool_calls_used",
        "tool_calls_max",
    )
    @classmethod
    def _non_negative(cls, value: int) -> int:
        return max(0, int(value))


class BudgetAdjust(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lower_context_limits: bool = False
    raise_context_limits: bool = False
    lower_llm_calls_max: int | None = None
    raise_llm_calls_max: int | None = None
    lower_tool_calls_max: int | None = None
    raise_tool_calls_max: int | None = None


class MetaDirective(BaseModel):
    model_config = ConfigDict(extra="forbid")

    override_next_state: (
        Literal["WAITING", "PLAN", "VERIFY", "RESPOND", "STOPPED"] | None
    ) = None

    tier_override: (
        Literal["T0_direct", "T1_light", "T2_tool", "T3_high_assurance"] | None
    ) = None

    require_confirmation: bool = False
    require_verification: bool = False
    verification_mode: VerificationMode = VerificationMode.none
    require_clarification: bool = False
    clarification_question: str | None = None

    temporary_tool_policy: dict[str, list[str]] | None = (
        None  # {"allow": [...], "deny": [...]}
    )
    tool_temp_denylist: list[str] = Field(default_factory=list)
    tool_temp_allowlist: list[str] = Field(default_factory=list)

    budget_adjustments: BudgetAdjust | None = None

    prompt_constraints: list[str] = Field(default_factory=list)

    ttl_ticks: int | None = None

    escalation_question: str | None = None
    note_to_user: str | None = None


class LowProgressSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    iterations_without_new_typed_record: int = Field(default=0, ge=0)
    repeated_arg_signature_count: int = Field(default=0, ge=0)
    unique_tool_call_count_delta: int = Field(default=0, ge=0)


class MetaResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    meta_state: MetaState
    directive: MetaDirective
    metrics: MetaMetrics
    reasons: list[str] = Field(default_factory=list)
    ruleset_version: str = "metactl.v1"


class MetaConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ruleset_version: str = "metactl.v1"

    high_risk_score_threshold: int = 70
    medium_risk_score_threshold: int = 40

    low_grounding_threshold: float = 0.5
    low_intent_confidence_threshold: float = 0.6
    high_ambiguity_threshold: float = 0.7

    repeat_failure_threshold: int = 2
    loop_count_threshold: int = 3
    replan_count_threshold: int = 3
    low_progress_ticks_threshold: int = 3
    low_progress_no_new_facts_threshold: int = 2
    low_progress_iterations_without_new_typed_record_threshold: int = 3
    low_progress_repeated_arg_signature_threshold: int = 2
    low_progress_unique_tool_call_count_delta_threshold: int = 2

    budget_pressure_threshold: float = 0.8

    tool_degraded_threshold: float = 0.8

    high_risk_verification_mode: VerificationMode = VerificationMode.panel_judge
    medium_risk_verification_mode: VerificationMode = VerificationMode.rule_based
