import pytest

from .parity import assert_structural_parity, capture_snapshot


def test_tool_selection_structure_snapshot():
    baseline_selection = {
        "query": "test query",
        "selected_tools": ["web.search", "web.fetch"],
        "ranking": [
            {"tool": "web.search", "score": 0.95},
            {"tool": "web.fetch", "score": 0.85},
        ],
        "metadata": {
            "intent_category": "web.search",
            "tool_count": 2,
        },
    }

    snapshot = capture_snapshot(baseline_selection)

    assert "query" in snapshot
    assert "selected_tools" in snapshot
    assert "ranking" in snapshot

    actual = baseline_selection.copy()
    actual["query"] = "different query"
    with pytest.raises(AssertionError):
        assert_structural_parity(actual, baseline_selection)
