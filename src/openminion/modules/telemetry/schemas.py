from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional
import time


class DropReason(str, Enum):
    """Canonical drop reasons for context sections."""

    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    LOW_RELEVANCE = "LOW_RELEVANCE"
    STALE_CONTEXT = "STALE_CONTEXT"
    DUPLICATE_CONTENT = "DUPLICATE_CONTENT"
    POLICY_REDACTION = "POLICY_REDACTION"
    MISSING_DEPENDENCY = "MISSING_DEPENDENCY"
    NOT_SELECTED = "NOT_SELECTED"
    FORMAT_CONSTRAINT = "FORMAT_CONSTRAINT"
    ERROR_FALLBACK = "ERROR_FALLBACK"
    USER_PRIVACY_MODE = "USER_PRIVACY_MODE"


class TruncationReason(str, Enum):
    """Canonical truncation reasons for context sections."""

    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    SECTION_MAX_TOKENS = "SECTION_MAX_TOKENS"
    SECTION_MAX_ITEMS = "SECTION_MAX_ITEMS"
    DEDUPE_TRIM = "DEDUPE_TRIM"
    EXTRACTIVE_SUMMARY = "EXTRACTIVE_SUMMARY"
    FORMAT_CONSTRAINT = "FORMAT_CONSTRAINT"


@dataclass
class TelemetryRecordBase:
    """Base fields for all telemetry records (TEL-001)."""

    record_type: str = ""
    schema_version: int = 1
    created_at: datetime = field(default_factory=datetime.utcnow)
    trace_id: str = ""
    session_id: str = ""
    agent_id: str = ""
    turn_id: int = 0
    pack_id: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_type": self.record_type,
            "schema_version": self.schema_version,
            "created_at": self.created_at.isoformat() + "Z",
            "trace_id": self.trace_id,
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "turn_id": self.turn_id,
            "pack_id": self.pack_id,
        }


@dataclass
class ContextSectionStat:
    """Statistics for a context section in a pack."""

    name: str
    included: bool = False
    items_count: int = 0
    tokens_est: int = 0
    chars: int = 0
    priority: int = 0
    source: str = "session"  # session | memory | skill | tool | static
    source_ids: list[str] = field(default_factory=list)
    max_tokens: Optional[int] = None
    max_items: Optional[int] = None
    trimmed_items: int = 0
    trimmed_tokens_est: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "included": self.included,
            "items_count": self.items_count,
            "tokens_est": self.tokens_est,
            "chars": self.chars,
            "priority": self.priority,
            "source": self.source,
            "source_ids": self.source_ids,
            "limits": {"max_tokens": self.max_tokens, "max_items": self.max_items},
            "trimmed_items": self.trimmed_items,
            "trimmed_tokens_est": self.trimmed_tokens_est,
        }


@dataclass
class DropEvent:
    """Record of items dropped from a context section."""

    section: str
    reason: DropReason
    dropped_items: int = 0
    dropped_tokens_est: int = 0
    note: Optional[str] = None
    ref_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "section": self.section,
            "reason": self.reason.value,
            "dropped_items": self.dropped_items,
            "dropped_tokens_est": self.dropped_tokens_est,
            "note": self.note,
            "ref_ids": self.ref_ids,
        }


@dataclass
class TruncationEvent:
    """Record of truncation applied to a context section."""

    section: str
    reason: TruncationReason
    original_tokens_est: int = 0
    final_tokens_est: int = 0
    strategy: str = (
        "head"  # head | tail | middle | summary_replace | extractive | section_priority
    )
    note: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "section": self.section,
            "reason": self.reason.value,
            "original_tokens_est": self.original_tokens_est,
            "final_tokens_est": self.final_tokens_est,
            "strategy": self.strategy,
            "note": self.note,
        }


@dataclass
class ContextPackReport(TelemetryRecordBase):
    """Required record: context.pack (TEL-002)."""

    model_id: Optional[str] = None
    budget_tokens_total: int = 0
    budget_tokens_target: int = 0
    budget_tokens_reserved: int = 0
    budget_tokens_available: int = 0
    prompt_tokens_est: int = 0
    prompt_chars: int = 0
    prompt_messages_count: int = 0
    format: str = "chat_messages"  # chat_messages | single_prompt
    token_estimator: str = "heuristic"  # tiktoken | heuristic | provider_estimator
    token_estimator_version: Optional[str] = None
    sections: list[ContextSectionStat] = field(default_factory=list)
    drops: list[DropEvent] = field(default_factory=list)
    truncations: list[TruncationEvent] = field(default_factory=list)
    summary_present: bool = False
    summary_age_turns: Optional[int] = None
    recent_window_messages: int = 0
    recent_window_turns: int = 0
    memory_snippets_n: int = 0
    memory_tokens_est: int = 0
    skill_cards_n: int = 0
    selected_skill_id: Optional[str] = None
    selected_skill_tokens_est: Optional[int] = None
    tool_digest_included: bool = False
    tool_digest_tokens_est: Optional[int] = None
    redundancy_ratio_est: float = 0.0
    staleness_score_est: float = 0.0
    compression_pressure: float = 0.0
    drop_pressure: float = 0.0
    session_event_range_min: Optional[str] = None
    session_event_range_max: Optional[str] = None
    summary_event_id: Optional[str] = None
    memory_query_hash: Optional[str] = None
    skill_shortlist_hash: Optional[str] = None
    selected_skill_hash: Optional[str] = None

    def __post_init__(self):
        self.record_type = "context.pack"

    def to_dict(self) -> dict[str, Any]:
        base = super().to_dict()
        base.update(
            {
                "model_id": self.model_id,
                "budget_tokens_total": self.budget_tokens_total,
                "budget_tokens_target": self.budget_tokens_target,
                "budget_tokens_reserved": self.budget_tokens_reserved,
                "budget_tokens_available": self.budget_tokens_available,
                "prompt_tokens_est": self.prompt_tokens_est,
                "prompt_chars": self.prompt_chars,
                "prompt_messages_count": self.prompt_messages_count,
                "format": self.format,
                "token_estimator": self.token_estimator,
                "token_estimator_version": self.token_estimator_version,
                "sections": [s.to_dict() for s in self.sections],
                "drops": [d.to_dict() for d in self.drops],
                "truncations": [t.to_dict() for t in self.truncations],
                "summary_present": self.summary_present,
                "summary_age_turns": self.summary_age_turns,
                "recent_window_messages": self.recent_window_messages,
                "recent_window_turns": self.recent_window_turns,
                "memory_snippets_n": self.memory_snippets_n,
                "memory_tokens_est": self.memory_tokens_est,
                "skill_cards_n": self.skill_cards_n,
                "selected_skill_id": self.selected_skill_id,
                "selected_skill_tokens_est": self.selected_skill_tokens_est,
                "tool_digest_included": self.tool_digest_included,
                "tool_digest_tokens_est": self.tool_digest_tokens_est,
                "redundancy_ratio_est": self.redundancy_ratio_est,
                "staleness_score_est": self.staleness_score_est,
                "compression_pressure": self.compression_pressure,
                "drop_pressure": self.drop_pressure,
                "session_event_range": {
                    "min_event_id": self.session_event_range_min,
                    "max_event_id": self.session_event_range_max,
                }
                if self.session_event_range_min
                else None,
                "summary_event_id": self.summary_event_id,
                "memory_query_hash": self.memory_query_hash,
                "skill_shortlist_hash": self.skill_shortlist_hash,
                "selected_skill_hash": self.selected_skill_hash,
            }
        )
        return base


@dataclass
class CompressSummaryReport(TelemetryRecordBase):
    """Recommended record: compress.summary (TEL-003)."""

    summary_event_id: str = ""
    covered_event_range_min: Optional[str] = None
    covered_event_range_max: Optional[str] = None
    summary_tokens_est: int = 0
    summary_chars: int = 0
    compression_ratio_est: float = 0.0
    reason: str = (
        "turn_interval"  # turn_interval | token_pressure | manual | error_recovery
    )

    def __post_init__(self):
        self.record_type = "compress.summary"

    def to_dict(self) -> dict[str, Any]:
        base = super().to_dict()
        base.update(
            {
                "summary_event_id": self.summary_event_id,
                "covered_event_range": {
                    "min_event_id": self.covered_event_range_min,
                    "max_event_id": self.covered_event_range_max,
                }
                if self.covered_event_range_min
                else None,
                "summary_tokens_est": self.summary_tokens_est,
                "summary_chars": self.summary_chars,
                "compression_ratio_est": self.compression_ratio_est,
                "reason": self.reason,
            }
        )
        return base


@dataclass
class SkillShortlistReport(TelemetryRecordBase):
    """Recommended record: skill.shortlist (TEL-003)."""

    query_hash: str = ""
    strategy: str = ""
    candidates_total: int = 0
    returned_cards_n: int = 0
    max_cards: int = 0

    def __post_init__(self):
        self.record_type = "skill.shortlist"

    def to_dict(self) -> dict[str, Any]:
        base = super().to_dict()
        base.update(
            {
                "query_hash": self.query_hash,
                "strategy": self.strategy,
                "candidates_total": self.candidates_total,
                "returned_cards_n": self.returned_cards_n,
                "max_cards": self.max_cards,
            }
        )
        return base


@dataclass
class SkillExpandReport(TelemetryRecordBase):
    """Recommended record: skill.expand (TEL-003)."""

    skill_id: str = ""
    version: str = ""
    excerpt_hash: str = ""
    excerpt_tokens_est: int = 0
    max_tokens: int = 0
    sections_included: list[str] = field(default_factory=list)

    def __post_init__(self):
        self.record_type = "skill.expand"

    def to_dict(self) -> dict[str, Any]:
        base = super().to_dict()
        base.update(
            {
                "skill_id": self.skill_id,
                "version": self.version,
                "excerpt_hash": self.excerpt_hash,
                "excerpt_tokens_est": self.excerpt_tokens_est,
                "max_tokens": self.max_tokens,
                "sections_included": self.sections_included,
            }
        )
        return base


@dataclass
class RetrieveQueryReport(TelemetryRecordBase):
    """Recommended record: retrieve.query (TEL-003)."""

    query_hash: str = ""
    top_k: int = 0
    returned_n: int = 0
    tokens_est: int = 0
    strategy: str = "bm25"  # bm25 | embed | hybrid
    latency_ms: float = 0.0

    def __post_init__(self):
        self.record_type = "retrieve.query"

    def to_dict(self) -> dict[str, Any]:
        base = super().to_dict()
        base.update(
            {
                "query_hash": self.query_hash,
                "top_k": self.top_k,
                "returned_n": self.returned_n,
                "tokens_est": self.tokens_est,
                "strategy": self.strategy,
                "latency_ms": self.latency_ms,
            }
        )
        return base


@dataclass
class LLMUsageReport(TelemetryRecordBase):
    """Recommended record: llm.usage (TEL-003)."""

    provider: str = ""
    model_id: str = ""
    prompt_tokens_actual: int = 0
    completion_tokens_actual: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    cache_hit: bool = False

    def __post_init__(self):
        self.record_type = "llm.usage"

    def to_dict(self) -> dict[str, Any]:
        base = super().to_dict()
        base.update(
            {
                "provider": self.provider,
                "model_id": self.model_id,
                "prompt_tokens_actual": self.prompt_tokens_actual,
                "completion_tokens_actual": self.completion_tokens_actual,
                "cost_usd": self.cost_usd,
                "latency_ms": self.latency_ms,
                "cache_hit": self.cache_hit,
            }
        )
        return base


@dataclass
class TurnOutcomeReport(TelemetryRecordBase):
    """Recommended record: turn.outcome (TEL-003)."""

    pack_count: int = 0
    tool_calls_count: int = 0
    status: str = "ok"  # ok | retry | error | blocked_by_policy
    error_class: Optional[str] = None
    total_latency_ms: float = 0.0

    def __post_init__(self):
        self.record_type = "turn.outcome"

    def to_dict(self) -> dict[str, Any]:
        base = super().to_dict()
        base.update(
            {
                "pack_count": self.pack_count,
                "tool_calls_count": self.tool_calls_count,
                "status": self.status,
                "error_class": self.error_class,
                "total_latency_ms": self.total_latency_ms,
            }
        )
        return base


@dataclass
class TelemetryEvent:
    """A telemetry event emitted during agent runtime."""

    session_id: str
    turn_id: str
    event_type: str  # "tick", "tool_call", "llm_call", "context_pack", "module.stats"
    timestamp: float = field(default_factory=time.time)
    data: dict[str, Any] = field(default_factory=dict)
    mode: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "event_type": self.event_type,
            "timestamp": self.timestamp,
            "data": self.data,
            "mode": self.mode,
        }


@dataclass
class ModuleTelemetryStats:
    """Aggregated telemetry stats for one module within a session."""

    module_id: str
    event_count: int = 0
    success_count: int = 0
    error_count: int = 0
    total_latency_ms: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cached_tokens: int = 0
    total_dropped_items: int = 0
    total_truncated_items: int = 0
    operation_counts: dict[str, int] = field(default_factory=dict)
    custom_counter_sums: dict[str, float] = field(default_factory=dict)
    last_operation: Optional[str] = None
    last_turn_id: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "module_id": self.module_id,
            "event_count": self.event_count,
            "success_count": self.success_count,
            "error_count": self.error_count,
            "total_latency_ms": self.total_latency_ms,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_cached_tokens": self.total_cached_tokens,
            "total_dropped_items": self.total_dropped_items,
            "total_truncated_items": self.total_truncated_items,
            "operation_counts": self.operation_counts,
            "custom_counter_sums": self.custom_counter_sums,
            "last_operation": self.last_operation,
            "last_turn_id": self.last_turn_id,
        }


@dataclass
class SessionTelemetry:
    """Aggregated telemetry for a session."""

    session_id: str
    event_count: int
    tick_count: int
    tool_call_count: int
    llm_call_count: int
    total_input_tokens: int
    total_output_tokens: int
    total_cached_tokens: int
    elapsed_ms: float
    module_stats: dict[str, ModuleTelemetryStats] = field(default_factory=dict)
    events: list[TelemetryEvent] = field(default_factory=list)


@dataclass
class CostSummary:
    """Cost summary for a session based on token usage."""

    session_id: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    estimated_cost_usd: float
    provider: str = "unknown"
    model: str = "unknown"


# Default cost per 1M tokens (USD)
DEFAULT_COST_TABLE = {
    "openai": {
        "gpt-4": {"input": 30.0, "output": 60.0},
        "gpt-4-turbo": {"input": 10.0, "output": 30.0},
        "gpt-3.5-turbo": {"input": 0.5, "output": 1.5},
    },
    "anthropic": {
        "claude-3-opus": {"input": 15.0, "output": 75.0},
        "claude-3-sonnet": {"input": 3.0, "output": 15.0},
        "claude-3-haiku": {"input": 0.25, "output": 1.25},
    },
    "default": {"input": 1.0, "output": 2.0},
}


def calculate_cost(
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int,
    provider: str = "default",
    model: str = "default",
    cost_table: dict = DEFAULT_COST_TABLE,
) -> float:
    """Calculate estimated cost in USD based on token usage."""
    provider_rates = cost_table.get(provider, cost_table["default"])
    model_rates = provider_rates.get(model, provider_rates)

    input_rate = model_rates.get("input", 1.0)
    output_rate = model_rates.get("output", 2.0)

    effective_input = max(0, input_tokens - cached_tokens)
    cost = (effective_input * input_rate + output_tokens * output_rate) / 1_000_000
    return round(cost, 6)
