from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from typing import Any

from openminion.base.config import OpenMinionConfig
from openminion.cli.commands.status.session_store import build_status_session_store
from openminion.cli.presentation.json_output import print_json_payload
from openminion.modules.telemetry.usage import (
    StatsService,
    TokenUsageRecord,
    TokenUsageSummary,
    summary_to_json_payload,
)
from openminion.modules.telemetry.usage.token_usage import (
    SURFACE_CONTEXT_BUCKET,
    SURFACE_CONTEXT_PACK,
    SURFACE_LLM_TOTAL,
)


def _record_tokens(record: TokenUsageRecord) -> int:
    return (
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


def _session_id_from_row(row: Any) -> str:
    if isinstance(row, Mapping):
        return str(row.get("session_id") or row.get("id") or "").strip()
    return str(getattr(row, "session_id", None) or getattr(row, "id", "") or "").strip()


def _resolve_session_id(args: Any, store: Any) -> str:
    session_id = str(getattr(args, "session_id", "") or "").strip()
    if session_id:
        return session_id
    list_sessions = getattr(store, "list_sessions", None)
    if not callable(list_sessions):
        raise RuntimeError("--session-id is required for this session store.")
    sessions = list_sessions(limit=1)
    if not sessions:
        raise RuntimeError(
            "No sessions found. Start a session or run `openminion sessions list` "
            "to confirm the active data root."
        )
    latest_session_id = _session_id_from_row(sessions[0])
    if not latest_session_id:
        raise RuntimeError("--session-id is required; latest session has no id.")
    return latest_session_id


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


def _format_summary(summary: TokenUsageSummary) -> str:
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


def run_tokens_status(args: Any, *, config: OpenMinionConfig) -> int:
    run_id = str(args.run_id or "").strip()
    requested_session_id = str(getattr(args, "session_id", "") or "").strip()
    event_limit = args.event_limit
    if event_limit is not None and int(event_limit) <= 0:
        raise RuntimeError("--event-limit must be greater than zero")

    store = build_status_session_store(args, config)
    try:
        service = StatsService(store)
        if run_id and not requested_session_id:
            summary = service.get_run_token_usage(run_id, event_limit=event_limit)
            if summary is None:
                raise RuntimeError(f"Run '{run_id}' was not found.")
            if store.get_session(summary.session_id) is None:
                raise RuntimeError(f"Session '{summary.session_id}' was not found.")
        else:
            session_id = _resolve_session_id(args, store)
            if store.get_session(session_id) is None:
                raise RuntimeError(f"Session '{session_id}' was not found.")
            if not run_id:
                summary = service.get_session_token_usage(
                    session_id,
                    event_limit=event_limit,
                )
            else:
                summary = service.get_run_token_usage(run_id, event_limit=event_limit)
                if summary is None:
                    raise RuntimeError(f"Run '{run_id}' was not found.")
                if summary.session_id != session_id:
                    raise RuntimeError(
                        f"Run '{run_id}' does not belong to session '{session_id}'."
                    )
        if bool(args.json):
            print_json_payload(summary_to_json_payload(summary))
        else:
            print(_format_summary(summary))
        return 0
    finally:
        store.close()


__all__ = ["run_tokens_status"]
