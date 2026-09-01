# ruff: noqa: F403,F405
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from openminion.modules.session.capture import TerminalCaptureIntentReceipt

from .common import *
from .common import (
    CognitionTier,
    PermissionMode,
    RunSubstate,
    WorkingStatus,
)
from ...constants import RESPOND_KIND_ASSISTANT, RespondKindLiteral
from ..base import ArtifactRef, iso_now
from ..commands import Command
from ..decisions import ClarifyContext, PendingTurnContext, RequestReadiness
from ..freshness import FreshnessContract, FreshnessDiagnostics, FreshnessObligations
from ..plan import (
    AdaptiveRevisionCheckpoint,
    FeasibilityReport,
    IntentExecutionState,
    Plan,
    ProgressCheckpointReport,
    StepRiskAssessment,
    SubIntent,
    build_intent_execution_states,
    feasibility_report_payload,
    normalize_sub_intent_ids,
    select_sub_intents_by_ids,
    sub_intent_descriptions,
    to_structured_sub_intents,
)
from .action import ActionResult, JobHandle
from .budget import BudgetCounters
from .clarify import BrainMode, ClarifyPolicy, ClarifyQuestion
from .mission import MissionState


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
    def _normalize_sub_intent_ids(cls, value: Any) -> list[str]:
        return normalize_sub_intent_ids(value)


class MetaDirectiveLogEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hook: str = Field(..., min_length=1)
    meta_state: str = Field(..., min_length=1)
    applied_at: str = Field(default_factory=iso_now)
    directive: dict[str, Any] = Field(default_factory=dict)


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
    runtime_session_id: str | None = None
    root_turn_id: str | None = None
    capture_event_id: str | None = None
    capture_id: str | None = None
    memory_capture_report_root_turn_id: str | None = None
    memory_capture_report: dict[str, Any] | None = None
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
    mode: BrainMode = cast(BrainMode, BrainMode.COMMAND)
    pending_confirmation_command: Command | None = None
    pending_policy_approval_id: str | None = None
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
    policy: ClarifyPolicy = cast(ClarifyPolicy, ClarifyPolicy.ALWAYS_ASK)

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

    def _sync_skill_fields(self) -> None:
        active_ids = _normalize_skill_ids(self.active_skill_ids)
        resolved_ids = _normalize_skill_ids(self.resolved_skill_ids)
        current_active = str(self.active_skill_id or "").strip()
        if not active_ids:
            active_ids = list(resolved_ids)
        if current_active and current_active.lower() not in {
            item.lower() for item in active_ids
        }:
            active_ids.insert(0, current_active)
        if not current_active and active_ids:
            self.active_skill_id = active_ids[0]
        self.active_skill_ids = active_ids
        self.resolved_skill_ids = resolved_ids

    def _sync_clarify_item_mirrors(self) -> None:
        if not self.pending_clarify_items and self.unresolved_clarify_items:
            self.pending_clarify_items = list(self.unresolved_clarify_items)
        if not self.unresolved_clarify_items and self.pending_clarify_items:
            self.unresolved_clarify_items = list(self.pending_clarify_items)

    def _sync_decision_sub_intents(self) -> None:
        if not self.decision_sub_intent_refs:
            source = self.decision_sub_intents or (
                self.plan.sub_intents if self.plan is not None else []
            )
            if source:
                self.decision_sub_intent_refs = to_structured_sub_intents(source)
        if not self.decision_sub_intents and self.decision_sub_intent_refs:
            self.decision_sub_intents = sub_intent_descriptions(
                self.decision_sub_intent_refs
            )

    def _confirmation_source_sub_intents(self) -> list[SubIntent]:
        if self.pending_confirmation_command is None:
            return []
        command_ids = getattr(self.pending_confirmation_command, "sub_intent_ids", [])
        if self.plan is not None and self.plan.sub_intents:
            return select_sub_intents_by_ids(self.plan.sub_intents, command_ids)
        if self.decision_sub_intent_refs:
            return select_sub_intents_by_ids(
                self.decision_sub_intent_refs,
                command_ids,
            )
        return []

    def _sync_pending_confirmation_sub_intents(self) -> None:
        if not self.pending_confirmation_sub_intent_refs:
            if self.pending_confirmation_sub_intents:
                self.pending_confirmation_sub_intent_refs = to_structured_sub_intents(
                    self.pending_confirmation_sub_intents
                )
            else:
                self.pending_confirmation_sub_intent_refs = (
                    self._confirmation_source_sub_intents()
                )
        if (
            not self.pending_confirmation_sub_intents
            and self.pending_confirmation_sub_intent_refs
        ):
            self.pending_confirmation_sub_intents = sub_intent_descriptions(
                self.pending_confirmation_sub_intent_refs
            )

    @staticmethod
    def _feasibility_report_from_state(
        state_payload: dict[str, Any],
    ) -> FeasibilityReport | None:
        if not state_payload:
            return None
        try:
            return FeasibilityReport.model_validate(
                feasibility_report_payload(state_payload)
            )
        except Exception:
            return None

    def _sync_feasibility_reports(self) -> None:
        if self.decision_feasibility_report is None:
            self.decision_feasibility_report = self._feasibility_report_from_state(
                self.decision_feasibility_state
            )
        if self.pending_confirmation_feasibility_report is None:
            self.pending_confirmation_feasibility_report = (
                self._feasibility_report_from_state(
                    self.pending_confirmation_feasibility_state
                )
            )

    def _sync_feasibility_state_payloads(self) -> None:
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

    def _sync_intent_execution_states(self) -> None:
        source = self.decision_sub_intent_refs or (
            self.plan.sub_intents if self.plan is not None else []
        )
        self.intent_execution_states = (
            build_intent_execution_states(source, existing=self.intent_execution_states)
            if source
            else []
        )

    @model_validator(mode="after")
    def _sync_clarify_fields(self) -> "WorkingState":
        self._sync_skill_fields()
        self._sync_clarify_item_mirrors()
        self._sync_decision_sub_intents()
        self._sync_pending_confirmation_sub_intents()
        self._sync_feasibility_reports()
        self._sync_feasibility_state_payloads()
        self._sync_intent_execution_states()
        return self


class StepOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(..., min_length=1)
    status: WorkingStatus
    message: str | None = None
    working_state: WorkingState
    action_result: ActionResult | None = None
    terminal_capture_intent_receipt: TerminalCaptureIntentReceipt | None = None
    memory_capture_bundle_result: dict[str, Any] | None = None
    # explicit structural no-op marker. Set True by
    pae_idle_tick_noop: bool = False
    kind: RespondKindLiteral = RESPOND_KIND_ASSISTANT
