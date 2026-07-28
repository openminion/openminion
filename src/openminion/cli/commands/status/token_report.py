from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from typing import Any

from openminion.modules.telemetry.trace.turn_cost import TurnCostEnvelope
from openminion.modules.telemetry.usage import (
    TokenUsageRecord,
    TokenUsageSummary,
    summary_to_json_payload,
)
from openminion.modules.telemetry.usage.token_usage import (
    SURFACE_CONTEXT_BUCKET,
    SURFACE_CONTEXT_PACK,
    SURFACE_LLM_TOTAL,
)

_ROLLUP_SCHEMA_VERSION = "openminion.token_usage_rollup.v1"


def _record_tokens(record: TokenUsageRecord) -> int:
    return int(
        record.total_tokens
        + record.input_tokens
        + record.output_tokens
        + record.cache_read_tokens
        + record.cache_write_tokens
        + record.estimated_tokens
        + record.saved_tokens
    )


def _format_token_count(value: int) -> str:
    return f"{int(value):,}"


def _top_llm_total(summary: TokenUsageSummary) -> tuple[str, int] | None:
    grouped: dict[tuple[str, str], int] = defaultdict(int)
    for record in summary.records:
        if record.surface != SURFACE_LLM_TOTAL:
            continue
        key = (record.provider or "-", record.model or "-")
        grouped[key] += record.total_tokens
    if not grouped:
        return None
    (provider, model), tokens = max(grouped.items(), key=lambda item: item[1])
    return f"{provider}/{model}", tokens


def _summary_visible_tokens(summary: TokenUsageSummary) -> int:
    return int(
        summary.total_provider_tokens
        + summary.total_derived_tokens
        + summary.totals_by_surface.get(SURFACE_CONTEXT_PACK, 0)
    )


def _yes_no_unknown(value: bool | None) -> str:
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return "unknown"


def _format_ranked_totals(label: str, totals: Mapping[str, int]) -> str:
    if not totals:
        return f"{label}: -"
    parts = [
        f"{name or '-'}={_format_token_count(tokens)}"
        for name, tokens in sorted(
            totals.items(), key=lambda item: item[1], reverse=True
        )
        if tokens
    ]
    return f"{label}: " + (", ".join(parts) if parts else "-")


def _add_unique(lines: list[str], text: str) -> None:
    if text and text not in lines:
        lines.append(text)


def _format_turn_cost(cost: TurnCostEnvelope | None) -> list[str]:
    if cost is None:
        return []
    parts = [
        f"provider_calls={cost.provider_calls_total}",
        f"post_delivery={cost.provider_calls_post_delivery}",
        f"retries={cost.provider_retries}",
        f"tools={cost.invoked_tool_count}",
        f"duplicate_tools={cost.duplicate_tool_calls}",
        f"policy_denials={cost.policy_denials}",
        f"latency_ms={cost.total_wall_ms if cost.total_wall_ms is not None else '-'}",
        f"success={_yes_no_unknown(cost.task_success)}",
        f"truthful={_yes_no_unknown(cost.final_truthful)}",
    ]
    if cost.call_purposes:
        parts.append("purposes=" + ",".join(cost.call_purposes))
    return ["outcome: " + " ".join(parts)]


def _format_recommendations(
    summary: TokenUsageSummary,
    *,
    cost: TurnCostEnvelope | None = None,
) -> list[str]:
    if not summary.records:
        return []
    recommendations: list[str] = []
    coverage = summary.coverage
    llm_calls = coverage.llm_call_events
    if not summary.complete:
        _add_unique(
            recommendations,
            "read a larger event window; this report is limited by --event-limit",
        )
    if llm_calls:
        if coverage.provider_identified_llm_call_events < llm_calls:
            _add_unique(
                recommendations,
                "fill provider identity on llm.call.completed events",
            )
        if coverage.model_identified_llm_call_events < llm_calls:
            _add_unique(
                recommendations,
                "fill model identity on llm.call.completed events",
            )
        if coverage.total_tokens.missing or coverage.total_tokens.invalid:
            _add_unique(
                recommendations,
                "prefer provider total_tokens; derived totals are less authoritative",
            )
        if coverage.input_tokens.missing or coverage.output_tokens.missing:
            _add_unique(
                recommendations,
                "emit both input and output token fields for split analysis",
            )
    if summary.source_event_count:
        if coverage.run_id_present_events < summary.source_event_count:
            _add_unique(recommendations, "attach run_id to usage-producing events")
        if coverage.llm_call_id_present_events < summary.source_event_count:
            _add_unique(
                recommendations,
                "attach llm_call_id so context/cache facts correlate to calls",
            )
    context_tokens = summary.totals_by_surface.get(SURFACE_CONTEXT_PACK, 0)
    llm_tokens = summary.total_provider_tokens + summary.total_derived_tokens
    if context_tokens and context_tokens >= max(1, llm_tokens):
        _add_unique(
            recommendations,
            "context packing is a major token driver; inspect context buckets first",
        )
    if summary.total_cache_write_tokens and not summary.total_cache_read_tokens:
        _add_unique(
            recommendations,
            "cache writes are present without cache reads; check cache reuse",
        )
    if cost is not None:
        if cost.provider_retries:
            _add_unique(
                recommendations,
                "provider retries increased token friction for this run",
            )
        if cost.provider_calls_post_delivery:
            _add_unique(
                recommendations,
                "post-delivery calls exist; verify they are intentional auxiliary work",
            )
        if cost.duplicate_tool_calls:
            _add_unique(
                recommendations,
                "duplicate tool calls detected; inspect tool-loop planning",
            )
        if cost.policy_denials:
            _add_unique(
                recommendations,
                "policy denials consumed turn work; inspect approval/tool policy inputs",
            )
        if cost.task_success is False or cost.final_truthful is False:
            _add_unique(
                recommendations,
                "token spend ended with a negative outcome signal; review this run",
            )
    if not recommendations:
        return ["recommendations: no immediate token telemetry gaps detected"]
    return ["recommendations: " + "; ".join(recommendations)]


def _format_insights(summary: TokenUsageSummary) -> list[str]:
    if not summary.records:
        return []
    lines: list[str] = []
    top = _top_llm_total(summary)
    top_segment = f"top_model={top[0]} {_format_token_count(top[1])}" if top else ""
    context_estimated = summary.totals_by_surface.get(SURFACE_CONTEXT_PACK, 0)
    segments = [
        top_segment,
        f"provider_total={_format_token_count(summary.total_provider_tokens)}",
        f"derived_total={_format_token_count(summary.total_derived_tokens)}",
        f"context_estimated={_format_token_count(context_estimated)}",
        f"cache_read={_format_token_count(summary.total_cache_read_tokens)}",
        f"cache_write={_format_token_count(summary.total_cache_write_tokens)}",
    ]
    lines.append("insights: " + " ".join(segment for segment in segments if segment))
    surface_totals = {
        surface: tokens
        for surface, tokens in summary.totals_by_surface.items()
        if surface != SURFACE_CONTEXT_BUCKET
    }
    lines.append(_format_ranked_totals("by surface", surface_totals))
    context_buckets = summary.totals_by_context_bucket
    if context_buckets:
        lines.append(_format_ranked_totals("context buckets", context_buckets))
    return lines


def format_token_rollup(summaries: tuple[TokenUsageSummary, ...]) -> str:
    if not summaries:
        return "no sessions found"
    token_summaries = [summary for summary in summaries if summary.records]
    total_provider = sum(summary.total_provider_tokens for summary in token_summaries)
    total_derived = sum(summary.total_derived_tokens for summary in token_summaries)
    total_context = sum(
        summary.totals_by_surface.get(SURFACE_CONTEXT_PACK, 0)
        for summary in token_summaries
    )
    total_cache_read = sum(
        summary.total_cache_read_tokens for summary in token_summaries
    )
    total_cache_write = sum(
        summary.total_cache_write_tokens for summary in token_summaries
    )
    lines = [
        "status tokens: "
        f"recent_sessions={len(summaries)} "
        f"with_usage={len(token_summaries)} "
        f"complete={'yes' if all(summary.complete for summary in summaries) else 'no'}",
        "totals: "
        f"provider={_format_token_count(total_provider)} "
        f"derived={_format_token_count(total_derived)} "
        f"context_estimated={_format_token_count(total_context)} "
        f"cache_read={_format_token_count(total_cache_read)} "
        f"cache_write={_format_token_count(total_cache_write)}",
    ]
    surface_totals: dict[str, int] = defaultdict(int)
    context_buckets: dict[str, int] = defaultdict(int)
    model_totals: dict[str, int] = defaultdict(int)
    for summary in token_summaries:
        for surface, tokens in summary.totals_by_surface.items():
            if surface != SURFACE_CONTEXT_BUCKET:
                surface_totals[surface] += tokens
        for bucket, tokens in summary.totals_by_context_bucket.items():
            context_buckets[bucket] += tokens
        for record in summary.records:
            if record.surface == SURFACE_LLM_TOTAL:
                model_totals[f"{record.provider or '-'}/{record.model or '-'}"] += (
                    record.total_tokens
                )
    lines.append(_format_ranked_totals("by model", model_totals))
    lines.append(_format_ranked_totals("by surface", surface_totals))
    if context_buckets:
        lines.append(_format_ranked_totals("context buckets", context_buckets))
    top_sessions = sorted(
        (
            (summary.session_id, _summary_visible_tokens(summary))
            for summary in token_summaries
        ),
        key=lambda item: item[1],
        reverse=True,
    )[:5]
    if top_sessions:
        lines.append(
            "top sessions: "
            + ", ".join(
                f"{session_id}={_format_token_count(tokens)}"
                for session_id, tokens in top_sessions
            )
        )
    lines.extend(_format_rollup_recommendations(summaries))
    lines.append(
        "next: use `--session-id <session-id>` or `--run-id <run-id>` to drill in, "
        "or `--json` for raw session envelopes."
    )
    return "\n".join(lines)


def _format_rollup_recommendations(
    summaries: tuple[TokenUsageSummary, ...],
) -> list[str]:
    recommendations: list[str] = []
    empty = sum(1 for summary in summaries if not summary.records)
    incomplete = sum(1 for summary in summaries if not summary.complete)
    derived = sum(summary.total_derived_tokens for summary in summaries)
    context = sum(
        summary.totals_by_surface.get(SURFACE_CONTEXT_PACK, 0) for summary in summaries
    )
    llm = sum(
        summary.total_provider_tokens + summary.total_derived_tokens
        for summary in summaries
    )
    cache_read = sum(summary.total_cache_read_tokens for summary in summaries)
    cache_write = sum(summary.total_cache_write_tokens for summary in summaries)
    missing_provider = sum(
        summary.coverage.llm_call_events
        - summary.coverage.provider_identified_llm_call_events
        for summary in summaries
    )
    missing_model = sum(
        summary.coverage.llm_call_events
        - summary.coverage.model_identified_llm_call_events
        for summary in summaries
    )
    if empty:
        _add_unique(recommendations, f"{empty} recent session(s) have no usage events")
    if incomplete:
        _add_unique(
            recommendations,
            f"{incomplete} recent session(s) were limited by --event-limit",
        )
    if derived:
        _add_unique(
            recommendations,
            "derived totals exist; improve provider usage emitters",
        )
    if missing_provider or missing_model:
        _add_unique(recommendations, "some llm calls lack provider/model identity")
    if context and context >= max(1, llm):
        _add_unique(
            recommendations,
            "context packing dominates recent usage; inspect bucket totals",
        )
    if cache_write and not cache_read:
        _add_unique(
            recommendations,
            "cache writes appear without reads across recent sessions",
        )
    if not recommendations:
        return ["recommendations: no immediate token telemetry gaps detected"]
    return ["recommendations: " + "; ".join(recommendations)]


def token_rollup_json_payload(
    summaries: tuple[TokenUsageSummary, ...],
) -> dict[str, Any]:
    return {
        "schema_version": _ROLLUP_SCHEMA_VERSION,
        "session_count": len(summaries),
        "complete": all(summary.complete for summary in summaries),
        "summaries": [summary_to_json_payload(summary) for summary in summaries],
    }


def format_token_summary(
    summary: TokenUsageSummary,
    *,
    cost: TurnCostEnvelope | None = None,
) -> str:
    run_label = f" run={summary.run_id}" if summary.run_id else ""
    lines = [
        "status tokens: "
        f"session={summary.session_id}{run_label} "
        f"complete={'yes' if summary.complete else 'no'} "
        f"events={summary.source_event_count} records={summary.records_emitted}",
    ]
    if not summary.records:
        lines.append("no token usage events")
    else:
        lines.append(
            "totals: "
            f"provider={summary.total_provider_tokens} "
            f"derived={summary.total_derived_tokens} "
            f"input={summary.total_input_tokens} "
            f"output={summary.total_output_tokens} "
            f"cache_read={summary.total_cache_read_tokens} "
            f"cache_write={summary.total_cache_write_tokens}"
        )
        lines.extend(_format_insights(summary))
        lines.extend(_format_turn_cost(cost))
        lines.extend(_format_recommendations(summary, cost=cost))
    grouped: dict[tuple[str, str, str, str, str], int] = defaultdict(int)
    for record in summary.records:
        key = (
            record.provider or "-",
            record.model or "-",
            record.surface or "unknown",
            record.bucket,
            record.total_source,
        )
        grouped[key] += _record_tokens(record)
    if grouped:
        lines.append("breakdown:")
    for (
        provider,
        model,
        surface,
        bucket,
        total_source,
    ), tokens in sorted(grouped.items(), key=lambda item: item[1], reverse=True):
        details = f"provider={provider} model={model} surface={surface} tokens={tokens}"
        if bucket:
            details += f" bucket={bucket}"
        if total_source:
            details += f" total_source={total_source}"
        lines.append(f"- {details}")
    coverage = summary.coverage
    if coverage.llm_call_events:
        llm_calls = coverage.llm_call_events
        dimensions = (
            ("input", coverage.input_tokens),
            ("output", coverage.output_tokens),
            ("total", coverage.total_tokens),
            ("cache_read", coverage.cache_read_tokens),
            ("cache_write", coverage.cache_write_tokens),
        )
        lines.append(
            "coverage: "
            f"llm_calls={llm_calls} "
            f"provider={coverage.provider_identified_llm_call_events}/{llm_calls} "
            f"model={coverage.model_identified_llm_call_events}/{llm_calls} "
            + " ".join(
                f"{name}={dimension.reported}/{dimension.total}"
                for name, dimension in dimensions
            )
        )
        invalid = [
            f"{name}={dimension.invalid}"
            for name, dimension in dimensions
            if dimension.invalid
        ]
        if invalid:
            lines.append("invalid usage fields: " + " ".join(invalid))
    if summary.source_event_count:
        lines.append(
            "correlation: "
            f"run_id={coverage.run_id_present_events}/{summary.source_event_count} "
            f"trace_id={coverage.trace_id_present_events}/{summary.source_event_count} "
            "llm_call_id="
            f"{coverage.llm_call_id_present_events}/{summary.source_event_count}"
        )
    if not summary.complete:
        lines.append(
            "incomplete: "
            f"event_limit={summary.event_limit} events_scanned={summary.events_scanned}"
        )
    if not summary.run_id:
        lines.append(
            "next: add `--run-id <run-id>` for one run, or `--json` for the raw "
            "openminion.token_usage.v1 envelope."
        )
    return "\n".join(lines)


__all__ = [
    "format_token_rollup",
    "format_token_summary",
    "token_rollup_json_payload",
]
