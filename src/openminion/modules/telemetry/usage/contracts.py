"""Versioned token usage export contract."""

from typing import Literal, TypedDict

TokenUsageSchemaVersion = Literal["openminion.token_usage.v1"]
TOKEN_USAGE_SCHEMA_VERSION: TokenUsageSchemaVersion = "openminion.token_usage.v1"

TOTAL_SOURCE_PROVIDER: Literal["provider"] = "provider"
TOTAL_SOURCE_DERIVED: Literal["derived"] = "derived"
TOKEN_TOTAL_SOURCES = frozenset({TOTAL_SOURCE_PROVIDER, TOTAL_SOURCE_DERIVED})

TokenTotalSource = Literal["", "provider", "derived"]


class TokenUsageEventRefPayload(TypedDict, total=False):
    sequence: int
    observed_at: str
    event_type: str
    event_id: str


class TokenUsageSourceRangePayload(TypedDict):
    first: TokenUsageEventRefPayload | None
    last: TokenUsageEventRefPayload | None


class TokenUsageRecordPayload(TypedDict):
    session_id: str
    run_id: str
    turn_id: str
    llm_call_id: str
    prompt_context_id: str
    provider: str
    model: str
    surface: str
    bucket: str
    source_event_type: str
    source_event_id: str
    source_event_sequence: int | None
    observed_at: str
    total_tokens: int
    total_source: TokenTotalSource
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    estimated_tokens: int
    cap_tokens: int
    saved_tokens: int
    original_ref: str
    policy: str
    estimated: bool
    prompt_cache_key: str
    static_prefix_hash: str
    cache_hit: bool | None


class TokenUsageDimensionCoveragePayload(TypedDict):
    reported: int
    missing: int
    invalid: int


class TokenUsageCoveragePayload(TypedDict):
    llm_call_events: int
    context_manifest_events: int
    cache_metric_events: int
    provider_identified_llm_call_events: int
    model_identified_llm_call_events: int
    run_id_present_events: int
    trace_id_present_events: int
    llm_call_id_present_events: int
    input_tokens: TokenUsageDimensionCoveragePayload
    output_tokens: TokenUsageDimensionCoveragePayload
    total_tokens: TokenUsageDimensionCoveragePayload
    cache_read_tokens: TokenUsageDimensionCoveragePayload
    cache_write_tokens: TokenUsageDimensionCoveragePayload


class TokenUsageTotalsPayload(TypedDict):
    provider_tokens: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    estimated_tokens: int
    saved_tokens: int


class TokenUsageExportPayload(TypedDict):
    schema_version: TokenUsageSchemaVersion
    session_id: str
    run_id: str
    complete: bool
    source_event_count: int
    records_emitted: int
    events_scanned: int
    event_limit: int | None
    source_event_range: TokenUsageSourceRangePayload
    coverage: TokenUsageCoveragePayload
    records: list[TokenUsageRecordPayload]
    totals: TokenUsageTotalsPayload
    totals_by_surface: dict[str, int]
    totals_by_context_bucket: dict[str, int]


__all__ = [
    "TOKEN_TOTAL_SOURCES",
    "TOKEN_USAGE_SCHEMA_VERSION",
    "TokenTotalSource",
    "TokenUsageCoveragePayload",
    "TokenUsageDimensionCoveragePayload",
    "TokenUsageEventRefPayload",
    "TokenUsageExportPayload",
    "TokenUsageRecordPayload",
    "TokenUsageSchemaVersion",
    "TokenUsageSourceRangePayload",
    "TokenUsageTotalsPayload",
    "TOTAL_SOURCE_DERIVED",
    "TOTAL_SOURCE_PROVIDER",
]
