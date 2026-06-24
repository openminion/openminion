import io
import json
import sys
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch
from tests._csc_fixtures import _csc_install_default_agent


from openminion.api.server import dispatch_request
from openminion.base.config import OpenMinionConfig, save_config
from openminion.cli.commands.doctor import run_doctor
from openminion.services.health.probes import ProbeResult


class HealthDoctorProbeParityTests(unittest.TestCase):
    def setUp(self) -> None:
        current = sys.modules.get("openminion.cli.commands.doctor")
        if current is not None:
            globals()["doctor_command"] = current
            globals()["run_doctor"] = current.run_doctor

    def test_storage_failure_parity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self._write_config(
                tmp,
                provider="echo",
                storage_path="/dev/null/openminion-health.db",
            )
            _, health_payload = self._run_health(config_path)
            _, doctor_payload = self._run_doctor(config_path)

            health_storage = _check_by_id(health_payload)["storage.ready"]
            doctor_storage = _check_by_id(doctor_payload)["storage.ready"]
            self.assertEqual(health_storage["status"], "fail")
            self.assertEqual(doctor_storage["status"], "fail")
            storage_snapshot = next(
                item
                for item in health_payload["normalized_health_snapshot"]["components"]
                if item["component"]["component_kind"] == "storage_backend"
            )
            self.assertEqual(storage_snapshot["health_state"], "failed")
            self.assertEqual(
                doctor_storage["target_component"]["component_kind"],
                "storage_backend",
            )

    def test_unsupported_provider_parity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self._write_config(tmp, provider="unknown-provider")
            _, health_payload = self._run_health(config_path)
            _, doctor_payload = self._run_doctor(config_path)

            health_provider = _check_by_id(health_payload)["provider.supported"]
            doctor_provider = _check_by_id(doctor_payload)["provider.supported"]
            self.assertEqual(health_provider["status"], "fail")
            self.assertEqual(doctor_provider["status"], "fail")
            provider_snapshot = next(
                item
                for item in health_payload["normalized_health_snapshot"]["components"]
                if item["component"]["component_kind"] == "provider_binding"
            )
            self.assertEqual(provider_snapshot["health_state"], "failed")
            self.assertEqual(
                doctor_provider["target_component"]["component_kind"],
                "provider_binding",
            )
            self.assertEqual(
                doctor_provider["related_probe_ids"], ["provider.supported"]
            )

    def test_runtime_bootstrap_failure_parity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self._write_config(tmp, provider="echo")
            forced_failure = ProbeResult(
                id="runtime.bootstrap",
                status="fail",
                message="Runtime bootstrap failed: forced by test",
            )
            with (
                patch(
                    "openminion.services.health.service.probe_runtime_bootstrap",
                    return_value=forced_failure,
                ),
                patch(
                    "openminion.cli.commands.doctor.probe_runtime_bootstrap",
                    return_value=forced_failure,
                ),
            ):
                _, health_payload = self._run_health(config_path)
                _, doctor_payload = self._run_doctor(config_path)

            health_runtime = _check_by_id(health_payload)["runtime.bootstrap"]
            doctor_runtime = _check_by_id(doctor_payload)["runtime.bootstrap"]
            self.assertEqual(health_runtime["status"], "fail")
            self.assertEqual(doctor_runtime["status"], "fail")
            runtime_snapshot = next(
                item
                for item in health_payload["normalized_health_snapshot"]["components"]
                if item["component"]["component_id"] == "primary"
            )
            self.assertEqual(runtime_snapshot["health_state"], "failed")
            self.assertEqual(
                doctor_runtime["target_component"]["component_kind"],
                "runtime_manager",
            )

    def _write_config(
        self,
        tmp: str,
        *,
        provider: str,
        storage_path: str | None = None,
    ) -> Path:
        config_path = Path(tmp) / "config.json"
        config = OpenMinionConfig()
        _csc_install_default_agent(config)  # type: ignore[attr-defined]
        config.runtime.log_level = "ERROR"
        _csc_install_default_agent(config, provider=provider)
        config.storage.path = storage_path or str(Path(tmp) / "state" / "health.db")
        save_config(config, str(config_path))
        return config_path

    def _run_health(self, config_path: Path) -> tuple[int, dict]:
        status, payload = dispatch_request("GET", "/health", str(config_path))
        return int(status), payload

    def _run_doctor(self, config_path: Path) -> tuple[int, dict]:
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
            code = run_doctor(args)
        return int(code), json.loads(buffer.getvalue())


def _check_by_id(payload: dict) -> dict:
    return {check["id"]: check for check in payload["checks"]}
