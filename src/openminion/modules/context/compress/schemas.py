from dataclasses import asdict, dataclass, field
from typing import Any, Literal


BlockType = Literal["retrieval", "dialogue", "memory", "skill", "wm", "episode"]
CompressedBlockType = Literal[
    "retrieval",
    "dialogue",
    "memory",
    "skill",
    "episode_condensate",
]
QualityTier = Literal["GOOD", "OK", "BAD"]
PositioningMode = Literal["frontload_key_evidence", "balanced"]
PolicyMode = Literal["extractive", "hybrid"]
FaithfulnessLevel = Literal["strict", "normal"]


@dataclass(frozen=True)
class InputBlock:
    block_id: str
    type: BlockType
    text: str
    refs: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CompressionBudgets:
    max_output_tokens_total: int
    max_output_tokens_by_type: dict[str, int] = field(default_factory=dict)
    reserve_tokens_for_headers: int = 0
    hard_cap: bool = True


@dataclass(frozen=True)
class CompressionPolicy:
    mode: PolicyMode = "extractive"
    target_ratio: float = 0.25
    min_evidence_items: int = 2
    max_items_per_source: int = 2
    allow_empty_augmentation: bool = True
    faithfulness_level: FaithfulnessLevel = "strict"
    quote_budget_tokens: int = 128
    preserve_refs: bool = True
    positioning: PositioningMode = "frontload_key_evidence"
    method_prepass: str | None = "selective_context"
    method_main: str = "extractive.v1"
    fallback_method_id: str = "extractive.v1"
    abstractive_enabled: bool = False


@dataclass(frozen=True)
class CompressionRequest:
    request_id: str
    query: str
    blocks: list[InputBlock]
    budgets: CompressionBudgets
    policy: CompressionPolicy
    engine_version: str
    trace_id: str | None = None
    session_id: str | None = None
    mode_name: str | None = None
    retrieval_quality_hint: QualityTier | None = None


@dataclass(frozen=True)
class CompressionReport:
    empty_augmentation: bool
    empty_reason: str | None
    dropped_reason_stats: dict[str, int]
    count_by_type: dict[str, int]
    fallback_used: bool
    policy_hash: str
    input_hash: str
    output_hash: str
    engine_version: str
    tokenizer_id: str
    scorer_version: str


@dataclass(frozen=True)
class CompressedBlock:
    block_id: str
    type: CompressedBlockType
    text: str
    refs: list[str]
    unit_refs: list[str]
    compression_meta: dict[str, Any]


@dataclass(frozen=True)
class CompressionResult:
    blocks: list[CompressedBlock]
    report: CompressionReport
    method_id: str
    input_tokens: int
    output_tokens: int
    ratio: float
    compression_hash: str
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-ready representation."""

        return asdict(self)


# V1.5 schemas — CompressionBundle and SeedBundle (C15-001)

TierType = Literal[
    "summary",
    "decisions",
    "constraints",
    "open_loops",
    "entities",
    "tool_digests",
    "failures",
]


@dataclass(frozen=True)
class TierEntry:
    """One tier of the compression bundle (e.g. decisions, entities)."""

    tier_type: TierType
    text: str
    refs: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
    token_count: int = 0


@dataclass(frozen=True)
class CompressionBundle:
    """Persistent compression state for a session."""

    bundle_id: str
    session_id: str
    summary_text: str
    tiers: list[TierEntry] = field(default_factory=list)
    up_to_event_id: str | None = None
    checkpoint_id: str | None = None
    total_tokens: int = 0
    version: int = 1
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SeedBundleBudgets:
    """Hard caps for the prompt-injectable seed."""

    total_max_tokens: int = 1200
    summary_max_tokens: int = 400
    decisions_max_tokens: int = 200
    constraints_max_tokens: int = 150
    entities_max_tokens: int = 200
    open_loops_max_tokens: int = 100
    tool_digests_max_tokens: int = 150


@dataclass(frozen=True)
class SeedSection:
    """One section of the rendered SeedBundle."""

    section_type: TierType
    text: str
    refs: list[str] = field(default_factory=list)
    token_count: int = 0


@dataclass(frozen=True)
class SeedBundle:
    """Prompt-injectable compact context derived from a CompressionBundle.

    Built by `build_rollover_seed()` and consumed by `openminion-context`
    when constructing the `seed_block` of a fresh prompt context.
    """

    seed_id: str
    session_id: str
    source_bundle_id: str
    source_checkpoint_id: str | None = None
    sections: list[SeedSection] = field(default_factory=list)
    total_tokens: int = 0
    budgets: SeedBundleBudgets = field(default_factory=SeedBundleBudgets)
    up_to_event_id: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def render_text(self) -> str:
        """Return a single text blob for prompt injection."""
        parts: list[str] = []
        for sec in self.sections:
            if sec.text.strip():
                parts.append(f"[{sec.section_type.upper()}]\n{sec.text}")
        return "\n\n".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# Spec #3 schemas — CompressionCheckpoint and structured state (C15-001/002)


@dataclass(frozen=True)
class StructuredDecision:
    """A decision record with stable ID for anti-drift tracking."""

    id: str
    statement: str
    evidence_refs: list[str] = field(default_factory=list)
    last_updated_event_id: str | None = None


@dataclass(frozen=True)
class StructuredConstraint:
    """A constraint record with stable ID."""

    id: str
    statement: str
    scope: str = "global"
    priority: str = "normal"


@dataclass(frozen=True)
class StructuredOpenLoop:
    """Structuredopenloop contract."""

    id: str
    question_or_todo: str
    owner: str | None = None
    status: str = "open"


@dataclass(frozen=True)
class StructuredToolDigest:
    """A distilled tool outcome (no raw payload)."""

    tool_name: str
    outcome: str
    artifact_refs: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CheckpointStructuredState:
    """All structured anti-drift state in one container."""

    decisions: list[StructuredDecision] = field(default_factory=list)
    constraints: list[StructuredConstraint] = field(default_factory=list)
    open_loops: list[StructuredOpenLoop] = field(default_factory=list)
    entities: dict[str, Any] = field(default_factory=dict)
    tool_digests: list[StructuredToolDigest] = field(default_factory=list)


@dataclass(frozen=True)
class CheckpointStats:
    """Token estimates and compression ratio for a checkpoint."""

    summary_tokens: int = 0
    structured_tokens: int = 0
    total_tokens: int = 0
    compression_ratio: float = 0.0


@dataclass(frozen=True)
class CompressionCheckpoint:
    """Canonical checkpoint artifact (Spec #3, C15-001)."""

    checkpoint_id: str
    session_id: str
    created_at: str
    from_event_id: str | None
    to_event_id: str
    summary_text: str
    recent_window_event_ids: list[str] = field(default_factory=list)
    structured: CheckpointStructuredState = field(
        default_factory=CheckpointStructuredState
    )
    stats: CheckpointStats = field(default_factory=CheckpointStats)
    version: str = "1.6"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CheckpointFailedPayload:
    """Payload for a ``compression.checkpoint.failed`` event (C15-002)."""

    failure_id: str
    session_id: str
    reason: str
    error_code: str
    created_at: str
    from_event_id: str | None = None
    until_event_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
