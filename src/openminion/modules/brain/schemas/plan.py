from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .base import ArtifactRef, iso_now
from .commands import Command
from .decisions import MetaRulePreference
from .readiness import (
    contains_unresolved_template_text,
    find_unknown_sentinel_path,
)


FeasibilityStatus = Literal["covered", "partial", "uncovered", "unauthorized"]
FeasibilityRecommendation = Literal[
    "proceed_full",
    "proceed_partial",
    "retry_full",
    "abort",
    "suggest_alternatives",
]
ExecutionOutcome = Literal[
    "pending",
    "in_progress",
    "retrying",
    "succeeded",
    "failed",
    "blocked",
    "skipped",
    "needs_user",
]
AdaptiveRevisionAction = Literal[
    "continue",
    "skip_next_step",
    "replan",
    "ask_user",
]
StructuredFixAction = Literal[
    "replan",
    "ask_user",
    "retry_with_precondition",
    "skip_next_step",
]
ProgressCheckpointOutcome = Literal["continue", "adjust", "replan"]
StepRiskOutcome = Literal["execute", "pause", "ask_user", "replan"]
SuccessMemoryKind = Literal["procedure", "tool_habit"]
FailureMemoryKind = Literal["correction"]

UserMessageCandidateKind = Literal["fact", "user_preference", "task"]

FixKind = Literal[
    "lesson", "procedure", "pin_candidate", "eval_case", "tool_wrapper_change"
]
ReflectOutcome = Literal["success", "failure", "partial"]
FailureType = Literal[
    "tool_misuse",
    "bad_assumption",
    "planning_error",
    "policy_violation",
    "execution_error",
    "communication_error",
    "memory_error",
]

_SUB_INTENT_ID_SANITIZER_RE = re.compile(r"[^a-z0-9]+")


def build_sub_intent_id(description: str, *, index: int | None = None) -> str:
    text = str(description or "").strip().lower()
    slug = _SUB_INTENT_ID_SANITIZER_RE.sub("_", text).strip("_") or "intent"
    if index is None:
        return f"intent_{slug}"
    return f"intent_{index:02d}_{slug}"


def _strip_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value or "").strip()
    return text or None


def _normalize_string_list(value: Any) -> Any:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, tuple):
        return list(value)
    return value


def _dedupe_texts(values: Iterable[Any]) -> list[str]:
    normalized: list[str] = []
    for raw_value in values:
        text = str(raw_value or "").strip()
        if text and text not in normalized:
            normalized.append(text)
    return normalized


def _sub_intent_state_update(item: "SubIntent") -> dict[str, Any]:
    return {
        "description": item.description,
        "skill_id": item.skill_id,
        "conditional": item.conditional,
        "depends_on": list(item.depends_on),
    }


class SubIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    skill_id: str | None = Field(
        default=None,
        description=(
            "Optional model-authored skill binding for this sub-intent. Runtime "
            "transports the id and may activate it only when it matches an active skill."
        ),
    )
    conditional: bool = False
    depends_on: list[str] = Field(default_factory=list)

    @field_validator("id", "description", mode="before")
    @classmethod
    def _strip_required_text(cls, value: Any) -> Any:
        if value is None:
            return value
        return str(value).strip()

    @field_validator("skill_id", mode="before")
    @classmethod
    def _strip_optional_skill_id(cls, value: Any) -> Any:
        return _strip_optional_text(value)

    @field_validator("depends_on", mode="before")
    @classmethod
    def _normalize_depends_on(cls, value: Any) -> Any:
        return _normalize_string_list(value)

    @model_validator(mode="after")
    def _validate_dependencies(self) -> "SubIntent":
        self.depends_on = _dedupe_texts(self.depends_on)
        if self.id in self.depends_on:
            raise ValueError("SubIntent.depends_on cannot reference itself")
        return self


class IntentExecutionState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent_id: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    skill_id: str | None = None
    conditional: bool = False
    depends_on: list[str] = Field(default_factory=list)
    status: ExecutionOutcome = "pending"
    last_command_id: str = ""
    last_step_index: int | None = Field(default=None, ge=0)
    last_action_status: str = ""
    summary: str = ""
    updated_at: str = Field(default_factory=iso_now)

    @field_validator("intent_id", "description", mode="before")
    @classmethod
    def _strip_required_text(cls, value: Any) -> Any:
        if value is None:
            return value
        return str(value).strip()

    @field_validator("skill_id", mode="before")
    @classmethod
    def _strip_optional_skill_id(cls, value: Any) -> Any:
        return _strip_optional_text(value)

    @field_validator("depends_on", mode="before")
    @classmethod
    def _normalize_depends_on(cls, value: Any) -> Any:
        return _normalize_string_list(value)

    @model_validator(mode="after")
    def _dedupe_depends_on(self) -> "IntentExecutionState":
        self.depends_on = _dedupe_texts(self.depends_on)
        if self.intent_id in self.depends_on:
            raise ValueError("IntentExecutionState.depends_on cannot reference itself")
        return self


class SubIntentFeasibility(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent_id: str = Field(..., min_length=1)
    status: FeasibilityStatus
    reason: str = ""
    covering_tools: list[str] = Field(default_factory=list)
    blocked_by: list[str] = Field(default_factory=list)
    alternatives: list[str] = Field(default_factory=list)

    @field_validator("intent_id", mode="before")
    @classmethod
    def _strip_intent_id(cls, value: Any) -> Any:
        if value is None:
            return value
        return str(value).strip()


class FeasibilityReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_viable: bool = True
    recommendation: FeasibilityRecommendation = "proceed_full"
    user_message: str = ""
    requires_user_choice: bool = False
    viable_intent_ids: list[str] = Field(default_factory=list)
    blocked_intent_ids: list[str] = Field(default_factory=list)
    assessments: list[SubIntentFeasibility] = Field(default_factory=list)

    @model_validator(mode="after")
    def _sync_assessment_refs(self) -> "FeasibilityReport":
        assessment_ids = [
            item.intent_id for item in self.assessments if str(item.intent_id).strip()
        ]
        if not self.viable_intent_ids:
            self.viable_intent_ids = [
                item.intent_id
                for item in self.assessments
                if item.status in {"covered", "partial"}
            ]
        else:
            self.viable_intent_ids = normalize_sub_intent_ids(
                self.viable_intent_ids,
                allowed_ids=assessment_ids or None,
            )
        if not self.blocked_intent_ids:
            self.blocked_intent_ids = [
                item.intent_id
                for item in self.assessments
                if item.status in {"uncovered", "unauthorized"}
            ]
        else:
            self.blocked_intent_ids = normalize_sub_intent_ids(
                self.blocked_intent_ids,
                allowed_ids=assessment_ids or None,
            )
        if self.recommendation == "proceed_full" and self.blocked_intent_ids:
            self.recommendation = "proceed_partial"
        if self.recommendation in {
            "proceed_partial",
            "retry_full",
            "abort",
            "suggest_alternatives",
        }:
            self.requires_user_choice = True
        return self


class SuccessMemoryItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: SuccessMemoryKind
    title: str = Field(..., min_length=1)
    content: str | dict[str, Any]
    confidence: float = Field(..., ge=0.0, le=1.0)
    rationale: str = ""
    tags: list[str] = Field(default_factory=list)
    evidence_refs: list[ArtifactRef] = Field(default_factory=list)
    scope_suggestion: str = "agent"


class SuccessMemoryReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(..., min_length=1)
    agent_id: str = Field(..., min_length=1)
    outcome: Literal["success"] = "success"
    command_ids: list[str] = Field(default_factory=list)
    items: list[SuccessMemoryItem] = Field(default_factory=list)


class FailureMemoryItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: FailureMemoryKind = "correction"
    title: str = Field(..., min_length=1)
    content: str | dict[str, Any]
    confidence: float = Field(..., ge=0.0, le=1.0)
    tags: list[str] = Field(default_factory=list)
    evidence_refs: list[ArtifactRef] = Field(default_factory=list)
    scope_suggestion: str = "agent"


class FailureMemoryReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(..., min_length=1)
    agent_id: str = Field(..., min_length=1)
    outcome: Literal["failure"] = "failure"
    termination_reason: str = Field(..., min_length=1)
    command_ids: list[str] = Field(default_factory=list)
    items: list[FailureMemoryItem] = Field(default_factory=list)
    meta_rule_preference: MetaRulePreference | None = None


class UserMessageCandidateItem(BaseModel):
    """A typed fact, preference, or task candidate extracted from a user message."""

    model_config = ConfigDict(extra="forbid")

    kind: UserMessageCandidateKind
    normalized_key: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description=(
            "Deterministic identity key for same-key reinforcement, e.g. "
            "'fact:user_name', 'user_preference:response_style', "
            "'task:deploy_auth_service'. Validated against the bounded "
            "category set by runtime."
        ),
    )
    title: str = Field(..., min_length=1, max_length=200)
    content: str = Field(..., min_length=1, max_length=600)
    tags: list[str] = Field(default_factory=list)


class UserMessageCandidateReport(BaseModel):
    """Bounded schema for AFE extraction output."""

    model_config = ConfigDict(extra="forbid")

    session_id: str | None = Field(default=None)
    agent_id: str | None = Field(default=None)
    items: list[UserMessageCandidateItem] = Field(default_factory=list)


class RemainingPlanContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    completed_intents: list[IntentExecutionState] = Field(default_factory=list)
    remaining_intents: list[IntentExecutionState] = Field(default_factory=list)
    blocked_intents: list[IntentExecutionState] = Field(default_factory=list)


class AdaptiveRevisionCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: AdaptiveRevisionAction = "continue"
    reason: str = ""
    completed_intent_ids: list[str] = Field(default_factory=list)
    remaining_intent_ids: list[str] = Field(default_factory=list)
    blocked_intent_ids: list[str] = Field(default_factory=list)
    target_command_id: str | None = None
    question: str = ""

    @field_validator(
        "completed_intent_ids",
        "remaining_intent_ids",
        "blocked_intent_ids",
        mode="before",
    )
    @classmethod
    def _normalize_intent_id_lists(cls, value: Any) -> Any:
        return normalize_sub_intent_ids(value)


class ProgressCheckpointReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: ProgressCheckpointOutcome = "continue"
    reason: str = ""
    question: str = ""
    suggested_constraints: list[str] = Field(default_factory=list)
    completed_intent_ids: list[str] = Field(default_factory=list)
    remaining_intent_ids: list[str] = Field(default_factory=list)
    blocked_intent_ids: list[str] = Field(default_factory=list)

    @field_validator(
        "completed_intent_ids",
        "remaining_intent_ids",
        "blocked_intent_ids",
        mode="before",
    )
    @classmethod
    def _normalize_checkpoint_intent_ids(cls, value: Any) -> Any:
        return normalize_sub_intent_ids(value)

    @model_validator(mode="after")
    def _dedupe_checkpoint_payload(self) -> "ProgressCheckpointReport":
        self.completed_intent_ids = normalize_sub_intent_ids(self.completed_intent_ids)
        self.remaining_intent_ids = normalize_sub_intent_ids(self.remaining_intent_ids)
        self.blocked_intent_ids = normalize_sub_intent_ids(self.blocked_intent_ids)
        self.suggested_constraints = _dedupe_texts(self.suggested_constraints)
        return self


class StepRiskAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: StepRiskOutcome = "execute"
    reason: str = ""
    question: str = ""
    risk_level: str = "low"
    requires_confirmation: bool = False

    @field_validator("risk_level", mode="before")
    @classmethod
    def _normalize_risk_level(cls, value: Any) -> Any:
        return str(value or "low").strip().lower() or "low"


def feasibility_report_payload(
    value: Mapping[str, Any] | dict[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    allowed = set(FeasibilityReport.model_fields.keys())
    return {str(key): raw for key, raw in value.items() if str(key) in allowed}


def to_structured_sub_intents(
    values: Iterable[str | SubIntent | Mapping[str, Any]],
) -> list[SubIntent]:
    structured: list[SubIntent] = []
    seen_ids: set[str] = set()

    for index, raw_value in enumerate(values, start=1):
        if isinstance(raw_value, SubIntent):
            item = raw_value
        elif isinstance(raw_value, Mapping):
            payload = dict(raw_value)
            if "id" not in payload and "description" in payload:
                payload["id"] = build_sub_intent_id(
                    str(payload.get("description", "")),
                    index=index,
                )
            item = SubIntent.model_validate(payload)
        else:
            description = str(raw_value or "").strip()
            if not description:
                raise ValueError("SubIntent description cannot be blank")
            item = SubIntent(
                id=build_sub_intent_id(description, index=index),
                description=description,
            )
        if item.id in seen_ids:
            raise ValueError(f"Duplicate SubIntent id: {item.id}")
        seen_ids.add(item.id)
        structured.append(item)
    return structured


def sub_intent_descriptions(
    values: Iterable[str | SubIntent | Mapping[str, Any]],
) -> list[str]:
    return [item.description for item in to_structured_sub_intents(values)]


def normalize_sub_intent_ids(
    values: Iterable[str] | str | None,
    *,
    allowed_ids: Iterable[str] | None = None,
) -> list[str]:
    if values is None:
        raw_values: list[str] = []
    elif isinstance(values, str):
        raw_values = [values]
    else:
        raw_values = [str(item or "") for item in values]
    allowed = (
        {str(item or "").strip() for item in allowed_ids if str(item or "").strip()}
        if allowed_ids is not None
        else None
    )
    normalized: list[str] = []
    for raw_value in raw_values:
        text = str(raw_value or "").strip()
        if not text:
            continue
        if allowed is not None and text not in allowed:
            continue
        if text not in normalized:
            normalized.append(text)
    return normalized


def select_sub_intents_by_ids(
    values: Iterable[str | SubIntent | Mapping[str, Any]],
    sub_intent_ids: Iterable[str] | str | None,
) -> list[SubIntent]:
    structured = to_structured_sub_intents(values)
    if not structured:
        return []
    normalized_ids = normalize_sub_intent_ids(
        sub_intent_ids,
        allowed_ids=[item.id for item in structured],
    )
    if not normalized_ids:
        return []
    by_id = {item.id: item for item in structured}
    return [by_id[item_id] for item_id in normalized_ids if item_id in by_id]


def build_intent_execution_states(
    values: Iterable[str | SubIntent | Mapping[str, Any]],
    *,
    existing: Iterable[IntentExecutionState | Mapping[str, Any]] | None = None,
) -> list[IntentExecutionState]:
    structured = to_structured_sub_intents(values)
    if not structured:
        return []

    existing_by_id: dict[str, IntentExecutionState] = {}
    for raw_item in existing or []:
        try:
            existing_state = (
                raw_item
                if isinstance(raw_item, IntentExecutionState)
                else IntentExecutionState.model_validate(raw_item)
            )
        except Exception:
            continue
        existing_by_id[existing_state.intent_id] = existing_state

    normalized: list[IntentExecutionState] = []
    for sub_intent in structured:
        prior = existing_by_id.get(sub_intent.id)
        if prior is None:
            normalized.append(
                IntentExecutionState(
                    intent_id=sub_intent.id,
                    **_sub_intent_state_update(sub_intent),
                )
            )
            continue
        normalized.append(
            prior.model_copy(
                update=_sub_intent_state_update(sub_intent),
                deep=True,
            )
        )
    return normalized


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objective: str = Field(..., min_length=1)
    steps: list[Command] = Field(default_factory=list)
    stop_conditions: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    risk_summary: str = ""
    success_criteria: dict[str, Any] = Field(default_factory=dict)
    sub_intents: list[SubIntent] = Field(
        default_factory=list,
        description=(
            "Internal structured sub-intent refs for this plan. "
            "This field is not part of the external Decision wire contract."
        ),
    )
    created_at: str = Field(default_factory=iso_now)
    plan_version: int = Field(default=1, ge=1)

    def first_executable_step(self) -> Command | None:
        for step in self.steps:
            if str(getattr(step, "kind", "") or "").strip() in {
                "tool",
                "agent",
                "think",
                "finish",
            }:
                return step
        return None

    @model_validator(mode="after")
    def _reject_unresolved_placeholder_steps(self) -> "Plan":
        for index, step in enumerate(self.steps, start=1):
            if str(getattr(step, "kind", "") or "").strip() == "finish":
                final_message = str(getattr(step, "final_message", "") or "").strip()
                if contains_unresolved_template_text(final_message):
                    raise ValueError(
                        "Plan finish step contains unresolved template placeholders "
                        f"at steps[{index - 1}].final_message"
                    )
            if str(getattr(step, "kind", "") or "").strip() == "tool":
                unknown_path = find_unknown_sentinel_path(
                    getattr(step, "args", {}) or {},
                    prefix=f"steps[{index - 1}].args",
                )
                if unknown_path:
                    raise ValueError(
                        "Plan tool step contains unresolved placeholder value "
                        f"at {unknown_path}"
                    )
        return self


class FixItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: FixKind
    scope_suggestion: str = Field(default="agent")
    key: str | None = None
    title: str = Field(..., min_length=1)
    content: dict[str, Any] | str
    confidence: float = Field(..., ge=0.0, le=1.0)
    requires_approval: bool = False
    tags: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    evidence_refs: list[ArtifactRef] = Field(default_factory=list)
    action: StructuredFixAction | None = None
    target_command_id: str | None = None
    question: str = ""
    precondition: str = ""
