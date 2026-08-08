"""Versioned token usage export contract."""

from typing import Literal, TypedDict

TokenUsageSchemaVersion = Literal["openminion.token_usage.v1"]
TOKEN_USAGE_SCHEMA_VERSION: TokenUsageSchemaVersion = "openminion.token_usage.v1"
TokenUsageRollupSchemaVersion = Literal["openminion.token_usage_rollup.v1"]
TOKEN_USAGE_ROLLUP_SCHEMA_VERSION: TokenUsageRollupSchemaVersion = (
    "openminion.token_usage_rollup.v1"
)

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
    derived_tokens: int
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


class TokenUsageAdvisoryPayload(TypedDict):
    code: str
    message: str


class TokenUsageRollupTotalsPayload(TypedDict):
    provider_tokens: int
    derived_tokens: int
    context_estimated_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int


class TokenUsageRollupCoveragePayload(TypedDict):
    source_event_count: int
    llm_call_events: int
    provider_identified_llm_call_events: int
    model_identified_llm_call_events: int
    run_id_present_events: int
    trace_id_present_events: int
    llm_call_id_present_events: int


class TokenUsageProviderCoveragePayload(TypedDict):
    provider: str
    model: str
    llm_total_records: int
    provider_total_records: int
    derived_total_records: int
    provider_tokens: int
    derived_tokens: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int


class TokenUsageRollupEfficiencyPayload(TypedDict):
    total_visible_tokens: int
    provider_total_ratio_bps: int
    derived_total_ratio_bps: int
    context_share_bps: int
    cache_read_to_write_bps: int


class TokenUsageSessionTrendPayload(TypedDict):
    session_id: str
    complete: bool
    first_observed_at: str
    last_observed_at: str
    provider_tokens: int
    derived_tokens: int
    context_estimated_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    total_visible_tokens: int
    advisory_codes: list[str]


class TokenUsageRollupPayload(TypedDict):
    schema_version: TokenUsageRollupSchemaVersion
    session_count: int
    input_session_count: int
    only_warnings: bool
    complete: bool
    totals: TokenUsageRollupTotalsPayload
    coverage: TokenUsageRollupCoveragePayload
    provider_coverage: list[TokenUsageProviderCoveragePayload]
    efficiency: TokenUsageRollupEfficiencyPayload
    session_trends: list[TokenUsageSessionTrendPayload]
    advisories: list[TokenUsageAdvisoryPayload]
    summaries: list[TokenUsageExportPayload]


__all__ = [
    "TOKEN_TOTAL_SOURCES",
    "TOKEN_USAGE_ROLLUP_SCHEMA_VERSION",
    "TOKEN_USAGE_SCHEMA_VERSION",
    "TokenTotalSource",
    "TokenUsageAdvisoryPayload",
    "TokenUsageCoveragePayload",
    "TokenUsageDimensionCoveragePayload",
    "TokenUsageEventRefPayload",
    "TokenUsageExportPayload",
    "TokenUsageProviderCoveragePayload",
    "TokenUsageRollupEfficiencyPayload",
    "TokenUsageRollupCoveragePayload",
    "TokenUsageRollupPayload",
    "TokenUsageRollupSchemaVersion",
    "TokenUsageSessionTrendPayload",
    "TokenUsageRollupTotalsPayload",
    "TokenUsageRecordPayload",
    "TokenUsageSchemaVersion",
    "TokenUsageSourceRangePayload",
    "TokenUsageTotalsPayload",
    "TOTAL_SOURCE_DERIVED",
    "TOTAL_SOURCE_PROVIDER",
]
