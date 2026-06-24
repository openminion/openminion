from dataclasses import asdict, is_dataclass
import json
import os
import time
from pathlib import Path
from typing import Any, Iterable

import typer

from openminion.base.config import resolve_data_root, resolve_home_root
from openminion.base.config.env import resolve_environment_config
from openminion.base.constants import OPENMINION_DATA_ROOT_ENV
from openminion.modules.memory.constants import DEFAULT_INTEGRATED_SQLITE_SUBPATH
from openminion.modules.memory.diagnostics.introspection import build_memory_snapshot
from openminion.modules.memory.diagnostics.operability import (
    compute_stats,
    last_trace_timestamp,
    read_trace_events,
    serialize_for_json,
    summarize_history,
    summarize_trace_event,
)
from openminion.modules.memory.models import MemoryScope
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore


def serialize_record(record: Any) -> dict[str, Any]:
    if is_dataclass(record):
        return asdict(record)
    if isinstance(record, dict):
        return {str(key): serialize_for_json(value) for key, value in record.items()}
    return serialize_for_json(record)


def emit_export(
    records: Iterable[Any],
    *,
    export_format: str,
    out: Path | None,
) -> None:
    rows = [serialize_record(record) for record in records]
    if export_format == "json":
        payload = json.dumps(rows, default=str, indent=2)
    else:
        payload = "\n".join(json.dumps(row, default=str) for row in rows)
        if payload:
            payload += "\n"
    if out is None:
        typer.echo(payload.rstrip("\n") if export_format == "jsonl" else payload)
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(payload, encoding="utf-8")
    typer.echo(str(out))


def render_trace_rows(events: list[dict[str, Any]]) -> str:
    if not events:
        return "No trace events found."
    lines = []
    for event in events:
        lines.append(
            f"{event.get('ts', '')}  {event.get('event', '')}  {event.get('agent_id', '')}  "
            f"{summarize_trace_event(event)}"
        )
    return "\n".join(lines)


def read_trace_events_or_warn(
    trace_file: Path,
    *,
    limit: int,
    event_type: str | None = None,
    since: str | None = None,
) -> list[dict[str, Any]]:
    events = read_trace_events(
        trace_file,
        limit=limit,
        event_type=event_type,
        since=since,
    )
    if events:
        return events
    typer.echo(
        f"Warning: no trace events found at {trace_file}",
        err=True,
    )
    return []


def follow_trace_file(
    trace_file: Path,
    *,
    limit: int,
    event_type: str | None = None,
    poll_interval: float = 0.2,
) -> None:
    events = read_trace_events_or_warn(
        trace_file,
        limit=limit,
        event_type=event_type,
    )
    if events:
        typer.echo(render_trace_rows(events))
    if not trace_file.exists():
        return
    with trace_file.open("r", encoding="utf-8") as handle:
        handle.seek(0, os.SEEK_END)
        try:
            while True:
                line = handle.readline()
                if not line:
                    time.sleep(poll_interval)
                    continue
                raw = line.strip()
                if not raw:
                    continue
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(payload, dict):
                    continue
                if event_type and str(payload.get("event", "") or "") != event_type:
                    continue
                typer.echo(render_trace_rows([payload]))
        except KeyboardInterrupt:
            return


def render_stats_human(stats: dict[str, Any]) -> str:
    lines = [
        f"Scope: {stats.get('scope') or 'all'}",
        f"Active records: {stats.get('active_record_count', 0)}",
        f"Soft-deleted records: {stats.get('soft_deleted_count', 0)}",
        (
            "Supersession chains: "
            f"{stats.get('supersession_chain_count', 0)} "
            f"(max depth {stats.get('max_chain_depth', 0)})"
        ),
        "Per-type counts:",
    ]
    per_type = list(stats.get("per_type", []))
    if per_type:
        for item in per_type:
            lines.append(
                "  "
                f"{item.get('type', '')}: count={item.get('count', 0)} "
                f"avg_confidence={float(item.get('avg_confidence', 0.0) or 0.0):.2f}"
            )
    else:
        lines.append("  none")
    lines.append("Candidate counts:")
    candidate_counts = dict(stats.get("candidate_counts", {}) or {})
    if candidate_counts:
        for status, count in sorted(candidate_counts.items()):
            lines.append(f"  {status}: {count}")
    else:
        lines.append("  none")
    return "\n".join(lines)


def build_inspect_payload(
    *,
    service: MemoryService,
    scope: str | None,
    db_path: Path,
    trace_file: Path,
) -> dict[str, Any]:
    store = service._store  # noqa: SLF001
    session_id = "inspect"
    agent_id = "inspect"
    try:
        parsed_scope = MemoryScope.parse(str(scope or "").strip())
    except ValueError:
        parsed_scope = None
    if parsed_scope is not None:
        if parsed_scope.is_session:
            session_id = parsed_scope.value
        if parsed_scope.is_agent:
            agent_id = parsed_scope.value
    snapshot = build_memory_snapshot(
        store=store,
        session_id=session_id,
        agent_id=agent_id,
    )
    stats: dict[str, Any] | None = None
    try:
        stats = compute_stats(store, scope=scope)
    except TypeError:
        stats = None
    all_events = read_trace_events(trace_file)
    recent_events = all_events[-5:]
    history_summary = None
    if isinstance(store, SQLiteMemoryStore):
        with store._connect() as conn:
            row = conn.execute(
                """
                SELECT scope, type, key, COUNT(*) AS depth, MAX(updated_at) AS latest_updated_at
                FROM memory_records
                WHERE key IS NOT NULL
                GROUP BY scope, type, key
                HAVING COUNT(*) > 1
                ORDER BY depth DESC, latest_updated_at DESC
                LIMIT 1
                """
            ).fetchone()
        if row is not None:
            records = store.history(
                str(row["scope"]), str(row["type"]), str(row["key"])
            )
            history_summary = summarize_history(records)
            history_summary["scope"] = str(row["scope"])
            history_summary["type"] = str(row["type"])
            history_summary["key"] = str(row["key"])
    return {
        "db_path": str(db_path),
        "scope": scope,
        "snapshot": snapshot.model_dump(),
        "stats": stats,
        "recent_trace_events": recent_events,
        "last_gc": last_trace_timestamp(
            events=all_events,
            event_name="memory.context.built",
        ),
        "last_reflection": last_trace_timestamp(
            events=all_events,
            event_name="memory.reflection.completed",
        ),
        "history_summary": history_summary,
    }


def resolve_integrated_db_path_from_env() -> Path | None:
    env_owner = resolve_environment_config()
    data_root_env = env_owner.get(OPENMINION_DATA_ROOT_ENV, "").strip()
    if not data_root_env:
        return None
    home_root = resolve_home_root()
    data_root = resolve_data_root(home_root, data_root=data_root_env)
    return (Path(data_root) / DEFAULT_INTEGRATED_SQLITE_SUBPATH).resolve()
