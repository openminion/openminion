import hashlib
import json
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from . import constants as _context_constants

from openminion.modules.task.plan import (  # noqa: F401
    TaskPlan,
    TaskPlanDifficulty,
    TaskPlanRevision,
    TaskPlanStatus,
    TaskPlanStep,
    TaskPlanStepBlocked,
    TaskPlanStepCompleted,
    TaskPlanStepStatus,
    TaskPlanTerminalSignal,
    TaskPlanToolFamily,
)

Purpose = Literal[
    "decide", "plan", "act", "reflect", "summarize", "judge", "validate", "chat"
]
MessageRole = Literal["system", "developer", "user", "assistant", "tool"]
ARTIFACT_PREVIEW_MAX_CHARS = _context_constants.ARTIFACT_PREVIEW_MAX_CHARS
ARTIFACT_PREVIEW_MAX_BULLETS = _context_constants.ARTIFACT_PREVIEW_MAX_BULLETS
PINNED_BUCKETS = _context_constants.PINNED_BUCKETS
TRIM_ORDER = _context_constants.TRIM_ORDER
TASK_PLAN_OUTPUT_SUMMARY_MAX_CHARS = (
    _context_constants.TASK_PLAN_OUTPUT_SUMMARY_MAX_CHARS
)
TASK_PLAN_TOOL_FAMILIES = _context_constants.TASK_PLAN_TOOL_FAMILIES


ContextBudgetTier = Literal["short", "medium", "full"]
ContextDecisionTracePersistenceStatus = Literal["pending", "persisted", "degraded"]
ContextTracePersistenceReason = Literal[
    "persisted_canonical",
    "persisted_fallback",
    "canonical_failed",
    "fallback_failed",
    "no_persistence_sink",
    "not_attempted",
]

CONTEXT_DECISION_TRACE_VERSION = _context_constants.CONTEXT_DECISION_TRACE_VERSION
CONTEXT_DECISION_TRACE_MAX_REFERENCES = (
    _context_constants.CONTEXT_DECISION_TRACE_MAX_REFERENCES
)
CONTEXT_DECISION_TRACE_MAX_BYTES = _context_constants.CONTEXT_DECISION_TRACE_MAX_BYTES


class LastResultSummary(BaseModel):
    """ASPM-03: Structured summary of last_result for prompt projection."""

    command: Optional[str] = None
    tool: Optional[str] = None
    status: str = "unknown"
    exit_code: Optional[int] = None
    summary: str = ""
    artifact_refs: List[str] = Field(default_factory=list)


class IntentExecutionPromptView(BaseModel):
    intent_id: str
    status: str
    depends_on: List[str] = Field(default_factory=list)
    last_step_index: Optional[int] = None
    updated_at: Optional[str] = None


class PlanProgressPromptView(BaseModel):
    has_plan: bool = False
    step_count: int = 0
    cursor: int = 0


class ActiveStatePromptView(BaseModel):
    """Compact prompt-facing projection of active state."""

    state_ref: Optional[str] = None
    task_id: Optional[str] = None
    task_description: Optional[str] = None
    status: str = "idle"
    last_result: Optional[LastResultSummary] = None
    open_questions: List[str] = Field(default_factory=list)
    declared_sub_intents: List[str] = Field(default_factory=list)
    intent_execution_states: List[IntentExecutionPromptView] = Field(
        default_factory=list
    )
    plan_progress: Optional[PlanProgressPromptView] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


SegmentBucket = Literal[
    "static_prefix",
    "mission_snapshot",
    "budget_telemetry",
    "summaries",
    "conversation_summary",
    "active_plan",
    "task_digest",
    "trailer_feedback",
    "self_awareness",
    "recent_window",
    "memory",
    "retrieval",
    "evidence_refs",
    "turn_input",
]

BlockPriority = Literal["P0", "P1", "P2", "P3", "P4"]
BlockType = Literal[
    "identity",
    "safety",
    "task_header",
    "summary",
    "continuation",
    "active_state",
    "facts",
    "memory",
    "skills",
    "artifacts",
    "instructions",
    "dialogue",
    "tool_events",
    "retrieval",
]


class ContextBudgets(BaseModel):
    total_max_tokens: int = Field(ge=1)
    identity_tokens: int = Field(ge=1)
    summary_tokens: int = Field(ge=1)
    conversation_summary_tokens: int = Field(default=0, ge=0)
    active_plan_tokens: int = Field(default=0, ge=0)
    task_digest_tokens: int = Field(default=0, ge=0)
    trailer_feedback_tokens: int = Field(default=0, ge=0)
    recent_turn_tokens: int = Field(ge=1)
    facts_tokens: int = Field(ge=0)
    memory_tokens: int = Field(ge=0)
    skills_tokens: int = Field(ge=0)
    artifact_tokens: int = Field(ge=0)
    instructions_tokens: int = Field(ge=1)


class SkillSnippetRef(BaseModel):
    skill_id: str
    version_hash: Optional[str] = None


class BuildConstraints(BaseModel):
    output_schema: Optional[Dict[str, Any]] = None
    style_overrides: Dict[str, str] = Field(default_factory=dict)
    safety_tags: List[str] = Field(default_factory=list)
    procedure_id: Optional[str] = None
    skill_id: Optional[str] = None
    skill_version_hash: Optional[str] = None
    skill_refs: List[SkillSnippetRef] = Field(default_factory=list)
    context_budget_tier: Optional[ContextBudgetTier] = None
    tool_schemas: List[Dict[str, Any]] = Field(default_factory=list)
    runtime_tool_schemas: List[Dict[str, Any]] = Field(default_factory=list)


class BuildPackRequest(BaseModel):
    session_id: str
    agent_id: str
    purpose: Purpose
    mode_name: Optional[str] = None
    query: str
    provider_pref: Optional[str] = None
    budgets_override: Optional[ContextBudgets] = None
    constraints: Optional[BuildConstraints] = None
    model_hint: Optional[str] = None
    llm_call_id: Optional[str] = None
    introspection_intent: bool = Field(default=False)
    budget_telemetry: Dict[str, Any] = Field(default_factory=dict)
    live_state_overlay: Dict[str, Any] = Field(default_factory=dict)
    phase_hints: Dict[str, Any] = Field(default_factory=dict)
    gateway_system_context: str = ""
    self_awareness: Dict[str, Any] = Field(default_factory=dict)


class SessionTurn(BaseModel):
    turn_id: str
    role: str
    content: str
    ts: Optional[str] = None
    is_error: bool = False


class SessionToolEvent(BaseModel):
    event_id: str
    tool_name: str
    excerpt: str
    artifact_refs: List[str] = Field(default_factory=list)


class SessionSlice(BaseModel):
    session_id: str
    slice_version: str
    last_event_id: Optional[str] = None
    summary_short: str
    summary_long: Optional[str] = None
    conversation_summary: str = ""
    active_task_plan: Optional[TaskPlan] = None
    continuation: Optional[Dict[str, Any]] = None
    task_digest: Optional[Dict[str, Any]] = None
    pending_trailer_feedback: Optional[Dict[str, Any]] = None
    total_turn_count: int = Field(default=0, ge=0)
    recent_turns: List[SessionTurn] = Field(default_factory=list)
    open_tasks: List[str] = Field(default_factory=list)
    active_state: Optional[Dict[str, Any]] = None
    recent_tool_events: List[SessionToolEvent] = Field(default_factory=list)
    prompt_context_id: Optional[str] = None
    checkpoint_id: Optional[str] = None
    seed_bundle_id: Optional[str] = None
    archive_refs: List[str] = Field(default_factory=list)


class IdentitySnippet(BaseModel):
    agent_id: str
    purpose: str = ""
    profile_version: str
    render_version: str
    text: str
    budget: Optional[dict] = None
    sections: Optional[Dict[str, str]] = None
    included_fields: List[str] = Field(default_factory=list)
    omitted_fields: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class FactRecord(BaseModel):
    record_id: str
    text: str
    score: float = 0.0
    confidence: float = 0.0
    ttl_valid: bool = True
    record_type: str = "fact"
    source: str = ""
    tags: List[str] = Field(default_factory=list)
    meta: Dict[str, Any] = Field(default_factory=dict)


class MemoryCard(BaseModel):
    record_id: str
    record_type: str
    text: str
    score: float = 0.0
    pinned: bool = False
    source: str = ""
    tags: List[str] = Field(default_factory=list)
    meta: Dict[str, Any] = Field(default_factory=dict)


class RecentSessionArtifactRef(BaseModel):
    record_id: str
    artifact_type: str
    artifact_path: str
    artifact_digest: str = ""
    session_id: str
    turn_index: int = 0
    tool_name: str = ""


class ProcedureSnippet(BaseModel):
    procedure_id: str
    title: str
    preflight: List[str] = Field(default_factory=list)
    steps: List[str] = Field(default_factory=list)
    rollback_hint: str = ""


class ArtifactDigest(BaseModel):
    ref: str
    view_id: Optional[str] = None
    digest_hash: str = ""
    bullets: List[str] = Field(default_factory=list)
    excerpt: Optional[str] = None
    score: float = 0.0


class RenderMessage(BaseModel):
    role: MessageRole
    content: str
    cache_control: Optional[Dict[str, Any]] = None
    meta: Dict[str, Any] = Field(default_factory=dict)


class ContextSegment(BaseModel):
    """Typed segment inside a `ContextPack`."""

    id: str  # stable identifier (e.g. "safety", "identity", "turn:<turn_id>")
    bucket: SegmentBucket
    role: MessageRole = "system"
    content: str
    token_estimate: int = Field(ge=0)
    content_hash: str = ""
    refs: List[str] = Field(default_factory=list)
    is_artifact_preview: bool = False
    is_cacheable: bool = False
    cache_key: str = ""
    cache_invalidation_refs: List[str] = Field(default_factory=list)
    pinned: bool = False  # mission_snapshot/safety/identity: always True
    metadata: Dict[str, Any] = Field(default_factory=dict)


class BucketAllocation(BaseModel):
    bucket: SegmentBucket
    cap_tokens: int = Field(ge=0)
    used_tokens: int = Field(ge=0)
    selected_count: int = Field(ge=0)
    total_available: int = Field(ge=0)
    dropped_count: int = Field(ge=0)
    trim_applied: bool = False


class TrimAction(BaseModel):
    action: str  # e.g. "drop_segment", "shrink_preview", "shrink_recent_window"
    reason_code: str  # e.g. "over_budget", "artifact_too_large"
    segment_ids: List[str] = Field(default_factory=list)
    bucket: Optional[str] = None
    tokens_saved: int = 0


class PackingDecisionLog(BaseModel):
    """Ordered log of all trim actions taken during pack assembly."""

    actions: List[TrimAction] = Field(default_factory=list)
    total_tokens_saved: int = 0
    invariants_preserved: List[str] = Field(
        default_factory=list
    )  # Pinned segment IDs preserved through trimming.

    def append(self, action: TrimAction) -> None:
        self.actions.append(action)
        self.total_tokens_saved += action.tokens_saved


class TokenBudgetReport(BaseModel):
    total_cap_tokens: int = Field(ge=1)
    total_used_tokens: int = Field(ge=0)
    buckets: Dict[str, BucketAllocation] = Field(default_factory=dict)
    total_dropped_segments: int = Field(ge=0, default=0)
    over_budget: bool = False
    degrade_trace: List[str] = Field(default_factory=list)
    decision_log: Optional[PackingDecisionLog] = None


class ContextDecisionRef(BaseModel):
    """Structural decision reference; never carries segment content."""

    segment_id: str
    bucket: str
    action: str
    reason_code: str
    token_estimate: int = Field(ge=0)
    content_digest: str = ""
    refs: List[str] = Field(default_factory=list)
    source: str = "typed_schema"


class MemoryBlockSegmentRef(BaseModel):
    """OpenMinion reference to a Sophiagraph-owned memory block."""

    block_id: str
    class_name: str
    mode: str
    namespace_id: str
    provenance_ref: str = ""
    updated_at: str = ""
    stale: bool = False


class ContextTracePersistenceResult(BaseModel):
    persisted: bool = False
    event_id: Optional[str] = None
    reason_code: ContextTracePersistenceReason = "not_attempted"
    sink: str = ""


class ContextDecisionTraceV1(BaseModel):
    trace_version: str = CONTEXT_DECISION_TRACE_VERSION
    session_id: str
    turn_id: Optional[str] = None
    llm_call_id: Optional[str] = None
    prompt_context_id: Optional[str] = None
    pack_version: str = ""
    decisions: List[ContextDecisionRef] = Field(default_factory=list)
    token_budget_report: Optional[TokenBudgetReport] = None
    memory_provenance_refs: List[str] = Field(default_factory=list)
    retrieval_score_refs: List[str] = Field(default_factory=list)
    summary_checkpoint_refs: List[str] = Field(default_factory=list)
    memory_block_refs: List[str] = Field(default_factory=list)
    missing_sources: List[str] = Field(default_factory=list)
    persistence_status: ContextDecisionTracePersistenceStatus = "pending"
    persistence_result: ContextTracePersistenceResult = Field(
        default_factory=ContextTracePersistenceResult
    )
    truncated: bool = False
    omitted_decision_count: int = 0
    omitted_decision_digest: str = ""

    def bounded(self) -> "ContextDecisionTraceV1":
        """Return a payload bounded to the CDT durable-event contract."""

        trace = self.model_copy(deep=True)
        if len(trace.decisions) > CONTEXT_DECISION_TRACE_MAX_REFERENCES:
            trace._trim_decisions_to(CONTEXT_DECISION_TRACE_MAX_REFERENCES)
        while len(trace._json_bytes()) > CONTEXT_DECISION_TRACE_MAX_BYTES:
            if not trace.decisions:
                break
            keep_count = max(0, len(trace.decisions) // 2)
            trace._trim_decisions_to(keep_count)
        return trace

    def with_persistence_result(
        self, result: ContextTracePersistenceResult
    ) -> "ContextDecisionTraceV1":
        return self.model_copy(
            update={
                "persistence_result": result,
                "persistence_status": "persisted" if result.persisted else "degraded",
            },
            deep=True,
        )

    def _trim_decisions_to(self, keep_count: int) -> None:
        keep_count = max(0, int(keep_count))
        omitted = self.decisions[keep_count:]
        if not omitted:
            return
        omitted_payload = [
            decision.model_dump(mode="json", exclude_none=True) for decision in omitted
        ]
        existing_count = int(self.omitted_decision_count or 0)
        self.decisions = self.decisions[:keep_count]
        self.truncated = True
        self.omitted_decision_count = existing_count + len(omitted)
        self.omitted_decision_digest = _stable_hash(
            {
                "previous_digest": self.omitted_decision_digest,
                "omitted": omitted_payload,
            }
        )

    def _json_bytes(self) -> bytes:
        return json.dumps(
            self.model_dump(mode="json", exclude_none=True),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")


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


def default_budgets_for(purpose: Purpose) -> ContextBudgets:
    presets: dict[str, ContextBudgets] = {
        "decide": ContextBudgets(
            total_max_tokens=2200,
            identity_tokens=160,
            summary_tokens=300,
            conversation_summary_tokens=300,
            active_plan_tokens=400,
            task_digest_tokens=240,
            trailer_feedback_tokens=200,
            recent_turn_tokens=1400,
            facts_tokens=150,
            memory_tokens=150,
            skills_tokens=120,
            artifact_tokens=50,
            instructions_tokens=80,
        ),
        "plan": ContextBudgets(
            total_max_tokens=3500,
            identity_tokens=220,
            summary_tokens=350,
            conversation_summary_tokens=0,
            active_plan_tokens=0,
            task_digest_tokens=0,
            recent_turn_tokens=1000,
            facts_tokens=250,
            memory_tokens=700,
            skills_tokens=250,
            artifact_tokens=600,
            instructions_tokens=130,
        ),
        "act": ContextBudgets(
            total_max_tokens=1800,
            identity_tokens=180,
            summary_tokens=250,
            conversation_summary_tokens=0,
            active_plan_tokens=0,
            task_digest_tokens=0,
            recent_turn_tokens=650,
            facts_tokens=150,
            memory_tokens=250,
            skills_tokens=250,
            artifact_tokens=200,
            instructions_tokens=120,
        ),
        "reflect": ContextBudgets(
            total_max_tokens=2800,
            identity_tokens=220,
            summary_tokens=300,
            conversation_summary_tokens=0,
            active_plan_tokens=0,
            task_digest_tokens=0,
            recent_turn_tokens=700,
            facts_tokens=200,
            memory_tokens=900,
            skills_tokens=80,
            artifact_tokens=400,
            instructions_tokens=120,
        ),
        "judge": ContextBudgets(
            total_max_tokens=3000,
            identity_tokens=200,
            summary_tokens=250,
            conversation_summary_tokens=0,
            active_plan_tokens=0,
            task_digest_tokens=0,
            recent_turn_tokens=600,
            facts_tokens=500,
            memory_tokens=400,
            skills_tokens=80,
            artifact_tokens=700,
            instructions_tokens=120,
        ),
        "validate": ContextBudgets(
            total_max_tokens=3000,
            identity_tokens=200,
            summary_tokens=250,
            conversation_summary_tokens=0,
            active_plan_tokens=0,
            task_digest_tokens=0,
            recent_turn_tokens=600,
            facts_tokens=500,
            memory_tokens=400,
            skills_tokens=80,
            artifact_tokens=700,
            instructions_tokens=120,
        ),
        "summarize": ContextBudgets(
            total_max_tokens=2200,
            identity_tokens=180,
            summary_tokens=300,
            conversation_summary_tokens=0,
            active_plan_tokens=0,
            task_digest_tokens=0,
            recent_turn_tokens=750,
            facts_tokens=220,
            memory_tokens=420,
            skills_tokens=60,
            artifact_tokens=180,
            instructions_tokens=90,
        ),
        "chat": ContextBudgets(
            total_max_tokens=1600,
            identity_tokens=150,
            summary_tokens=220,
            conversation_summary_tokens=0,
            active_plan_tokens=0,
            task_digest_tokens=0,
            recent_turn_tokens=700,
            facts_tokens=120,
            memory_tokens=200,
            skills_tokens=40,
            artifact_tokens=70,
            instructions_tokens=100,
        ),
    }
    return presets[purpose].model_copy(deep=True)


def decide_budget_for_turn_depth(turn_count: int) -> ContextBudgets:
    """Return decide budgets keyed only by canonical session turn depth."""
    safe_count = max(0, int(turn_count))
    if safe_count <= 2:
        return ContextBudgets(
            total_max_tokens=1500,
            identity_tokens=160,
            summary_tokens=300,
            conversation_summary_tokens=0,
            active_plan_tokens=400,
            task_digest_tokens=240,
            recent_turn_tokens=1000,
            facts_tokens=150,
            memory_tokens=150,
            skills_tokens=120,
            artifact_tokens=50,
            instructions_tokens=80,
        )
    if safe_count <= 5:
        return default_budgets_for("decide")
    if safe_count <= 10:
        return ContextBudgets(
            total_max_tokens=2800,
            identity_tokens=160,
            summary_tokens=300,
            conversation_summary_tokens=500,
            active_plan_tokens=400,
            task_digest_tokens=240,
            recent_turn_tokens=1600,
            facts_tokens=150,
            memory_tokens=150,
            skills_tokens=120,
            artifact_tokens=50,
            instructions_tokens=80,
        )
    return ContextBudgets(
        total_max_tokens=3200,
        identity_tokens=160,
        summary_tokens=300,
        conversation_summary_tokens=800,
        active_plan_tokens=400,
        task_digest_tokens=240,
        recent_turn_tokens=1600,
        facts_tokens=150,
        memory_tokens=150,
        skills_tokens=120,
        artifact_tokens=50,
        instructions_tokens=80,
    )


# V1.5 bucket budget allocator helpers
# Bucket token caps as fractions of total_max_tokens
BUCKET_TOKEN_FRACTIONS: dict[str, float] = {
    "static_prefix": 0.15,
    "mission_snapshot": 0.10,
    "summaries": 0.12,
    "conversation_summary": 0.12,
    "active_plan": 0.12,
    "task_digest": 0.08,
    "self_awareness": 0.05,
    "recent_window": 0.30,
    "retrieval": 0.15,
    "evidence_refs": 0.12,
    "turn_input": 0.06,
}


def bucket_caps_for(budgets: ContextBudgets) -> Dict[str, int]:
    total = budgets.total_max_tokens
    return {
        "static_prefix": budgets.identity_tokens + budgets.instructions_tokens,
        "mission_snapshot": max(
            64, int(total * BUCKET_TOKEN_FRACTIONS["mission_snapshot"])
        ),
        "summaries": budgets.summary_tokens,
        "conversation_summary": budgets.conversation_summary_tokens,
        "active_plan": budgets.active_plan_tokens,
        "task_digest": budgets.task_digest_tokens,
        "self_awareness": max(
            64, int(total * BUCKET_TOKEN_FRACTIONS["self_awareness"])
        ),
        "recent_window": budgets.recent_turn_tokens,
        "memory": budgets.memory_tokens,
        "retrieval": budgets.facts_tokens
        + budgets.memory_tokens
        + budgets.skills_tokens,
        "evidence_refs": budgets.artifact_tokens,
        "turn_input": max(64, int(total * BUCKET_TOKEN_FRACTIONS["turn_input"])),
    }


def _stable_hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


# Plugin pipeline types


class EvidenceItem(BaseModel):
    """A candidate evidence item produced by a ContextRetriever."""

    ref: str
    content: str
    score: float = 0.0
    source: str = ""  # retriever name that produced this
    metadata: Dict[str, Any] = Field(default_factory=dict)
