from __future__ import annotations

from types import SimpleNamespace

from openminion.modules.brain.runtime.attribution import (
    AttributionAggregateRow,
    AttributionReadout,
    LearningAttributionEvent,
    aggregate_attribution_readout,
    project_records_to_learning_events,
)


def _record(
    record_id: str,
    *,
    retrieved_ids: list[str] | None = None,
    outcome_status: str = "",
    context_pack_version: str = "",
    context_recorded_at: str = "",
    trace_id: str = "",
    extra_meta: dict | None = None,
) -> SimpleNamespace:
    meta: dict = {}
    if retrieved_ids is not None:
        meta["source_outcome_record_ids"] = list(retrieved_ids)
    if outcome_status:
        meta["source_outcome_status"] = outcome_status
    if context_pack_version:
        meta["source_context_pack_version"] = context_pack_version
    if context_recorded_at:
        meta["source_context_recorded_at"] = context_recorded_at
    if trace_id:
        meta["source_trace_id"] = trace_id
    if extra_meta:
        meta.update(extra_meta)
    return SimpleNamespace(record_id=record_id, meta=meta)


# --- project_records_to_learning_events ------------------------------------


def test_projection_emits_one_event_per_retrieved_id() -> None:
    records = [
        _record(
            "outcome-1",
            retrieved_ids=["ret-a", "ret-b", "ret-c"],
            outcome_status="success",
            context_pack_version="pack-7",
            context_recorded_at="2026-05-13T10:00:00Z",
            trace_id="trace-1",
        ),
    ]
    events = project_records_to_learning_events(records)
    assert len(events) == 3
    assert [e.retrieved_record_id for e in events] == ["ret-a", "ret-b", "ret-c"]
    assert all(e.outcome_record_id == "outcome-1" for e in events)
    assert all(e.outcome_status == "success" for e in events)
    assert all(e.context_pack_version == "pack-7" for e in events)
    assert all(e.trace_id == "trace-1" for e in events)


def test_projection_skips_records_without_retrieved_ids() -> None:
    records = [
        _record("outcome-1", outcome_status="success"),
        _record("outcome-2", retrieved_ids=[], outcome_status="success"),
        _record(
            "outcome-3", retrieved_ids=["ret-a"], outcome_status="success"
        ),  # only this one emits
    ]
    events = project_records_to_learning_events(records)
    assert [e.outcome_record_id for e in events] == ["outcome-3"]


def test_projection_skips_records_without_record_id_or_meta() -> None:
    records = [
        SimpleNamespace(record_id="", meta={"source_outcome_record_ids": ["ret-a"]}),
        SimpleNamespace(record_id="outcome-1", meta=None),
        SimpleNamespace(record_id="outcome-2", meta="not-a-mapping"),
        # Only this one emits:
        _record("outcome-3", retrieved_ids=["ret-a"]),
    ]
    events = project_records_to_learning_events(records)
    assert [e.outcome_record_id for e in events] == ["outcome-3"]


def test_projection_accepts_both_attribute_records_and_mappings() -> None:
    records = [
        # Attribute-shaped (e.g. SimpleNamespace, dataclass, pydantic).
        _record("outcome-attr", retrieved_ids=["ret-a"]),
        # Mapping-shaped (e.g. raw fixture dict).
        {
            "record_id": "outcome-map",
            "meta": {"source_outcome_record_ids": ["ret-b"]},
        },
    ]
    events = project_records_to_learning_events(records)
    assert [e.outcome_record_id for e in events] == ["outcome-attr", "outcome-map"]


def test_projection_drops_blank_retrieved_ids_in_the_list() -> None:
    records = [
        _record(
            "outcome-1",
            retrieved_ids=["ret-a", "", "   ", None, "ret-b"],  # type: ignore[list-item]
            outcome_status="success",
        ),
    ]
    events = project_records_to_learning_events(records)
    assert [e.retrieved_record_id for e in events] == ["ret-a", "ret-b"]


def test_projection_is_deterministic_and_idempotent() -> None:
    records = [
        _record("outcome-1", retrieved_ids=["ret-a"], outcome_status="success"),
        _record(
            "outcome-2", retrieved_ids=["ret-a", "ret-b"], outcome_status="failure"
        ),
    ]
    first = project_records_to_learning_events(records)
    second = project_records_to_learning_events(records)
    assert [e.model_dump() for e in first] == [e.model_dump() for e in second]


# --- aggregate_attribution_readout -----------------------------------------


def test_aggregate_groups_by_retrieved_record_with_bucket_counts() -> None:
    events = [
        LearningAttributionEvent(
            retrieved_record_id="ret-a",
            outcome_record_id="outcome-1",
            outcome_status="success",
            context_recorded_at="2026-05-13T10:00:00Z",
            trace_id="trace-1",
        ),
        LearningAttributionEvent(
            retrieved_record_id="ret-a",
            outcome_record_id="outcome-2",
            outcome_status="failure",
            context_recorded_at="2026-05-13T11:00:00Z",
            trace_id="trace-2",
        ),
        LearningAttributionEvent(
            retrieved_record_id="ret-b",
            outcome_record_id="outcome-3",
            outcome_status="success",
            context_recorded_at="2026-05-13T12:00:00Z",
            trace_id="trace-1",
        ),
    ]
    readout = aggregate_attribution_readout(events)
    assert readout.total_events_scanned == 3
    assert readout.distinct_retrieved_records == 2
    # ret-a has more events so should sort first.
    assert [r.retrieved_record_id for r in readout.rows] == ["ret-a", "ret-b"]
    row_a = readout.rows[0]
    assert row_a.total_events == 2
    assert row_a.by_outcome_status == {"success": 1, "failure": 1, "other": 0}
    assert row_a.distinct_traces == 2
    assert row_a.earliest_event_at == "2026-05-13T10:00:00Z"
    assert row_a.latest_event_at == "2026-05-13T11:00:00Z"


def test_aggregate_arbitrary_status_falls_into_other_bucket() -> None:
    events = [
        LearningAttributionEvent(
            retrieved_record_id="ret-a",
            outcome_record_id="outcome-1",
            outcome_status="success",
        ),
        LearningAttributionEvent(
            retrieved_record_id="ret-a",
            outcome_record_id="outcome-2",
            outcome_status="partial-ish",  # not in {success, failure}
        ),
        LearningAttributionEvent(
            retrieved_record_id="ret-a",
            outcome_record_id="outcome-3",
            outcome_status="",  # blank → other
        ),
    ]
    readout = aggregate_attribution_readout(events)
    row = readout.rows[0]
    assert set(row.by_outcome_status.keys()) == {"success", "failure", "other"}
    assert row.by_outcome_status == {"success": 1, "failure": 0, "other": 2}


def test_aggregate_empty_events_returns_empty_readout() -> None:
    readout = aggregate_attribution_readout([])
    assert readout.rows == []
    assert readout.total_events_scanned == 0
    assert readout.distinct_retrieved_records == 0


def test_aggregate_passes_evidence_window_through_unchanged() -> None:
    window = {"record_types": ["procedure", "tool_habit"], "limit": 200}
    readout = aggregate_attribution_readout([], evidence_window=window)
    assert readout.evidence_window == window


def test_aggregate_sort_is_descending_by_count_then_alphabetical() -> None:
    events = [
        LearningAttributionEvent(retrieved_record_id="ret-z", outcome_record_id="o-1"),
        LearningAttributionEvent(retrieved_record_id="ret-a", outcome_record_id="o-2"),
        LearningAttributionEvent(retrieved_record_id="ret-a", outcome_record_id="o-3"),
        LearningAttributionEvent(retrieved_record_id="ret-m", outcome_record_id="o-4"),
    ]
    readout = aggregate_attribution_readout(events)
    # ret-a has 2 events (most), then ret-m and ret-z tied at 1 each →
    # alphabetical for stability.
    assert [r.retrieved_record_id for r in readout.rows] == ["ret-a", "ret-m", "ret-z"]


def test_aggregate_distinct_traces_dedups_blank_trace_ids() -> None:
    events = [
        LearningAttributionEvent(
            retrieved_record_id="ret-a", outcome_record_id="o-1", trace_id="trace-1"
        ),
        LearningAttributionEvent(
            retrieved_record_id="ret-a", outcome_record_id="o-2", trace_id="trace-1"
        ),
        LearningAttributionEvent(
            retrieved_record_id="ret-a", outcome_record_id="o-3", trace_id=""
        ),
    ]
    readout = aggregate_attribution_readout(events)
    # Two events share trace-1; one event has no trace id → 1 distinct
    # trace, not 2.
    assert readout.rows[0].distinct_traces == 1


# --- Anti-LLM discipline regressions --------------------------------------


def test_schemas_do_not_expose_learning_happened_or_causal_fields() -> None:
    forbidden_substrings = (
        "learning_happened",
        "causal_influence",
        "causal_score",
        "improvement_score",
        "learned",
        "helpful",
        "useful_score",
    )
    schema_fields = (
        set(LearningAttributionEvent.model_fields.keys())
        | set(AttributionAggregateRow.model_fields.keys())
        | set(AttributionReadout.model_fields.keys())
    )
    for field_name in schema_fields:
        for forbidden in forbidden_substrings:
            assert forbidden not in field_name, (
                f"CSLC discipline violation: schema field {field_name!r} "
                f"contains forbidden substring {forbidden!r}. The readout "
                "reports structural co-occurrence; it must not claim "
                "learning or causal influence."
            )


def test_aggregate_bucket_set_is_closed() -> None:
    events = [
        LearningAttributionEvent(
            retrieved_record_id="ret-a",
            outcome_record_id="o-1",
            outcome_status=status,
        )
        for status in ("success", "failure", "", "weird", "skipped", "partial")
    ]
    readout = aggregate_attribution_readout(events)
    assert set(readout.rows[0].by_outcome_status.keys()) == {
        "success",
        "failure",
        "other",
    }


def test_no_synthesis_when_provenance_is_partial() -> None:
    records = [
        _record("outcome-1", retrieved_ids=["ret-a"]),  # only retrieved_ids set
    ]
    events = project_records_to_learning_events(records)
    assert len(events) == 1
    event = events[0]
    assert event.retrieved_record_id == "ret-a"
    assert event.outcome_record_id == "outcome-1"
    assert event.outcome_status == ""
    assert event.context_pack_version == ""
    assert event.trace_id == ""
