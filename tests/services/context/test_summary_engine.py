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


def test_merge_summary_preserves_oldest_and_newest_edges_after_repeated_overflow() -> (
    None
):
    engine = SessionSummaryEngine()
    merged = "OBJECTIVE-ALPHA"

    for index in range(80):
        merged = engine.merge_summary(
            current=merged,
            delta=f"progress-{index:03d} " + ("x" * 32),
            max_chars=800,
        )

    assert "OBJECTIVE-ALPHA" in merged
    assert "progress-079" in merged
    assert len(merged) <= 800


def test_summary_enrichment_cannot_replace_deterministic_edges() -> None:
    engine = SessionSummaryEngine()
    base = "OBJECTIVE-ALPHA\n" + ("middle\n" * 80) + "progress-079"

    merged = engine.merge_enrichment(
        deterministic_summary=base,
        enriched_summary="rewritten context without either anchor",
        max_chars=256,
    )

    assert merged.startswith("OBJECTIVE-ALPHA")
    assert merged.endswith("progress-079")
    assert "rewritten context" in merged
    assert len(merged) <= 256


def test_compaction_chunk_preserves_trailing_identifier_in_long_turn() -> None:
    engine = SessionSummaryEngine()

    result = engine.summarize_compaction_chunk(
        [
            SummaryTurn(
                role="user",
                text="start " + ("filler " * 50) + "TRAILING-ID-731",
            )
        ]
    )

    assert "start" in result.summary_text
    assert "TRAILING-ID-731" in result.summary_text


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


def test_render_summary_short_preserves_both_ends_of_long_turns() -> None:
    engine = SessionSummaryEngine()
    short = engine.render_summary_short(
        [
            SummaryTurn(
                role="user",
                text=(
                    "This is a long continuity setup with filler in the middle "
                    "and the durable marker is COBALT-731."
                ),
            )
        ],
        max_chars_per_turn=50,
    )

    assert "This is a long continui" in short
    assert "COBALT-731" in short
    assert len(short.removeprefix("user: ")) == 50
