from __future__ import annotations

from datetime import datetime, timedelta, timezone

from openminion.modules.brain.runtime.recall.consultation import (
    consult_recall_decisions,
    stamp_recall_decision,
    summarize_decisions,
)
from openminion.modules.memory.models import MemoryRecord


class _SmokeLogger:
    def __init__(self):
        self.events = []

    def log_canonical_event(self, *, event_type, payload):
        self.events.append((event_type, payload))


NOW = datetime(2026, 5, 26, 12, 0, 0, tzinfo=timezone.utc)


def _make_real_record(
    *, record_id: str, confidence: float, valid_to: str | None = None
) -> MemoryRecord:
    created = NOW.isoformat()
    return MemoryRecord(
        id=record_id,
        scope="agent:smoke-agent",
        type="fact",
        content={"value": "smoke"},
        created_at=created,
        updated_at=created,
        confidence=confidence,
        valid_to=valid_to,
    )


def test_e2e_smoke_memory_query_to_decision_to_telemetry_to_summary():

    records = [
        _make_real_record(record_id="r-mem-1", confidence=0.92),
        _make_real_record(record_id="r-mem-2", confidence=0.75),
        _make_real_record(record_id="r-low", confidence=0.4),
        _make_real_record(
            record_id="r-stale",
            confidence=0.95,
            valid_to=(NOW - timedelta(hours=2)).isoformat(),
        ),
    ]

    decisions = consult_recall_decisions(records, now=NOW)
    assert len(decisions) == 4
    assert decisions[0].source == "memory"
    assert decisions[1].source == "memory"
    assert decisions[2].source == "recompute"
    assert decisions[2].reason == "recompute_low_confidence"
    assert decisions[3].source == "recompute"
    assert decisions[3].reason == "recompute_record_invalidated"

    logger = _SmokeLogger()
    stamp_recall_decision(decisions, logger=logger)
    assert len(logger.events) == 4
    assert all(e[0] == "rvrh_recall_decision" for e in logger.events)
    record_ids = {e[1].get("record_id") for e in logger.events}
    assert record_ids == {"r-mem-1", "r-mem-2", "r-low", "r-stale"}

    histogram = summarize_decisions(decisions)
    assert histogram == {"memory": 2, "context": 0, "recompute": 2}

    empty_decisions = consult_recall_decisions([], now=NOW)
    assert len(empty_decisions) == 1
    assert empty_decisions[0].source == "context"
    empty_logger = _SmokeLogger()
    stamp_recall_decision(empty_decisions, logger=empty_logger)
    assert len(empty_logger.events) == 1
    assert empty_logger.events[0][1]["source"] == "context"
