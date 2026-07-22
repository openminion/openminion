from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from openminion.base.config.env import resolve_environment_config

from ..config import load_config
from ..constants import DEFAULT_TRACE_FILENAME, OPENMINION_MEMORY_TRACE_FILE_ENV
from ..models import ArtifactRef
from ..storage.audit import AuditedMemoryStore
from ..storage.base import MemoryStore
from ..storage.postgres.store import PostgresMemoryStore
from ..storage.sqlite.store import SQLiteMemoryStore


def parse_iso_utc(value: str | None) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _resolve_path(value: str | Path) -> Path:
    return Path(value).expanduser().resolve(strict=False)


def configured_trace_file_path(
    *,
    memory_config: Any | None = None,
) -> Path | None:
    env_owner = resolve_environment_config()
    env_path = str(env_owner.get(OPENMINION_MEMORY_TRACE_FILE_ENV, "") or "").strip()
    if env_path:
        return _resolve_path(env_path)
    trace_file = getattr(memory_config, "trace_file", None)
    if trace_file:
        return _resolve_path(trace_file)
    return None


def resolve_trace_file_path(
    *,
    explicit_path: str | Path | None = None,
    memory_config: Any | None = None,
    db_path: str | Path | None = None,
) -> Path:
    if explicit_path:
        return _resolve_path(explicit_path)
    configured = configured_trace_file_path(memory_config=memory_config)
    if configured is not None:
        return configured
    if db_path:
        return _resolve_path(db_path).parent / DEFAULT_TRACE_FILENAME
    cfg = load_config()
    sqlite_path = getattr(getattr(cfg, "store", None), "sqlite_path", None)
    if sqlite_path:
        return _resolve_path(sqlite_path).parent / DEFAULT_TRACE_FILENAME
    return (Path.cwd() / DEFAULT_TRACE_FILENAME).resolve(strict=False)


def serialize_for_json(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, ArtifactRef):
        return asdict(value)
    if isinstance(value, dict):
        return {str(key): serialize_for_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [serialize_for_json(item) for item in value]
    return value


def append_trace_event(
    trace_file: Path,
    *,
    event_type: str,
    agent_id: str,
    ts: str,
    payload: Mapping[str, Any],
) -> None:
    trace_file.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "event": str(event_type),
        "agent_id": str(agent_id),
        "ts": str(ts),
        **{str(key): serialize_for_json(value) for key, value in payload.items()},
    }
    with trace_file.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, default=str) + "\n")
        handle.flush()


def read_trace_events(
    trace_file: Path,
    *,
    limit: int | None = None,
    event_type: str | None = None,
    since: str | None = None,
) -> list[dict[str, Any]]:
    if not trace_file.exists() or trace_file.stat().st_size == 0:
        return []
    since_dt = parse_iso_utc(since)
    results: list[dict[str, Any]] = []
    with trace_file.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            if event_type and str(payload.get("event", "") or "") != event_type:
                continue
            if since_dt is not None:
                event_dt = parse_iso_utc(str(payload.get("ts", "") or ""))
                if event_dt is None or event_dt < since_dt:
                    continue
            results.append(payload)
    if limit is not None and limit >= 0:
        return results[-int(limit) :]
    return results


def summarize_trace_event(payload: Mapping[str, Any], *, max_chars: int = 60) -> str:
    ignored = {"event", "agent_id", "ts"}
    parts: list[str] = []
    for key, value in payload.items():
        if key in ignored:
            continue
        rendered = str(value).strip()
        if not rendered:
            continue
        parts.append(f"{key}={rendered}")
    summary = " ".join(parts).strip()
    if len(summary) <= max_chars:
        return summary
    return summary[: max_chars - 3].rstrip() + "..."


def compute_sqlite_stats(
    store: SQLiteMemoryStore,
    *,
    scope: str | None = None,
) -> dict[str, Any]:
    where = "WHERE 1=1"
    params: list[Any] = []
    if scope:
        where += " AND scope = ?"
        params.append(scope)
    with store._connect() as conn:
        per_type_rows = conn.execute(
            f"""
            SELECT type, COUNT(*) AS count, AVG(confidence) AS avg_confidence
            FROM memory_records
            {where} AND is_deleted = 0
            GROUP BY type
            ORDER BY type ASC
            """,
            params,
        ).fetchall()
        soft_deleted_count = int(
            conn.execute(
                f"SELECT COUNT(*) FROM memory_records {where} AND is_deleted = 1",
                params,
            ).fetchone()[0]
            or 0
        )
        total_active = int(
            conn.execute(
                f"SELECT COUNT(*) FROM memory_records {where} AND is_deleted = 0",
                params,
            ).fetchone()[0]
            or 0
        )
        chain_rows = conn.execute(
            f"""
            SELECT scope, type, key, COUNT(*) AS depth
            FROM memory_records
            {where} AND key IS NOT NULL
            GROUP BY scope, type, key
            HAVING COUNT(*) > 1
            """,
            params,
        ).fetchall()
        candidate_where = "WHERE 1=1"
        candidate_params: list[Any] = []
        if scope:
            candidate_where += " AND proposed_scope = ?"
            candidate_params.append(scope)
        candidate_rows = conn.execute(
            f"""
            SELECT status, COUNT(*) AS count
            FROM memory_candidates
            {candidate_where}
            GROUP BY status
            ORDER BY status ASC
            """,
            candidate_params,
        ).fetchall()
    return {
        "scope": scope,
        "active_record_count": total_active,
        "soft_deleted_count": soft_deleted_count,
        "per_type": [
            {
                "type": str(row["type"]),
                "count": int(row["count"] or 0),
                "avg_confidence": round(float(row["avg_confidence"] or 0.0), 4),
            }
            for row in per_type_rows
        ],
        "supersession_chain_count": len(chain_rows),
        "max_chain_depth": max(
            [int(row["depth"] or 0) for row in chain_rows],
            default=0,
        ),
        "candidate_counts": {
            str(row["status"]): int(row["count"] or 0) for row in candidate_rows
        },
    }


def compute_postgres_stats(
    store: PostgresMemoryStore,
    *,
    scope: str | None = None,
) -> dict[str, Any]:
    from sqlalchemy import text

    where = "WHERE 1=1"
    params: dict[str, Any] = {}
    if scope:
        where += " AND scope = :scope"
        params["scope"] = scope
    candidate_where = "WHERE 1=1"
    candidate_params: dict[str, Any] = {}
    if scope:
        candidate_where += " AND proposed_scope = :scope"
        candidate_params["scope"] = scope

    with store.gc_connection() as conn:
        per_type_rows = _postgres_per_type_rows(conn, text=text, where=where, params=params)
        soft_deleted_count = int(
            conn.execute(
                text(
                    f"SELECT COUNT(*) FROM memory_records {where} AND is_deleted = TRUE"
                ),
                params,
            ).scalar()
            or 0
        )
        total_active = int(
            conn.execute(
                text(
                    f"SELECT COUNT(*) FROM memory_records {where} AND is_deleted = FALSE"
                ),
                params,
            ).scalar()
            or 0
        )
        chain_rows = (
            conn.execute(
                text(
                    f"""
                SELECT scope, type, key, COUNT(*) AS depth
                FROM memory_records
                {where} AND key IS NOT NULL
                GROUP BY scope, type, key
                HAVING COUNT(*) > 1
                """
                ),
                params,
            )
            .mappings()
            .all()
        )
        candidate_rows = (
            conn.execute(
                text(
                    f"""
                SELECT status, COUNT(*) AS count
                FROM memory_candidates
                {candidate_where}
                GROUP BY status
                ORDER BY status ASC
                """
                ),
                candidate_params,
            )
            .mappings()
            .all()
        )
    return {
        "scope": scope,
        "active_record_count": total_active,
        "soft_deleted_count": soft_deleted_count,
        "per_type": [
            {
                "type": str(row["type"]),
                "count": int(row["count"] or 0),
                "avg_confidence": round(float(row["avg_confidence"] or 0.0), 4),
            }
            for row in per_type_rows
        ],
        "supersession_chain_count": len(chain_rows),
        "max_chain_depth": max(
            [int(row["depth"] or 0) for row in chain_rows],
            default=0,
        ),
        "candidate_counts": {
            str(row["status"]): int(row["count"] or 0) for row in candidate_rows
        },
    }


def _postgres_per_type_rows(conn: Any, *, text: Any, where: str, params: dict[str, Any]):
    return (
        conn.execute(
            text(
                f"""
            SELECT type, COUNT(*) AS count, AVG(confidence) AS avg_confidence
            FROM memory_records
            {where} AND is_deleted = FALSE
            GROUP BY type
            ORDER BY type ASC
            """
            ),
            params,
        )
        .mappings()
        .all()
    )


def compute_stats(
    store: MemoryStore,
    *,
    scope: str | None = None,
) -> dict[str, Any]:
    if isinstance(store, AuditedMemoryStore):
        store = store._store
    if isinstance(store, SQLiteMemoryStore):
        return compute_sqlite_stats(store, scope=scope)
    if isinstance(store, PostgresMemoryStore):
        return compute_postgres_stats(store, scope=scope)
    raise TypeError(
        f"Unsupported memory store for stats: {type(store)!r}"
    )  # allow-bare-raise: defensive type guard


def format_history_timeline(records: Iterable[Any]) -> str:
    ordered = sorted(
        list(records),
        key=lambda record: str(getattr(record, "created_at", "") or ""),
    )
    if not ordered:
        return "No history found."
    lines: list[str] = []
    for index, record in enumerate(ordered, start=1):
        status = "[active]"
        if bool(getattr(record, "is_deleted", False)):
            status = "[superseded]"
        key = str(getattr(record, "key", "") or "-")
        record_type = str(getattr(record, "type", "") or "-")
        confidence = float(getattr(record, "confidence", 0.0) or 0.0)
        title = str(
            getattr(record, "title", "") or getattr(record, "content", "") or ""
        )
        title = " ".join(title.split()).strip()
        if len(title) > 90:
            title = title[:87].rstrip() + "..."
        lines.append(
            f"{index}. {status} {record_type} key={key} confidence={confidence:.2f} id={getattr(record, 'id', '')}"
        )
        if title:
            lines.append(f"   {title}")
        reason = str(getattr(record, "supersession_reason", "") or "").strip()
        next_id = str(getattr(record, "superseded_by_id", "") or "").strip()
        if reason and next_id:
            lines.append(f"   -> superseded by {next_id} ({reason})")
    return "\n".join(lines)


def summarize_history(records: Iterable[Any]) -> dict[str, Any]:
    ordered = sorted(
        list(records),
        key=lambda record: str(getattr(record, "created_at", "") or ""),
    )
    return {
        "depth": len(ordered),
        "active_id": next(
            (
                str(getattr(record, "id", "") or "")
                for record in reversed(ordered)
                if not bool(getattr(record, "is_deleted", False))
            ),
            "",
        ),
        "reasons": [
            str(getattr(record, "supersession_reason", "") or "")
            for record in ordered
            if str(getattr(record, "supersession_reason", "") or "").strip()
        ],
    }


def last_trace_timestamp(
    events: Iterable[Mapping[str, Any]],
    *,
    event_name: str,
) -> str | None:
    for payload in reversed(list(events)):
        if str(payload.get("event", "") or "") == event_name:
            ts = str(payload.get("ts", "") or "").strip()
            if ts:
                return ts
    return None
