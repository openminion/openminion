import io
import tempfile
import unittest
from contextlib import redirect_stdout
import os
from pathlib import Path
from tests._csc_fixtures import _csc_install_default_agent


from openminion.api.server import dispatch_request
from openminion.base.config import AgentProfileConfig, OpenMinionConfig, save_config

# Set soft enforcement mode for tests


class APITurnsTests(unittest.TestCase):
    def test_post_turns_returns_turn_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["OPENMINION_DATA_ROOT"] = str(Path(tmp) / ".openminion")
            config_path = _write_echo_config(Path(tmp))
            with redirect_stdout(io.StringIO()):
                status, payload = dispatch_request(
                    "POST",
                    "/turns",
                    str(config_path),
                    body={
                        "message": "hello from api",
                        "channel": "console",
                        "target": "api-user",
                    },
                )

            self.assertEqual(int(status), 200)
            self.assertTrue(payload["ok"])
            turn = payload["turn"]
            self.assertEqual(turn["channel"], "console")
            self.assertEqual(turn["target"], "api-user")
            self.assertTrue(str(turn["body"]).startswith("openminion: "))
            self.assertTrue(turn["session_id"])

    def test_post_turns_idempotency_reuses_completed_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["OPENMINION_DATA_ROOT"] = str(Path(tmp) / ".openminion")
            config_path = _write_echo_config(Path(tmp))
            request_body = {
                "message": "idempotent turn",
                "channel": "console",
                "target": "api-user",
                "idempotency_key": "api-turn-1",
            }

            with redirect_stdout(io.StringIO()):
                first_status, first_payload = dispatch_request(
                    "POST",
                    "/turns",
                    str(config_path),
                    body=request_body,
                )
                second_status, second_payload = dispatch_request(
                    "POST",
                    "/turns",
                    str(config_path),
                    body=request_body,
                )

            self.assertEqual(int(first_status), 200)
            self.assertEqual(int(second_status), 200)
            self.assertEqual(first_payload["turn"]["id"], second_payload["turn"]["id"])

    def test_post_turns_agent_id_uses_agent_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["OPENMINION_DATA_ROOT"] = str(Path(tmp) / ".openminion")
            config_path = _write_echo_config(Path(tmp))
            status, payload = dispatch_request(
                "POST",
                "/turns",
                str(config_path),
                body={
                    "message": "agent-specific hello",
                    "agent_id": "research",
                    "target": "api-user",
                },
            )

            self.assertEqual(int(status), 200)
            self.assertTrue(payload["ok"])
            turn = payload["turn"]
            self.assertEqual(turn["agent_id"], "research")
            self.assertIn("agent-specific hello", str(turn["body"]))

    def test_post_turns_missing_message_returns_bad_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["OPENMINION_DATA_ROOT"] = str(Path(tmp) / ".openminion")
            config_path = _write_echo_config(Path(tmp))
            status, payload = dispatch_request(
                "POST",
                "/turns",
                str(config_path),
                body={"message": "   "},
            )
            self.assertEqual(int(status), 400)
            self.assertFalse(payload["ok"])
            _assert_error_envelope(payload, code="invalid_request")

    def test_post_turns_missing_body_returns_bad_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["OPENMINION_DATA_ROOT"] = str(Path(tmp) / ".openminion")
            config_path = _write_echo_config(Path(tmp))
            status, payload = dispatch_request(
                "POST",
                "/turns",
                str(config_path),
                body=None,
            )
            self.assertEqual(int(status), 400)
            self.assertFalse(payload["ok"])
            _assert_error_envelope(payload, code="invalid_request")

    def test_post_turns_provider_failure_returns_500(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["OPENMINION_DATA_ROOT"] = str(Path(tmp) / ".openminion")
            config_path = _write_openai_missing_key_config(Path(tmp))
            status, payload = dispatch_request(
                "POST",
                "/turns",
                str(config_path),
                body={"message": "hello"},
            )
            self.assertEqual(int(status), 500)
            self.assertFalse(payload["ok"])
            _assert_error_envelope(payload, code="turn_failed")


def _write_echo_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "config.json"
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    # Set OPENMINION_DATA_ROOT to tmp for test isolation
    os.environ["OPENMINION_DATA_ROOT"] = str(tmp_path / ".openminion")
    config.runtime.log_level = "ERROR"
    config.agents["openminion"] = AgentProfileConfig(
        name="openminion",
        provider="echo",
        default_channel="console",
    )
    config.agents["research"] = AgentProfileConfig(
        name="research",
        provider="echo",
        default_channel="console",
    )
    config.default_agent = "openminion"
    config.storage.path = str(tmp_path / "state" / "api.db")
    save_config(config, str(config_path))
    return config_path


def _write_openai_missing_key_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "config.json"
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    # Set OPENMINION_DATA_ROOT to tmp for test isolation
    os.environ["OPENMINION_DATA_ROOT"] = str(tmp_path / ".openminion")
    config.runtime.log_level = "ERROR"
    _csc_install_default_agent(config, provider="openai")
    config.providers.openai.api_key = ""
    config.providers.openai.api_key_env = "OPENMINION_TEST_OPENAI_KEY_MISSING"
    config.storage.path = str(tmp_path / "state" / "api.db")
    # Set OPENMINION_DATA_ROOT for path validation
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


def _assert_error_envelope(payload: dict, *, code: str) -> None:
    error = payload["error"]
    assert isinstance(error, dict)
    if error.get("code") != code:
        raise AssertionError(f"Expected error code {code}, got {error.get('code')}")
    if "message" not in error:
        raise AssertionError("Error envelope missing 'message'")
    if "details" not in error:
        raise AssertionError("Error envelope missing 'details'")
    if "retryable" not in error:
        raise AssertionError("Error envelope missing 'retryable'")
    if "retry_after_ms" not in error:
        raise AssertionError("Error envelope missing 'retry_after_ms'")
