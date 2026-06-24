from __future__ import annotations

from datetime import datetime, timedelta, timezone

from openminion.modules.brain.runtime.recall.consultation import (
    consult_recall_decisions,
    stamp_recall_decision,
    summarize_decisions,
)


NOW = datetime(2026, 5, 26, 12, 0, 0, tzinfo=timezone.utc)


class _FakeRecord:
    def __init__(self, *, id="r", confidence=0.8, valid_to=None):
        self.id = id
        self.confidence = confidence
        self.valid_to = valid_to


class _FakeLogger:
    def __init__(self):
        self.events = []

    def log_canonical_event(self, *, event_type, payload):
        self.events.append((event_type, payload))


def test_empty_records_yields_single_context_decision():

    decisions = consult_recall_decisions([], now=NOW)
    assert len(decisions) == 1
    assert decisions[0].source == "context"


def test_consultation_emits_one_decision_per_record():

    records = [_FakeRecord(id="a", confidence=0.9), _FakeRecord(id="b", confidence=0.3)]
    decisions = consult_recall_decisions(records, now=NOW)
    assert len(decisions) == 2
    assert decisions[0].source == "memory"
    assert decisions[1].source == "recompute"


def test_stamp_recall_decision_emits_per_decision_telemetry():

    records = [_FakeRecord(confidence=0.9), _FakeRecord(confidence=0.3)]
    decisions = consult_recall_decisions(records, now=NOW)
    logger = _FakeLogger()
    stamp_recall_decision(decisions, logger=logger)
    assert len(logger.events) == 2
    assert all(e[0] == "rvrh_recall_decision" for e in logger.events)


def test_stamp_recall_decision_swallows_logger_failures():

    class _RaisingLogger:
        def log_canonical_event(self, *, event_type, payload):
            raise RuntimeError("boom")

    decisions = consult_recall_decisions([_FakeRecord(confidence=0.9)], now=NOW)
    # No raise expected
    stamp_recall_decision(decisions, logger=_RaisingLogger())


def test_summarize_decisions_aggregates_sources():

    past = (NOW - timedelta(hours=1)).isoformat()
    records = [
        _FakeRecord(confidence=0.9),
        _FakeRecord(confidence=0.9),
        _FakeRecord(confidence=0.3),
        _FakeRecord(confidence=0.9, valid_to=past),
    ]
    decisions = consult_recall_decisions(records, now=NOW)
    histogram = summarize_decisions(decisions)
    assert histogram == {"memory": 2, "context": 0, "recompute": 2}


def test_stamp_recall_decision_safe_with_none_logger():

    decisions = consult_recall_decisions([_FakeRecord(confidence=0.9)], now=NOW)
    stamp_recall_decision(decisions, logger=None)
