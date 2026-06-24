"""Typed span records for memory observability."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MemorySpanRecord:
    span_id: str
    record_id: str
    relevance_score: float
    freshness_at_read: float
    read_at: str
    outcome_tag: str | None = None
    scope: str = ""
    session_id: str = ""


@dataclass(frozen=True)
class StaleReadSignal:
    span_id: str
    record_id: str
    freshness_at_read: float
    threshold: float


def build_span_record(
    *,
    span_id: str,
    record_id: str,
    relevance_score: float,
    freshness_at_read: float,
    read_at: str,
    scope: str = "",
    session_id: str = "",
    outcome_tag: str | None = None,
) -> MemorySpanRecord:
    return MemorySpanRecord(
        span_id=str(span_id or "").strip(),
        record_id=str(record_id or "").strip(),
        relevance_score=float(relevance_score),
        freshness_at_read=float(freshness_at_read),
        read_at=str(read_at or "").strip(),
        scope=str(scope or "").strip(),
        session_id=str(session_id or "").strip(),
        outcome_tag=(str(outcome_tag).strip() if outcome_tag else None),
    )


def detect_stale_read(
    span: MemorySpanRecord, *, threshold: float
) -> StaleReadSignal | None:
    if span.freshness_at_read > float(threshold):
        return StaleReadSignal(
            span_id=span.span_id,
            record_id=span.record_id,
            freshness_at_read=span.freshness_at_read,
            threshold=float(threshold),
        )
    return None


def span_telemetry_payload(span: MemorySpanRecord) -> dict[str, Any]:
    return {
        "span_id": span.span_id,
        "record_id": span.record_id,
        "relevance_score": span.relevance_score,
        "freshness_at_read": span.freshness_at_read,
        "read_at": span.read_at,
        "scope": span.scope,
        "session_id": span.session_id,
        "outcome_tag": span.outcome_tag,
    }


__all__ = [
    "MemorySpanRecord",
    "StaleReadSignal",
    "build_span_record",
    "detect_stale_read",
    "span_telemetry_payload",
]
