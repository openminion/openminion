from __future__ import annotations

import dataclasses

import pytest

from openminion.modules.brain.runtime.recall.decision import RecallDecision


def _make_decision(**overrides):
    payload = dict(
        source="memory",
        confidence_threshold=0.6,
        freshness_cap_seconds=None,
        reason="use_memory_confident_fresh",
        observed_confidence=0.9,
        observed_age_seconds=None,
        record_id="r1",
    )
    payload.update(overrides)
    return RecallDecision(**payload)


def test_recall_decision_constructs_for_all_three_closed_sources():

    for source in ("memory", "context", "recompute"):
        d = _make_decision(source=source)
        assert d.source == source


def test_recall_decision_is_frozen():

    d = _make_decision()
    with pytest.raises(dataclasses.FrozenInstanceError):
        d.source = "recompute"  # type: ignore[misc]


def test_recall_decision_carries_threshold_and_observed_pair():

    d = _make_decision(confidence_threshold=0.7, observed_confidence=0.85)
    assert d.confidence_threshold == 0.7
    assert d.observed_confidence == 0.85


def test_recall_decision_freshness_cap_optional():

    d = _make_decision(freshness_cap_seconds=None)
    assert d.freshness_cap_seconds is None
