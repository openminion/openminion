from unittest import mock

from openminion.api.metrics import APIRequestMetrics


def test_snapshot_includes_schema_version_metadata() -> None:
    snapshot = APIRequestMetrics().snapshot()
    assert "snapshot_utc" in snapshot
    assert "consistency" in snapshot
    assert "stamp" in snapshot["consistency"]
    assert "runtime_started_at_utc" in snapshot["consistency"]
    assert "metrics_reset_at_utc" in snapshot["consistency"]
    assert snapshot["consistency"]["stamp"]
    assert snapshot["schema"] == {"name": "openminion.api.metrics", "version": 1}
    assert "started_at_utc" in snapshot["runtime"]
    assert snapshot["runtime"]["uptime_ms"] >= 0
    assert "reset_at_utc" in snapshot["reset"]
    assert snapshot["reset"]["since_reset_ms"] >= 0
    assert snapshot["errors"]["total"] == 0
    assert snapshot["errors"]["top_error_codes"] == []


def test_turn_latency_rolling_window_percentiles() -> None:
    metrics = APIRequestMetrics()
    for duration_ms in (10, 20, 30, 40, 50, 60, 70, 80, 90, 100):
        metrics.observe(route="POST /turns", status_code=200, duration_ms=duration_ms)

    rolling = metrics.snapshot()["turn_latency_ms"]["rolling_window"]
    assert rolling["size"] == 128
    assert rolling["count"] == 10
    assert rolling["p50"] == 50
    assert rolling["p95"] == 100
    assert rolling["min"] == 10
    assert rolling["max"] == 100


def test_turn_latency_rolling_window_keeps_recent_samples_only() -> None:
    metrics = APIRequestMetrics()
    for duration_ms in range(1, 201):
        metrics.observe(route="POST /turns", status_code=200, duration_ms=duration_ms)

    rolling = metrics.snapshot()["turn_latency_ms"]["rolling_window"]
    assert rolling["size"] == 128
    assert rolling["count"] == 128
    assert rolling["min"] == 73
    assert rolling["max"] == 200


def test_turn_latency_rolling_window_ignores_non_turn_routes_and_resets() -> None:
    metrics = APIRequestMetrics()
    metrics.observe(route="GET /health", status_code=200, duration_ms=999)
    assert metrics.snapshot()["turn_latency_ms"]["rolling_window"]["count"] == 0

    metrics.observe(route="POST /turns", status_code=200, duration_ms=25)
    metrics.reset()
    rolling_after_reset = metrics.snapshot()["turn_latency_ms"]["rolling_window"]
    assert rolling_after_reset["count"] == 0
    assert rolling_after_reset["p50"] == 0
    assert rolling_after_reset["p95"] == 0


def test_request_rate_window_tracks_recent_requests() -> None:
    metrics = APIRequestMetrics()
    with mock.patch(
        "openminion.api.metrics.perf_counter",
        side_effect=[1.0, 2.0, 65.0, 65.0, 65.0, 65.0],
    ):
        for _ in range(3):
            metrics.observe(route="GET /health", status_code=200, duration_ms=5)
        snapshot = metrics.snapshot()

    rate = snapshot["requests"]["rate"]
    assert rate["window_seconds"] == 60
    assert rate["count"] == 1
    assert rate["rps_1m"] == round(1 / 60, 4)


def test_turn_timeout_counters_track_turn_route_and_reset() -> None:
    metrics = APIRequestMetrics()
    metrics.observe(
        route="GET /health",
        status_code=504,
        duration_ms=5,
        error_code="turn_timeout",
    )
    metrics.observe(
        route="POST /turns",
        status_code=504,
        duration_ms=25,
        error_code="turn_timeout",
    )
    metrics.observe(route="POST /turns", status_code=200, duration_ms=10)

    timeouts = metrics.snapshot()["turn_timeouts"]
    assert timeouts["total"] == 1
    assert timeouts["by_route"] == {"POST /turns": 1}
    assert timeouts["turn_requests_total"] == 2
    assert timeouts["timeout_ratio"] == 0.5

    metrics.reset()
    reset_snapshot = metrics.snapshot()["turn_timeouts"]
    assert reset_snapshot["total"] == 0
    assert reset_snapshot["by_route"] == {}
    assert reset_snapshot["turn_requests_total"] == 0
    assert reset_snapshot["timeout_ratio"] == 0.0


def test_top_error_codes_summary_is_ranked_and_capped() -> None:
    metrics = APIRequestMetrics()
    for code, count in (
        ("turn_timeout", 4),
        ("invalid_request", 3),
        ("not_found", 3),
        ("session_not_found", 2),
        ("forbidden", 1),
        ("runtime_unavailable", 1),
    ):
        for _ in range(count):
            metrics.observe(
                route="GET /<unknown>",
                status_code=400,
                duration_ms=5,
                error_code=code,
            )

    errors = metrics.snapshot()["errors"]
    assert errors["total"] == 14
    assert errors["top_error_codes"] == [
        {"code": "turn_timeout", "count": 4, "ratio": round(4 / 14, 4)},
        {"code": "invalid_request", "count": 3, "ratio": round(3 / 14, 4)},
        {"code": "not_found", "count": 3, "ratio": round(3 / 14, 4)},
        {"code": "session_not_found", "count": 2, "ratio": round(2 / 14, 4)},
        {"code": "forbidden", "count": 1, "ratio": round(1 / 14, 4)},
    ]
