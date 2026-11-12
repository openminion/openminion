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


class CortensorDoctorEnvAndPoolParityTests(unittest.TestCase):
    def test_probe_provider_session_honors_cortensor_env_overrides(self) -> None:
        from openminion.services.health.probes import probe_provider_session

        cfg = OpenMinionConfig()
        cfg.providers.cortensor.session_id = 0
        with patch.dict(
            "os.environ",
            {
                "CORTENSOR_API_MODE": "",
                "CORTENSOR_API_URL": "",
                "CORTENSOR_SESSION_ID": "",
                "CORTENSOR_SESSION_IDS": "",
                "CORTENSOR_DEDICATED_SESSION_IDS": "",
                "CORTENSOR_EPHEMERAL_SESSION_IDS": "",
            },
            clear=False,
        ):
            missing_probe = probe_provider_session(cfg, "cortensor")
            self.assertIsNotNone(missing_probe)
            assert missing_probe is not None
            self.assertEqual(missing_probe.status, "fail")

            with patch.dict("os.environ", {"CORTENSOR_SESSION_ID": "42"}, clear=False):
                router_probe = probe_provider_session(cfg, "cortensor")
            self.assertIsNotNone(router_probe)
            assert router_probe is not None
            self.assertEqual(router_probe.status, "ok")
            self.assertIn("42", router_probe.message)

            with patch.dict(
                "os.environ",
                {"CORTENSOR_DEDICATED_SESSION_IDS": "7, 9"},
                clear=False,
            ):
                dedicated_probe = probe_provider_session(cfg, "cortensor")
            self.assertIsNotNone(dedicated_probe)
            assert dedicated_probe is not None
            self.assertEqual(dedicated_probe.status, "ok")
            self.assertIn("7, 9", dedicated_probe.message)

            with patch.dict(
                "os.environ",
                {
                    "CORTENSOR_API_MODE": "openai_chat",
                    "CORTENSOR_API_URL": "https://api.cortensor.app/v1",
                },
                clear=False,
            ):
                portal_probe = probe_provider_session(cfg, "cortensor")
            self.assertIsNone(portal_probe)

    def test_cortensor_openai_chat_mode_forwards_configured_http_client(self) -> None:
        from openminion.modules.llm.providers.cortensor.adapter import CortensorProvider
        from openminion.modules.llm.schemas import LLMRequest, Message

        provider = CortensorProvider()
        try:
            req = LLMRequest(
                provider="cortensor",
                model="gpt-oss-20b",
                messages=[Message(role="user", content="ping")],
            )
            with patch.object(
                provider,
                "_post_with_retries",
                return_value={"choices": [{"message": {"content": "pong"}}]},
            ) as post_mock:
                provider.complete(
                    req,
                    config={
                        "api_mode": "openai_chat",
                        "base_url": "https://api.cortensor.app/v1",
                        "http_connection_reuse_enabled": True,
                    },
                )
                self.assertIs(
                    post_mock.call_args.kwargs.get("http_client"),
                    provider._http_client,
                )
                provider.complete(
                    req,
                    config={
                        "api_mode": "openai_chat",
                        "base_url": "https://api.cortensor.app/v1",
                        "http_connection_reuse_enabled": False,
                    },
                )
                self.assertIsNone(post_mock.call_args.kwargs.get("http_client"))
        finally:
            provider.close()
