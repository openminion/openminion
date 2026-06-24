from __future__ import annotations

import pytest

from openminion.modules.memory.observability import (
    MemorySpanRecord,
    SpanEmitter,
    SpanReadContext,
    apply_outcome_tag,
    backref_outcome_to_spans,
    build_span_record,
    detect_stale_read,
    record_span_read,
    span_telemetry_payload,
)
from openminion.modules.memory.observability.scoring import (
    aggregate_for_scoring,
)


class _Logger:
    def __init__(self):
        self.events = []

    def log_canonical_event(self, *, event_type, payload):
        self.events.append((event_type, payload))


def _make_ctx(record_id="rec-1", rel=0.8, fresh=0.9, scope="agent:test", sess="s1"):
    return SpanReadContext(
        record_id=record_id,
        relevance_score=rel,
        freshness_at_read=fresh,
        scope=scope,
        session_id=sess,
    )


# --- MSPO-01 schema ---


def test_span_record_is_frozen():
    span = build_span_record(
        span_id="sp-1",
        record_id="r-1",
        relevance_score=0.5,
        freshness_at_read=0.5,
        read_at="2026-05-26T00:00:00Z",
    )
    with pytest.raises(Exception):
        span.outcome_tag = "x"  # type: ignore[misc]


def test_build_span_record_normalizes_numeric_fields():
    span = build_span_record(
        span_id="sp",
        record_id="r",
        relevance_score="0.5",  # type: ignore[arg-type]
        freshness_at_read="0.7",  # type: ignore[arg-type]
        read_at="2026",
    )
    assert span.relevance_score == 0.5
    assert span.freshness_at_read == 0.7


def test_build_span_record_strips_text_fields():
    span = build_span_record(
        span_id="  sp  ",
        record_id="  r  ",
        relevance_score=0.5,
        freshness_at_read=0.5,
        read_at="  2026  ",
        scope="  agent:x  ",
        session_id="  s  ",
    )
    assert span.span_id == "sp"
    assert span.record_id == "r"
    assert span.scope == "agent:x"


def test_span_telemetry_payload_round_trips_fields():
    span = build_span_record(
        span_id="sp",
        record_id="r",
        relevance_score=0.5,
        freshness_at_read=0.5,
        read_at="2026",
        outcome_tag="success",
    )
    payload = span_telemetry_payload(span)
    assert payload["span_id"] == "sp"
    assert payload["outcome_tag"] == "success"


# --- MSPO-02 emitter ---


def test_emitter_records_default_emits_every_read():
    emitter = SpanEmitter()
    emitter.record(_make_ctx())
    emitter.record(_make_ctx(record_id="rec-2"))
    assert len(emitter.spans) == 2
    assert {s.record_id for s in emitter.spans} == {"rec-1", "rec-2"}


def test_emitter_stamps_telemetry_event_when_logger_present():
    logger = _Logger()
    emitter = SpanEmitter(logger=logger)
    emitter.record(_make_ctx())
    assert len(logger.events) == 1
    assert logger.events[0][0] == "mspo_memory_span_read"


def test_emitter_swallows_logger_failures():
    class _Bad:
        def log_canonical_event(self, *, event_type, payload):
            raise RuntimeError("boom")

    emitter = SpanEmitter(logger=_Bad())
    # No raise expected
    span = emitter.record(_make_ctx())
    assert isinstance(span, MemorySpanRecord)


def test_emitter_query_helpers_by_record_and_session():
    emitter = SpanEmitter()
    emitter.record(_make_ctx(record_id="A", sess="s1"))
    emitter.record(_make_ctx(record_id="A", sess="s2"))
    emitter.record(_make_ctx(record_id="B", sess="s1"))
    assert len(emitter.by_record("A")) == 2
    assert len(emitter.by_session("s1")) == 2


def test_record_span_read_functional_wrapper():
    emitter = SpanEmitter()
    record_span_read(emitter, _make_ctx())
    assert len(emitter.spans) == 1


# --- MSPO-03 outcome-tag backref ---


def test_apply_outcome_tag_returns_new_span_with_tag():
    span = build_span_record(
        span_id="sp",
        record_id="r",
        relevance_score=0.5,
        freshness_at_read=0.5,
        read_at="2026",
    )
    tagged = apply_outcome_tag(span, outcome_tag="success")
    assert tagged.outcome_tag == "success"
    assert span.outcome_tag is None  # original unchanged


def test_backref_stamps_only_target_record_ids():
    emitter = SpanEmitter()
    emitter.record(_make_ctx(record_id="A"))
    emitter.record(_make_ctx(record_id="B"))
    tagged = backref_outcome_to_spans(
        emitter.spans, outcome_tag="success", record_ids={"A"}
    )
    by_record = {s.record_id: s for s in tagged}
    assert by_record["A"].outcome_tag == "success"
    assert by_record["B"].outcome_tag is None


def test_backref_stamps_all_when_record_ids_is_none():
    emitter = SpanEmitter()
    emitter.record(_make_ctx(record_id="A"))
    emitter.record(_make_ctx(record_id="B"))
    tagged = backref_outcome_to_spans(emitter.spans, outcome_tag="positive")
    assert all(s.outcome_tag == "positive" for s in tagged)


# --- MSPO-04 stale-read detector ---


def test_detect_stale_read_returns_signal_above_threshold():
    span = build_span_record(
        span_id="sp",
        record_id="r",
        relevance_score=0.5,
        freshness_at_read=0.9,
        read_at="2026",
    )
    signal = detect_stale_read(span, threshold=0.5)
    assert signal is not None
    assert signal.span_id == "sp"
    assert signal.freshness_at_read == 0.9


def test_detect_stale_read_returns_none_at_or_below_threshold():
    span = build_span_record(
        span_id="sp",
        record_id="r",
        relevance_score=0.5,
        freshness_at_read=0.5,
        read_at="2026",
    )
    assert detect_stale_read(span, threshold=0.5) is None


# --- MSPO-05 scoring-integration ---


def test_aggregate_for_scoring_buckets_by_record_id():
    emitter = SpanEmitter()
    emitter.record(_make_ctx(record_id="A", rel=0.5))
    emitter.record(_make_ctx(record_id="A", rel=0.7))
    emitter.record(_make_ctx(record_id="B", rel=0.9))
    tagged = backref_outcome_to_spans(emitter.spans, outcome_tag="success")
    inputs = {i.record_id: i for i in aggregate_for_scoring(tagged)}
    assert inputs["A"].read_count == 2
    assert abs(inputs["A"].avg_relevance - 0.6) < 1e-9
    assert inputs["A"].positive_outcomes == 2
    assert inputs["B"].positive_outcomes == 1


def test_aggregate_categorizes_outcomes_correctly():
    emitter = SpanEmitter()
    emitter.record(_make_ctx(record_id="A"))
    emitter.record(_make_ctx(record_id="B"))
    emitter.record(_make_ctx(record_id="C"))
    spans = list(emitter.spans)
    spans[0] = apply_outcome_tag(spans[0], outcome_tag="success")
    spans[1] = apply_outcome_tag(spans[1], outcome_tag="failure")
    # spans[2] left untagged (neutral)
    inputs = {i.record_id: i for i in aggregate_for_scoring(spans)}
    assert inputs["A"].positive_outcomes == 1
    assert inputs["B"].negative_outcomes == 1
    assert inputs["C"].neutral_outcomes == 1


# --- MSPO-07 E2E smoke ---


def test_e2e_smoke_read_emit_backref_score_audit():
    logger = _Logger()
    emitter = SpanEmitter(logger=logger)

    # Surface 1: memory reads emit spans
    emitter.record(_make_ctx(record_id="r-1", rel=0.9, fresh=0.9))
    emitter.record(_make_ctx(record_id="r-2", rel=0.4, fresh=0.7))

    assert len(emitter.spans) == 2
    assert len(logger.events) == 2

    # Surface 2: stale-read detector flags r-1 if threshold low
    stale = [detect_stale_read(s, threshold=0.5) for s in emitter.spans]
    flagged = [s for s in stale if s is not None]
    assert len(flagged) == 2

    # Surface 3: outcome back-reference
    rewritten = backref_outcome_to_spans(
        emitter.spans, outcome_tag="success", record_ids={"r-1"}
    )
    tagged = {s.record_id: s.outcome_tag for s in rewritten}
    assert tagged["r-1"] == "success"
    assert tagged["r-2"] is None

    # Surface 4: scoring aggregate
    inputs = {i.record_id: i for i in aggregate_for_scoring(rewritten)}
    assert inputs["r-1"].positive_outcomes == 1
    assert inputs["r-2"].positive_outcomes == 0

    # Surface 5: telemetry-audit was stamped per read
    assert all(e[0] == "mspo_memory_span_read" for e in logger.events)
