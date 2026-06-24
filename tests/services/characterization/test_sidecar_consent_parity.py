from .parity import assert_structural_parity, capture_snapshot


def test_sidecar_consent_structure_snapshot():
    baseline_consent = {
        "sidecar": "self_improvement",
        "consent_required": True,
        "consent_given": False,
        "request_id": "req-123",  # Dynamic - ignored
        "metadata": {
            "trigger": "user_feedback",
            "confidence": 0.75,
        },
    }

    snapshot = capture_snapshot(baseline_consent)

    assert "request_id" not in snapshot
    assert "sidecar" in snapshot
    assert "consent_required" in snapshot

    actual = baseline_consent.copy()
    actual["request_id"] = "req-999"
    assert_structural_parity(actual, baseline_consent)
