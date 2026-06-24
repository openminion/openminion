import json
import io
import os
import sqlite3
import tempfile
import unittest
from argparse import Namespace
from contextlib import contextmanager, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tests._csc_fixtures import _csc_install_default_agent


from openminion.api.server import dispatch_request
from openminion.base.config import OpenMinionConfig, save_config
from openminion.cli.commands.doctor import run_doctor
from openminion.modules.telemetry.lifecycle import (
    build_component_identity,
    build_cron_scheduler_component_identity,
    build_lifecycle_telemetry_event,
)
from openminion.modules.telemetry.service import (
    TelemetryService,
    resolve_telemetry_db_path,
)


@contextmanager
def _isolated_openminion_home(home_root: Path):
    previous_home = os.environ.get("OPENMINION_HOME")
    previous_data_root = os.environ.get("OPENMINION_DATA_ROOT")
    os.environ["OPENMINION_HOME"] = str(home_root)
    os.environ.pop("OPENMINION_DATA_ROOT", None)
    try:
        yield
    finally:
        if previous_home is None:
            os.environ.pop("OPENMINION_HOME", None)
        else:
            os.environ["OPENMINION_HOME"] = previous_home
        if previous_data_root is None:
            os.environ.pop("OPENMINION_DATA_ROOT", None)
        else:
            os.environ["OPENMINION_DATA_ROOT"] = previous_data_root


def _record_runtime_lifecycle_event(
    *,
    home_root: Path,
    event_type: str,
    reason: str | None = None,
) -> None:
    _record_lifecycle_event(
        home_root=home_root,
        component=build_component_identity(
            component_kind="runtime_manager",
            component_id="primary",
            scope="system",
            owner_module="openminion-runtime",
        ),
        event_type=event_type,
        reason=reason,
    )


def _record_lifecycle_event(
    *,
    home_root: Path,
    component: dict[str, object],
    event_type: str,
    reason: str | None = None,
    metrics: dict[str, object] | None = None,
    observed_at: datetime | None = None,
) -> None:
    service = TelemetryService(home_root=home_root)
    component_kind = str(component.get("component_kind") or "").strip()
    component_id = str(component.get("component_id") or "").strip()
    event = build_lifecycle_telemetry_event(
        event_type=event_type,
        component=component,
        module_id="openminion-runtime",
        session_id=f"lifecycle:{component_kind}:{component_id}",
        turn_id=f"{component_kind}:{event_type.rsplit('.', 1)[-1]}",
        status="error" if event_type == "component.crashed" else "ok",
        reason=reason,
        metrics=metrics,
        source_classification="native_canonical",
    )
    service.record_event_sync(event)
    service.close_sync()
    if observed_at is None:
        return
    telemetry_db = Path(resolve_telemetry_db_path(home_root=home_root).db_path)
    conn = sqlite3.connect(str(telemetry_db))
    try:
        conn.execute(
            "UPDATE events SET timestamp = ? WHERE rowid = (SELECT MAX(rowid) FROM events)",
            (float(observed_at.timestamp()),),
        )
        conn.commit()
    finally:
        conn.close()


class APIHealthTests(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self._previous_openminion_home = os.environ.pop("OPENMINION_HOME", None)
        self._previous_openminion_data_root = os.environ.pop(
            "OPENMINION_DATA_ROOT",
            None,
        )

    def tearDown(self) -> None:
        if self._previous_openminion_home is not None:
            os.environ["OPENMINION_HOME"] = self._previous_openminion_home
        else:
            os.environ.pop("OPENMINION_HOME", None)
        if self._previous_openminion_data_root is not None:
            os.environ["OPENMINION_DATA_ROOT"] = self._previous_openminion_data_root
        else:
            os.environ.pop("OPENMINION_DATA_ROOT", None)
        super().tearDown()

    def test_health_endpoint_returns_ok_for_echo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "config.json"
            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.runtime.log_level = "ERROR"
            _csc_install_default_agent(config, provider="echo")
            config.storage.path = str(tmp_path / "state" / "health.db")
            save_config(config, str(config_path))

            status, payload = dispatch_request("GET", "/health", str(config_path))
            self.assertEqual(int(status), 200)
            self.assertTrue(payload["ok"])
            check_ids = {check["id"] for check in payload["checks"]}
            self.assertIn("storage.ready", check_ids)
            self.assertIn("provider.supported", check_ids)
            self.assertIn("runtime.bootstrap", check_ids)
            self.assertIn("runtime.brain.llm_mode_observability", check_ids)
            self.assertIn("runtime.dependency_latency_budget", check_ids)
            for check in payload["checks"]:
                self.assertIn("duration_ms", check)
                self.assertGreaterEqual(check["duration_ms"], 0)
            latency_budget_check = next(
                check
                for check in payload["checks"]
                if check["id"] == "runtime.dependency_latency_budget"
            )
            self.assertIn("details", latency_budget_check)
            self.assertIn("threshold_ms", latency_budget_check["details"])
            self.assertIn("slow_checks", latency_budget_check["details"])
            readiness = payload["readiness_by_group"]
            self.assertIn("storage", readiness)
            self.assertIn("provider", readiness)
            self.assertIn("runtime", readiness)
            self.assertGreaterEqual(readiness["storage"]["ok"], 1)
            timing = payload["dependency_timing_ms"]
            self.assertEqual(timing["count"], len(payload["checks"]))
            self.assertGreaterEqual(timing["total"], 0)
            self.assertGreaterEqual(timing["max"], 0)
            self.assertIn(timing["slowest_check_id"], check_ids)
            self.assertIn("storage", timing["by_group"])
            hints = payload["operator_hints"]
            self.assertEqual(hints["metrics"]["path"], "/metrics")
            self.assertEqual(hints["metrics"]["reset_example"], "/metrics?reset=true")
            self.assertEqual(hints["metrics"]["requires_token"], False)
            consistency = payload["consistency"]
            self.assertTrue(consistency["stamp"])
            self.assertTrue(consistency["runtime_started_at_utc"])
            self.assertTrue(consistency["metrics_reset_at_utc"])
            normalized = payload["normalized_health_snapshot"]
            self.assertEqual(normalized["contract"], "observability-health-snapshot-v1")
            self.assertEqual(normalized["scope"], "system")
            self.assertGreaterEqual(normalized["summary"]["component_count"], 1)
            component_snapshots = normalized["components"]
            self.assertGreaterEqual(len(component_snapshots), 1)
            first_snapshot = component_snapshots[0]
            self.assertIn("component", first_snapshot)
            self.assertIn("liveness", first_snapshot)
            self.assertIn("readiness", first_snapshot)
            self.assertIn("health_state", first_snapshot)
            self.assertIn("observed_at", first_snapshot)
            self.assertIn("related_checks", first_snapshot)
            component = first_snapshot["component"]
            self.assertIn("component_kind", component)
            self.assertIn("component_id", component)
            self.assertIn("scope", component)

    def test_health_runtime_snapshot_uses_lifecycle_heartbeat_for_liveness_evidence(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "config.json"
            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.runtime.log_level = "ERROR"
            _csc_install_default_agent(config, provider="echo")
            config.storage.path = str(tmp_path / "state" / "health.db")
            save_config(config, str(config_path))

            with _isolated_openminion_home(tmp_path):
                _record_runtime_lifecycle_event(
                    home_root=tmp_path,
                    event_type="component.heartbeat",
                    reason="heartbeat",
                )
                status, payload = dispatch_request("GET", "/health", str(config_path))

            self.assertEqual(int(status), 200)
            runtime_snapshot = next(
                item
                for item in payload["normalized_health_snapshot"]["components"]
                if item["component"]["component_kind"] == "runtime_manager"
                and item["component"]["component_id"] == "primary"
            )
            self.assertEqual(runtime_snapshot["liveness"], "alive")
            self.assertEqual(runtime_snapshot["health_state"], "degraded")
            self.assertEqual(
                runtime_snapshot["lifecycle_event_type"], "component.heartbeat"
            )
            self.assertEqual(
                runtime_snapshot["lifecycle_source_classification"],
                "native_canonical",
            )
            self.assertTrue(runtime_snapshot["last_heartbeat_at"])

    def test_health_runtime_snapshot_uses_recent_crash_for_degraded_posture(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "config.json"
            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.runtime.log_level = "ERROR"
            _csc_install_default_agent(config, provider="echo")
            config.storage.path = str(tmp_path / "state" / "health.db")
            save_config(config, str(config_path))

            with _isolated_openminion_home(tmp_path):
                _record_runtime_lifecycle_event(
                    home_root=tmp_path,
                    event_type="component.crashed",
                    reason="kill_switch",
                )
                status, payload = dispatch_request("GET", "/health", str(config_path))

            self.assertEqual(int(status), 200)
            runtime_snapshot = next(
                item
                for item in payload["normalized_health_snapshot"]["components"]
                if item["component"]["component_kind"] == "runtime_manager"
                and item["component"]["component_id"] == "primary"
            )
            self.assertEqual(runtime_snapshot["liveness"], "unknown")
            self.assertEqual(runtime_snapshot["readiness"], "not_ready")
            self.assertEqual(runtime_snapshot["health_state"], "degraded")
            self.assertEqual(
                runtime_snapshot["lifecycle_event_type"], "component.crashed"
            )
            self.assertEqual(runtime_snapshot["last_exit_reason"], "kill_switch")
            self.assertIn("kill_switch", runtime_snapshot["status_message"])

    def test_health_runtime_snapshot_clears_stale_exit_reason_after_heartbeat(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "config.json"
            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.runtime.log_level = "ERROR"
            _csc_install_default_agent(config, provider="echo")
            config.storage.path = str(tmp_path / "state" / "health.db")
            save_config(config, str(config_path))

            with _isolated_openminion_home(tmp_path):
                _record_runtime_lifecycle_event(
                    home_root=tmp_path,
                    event_type="component.crashed",
                    reason="kill_switch",
                )
                _record_runtime_lifecycle_event(
                    home_root=tmp_path,
                    event_type="component.heartbeat",
                    reason="heartbeat",
                )
                status, payload = dispatch_request("GET", "/health", str(config_path))

            self.assertEqual(int(status), 200)
            runtime_snapshot = next(
                item
                for item in payload["normalized_health_snapshot"]["components"]
                if item["component"]["component_kind"] == "runtime_manager"
                and item["component"]["component_id"] == "primary"
            )
            self.assertEqual(runtime_snapshot["liveness"], "alive")
            self.assertEqual(
                runtime_snapshot["lifecycle_event_type"], "component.heartbeat"
            )
            self.assertNotIn("last_exit_reason", runtime_snapshot)
            self.assertNotIn("kill_switch", runtime_snapshot["status_message"])

    def test_health_runtime_snapshot_missing_lifecycle_data_stays_compatible(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "config.json"
            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.runtime.log_level = "ERROR"
            _csc_install_default_agent(config, provider="echo")
            config.storage.path = str(tmp_path / "state" / "health.db")
            save_config(config, str(config_path))

            with _isolated_openminion_home(tmp_path):
                status, payload = dispatch_request("GET", "/health", str(config_path))

            self.assertEqual(int(status), 200)
            runtime_snapshot = next(
                item
                for item in payload["normalized_health_snapshot"]["components"]
                if item["component"]["component_kind"] == "runtime_manager"
                and item["component"]["component_id"] == "primary"
            )
            self.assertEqual(runtime_snapshot["liveness"], "alive")
            self.assertEqual(runtime_snapshot["health_state"], "degraded")
            self.assertNotIn("last_heartbeat_at", runtime_snapshot)
            self.assertNotIn("last_exit_reason", runtime_snapshot)

    def test_health_adds_daemon_supervision_snapshot_for_stale_heartbeat(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "config.json"
            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.runtime.log_level = "ERROR"
            _csc_install_default_agent(config, provider="echo")
            config.storage.path = str(tmp_path / "state" / "health.db")
            save_config(config, str(config_path))

            with _isolated_openminion_home(tmp_path):
                _record_lifecycle_event(
                    home_root=tmp_path,
                    component=build_component_identity(
                        component_kind="daemon",
                        component_id="primary",
                        scope="system",
                        owner_module="openminion-runtime",
                    ),
                    event_type="component.heartbeat",
                    reason="heartbeat",
                    observed_at=datetime.now(tz=timezone.utc) - timedelta(seconds=70),
                )
                status, payload = dispatch_request("GET", "/health", str(config_path))

            self.assertEqual(int(status), 503)
            daemon_snapshot = next(
                item
                for item in payload["normalized_health_snapshot"]["components"]
                if item["component"]["component_kind"] == "daemon"
            )
            self.assertEqual(daemon_snapshot["health_state"], "failed")
            self.assertEqual(
                daemon_snapshot["supervision"]["reason"], "stale_heartbeat_failed"
            )
            self.assertEqual(
                daemon_snapshot["supervision"]["restart"]["action"], "none"
            )

    def test_health_adds_cron_supervision_snapshot_for_scheduler_lag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "config.json"
            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.runtime.log_level = "ERROR"
            _csc_install_default_agent(config, provider="echo")
            config.storage.path = str(tmp_path / "state" / "health.db")
            save_config(config, str(config_path))

            with _isolated_openminion_home(tmp_path):
                _record_lifecycle_event(
                    home_root=tmp_path,
                    component=build_cron_scheduler_component_identity(
                        daemon_component_id="primary"
                    ),
                    event_type="component.heartbeat",
                    reason="heartbeat",
                    metrics={"lag_seconds": 5.0, "tick_seconds": 2.0},
                )
                status, payload = dispatch_request("GET", "/health", str(config_path))

            self.assertEqual(int(status), 200)
            cron_snapshot = next(
                item
                for item in payload["normalized_health_snapshot"]["components"]
                if item["component"]["component_kind"] == "cron_scheduler"
            )
            self.assertEqual(cron_snapshot["health_state"], "degraded")
            self.assertEqual(
                cron_snapshot["supervision"]["reason"], "scheduler_lag_degraded"
            )

    def test_health_and_metrics_share_consistency_stamp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "config.json"
            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.runtime.log_level = "ERROR"
            _csc_install_default_agent(config, provider="echo")
            config.storage.path = str(tmp_path / "state" / "health.db")
            save_config(config, str(config_path))

            health_status, health_payload = dispatch_request(
                "GET", "/health", str(config_path)
            )
            metrics_status, metrics_payload = dispatch_request(
                "GET", "/metrics", str(config_path)
            )
            self.assertEqual(int(health_status), 200)
            self.assertEqual(int(metrics_status), 200)
            self.assertEqual(
                health_payload["consistency"],
                metrics_payload["metrics"]["consistency"],
            )

    def test_health_endpoint_returns_503_for_missing_openai_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "config.json"
            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.runtime.log_level = "ERROR"
            _csc_install_default_agent(config, provider="openai")
            config.providers.openai.api_key = ""
            config.providers.openai.api_key_env = "OPENMINION_TEST_OPENAI_KEY_MISSING"
            config.storage.path = str(tmp_path / "state" / "health.db")
            save_config(config, str(config_path))

            status, payload = dispatch_request("GET", "/health", str(config_path))
            self.assertEqual(int(status), 503)
            self.assertFalse(payload["ok"])
            check_by_id = {check["id"]: check for check in payload["checks"]}
            self.assertEqual(check_by_id["provider.openai.key"]["status"], "fail")
            normalized = payload["normalized_health_snapshot"]
            provider_snapshot = next(
                item
                for item in normalized["components"]
                if item["component"]["component_kind"] == "provider_binding"
            )
            self.assertEqual(provider_snapshot["health_state"], "failed")
            self.assertEqual(provider_snapshot["liveness"], "unknown")
            readiness = payload["readiness_by_group"]
            self.assertIn("provider", readiness)
            self.assertGreaterEqual(readiness["provider"]["fail"], 1)
            timing = payload["dependency_timing_ms"]
            self.assertEqual(timing["count"], len(payload["checks"]))
            self.assertGreaterEqual(timing["total"], 0)

    def test_health_endpoint_returns_503_for_missing_cerebras_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "config.json"
            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.runtime.log_level = "ERROR"
            _csc_install_default_agent(config, provider="cerebras")
            config.providers.cerebras.api_key = ""
            config.providers.cerebras.api_key_env = (
                "OPENMINION_TEST_CEREBRAS_KEY_MISSING"
            )
            config.storage.path = str(tmp_path / "state" / "health.db")
            save_config(config, str(config_path))

            status, payload = dispatch_request("GET", "/health", str(config_path))
            self.assertEqual(int(status), 503)
            self.assertFalse(payload["ok"])
            check_by_id = {check["id"]: check for check in payload["checks"]}
            self.assertEqual(check_by_id["provider.cerebras.key"]["status"], "fail")

    def test_health_endpoint_returns_503_for_missing_groq_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "config.json"
            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.runtime.log_level = "ERROR"
            _csc_install_default_agent(config, provider="groq")
            config.providers.groq.api_key = ""
            config.providers.groq.api_key_env = "OPENMINION_TEST_GROQ_KEY_MISSING"
            config.storage.path = str(tmp_path / "state" / "health.db")
            save_config(config, str(config_path))

            status, payload = dispatch_request("GET", "/health", str(config_path))
            self.assertEqual(int(status), 503)
            self.assertFalse(payload["ok"])
            check_by_id = {check["id"]: check for check in payload["checks"]}
            self.assertEqual(check_by_id["provider.groq.key"]["status"], "fail")

    def test_health_and_doctor_provider_failure_use_same_normalized_probe_ids(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "config.json"
            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.runtime.log_level = "ERROR"
            _csc_install_default_agent(config, provider="unknown-provider")
            config.storage.path = str(tmp_path / "state" / "health.db")
            save_config(config, str(config_path))

            health_status, health_payload = dispatch_request(
                "GET", "/health", str(config_path)
            )
            self.assertEqual(int(health_status), 503)
            health_by_id = {check["id"]: check for check in health_payload["checks"]}
            self.assertEqual(health_by_id["provider.supported"]["status"], "fail")

            args = Namespace(
                config=str(config_path),
                check_turn=False,
                message="doctor ping",
                target="doctor",
                channel=None,
                json=True,
            )
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                doctor_code = run_doctor(args)
            self.assertEqual(doctor_code, 1)
            doctor_payload = json.loads(buffer.getvalue())
            doctor_by_id = {check["id"]: check for check in doctor_payload["checks"]}
            provider_check = doctor_by_id["provider.supported"]
            self.assertEqual(provider_check["status"], "fail")
            self.assertEqual(provider_check["finding_id"], "provider.supported")
            self.assertEqual(
                provider_check["related_probe_ids"], ["provider.supported"]
            )

            provider_snapshot = next(
                item
                for item in health_payload["normalized_health_snapshot"]["components"]
                if item["component"]["component_kind"] == "provider_binding"
            )
            self.assertEqual(provider_snapshot["health_state"], "failed")

    def test_health_and_doctor_surface_same_daemon_supervision_posture(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "config.json"
            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.runtime.log_level = "ERROR"
            _csc_install_default_agent(config, provider="echo")
            config.storage.path = str(tmp_path / "state" / "health.db")
            save_config(config, str(config_path))

            with _isolated_openminion_home(tmp_path):
                _record_lifecycle_event(
                    home_root=tmp_path,
                    component=build_component_identity(
                        component_kind="daemon",
                        component_id="primary",
                        scope="system",
                        owner_module="openminion-runtime",
                    ),
                    event_type="component.heartbeat",
                    reason="heartbeat",
                    observed_at=datetime.now(tz=timezone.utc) - timedelta(seconds=70),
                )
                health_status, health_payload = dispatch_request(
                    "GET", "/health", str(config_path)
                )
                args = Namespace(
                    config=str(config_path),
                    check_turn=False,
                    message="doctor ping",
                    target="doctor",
                    channel=None,
                    json=True,
                )
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    doctor_code = run_doctor(args)

            self.assertEqual(int(health_status), 503)
            self.assertEqual(doctor_code, 1)
            daemon_snapshot = next(
                item
                for item in health_payload["normalized_health_snapshot"]["components"]
                if item["component"]["component_kind"] == "daemon"
            )
            self.assertEqual(
                daemon_snapshot["supervision"]["reason"], "stale_heartbeat_failed"
            )
            doctor_payload = json.loads(buffer.getvalue())
            doctor_by_id = {check["id"]: check for check in doctor_payload["checks"]}
            daemon_check = doctor_by_id["supervision.daemon.primary"]
            self.assertEqual(daemon_check["status"], "fail")
            self.assertIn("stale_heartbeat_failed", daemon_check["message"])
            self.assertEqual(
                daemon_check["target_component"]["component_kind"], "daemon"
            )

    def test_health_uses_resolved_openminion_home_for_supervision_lookup(self) -> None:
        with (
            tempfile.TemporaryDirectory() as home_tmp,
            tempfile.TemporaryDirectory() as cfg_tmp,
        ):
            home_root = Path(home_tmp)
            cfg_root = Path(cfg_tmp)
            config_path = cfg_root / "config.json"
            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.runtime.log_level = "ERROR"
            _csc_install_default_agent(config, provider="echo")
            config.storage.path = str(home_root / "state" / "health.db")
            save_config(config, str(config_path))

            with _isolated_openminion_home(home_root):
                _record_lifecycle_event(
                    home_root=home_root,
                    component=build_component_identity(
                        component_kind="daemon",
                        component_id="primary",
                        scope="system",
                        owner_module="openminion-runtime",
                    ),
                    event_type="component.heartbeat",
                    reason="heartbeat",
                    observed_at=datetime.now(tz=timezone.utc) - timedelta(seconds=70),
                )
                status, payload = dispatch_request("GET", "/health", str(config_path))

            self.assertEqual(int(status), 503)
            daemon_snapshot = next(
                item
                for item in payload["normalized_health_snapshot"]["components"]
                if item["component"]["component_kind"] == "daemon"
            )
            self.assertEqual(
                daemon_snapshot["supervision"]["reason"], "stale_heartbeat_failed"
            )
            self.assertGreaterEqual(payload["counts"]["fail"], 1)

    def test_health_normalization_logs_positive_and_negative_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            ok_config_path = tmp_path / "ok-config.json"
            ok_config = OpenMinionConfig()
            _csc_install_default_agent(ok_config)
            ok_config.runtime.log_level = "ERROR"
            ok_config.agents[next(iter(ok_config.agents.keys()))].provider = "echo"
            ok_config.storage.path = str(tmp_path / "state" / "ok-health.db")
            save_config(ok_config, str(ok_config_path))

            with self.assertLogs("openminion.health", level="INFO") as ok_logs:
                status, _payload = dispatch_request(
                    "GET", "/health", str(ok_config_path)
                )
            self.assertEqual(int(status), 200)
            self.assertTrue(
                any("health.normalization.summary" in entry for entry in ok_logs.output)
            )

            fail_config_path = tmp_path / "fail-config.json"
            fail_config = OpenMinionConfig()
            _csc_install_default_agent(fail_config)
            fail_config.runtime.log_level = "ERROR"
            fail_config.agents[
                next(iter(fail_config.agents.keys()))
            ].provider = "openai"
            fail_config.providers.openai.api_key = ""
            fail_config.providers.openai.api_key_env = (
                "OPENMINION_TEST_OPENAI_KEY_MISSING_LOGS"
            )
            fail_config.storage.path = str(tmp_path / "state" / "fail-health.db")
            save_config(fail_config, str(fail_config_path))

            with self.assertLogs("openminion.health", level="WARNING") as fail_logs:
                status, _payload = dispatch_request(
                    "GET", "/health", str(fail_config_path)
                )
            self.assertEqual(int(status), 503)
            self.assertTrue(
                any(
                    "health.normalization.negative" in entry
                    for entry in fail_logs.output
                )
            )

    def test_unknown_route_returns_404(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "config.json"
            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.runtime.log_level = "ERROR"
            _csc_install_default_agent(config, provider="echo")
            config.storage.path = str(tmp_path / "state" / "health.db")
            save_config(config, str(config_path))

            status, payload = dispatch_request("GET", "/missing", str(config_path))
            self.assertEqual(int(status), 404)
            self.assertFalse(payload["ok"])
            error = payload["error"]
            self.assertEqual(error["code"], "not_found")
            self.assertIn("message", error)
            self.assertIn("details", error)
            self.assertIn("retryable", error)
            self.assertIn("retry_after_ms", error)

    def test_health_endpoint_cortensor_without_key_is_warn_not_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "config.json"
            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.runtime.log_level = "ERROR"
            _csc_install_default_agent(config, provider="cortensor")
            config.providers.cortensor.api_key = ""
            config.providers.cortensor.api_key_env = (
                "OPENMINION_TEST_CORTENSOR_KEY_MISSING"
            )
            config.storage.path = str(tmp_path / "state" / "health.db")
            save_config(config, str(config_path))

            status, payload = dispatch_request("GET", "/health", str(config_path))
            self.assertEqual(int(status), 200)
            self.assertTrue(payload["ok"])
            check_by_id = {check["id"]: check for check in payload["checks"]}
            self.assertEqual(check_by_id["provider.cortensor.key"]["status"], "warn")
            self.assertIn("dependency_timing_ms", payload)
            self.assertGreaterEqual(payload["dependency_timing_ms"]["total"], 0)

    def test_health_endpoint_cortensor_completion_mode_requires_session_id(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "config.json"
            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.runtime.log_level = "ERROR"
            _csc_install_default_agent(config, provider="cortensor")
            config.providers.cortensor.api_mode = "cortensor_completion"
            config.providers.cortensor.session_id = 0
            config.storage.path = str(tmp_path / "state" / "health.db")
            save_config(config, str(config_path))

            status, payload = dispatch_request("GET", "/health", str(config_path))
            self.assertEqual(int(status), 503)
            self.assertFalse(payload["ok"])
            check_by_id = {check["id"]: check for check in payload["checks"]}
            self.assertEqual(
                check_by_id["provider.cortensor.session_id"]["status"], "fail"
            )

    def test_health_endpoint_cortensor_completion_mode_accepts_session_ids_list(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "config.json"
            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.runtime.log_level = "ERROR"
            _csc_install_default_agent(config, provider="cortensor")
            config.providers.cortensor.api_mode = "cortensor_completion"
            config.providers.cortensor.session_id = 0
            config.providers.cortensor.session_ids = [45]
            config.storage.path = str(tmp_path / "state" / "health.db")
            save_config(config, str(config_path))

            status, payload = dispatch_request("GET", "/health", str(config_path))
            self.assertEqual(int(status), 200)
            self.assertTrue(payload["ok"])
            check_by_id = {check["id"]: check for check in payload["checks"]}
            self.assertEqual(
                check_by_id["provider.cortensor.session_id"]["status"], "ok"
            )

    def test_health_endpoint_cortensor_completion_mode_accepts_dedicated_session_ids_list(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "config.json"
            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.runtime.log_level = "ERROR"
            _csc_install_default_agent(config, provider="cortensor")
            config.providers.cortensor.api_mode = "cortensor_completion"
            config.providers.cortensor.session_id = 0
            config.providers.cortensor.dedicated_session_ids = [47]
            config.storage.path = str(tmp_path / "state" / "health.db")
            save_config(config, str(config_path))

            status, payload = dispatch_request("GET", "/health", str(config_path))
            self.assertEqual(int(status), 200)
            self.assertTrue(payload["ok"])
            check_by_id = {check["id"]: check for check in payload["checks"]}
            self.assertEqual(
                check_by_id["provider.cortensor.session_id"]["status"], "ok"
            )

    def test_health_latency_budget_warns_when_threshold_is_forced_to_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "config.json"
            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.runtime.log_level = "ERROR"
            _csc_install_default_agent(config, provider="echo")
            config.storage.path = str(tmp_path / "state" / "health.db")
            save_config(config, str(config_path))

            previous = os.environ.get("OPENMINION_HEALTH_CHECK_WARN_MS")
            os.environ["OPENMINION_HEALTH_CHECK_WARN_MS"] = "0"
            try:
                status, payload = dispatch_request("GET", "/health", str(config_path))
                self.assertEqual(int(status), 200)
                check_by_id = {check["id"]: check for check in payload["checks"]}
                latency_budget_check = check_by_id["runtime.dependency_latency_budget"]
                self.assertEqual(latency_budget_check["status"], "warn")
                self.assertGreater(
                    latency_budget_check["details"]["slow_check_count"], 0
                )
            finally:
                if previous is None:
                    os.environ.pop("OPENMINION_HEALTH_CHECK_WARN_MS", None)
                else:
                    os.environ["OPENMINION_HEALTH_CHECK_WARN_MS"] = previous

    def test_health_operator_hints_reflect_metrics_token_requirement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "config.json"
            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.runtime.log_level = "ERROR"
            _csc_install_default_agent(config, provider="echo")
            config.storage.path = str(tmp_path / "state" / "health.db")
            save_config(config, str(config_path))

            previous = os.environ.get("OPENMINION_API_METRICS_TOKEN")
            os.environ["OPENMINION_API_METRICS_TOKEN"] = "operator-token"
            try:
                status, payload = dispatch_request("GET", "/health", str(config_path))
                self.assertEqual(int(status), 200)
                self.assertTrue(payload["ok"])
                self.assertTrue(payload["operator_hints"]["metrics"]["requires_token"])
            finally:
                if previous is None:
                    os.environ.pop("OPENMINION_API_METRICS_TOKEN", None)
                else:
                    os.environ["OPENMINION_API_METRICS_TOKEN"] = previous

    def test_health_brain_observability_supports_session_probe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "config.json"
            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.runtime.log_level = "ERROR"
            _csc_install_default_agent(config, provider="echo")
            config.storage.path = str(tmp_path / "state" / "health.db")
            save_config(config, str(config_path))

            brain_db_path = tmp_path / "state" / "brain" / "sessions.db"
            brain_db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(brain_db_path))
            try:
                conn.execute(
                    """
                    CREATE TABLE session_events (
                        session_id TEXT NOT NULL,
                        seq INTEGER NOT NULL,
                        event_type TEXT NOT NULL,
                        payload_json TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE working_state (
                        session_id TEXT NOT NULL,
                        version INTEGER NOT NULL,
                        state_inline_json TEXT
                    )
                    """
                )
                session_id = "mode-probe-session"
                rows = [
                    (
                        session_id,
                        1,
                        "llm.call.started",
                        json.dumps({"llm_call_id": "call-1"}),
                    ),
                    (
                        session_id,
                        2,
                        "context.manifest.created",
                        json.dumps({"llm_call_id": "call-1"}),
                    ),
                    (
                        session_id,
                        3,
                        "llm.call.completed",
                        json.dumps({"llm_call_id": "call-1"}),
                    ),
                    (session_id, 4, "brain.decide", json.dumps({"mode": "plan"})),
                ]
                conn.executemany(
                    """
                    INSERT INTO session_events(session_id, seq, event_type, payload_json)
                    VALUES (?, ?, ?, ?)
                    """,
                    rows,
                )
                conn.execute(
                    """
                    INSERT INTO working_state(session_id, version, state_inline_json)
                    VALUES (?, ?, ?)
                    """,
                    (session_id, 1, json.dumps({"mode": "guided"})),
                )
                conn.commit()
            finally:
                conn.close()

            status, payload = dispatch_request(
                "GET",
                "/health",
                str(config_path),
                query="session_id=mode-probe-session",
            )
            self.assertEqual(int(status), 200)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload.get("probe_session_id"), "mode-probe-session")
            check_by_id = {check["id"]: check for check in payload["checks"]}
            self.assertIn("runtime.brain.llm_mode_observability", check_by_id)
            observability = check_by_id["runtime.brain.llm_mode_observability"]
            self.assertEqual(observability["status"], "ok")
            details = observability.get("details", {})
            self.assertTrue(details.get("llm_pipeline_complete"))
            self.assertTrue(details.get("plan_mode_seen"))
            self.assertEqual(details.get("decision_mode_counts", {}).get("plan"), 1)
            self.assertEqual(details.get("brain_mode_counts", {}).get("guided"), 1)

    def test_health_brain_observability_allows_simple_qa_without_plan_mode(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "config.json"
            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.runtime.log_level = "ERROR"
            _csc_install_default_agent(config, provider="echo")
            config.storage.path = str(tmp_path / "state" / "health.db")
            save_config(config, str(config_path))

            brain_db_path = tmp_path / "state" / "brain" / "sessions.db"
            brain_db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(brain_db_path))
            try:
                conn.execute(
                    """
                    CREATE TABLE session_events (
                        session_id TEXT NOT NULL,
                        seq INTEGER NOT NULL,
                        event_type TEXT NOT NULL,
                        payload_json TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE working_state (
                        session_id TEXT NOT NULL,
                        version INTEGER NOT NULL,
                        state_inline_json TEXT
                    )
                    """
                )
                session_id = "simple-qa-session"
                rows = [
                    (
                        session_id,
                        1,
                        "llm.call.started",
                        json.dumps({"llm_call_id": "call-1"}),
                    ),
                    (
                        session_id,
                        2,
                        "context.manifest.created",
                        json.dumps({"llm_call_id": "call-1"}),
                    ),
                    (
                        session_id,
                        3,
                        "llm.call.completed",
                        json.dumps({"llm_call_id": "call-1"}),
                    ),
                    (session_id, 4, "brain.decide", json.dumps({"mode": "respond"})),
                ]
                conn.executemany(
                    """
                    INSERT INTO session_events(session_id, seq, event_type, payload_json)
                    VALUES (?, ?, ?, ?)
                    """,
                    rows,
                )
                conn.execute(
                    """
                    INSERT INTO working_state(session_id, version, state_inline_json)
                    VALUES (?, ?, ?)
                    """,
                    (session_id, 1, json.dumps({"mode": "guided"})),
                )
                conn.commit()
            finally:
                conn.close()

            status, payload = dispatch_request(
                "GET",
                "/health",
                str(config_path),
                query="session_id=simple-qa-session",
            )
            self.assertEqual(int(status), 200)
            self.assertTrue(payload["ok"])
            check_by_id = {check["id"]: check for check in payload["checks"]}
            observability = check_by_id["runtime.brain.llm_mode_observability"]
            self.assertEqual(observability["status"], "ok")
            details = observability.get("details", {})
            self.assertTrue(details.get("llm_pipeline_complete"))
            self.assertFalse(details.get("plan_mode_seen"))

    def test_health_brain_observability_includes_recursive_source_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "config.json"
            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.runtime.log_level = "ERROR"
            _csc_install_default_agent(config, provider="echo")
            config.storage.path = str(tmp_path / "state" / "health.db")
            save_config(config, str(config_path))

            brain_db_path = tmp_path / "state" / "brain" / "sessions.db"
            brain_db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(brain_db_path))
            try:
                conn.execute(
                    """
                    CREATE TABLE session_events (
                        session_id TEXT NOT NULL,
                        seq INTEGER NOT NULL,
                        event_type TEXT NOT NULL,
                        payload_json TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE working_state (
                        session_id TEXT NOT NULL,
                        version INTEGER NOT NULL,
                        state_inline_json TEXT
                    )
                    """
                )
                session_id = "autonomy-source-session"
                rows = [
                    (
                        session_id,
                        1,
                        "llm.call.started",
                        json.dumps({"llm_call_id": "call-1"}),
                    ),
                    (
                        session_id,
                        2,
                        "context.manifest.created",
                        json.dumps({"llm_call_id": "call-1"}),
                    ),
                    (
                        session_id,
                        3,
                        "llm.call.completed",
                        json.dumps({"llm_call_id": "call-1"}),
                    ),
                    (session_id, 4, "brain.decide", json.dumps({"mode": "respond"})),
                    (
                        session_id,
                        5,
                        "brain.recursive_turn.started",
                        json.dumps({"source": "real_rlm"}),
                    ),
                ]
                conn.executemany(
                    """
                    INSERT INTO session_events(session_id, seq, event_type, payload_json)
                    VALUES (?, ?, ?, ?)
                    """,
                    rows,
                )
                conn.execute(
                    """
                    INSERT INTO working_state(session_id, version, state_inline_json)
                    VALUES (?, ?, ?)
                    """,
                    (session_id, 1, json.dumps({"mode": "autonomous"})),
                )
                conn.commit()
            finally:
                conn.close()

            status, payload = dispatch_request(
                "GET",
                "/health",
                str(config_path),
                query="session_id=autonomy-source-session",
            )
            self.assertEqual(int(status), 200)
            self.assertTrue(payload["ok"])
            check_by_id = {check["id"]: check for check in payload["checks"]}
            observability = check_by_id["runtime.brain.llm_mode_observability"]
            self.assertEqual(observability["status"], "ok")
            details = observability.get("details", {})
            self.assertEqual(
                details.get("recursive_source_counts", {}).get("real_rlm"), 1
            )
            self.assertTrue(details.get("recursive_turn_seen"))

    def test_health_brain_observability_includes_llm_call_counts_by_purpose(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "config.json"
            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.runtime.log_level = "ERROR"
            _csc_install_default_agent(config, provider="echo")
            config.storage.path = str(tmp_path / "state" / "health.db")
            save_config(config, str(config_path))

            brain_db_path = tmp_path / "state" / "brain" / "sessions.db"
            brain_db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(brain_db_path))
            try:
                conn.execute(
                    """
                    CREATE TABLE session_events (
                        session_id TEXT NOT NULL,
                        seq INTEGER NOT NULL,
                        event_type TEXT NOT NULL,
                        payload_json TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE working_state (
                        session_id TEXT NOT NULL,
                        version INTEGER NOT NULL,
                        state_inline_json TEXT
                    )
                    """
                )
                session_id = "purpose-counts-session"
                rows = [
                    (
                        session_id,
                        1,
                        "llm.call.started",
                        json.dumps({"llm_call_id": "call-1"}),
                    ),
                    (
                        session_id,
                        2,
                        "context.manifest.created",
                        json.dumps({"llm_call_id": "call-1"}),
                    ),
                    (
                        session_id,
                        3,
                        "llm.call.completed",
                        json.dumps({"llm_call_id": "call-1", "purpose": "decide"}),
                    ),
                    (
                        session_id,
                        4,
                        "llm.call.completed",
                        json.dumps({"llm_call_id": "call-2", "purpose": "plan"}),
                    ),
                    (
                        session_id,
                        5,
                        "llm.call.completed",
                        json.dumps({"llm_call_id": "call-3", "purpose": "reflect"}),
                    ),
                    (
                        session_id,
                        6,
                        "llm.call.completed",
                        json.dumps(
                            {"llm_call_id": "call-4", "purpose": "respond_followup"}
                        ),
                    ),
                    (session_id, 7, "brain.decide", json.dumps({"mode": "plan"})),
                ]
                conn.executemany(
                    """
                    INSERT INTO session_events(session_id, seq, event_type, payload_json)
                    VALUES (?, ?, ?, ?)
                    """,
                    rows,
                )
                conn.execute(
                    """
                    INSERT INTO working_state(session_id, version, state_inline_json)
                    VALUES (?, ?, ?)
                    """,
                    (session_id, 1, json.dumps({"mode": "guided"})),
                )
                conn.commit()
            finally:
                conn.close()

            status, payload = dispatch_request(
                "GET",
                "/health",
                str(config_path),
                query="session_id=purpose-counts-session",
            )
            self.assertEqual(int(status), 200)
            self.assertTrue(payload["ok"])
            check_by_id = {check["id"]: check for check in payload["checks"]}
            observability = check_by_id["runtime.brain.llm_mode_observability"]
            self.assertEqual(observability["status"], "ok")
            details = observability.get("details", {})
            counts = details.get("llm_call_counts_by_purpose", {})
            self.assertEqual(counts.get("decide"), 1)
            self.assertEqual(counts.get("plan"), 1)
            self.assertEqual(counts.get("reflect"), 1)
            self.assertEqual(counts.get("follow_up"), 1)

    def test_health_brain_observability_strict_real_rlm_gate_fails_for_local_mock(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "config.json"
            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.runtime.log_level = "ERROR"
            _csc_install_default_agent(config, provider="echo")
            config.storage.path = str(tmp_path / "state" / "health.db")
            save_config(config, str(config_path))

            brain_db_path = tmp_path / "state" / "brain" / "sessions.db"
            brain_db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(brain_db_path))
            try:
                conn.execute(
                    """
                    CREATE TABLE session_events (
                        session_id TEXT NOT NULL,
                        seq INTEGER NOT NULL,
                        event_type TEXT NOT NULL,
                        payload_json TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE working_state (
                        session_id TEXT NOT NULL,
                        version INTEGER NOT NULL,
                        state_inline_json TEXT
                    )
                    """
                )
                session_id = "autonomy-source-mock-session"
                rows = [
                    (
                        session_id,
                        1,
                        "llm.call.started",
                        json.dumps({"llm_call_id": "call-1"}),
                    ),
                    (
                        session_id,
                        2,
                        "context.manifest.created",
                        json.dumps({"llm_call_id": "call-1"}),
                    ),
                    (
                        session_id,
                        3,
                        "llm.call.completed",
                        json.dumps({"llm_call_id": "call-1"}),
                    ),
                    (session_id, 4, "brain.decide", json.dumps({"mode": "respond"})),
                    (
                        session_id,
                        5,
                        "brain.recursive_turn.started",
                        json.dumps({"source": "local_mock"}),
                    ),
                ]
                conn.executemany(
                    """
                    INSERT INTO session_events(session_id, seq, event_type, payload_json)
                    VALUES (?, ?, ?, ?)
                    """,
                    rows,
                )
                conn.execute(
                    """
                    INSERT INTO working_state(session_id, version, state_inline_json)
                    VALUES (?, ?, ?)
                    """,
                    (session_id, 1, json.dumps({"mode": "autonomous"})),
                )
                conn.commit()
            finally:
                conn.close()

            prev = os.environ.get("OPENMINION_HEALTH_REQUIRE_REAL_RLM_AUTONOMY")
            os.environ["OPENMINION_HEALTH_REQUIRE_REAL_RLM_AUTONOMY"] = "1"
            try:
                status, payload = dispatch_request(
                    "GET",
                    "/health",
                    str(config_path),
                    query="session_id=autonomy-source-mock-session",
                )
            finally:
                if prev is None:
                    os.environ.pop("OPENMINION_HEALTH_REQUIRE_REAL_RLM_AUTONOMY", None)
                else:
                    os.environ["OPENMINION_HEALTH_REQUIRE_REAL_RLM_AUTONOMY"] = prev

            self.assertEqual(int(status), 503)
            self.assertFalse(payload["ok"])
            check_by_id = {check["id"]: check for check in payload["checks"]}
            observability = check_by_id["runtime.brain.llm_mode_observability"]
            self.assertEqual(observability["status"], "fail")
            details = observability.get("details", {})
            self.assertEqual(
                details.get("recursive_source_counts", {}).get("local_mock"), 1
            )
