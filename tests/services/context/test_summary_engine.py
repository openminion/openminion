from __future__ import annotations

from openminion.modules.context.summary.engine import (
    SessionSummaryEngine,
    SummaryTurn,
)


def test_summarize_compaction_chunk_normalizes_roles() -> None:
    engine = SessionSummaryEngine()
    result = engine.summarize_compaction_chunk(
        [
            SummaryTurn(role="inbound", text="Hello   there"),
            SummaryTurn(role="outbound", text="Hi"),
        ]
    )
    assert result.source_turn_count == 2
    assert "- user: Hello there" in result.summary_text
    assert "- assistant: Hi" in result.summary_text


def test_merge_summary_dedupes_and_respects_max_chars() -> None:
    engine = SessionSummaryEngine()
    merged = engine.merge_summary(
        current="- user: alpha\n- assistant: beta",
        delta="- user: alpha\n- assistant: gamma",
        max_chars=10_000,
    )
    assert merged.count("- user: alpha") == 1
    assert "- assistant: gamma" in merged


def test_render_summary_short_and_long_use_recent_window() -> None:
    engine = SessionSummaryEngine()
    turns = [
        SummaryTurn(role="user", text="one"),
        SummaryTurn(role="assistant", text="two"),
        SummaryTurn(role="user", text="three"),
        SummaryTurn(role="assistant", text="four"),
    ]
    short = engine.render_summary_short(turns, recent_limit=3, max_chars_per_turn=50)
    long = engine.render_summary_long(turns, recent_limit=3)
    assert "user: one" not in short
    assert "assistant: two" in short
    assert "user: one" not in long
    assert "assistant: two" in long
