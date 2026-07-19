from __future__ import annotations

from typing import Any, Literal
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from openminion.modules.brain.constants import (
    RESPOND_KIND_ASSISTANT,
    MissionStatus,
    RespondKindLiteral,
)
from .base import ActionStatus, ArtifactRef, iso_now, new_uuid
from .commands import Command
from .decisions import ClarifyContext, PendingTurnContext, RequestReadiness
from .freshness import (
    FreshnessContract,
    FreshnessDiagnostics,
    FreshnessObligations,
)
from .plan import (
    AdaptiveRevisionCheckpoint,
    FailureType,
    FeasibilityReport,
    FixItem,
    IntentExecutionState,
    Plan,
    ProgressCheckpointReport,
    ReflectOutcome,
    StepRiskAssessment,
    SubIntent,
    build_intent_execution_states,
    feasibility_report_payload,
    normalize_sub_intent_ids,
    select_sub_intents_by_ids,
    sub_intent_descriptions,
    to_structured_sub_intents,
)


CognitionTier = Literal["T0_direct", "T1_light", "T2_tool", "T3_high_assurance"]
WorkingStatus = Literal[
    "active",
    "continue",
    "waiting_user",
    "job_pending",
    "done",
    "error",
    "stopped",
]
MissionLifecycleStatus = MissionStatus
MissionJudgmentOutcome = Literal["complete", "continue", "ask_user", "halt"]
PostActionJudgmentOutcome = Literal[
    "advance",
    "retry",
    "replan",
    "ask_user",
    "halt",
    "skip",
]
PermissionMode = Literal[
    "ask",
    "auto",
    "bypass",
    "readonly",
    "plan",
    "default",
    "acceptEdits",
    "bypassPermissions",
]
RunSubstate = Literal[
    "INTERPRET",
    "CLARIFY",
    "DECIDE",
    "PLAN",
    "APPROVE",
    "ACT",
    "OBSERVE",
    "VERIFY",
    "REFLECT",
    "IMPROVE",
    "COMPACT",
    "RESPOND",
]
ClarifyQuestionType = Literal[
    "missing_field",
    "ambiguous_input",
    "risk_confirmation",
    "constraint_check",
    "tool_permission",
]


class BrainMode(str, Enum):
    COMMAND = "command"
    GUIDED = "guided"
    AUTONOMOUS = "autonomous"
    BATCH = "batch"


class ClarifyPolicy(str, Enum):
    ALWAYS_ASK = "always_ask"
    ASK_IF_AMBIGUOUS = "ask_if_ambiguous"
    ASK_IF_RISKY = "ask_if_risky"
    ASSUME_DEFAULTS = "assume_defaults"
    SMART_ASSUME = "smart_assume"


class BudgetStopReason(str, Enum):
    TICKS_EXHAUSTED = "ticks_exhausted"
    TIME_EXHAUSTED = "time_exhausted"


class ClarifyQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=new_uuid, min_length=1)
    type: ClarifyQuestionType
    question: str = Field(..., min_length=1)
    description: str = ""
    options: list[str] | None = None
    default_value: str | None = None
    is_blocking: bool = True
    reason_code: str = ""
    source: str = ""
    requires_validation: bool = False
    confidence_threshold: float = Field(default=0.5, ge=0.0, le=1.0)


class ClarifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(..., min_length=1)
    trace_id: str = Field(..., min_length=1)
    questions: list[ClarifyQuestion] = Field(default_factory=list)
    mode: BrainMode
    policy: ClarifyPolicy
    reason: str = ""
    context_snapshot: dict[str, Any] | None = None
    deadline: str | None = None


class ClarifyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(..., min_length=1)
    trace_id: str = Field(..., min_length=1)
    answers: dict[str, str] = Field(default_factory=dict)
    unanswered_ids: list[str] = Field(default_factory=list)


class BudgetCounters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticks: int = Field(..., ge=0)
    tool_calls: int = Field(..., ge=0)
    a2a_calls: int = Field(..., ge=0)
    tokens: int = Field(..., ge=0)
    time_ms: int = Field(..., ge=0)


BudgetEnvelopeStatus = Literal["comfortable", "tight", "near_exhaustion"]
LearningLoopMetricReadiness = Literal["ready", "partial"]


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


class MissionJudgment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: MissionJudgmentOutcome = "continue"
    reason: str = ""
    final_answer: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class PostActionJudgment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: PostActionJudgmentOutcome
    reason: str = ""
    user_message: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class MissionState(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    mission_id: str = Field(..., min_length=1)
    objective: str = Field(..., min_length=1)
    status: MissionLifecycleStatus = MissionStatus.ACTIVE
    started_at: str = Field(default_factory=iso_now)
    last_progress_at: str | None = None
    completed_at: str | None = None
    task_id: str | None = None
    budget: MissionBudgetEnvelope
    completion_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    latest_judgment: MissionJudgment | None = None
    latest_reason: str = ""
    latest_reset_policy: str = ""
    latest_route_action: str = ""


class ActionError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(..., min_length=1)
    message: str = Field(..., min_length=1)
    details: dict[str, Any] = Field(default_factory=dict)


class ActionMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    latency_ms: int | None = Field(default=None, ge=0)
    tokens_used: int | None = Field(default=None, ge=0)
    cost_estimate: float | None = Field(default=None, ge=0)


class ActionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command_id: str = Field(..., min_length=1)
    status: ActionStatus
    summary: str = ""
    outputs: dict[str, Any] = Field(default_factory=dict)
    artifact_refs: list[ArtifactRef] = Field(default_factory=list)
    memory_refs: list[str] = Field(default_factory=list)
    error: ActionError | None = None
    metrics: ActionMetrics | None = None


class JobHandle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(..., min_length=1)
    command_id: str = Field(..., min_length=1)
    provider: Literal["tool", "a2actl"]
    status: Literal["pending", "running", "done", "failed"]
    poll_after_ms: int = Field(default=1000, ge=1)
    created_at: str = Field(default_factory=iso_now)


class ReflectReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(..., min_length=1)
    agent_id: str = Field(..., min_length=1)
    command_id: str = Field(..., min_length=1)
    outcome: ReflectOutcome
    failure_type: FailureType | None = None
    root_cause: str = ""
    evidence_refs: list[ArtifactRef] = Field(default_factory=list)
    fixes: list[FixItem] = Field(default_factory=list)


class PolicyDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Literal[
        "ALLOW",
        "DENY",
        "REQUIRE_CONFIRMATION",
        "MODIFY",
        "REQUIRE_CLARIFICATION",
    ]
    explanation: str = ""
    patched_command: Command | None = None
    require_clarification: bool = False
    clarification_question: str | None = None


class StepOutputEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_index: int = Field(default=0, ge=0)
    command_id: str = Field(..., min_length=1)
    output_key: str = ""
    summary: str = ""
    sub_intent_ids: list[str] = Field(default_factory=list)
    outputs: dict[str, Any] = Field(default_factory=dict)
    artifact_refs: list[str] = Field(default_factory=list)

    @field_validator("sub_intent_ids", mode="before")
    @classmethod
    def _normalize_sub_intent_ids(cls, value: Any) -> Any:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, tuple):
            return list(value)
        return value

    @model_validator(mode="after")
    def _dedupe_sub_intent_ids(self) -> "StepOutputEntry":
        self.sub_intent_ids = normalize_sub_intent_ids(self.sub_intent_ids)
        return self


class MetaDirectiveLogEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hook: str = Field(..., min_length=1)
    meta_state: str = Field(..., min_length=1)
    applied_at: str = Field(default_factory=iso_now)
    directive: dict[str, Any] = Field(default_factory=dict)


def _normalize_skill_ids(values: object) -> list[str]:
    if values is None:
        return []
    raw_values = values if isinstance(values, list) else [values]
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_value in raw_values:
        text = str(raw_value or "").strip()
        if not text:
            continue
        lowered = text.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        normalized.append(text)
    return normalized


class WorkingState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _drop_retired_runtime_clarification_state(cls, data: Any) -> Any:
        if isinstance(data, dict) and "runtime_clarification_state" in data:
            payload = dict(data)
            payload.pop("runtime_clarification_state", None)
            return payload
        return data

    session_id: str = Field(..., min_length=1)
    agent_id: str = Field(..., min_length=1)
    goal: str | None = None
    active_goal_id: str | None = None
    last_user_input: str = ""
    active_mode_name: str | None = None
    active_skill_id: str | None = None
    active_skill_ids: list[str] = Field(default_factory=list)
    active_skill_version_hash: str | None = None
    resolved_skill_ids: list[str] = Field(default_factory=list)
    resolved_skill_versions: dict[str, str] = Field(default_factory=dict)
    permission_mode: PermissionMode = "default"
    permission_overrides: dict[str, str] = Field(default_factory=dict)
    tier: CognitionTier = "T1_light"
    llm_calls_used: int = Field(default=0, ge=0)
    llm_calls_max: int = Field(default=8, ge=1)
    meta_state: str = "NORMAL"
    constraints: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    plan: Plan | None = None
    cursor: int = Field(default=0, ge=0)
    consecutive_step_failures: int = Field(default=0, ge=0)
    last_checkpoint_cursor: int = Field(default=-1)
    status: WorkingStatus = "active"
    budgets_remaining: BudgetCounters
    last_command_id: str | None = None
    last_result: ActionResult | None = None
    post_action_user_message: str = ""
    step_outputs: list[StepOutputEntry] = Field(default_factory=list)
    adaptive_satisfied_intent_ids: list[str] = Field(default_factory=list)
    last_adaptive_revision_checkpoint: AdaptiveRevisionCheckpoint | None = None
    last_progress_checkpoint: ProgressCheckpointReport | None = None
    last_step_risk_assessment: StepRiskAssessment | None = None
    recent_artifacts: list[ArtifactRef] = Field(default_factory=list)
    pending_jobs: list[JobHandle] = Field(default_factory=list)
    mission: MissionState | None = None
    reflection_backlog: list[str] = Field(default_factory=list)
    memory_candidates: list[str] = Field(default_factory=list)
    idempotency_cache: dict[str, ActionResult] = Field(default_factory=dict)
    phase: RunSubstate | None = None
    trace_id: str | None = None
    # trigger that initiated the current `run_until_idle` entry.
    run_trigger: str = "user_input"
    retries_for_step: dict[str, int] = Field(default_factory=dict)
    replans_used: int = Field(default=0, ge=0)
    meta_logs: list[MetaDirectiveLogEntry] = Field(default_factory=list)
    pending_clarify_items: list[ClarifyQuestion] = Field(default_factory=list)
    unresolved_clarify_items: list[ClarifyQuestion] = Field(default_factory=list)
    clarify_responses: dict[str, str] = Field(default_factory=dict)
    clarify_resume_cursor: str | None = None
    pending_llm_clarify_context: ClarifyContext | None = None
    pending_turn_context: PendingTurnContext | None = Field(
        default=None,
        description=(
            "Stored model-authored carry-forward context for the next user turn. "
            "Runtime preserves and re-injects this opaquely until the model "
            "explicitly replaces or clears it, or the typed staleness guard ages "
            "it out."
        ),
    )
    pending_turn_context_stale_turns: int = Field(default=0, ge=0)
    request_readiness: RequestReadiness | None = Field(
        default=None,
        description=(
            "Single persisted copy of the latest validated high-level request "
            "readiness payload while request handoff is enabled."
        ),
    )
    session_work_summary: str | None = Field(
        default=None,
        description=(
            "Optional model-authored session-level work checkpoint. Runtime stores "
            "and re-injects this opaquely across turns within the current session."
        ),
    )
    continuation_guard_command_signature: str | None = None
    continuation_guard_reason: str = ""
    awaiting_continuation_reply: bool = False
    active_workflow_name: str | None = None
    active_workflow_kind: str | None = None
    mode: BrainMode = BrainMode.COMMAND
    pending_confirmation_command: Command | None = None
    pending_confirmation_sub_intents: list[str] = Field(default_factory=list)
    pending_confirmation_sub_intent_refs: list[SubIntent] = Field(default_factory=list)
    pending_confirmation_goal: str | None = None
    pending_confirmation_last_user_input: str = ""
    pending_confirmation_rationale: str = ""
    pending_confirmation_success_criteria: dict[str, Any] = Field(default_factory=dict)
    pending_confirmation_feasibility_state: dict[str, Any] = Field(default_factory=dict)
    pending_confirmation_feasibility_report: FeasibilityReport | None = None
    session_action_policy_mode_override: str | None = None
    session_skill_loaded: list[str] = Field(default_factory=list)
    session_skill_unloaded: list[str] = Field(default_factory=list)
    skill_selection_mode: str | None = None
    decision_reason_code: str = ""
    decision_capability_category: str | None = None
    decision_sub_intents: list[str] = Field(default_factory=list)
    decision_sub_intent_refs: list[SubIntent] = Field(default_factory=list)
    decision_rationale: str = ""
    decision_success_criteria: dict[str, Any] = Field(default_factory=dict)
    decision_feasibility_state: dict[str, Any] = Field(default_factory=dict)
    decision_feasibility_report: FeasibilityReport | None = None
    working_act_profile: str | None = None
    working_execution_target_kind: str | None = None
    working_route_source: str | None = None
    decision_memory_refs: list[str] = Field(default_factory=list)
    decision_context_pack_version: str | None = None
    decision_context_recorded_at: str | None = None
    gateway_system_context: str = ""
    freshness_contract: FreshnessContract | None = None
    freshness_obligations: FreshnessObligations | None = None
    freshness_diagnostics: FreshnessDiagnostics | None = None
    resume_task_id_hint: str | None = None
    resume_cron_job_id_hint: str | None = None
    child_tasks: dict[str, str] = Field(default_factory=dict)
    child_task_order: list[str] = Field(default_factory=list)
    module_state: dict[str, dict[str, Any]] = Field(default_factory=dict)
    task_backed_task_id: str | None = None
    task_backed_checkpoint_id: str | None = None
    task_backed_resume_state: dict[str, Any] = Field(default_factory=dict)
    delegation_job_id: str | None = None
    delegation_task_id: str | None = None
    delegation_target_agent_id: str | None = None
    delegation_goal: str = ""
    delegation_synthesize_result: bool = False
    intent_execution_states: list[IntentExecutionState] = Field(default_factory=list)
    policy: ClarifyPolicy = ClarifyPolicy.ALWAYS_ASK

    @field_validator("adaptive_satisfied_intent_ids", mode="before")
    @classmethod
    def _normalize_adaptive_satisfied_intent_ids(cls, value: Any) -> Any:
        return normalize_sub_intent_ids(value)

    @field_validator("active_skill_ids", "resolved_skill_ids", mode="before")
    @classmethod
    def _normalize_skill_id_lists(cls, value: Any) -> Any:
        return _normalize_skill_ids(value)

    @field_validator("active_goal_id", mode="before")
    @classmethod
    def _normalize_active_goal_id(cls, value: Any) -> str | None:
        text = str(value or "").strip()
        return text or None

    @model_validator(mode="after")
    def _sync_clarify_fields(self) -> "WorkingState":
        active_ids = _normalize_skill_ids(self.active_skill_ids)
        resolved_ids = _normalize_skill_ids(self.resolved_skill_ids)
        current_active = str(self.active_skill_id or "").strip()
        active_id_lookup = {item.lower() for item in active_ids}
        if not active_ids:
            active_ids = list(resolved_ids)
            active_id_lookup = {item.lower() for item in active_ids}
        if current_active and current_active.lower() not in active_id_lookup:
            active_ids.insert(0, current_active)
        if not current_active and active_ids:
            self.active_skill_id = active_ids[0]
        self.active_skill_ids = active_ids
        self.resolved_skill_ids = resolved_ids
        if not self.pending_clarify_items and self.unresolved_clarify_items:
            self.pending_clarify_items = list(self.unresolved_clarify_items)
        if not self.unresolved_clarify_items and self.pending_clarify_items:
            self.unresolved_clarify_items = list(self.pending_clarify_items)
        if not self.decision_sub_intent_refs:
            if self.decision_sub_intents:
                self.decision_sub_intent_refs = to_structured_sub_intents(
                    self.decision_sub_intents
                )
            elif self.plan is not None and self.plan.sub_intents:
                self.decision_sub_intent_refs = to_structured_sub_intents(
                    self.plan.sub_intents
                )
        if not self.decision_sub_intents and self.decision_sub_intent_refs:
            self.decision_sub_intents = sub_intent_descriptions(
                self.decision_sub_intent_refs
            )

        if not self.pending_confirmation_sub_intent_refs:
            if self.pending_confirmation_sub_intents:
                self.pending_confirmation_sub_intent_refs = to_structured_sub_intents(
                    self.pending_confirmation_sub_intents
                )
            elif self.pending_confirmation_command is not None:
                source_values: list[SubIntent] = []
                if self.plan is not None and self.plan.sub_intents:
                    source_values = select_sub_intents_by_ids(
                        self.plan.sub_intents,
                        getattr(
                            self.pending_confirmation_command, "sub_intent_ids", []
                        ),
                    )
                elif self.decision_sub_intent_refs:
                    source_values = select_sub_intents_by_ids(
                        self.decision_sub_intent_refs,
                        getattr(
                            self.pending_confirmation_command, "sub_intent_ids", []
                        ),
                    )
                if source_values:
                    self.pending_confirmation_sub_intent_refs = source_values
        if (
            not self.pending_confirmation_sub_intents
            and self.pending_confirmation_sub_intent_refs
        ):
            self.pending_confirmation_sub_intents = sub_intent_descriptions(
                self.pending_confirmation_sub_intent_refs
            )
        if (
            self.decision_feasibility_report is None
            and isinstance(self.decision_feasibility_state, dict)
            and self.decision_feasibility_state
        ):
            try:
                self.decision_feasibility_report = FeasibilityReport.model_validate(
                    feasibility_report_payload(self.decision_feasibility_state)
                )
            except Exception:
                self.decision_feasibility_report = None
        if (
            self.pending_confirmation_feasibility_report is None
            and isinstance(self.pending_confirmation_feasibility_state, dict)
            and self.pending_confirmation_feasibility_state
        ):
            try:
                self.pending_confirmation_feasibility_report = (
                    FeasibilityReport.model_validate(
                        feasibility_report_payload(
                            self.pending_confirmation_feasibility_state
                        )
                    )
                )
            except Exception:
                self.pending_confirmation_feasibility_report = None
        if (
            self.decision_feasibility_report is not None
            and not self.decision_feasibility_state
        ):
            self.decision_feasibility_state = (
                self.decision_feasibility_report.model_dump(mode="json")
            )
        if (
            self.pending_confirmation_feasibility_report is not None
            and not self.pending_confirmation_feasibility_state
        ):
            self.pending_confirmation_feasibility_state = (
                self.pending_confirmation_feasibility_report.model_dump(mode="json")
            )
        source_sub_intents: list[SubIntent] = []
        if self.decision_sub_intent_refs:
            source_sub_intents = list(self.decision_sub_intent_refs)
        elif self.plan is not None and self.plan.sub_intents:
            source_sub_intents = list(self.plan.sub_intents)
        if source_sub_intents:
            self.intent_execution_states = build_intent_execution_states(
                source_sub_intents,
                existing=self.intent_execution_states,
            )
        elif self.intent_execution_states:
            self.intent_execution_states = []
        return self


class StepOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(..., min_length=1)
    status: WorkingStatus
    message: str | None = None
    working_state: WorkingState
    action_result: ActionResult | None = None
    # explicit structural no-op marker. Set True by
    pae_idle_tick_noop: bool = False
    kind: RespondKindLiteral = RESPOND_KIND_ASSISTANT
