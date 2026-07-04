import tempfile
import unittest
import os
from pathlib import Path
from tests._csc_fixtures import _csc_install_default_agent


from openminion.api.server import dispatch_request
from openminion.base.config import OpenMinionConfig, save_config


class APIV1RuntimeTests(unittest.TestCase):
    def test_v1_health_and_agents(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["OPENMINION_DATA_ROOT"] = str(Path(tmp) / ".openminion")
            config_path = _write_echo_config(Path(tmp))

            health_status, health_payload = dispatch_request(
                "GET", "/v1/health", str(config_path)
            )
            self.assertEqual(int(health_status), 200)
            self.assertTrue(health_payload["ok"])
            self.assertIn("daemon", health_payload)
            self.assertEqual(
                health_payload["daemon"]["config_path"],
                str(config_path.resolve()),
            )

            agents_status, agents_payload = dispatch_request(
                "GET", "/v1/agents", str(config_path)
            )
            self.assertEqual(int(agents_status), 200)
            self.assertTrue(agents_payload["ok"])
            self.assertIn("agents", agents_payload)
            self.assertIn("registry_agent_ids", agents_payload)
            self.assertIn("openminion", agents_payload["registry_agent_ids"])

    def test_v1_tools_list_schema_and_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["OPENMINION_DATA_ROOT"] = str(Path(tmp) / ".openminion")
            config_path = _write_echo_config(Path(tmp))

            list_status, list_payload = dispatch_request(
                "GET", "/v1/tools", str(config_path)
            )
            self.assertEqual(int(list_status), 200)
            self.assertTrue(list_payload["ok"])
            tool_names = {item["name"] for item in list_payload["tools"]}
            self.assertIn("weather", tool_names)

            schema_status, schema_payload = dispatch_request(
                "GET",
                "/v1/tools/weather/schema",
                str(config_path),
            )
            self.assertEqual(int(schema_status), 200)
            self.assertTrue(schema_payload["ok"])
            self.assertEqual(schema_payload["tool"]["name"], "weather")

            run_status, run_payload = dispatch_request(
                "POST",
                "/v1/tools/weather/run",
                str(config_path),
                body={"arguments": {"city": "Tokyo"}, "session_id": "tools-session"},
            )
            self.assertEqual(int(run_status), 200)
            self.assertTrue(run_payload["ok"])
            self.assertTrue(run_payload["trace_id"])
            self.assertTrue(run_payload["artifact_refs"])
            self.assertEqual(run_payload["tool"]["name"], "weather")

    def test_v1_runtime_capabilities_and_posture(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["OPENMINION_DATA_ROOT"] = str(Path(tmp) / ".openminion")
            config_path = _write_echo_config(Path(tmp))

            capabilities_status, capabilities_payload = dispatch_request(
                "GET", "/v1/runtime/capabilities", str(config_path)
            )
            self.assertEqual(int(capabilities_status), 200)
            self.assertTrue(capabilities_payload["ok"])
            self.assertEqual(
                capabilities_payload["capabilities"]["providers"]["selected"], "echo"
            )
            self.assertGreater(
                capabilities_payload["capabilities"]["tools"]["counts"]["total"], 0
            )

            runtime_status, runtime_payload = dispatch_request(
                "GET", "/v1/runtime/posture", str(config_path)
            )
            self.assertEqual(int(runtime_status), 200)
            self.assertTrue(runtime_payload["ok"])
            self.assertEqual(runtime_payload["runtime"]["runtime_mode"], "brain")
            self.assertTrue(runtime_payload["runtime"]["brain_bridge_active"])
            self.assertTrue(runtime_payload["runtime"]["canonical_turn_path"])

    def test_v1_tool_unknown_returns_404(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["OPENMINION_DATA_ROOT"] = str(Path(tmp) / ".openminion")
            config_path = _write_echo_config(Path(tmp))

            status, payload = dispatch_request(
                "GET",
                "/v1/tools/missing_tool/schema",
                str(config_path),
            )
            self.assertEqual(int(status), 404)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["error"]["code"], "tool_not_found")

    def test_v1_turn_returns_trace_and_turn(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["OPENMINION_DATA_ROOT"] = str(Path(tmp) / ".openminion")
            config_path = _write_echo_config(Path(tmp))
            status, payload = dispatch_request(
                "POST",
                "/v1/turn",
                str(config_path),
                body={
                    "agent_id": "openminion",
                    "session_id": "v1-turn-session",
                    "input_text": "hello v1",
                    "channel": "console",
                    "target": "api-user",
                },
            )
            self.assertEqual(int(status), 200)
            self.assertTrue(payload["ok"])
            turn = payload["turn"]
            self.assertTrue(turn["trace_id"])
            self.assertIn("final_text", turn)
            self.assertIn("artifacts", turn)

    def test_v1_debug_endpoints_disabled_by_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["OPENMINION_DATA_ROOT"] = str(Path(tmp) / ".openminion")
            config_path = Path(tmp) / "config.json"
            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.runtime.log_level = "ERROR"
            _csc_install_default_agent(config, provider="echo")
            config.runtime.debug_enabled = False
            config.storage.path = str(Path(tmp) / "state" / "api-v1-debug.db")
            save_config(config, str(config_path))

            status, payload = dispatch_request(
                "GET", "/v1/debug/modules", str(config_path)
            )
            self.assertEqual(int(status), 403)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["error"]["code"], "debug_disabled")


def _write_echo_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "config.json"
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    os.environ["OPENMINION_DATA_ROOT"] = str(tmp_path / ".openminion")
    config.runtime.log_level = "ERROR"
    _csc_install_default_agent(config, provider="echo")
    config.storage.path = str(tmp_path / "state" / "api-v1.db")
    old_data_root = os.environ.get("OPENMINION_DATA_ROOT")
    try:
        os.environ["OPENMINION_DATA_ROOT"] = str(tmp_path / ".openminion")
        save_config(config, str(config_path))
    finally:
        if old_data_root is None:
            os.environ.pop("OPENMINION_DATA_ROOT", None)
        else:
            os.environ["OPENMINION_DATA_ROOT"] = old_data_root
    return config_path
