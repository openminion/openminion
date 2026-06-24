from __future__ import annotations

from openminion.cli.status import (
    TokenUsageSnapshot,
    TokenUsageTotals,
    accumulate_usage,
    format_elapsed_duration,
    format_context_window,
    format_relative_age,
    format_token_count,
    format_token_usage_summary,
    usage_totals_from_mapping,
)


def test_usage_totals_from_mapping_accepts_final_and_live_keys() -> None:
    final_usage = usage_totals_from_mapping(
        {
            "total_input_tokens_used": "1200",
            "total_output_tokens_used": "300",
            "total_tokens_used": "1500",
        }
    )
    live_usage = usage_totals_from_mapping(
        {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
        }
    )

    assert final_usage == TokenUsageTotals(1200, 300, 1500)
    assert live_usage == TokenUsageTotals(10, 5, 15)


def test_usage_totals_from_mapping_ignores_non_numeric_values() -> None:
    usage = usage_totals_from_mapping(
        {
            "prompt_tokens": "12",
            "completion_tokens": object(),
            "total_tokens": "bad",
        }
    )

    assert usage == TokenUsageTotals(12, None, 12)


def test_accumulate_usage_sums_known_values_without_fabricating_unknowns() -> None:
    previous = TokenUsageTotals(
        prompt_tokens=100, completion_tokens=None, total_tokens=100
    )
    increment = TokenUsageTotals(prompt_tokens=20, completion_tokens=7, total_tokens=27)

    combined = accumulate_usage(previous, increment)

    assert combined == TokenUsageTotals(
        prompt_tokens=120,
        completion_tokens=7,
        total_tokens=127,
    )


def test_format_token_usage_summary_uses_dash_for_unknown() -> None:
    snapshot = TokenUsageSnapshot(
        turn_total_tokens=1500,
        session_total_tokens=4500,
        context_limit_tokens=200000,
    )

    summary = format_token_usage_summary(snapshot)

    assert "turn 1.5k" in summary
    assert "session 4.5k" in summary
    assert "ctx —" in summary


def test_format_token_usage_summary_appends_timing_when_available() -> None:
    snapshot = TokenUsageSnapshot(
        turn_total_tokens=1500,
        session_total_tokens=4500,
        turn_elapsed_seconds=82.0,
        updated_at_monotonic=100.0,
    )

    summary = format_token_usage_summary(snapshot, now_monotonic=102.0)

    assert "total 1m 22s" in summary
    assert "just now" in summary


def test_format_token_usage_summary_hides_zero_placeholder_counts() -> None:
    snapshot = TokenUsageSnapshot(
        turn_total_tokens=0,
        session_total_tokens=0,
        turn_elapsed_seconds=188.0,
        updated_at_monotonic=100.0,
    )

    summary = format_token_usage_summary(snapshot, now_monotonic=102.0)

    assert "turn 0" not in summary
    assert "session 0" not in summary
    assert summary == "total 3m 8s   just now"


def test_format_context_window_shows_percentage_only_when_both_values_known() -> None:
    with_both = TokenUsageSnapshot(
        context_used_tokens=12400, context_limit_tokens=200000
    )
    used_only = TokenUsageSnapshot(context_used_tokens=12400, context_limit_tokens=None)
    none_known = TokenUsageSnapshot()

    assert format_context_window(with_both) == "12.4k / 200k (6%)"
    assert format_context_window(used_only) == "12.4k"
    assert format_context_window(none_known) == "—"


def test_format_token_count_compacts_large_values() -> None:
    assert format_token_count(None) == "—"
    assert format_token_count(42) == "42"
    assert format_token_count(1500) == "1.5k"
    assert format_token_count(200000) == "200k"


def test_relative_age_and_elapsed_duration_formatters_cover_compact_ranges() -> None:
    assert format_elapsed_duration(None) == ""
    assert format_elapsed_duration(42) == "42s"
    assert format_elapsed_duration(82) == "1m 22s"
    assert format_relative_age(None) == ""
    assert format_relative_age(2) == "just now"
    assert format_relative_age(42) == "42s ago"
    assert format_relative_age(120) == "2m ago"
