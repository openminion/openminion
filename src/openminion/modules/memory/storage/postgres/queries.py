import datetime
from dataclasses import replace
from typing import Any

from ...models import MemoryRecord, MemoryType
from ..base import ListQueryOptions, SearchQueryOptions
from .query_terms import _tsquery_candidates
from .sql import _named_params
from ...errors import NotFoundError

# Expiry is an instant, not a string: a stored offset must not change readability and the
# comparison keeps the stored precision, because CAST(... AS timestamptz) is
# microsecond-exact. PostgreSQL raises instead of returning null on an impossible value,
# so the predicate proves the value is a calendar-valid instant from year 0001 before the
# cast: month lengths and leap years are spelled out inside the pattern (POSIX ERE has no
# lookahead) and the year is written so 0000 cannot match. Anything else - year 0000,
# 2020-02-30, 2020-13-01, '!' - is not an expiry and never hides a record. A value without
# an offset is read as UTC. Groups are capturing because SQLAlchemy's text() reads
# "?:<digit>" as a bind parameter.
_EXPIRY_YEAR = r"([1-9][0-9]{3}|[0-9][1-9][0-9]{2}|[0-9]{2}[1-9][0-9]|[0-9]{3}[1-9])"
_EXPIRY_LEAP_YEAR = (
    r"[0-9]{2}(0[48]|[2468][048]|[13579][26])"
    r"|(0[48]|[2468][048]|[13579][26])00"
)
_EXPIRY_DATE = (
    _EXPIRY_YEAR
    + r"-((0[13578]|1[02])-(0[1-9]|[12][0-9]|3[01])"
    + r"|(0[469]|11)-(0[1-9]|[12][0-9]|30)"
    + r"|02-(0[1-9]|1[0-9]|2[0-8]))"
    + r"|("
    + _EXPIRY_LEAP_YEAR
    + r")-02-29"
)
_EXPIRY_INSTANT = (
    r"("
    + _EXPIRY_DATE
    + r")[T ]([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](\.[0-9]{1,6})?"
    r"(Z|[+-](0[0-9]:[0-5][0-9]|1[0-3]:[0-5][0-9]|14:00))?"
)
_EXPIRY_OFFSET = r"(Z|[+-][0-9]{2}:[0-9]{2})$"
_EXPIRY_CLAUSE = (
    "(expires_at IS NULL OR CASE WHEN expires_at ~ '^" + _EXPIRY_INSTANT + "$'"
    " THEN CAST(expires_at || CASE WHEN expires_at ~ '" + _EXPIRY_OFFSET + "'"
    " THEN '' ELSE 'Z' END AS timestamptz)"
    " > CAST(:query_now AS timestamptz) ELSE TRUE END)"
)


def _active_record_clause(*, include_invalidated: bool) -> list[str]:
    clauses = (
        ["(is_deleted = FALSE OR superseded_by_id IS NOT NULL)"]
        if include_invalidated
        else ["is_deleted = FALSE"]
    )
    if not include_invalidated:
        clauses.append("(valid_to IS NULL OR valid_to > :query_now)")
        # Expiry is enforced on read as well as by the purge run, and compared as an
        # instant so a stored offset cannot change readability. A value that is not a
        # calendar-valid instant is not an expiry, so the record stays visible.
        clauses.append(_EXPIRY_CLAUSE)
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
    query_suffix = [f"AND scope IN ({scopes_sql})"]
    query_suffix.extend(
        f"AND {clause}"
        for clause in _active_record_clause(
            include_invalidated=options.include_invalidated
        )
    )
    params: dict[str, Any] = dict(scopes_params)
    if not options.include_invalidated:
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
    records: list[MemoryRecord] = []
    for row in scored_rows:
        raw_score = float(row.get("score") or 0.0)
        normalized_score = (
            1.0 if score_span <= 0 else (raw_score - min_score) / score_span
        )
        record = store._create_record_from_row(row)
        records.append(
            replace(
                record,
                meta={
                    **dict(record.meta or {}),
                    "bm25_score": normalized_score,
                    "tsrank_raw_score": raw_score,
                    "tsrank_score": normalized_score,
                },
            )
        )
    return records


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
        """
        + f"AND {_EXPIRY_CLAUSE}",
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
