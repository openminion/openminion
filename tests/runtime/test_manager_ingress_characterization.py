from __future__ import annotations

from openminion.services.runtime.daemon import build_turn_request


def test_build_turn_request_preserves_runtime_ingress_fields() -> None:
    request = build_turn_request(
        {
            "trace_id": "trace-managed",
            "message": "latest news on korea",
            "agent_id": "main",
            "session_id": "managed-session",
            "channel": "console",
            "user": "api-user",
            "idempotency_key": "idem-managed",
            "inbound_metadata": {"origin": "api"},
            "conversation_id": "conv-managed",
            "deliver": False,
            "forced_tools": ["web.search"],
            "capability_category": "search",
            "timeout_seconds": 27,
        },
        default_agent_id="main",
    )

    assert request.trace_id == "trace-managed"
    assert request.agent_id == "main"
    assert request.session_id == "managed-session"
    assert request.input_text == "latest news on korea"
    assert request.meta["channel"] == "console"
    assert request.meta["user"] == "api-user"
    assert request.meta["idempotency_key"] == "idem-managed"
    assert request.meta["inbound_metadata"] == {"origin": "api"}
    assert request.meta["conversation_id"] == "conv-managed"
    assert request.meta["deliver"] is False
    assert request.meta["forced_tools"] == ["web.search"]
    assert request.meta["capability_category"] == "search"
    assert request.meta["timeout_seconds"] == 27
