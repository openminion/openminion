from __future__ import annotations
from tests._csc_fixtures import _csc_install_default_agent


import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.helpers import (
    NoLegacyTestContext,
    assert_module_lane,
    extract_runtime_info_from_api_runtime,
)

from openminion.base.config import OpenMinionConfig, save_config
from openminion.api.runtime import APIRuntime
from openminion.api.turns import run_turn


class TestInProcessChatNoLegacy(unittest.TestCase):
    def setUp(self):
        self.env_patcher = mock.patch.dict(
            os.environ,
            {
                "OPENMINION_AGENT_RUNTIME_MODE": "brain",
            },
            clear=False,
        )
        self.env_patcher.start()

    def tearDown(self):
        self.env_patcher.stop()

    def _create_test_config(self, tmp_path: Path) -> Path:
        config_path = tmp_path / "config.json"
        config = OpenMinionConfig()
        _csc_install_default_agent(config)  # type: ignore[attr-defined]
        config.runtime.log_level = "ERROR"
        _csc_install_default_agent(config, provider="echo")
        config.storage.path = str(tmp_path / "state" / "api.db")
        save_config(config, str(config_path))
        return config_path

    def test_in_process_chat_default_mode_is_module(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self._create_test_config(Path(tmp))
            runtime = APIRuntime.from_config_path(str(config_path))
            try:
                runtime_info = extract_runtime_info_from_api_runtime(runtime)

                assert_module_lane(
                    runtime_mode=runtime_info["runtime_mode"],
                    fallback_reason=runtime_info["fallback_reason"],
                    source="in_process_chat_default",
                    strict=True,
                )

                self.assertNotEqual(
                    runtime_info["runtime_mode"],
                    "legacy",
                    "Default mode should not be legacy",
                )
            finally:
                runtime.close()

    def test_turn_api_uses_request_orchestrator_adapter(self):
        import openminion.api.turns as api_turns
        import openminion.services.lifecycle.request_orchestrator as orchestrator

        self.assertIs(
            api_turns._run_turn,
            orchestrator.run_turn,
            "API turn adapter must delegate to the request orchestrator",
        )

    def test_in_process_chat_turn_preserves_module_lane(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self._create_test_config(Path(tmp))
            runtime = APIRuntime.from_config_path(str(config_path))
            try:
                result = run_turn(
                    str(config_path),
                    {
                        "message": "test message for lane validation",
                        "session_id": "test-session-001",
                        "agent_id": "openminion",
                    },
                    runtime=runtime,
                )

                self.assertIsNotNone(result)
                self.assertIn("run_id", result)

                runtime_info = extract_runtime_info_from_api_runtime(runtime)
                assert_module_lane(
                    runtime_mode=runtime_info["runtime_mode"],
                    fallback_reason=runtime_info["fallback_reason"],
                    source="in_process_chat_after_turn",
                    strict=True,
                )
            finally:
                runtime.close()

    def test_in_process_chat_multiple_turns_no_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self._create_test_config(Path(tmp))
            runtime = APIRuntime.from_config_path(str(config_path))
            try:
                for i in range(3):
                    result = run_turn(
                        str(config_path),
                        {
                            "message": f"turn {i} message",
                            "session_id": "multi-turn-session",
                            "agent_id": "openminion",
                        },
                        runtime=runtime,
                    )
                    self.assertIsNotNone(result)

                runtime_info = extract_runtime_info_from_api_runtime(runtime)
                assert_module_lane(
                    runtime_mode=runtime_info["runtime_mode"],
                    fallback_reason=runtime_info["fallback_reason"],
                    source="in_process_chat_multi_turn",
                    strict=True,
                )
            finally:
                runtime.close()

    def test_in_process_chat_tool_invocation_module_lane(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self._create_test_config(Path(tmp))
            runtime = APIRuntime.from_config_path(str(config_path))
            try:
                tool_names = [spec.name for spec in runtime.tools.provider_specs()]

                self.assertGreater(len(tool_names), 0, "No tools available in registry")

                runtime_info = extract_runtime_info_from_api_runtime(runtime)
                assert_module_lane(
                    runtime_mode=runtime_info["runtime_mode"],
                    fallback_reason=runtime_info["fallback_reason"],
                    source="in_process_chat_tools",
                    strict=True,
                )
            finally:
                runtime.close()

    def test_no_legacy_warning_in_logs(self):
        with NoLegacyTestContext(source="log_validation", strict=True):
            with tempfile.TemporaryDirectory() as tmp:
                config_path = self._create_test_config(Path(tmp))
                runtime = APIRuntime.from_config_path(str(config_path))
                try:
                    runtime_info = extract_runtime_info_from_api_runtime(runtime)

                    self.assertIsNone(
                        runtime_info["fallback_reason"] or None,
                        f"Unexpected fallback reason: {runtime_info['fallback_reason']}",
                    )

                    self.assertEqual(
                        runtime_info["runtime_mode"],
                        "brain",
                        "Expected brain mode in default configuration",
                    )
                finally:
                    runtime.close()


class TestInProcessChatNegativePath(unittest.TestCase):
    def test_strict_mode_fails_fast_on_brain_import_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.runtime.log_level = "ERROR"
            _csc_install_default_agent(config, provider="echo")
            config.storage.path = str(Path(tmp) / "state" / "api.db")
            save_config(config, str(config_path))

            env_vars = {
                "OPENMINION_AGENT_RUNTIME_MODE": "brain",
            }

            with mock.patch.dict(os.environ, env_vars, clear=False):
                try:
                    runtime = APIRuntime.from_config_path(str(config_path))
                    runtime_info = extract_runtime_info_from_api_runtime(runtime)

                    self.assertEqual(runtime_info["runtime_mode"], "brain")
                    runtime.close()
                except RuntimeError as e:
                    self.assertIn(
                        "brain",
                        str(e).lower(),
                        "Error should mention brain mode failure",
                    )
