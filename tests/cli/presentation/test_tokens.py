from openminion.cli.presentation.tokens import (
    format_interactive_token_history,
    format_interactive_token_summary,
)
from openminion.modules.telemetry.usage import (
    TokenUsageEventRef,
    TokenUsageCoverage,
    TokenUsageRecord,
    TokenUsageSummary,
)
from openminion.modules.telemetry.usage.token_usage import (
    SURFACE_CONTEXT_PACK,
    SURFACE_LLM_TOTAL,
)


def _summary(
    session_id: str,
    *,
    tokens: int,
    observed_at: str,
) -> TokenUsageSummary:
    return TokenUsageSummary(
        session_id=session_id,
        records=(
            TokenUsageRecord(
                session_id=session_id,
                provider="openai",
                model="MiniMax-M2.7",
                surface=SURFACE_LLM_TOTAL,
                total_source="provider",
                total_tokens=tokens,
                input_tokens=tokens - 100,
                output_tokens=100,
            ),
            TokenUsageRecord(
                session_id=session_id,
                surface=SURFACE_CONTEXT_PACK,
                estimated_tokens=500,
            ),
        ),
        last_source_event=TokenUsageEventRef(observed_at=observed_at),
        coverage=TokenUsageCoverage(observed_llm_call_events=1),
    )


def test_interactive_token_summary_has_actionable_empty_state() -> None:
    output = format_interactive_token_summary(TokenUsageSummary("session-1"))

    assert "No model calls in this session yet." in output
    assert "send a prompt" in output
    assert "/tokens recent 10" in output
    assert "events=0" not in output


def test_interactive_token_summary_reports_unmetered_calls() -> None:
    output = format_interactive_token_summary(
        TokenUsageSummary(
            "session-1",
            coverage=TokenUsageCoverage(
                observed_llm_call_events=1,
                unmetered_llm_call_events=1,
                failed_llm_call_events=1,
            ),
        )
    )

    assert "Tokens: unavailable from the provider" in output
    assert "Calls: 1 observed · 1 unmetered · 1 failed" in output
    assert "No model calls" not in output


def test_interactive_token_summary_is_compact() -> None:
    output = format_interactive_token_summary(
        _summary(
            "focus-1234567890abcdefghijklmnopqrstuvwxyz",
            tokens=6500,
            observed_at="2026-09-09T07:30:00+00:00",
        )
    )

    assert "openai/MiniMax-M2.7" in output
    assert "6.5k total" in output
    assert "6.4k input" in output
    assert "100 output" in output
    assert "500 estimated" in output
    assert max(map(len, output.splitlines())) <= 80


def test_interactive_token_summary_preserves_small_nonzero_cost() -> None:
    output = format_interactive_token_summary(
        TokenUsageSummary(
            "session-1",
            records=(
                TokenUsageRecord(
                    session_id="session-1",
                    provider="openai",
                    model="gpt-test",
                    surface=SURFACE_LLM_TOTAL,
                    total_source="provider",
                    total_tokens=1,
                    cost_usd=0.000001,
                    cost_source="provider",
                ),
            ),
            coverage=TokenUsageCoverage(observed_llm_call_events=1),
        )
    )

    assert "Cost: $0.000001 provider" in output


def test_interactive_token_history_uses_readable_rows_and_deltas() -> None:
    output = format_interactive_token_history(
        (
            _summary(
                "focus-new-1234567890abcdefghijklmnopqrstuvwxyz",
                tokens=6500,
                observed_at="2026-09-09T07:30:00+00:00",
            ),
            _summary(
                "focus-old-1234567890abcdefghijklmnopqrstuvwxyz",
                tokens=6000,
                observed_at="2026-09-08T07:30:00+00:00",
            ),
        ),
        requested=10,
    )

    assert "Token history · 2 of 2 sessions with model calls" in output
    assert "2026-09-09 07:30" in output
    assert "6.4k input" in output
    assert "100 output" in output
    assert "change +500" in output
    assert "openminion status tokens --recent 10" in output
    assert "::conv:" not in output
    assert max(map(len, output.splitlines())) <= 90


def test_interactive_token_history_keeps_unmetered_sessions_visible() -> None:
    summary = TokenUsageSummary(
        "session-1",
        coverage=TokenUsageCoverage(
            observed_llm_call_events=2,
            unmetered_llm_call_events=2,
            failed_llm_call_events=1,
        ),
        last_source_event=TokenUsageEventRef(observed_at="2026-09-09T07:30:00+00:00"),
    )

    output = format_interactive_token_history((summary,), requested=10)

    assert "1 of 1 sessions with model calls" in output
    assert "tokens unavailable · 2 observed · 2 unmetered · 1 failed" in output


def test_interactive_token_history_does_not_compare_metered_to_unknown() -> None:
    unmetered = TokenUsageSummary(
        "session-old",
        coverage=TokenUsageCoverage(
            observed_llm_call_events=1,
            unmetered_llm_call_events=1,
        ),
    )

    output = format_interactive_token_history(
        (
            _summary(
                "session-new",
                tokens=6500,
                observed_at="2026-09-09T07:30:00+00:00",
            ),
            unmetered,
        ),
        requested=10,
    )

    assert "change —" in output
    assert "change +6,500" not in output


def test_interactive_token_history_ignores_context_only_sessions() -> None:
    context_only = TokenUsageSummary(
        "session-1",
        records=(
            TokenUsageRecord(
                session_id="session-1",
                surface=SURFACE_CONTEXT_PACK,
                estimated_tokens=500,
            ),
        ),
    )

    output = format_interactive_token_history((context_only,), requested=10)

    assert "No model calls in the newest 1 sessions." in output
    assert "sessions with model calls" not in output
