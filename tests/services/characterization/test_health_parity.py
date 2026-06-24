from .parity import assert_structural_parity, capture_snapshot, DEFAULT_IGNORED_FIELDS


def test_health_structure_snapshot():
    baseline_health = {
        "status": "ok",
        "ok": True,
        "timestamp_utc": "2026-03-14T00:00:00Z",
        "agent": "openminion",
        "provider": "echo",
        "default_channel": "console",
        "counts": {"ok": 1, "warn": 0, "fail": 0},
        "checks": [
            {
                "id": "runtime.bootstrap",
                "status": "ok",
                "message": "Runtime components initialized successfully",
                "duration_ms": 3,
            }
        ],
        "normalized_health_snapshot": {
            "contract": "observability-health-snapshot-v1",
            "scope": "system",
            "observed_at": "2026-03-14T00:00:00Z",
            "summary": {"health_state": "healthy", "component_count": 1},
            "components": [
                {
                    "component": {
                        "component_kind": "runtime_manager",
                        "component_id": "primary",
                        "scope": "system",
                    },
                    "liveness": "alive",
                    "readiness": "ready",
                    "health_state": "healthy",
                    "observed_at": "2026-03-14T00:00:00Z",
                    "related_checks": ["runtime.bootstrap"],
                }
            ],
        },
    }

    ignored = set(DEFAULT_IGNORED_FIELDS) | {"timestamp_utc", "observed_at"}
    snapshot = capture_snapshot(baseline_health, ignored_fields=ignored)

    assert "timestamp_utc" not in snapshot
    assert "status" in snapshot
    assert "checks" in snapshot
    assert "normalized_health_snapshot" in snapshot
    assert "components" in snapshot["normalized_health_snapshot"]

    actual = baseline_health.copy()
    actual["timestamp_utc"] = "2026-03-15T00:00:00Z"
    actual["normalized_health_snapshot"] = dict(
        baseline_health["normalized_health_snapshot"]
    )
    actual["normalized_health_snapshot"]["observed_at"] = "2026-03-15T00:00:00Z"
    actual["normalized_health_snapshot"]["components"] = [
        dict(baseline_health["normalized_health_snapshot"]["components"][0])
    ]
    actual["normalized_health_snapshot"]["components"][0]["observed_at"] = (
        "2026-03-15T00:00:00Z"
    )

    assert_structural_parity(actual, baseline_health, ignored_fields=ignored)
