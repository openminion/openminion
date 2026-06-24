from __future__ import annotations

from datetime import datetime, timedelta, timezone

from openminion.modules.brain.runtime.recall.decision import (
    DEFAULT_CONFIDENCE_THRESHOLD,
    RECALL_REASON_FALLBACK_CONTEXT,
    RECALL_REASON_RECOMPUTE_INVALIDATED,
    RECALL_REASON_RECOMPUTE_LOW_CONFIDENCE,
    RECALL_REASON_USE_MEMORY,
    decision_telemetry_payload,
    resolve_recall_decision,
)


NOW = datetime(2026, 5, 26, 12, 0, 0, tzinfo=timezone.utc)


class _FakeRecord:
    def __init__(self, *, id="r1", confidence=0.8, valid_to=None):
        self.id = id
        self.confidence = confidence
        self.valid_to = valid_to


def test_none_record_yields_context_fallback():

    d = resolve_recall_decision(None, now=NOW)
    assert d.source == "context"
    assert d.reason == RECALL_REASON_FALLBACK_CONTEXT
    assert d.observed_confidence == 0.0
    assert d.observed_age_seconds is None


def test_high_confidence_fresh_record_yields_memory():

    d = resolve_recall_decision(_FakeRecord(confidence=0.9), now=NOW)
    assert d.source == "memory"
    assert d.reason == RECALL_REASON_USE_MEMORY
    assert d.observed_confidence == 0.9


def test_low_confidence_yields_recompute():

    d = resolve_recall_decision(_FakeRecord(confidence=0.3), now=NOW)
    assert d.source == "recompute"
    assert d.reason == RECALL_REASON_RECOMPUTE_LOW_CONFIDENCE


def test_invalidated_record_yields_recompute_even_with_high_confidence():

    past = (NOW - timedelta(hours=1)).isoformat()
    d = resolve_recall_decision(_FakeRecord(confidence=0.99, valid_to=past), now=NOW)
    assert d.source == "recompute"
    assert d.reason == RECALL_REASON_RECOMPUTE_INVALIDATED
    assert d.observed_age_seconds == 3600


def test_freshness_cap_triggers_stale_recompute():

    past_valid_to = (NOW - timedelta(seconds=60)).isoformat()
    d = resolve_recall_decision(
        _FakeRecord(confidence=0.9, valid_to=past_valid_to),
        now=NOW,
        freshness_cap_seconds=10,
    )
    # Invalidated takes priority over stale.
    assert d.source == "recompute"
    assert d.reason == RECALL_REASON_RECOMPUTE_INVALIDATED


def test_freshness_cap_with_no_valid_to_does_not_trigger_stale():

    d = resolve_recall_decision(
        _FakeRecord(confidence=0.9, valid_to=None),
        now=NOW,
        freshness_cap_seconds=1,
    )
    assert d.source == "memory"
    assert d.observed_age_seconds is None


def test_default_threshold_matches_shipped_retrieval_filter():

    assert DEFAULT_CONFIDENCE_THRESHOLD == 0.6


def test_telemetry_payload_round_trips_typed_fields():

    d = resolve_recall_decision(_FakeRecord(confidence=0.9, id="rec-xyz"), now=NOW)
    payload = decision_telemetry_payload(d)
    assert payload["source"] == "memory"
    assert payload["record_id"] == "rec-xyz"
    assert payload["confidence_threshold"] == DEFAULT_CONFIDENCE_THRESHOLD
    assert payload["observed_confidence"] == 0.9


def test_malformed_valid_to_falls_back_to_no_age():

    d = resolve_recall_decision(
        _FakeRecord(confidence=0.9, valid_to="not-an-iso-string"),
        now=NOW,
    )
    assert d.source == "memory"
    assert d.observed_age_seconds is None


def test_threshold_boundary_is_inclusive():

    d = resolve_recall_decision(_FakeRecord(confidence=0.6), now=NOW)
    assert d.source == "memory"
    d = resolve_recall_decision(_FakeRecord(confidence=0.59), now=NOW)
    assert d.source == "recompute"
