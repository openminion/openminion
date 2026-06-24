import warnings

from ..models import MemoryRecord
from .scorer import score_records


_MODE_RESPOND = "respond"
_MODE_ACT = "act"
_MODE_PLAN = "plan"
_TYPE_PRIORITY_BY_MODE = {
    _MODE_PLAN: {"procedure": 0, "task": 1, "decision": 2, "summary": 3},
    _MODE_RESPOND: {"fact": 0, "summary": 1, "pin": 2, "decision": 3},
    _MODE_ACT: {"pin": 0, "procedure": 1, "task": 2, "decision": 3},
}


def normalize_mode_name(mode_name: str | None) -> str | None:
    normalized = str(mode_name or "").strip().lower()
    return normalized if normalized in _TYPE_PRIORITY_BY_MODE else None


def _record_unified_score(record: MemoryRecord) -> float:
    return float(
        getattr(record, "meta", {}).get("score_breakdown", {}).get("unified_score", 0.0)
    )


def rank_records_for_mode(
    results: list[tuple[MemoryRecord, float]],
    *,
    mode_name: str | None = None,
    use_unified_scorer: bool = True,
) -> list[tuple[MemoryRecord, float]]:
    warnings.warn(
        "openminion.modules.memory.ranking.rank_records_for_mode() is deprecated; "
        "use unified scoring instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    if use_unified_scorer:
        scored_records = score_records(
            [record for record, _ in results],
            query_bm25_scores=[score for _record, score in results],
        )
        return [(record, _record_unified_score(record)) for record in scored_records]

    normalized_mode = normalize_mode_name(mode_name)
    if normalized_mode is None:
        return rank_search_results_with_scores(results)

    type_priority = _TYPE_PRIORITY_BY_MODE[normalized_mode]

    return sorted(
        results,
        key=lambda item: (
            type_priority.get(item[0].type, 99),
            item[1],
            -item[0].confidence,
            item[0].updated_at,
        ),
    )


def rank_search_results_with_scores(
    results: list[tuple[MemoryRecord, float]],
) -> list[tuple[MemoryRecord, float]]:
    """Sort FTS results and preserve paired score values."""
    warnings.warn(
        "openminion.modules.memory.ranking.rank_search_results_with_scores() "
        "is deprecated; use the storage-local FTS pair sorter instead.",
        DeprecationWarning,
        stacklevel=2,
    )

    return sorted(
        results,
        key=lambda item: (item[1], -item[0].confidence, item[0].updated_at),
    )


def rank_search_results(
    results: list[tuple[MemoryRecord, float]],
) -> list[MemoryRecord]:
    """Sort FTS search results deterministically by score, confidence, and recency."""
    sorted_items = rank_search_results_with_scores(results)
    return [item[0] for item in sorted_items]
