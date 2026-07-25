# ruff: noqa: F403,F405
from .common import *
from .common import _stable_hash

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
