# ruff: noqa: F403,F405
from .common import *
from .segments import (
    ContextDecisionTraceV1,
    ContextSegment,
    PackingDecisionLog,
    RenderMessage,
    TokenBudgetReport,
)


class IdentityManifest(BaseModel):
    agent_id: str
    profile_version: str
    render_version: str


class SessionManifest(BaseModel):
    slice_version: str
    turn_index: int = 0
    turn_ids_included: List[str] = Field(default_factory=list)


class ArtifactManifestItem(BaseModel):
    ref: str
    view_id: Optional[str] = None
    digest_hash: str = ""


class RetrievalMetadata(BaseModel):
    strategy: str = ""
    score: float = 0.0
    node_id: Optional[str] = None
    level: Optional[str] = None


class CompressionMetadata(BaseModel):
    method_id: str = ""
    ratio: float = 0.0
    compression_hash: str = ""


class RetrievalSummary(BaseModel):
    total_chunks: int = 0
    selected_chunks: int = 0
    strategies_used: List[str] = Field(default_factory=list)
    chunks: List[RetrievalMetadata] = Field(default_factory=list)


class CompressionSummary(BaseModel):
    total_items_compressed: int = 0
    avg_ratio: float = 0.0
    methods_used: List[str] = Field(default_factory=list)
    items: List[CompressionMetadata] = Field(default_factory=list)


class MidSessionIntentSnapshot(BaseModel):
    intent_id: str
    status: str


class MidSessionRecallSnapshot(BaseModel):
    turn_index: int = 0
    intent_states: List[MidSessionIntentSnapshot] = Field(default_factory=list)
    latest_user_message: str = ""
    active_skill_id: Optional[str] = None
    resolved_skill_ids: List[str] = Field(default_factory=list)
    plan_cursor: int = 0
    plan_step_ids: List[str] = Field(default_factory=list)
    recent_tool_families: List[str] = Field(default_factory=list)


class ContextManifest(BaseModel):
    identity: IdentityManifest
    session: SessionManifest
    facts: List[str] = Field(default_factory=list)
    memory: List[str] = Field(default_factory=list)
    recalled_memory: List[str] = Field(default_factory=list)
    session_start_recalled_memory: List[str] = Field(default_factory=list)
    mid_session_recalled_memory: List[str] = Field(default_factory=list)
    recent_session_artifacts: List[str] = Field(default_factory=list)
    procedures: List[str] = Field(default_factory=list)
    artifacts: List[ArtifactManifestItem] = Field(default_factory=list)
    segment_ids: List[str] = Field(
        default_factory=list
    )  # all assembled segment IDs (incl. dropped)
    included_segment_ids: List[str] = Field(
        default_factory=list
    )  # surviving segment IDs
    dropped_segment_ids: List[str] = Field(default_factory=list)  # dropped segment IDs
    retrieval_summary: Optional[RetrievalSummary] = None
    compression_summary: Optional[CompressionSummary] = None
    static_prefix_hash: str = ""
    prompt_cache_key: str = ""
    prompt_context_id: Optional[str] = None
    rolled_over: bool = False
    rollover_reason: Optional[str] = None
    llm_call_id: Optional[str] = None
    context_budget_tier: Optional[ContextBudgetTier] = None
    pack_policy_used: str = ""
    retrievers_used: List[str] = Field(default_factory=list)
    compressors_used: List[str] = Field(default_factory=list)
    mid_session_recall_state: Optional[MidSessionRecallSnapshot] = None
    active_state_prompt_view: Optional[Dict[str, Any]] = Field(default_factory=dict)
    active_state_full: Optional[Dict[str, Any]] = Field(default_factory=dict)
    active_state_metrics: Optional[Dict[str, int]] = Field(default_factory=dict)
    decision_trace: Optional[ContextDecisionTraceV1] = None


class ContextPack(BaseModel):
    """Canonical context payload with segment-first provenance."""

    session_id: str
    agent_id: str
    purpose: Purpose
    segments: List[ContextSegment] = Field(default_factory=list)
    messages: List[RenderMessage] = Field(default_factory=list)
    profile_version: str
    render_version: str
    slice_version: str
    pack_version: str
    pack_hash: str
    prompt_cache_key: str = ""
    static_prefix_hash: str = ""
    context_manifest: Optional[ContextManifest] = None
    token_budget_report: Optional[TokenBudgetReport] = None
    pack_policy: Optional[PackingDecisionLog] = None
    warnings: List[str] = Field(default_factory=list)
    prompt_context_id: Optional[str] = None
    seed_bundle_id: Optional[str] = None
    introspection_digest: Optional[Dict[str, Any]] = Field(default=None)


class TokenReport(BaseModel):
    total_tokens: int = Field(ge=0)
    per_message_tokens: List[int] = Field(default_factory=list)
