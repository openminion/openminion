from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError
from openminion.modules.brain.runtime.recovery import TCRPContext, validate_payload

from .commands import Command
from .routing import (
    _flatten_branch_payloads,
    _normalize_route_name,
    _normalize_stripped_text,
    _normalize_sub_intents,
    _registry_route_names,
    _route_field_description,
    normalize_artifact_refs,
    normalize_decomposed_subtasks,
    normalize_delegation_summary,
    normalize_session_work_summary,
)


DecisionRoute = str
RespondKind = Literal["answer", "clarify"]
ActProfile = Literal["general", "coding", "research", "orchestrate"]
RequestPosture = Literal["direct", "brief_plan", "review_before_act"]
RequestedOutcome = Literal["answer_only", "plan_only", "review_only", "execute"]
RequestReadinessState = Literal[
    "ready",
    "needs_user",
    "needs_plan_review",
    "needs_operation_approval",
    "blocked",
]
RequestAssumptionSource = Literal[
    "user",
    "repository",
    "existing_contract",
    "reversible_default",
]


def _normalize_artifact_alias_payload(
    value: Any,
    *,
    target_field: str,
) -> Any:
    if not isinstance(value, Mapping):
        return value
    if target_field in value or "artifact_refs" not in value:
        return value
    normalized = dict(value)
    normalized[target_field] = normalized.pop("artifact_refs")
    return normalized


class ExecutionTargetPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    kind: Literal["local", "delegated"] = Field(
        ...,
        description=(
            "Who should execute the work. Use 'local' when the current agent "
            "should do it. Use 'delegated' only when the user explicitly wants "
            "another agent to execute the work."
        ),
    )
    target_agent_id: str = Field(
        default="",
        description=(
            "Exact agent ID to delegate to when kind='delegated'. Copy the "
            "user-supplied agent ID exactly when provided."
        ),
    )
    target_capability: str = Field(
        default="",
        description=(
            "Optional capability hint for delegated execution when the user "
            "asks for a specialist but does not name an exact agent."
        ),
    )
    expect_async: bool = Field(
        default=False,
        description=(
            "Set true only when delegated execution is expected to continue "
            "asynchronously beyond the current turn."
        ),
    )


class ClarifyContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    original_user_input: str = Field(..., min_length=1)
    inferred_goal: str = ""
    known_context: dict[str, str] = Field(default_factory=dict)
    unresolved_question: str = ""
    clarify_question: str = ""

    @field_validator("known_context", mode="before")
    @classmethod
    def normalize_known_context(cls, value: Any) -> dict[str, str]:
        if value in (None, ""):
            return {}
        if not isinstance(value, Mapping):
            raise ValueError("known_context must be a mapping")
        normalized: dict[str, str] = {}
        for key, item in value.items():
            label = str(key or "").strip()
            text = str(item or "").strip()
            if label and text:
                normalized[label] = text
        return normalized


class PendingTurnContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    original_user_request: str = Field(
        default="",
        description="Original user request that the next short follow-up should stay anchored to.",
    )
    active_work_summary: str = Field(
        default="",
        description="Short summary of the in-flight work or missing piece that remains unresolved.",
    )
    known_context: dict[str, str] = Field(
        default_factory=dict,
        description="Prompt-oriented known context that helps the next turn stay grounded.",
    )
    missing_fields: list[str] = Field(
        default_factory=list,
        description="Missing pieces the next user reply may supply.",
    )
    artifact_refs: list[str] = Field(
        default_factory=list,
        description="Compact references to artifacts from the prior turn.",
    )
    response_preferences: dict[str, str] = Field(
        default_factory=dict,
        description="Compact response preferences that should carry forward, such as language=en.",
    )

    @field_validator("known_context", "response_preferences", mode="before")
    @classmethod
    def normalize_prompt_mapping(cls, value: Any) -> dict[str, str]:
        if value in (None, ""):
            return {}
        if not isinstance(value, Mapping):
            raise ValueError("pending_turn_context mappings must be dict-like")
        normalized: dict[str, str] = {}
        for key, item in value.items():
            label = str(key or "").strip()
            text = str(item or "").strip()
            if label and text:
                normalized[label] = text
        return normalized

    @field_validator("missing_fields", "artifact_refs", mode="before")
    @classmethod
    def normalize_prompt_list(cls, value: Any) -> list[str]:
        if value in (None, ""):
            return []
        if not isinstance(value, list | tuple):
            raise ValueError("pending_turn_context lists must be sequences")
        normalized: list[str] = []
        for item in value:
            text = str(item or "").strip()
            if text:
                normalized.append(text)
        return normalized

    @model_validator(mode="after")
    def validate_non_empty(self) -> "PendingTurnContext":
        if any(
            (
                self.original_user_request,
                self.active_work_summary,
                self.known_context,
                self.missing_fields,
                self.artifact_refs,
                self.response_preferences,
            )
        ):
            return self
        raise ValueError(
            "pending_turn_context must include at least one non-empty field"
        )


class RequestAssumption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(..., min_length=1, max_length=500)
    source: RequestAssumptionSource
    reversible: bool
    validation_trigger: str = Field(..., min_length=1, max_length=240)

    @field_validator("text", "validation_trigger", mode="before")
    @classmethod
    def normalize_text(cls, value: Any) -> str:
        return _normalize_stripped_text(value)


class RequestReadiness(BaseModel):
    model_config = ConfigDict(extra="forbid")

    posture: RequestPosture
    requested_outcome: RequestedOutcome
    state: RequestReadinessState
    assumptions: list[RequestAssumption] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def validate_readiness_shape(self) -> "RequestReadiness":
        if self.state == "needs_plan_review" and self.posture != "review_before_act":
            raise ValueError(
                "needs_plan_review requires posture='review_before_act'"
            )
        return self


class ConfidentComplete(BaseModel):
    model_config = ConfigDict(extra="forbid")

    complete: bool = False
    reasoning: str = ""

    @field_validator("reasoning", mode="before")
    @classmethod
    def normalize_reasoning(cls, value: Any) -> str:
        return _normalize_stripped_text(value)


class FinalizationStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["final_answer", "incomplete", "blocked"]
    reasoning: str = Field(
        default="",
        validation_alias=AliasChoices("reasoning", "reason"),
    )
    remaining_work: str = ""
    blocking_reason: str = ""

    @field_validator("reasoning", "remaining_work", "blocking_reason", mode="before")
    @classmethod
    def normalize_text_fields(cls, value: Any) -> str:
        return _normalize_stripped_text(value)


class MetaRulePreference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule: str
    preferred_value: int | float | str
    reasoning: str = ""

    @field_validator("rule", mode="before")
    @classmethod
    def normalize_rule(cls, value: Any) -> str:
        return _normalize_stripped_text(value)

    @field_validator("preferred_value", mode="before")
    @classmethod
    def normalize_preferred_value(cls, value: Any) -> int | float | str:
        if isinstance(value, bool):
            return str(value).lower()
        if isinstance(value, int | float):
            return value
        return str(value or "").strip()

    @field_validator("reasoning", mode="before")
    @classmethod
    def normalize_reasoning(cls, value: Any) -> str:
        return _normalize_stripped_text(value)

    @model_validator(mode="after")
    def validate_non_empty(self) -> "MetaRulePreference":
        if not self.rule:
            raise ValueError("meta_rule_preference requires a non-empty rule")
        if isinstance(self.preferred_value, str) and not self.preferred_value:
            raise ValueError(
                "meta_rule_preference requires a non-empty preferred_value"
            )
        return self


class WatchOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    condition_met: bool = False
    summary: str = ""

    @field_validator("summary", mode="before")
    @classmethod
    def normalize_summary(cls, value: Any) -> str:
        return _normalize_stripped_text(value)


class MemoryConsolidationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    action: Literal["promote", "discard", "defer"]
    reasoning: str = ""

    @field_validator("candidate_id", mode="before")
    @classmethod
    def normalize_candidate_id(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("reasoning", mode="before")
    @classmethod
    def normalize_reasoning(cls, value: Any) -> str:
        return _normalize_stripped_text(value)

    @model_validator(mode="after")
    def validate_non_empty(self) -> "MemoryConsolidationDecision":
        if self.candidate_id:
            return self
        raise ValueError("memory_consolidation decisions require candidate_id")


class MemoryConsolidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decisions: list[MemoryConsolidationDecision] = Field(default_factory=list)


class SessionWorkSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = ""

    @field_validator("summary", mode="before")
    @classmethod
    def normalize_summary(cls, value: Any) -> str:
        return normalize_session_work_summary(value)

    @model_validator(mode="after")
    def validate_non_empty(self) -> "SessionWorkSummary":
        if self.summary:
            return self
        raise ValueError("session_work_summary must include non-empty summary text")


class DelegationContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = ""
    artifacts: list[str] = Field(default_factory=list)
    intent_id: str = ""

    @model_validator(mode="before")
    @classmethod
    def normalize_aliases(cls, value: Any) -> Any:
        return _normalize_artifact_alias_payload(value, target_field="artifacts")

    @field_validator("summary", mode="before")
    @classmethod
    def normalize_summary(cls, value: Any) -> str:
        return normalize_delegation_summary(value)

    @field_validator("artifacts", mode="before")
    @classmethod
    def normalize_artifact_refs(cls, value: Any) -> list[str]:
        return normalize_artifact_refs(value)

    @field_validator("intent_id", mode="before")
    @classmethod
    def normalize_intent_id(cls, value: Any) -> str:
        return _normalize_stripped_text(value)

    @model_validator(mode="after")
    def validate_non_empty(self) -> "DelegationContext":
        if self.summary or self.artifacts or self.intent_id:
            return self
        raise ValueError("delegation_context requires summary, artifacts, or intent_id")


class DelegationResultSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = ""
    artifacts_produced: list[str] = Field(default_factory=list)
    status: Literal["complete", "partial", "blocked", "failed"] = "complete"

    @model_validator(mode="before")
    @classmethod
    def normalize_aliases(cls, value: Any) -> Any:
        return _normalize_artifact_alias_payload(
            value,
            target_field="artifacts_produced",
        )

    @field_validator("summary", mode="before")
    @classmethod
    def normalize_summary(cls, value: Any) -> str:
        return normalize_delegation_summary(value)

    @field_validator("artifacts_produced", mode="before")
    @classmethod
    def normalize_artifact_refs(cls, value: Any) -> list[str]:
        return normalize_artifact_refs(value)

    @model_validator(mode="after")
    def validate_non_empty(self) -> "DelegationResultSummary":
        if self.summary or self.artifacts_produced:
            return self
        raise ValueError(
            "delegation_result_summary requires summary or artifacts_produced"
        )


GoalPriority = Literal["low", "medium", "high"]
GoalActionType = Literal["watch", "task", "suggest", "none"]


class GoalDeclaration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal_id: str | None = Field(
        default=None,
        description="Optional durable goal identifier; runtime may backfill when absent.",
    )
    parent_goal_id: str | None = Field(
        default=None,
        description="Optional parent goal id for hierarchical goal management.",
    )
    depth: int = Field(default=0, ge=0)
    goal: str = Field(
        default="",
        description=(
            "Concise statement of the work the agent is declaring. Required, "
            "non-empty. Example: `Monitor deployment health daily and report "
            "any degradation`."
        ),
    )
    trigger: str = Field(
        default="",
        description=(
            "Why the agent is declaring this goal — the observed context "
            "that prompted it. Required, non-empty. Example: `Recent tool "
            "failures when checking deployment status`."
        ),
    )
    priority: GoalPriority = Field(
        default="medium",
        description="Operator-visible priority hint set by the model.",
    )
    action_type: GoalActionType = Field(
        default="suggest",
        description=(
            "How the agent intends to act on the goal. `watch` → may create "
            "an APWS subscription; `task` → may create a scheduled task; "
            "`suggest` → surface to the user; `none` → store for context only."
        ),
    )
    suggested_schedule: str | None = Field(
        default=None,
        description=(
            "Optional natural-language cadence (e.g. `every 24h`); only "
            "meaningful when `action_type` is `watch` or `task`."
        ),
    )

    @field_validator("goal", "trigger", mode="before")
    @classmethod
    def _normalize_text(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("goal_id", "parent_goal_id", mode="before")
    @classmethod
    def _normalize_optional_text(cls, value: Any) -> str | None:
        text = str(value or "").strip()
        return text or None

    @model_validator(mode="after")
    def _validate_required_fields(self) -> "GoalDeclaration":
        if not self.goal:
            raise ValueError("goal_declaration requires non-empty `goal` text")
        if not self.trigger:
            raise ValueError("goal_declaration requires non-empty `trigger` text")
        return self


class GoalRevision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal_id: str | None = Field(
        default=None,
        description="Optional durable goal identifier for the revised goal.",
    )
    parent_goal_id: str | None = Field(
        default=None,
        description="Optional parent goal id retained across hierarchical revisions.",
    )
    depth: int = Field(default=0, ge=0)
    previous_goal: str = Field(
        default="",
        description=(
            "Previously declared goal being revised. Required, non-empty. "
            "Provides the structural audit chain without requiring runtime "
            "inference."
        ),
    )
    goal: str = Field(
        default="",
        description=("Revised goal statement. Required, non-empty."),
    )
    trigger: str = Field(
        default="",
        description=(
            "Observed counter-evidence or changed context prompting the "
            "revision. Required, non-empty."
        ),
    )
    priority: GoalPriority = Field(
        default="medium",
        description="Operator-visible priority hint set by the model.",
    )
    action_type: GoalActionType = Field(
        default="suggest",
        description=(
            "How the agent intends to act on the revised goal. Uses the same "
            "authorization contract as goal declaration."
        ),
    )
    suggested_schedule: str | None = Field(
        default=None,
        description=("Optional natural-language cadence for watch/task revisions."),
    )

    @field_validator("previous_goal", "goal", "trigger", mode="before")
    @classmethod
    def _normalize_revision_text(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("goal_id", "parent_goal_id", mode="before")
    @classmethod
    def _normalize_optional_revision_text(cls, value: Any) -> str | None:
        text = str(value or "").strip()
        return text or None

    @model_validator(mode="after")
    def _validate_required_fields(self) -> "GoalRevision":
        if not self.previous_goal:
            raise ValueError("goal_revision requires non-empty `previous_goal` text")
        if not self.goal:
            raise ValueError("goal_revision requires non-empty `goal` text")
        if not self.trigger:
            raise ValueError("goal_revision requires non-empty `trigger` text")
        return self


class _DecisionBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    _seeded_commands: list[Command] = PrivateAttr(default_factory=list)
    _entry_response: Any | None = PrivateAttr(default=None)
    _pre_resolved_act_route: Any | None = PrivateAttr(default=None)

    route: DecisionRoute = Field(..., description="Routing decision.")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    reason_code: str = ""
    sub_intents: list[str] = Field(
        default_factory=list,
        description=(
            "Detected sub-intents declared by the LLM for this request. "
            "Use to represent compound intent coverage and validation."
        ),
    )
    rationale: str = Field(
        default="",
        description=(
            "Optional rationale describing why this routing choice is appropriate."
        ),
    )
    respond_kind: RespondKind | None = None
    question: str | None = None
    answer: str | None = None
    clarify_context: ClarifyContext | None = None
    pending_turn_context: PendingTurnContext | None = Field(
        default=None,
        description=(
            "Optional carry-forward context for the next user turn. Use only when "
            "this turn leaves meaningful work in progress or awaits a missing detail."
        ),
    )
    request_readiness: RequestReadiness | None = Field(
        default=None,
        description=(
            "Optional typed high-level request handoff payload. Omitted payloads "
            "preserve legacy routing; runtime must not infer replacements."
        ),
    )
    confident_complete: ConfidentComplete | None = Field(
        default=None,
        description=(
            "Optional provider-validated completion signal. Use only when the "
            "answer already contains the final user-facing result."
        ),
    )
    finalization_status: FinalizationStatus | None = Field(
        default=None,
        description=(
            "Optional typed finalization state for substantive tool-backed turns. "
            "Use final_answer only when the answer already contains the final "
            "deliverable; use incomplete or blocked when the turn ends truthfully "
            "without a completed deliverable."
        ),
    )
    session_work_summary: SessionWorkSummary | None = Field(
        default=None,
        description=(
            "Optional concise checkpoint summary that should carry forward to "
            "future turns."
        ),
    )
    meta_rule_preference: MetaRulePreference | None = Field(
        default=None,
        description=(
            "Optional reusable retry, replan, or budget threshold preference "
            "that may be staged for memory."
        ),
    )
    delegation_context: DelegationContext | None = Field(
        default=None,
        description="Optional parent context for delegated execution.",
    )
    delegation_result_summary: DelegationResultSummary | None = Field(
        default=None,
        description="Optional bounded summary returned from delegated execution.",
    )
    goal_declaration: GoalDeclaration | None = Field(
        default=None,
        description=(
            "Optional model-authored goal declaration. Set when the model "
            "identifies work that should be done proactively based on "
            "observed context. None on every turn the model does not "
            "declare a goal. Engine stages a `declared_goal` memory "
            "candidate when present."
        ),
    )
    goal_revision: GoalRevision | None = Field(
        default=None,
        description=(
            "Optional model-authored goal revision. Set when the model has "
            "already declared a goal and observed context now justifies a "
            "typed revision. Runtime persists authorized revisions as "
            "`goal_revision` records."
        ),
    )
    act_profile: ActProfile | None = None
    execution_target: ExecutionTargetPayload | None = Field(
        default=None,
        description=(
            "Execution ownership for act-mode work. Include this explicitly when "
            "delegation is requested or another execution owner is already clear. "
            "Ordinary local act work may rely on runtime defaults."
        ),
    )
    max_steps_hint: int | None = Field(default=None, ge=1)
    subtasks: list[Any] = Field(default_factory=list)

    @property
    def mode(self) -> str:
        """Mode helper."""
        return self.route

    @mode.setter
    def mode(self, value: Any) -> None:
        self.route = _normalize_route_name(value)

    @model_validator(mode="before")
    @classmethod
    def flatten_branch_payloads(cls, value: Any) -> Any:
        return _flatten_branch_payloads(value)

    @field_validator("route", mode="before")
    @classmethod
    def validate_route_name(cls, value: Any) -> Any:
        return _normalize_route_name(value)

    @field_validator("sub_intents", mode="before")
    @classmethod
    def validate_sub_intents_wire_shape(cls, value: Any) -> Any:
        return _normalize_sub_intents(value)

    @field_validator("confidence", mode="before")
    @classmethod
    def normalize_confidence(cls, value: Any) -> Any:
        if value in (None, ""):
            return 0.5
        if isinstance(value, str):
            raw = value.strip().lower()
            if not raw:
                return 0.5
            if raw in {"high", "hi"}:
                return 0.9
            if raw in {"medium", "med", "mid"}:
                return 0.6
            if raw in {"low", "lo"}:
                return 0.3
            try:
                parsed = float(raw)
            except ValueError:
                return 0.5
            return max(0.0, min(1.0, parsed))
        return value

    @field_validator("reason_code", mode="before")
    @classmethod
    def normalize_reason_code(cls, value: Any) -> str:
        return _normalize_stripped_text(value)

    @field_validator("rationale", mode="before")
    @classmethod
    def normalize_rationale(cls, value: Any) -> str:
        return _normalize_stripped_text(value)

    @field_validator("execution_target", mode="before")
    @classmethod
    def normalize_execution_target(cls, value: Any) -> Any:
        if value in (None, ""):
            return None
        return value

    @field_validator("subtasks", mode="before")
    @classmethod
    def normalize_subtasks(cls, value: Any) -> Any:
        return normalize_decomposed_subtasks(value)

    @model_validator(mode="after")
    def validate_shape(self) -> "_DecisionBase":
        if self.route == "respond":
            if self.respond_kind is None:
                raise ValueError("respond_kind is required when route=respond")
            if self.respond_kind == "clarify" and not str(self.question or "").strip():
                raise ValueError("question is required when respond_kind=clarify")
            if self.respond_kind == "answer" and not str(self.answer or "").strip():
                raise ValueError("answer is required when respond_kind=answer")
            if (
                self.respond_kind == "clarify"
                and self.request_readiness is not None
                and self.request_readiness.state != "needs_user"
            ):
                raise ValueError(
                    "respond_kind='clarify' requires request_readiness.state='needs_user'"
                )
        elif self.route == "act":
            if (
                self.execution_target is not None
                and self.execution_target.kind == "delegated"
                and not (
                    str(self.execution_target.target_agent_id or "").strip()
                    or str(self.execution_target.target_capability or "").strip()
                )
            ):
                raise ValueError(
                    "target_agent_id or target_capability is required when "
                    "execution_target.kind=delegated"
                )
        if (
            self.request_readiness is not None
            and self.request_readiness.requested_outcome == "execute"
            and self.request_readiness.state == "ready"
            and self.route != "act"
        ):
            raise ValueError("execute + ready decisions must route to act")
        return self


class RespondDecision(_DecisionBase):
    route: Literal["respond"] = Field(
        default="respond", description="Routing decision."
    )
    respond_kind: RespondKind
    answer: str = ""


class ActDecision(_DecisionBase):
    route: Literal["act"] = Field(default="act", description="Routing decision.")
    act_profile: ActProfile | None = None
    execution_target: ExecutionTargetPayload | None = None


Decision = _DecisionBase


class _FlatDecisionCompatModel(_DecisionBase):
    route: DecisionRoute = Field(..., description="Routing decision.")


_BUILTIN_DECISION_MODELS: dict[str, type[_DecisionBase]] = {
    "respond": RespondDecision,
    "act": ActDecision,
}


class _NamedDecisionAdapter:
    __name__ = "Decision"

    def validate_python(self, value: Any) -> Decision:
        if isinstance(value, _DecisionBase):
            return value
        if not isinstance(value, Mapping):
            raise ValidationError.from_exception_data(
                "Decision",
                [
                    {
                        "type": "model_type",
                        "loc": (),
                        "msg": "Input must be a mapping",
                        "input": value,
                    }
                ],
            )
        normalized = _flatten_branch_payloads(value)
        route_name = _normalize_route_name(normalized.get("route"))
        model = _BUILTIN_DECISION_MODELS[route_name]
        validation = validate_payload(
            normalized,
            model=model,
            ctx=TCRPContext(channel_name="brain.decision"),
            retry_budget=None,
        )
        if validation.structured_payload is not None:
            return validation.structured_payload
        raise _validation_error_from_tcrp(
            "Decision", value, validation.validation_errors
        )

    def validate_json(self, value: Any) -> Decision:
        if isinstance(value, (bytes, bytearray, str)):
            validation = validate_payload(
                value,
                model=_FlatDecisionCompatModel,
                ctx=TCRPContext(channel_name="brain.decision.json"),
                allow_code_fence=True,
                retry_budget=None,
            )
            if validation.structured_payload is None:
                raise _validation_error_from_tcrp(
                    "Decision",
                    value,
                    validation.validation_errors,
                )
            flat = validation.structured_payload
            return self.validate_python(
                flat.model_dump(mode="python", exclude_none=False)
            )
        raise TypeError("DecisionAdapter.validate_json expects str/bytes input")

    def json_schema(self) -> dict[str, Any]:
        return self.flat_json_schema()

    def flat_json_schema(self) -> dict[str, Any]:
        schema = _FlatDecisionCompatModel.model_json_schema()
        properties = schema.setdefault("properties", {})
        properties.pop("mode", None)
        route_schema = properties.setdefault("route", {})
        route_schema["type"] = "string"
        route_schema["enum"] = _registry_route_names()
        route_schema["description"] = _route_field_description()
        return schema


def _validation_error_from_tcrp(
    title: str,
    raw_input: Any,
    validation_errors: tuple[Any, ...],
) -> ValidationError:
    line_errors: list[dict[str, Any]] = []
    for item in validation_errors:
        field_path = str(getattr(item, "field_path", "<root>") or "<root>")
        loc = () if field_path == "<root>" else tuple(field_path.split("."))
        message = (
            f"expected {getattr(item, 'expected_type', 'structured_value')} "
            f"got {getattr(item, 'actual_type', 'unknown')}"
        )
        line_errors.append(
            {
                "type": PydanticCustomError("tcrp_validation", message),
                "loc": loc,
                "input": raw_input,
            }
        )
    if not line_errors:
        line_errors.append(
            {
                "type": PydanticCustomError(
                    "tcrp_validation",
                    "typed-channel validation failed",
                ),
                "loc": (),
                "input": raw_input,
            }
        )
    return ValidationError.from_exception_data(title, line_errors)


DecisionAdapter = _NamedDecisionAdapter()


__all__ = [
    "ActDecision",
    "ActProfile",
    "ClarifyContext",
    "ConfidentComplete",
    "Decision",
    "DecisionAdapter",
    "DecisionRoute",
    "DelegationContext",
    "DelegationResultSummary",
    "ExecutionTargetPayload",
    "normalize_decomposed_subtasks",
    "RequestAssumption",
    "RequestAssumptionSource",
    "RequestedOutcome",
    "RequestPosture",
    "RequestReadiness",
    "RequestReadinessState",
    "RespondDecision",
    "RespondKind",
]
