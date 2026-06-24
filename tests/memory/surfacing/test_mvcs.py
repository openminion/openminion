from __future__ import annotations

from openminion.modules.brain.runtime.recall.decision import (
    RecallDecision,
)
from openminion.modules.memory.surfacing import (
    CONFIDENCE_BAND_HIGH,
    CONFIDENCE_BAND_LOW,
    CONFIDENCE_BAND_MEDIUM,
    annotate_with_recall_source,
    confidence_band,
    format_records_with_confidence,
    render_record_with_confidence,
)
from openminion.modules.memory.surfacing.decision import render_with_decision


class _Record:
    def __init__(self, title="", content="", confidence=0.5):
        self.title = title
        self.content = content
        self.confidence = confidence


# --- audit (MVCS-01) regression — existing renderer is the no-confidence baseline ---


def test_existing_renderer_does_not_include_confidence():

    from openminion.services.agent.memory.extraction import (
        _format_records_as_context,
    )

    out = _format_records_as_context(
        [_Record(title="t", content="c", confidence=0.9)],
        header="[mem]",
        max_chars=400,
    )
    assert "confidence" not in out.lower()


# --- MVCS-02 surfacing layer ---


def test_confidence_band_classifies_into_three_buckets():
    assert confidence_band(0.95) == CONFIDENCE_BAND_HIGH
    assert confidence_band(0.6) == CONFIDENCE_BAND_MEDIUM
    assert confidence_band(0.2) == CONFIDENCE_BAND_LOW


def test_render_record_with_confidence_includes_band_prefix():
    out = render_record_with_confidence(_Record(title="t", content="c", confidence=0.9))
    assert out.startswith("[confidence=high]")
    assert "t: c" in out


def test_format_records_with_confidence_renders_each_record():
    records = [
        _Record(title="A", content="alpha", confidence=0.9),
        _Record(title="B", content="beta", confidence=0.3),
    ]
    out = format_records_with_confidence(records, header="[mem]")
    assert "[confidence=high]" in out
    assert "[confidence=low]" in out


def test_format_records_with_confidence_returns_empty_for_no_records():
    assert format_records_with_confidence([]) == ""


# --- MVCS-04 RVRH composition ---


def test_annotate_with_recall_source_prefixes_decision_label():
    rendered = "[confidence=high] x: y"
    decision = RecallDecision(
        source="memory",
        confidence_threshold=0.6,
        freshness_cap_seconds=None,
        reason="use_memory_confident_fresh",
    )
    out = annotate_with_recall_source(rendered, decision)
    assert out.startswith("[recall=memory]")
    assert "[confidence=high]" in out


def test_render_with_decision_composes_both_layers():
    decision = RecallDecision(
        source="recompute",
        confidence_threshold=0.6,
        freshness_cap_seconds=None,
        reason="recompute_low_confidence",
    )
    out = render_with_decision(
        _Record(title="t", content="c", confidence=0.3), decision
    )
    assert "[recall=recompute]" in out
    assert "[confidence=low]" in out
