"""Aggregate retrieved-record to later-outcome attribution events."""

from collections.abc import Iterable, Mapping
from typing import Any

from openminion.base.constants import STATE_KEY_SOURCE_OUTCOME
from pydantic import BaseModel, ConfigDict, Field

_OUTCOME_STATUS_BUCKETS = ("success", "failure", "other")


class LearningAttributionEvent(BaseModel):
    """One typed retrieved-record → later-outcome attribution event."""

    model_config = ConfigDict(extra="forbid")

    retrieved_record_id: str
    outcome_record_id: str
    outcome_status: str = ""
    context_pack_version: str = ""
    context_recorded_at: str = ""
    trace_id: str = ""


class AttributionAggregateRow(BaseModel):
    """Per-retrieved-record aggregate co-occurrence row."""

    model_config = ConfigDict(extra="forbid")

    retrieved_record_id: str
    total_events: int = Field(default=0, ge=0)
    by_outcome_status: dict[str, int] = Field(default_factory=dict)
    distinct_traces: int = Field(default=0, ge=0)
    earliest_event_at: str = ""
    latest_event_at: str = ""


class AttributionReadout(BaseModel):
    """Operator-facing readout of learning-attribution aggregates."""

    model_config = ConfigDict(extra="forbid")

    rows: list[AttributionAggregateRow] = Field(default_factory=list)
    total_events_scanned: int = Field(default=0, ge=0)
    distinct_retrieved_records: int = Field(default=0, ge=0)
    evidence_window: dict[str, Any] = Field(default_factory=dict)


def _record_meta_and_id(record: Any) -> tuple[Mapping[str, Any] | None, str]:
    if isinstance(record, Mapping):
        meta_raw = record.get("meta")
        record_id = str(record.get("record_id") or "").strip()
    else:
        meta_raw = getattr(record, "meta", None)
        record_id = str(getattr(record, "record_id", "") or "").strip()
    meta: Mapping[str, Any] | None = meta_raw if isinstance(meta_raw, Mapping) else None
    return meta, record_id


def _coerce_str_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    out: list[str] = []
    for item in value:
        token = str(item or "").strip()
        if token:
            out.append(token)
    return out


def project_records_to_learning_events(
    records: Iterable[Any],
) -> list[LearningAttributionEvent]:
    """Project typed memory records into attribution events."""
    events: list[LearningAttributionEvent] = []
    for record in records or []:
        meta, outcome_record_id = _record_meta_and_id(record)
        if not outcome_record_id or meta is None:
            continue
        retrieved_ids = _coerce_str_list(meta.get("source_outcome_record_ids"))
        if not retrieved_ids:
            continue
        outcome_status = str(meta.get(STATE_KEY_SOURCE_OUTCOME) or "").strip()
        context_pack_version = str(
            meta.get("source_context_pack_version") or ""
        ).strip()
        context_recorded_at = str(meta.get("source_context_recorded_at") or "").strip()
        trace_id = str(meta.get("source_trace_id") or "").strip()
        for retrieved_id in retrieved_ids:
            events.append(
                LearningAttributionEvent(
                    retrieved_record_id=retrieved_id,
                    outcome_record_id=outcome_record_id,
                    outcome_status=outcome_status,
                    context_pack_version=context_pack_version,
                    context_recorded_at=context_recorded_at,
                    trace_id=trace_id,
                )
            )
    return events


def _outcome_status_bucket(status: str) -> str:
    """Map a raw outcome status to one of the bucket keys."""
    if status == "success":
        return "success"
    if status == "failure":
        return "failure"
    return "other"


def aggregate_attribution_readout(
    events: Iterable[LearningAttributionEvent],
    *,
    evidence_window: Mapping[str, Any] | None = None,
) -> AttributionReadout:
    """Aggregate typed learning events into per-record rows."""
    materialized = list(events)
    by_retrieved: dict[str, list[LearningAttributionEvent]] = {}
    for ev in materialized:
        by_retrieved.setdefault(ev.retrieved_record_id, []).append(ev)

    rows: list[AttributionAggregateRow] = []
    for retrieved_id, group in by_retrieved.items():
        bucket_counts: dict[str, int] = {key: 0 for key in _OUTCOME_STATUS_BUCKETS}
        for ev in group:
            bucket_counts[_outcome_status_bucket(ev.outcome_status)] += 1
        traces = {ev.trace_id for ev in group if ev.trace_id}
        timestamps = sorted(
            ev.context_recorded_at for ev in group if ev.context_recorded_at
        )
        rows.append(
            AttributionAggregateRow(
                retrieved_record_id=retrieved_id,
                total_events=len(group),
                by_outcome_status=bucket_counts,
                distinct_traces=len(traces),
                earliest_event_at=timestamps[0] if timestamps else "",
                latest_event_at=timestamps[-1] if timestamps else "",
            )
        )

    rows.sort(key=lambda row: (-row.total_events, row.retrieved_record_id))

    return AttributionReadout(
        rows=rows,
        total_events_scanned=len(materialized),
        distinct_retrieved_records=len(by_retrieved),
        evidence_window=dict(evidence_window or {}),
    )


__all__ = [
    "LearningAttributionEvent",
    "AttributionAggregateRow",
    "AttributionReadout",
    "project_records_to_learning_events",
    "aggregate_attribution_readout",
]
