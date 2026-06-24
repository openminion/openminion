from __future__ import annotations

from openminion.cli.chat.runtime import ChatRuntimeState, _format_post_turn_footer


def _runtime_state() -> ChatRuntimeState:
    return ChatRuntimeState(
        endpoint=None,
        transport="in-process",
        inproc_runtime=None,
        mode="single-process",
        auto_start=False,
        show_progress=False,
        quiet=False,
    )


def test_format_post_turn_footer_prefers_shared_stats_payload() -> None:
    summary = _format_post_turn_footer(
        _runtime_state(),
        payload={
            "stats": {
                "input_tokens": 30,
                "output_tokens": 12,
                "cache_read_tokens": 4,
                "llm_calls": 2,
                "tool_calls": 1,
                "tool_errors": 0,
                "duration_ms": 850,
            }
        },
        elapsed_seconds=1.2,
    )

    assert summary == "[tokens 30/12 cache 4 | calls 2 llm, 1 tools (0 err) | 0.8s]"
