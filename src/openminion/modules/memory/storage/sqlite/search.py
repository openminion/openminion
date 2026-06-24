"""SQLite search/query helpers for memory storage."""

import datetime
import logging
import math
import sqlite3
from dataclasses import replace
from typing import Any

from ...models import MemoryRecord, MemoryType
from ..base import SearchQueryOptions
from .queries import is_fts_query_parse_error, sanitize_fts_query

logger = logging.getLogger(__name__)


def _rank_fts_results_with_scores(
    results: list[tuple[MemoryRecord, float]],
) -> list[tuple[MemoryRecord, float]]:
    def sort_key(item: tuple[MemoryRecord, float]) -> tuple[float, float, Any]:
        record, score = item
        return (score, -record.confidence, record.updated_at)

    return sorted(results, key=sort_key)


def search(store: Any, options: SearchQueryOptions) -> list[MemoryRecord]:
    if not options.query or not options.scopes:
        return []

    placeholders_scopes = ",".join("?" * len(options.scopes))
    query = f"""
        SELECT r.*, bm25(memory_fts) as score
        FROM memory_fts f
        JOIN memory_records r ON f.id = r.id
        WHERE memory_fts MATCH ?
        AND r.scope IN ({placeholders_scopes})
        AND {"(r.is_deleted = 0 OR r.superseded_by_id IS NOT NULL)" if options.include_invalidated else "r.is_deleted = 0"}
    """
    params = [options.query] + list(options.scopes)
    if not options.include_invalidated:
        query += " AND (r.valid_to IS NULL OR r.valid_to > ?)"
        params.append(datetime.datetime.now(datetime.timezone.utc).isoformat())

    if options.types:
        placeholders_types = ",".join("?" * len(options.types))
        query += f" AND r.type IN ({placeholders_types})"
        params.extend(options.types)

    if options.tiers:
        placeholders_tiers = ",".join("?" * len(options.tiers))
        query += f" AND r.tier IN ({placeholders_tiers})"
        params.extend(options.tiers)

    if options.filters:
        f = options.filters
        if f.min_confidence is not None:
            query += " AND r.confidence >= ?"
            params.append(f.min_confidence)
        if f.source_allowlist:
            pl_src = ",".join("?" * len(f.source_allowlist))
            query += f" AND r.source IN ({pl_src})"
            params.extend(f.source_allowlist)
        if f.updated_since:
            query += " AND r.updated_at >= ?"
            params.append(f.updated_since)

    query_candidates = [str(options.query)]
    sanitized_query = sanitize_fts_query(str(options.query))
    if sanitized_query and sanitized_query != str(options.query):
        query_candidates.append(sanitized_query)

    last_exc: sqlite3.OperationalError | None = None
    raw_results: list[tuple[MemoryRecord, float]] = []
    for match_query in query_candidates:
        try:
            with store._connect() as conn:
                cursor = conn.execute(query, [match_query] + params[1:])
                raw_results = [
                    (store._create_record_from_row(row), float(row["score"]))
                    for row in cursor.fetchall()
                ]
            if raw_results or match_query == query_candidates[-1]:
                break
        except sqlite3.OperationalError as exc:
            if is_fts_query_parse_error(exc):
                last_exc = exc
                continue
            raise
    else:
        if last_exc is not None:
            logger.debug(
                "memory search fallback exhausted for query=%r: %s",
                options.query,
                last_exc,
            )
            return []
        return []

    ranked_results = _rank_fts_results_with_scores(raw_results)
    if options.limit is not None:
        ranked_results = ranked_results[: options.limit]
    if not ranked_results:
        return []

    scored_records: list[MemoryRecord] = []
    for record, raw_score in ranked_results:
        raw = float(raw_score)
        normalized_score = 0.0 if raw >= 0.0 else 1.0 - math.exp(raw)
        normalized_score = max(0.0, min(1.0, normalized_score))
        meta = dict(getattr(record, "meta", {}) or {})
        meta["bm25_raw_score"] = raw
        meta["bm25_score"] = normalized_score
        scored_records.append(replace(record, meta=meta))

    return scored_records


def retrieve_by_entities(
    store: Any,
    entities: list[str],
    scopes: list[str],
    *,
    types: list[MemoryType] | None = None,
    tiers: list[str] | None = None,
    limit: int | None = None,
) -> list[MemoryRecord]:
    if not entities or not scopes:
        return []

    placeholders_entities = ",".join("?" * len(entities))
    placeholders_scopes = ",".join("?" * len(scopes))

    query = f"""
        SELECT DISTINCT r.*
        FROM memory_records r
        JOIN memory_entities e ON r.id = e.record_id
        WHERE (r.is_deleted = 0 OR r.superseded_by_id IS NOT NULL)
        AND (r.valid_to IS NULL OR r.valid_to > ?)
        AND e.entity IN ({placeholders_entities})
        AND r.scope IN ({placeholders_scopes})
    """
    params = (
        [datetime.datetime.now(datetime.timezone.utc).isoformat()]
        + list(entities)
        + list(scopes)
    )

    if types:
        placeholders_types = ",".join("?" * len(types))
        query += f" AND r.type IN ({placeholders_types})"
        params.extend(types)

    if tiers:
        placeholders_tiers = ",".join("?" * len(tiers))
        query += f" AND r.tier IN ({placeholders_tiers})"
        params.extend(tiers)

    query += " ORDER BY r.updated_at DESC"

    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)

    with store._connect() as conn:
        cursor = conn.execute(query, params)
        return [store._create_record_from_row(row) for row in cursor.fetchall()]


__all__ = ["retrieve_by_entities", "search"]
