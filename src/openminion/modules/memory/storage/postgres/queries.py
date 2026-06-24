import datetime
from dataclasses import replace
from typing import Any

from ...models import MemoryRecord, MemoryType
from ..base import ListQueryOptions, SearchQueryOptions
from .query_terms import _tsquery_candidates
from .sql import _named_params
from ...errors import NotFoundError


def _active_record_clause(*, include_invalidated: bool) -> list[str]:
    clauses = (
        ["(is_deleted = FALSE OR superseded_by_id IS NOT NULL)"]
        if include_invalidated
        else ["is_deleted = FALSE"]
    )
    if not include_invalidated:
        clauses.append("(valid_to IS NULL OR valid_to > :query_now)")
    return clauses


def get(store: Any, record_id: str) -> MemoryRecord | None:
    row = store._fetchone(
        "SELECT * FROM memory_records WHERE id = :id",
        {"id": record_id},
    )
    return None if row is None else store._create_record_from_row(row)


def list_records(store: Any, options: ListQueryOptions) -> list[MemoryRecord]:
    query = [
        "SELECT * FROM memory_records WHERE "
        + " AND ".join(
            _active_record_clause(include_invalidated=options.include_invalidated)
        )
    ]
    params: dict[str, Any] = {}
    if not options.include_invalidated:
        params["query_now"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    if options.scopes:
        scopes_sql, scopes_params = _named_params("scope", list(options.scopes))
        query.append(f"AND scope IN ({scopes_sql})")
        params.update(scopes_params)
    if options.types:
        types_sql, types_params = _named_params("type", list(options.types))
        query.append(f"AND type IN ({types_sql})")
        params.update(types_params)
    if options.tiers:
        tiers_sql, tiers_params = _named_params("tier", list(options.tiers))
        query.append(f"AND tier IN ({tiers_sql})")
        params.update(tiers_params)
    if options.order_by is not None:
        query.append(
            "ORDER BY updated_at DESC"
            if options.order_by.value == "updated_at_desc"
            else "ORDER BY updated_at ASC"
        )
    if options.limit is not None:
        query.append("LIMIT :limit")
        params["limit"] = int(options.limit)
        if options.offset is not None:
            query.append("OFFSET :offset")
            params["offset"] = int(options.offset)
    return [
        store._create_record_from_row(row)
        for row in store._fetchall(" ".join(query), params)
    ]


def list_scopes(store: Any) -> list[str]:
    rows = store._fetchall(
        """
        SELECT DISTINCT scope
          FROM memory_records
         WHERE is_deleted = FALSE
         ORDER BY scope ASC
        """
    )
    return [str(row["scope"]) for row in rows if str(row.get("scope") or "").strip()]


def touch_last_hit(store: Any, record_id: str) -> None:
    updated = store._execute(
        """
        UPDATE memory_records
           SET last_hit_at = :last_hit_at,
               access_count = COALESCE(access_count, 0) + 1
         WHERE id = :id
        """,
        {
            "last_hit_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "id": record_id,
        },
    )
    if updated <= 0:
        raise NotFoundError(f"record not found: {record_id}")


def search(store: Any, options: SearchQueryOptions) -> list[MemoryRecord]:
    if not options.query or not options.scopes:
        return []
    scopes_sql, scopes_params = _named_params("scope", list(options.scopes))
    query_suffix = [
        f"AND scope IN ({scopes_sql})",
        (
            "AND (is_deleted = FALSE OR superseded_by_id IS NOT NULL)"
            if options.include_invalidated
            else "AND is_deleted = FALSE"
        ),
    ]
    params: dict[str, Any] = dict(scopes_params)
    if not options.include_invalidated:
        query_suffix.append("AND (valid_to IS NULL OR valid_to > :query_now)")
        params["query_now"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    if options.types:
        types_sql, types_params = _named_params("type", list(options.types))
        query_suffix.append(f"AND type IN ({types_sql})")
        params.update(types_params)
    if options.tiers:
        tiers_sql, tiers_params = _named_params("tier", list(options.tiers))
        query_suffix.append(f"AND tier IN ({tiers_sql})")
        params.update(tiers_params)
    if options.filters:
        if options.filters.min_confidence is not None:
            query_suffix.append("AND confidence >= :min_confidence")
            params["min_confidence"] = float(options.filters.min_confidence)
        if options.filters.source_allowlist:
            source_sql, source_params = _named_params(
                "source", list(options.filters.source_allowlist)
            )
            query_suffix.append(f"AND source IN ({source_sql})")
            params.update(source_params)
        if options.filters.updated_since:
            query_suffix.append("AND updated_at >= :updated_since")
            params["updated_since"] = str(options.filters.updated_since)
    scored_rows: list[dict[str, Any]] = []
    candidate_defs = store._tsquery_candidates(str(options.query))
    for expression, expr_params in candidate_defs:
        run_params = dict(params)
        run_params.update(expr_params)
        rows = store._fetchall(
            f"""
            SELECT *, ts_rank_cd(search_vector, {expression}) AS score
              FROM memory_records
             WHERE search_vector @@ {expression}
               {" ".join(query_suffix)}
             ORDER BY score DESC, confidence DESC, updated_at DESC
            """,
            run_params,
        )
        if rows or (expression, expr_params) == candidate_defs[-1]:
            scored_rows = rows
            break
    if options.limit is not None:
        scored_rows = scored_rows[: int(options.limit)]
    if not scored_rows:
        return []
    raw_scores = [float(row.get("score") or 0.0) for row in scored_rows]
    min_score = min(raw_scores)
    score_span = max(raw_scores) - min_score
    return [
        replace(
            store._create_record_from_row(row),
            meta={
                **dict(store._create_record_from_row(row).meta or {}),
                "tsrank_raw_score": float(row.get("score") or 0.0),
                "tsrank_score": 1.0
                if score_span <= 0
                else (float(row.get("score") or 0.0) - min_score) / score_span,
            },
        )
        for row in scored_rows
    ]


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
    entities_sql, entities_params = _named_params("entity", list(entities))
    scopes_sql, scopes_params = _named_params("scope", list(scopes))
    params = dict(entities_params)
    params.update(scopes_params)
    query = [
        """
        SELECT DISTINCT r.*
          FROM memory_records r
          JOIN memory_entities e ON r.id = e.record_id
         WHERE r.is_deleted = FALSE
           AND (r.valid_to IS NULL OR r.valid_to > :query_now)
        """,
        f"AND e.entity IN ({entities_sql})",
        f"AND r.scope IN ({scopes_sql})",
    ]
    if types:
        types_sql, types_params = _named_params("type", list(types))
        query.append(f"AND r.type IN ({types_sql})")
        params.update(types_params)
    if tiers:
        tiers_sql, tiers_params = _named_params("tier", list(tiers))
        query.append(f"AND r.tier IN ({tiers_sql})")
        params.update(tiers_params)
    query.append("ORDER BY r.updated_at DESC")
    if limit is not None:
        query.append("LIMIT :limit")
        params["limit"] = int(limit)
    params["query_now"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    return [
        store._create_record_from_row(row)
        for row in store._fetchall(" ".join(query), params)
    ]


__all__ = [
    "_tsquery_candidates",
    "get",
    "list_records",
    "list_scopes",
    "retrieve_by_entities",
    "search",
    "touch_last_hit",
]
