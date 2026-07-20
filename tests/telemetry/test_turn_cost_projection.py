from openminion.modules.telemetry.trace.turn_cost import project_turn_cost


def test_turn_cost_projects_cadence_payload_and_delivery_boundary() -> None:
    events = [
        {
            "type": "llm.call.completed",
            "timestamp": "2026-07-20T08:00:01Z",
            "payload": {
                "purpose": "entry",
                "usage": {"input_tokens": 120, "output_tokens": 12},
                "request_bytes": 4096,
                "response_bytes": 256,
                "context_segment_count": 3,
                "context_tokens": 90,
                "context_bytes": 2048,
                "tool_schema_count": 8,
                "tool_schema_bytes": 1800,
                "exposed_tool_count": 8,
            },
        },
        {
            "type": "turn.assistant",
            "timestamp": "2026-07-20T08:00:02Z",
            "payload": {"content": "done"},
        },
        {
            "type": "llm.call.completed",
            "timestamp": "2026-07-20T08:00:03Z",
            "payload": {
                "purpose": "memory_reflection",
                "auxiliary": True,
                "usage": {"input_tokens": 20, "output_tokens": 4},
            },
        },
        {
            "type": "llm.call.retry",
            "timestamp": "2026-07-20T08:00:03Z",
            "payload": {"purpose": "memory_reflection"},
        },
        {
            "type": "chat.phase_timing",
            "timestamp": "2026-07-20T08:00:04Z",
            "payload": {
                "turn_id": "turn-1",
                "session_id": "session-1",
                "time_to_first_text_ms": 150,
                "provider_token_ttft_ms": None,
                "total_turn_ms": 400,
            },
        },
    ]

    cost = project_turn_cost(events, run_id="run-1")

    assert cost.provider_calls_critical_path == 1
    assert cost.provider_calls_post_delivery == 1
    assert cost.provider_calls_total == 2
    assert cost.provider_calls_auxiliary == 1
    assert cost.provider_retries == 1
    assert cost.call_purposes == ("entry", "memory_reflection")
    assert cost.input_tokens == 140
    assert cost.output_tokens == 16
    assert cost.request_bytes == 4096
    assert cost.tool_schema_count == 8
    assert cost.tool_schema_bytes == 1800
    assert cost.provider_token_ttft_ms is None
    assert cost.visible_text_ttft_ms == 150
    assert cost.total_wall_ms == 400


def test_turn_cost_preserves_unavailable_provider_facts_as_none() -> None:
    cost = project_turn_cost(
        [
            {
                "type": "llm.call.completed",
                "timestamp": "2026-07-20T08:00:01Z",
                "payload": {"purpose": "entry", "usage": {}},
            }
        ]
    )

    assert cost.request_bytes is None
    assert cost.context_tokens is None
    assert cost.cache_read_tokens is None
    assert cost.provider_token_ttft_ms is None
    assert cost.task_success is None
