from __future__ import annotations

from openminion.cli.presentation.timing_report import format_chat_phase_timing_report


def test_phase_timing_report_formats_total_and_major_phases() -> None:
    report = format_chat_phase_timing_report(
        {
            "total_turn_ms": 1530,
            "time_to_first_text_ms": 42,
            "provider_token_ttft_ms": 30,
            "phases_instrumented": [
                "context_pack_build",
                "provider_round_trip",
                "tool_calls",
                "approval_wait",
                "response_persistence",
            ],
            "context_pack_build_ms": 100,
            "provider_round_trip_ms": 1100,
            "tool_calls_ms": 200,
            "approval_wait_ms": 0,
            "response_persistence_ms": 20,
        }
    )

    assert report.startswith("Timing: total 1.5s; first text 42ms")
    assert "provider token 30ms" in report
    assert "context 100ms" in report
    assert "provider 1.1s" in report
    assert "tools 200ms" in report
    assert "approval 0ms" in report
    assert "finalization 20ms" in report


def test_phase_timing_report_omits_missing_phase_groups() -> None:
    report = format_chat_phase_timing_report(
        {"total_turn_ms": 20, "phases_instrumented": []}
    )

    assert report == "Timing: total 20ms"


def test_phase_timing_report_returns_empty_for_missing_total() -> None:
    assert format_chat_phase_timing_report({"provider_round_trip_ms": 10}) == ""
