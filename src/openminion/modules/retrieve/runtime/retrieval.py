from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol, Sequence

from openminion.modules.memory.runtime.scorer import clamp01

from ..schemas import RetrievalFilters, RetrievalStrategy, RetrievedItem
from .time import parse_iso_timestamp

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
_LOGGER = logging.getLogger(__name__)
_RLM_TRUST_SCORES = {"sm": 1.0, "skill": 0.9, "session": 0.7}


def _tokenize(text: str) -> list[str]:
    return [part.lower() for part in _TOKEN_RE.findall(text or "")]


def _title_identity_boost(
    *, query_tokens: Sequence[str], title: str, max_boost: float
) -> float:
    title_tokens = list(dict.fromkeys(_tokenize(title)))
    if not title_tokens:
        return 0.0
    query_token_set = {
        str(token).strip().lower() for token in query_tokens if str(token).strip()
    }
    if not query_token_set:
        return 0.0
    matched = sum(1 for token in title_tokens if token in query_token_set)
    if matched <= 0:
        return 0.0
    overlap = matched / len(title_tokens)
    title_weight = min(1.0, len(title_tokens) / 2.0)
    return clamp01(float(max_boost) * overlap * title_weight)


def _safe_json_loads(raw: str | None, fallback: Any) -> Any:
    if raw is None:
        return fallback
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return fallback


def _normalize_scope_keys(raw_scope_keys: Sequence[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in raw_scope_keys:
        key = str(raw or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        normalized.append(key)
    return normalized


_MEMORY_TYPE_TAGS = {
    "correction",
    "meta_rule_preference",
    "user_preference",
    "project_convention",
    "tool_habit",
    "tool_outcome",
    "strategy_outcome",
    "pin",
    "declared_goal",
    "goal_revision",
}


def _candidate_type(tags: Sequence[str]) -> str:
    normalized_tags = {str(tag or "").strip().lower() for tag in tags}
    for candidate_type in _MEMORY_TYPE_TAGS:
        if candidate_type in normalized_tags:
            return candidate_type
    return "fact"


@dataclass(frozen=True)
class RetrievalContext:
    query: str
    purpose: str
    requested_strategy: str
    scope: Mapping[str, Any]
    filters: RetrievalFilters
    k: int


class RetrievalStrategyResolver(Protocol):
    def resolve(self, ctx: RetrievalContext) -> RetrievalStrategy: ...


def resolve_retrieval_strategy(
    *,
    requested_strategy: str,
    purpose: str,
    query: str,
    scope: Mapping[str, Any],
    filters: RetrievalFilters,
    default_strategy: str,
    vector_adapter_enabled: bool,
    embeddings_enabled: bool,
) -> RetrievalStrategy:
    normalized = str(requested_strategy or "auto").strip().lower()
    if normalized == "semantic":
        if vector_adapter_enabled and embeddings_enabled:
            return "semantic"  # type: ignore[return-value]
        return "contextual"
    if normalized in {"contextual", "raptor", "longrag_doc_group"}:
        return normalized  # type: ignore[return-value]

    if str(purpose).lower() == "verify":
        return "contextual"
    if bool(scope.get("doc_heavy")):
        return "raptor"
    if default_strategy in {"contextual", "raptor", "longrag_doc_group"}:
        return default_strategy  # type: ignore[return-value]
    return "contextual"


def generate_candidates(
    service: Any,
    *,
    query: str,
    scope: dict[str, Any],
    filters: RetrievalFilters,
    limit: int,
) -> list[dict[str, Any]]:
    tokens = _tokenize(query)
    allowed_scopes = service._allowed_scopes(scope)
    candidate_limit = max(1, int(limit))
    defaults = service.config.defaults
    has_post_filters = bool(filters.tags) or filters.time_window_hours is not None
    fetch_limit = candidate_limit
    if has_post_filters:
        fetch_limit = candidate_limit * int(defaults.candidate_overfetch_multiplier)

    rows: list[Mapping[str, Any]]
    if tokens:
        rows = search_rows(
            service,
            tokens=tokens,
            allowed_scopes=allowed_scopes,
            filters=filters,
            limit=fetch_limit,
        )
    else:
        rows = recent_rows(
            service,
            allowed_scopes=allowed_scopes,
            filters=filters,
            limit=fetch_limit,
        )

    results: list[dict[str, Any]] = []
    for row in rows:
        tags = _safe_json_loads(str(row["tags_json"]), [])
        if not isinstance(tags, list):
            tags = []
        tags = [str(tag) for tag in tags if str(tag).strip()]

        if filters.tags:
            wanted = {tag.strip().lower() for tag in filters.tags if tag.strip()}
            if wanted and not wanted.intersection({tag.lower() for tag in tags}):
                continue

        created_at = str(row["created_at"])
        if filters.time_window_hours is not None:
            created_dt = parse_iso_timestamp(created_at)
            if created_dt is not None:
                age_hours = max(
                    0.0,
                    (datetime.now(timezone.utc) - created_dt).total_seconds() / 3600.0,
                )
                if age_hours > float(filters.time_window_hours):
                    continue

        bm25_score = float(row["bm25_score"] or 0.0)

        results.append(
            {
                "unit_id": str(row["unit_id"]),
                "doc_id": str(row["doc_id"]),
                "title": str(row["title"] or ""),
                "source_type": str(row["source_type"]),
                "source_ref": str(row["source_ref"]),
                "doc_scope": str(row["scope"]),
                "tags": tags,
                "created_at": created_at,
                "unit_kind": str(row["unit_kind"]),
                "level": str(row["level"] or "none"),
                "node_id": str(row["node_id"]) if row["node_id"] is not None else None,
                "group_id": str(row["group_id"])
                if row["group_id"] is not None
                else None,
                "text_ref": str(row["text_ref"]),
                "offsets": _safe_json_loads(str(row["offsets_json"]), {}),
                "query": query,
                "bm25_score": bm25_score,
                "type": _candidate_type(tags),
                "confidence": (
                    float(defaults.confidence_memory)
                    if str(row["source_type"]) == "mem"
                    else float(defaults.confidence_default)
                ),
                "why": "",
                "meta": {
                    "hit_count": int(row["hit_count"] or 0),
                    "last_hit_at": str(row["last_hit_at"])
                    if row["last_hit_at"]
                    else None,
                    "feedback_score": clamp01(float(row["feedback_score"] or 0.0)),
                },
            }
        )

    results.sort(
        key=lambda item: (
            -float(item.get("bm25_score", 0.0) or 0.0),
            str(item["unit_id"]),
        )
    )
    if has_post_filters and len(results) < candidate_limit:
        _LOGGER.debug(
            "post-filter candidate shrinkage: %d -> %d (requested %d)",
            len(rows),
            len(results),
            candidate_limit,
        )
    return results[:candidate_limit]


def apply_title_identity_boost_in_place(
    *,
    candidates: Sequence[dict[str, Any]],
    query: str,
    max_boost: float,
) -> None:
    query_tokens = _tokenize(query)
    for candidate in candidates:
        meta = candidate.get("meta")
        if not isinstance(meta, dict):
            meta = {}
            candidate["meta"] = meta
        breakdown = meta.get("score_breakdown")
        if not isinstance(breakdown, dict):
            breakdown = {}
            meta["score_breakdown"] = breakdown
        boost = _title_identity_boost(
            query_tokens=query_tokens,
            title=str(candidate.get("title", "") or ""),
            max_boost=max_boost,
        )
        base_score = clamp01(
            float(
                candidate.get(
                    "unified_score",
                    candidate.get("score", 0.0),
                )
                or 0.0
            )
        )
        boosted_score = clamp01(base_score + boost)
        breakdown["title_identity_boost"] = clamp01(boost)
        breakdown["unified_score"] = clamp01(boosted_score)
        meta["unified_score"] = clamp01(boosted_score)
        candidate["score"] = clamp01(boosted_score)
        candidate["unified_score"] = clamp01(boosted_score)
    candidates.sort(
        key=lambda item: (
            -float(item.get("unified_score", item.get("score", 0.0)) or 0.0),
            str(item.get("unit_id", "")),
        )
    )


def _select_contextual(
    candidates: list[dict[str, Any]], target_k: int
) -> list[dict[str, Any]]:
    return candidates[:target_k]


def _select_semantic(
    service: Any,
    *,
    candidates: list[dict[str, Any]],
    target_k: int,
) -> list[dict[str, Any]]:
    if service.vector_adapter is None:
        return _select_contextual(candidates, target_k)
    return select_candidates_semantic(service, candidates=candidates, k=target_k)


def _select_longrag_doc_group(
    service: Any,
    *,
    candidates: list[dict[str, Any]],
    target_k: int,
) -> list[dict[str, Any]]:
    preferred = [
        item
        for item in candidates
        if item.get("unit_kind") in {"doc_group", "document"}
    ]
    fallback = [
        item
        for item in candidates
        if item.get("unit_kind") not in {"doc_group", "document"}
    ]
    deduped = service._dedupe_candidates(preferred + fallback)
    return deduped[:target_k]


def _select_raptor(
    service: Any,
    *,
    candidates: list[dict[str, Any]],
    target_k: int,
) -> list[dict[str, Any]]:
    internals = [
        item for item in candidates if item.get("level") in {"root", "internal"}
    ]
    leaves = [item for item in candidates if item.get("level") in {"leaf", "none"}]

    selected = list(internals[: service.config.defaults.raptor_internal_k])
    candidate_map = {str(item["unit_id"]): item for item in candidates}
    missing_leaf_refs: list[tuple[dict[str, Any], str]] = []
    for node in list(selected):
        if str(node.get("level", "none")) not in {"root", "internal"}:
            continue
        node_id = str(node.get("node_id") or "")
        if not node_id:
            continue
        for leaf_id in service._leaf_ids_for_node(node_id):
            if leaf_id in candidate_map:
                selected.append(candidate_map[leaf_id])
                continue
            missing_leaf_refs.append((node, leaf_id))

    missing_rows = _lookup_missing_raptor_leaf_rows(service, missing_leaf_refs)
    for node, leaf_id in missing_leaf_refs:
        row = missing_rows.get(leaf_id)
        if row is None:
            continue
        selected.append(
            candidate_from_row(
                service,
                row,
                inherited_score=float(node.get("score", 0.0))
                * float(service.config.defaults.raptor_inheritance_multiplier),
            )
        )

    selected.extend(leaves)
    deduped = service._dedupe_candidates(selected)
    deduped.sort(
        key=lambda item: (
            -float(item.get("score", 0.0)),
            str(item.get("unit_id", "")),
        )
    )
    return deduped[:target_k]


def _lookup_missing_raptor_leaf_rows(
    service: Any,
    missing_leaf_refs: Sequence[tuple[dict[str, Any], str]],
) -> dict[str, Mapping[str, Any]]:
    if not missing_leaf_refs:
        return {}
    missing_leaf_ids = [leaf_id for _node, leaf_id in missing_leaf_refs]
    missing_rows = service._lookup_unit_rows_batch(missing_leaf_ids)
    missing_unique_count = len(dict.fromkeys(missing_leaf_ids))
    if len(missing_rows) < missing_unique_count:
        _LOGGER.warning(
            "raptor leaf batch lookup returned %d/%d rows",
            len(missing_rows),
            missing_unique_count,
        )
    return missing_rows


def select_candidates(
    service: Any,
    *,
    candidates: list[dict[str, Any]],
    strategy: RetrievalStrategy,
    k: int,
) -> list[dict[str, Any]]:
    if not candidates:
        return []
    target_k = max(1, int(k))
    if strategy == "semantic":
        return _select_semantic(service, candidates=candidates, target_k=target_k)
    if strategy == "longrag_doc_group":
        return _select_longrag_doc_group(
            service, candidates=candidates, target_k=target_k
        )
    if strategy == "raptor":
        return _select_raptor(service, candidates=candidates, target_k=target_k)
    return _select_contextual(candidates, target_k)


def select_candidates_semantic(
    service: Any,
    *,
    candidates: list[dict[str, Any]],
    k: int,
) -> list[dict[str, Any]]:
    if not candidates or not service.vector_adapter:
        return candidates[:k]

    query_text = ""
    try:
        query_text = candidates[0].get("query", "") if candidates else ""
    except Exception as exc:
        _LOGGER.debug("semantic query extraction fallback: %s", exc, exc_info=True)
    if not query_text:
        return candidates[:k]

    try:
        search_results = service.vector_adapter.search(
            query=query_text,
            k=min(k * 2, len(candidates)),
            filters=None,
        )
        score_map: dict[str, float] = {}
        for result in search_results:
            unit_id = result.get("id", "")
            score_map[unit_id] = result.get("score", 0.0)
        for item in candidates:
            unit_id = item.get("unit_id", "")
            item["vector_score"] = score_map.get(unit_id, 0.0)
        candidates = sorted(
            candidates,
            key=lambda item: (
                -float(item.get("vector_score", 0.0)),
                str(item.get("unit_id", "")),
            ),
        )
    except Exception as exc:
        _LOGGER.warning("semantic_search_fallback: %s", exc, exc_info=True)
    return candidates[:k]


def to_retrieved_item(
    service: Any,
    *,
    candidate: dict[str, Any],
    strategy: RetrievalStrategy,
) -> RetrievedItem:
    source_type = service._normalize_source_type(
        str(candidate.get("source_type", "doc"))
    )
    source_ref = str(candidate.get("source_ref", "retrieve://unknown"))
    unit_id = str(candidate.get("unit_id", ""))
    unit_kind = service._normalize_unit_kind(str(candidate.get("unit_kind", "chunk")))
    level = service._normalize_level(str(candidate.get("level", "none")))
    node_id = str(candidate.get("node_id")) if candidate.get("node_id") else None
    group_id = str(candidate.get("group_id")) if candidate.get("group_id") else None
    text = service._read_text_blob(str(candidate.get("text_ref", ""))).strip()
    snippet = service._trim_tokens(
        text, max_tokens=service.config.defaults.snippet_tokens
    )

    if node_id and level in {"root", "internal"}:
        ref_id = f"node://{node_id}"
    elif group_id:
        ref_id = f"group://{group_id}"
    elif unit_id:
        ref_id = f"{source_ref}#u={unit_id}"
    else:
        ref_id = source_ref

    rlm_source = service._to_rlm_source(source_type)
    raptor_level = "none"
    if level == "leaf":
        raptor_level = "leaf"
    elif level in {"root", "internal"}:
        raptor_level = "internal"

    candidate_meta = candidate.get("meta") or {}
    score = clamp01(float(candidate.get("score", 0.0) or 0.0))
    recency = clamp01(float(candidate.get("recency", 0.0) or 0.0))
    if isinstance(candidate_meta, Mapping):
        breakdown = candidate_meta.get("score_breakdown", {})
        if isinstance(breakdown, Mapping):
            try:
                recency = clamp01(float(breakdown.get("recency", recency) or 0.0))
            except (TypeError, ValueError):
                recency = clamp01(recency)
    tags = [str(tag) for tag in candidate.get("tags", []) if str(tag).strip()]

    meta = {
        "doc_id": str(candidate.get("doc_id", "")),
        "unit_id": unit_id,
        "node_id": node_id,
        "offsets": candidate.get("offsets", {}),
        "created_at": candidate.get("created_at"),
        "tags": tags,
        "hit_count": int(candidate_meta.get("hit_count") or 0),
        "last_hit_at": candidate_meta.get("last_hit_at"),
        "feedback_score": clamp01(float(candidate_meta.get("feedback_score") or 0.0)),
        "score_breakdown": candidate_meta.get("score_breakdown", {}),
    }

    return RetrievedItem(
        ref_type=source_type,
        ref_id=ref_id,
        text_snippet=snippet,
        score=score,
        why=str(candidate.get("why", "")),
        level=level,
        unit_kind=unit_kind,
        meta=meta,
        source=rlm_source,
        text=snippet,
        recency_score=recency,
        tags=tags,
        created_at=str(candidate.get("created_at"))
        if candidate.get("created_at")
        else None,
        retrieval_strategy=strategy,
        raptor_level=raptor_level,
        node_id=node_id,
        doc_group_id=group_id,
        trust_score=_RLM_TRUST_SCORES.get(rlm_source, 0.6),
    )


def search_rows(
    service: Any,
    *,
    tokens: list[str],
    allowed_scopes: list[str],
    filters: RetrievalFilters,
    limit: int,
) -> list[Mapping[str, Any]]:
    params: list[Any] = []
    joins = (
        "FROM retrievectl_units u "
        "JOIN retrievectl_docs d ON d.doc_id = u.doc_id "
        "JOIN retrievectl_units_fts f ON f.unit_id = u.unit_id "
    )
    where: list[str] = []

    if service._fts_enabled:
        terms = [f'"{token}"' for token in tokens if token.strip()]
        query = " OR ".join(terms) if terms else " ".join(tokens)
        where.append("retrievectl_units_fts MATCH ?")
        params.append(query)
    else:
        like_clauses = []
        for token in tokens:
            like_clauses.append("f.title LIKE ?")
            params.append(f"%{token}%")
            like_clauses.append("f.fts_text LIKE ?")
            params.append(f"%{token}%")
        where.append("(" + " OR ".join(like_clauses) + ")")

    if allowed_scopes:
        where.append("d.scope IN ({})".format(",".join("?" for _ in allowed_scopes)))
        params.extend(allowed_scopes)
    scope_keys = _normalize_scope_keys(filters.scope_keys)
    if scope_keys:
        where.append("d.scope_key IN ({})".format(",".join("?" for _ in scope_keys)))
        params.extend(scope_keys)
    if filters.types:
        where.append(
            "d.source_type IN ({})".format(",".join("?" for _ in filters.types))
        )
        params.extend([service._normalize_source_type(item) for item in filters.types])

    where_sql = " AND ".join(where) if where else "1=1"
    sql = (
        "SELECT u.unit_id, u.doc_id, u.unit_kind, u.level, u.node_id, u.group_id, u.text_ref, u.offsets_json, "
        "u.hit_count, u.last_hit_at, u.feedback_score, "
        "d.source_type, d.source_ref, d.scope, d.tags_json, d.created_at, COALESCE(f.title, d.title) AS title, "
        + (
            "bm25(retrievectl_units_fts) AS bm25_score "
            if service._fts_enabled
            else "0.0 AS bm25_score "
        )
        + joins
        + f"WHERE {where_sql} "
        + "ORDER BY bm25_score ASC, u.unit_id ASC LIMIT ?"
    )
    params.append(int(limit))
    return service.store.execute(sql, tuple(params)).fetchall()


def recent_rows(
    service: Any,
    *,
    allowed_scopes: list[str],
    filters: RetrievalFilters,
    limit: int,
) -> list[Mapping[str, Any]]:
    params: list[Any] = []
    where = ["1=1"]
    if allowed_scopes:
        where.append("d.scope IN ({})".format(",".join("?" for _ in allowed_scopes)))
        params.extend(allowed_scopes)
    scope_keys = _normalize_scope_keys(filters.scope_keys)
    if scope_keys:
        where.append("d.scope_key IN ({})".format(",".join("?" for _ in scope_keys)))
        params.extend(scope_keys)
    if filters.types:
        where.append(
            "d.source_type IN ({})".format(",".join("?" for _ in filters.types))
        )
        params.extend([service._normalize_source_type(item) for item in filters.types])

    sql = (
        "SELECT u.unit_id, u.doc_id, u.unit_kind, u.level, u.node_id, u.group_id, u.text_ref, u.offsets_json, "
        "u.hit_count, u.last_hit_at, u.feedback_score, "
        "d.source_type, d.source_ref, d.scope, d.tags_json, d.created_at, 1.0 AS bm25_score "
        "FROM retrievectl_units u "
        "JOIN retrievectl_docs d ON d.doc_id = u.doc_id "
        f"WHERE {' AND '.join(where)} "
        "ORDER BY d.created_at DESC, u.unit_id ASC LIMIT ?"
    )
    params.append(int(limit))
    return service.store.execute(sql, tuple(params)).fetchall()


def candidate_from_row(
    service: Any, row: Mapping[str, Any], inherited_score: float
) -> dict[str, Any]:
    tags = _safe_json_loads(str(row["tags_json"]), [])
    try:
        hit_count_raw = row["hit_count"]
    except Exception:
        hit_count_raw = None
    try:
        last_hit_raw = row["last_hit_at"]
    except Exception:
        last_hit_raw = None
    try:
        feedback_raw = row["feedback_score"]
    except Exception:
        feedback_raw = None
    return {
        "unit_id": str(row["unit_id"]),
        "doc_id": str(row["doc_id"]),
        "source_type": str(row["source_type"]),
        "source_ref": str(row["source_ref"]),
        "doc_scope": str(row["scope"]),
        "tags": [str(tag) for tag in tags] if isinstance(tags, list) else [],
        "created_at": str(row["created_at"]),
        "unit_kind": str(row["unit_kind"]),
        "level": str(row["level"] or "none"),
        "node_id": str(row["node_id"]) if row["node_id"] is not None else None,
        "group_id": str(row["group_id"]) if row["group_id"] is not None else None,
        "text_ref": str(row["text_ref"]),
        "offsets": _safe_json_loads(str(row["offsets_json"]), {}),
        "score": clamp01(inherited_score),
        "unified_score": clamp01(inherited_score),
        "bm25_score": clamp01(inherited_score),
        "type": _candidate_type(tags if isinstance(tags, list) else []),
        "confidence": float(service.config.defaults.confidence_default),
        "meta": {
            "hit_count": int(hit_count_raw or 0),
            "last_hit_at": str(last_hit_raw) if last_hit_raw else None,
            "feedback_score": clamp01(float(feedback_raw or 0.0)),
            "score_breakdown": {
                "relevance": clamp01(inherited_score),
                "recency": 0.0,
                "feedback": 0.0,
                "type_bonus": 0.0,
                "confidence": float(service.config.defaults.confidence_default),
                "outcome_utility": 0.5,
                "unified_score": clamp01(inherited_score),
            },
        },
        "why": "raptor_expand",
    }
