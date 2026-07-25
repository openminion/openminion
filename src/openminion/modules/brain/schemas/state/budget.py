# ruff: noqa: F403,F405
from .common import *

class BudgetCounters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticks: int = Field(..., ge=0)
    tool_calls: int = Field(..., ge=0)
    a2a_calls: int = Field(..., ge=0)
    tokens: int = Field(..., ge=0)
    time_ms: int = Field(..., ge=0)

class BudgetTelemetryBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    iteration_used: int = Field(default=0, ge=0)
    iteration_remaining: int = Field(default=0, ge=0)
    iteration_max: int = Field(default=0, ge=0)
    tool_calls_used: int = Field(default=0, ge=0)
    tool_calls_remaining: int = Field(default=0, ge=0)
    tool_calls_max: int = Field(default=0, ge=0)
    token_used: int | None = Field(default=None, ge=0)
    token_remaining: int | None = Field(default=None, ge=0)
    token_max: int | None = Field(default=None, ge=0)
    time_elapsed_ms: int | None = Field(default=None, ge=0)
    time_remaining_ms: int | None = Field(default=None, ge=0)
    budget_envelope_status: BudgetEnvelopeStatus = "comfortable"

class LearningLoopMetric(BaseModel):
    """Typed learning-loop metric surfaced into the context-pack."""

    model_config = ConfigDict(extra="forbid")

    readiness: LearningLoopMetricReadiness = "partial"
    improvement_note_count: int = Field(default=0, ge=0)
    strategy_outcome_count: int = Field(default=0, ge=0)
    decision_memory_ref_count: int = Field(default=0, ge=0)
    cross_session_strategy_outcomes_present: bool = False

class MissionBudgetEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_remaining: BudgetCounters
    per_turn_max: BudgetCounters
    remaining_llm_calls_total: int = Field(default=0, ge=0)
    llm_calls_per_turn_max: int = Field(default=0, ge=0)
    turn_budget_baseline: BudgetCounters | None = None
    turn_budget_allocated: BudgetCounters | None = None
    turn_llm_calls_baseline_total: int | None = Field(default=None, ge=0)
    turns_started: int = Field(default=0, ge=0)
