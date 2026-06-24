from __future__ import annotations

from types import SimpleNamespace

from openminion.services.gateway.response import build_outbound_message


def test_build_outbound_message_attaches_shared_run_stats() -> None:
    outbound = build_outbound_message(
        response=SimpleNamespace(
            channel="console",
            target="cli-chat",
            text="hello",
            metadata={
                "total_input_tokens_used": "21",
                "total_output_tokens_used": "9",
                "llm_calls_count": "2",
                "tool_calls_count": "1",
                "elapsed_ms": "450",
                "tool_results": '[{"tool_name":"web.search","ok":false}]',
            },
        ),
        session_id="sess-1",
        run_id="run-1",
        request_id="req-1",
        conversation_id="conv-1",
        thread_id="thread-1",
        attach_id="attach-1",
        memory_context_meta={},
        memory_retrieval_meta={},
    )

    assert outbound.stats is not None
    assert outbound.stats.input_tokens == 21
    assert outbound.stats.output_tokens == 9
    assert outbound.stats.llm_calls == 2
    assert outbound.stats.tool_calls == 1
    assert outbound.stats.tool_errors == 1
    assert outbound.stats.duration_ms == 450
