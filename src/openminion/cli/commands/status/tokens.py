from __future__ import annotations

from collections import defaultdict
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
    for (provider, model, surface, bucket, total_source), tokens in grouped.items():
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
    return "\n".join(lines)


def run_tokens_status(args: Any, *, config: OpenMinionConfig) -> int:
    session_id = str(args.session_id or "").strip()
    run_id = str(args.run_id or "").strip()
    event_limit = args.event_limit
    if event_limit is not None and int(event_limit) <= 0:
        raise RuntimeError("--event-limit must be greater than zero")

    store = build_status_session_store(args, config)
    try:
        if store.get_session(session_id) is None:
            raise RuntimeError(f"Session '{session_id}' was not found.")
        service = StatsService(store)
        if run_id:
            summary = service.get_run_token_usage(run_id, event_limit=event_limit)
            if summary is None:
                raise RuntimeError(f"Run '{run_id}' was not found.")
            if summary.session_id != session_id:
                raise RuntimeError(
                    f"Run '{run_id}' does not belong to session '{session_id}'."
                )
        else:
            summary = service.get_session_token_usage(
                session_id,
                event_limit=event_limit,
            )
        if bool(args.json):
            print_json_payload(summary_to_json_payload(summary))
        else:
            print(_format_summary(summary))
        return 0
    finally:
        store.close()


__all__ = ["run_tokens_status"]
