from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from openminion.base.time import utc_now_iso as iso_now

Purpose = Literal[
    "decide", "plan", "act", "reflect", "summarize", "judge", "validate", "chat"
]
VerificationMode = Literal["none", "rule_based", "panel_judge"]
SourceType = Literal["wm", "em", "sm", "skill", "session"]
IntentType = Literal["fact", "lesson", "procedure"]
RetrievalStrategy = Literal["auto", "contextual", "raptor", "longrag_doc_group"]
RetrievalQuality = Literal["GOOD", "OK", "BAD"]
RetrievalUnitKind = Literal["chunk", "doc_group", "document", "unknown"]
RaptorLevel = Literal["none", "internal", "leaf"]
EvidenceRefType = Literal["artifact", "event", "memory", "session", "skill", "other"]


class RLMBudgets(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_prompt_tokens: int = Field(default=2800, ge=256)
    max_output_tokens: int = Field(default=700, ge=64)
    max_ticks: int = Field(default=4, ge=1, le=24)
    max_tool_calls: int = Field(default=4, ge=0)
    timeout_ms: int = Field(default=45000, ge=1000)


class RetrievalLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    k_sm: int = Field(default=4, ge=0, le=32)
    k_em: int = Field(default=6, ge=0, le=64)
    k_skill: int = Field(default=1, ge=0, le=8)
    k_total: int = Field(default=12, ge=1, le=128)
    artifact_scan_limit: int = Field(default=32, ge=8, le=256)
    text_snippet_chars: int = Field(default=1000, ge=200, le=8000)
    recency_half_life_hours: int = Field(default=24, ge=1, le=24 * 30)


class RetrievalFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    include_sources: list[SourceType] = Field(
        default_factory=lambda: ["sm", "em", "skill"]
    )
    tags: list[str] = Field(default_factory=list)
    time_window_hours: int | None = Field(default=None, ge=1, le=24 * 365)
    strategy: RetrievalStrategy = "auto"
    scope: dict[str, str] = Field(default_factory=dict)


class RLMConstraints(BaseModel):
    model_config = ConfigDict(extra="forbid")

    output_schema: dict[str, Any] | None = None
    evidence_only: bool = False
    style: dict[str, str] = Field(default_factory=dict)
    risk_level: Literal["low", "medium", "high"] = "low"
    verification_mode: VerificationMode = "none"
    must_cite_evidence: bool = False
    retrieval_strategy: RetrievalStrategy = "auto"
    self_reflect: bool = False


class MetaDirective(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_ticks_override: int | None = Field(default=None, ge=1, le=24)
    retrieval_cap_override: int | None = Field(default=None, ge=1, le=64)
    require_evidence: bool = False
    verification_mode: VerificationMode = "none"
    retrieve_only_if_good: bool = False
    max_bad_retrieval_streak: int | None = Field(default=None, ge=1, le=8)


class TaskState(BaseModel):
    model_config = ConfigDict(extra="allow")

    plan_id: str | None = None
    step_id: str | None = None
    retry_count: int = Field(default=0, ge=0)
    verification_mode: VerificationMode = "none"
    budget_tier: Literal["normal", "cautious", "high_assurance"] = "normal"
    invariants: list[str] = Field(default_factory=list)
    retrieve_only_if_good: bool = False


class WMState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    wm_version: int = Field(default=1, ge=1)
    objective: str = ""
    constraints: list[str] = Field(default_factory=list)
    current_step: str | None = None
    step_cursor: str | None = None
    key_decisions: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    must_not_forget: list[str] = Field(default_factory=list)
    invariants: list[str] = Field(default_factory=list)
    tool_summaries: list[str] = Field(default_factory=list)
    updated_at: str = Field(default_factory=iso_now)


class RetrievedContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: SourceType
    ref_id: str
    text: str
    score: float = 0.0
    recency_score: float = 0.0
    tags: list[str] = Field(default_factory=list)
    created_at: str | None = None
    unit_kind: RetrievalUnitKind = "unknown"
    retrieval_strategy: RetrievalStrategy = "auto"
    raptor_level: RaptorLevel = "none"
    node_id: str | None = None
    doc_group_id: str | None = None
    trust_score: float = Field(default=0.0, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryWriteIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent_type: IntentType
    title: str
    content: dict[str, Any] | str
    salience: float = Field(default=0.5, ge=0.0, le=1.0)
    ttl_seconds: int | None = Field(default=None, ge=1)
    tags: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)


class EvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref_type: EvidenceRefType = "other"
    ref_id: str
    source: SourceType | None = None
    note: str | None = None


class RetrievalEval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quality: RetrievalQuality = "BAD"
    top_score: float = 0.0
    mean_score: float = 0.0
    trusted_ratio: float = 0.0
    duplicate_ratio: float = 0.0
    score_histogram: dict[str, int] = Field(default_factory=dict)
    action: str = ""


class TickTelemetry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tick_index: int = Field(ge=1)
    phases: list[str] = Field(default_factory=list)
    retrieval_strategy: RetrievalStrategy = "auto"
    retrieval_k: int = 0
    retrieval_quality: RetrievalQuality = "BAD"
    retrieval_action: str = ""
    retrieval_score_histogram: dict[str, int] = Field(default_factory=dict)
    retrieved_total: int = 0
    retrieved_sm: int = 0
    retrieved_em: int = 0
    retrieved_skill: int = 0
    selected_unit_kinds: list[str] = Field(default_factory=list)
    selected_raptor_levels: list[str] = Field(default_factory=list)
    compression_method: str = ""
    compression_ratio: float = 1.0
    compression_input_tokens: int = 0
    compression_output_tokens: int = 0
    used_empty_augmentation: bool = False
    pack_hash: str = ""
    llm_status: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    citation_coverage: float = 0.0
    stop: bool = False
    stop_reason: str = ""


class RLMTelemetry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticks_used: int = 0
    stop_reason: str = ""
    retrieval_stats: dict[str, int] = Field(default_factory=dict)
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    max_bad_retrieval_streak: int = 0
    tick_reports: list[TickTelemetry] = Field(default_factory=list)


class RLMContinuation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    needs_more_ticks: bool = False
    suggested_next_query: str | None = None
    reason: str = ""


class RLMResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    final_text: str = ""
    structured_output: dict[str, Any] | None = None
    final_json: dict[str, Any] | None = None
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    memory_write_intents: list[MemoryWriteIntent] = Field(default_factory=list)
    wm_update: WMState
    telemetry: RLMTelemetry
    continuation: RLMContinuation | None = None


class RLMConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    budgets: RLMBudgets = Field(default_factory=RLMBudgets)
    retrieval: RetrievalLimits = Field(default_factory=RetrievalLimits)
    wm_max_items_per_list: int = Field(default=8, ge=2, le=32)
    wm_max_tool_summaries: int = Field(default=6, ge=1, le=16)
    allow_empty_augmentation: bool = True
    quality_good_threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    quality_ok_threshold: float = Field(default=0.35, ge=0.0, le=1.0)
    duplication_bad_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    bad_retrieval_escalation_ticks: int = Field(default=2, ge=1, le=8)
    default_retrieval_strategy: RetrievalStrategy = "auto"
    compression_method_id: str = "extractive.v1"
    compression_extractive_max_blocks_good: int = Field(default=8, ge=1, le=32)
    compression_extractive_max_blocks_ok: int = Field(default=4, ge=1, le=16)
    compression_extractive_max_blocks_bad: int = Field(default=1, ge=0, le=8)
    default_agent_policy: dict[str, Any] = Field(default_factory=dict)


class TickOutput(BaseModel):
    model_config = ConfigDict(extra="allow")

    final: bool = False
    answer: str = ""
    structured_output: dict[str, Any] | None = None
    next_query: str | None = None
    episode_note: str = ""
    evidence_refs: list[str] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)
    wm_update: dict[str, Any] = Field(default_factory=dict)
    memory_write_intents: list[MemoryWriteIntent] = Field(default_factory=list)
