"""Outcome-weighted scoring bridge for memory spans."""

from collections import defaultdict
from dataclasses import dataclass
from collections.abc import Sequence

from openminion.modules.memory.observability.span import MemorySpanRecord


@dataclass(frozen=True)
class SpanScoringInput:
    record_id: str
    read_count: int
    avg_relevance: float
    avg_freshness: float
    positive_outcomes: int
    negative_outcomes: int
    neutral_outcomes: int


_POSITIVE_OUTCOMES = {"success", "satisfied", "verified", "positive"}
_NEGATIVE_OUTCOMES = {"failure", "rejected", "stale", "negative"}


def aggregate_for_scoring(
    spans: Sequence[MemorySpanRecord],
) -> list[SpanScoringInput]:
    buckets: dict[str, list[MemorySpanRecord]] = defaultdict(list)
    for span in spans:
        if span.record_id:
            buckets[span.record_id].append(span)
    inputs: list[SpanScoringInput] = []
    for record_id, items in buckets.items():
        n = len(items)
        avg_rel = sum(s.relevance_score for s in items) / n
        avg_fresh = sum(s.freshness_at_read for s in items) / n
        pos = sum(1 for s in items if (s.outcome_tag or "") in _POSITIVE_OUTCOMES)
        neg = sum(1 for s in items if (s.outcome_tag or "") in _NEGATIVE_OUTCOMES)
        neutral = n - pos - neg
        inputs.append(
            SpanScoringInput(
                record_id=record_id,
                read_count=n,
                avg_relevance=avg_rel,
                avg_freshness=avg_fresh,
                positive_outcomes=pos,
                negative_outcomes=neg,
                neutral_outcomes=neutral,
            )
        )
    return inputs


__all__ = ["SpanScoringInput", "aggregate_for_scoring"]
