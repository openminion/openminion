from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from openminion.modules.brain.adapters.memory import MemctlAdapter
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
from openminion.modules.memory.surfacing.evidence import MemoryContextSpan
from openminion.modules.memory.runtime.extraction.records import (
    _format_records_as_context,
)


class _Record:
    def __init__(self, title="", content="", confidence=0.5):
        self.title = title
        self.content = content
        self.confidence = confidence


# --- audit (MVCS-01) regression — existing renderer is the no-confidence baseline ---


def test_existing_renderer_does_not_include_confidence():

    out = _format_records_as_context(
        [_Record(title="t", content="c", confidence=0.9)],
        header="[mem]",
        max_chars=400,
    )
    assert "confidence" not in out.lower()


def test_existing_renderer_keeps_records_atomic_at_budget_boundary():
    out = _format_records_as_context(
        [
            _Record(title="first", content="short"),
            _Record(title="second", content="x" * 200),
            _Record(title="third", content="also short"),
        ],
        header="[mem]",
        max_chars=80,
    )

    assert "first: short" in out
    assert "third: also short" in out
    assert "second:" not in out
    assert len(out) <= 80


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


@pytest.mark.parametrize("fallback", [False, True])
def test_capsule_spans_follow_rendered_rows_without_extra_retrieval(fallback):
    records = [
        SimpleNamespace(id=record_id, content=content)
        for record_id, content in (
            ("first", "  same literal  "),
            ("second", "same literal"),
            ("multiline", "snowman \u2603\ncontinued"),
            ("empty", " \n "),
            ("", "anonymous"),
            ("last", {"text": "last fact"}),
            ("unselected", "not selected"),
        )
    ]
    backend = SimpleNamespace(
        search=Mock(
            side_effect=lambda options: [] if fallback else records[: options.limit]
        ),
        list=Mock(side_effect=lambda options: records[: options.limit]),
    )
    adapter = MemctlAdapter(backend, agent_id="agent-a")

    text, meta = adapter.build_context_with_metadata(
        session_id="session-a", user_message="query"
    )

    prefix = "Agent canonical memory (cross-session):\n\nRelevant facts:"
    lines = [
        "- same literal",
        "- same literal",
        "- snowman \u2603\ncontinued",
        "- anonymous",
        "- last fact",
    ]
    assert text == prefix + "\n" + "\n".join(lines)
    expected = []
    start = len(prefix) + 1
    for record_id, line in zip(("first", "second", "multiline", "", "last"), lines):
        if record_id:
            expected.append(
                {"record_id": record_id, "start": start, "end": start + len(line)}
            )
        start += len(line) + 1
    assert json.loads(meta["memory_context_spans"]) == expected
    assert all(isinstance(value, str) for value in meta.values())
    backend.search.assert_called_once()
    options = backend.search.call_args.args[0]
    assert (options.query, options.limit, options.types) == ("query", 6, ["fact"])
    assert options.scopes == ["session:session-a", "agent:agent-a"]
    assert backend.list.call_count == int(fallback)
    if fallback:
        assert backend.list.call_args.args[0].limit == 6
    assert adapter.build_context(session_id="session-a", user_message="query") == text


@pytest.mark.parametrize("facts", [[], [{"record_id": "empty", "text": " \n "}]])
def test_capsule_empty_and_header_only_have_no_spans(monkeypatch, facts):
    adapter = MemctlAdapter(SimpleNamespace())
    monkeypatch.setattr(adapter, "query_facts", Mock(return_value=facts))

    text, meta = adapter.build_context_with_metadata(session_id="s", user_message="")

    expected = (
        "Agent canonical memory (cross-session):\n\nRelevant facts:" if facts else ""
    )
    assert text == expected
    assert meta["memory_context_spans"] == "[]"


def test_capsule_spans_preserve_existing_unbounded_rendering():
    content = "long fact " * 200
    backend = SimpleNamespace(
        search=Mock(return_value=[SimpleNamespace(id="long", content=content)])
    )
    adapter = MemctlAdapter(backend)

    text, meta = adapter.build_context_with_metadata(
        session_id="s", user_message="long"
    )

    prefix = "Agent canonical memory (cross-session):\n\nRelevant facts:\n"
    assert text == prefix + "- " + content.strip()
    assert len(text) > 1600
    assert meta["memory_envelope_truncated"] == "false"
    assert json.loads(meta["memory_context_spans"]) == [
        {"record_id": "long", "start": len(prefix), "end": len(text)}
    ]


@pytest.mark.parametrize(("start", "end"), [(-1, 1), (0, 0), (2, 1), (1, 1)])
def test_memory_context_span_rejects_invalid_bounds(start, end):
    with pytest.raises(ValidationError):
        MemoryContextSpan(record_id="record", start=start, end=end)


def test_memory_context_span_serializes_character_bounds():
    assert MemoryContextSpan(record_id="record", start=0, end=1).model_dump() == {
        "record_id": "record",
        "start": 0,
        "end": 1,
    }


def test_record_spans_exclude_budget_drops_and_empty_content():
    records = [
        SimpleNamespace(id=record_id, title=title, content=content)
        for record_id, title, content in (
            ("first", "first", "short"),
            ("dropped", "second", "x" * 200),
            ("empty", "", ""),
            ("third", "third", "also short"),
        )
    ]
    spans: list[MemoryContextSpan] = []
    text = _format_records_as_context(
        records, header="[mem]", max_chars=80, spans=spans
    )
    assert text == "[mem]\n  • [key=] first: short\n  • \n  • [key=] third: also short"
    assert [(span.record_id, text[span.start : span.end]) for span in spans] == [
        ("first", "  • [key=] first: short"),
        ("third", "  • [key=] third: also short"),
    ]
    assert _format_records_as_context(records, header="[mem]", max_chars=80) == text
