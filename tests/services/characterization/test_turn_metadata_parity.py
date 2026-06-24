from .parity import assert_structural_parity, capture_snapshot


def test_turn_metadata_structure_snapshot():
    baseline_metadata = {
        "turn_id": "turn-123",  # Dynamic - ignored
        "session_id": "sess-456",  # Dynamic - ignored
        "model": "MiniMax-M2.5",
        "provider": "alibaba",
        "tool_calls": [
            {"name": "web.search", "arguments": {"query": "test"}},
        ],
        "metadata": {
            "route_lane": "conversational",
            "reason_code": "llm_routed",
        },
    }

    snapshot = capture_snapshot(baseline_metadata)

    assert "turn_id" not in snapshot
    assert "session_id" not in snapshot
    assert "model" in snapshot
    assert "tool_calls" in snapshot

    actual = baseline_metadata.copy()
    actual["turn_id"] = "turn-789"
    actual["session_id"] = "sess-999"
    assert_structural_parity(actual, baseline_metadata)
