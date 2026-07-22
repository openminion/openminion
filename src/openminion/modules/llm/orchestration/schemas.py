import uuid
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..schemas import Message, ResponseError

EnsembleMode = Literal["second_opinion", "panel_judge", "self_consistency", "vote"]
SelectionPolicyName = Literal[
    "pick_primary_if_ok",
    "pick_highest_score",
    "majority_vote",
    "first_success",
    "ask_user_on_disagreement",
]
CandidateStatus = Literal["success", "failed", "timeout"]
FallbackMode = Literal["single", "ensemble"]
ProviderCapabilityName = Literal[
    "json",
    "tools",
    "vision",
    "streaming",
    "prompt_caching",
    "cost",
    "auth",
]


class ProfileCostHint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_per_1k: Optional[float] = None
    output_per_1k: Optional[float] = None


class ProfileCapabilities(BaseModel):
    model_config = ConfigDict(extra="allow")

    supports_json: bool = False
    supports_tools: bool = False
    supports_vision: bool = False
    supports_streaming: bool = False
    supports_prompt_caching: bool = False


class ProviderProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1)
    provider: str = Field(..., min_length=1)
    model: str = Field(..., min_length=1)
    endpoint: Optional[str] = None
    auth_ref: Optional[str] = None
    params: dict[str, Any] = Field(default_factory=dict)
    capabilities: ProfileCapabilities = Field(default_factory=ProfileCapabilities)
    supports_json: bool = False
    supports_tools: bool = False
    supports_vision: bool = False
    supports_streaming: bool = False
    supports_prompt_caching: bool = False
    cost_hint: Optional[ProfileCostHint] = None
    tags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _sync_capabilities(self) -> "ProviderProfile":
        caps = self.capabilities
        self.supports_json = bool(self.supports_json or caps.supports_json)
        self.supports_tools = bool(self.supports_tools or caps.supports_tools)
        self.supports_vision = bool(self.supports_vision or caps.supports_vision)
        self.supports_streaming = bool(
            self.supports_streaming or caps.supports_streaming
        )
        self.supports_prompt_caching = bool(
            self.supports_prompt_caching or caps.supports_prompt_caching
        )
        return self


class RubricCriterion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    weight: float = 1.0


class Rubric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instructions: str = ""
    criteria: list[RubricCriterion] = Field(default_factory=list)


class NormalizationRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strip_whitespace: bool = True
    lowercase: bool = False


class DisagreementConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    method: Literal["simple_text", "json_field_diff"] = "simple_text"
    threshold: float = 0.75
    max_excerpt_chars: int = 240


class EnsembleTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1)
    mode: EnsembleMode
    providers: list[str] = Field(default_factory=list)
    judge_profile_id: Optional[str] = None
    selection_policy: SelectionPolicyName = "pick_primary_if_ok"
    rubric: Optional[Rubric] = None
    timeout_ms: int = 30000
    max_parallel: int = 2
    stop_early: bool = False
    normalization: Optional[NormalizationRules] = None
    disagreement: Optional[DisagreementConfig] = None


class LLMCatalogDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_timeout_ms: int = 30000
    default_max_parallel: int = 2
    default_selection_policy: SelectionPolicyName = "pick_primary_if_ok"
    default_rubric: Optional[Rubric] = None


class GlobalLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_parallel_global: int = 8
    max_inflight_requests: Optional[int] = None
    max_tokens_per_call_hard: int = 8192


class CatalogLoggingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    store_raw_provider_payloads: bool = False
    store_normalized_candidates: bool = False
    store_ensemble_report: bool = True
    emit_events: bool = True


class SecretResolutionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    env_prefix: str = ""


class LLMCatalogConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    profiles: list[ProviderProfile] = Field(default_factory=list)
    ensembles: list[EnsembleTemplate] = Field(default_factory=list)
    defaults: LLMCatalogDefaults = Field(default_factory=LLMCatalogDefaults)
    limits: GlobalLimits = Field(default_factory=GlobalLimits)
    logging: CatalogLoggingConfig = Field(default_factory=CatalogLoggingConfig)
    secrets: SecretResolutionConfig = Field(default_factory=SecretResolutionConfig)

    @model_validator(mode="after")
    def _validate_ids(self) -> "LLMCatalogConfig":
        profile_ids = [item.id for item in self.profiles]
        ensemble_ids = [item.id for item in self.ensembles]
        if len(profile_ids) != len(set(profile_ids)):
            raise ValueError(
                "profiles.*.id must be unique"
            )  # allow-bare-raise: pydantic @model_validator body
        if len(ensemble_ids) != len(set(ensemble_ids)):
            raise ValueError(
                "ensembles.*.id must be unique"
            )  # allow-bare-raise: pydantic @model_validator body
        return self


class SingleRoute(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["single"]
    profile_id: str = Field(..., min_length=1)
    params_override: Optional[dict[str, Any]] = None
    timeout_ms: Optional[int] = None


class EnsembleRoute(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["ensemble"]
    strategy_id: Optional[str] = None
    strategy_inline: Optional[EnsembleTemplate] = None
    providers: Optional[list[str]] = None
    judge_profile_id: Optional[str] = None
    selection_policy: Optional[SelectionPolicyName] = None
    rubric: Optional[Rubric] = None
    timeout_ms: Optional[int] = None
    max_parallel: Optional[int] = None
    stop_early: Optional[bool] = None
    fanout: Optional[int] = None

    @model_validator(mode="after")
    def _validate_ensemble_route(self) -> "EnsembleRoute":
        if self.strategy_id or self.strategy_inline or self.providers:
            return self
        raise ValueError(  # allow-bare-raise: pydantic @model_validator body
            "Ensemble route requires strategy_id, strategy_inline, or providers"
        )


LLMRoute = SingleRoute | EnsembleRoute


class AgentLLMBudgets(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_tokens_per_call: int = 2048
    max_tokens_per_turn: int = 8192
    max_cost_per_turn: Optional[float] = None
    max_parallel: int = 2
    max_ensemble_fanout: int = 3
    max_time_ms_per_turn: int = 120000


class FallbackPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fallback_profile_ids: list[str] = Field(default_factory=list)
    fallback_mode: FallbackMode = "single"
    on_error_codes: Optional[list[str]] = None
    max_fallback_attempts: int = 1


class AgentLLMPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_route: Optional[LLMRoute] = None
    by_purpose: dict[str, LLMRoute] = Field(default_factory=dict)
    allow_profiles: Optional[list[str]] = None
    deny_profiles: Optional[list[str]] = None
    budgets: AgentLLMBudgets = Field(default_factory=AgentLLMBudgets)
    fallbacks: dict[str, FallbackPolicy] = Field(default_factory=dict)
    overrides: dict[str, Any] = Field(default_factory=dict)


class TraceContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: Optional[str] = None
    trace_id: Optional[str] = None
    span_id: Optional[str] = None
    agent_id: Optional[str] = None
    task_id: Optional[str] = None


class RequestBudget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timeout_ms: int = 30000
    max_tokens: int = 1024
    max_cost: Optional[float] = None


class RuntimeLLMRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    purpose: str = Field(default="act", min_length=1)
    messages: list[Message] = Field(default_factory=list)
    output_schema: Optional[dict[str, Any]] = None
    required_capabilities: list[ProviderCapabilityName] = Field(default_factory=list)
    constraints: Optional[dict[str, Any]] = None
    budget: RequestBudget = Field(default_factory=RequestBudget)
    trace: TraceContext = Field(default_factory=TraceContext)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Usage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_estimate: Optional[float] = None


class CandidateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    candidate_id: str
    profile_id: str
    provider: str
    model: str
    status: CandidateStatus
    text: Optional[str] = None
    json_output: Optional[dict[str, Any]] = Field(
        default=None, alias="json", serialization_alias="json"
    )
    usage: Usage = Field(default_factory=Usage)
    error: Optional[ResponseError] = None
    raw_artifact_ref: Optional[str] = None


class SelectionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    winner_candidate_id: str
    winner_profile_id: str
    scores: Optional[dict[str, float]] = None
    reasons: list[str] = Field(default_factory=list)
    risk_flags: Optional[list[str]] = None


class DisagreementCluster(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_ids: list[str] = Field(default_factory=list)
    excerpt: str = ""


class DisagreementReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = ""
    clusters: list[DisagreementCluster] = Field(default_factory=list)
    json_diffs: Optional[dict[str, Any]] = None
    risk_flags: list[str] = Field(default_factory=list)


class UsageTotal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    latency_ms_total: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_estimate: Optional[float] = None


class EnsembleResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    mode: EnsembleMode
    candidates: list[CandidateResponse] = Field(default_factory=list)
    selection: Optional[SelectionResult] = None
    disagreement: Optional[DisagreementReport] = None
    usage_total: UsageTotal = Field(default_factory=UsageTotal)
