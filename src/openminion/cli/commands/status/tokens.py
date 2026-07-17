from __future__ import annotations

from collections import defaultdict
from typing import Any

from openminion.base.config import OpenMinionConfig
from openminion.cli.config import load_cli_manager
from openminion.cli.presentation.json_output import print_json_payload
from openminion.modules.brain.paths import resolve_brain_sessions_db_path
from openminion.modules.session.runtime.factory import build_module_session_store
from openminion.modules.storage.engine import StorageEngineConfig
from openminion.modules.storage.runtime.sqlite import resolve_database_path
from openminion.modules.telemetry.usage import (
    StatsService,
    TokenUsageSummary,
    summary_to_json_payload,
)
from openminion.modules.telemetry.usage.token_usage import TokenUsageRecord


def _build_session_store(args: Any, config: OpenMinionConfig) -> Any:
    manager = load_cli_manager(args.config)
    storage_path = resolve_database_path(config.storage.path, env=manager.env)
    session_path = resolve_brain_sessions_db_path(storage_path=storage_path)
    return build_module_session_store(
        config=StorageEngineConfig(
            root_dir=session_path.parent,
            sqlite_path=session_path,
            fallback_root=session_path.parent,
            record_backend=config.storage.record_backend(),
            record_backend_options=config.storage.record_backend_options(),
        ),
        database_path=session_path,
        env=manager.env,
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

    store = _build_session_store(args, config)
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
