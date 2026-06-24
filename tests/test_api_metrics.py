import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock
from tests._csc_fixtures import _csc_install_default_agent


from openminion.api.server import (
    dispatch_request,
    get_api_metrics_snapshot,
    reset_api_metrics,
)
from openminion.base.config import OpenMinionConfig, save_config
from openminion.api.turns import TurnTimeoutError


class APIMetricsTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_api_metrics()

    def tearDown(self) -> None:
        reset_api_metrics()

    def test_metrics_track_request_totals_route_totals_and_status_classes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = _write_echo_config(Path(tmp))
            dispatch_request("GET", "/health", str(config_path))
            dispatch_request("GET", "/missing", str(config_path))
            dispatch_request(
                "GET",
                "/sessions/session-1/messages",
                str(config_path),
            )
            dispatch_request(
                "POST",
                "/turns",
                str(config_path),
                body=None,
            )

            metrics = get_api_metrics_snapshot()

        requests = metrics["requests"]
        errors = metrics["errors"]
        self.assertEqual(requests["total"], 4)
        self.assertEqual(requests["by_route"]["GET /health"], 1)
        self.assertEqual(requests["by_route"]["GET /sessions/{id}/messages"], 1)
        self.assertEqual(requests["by_route"]["GET /<unknown>"], 1)
        self.assertEqual(requests["by_route"]["POST /turns"], 1)
        self.assertEqual(requests["status_classes"]["2xx"], 1)
        self.assertEqual(requests["status_classes"]["4xx"], 3)
        self.assertEqual(requests["by_route_status_classes"]["GET /health"]["2xx"], 1)
        self.assertEqual(
            requests["by_route_status_classes"]["GET /<unknown>"]["4xx"], 1
        )
        self.assertEqual(requests["by_route_status_classes"]["POST /turns"]["4xx"], 1)
        self.assertEqual(
            requests["by_route_status_classes"]["GET /sessions/{id}/messages"]["4xx"], 1
        )
        self.assertEqual(requests["rate"]["window_seconds"], 60)
        self.assertEqual(requests["rate"]["count"], 4)
        self.assertGreaterEqual(requests["rate"]["rps_1m"], 0.06)
        self.assertEqual(errors["by_code"]["invalid_request"], 1)
        self.assertEqual(errors["by_code"]["not_found"], 1)
        self.assertEqual(errors["by_code"]["session_not_found"], 1)
        self.assertEqual(errors["total"], 3)
        self.assertEqual(errors["by_route"]["GET /<unknown>"]["not_found"], 1)
        self.assertEqual(
            errors["by_route"]["GET /sessions/{id}/messages"]["session_not_found"],
            1,
        )
        self.assertEqual(errors["by_route"]["POST /turns"]["invalid_request"], 1)
        self.assertEqual(
            errors["top_error_codes"],
            [
                {"code": "invalid_request", "count": 1, "ratio": round(1 / 3, 4)},
                {"code": "not_found", "count": 1, "ratio": round(1 / 3, 4)},
                {"code": "session_not_found", "count": 1, "ratio": round(1 / 3, 4)},
            ],
        )
        self.assertEqual(metrics["turn_timeouts"]["total"], 0)
        self.assertEqual(metrics["turn_timeouts"]["by_route"], {})
        self.assertEqual(metrics["turn_timeouts"]["turn_requests_total"], 1)
        self.assertEqual(metrics["turn_timeouts"]["timeout_ratio"], 0.0)

    def test_metrics_track_turn_timeout_counters_for_sla_watch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = _write_echo_config(Path(tmp))
            with mock.patch(
                "openminion.api.server.run_turn",
                side_effect=TurnTimeoutError("timed out"),
            ):
                timeout_status, timeout_payload = dispatch_request(
                    "POST",
                    "/turns",
                    str(config_path),
                    body={"message": "timeout please"},
                )
            self.assertEqual(int(timeout_status), 504)
            self.assertEqual(timeout_payload["error"]["code"], "turn_timeout")

            with redirect_stdout(io.StringIO()):
                ok_status, ok_payload = dispatch_request(
                    "POST",
                    "/turns",
                    str(config_path),
                    body={"message": "healthy turn", "session_id": "timeout-metrics"},
                )
            self.assertEqual(int(ok_status), 200)
            self.assertTrue(ok_payload["ok"])

            metrics = get_api_metrics_snapshot()

        self.assertEqual(metrics["errors"]["by_code"]["turn_timeout"], 1)
        self.assertEqual(metrics["errors"]["total"], 1)
        self.assertEqual(
            metrics["errors"]["top_error_codes"],
            [{"code": "turn_timeout", "count": 1, "ratio": 1.0}],
        )
        turn_timeouts = metrics["turn_timeouts"]
        self.assertEqual(turn_timeouts["total"], 1)
        self.assertEqual(turn_timeouts["by_route"]["POST /turns"], 1)
        self.assertEqual(turn_timeouts["turn_requests_total"], 2)
        self.assertEqual(turn_timeouts["timeout_ratio"], 0.5)

    def test_metrics_track_turn_latency_and_reset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = _write_echo_config(Path(tmp))
            with redirect_stdout(io.StringIO()):
                dispatch_request(
                    "POST",
                    "/turns",
                    str(config_path),
                    body={"message": "hello metrics", "session_id": "metrics-session"},
                )

            snapshot = get_api_metrics_snapshot(reset=True)
            turn_latency = snapshot["turn_latency_ms"]
            rolling = turn_latency["rolling_window"]
            self.assertEqual(turn_latency["count"], 1)
            self.assertGreaterEqual(turn_latency["avg"], 0)
            self.assertGreaterEqual(turn_latency["max"], turn_latency["min"])
            self.assertEqual(rolling["count"], 1)
            self.assertGreaterEqual(rolling["p95"], rolling["p50"])
            self.assertGreaterEqual(rolling["max"], rolling["min"])
            self.assertEqual(snapshot["errors"]["by_code"], {})
            self.assertEqual(snapshot["errors"]["by_route"], {})
            self.assertEqual(snapshot["errors"]["total"], 0)
            self.assertEqual(snapshot["errors"]["top_error_codes"], [])

            reset_snapshot = get_api_metrics_snapshot()
            self.assertEqual(reset_snapshot["requests"]["total"], 0)
            self.assertEqual(reset_snapshot["requests"]["rate"]["count"], 0)
            self.assertEqual(reset_snapshot["requests"]["rate"]["rps_1m"], 0.0)
            self.assertEqual(reset_snapshot["turn_latency_ms"]["count"], 0)
            self.assertEqual(
                reset_snapshot["turn_latency_ms"]["rolling_window"]["count"], 0
            )
            self.assertEqual(reset_snapshot["errors"]["by_code"], {})
            self.assertEqual(reset_snapshot["errors"]["by_route"], {})
            self.assertEqual(reset_snapshot["errors"]["total"], 0)
            self.assertEqual(reset_snapshot["errors"]["top_error_codes"], [])
            self.assertEqual(reset_snapshot["turn_timeouts"]["total"], 0)
            self.assertEqual(reset_snapshot["turn_timeouts"]["by_route"], {})
            self.assertEqual(reset_snapshot["turn_timeouts"]["turn_requests_total"], 0)
            self.assertEqual(reset_snapshot["turn_timeouts"]["timeout_ratio"], 0.0)

    def test_metrics_route_returns_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = _write_echo_config(Path(tmp))
            dispatch_request("GET", "/health", str(config_path))
            dispatch_request("GET", "/missing", str(config_path))

            status, payload = dispatch_request("GET", "/metrics", str(config_path))
            self.assertEqual(int(status), 200)
            self.assertTrue(payload["ok"])
            self.assertFalse(payload["reset"])
            self.assertIn("snapshot_utc", payload["metrics"])
            self.assertIn("consistency", payload["metrics"])
            self.assertTrue(payload["metrics"]["consistency"]["stamp"])
            self.assertEqual(
                payload["metrics"]["schema"]["name"], "openminion.api.metrics"
            )
            self.assertEqual(payload["metrics"]["schema"]["version"], 1)
            self.assertIn("started_at_utc", payload["metrics"]["runtime"])
            self.assertGreaterEqual(payload["metrics"]["runtime"]["uptime_ms"], 0)
            self.assertIn("reset_at_utc", payload["metrics"]["reset"])
            self.assertGreaterEqual(payload["metrics"]["reset"]["since_reset_ms"], 0)
            self.assertEqual(payload["metrics"]["requests"]["total"], 2)
            self.assertEqual(
                payload["metrics"]["requests"]["rate"]["window_seconds"], 60
            )
            self.assertEqual(payload["metrics"]["requests"]["rate"]["count"], 2)
            self.assertGreaterEqual(
                payload["metrics"]["requests"]["rate"]["rps_1m"], 0.03
            )
            self.assertEqual(payload["metrics"]["errors"]["by_code"]["not_found"], 1)
            self.assertEqual(payload["metrics"]["errors"]["total"], 1)
            self.assertEqual(
                payload["metrics"]["errors"]["by_route"]["GET /<unknown>"]["not_found"],
                1,
            )
            self.assertEqual(
                payload["metrics"]["errors"]["top_error_codes"],
                [{"code": "not_found", "count": 1, "ratio": 1.0}],
            )
            self.assertEqual(payload["metrics"]["turn_timeouts"]["total"], 0)
            self.assertNotIn("GET /metrics", payload["metrics"]["requests"]["by_route"])
            self.assertNotIn(
                "GET /metrics",
                payload["metrics"]["requests"]["by_route_status_classes"],
            )

    def test_metrics_route_reset_query_clears_counters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = _write_echo_config(Path(tmp))
            dispatch_request("GET", "/health", str(config_path))

            status, payload = dispatch_request(
                "GET", "/metrics", str(config_path), query="reset=true"
            )
            self.assertEqual(int(status), 200)
            self.assertTrue(payload["reset"])
            self.assertEqual(payload["metrics"]["requests"]["total"], 1)
            first_stamp = payload["metrics"]["consistency"]["stamp"]

            status, second_payload = dispatch_request(
                "GET", "/metrics", str(config_path)
            )
            self.assertEqual(int(status), 200)
            self.assertEqual(second_payload["metrics"]["requests"]["total"], 0)
            self.assertIn("reset_at_utc", second_payload["metrics"]["reset"])
            self.assertGreaterEqual(
                second_payload["metrics"]["reset"]["since_reset_ms"], 0
            )
            self.assertNotEqual(
                second_payload["metrics"]["consistency"]["stamp"], first_stamp
            )

    def test_metrics_route_invalid_reset_returns_bad_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = _write_echo_config(Path(tmp))
            status, payload = dispatch_request(
                "GET", "/metrics", str(config_path), query="reset=maybe"
            )
            self.assertEqual(int(status), 400)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["error"]["code"], "invalid_request")

    def test_metrics_route_requires_token_when_env_set(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = _write_echo_config(Path(tmp))
            previous = os.environ.get("OPENMINION_API_METRICS_TOKEN")
            os.environ["OPENMINION_API_METRICS_TOKEN"] = "metrics-secret"
            try:
                denied_status, denied_payload = dispatch_request(
                    "GET", "/metrics", str(config_path)
                )
                self.assertEqual(int(denied_status), 403)
                self.assertFalse(denied_payload["ok"])
                self.assertEqual(denied_payload["error"]["code"], "forbidden")

                allowed_status, allowed_payload = dispatch_request(
                    "GET",
                    "/metrics",
                    str(config_path),
                    request_headers={"X-Metrics-Token": "metrics-secret"},
                )
                self.assertEqual(int(allowed_status), 200)
                self.assertTrue(allowed_payload["ok"])
            finally:
                if previous is None:
                    os.environ.pop("OPENMINION_API_METRICS_TOKEN", None)
                else:
                    os.environ["OPENMINION_API_METRICS_TOKEN"] = previous


def _write_echo_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "config.json"
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    # Set OPENMINION_DATA_ROOT to tmp for test isolation
    os.environ["OPENMINION_DATA_ROOT"] = str(tmp_path / ".openminion")
    config.runtime.log_level = "ERROR"
    _csc_install_default_agent(config, provider="echo")
    config.storage.path = str(tmp_path / "state" / "api.db")
    save_config(config, str(config_path))
    return config_path


class APITimingLogTests(unittest.TestCase):
    def test_request_done_logs_include_route_and_status_class(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = _write_echo_config(Path(tmp))
            with self.assertLogs("openminion.api", level="INFO") as captured:
                dispatch_request(
                    "GET", "/missing", str(config_path), request_id="timing-log-1"
                )

            joined = "\n".join(captured.output)
            self.assertIn("route=GET /<unknown>", joined)
            self.assertIn("status_class=4xx", joined)
            self.assertIn("duration_ms=", joined)
            self.assertIn("request_id=timing-log-1", joined)

    def test_slow_request_emits_warning_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = _write_echo_config(Path(tmp))
            with (
                mock.patch(
                    "openminion.api.server.perf_counter", side_effect=[0.0, 2.0]
                ),
                self.assertLogs("openminion.api", level="WARNING") as captured,
            ):
                dispatch_request(
                    "GET", "/health", str(config_path), request_id="timing-slow-1"
                )

            joined = "\n".join(captured.output)
            self.assertIn("api slow request", joined)
            self.assertIn("route=GET /health", joined)
            self.assertIn("status_class=2xx", joined)
            self.assertIn("duration_ms=2000", joined)
            self.assertIn("threshold_ms=1000", joined)
