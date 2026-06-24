from __future__ import annotations

from openminion.cli.chat.ui import (
    PhaseStatusDisplay,
    _format_elapsed_time,
    _phase_status_text,
)
from openminion.modules.brain.diagnostics.status import (
    PhaseStatus,
    phase_status_from_phase,
)


def test_phase_status_text_appends_mode_label_without_duplication() -> None:
    text = _phase_status_text(
        PhaseStatus(
            trace_id="trace-ui-mode-status",
            status_key="planning",
            label="Planning steps...",
            mode="plan",
            mode_state="generate_plan",
            mode_label="Generating execution plan",
        )
    )

    assert text == "Planning steps... Generating execution plan"


def test_phase_status_text_keeps_waiting_user_reply_text_out_of_spinner() -> None:
    text = _phase_status_text(
        PhaseStatus(
            trace_id="trace-ui-waiting",
            status_key="waiting_for_user",
            label="Waiting for your reply...",
            detail_text="Could you clarify what you'd like me to do?",
        )
    )

    assert text == "Waiting for your reply..."


def test_phase_status_text_normalizes_multiline_label_to_single_line() -> None:
    text = _phase_status_text(
        PhaseStatus(
            trace_id="trace-ui-multiline-label",
            status_key="executing",
            label="Executing step...\n\npartial result",
            detail_text="## Iteration 1\n\n### Finding",
        )
    )

    assert "\n" not in text
    assert "\r" not in text
    assert text == "Executing step... partial result ## Iteration 1…"


def test_phase_status_text_prefers_structured_turn_progress() -> None:
    text = _phase_status_text(
        PhaseStatus(
            trace_id="trace-ui-progress",
            status_key="working",
            label="Working...",
            llm_call_count=2,
            llm_call_limit=12,
            total_tokens_used=1500,
            tool_name="location.get",
            progress_phase="thinking...",
        )
    )

    assert text == "LLM 2/12 | 1.5k tokens | tool: location.get"


def test_phase_status_text_prefers_token_breakdown_when_available() -> None:
    text = _phase_status_text(
        PhaseStatus(
            trace_id="trace-ui-token-breakdown",
            status_key="working",
            label="Working...",
            llm_call_count=2,
            llm_call_limit=12,
            total_input_tokens_used=832,
            total_output_tokens_used=156,
            total_tokens_used=988,
            tool_name="location.get",
        )
    )

    assert text == "LLM 2/12 | ↑832 ↓156 tokens | tool: location.get"


def test_phase_status_text_token_breakdown_formats_thousands() -> None:
    text = _phase_status_text(
        PhaseStatus(
            trace_id="trace-ui-token-breakdown-thousands",
            status_key="working",
            label="Working...",
            llm_call_count=3,
            llm_call_limit=12,
            total_input_tokens_used=5100,
            total_output_tokens_used=1500,
            total_tokens_used=6600,
            progress_phase="thinking...",
        )
    )

    assert text == "LLM 3/12 | ↑5.1k ↓1.5k tokens | thinking..."


def test_phase_status_from_phase_carries_token_breakdown_payload() -> None:
    status = phase_status_from_phase(
        trace_id="trace-ui-status-token-breakdown",
        phase="ACT",
        payload={
            "turn.llm_call_count": 2,
            "turn.llm_call_limit": 12,
            "total_input_tokens_used": 1200,
            "total_output_tokens_used": 300,
            "total_tokens_used": 1500,
            "turn.tool_name": "web.search",
        },
    )

    assert status.total_input_tokens_used == 1200
    assert status.total_output_tokens_used == 300
    # specific phases now render the label as a
    # dedicated slot between `LLM N/M` and the tokens/tool slots,
    # instead of dropping the label entirely.
    assert (
        _phase_status_text(status)
        == "LLM 2/12 | Executing step... | ↑1.2k ↓300 tokens | tool: Web Search"
    )


def test_phase_status_text_shows_input_only_when_output_is_none() -> None:
    text = _phase_status_text(
        PhaseStatus(
            trace_id="trace-ui-pre-call-estimate",
            status_key="analyzing",
            label="Analyzing request...",
            llm_call_count=1,
            llm_call_limit=1,
            total_input_tokens_used=7200,
            total_output_tokens_used=None,
            total_tokens_used=7200,
            token_usage_estimated=True,
        )
    )

    assert text == "LLM 1/1 | Analyzing request... | ↑~7.2k tokens"


def test_phase_status_from_phase_carries_entry_call_token_breakdown() -> None:
    status = phase_status_from_phase(
        trace_id="trace-ui-entry-token-breakdown",
        phase="DECIDE",
        payload={
            "turn.llm_call_count": 1,
            "turn.llm_call_limit": 1,
            "total_input_tokens_used": 7200,
            "total_output_tokens_used": 150,
            "total_tokens_used": 7350,
        },
    )

    assert status.total_input_tokens_used == 7200
    assert status.total_output_tokens_used == 150
    assert status.status_key == "analyzing"
    assert (
        _phase_status_text(status)
        == "LLM 1/1 | Analyzing request... | ↑7.2k ↓150 tokens"
    )


def test_format_elapsed_time_uses_seconds_before_minute_boundary() -> None:
    assert _format_elapsed_time(0) == "0s"
    assert _format_elapsed_time(4.24) == "4s"
    assert _format_elapsed_time(59.9) == "59s"


def test_format_elapsed_time_uses_minutes_at_minute_boundary() -> None:
    assert _format_elapsed_time(60) == "1m 0s"
    assert _format_elapsed_time(125.9) == "2m 5s"


def test_phase_status_display_prefixes_elapsed_time(capsys) -> None:
    clock_values = iter([10.0, 14.2])
    display = PhaseStatusDisplay(enabled=False, clock=lambda: next(clock_values))
    display.enabled = True
    display._controller.start_turn()
    display.update(
        PhaseStatus(
            trace_id="trace-ui-progress-elapsed",
            status_key="working",
            label="Working...",
            llm_call_count=2,
            llm_call_limit=12,
            total_tokens_used=1500,
            tool_name="location.get",
        )
    )

    output = capsys.readouterr().out
    assert "4s | LLM 2/12 | 1.5k tokens | tool: location.get" in output


def test_phase_status_display_repaints_when_only_mode_label_changes() -> None:
    display = PhaseStatusDisplay(enabled=False)
    display.enabled = True
    display._render_once = lambda *args, **kwargs: None  # type: ignore[method-assign]

    display.update(
        PhaseStatus(
            trace_id="trace-ui-signature",
            status_key="planning",
            label="Planning steps...",
            mode="plan",
            mode_state="generate_plan",
            mode_label="Generating execution plan",
        )
    )
    first_label = display._label

    display.update(
        PhaseStatus(
            trace_id="trace-ui-signature",
            status_key="planning",
            label="Planning steps...",
            mode="plan",
            mode_state="approve_step",
            mode_label="Reviewing step 1/2: search",
        )
    )

    assert first_label == "Planning steps... Generating execution plan"
    assert display._label == "Planning steps... Reviewing step 1/2: search"


def test_phase_status_display_repaints_when_only_turn_progress_changes() -> None:
    display = PhaseStatusDisplay(enabled=False)
    display.enabled = True
    display._render_once = lambda *args, **kwargs: None  # type: ignore[method-assign]

    display.update(
        PhaseStatus(
            trace_id="trace-ui-progress-signature",
            status_key="working",
            label="Working...",
            llm_call_count=1,
            llm_call_limit=12,
            total_tokens_used=800,
            progress_phase="thinking...",
        )
    )
    first_label = display._label

    display.update(
        PhaseStatus(
            trace_id="trace-ui-progress-signature",
            status_key="working",
            label="Working...",
            llm_call_count=1,
            llm_call_limit=12,
            total_tokens_used=1500,
            progress_phase="composing answer",
        )
    )

    assert first_label == "LLM 1/12 | 0.8k tokens | thinking..."
    assert display._label == "LLM 1/12 | 1.5k tokens | composing answer"


def test_phase_status_display_repaints_when_only_token_breakdown_changes() -> None:
    display = PhaseStatusDisplay(enabled=False)
    display.enabled = True
    display._render_once = lambda *args, **kwargs: None  # type: ignore[method-assign]

    display.update(
        PhaseStatus(
            trace_id="trace-ui-token-breakdown-signature",
            status_key="working",
            label="Working...",
            llm_call_count=1,
            llm_call_limit=12,
            total_input_tokens_used=700,
            total_output_tokens_used=100,
            total_tokens_used=800,
            progress_phase="thinking...",
        )
    )
    first_label = display._label

    display.update(
        PhaseStatus(
            trace_id="trace-ui-token-breakdown-signature",
            status_key="working",
            label="Working...",
            llm_call_count=1,
            llm_call_limit=12,
            total_input_tokens_used=1200,
            total_output_tokens_used=300,
            total_tokens_used=1500,
            progress_phase="thinking...",
        )
    )

    assert first_label == "LLM 1/12 | ↑700 ↓100 tokens | thinking..."
    assert display._label == "LLM 1/12 | ↑1.2k ↓300 tokens | thinking..."
