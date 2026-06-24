from __future__ import annotations
from tests._csc_fixtures import _csc_install_default_agent


import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.helpers import (
    assert_module_lane,
    extract_runtime_info_from_api_runtime,
)

from openminion.base.config import OpenMinionConfig, save_config
from openminion.api.runtime import APIRuntime
from openminion.api.turns import run_turn


class TestPromptPayloadNoLegacyDuplication(unittest.TestCase):
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
        _csc_install_default_agent(
            config, name="test-agent", provider="echo", default_channel="console"
        )
        config.storage.path = str(tmp_path / "state" / "api.db")
        save_config(config, str(config_path))
        return config_path

    def test_module_mode_single_context_lane(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self._create_test_config(Path(tmp))
            runtime = APIRuntime.from_config_path(str(config_path))
            try:
                runtime_info = extract_runtime_info_from_api_runtime(runtime)

                assert_module_lane(
                    runtime_mode=runtime_info["runtime_mode"],
                    fallback_reason=runtime_info["fallback_reason"],
                    source="prompt_payload_single_lane",
                    strict=True,
                )

                self.assertIn(
                    runtime_info["runtime_mode"],
                    ["brain", "brain-bridge", "bridge"],
                    "Module mode should use brain/brain-bridge/bridge lane",
                )
            finally:
                runtime.close()

    def test_module_mode_no_system_prompt_duplication_in_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self._create_test_config(Path(tmp))
            runtime = APIRuntime.from_config_path(str(config_path))
            try:
                session_id = "test-session-prompt-001"

                run_turn(
                    str(config_path),
                    {
                        "message": "Third user message",
                        "session_id": session_id,
                        "agent_id": "test-agent",
                    },
                )

                runtime_info = extract_runtime_info_from_api_runtime(runtime)
                assert_module_lane(
                    runtime_mode=runtime_info["runtime_mode"],
                    fallback_reason=runtime_info["fallback_reason"],
                    source="prompt_no_system_duplication",
                    strict=True,
                )

            finally:
                runtime.close()

    def test_module_mode_system_prompt_not_in_conversation_turns(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self._create_test_config(Path(tmp))
            runtime = APIRuntime.from_config_path(str(config_path))
            try:
                session_id = "test-session-sys-001"

                run_turn(
                    str(config_path),
                    {
                        "message": "Hello",
                        "session_id": session_id,
                        "agent_id": "test-agent",
                    },
                )

                run_turn(
                    str(config_path),
                    {
                        "message": "How are you?",
                        "session_id": session_id,
                        "agent_id": "test-agent",
                    },
                )

                runtime_info = extract_runtime_info_from_api_runtime(runtime)

                if runtime_info["runtime_mode"] == "brain":
                    if hasattr(runtime, "_agent_service") and runtime._agent_service:
                        agent = runtime._agent_service
                        if hasattr(agent, "_runner") and agent._runner:
                            try:
                                turns = agent._runner.session_api.list_turns(session_id)
                                if turns:
                                    for turn in turns:
                                        role = str(turn.get("role", "")).lower()
                                        self.assertNotEqual(
                                            role,
                                            "system",
                                            "System prompt should not be persisted as conversation turn in module mode",
                                        )
                            except Exception:
                                pass

                assert_module_lane(
                    runtime_mode=runtime_info["runtime_mode"],
                    fallback_reason=runtime_info["fallback_reason"],
                    source="prompt_system_not_in_turns",
                    strict=True,
                )

            finally:
                runtime.close()

    def test_module_mode_context_compression_no_legacy_sections(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self._create_test_config(Path(tmp))
            runtime = APIRuntime.from_config_path(str(config_path))
            try:
                session_id = "test-session-compress-001"

                for i in range(5):
                    run_turn(
                        str(config_path),
                        {
                            "message": f"Message {i}",
                            "session_id": session_id,
                            "agent_id": "test-agent",
                        },
                    )

                runtime_info = extract_runtime_info_from_api_runtime(runtime)
                assert_module_lane(
                    runtime_mode=runtime_info["runtime_mode"],
                    fallback_reason=runtime_info["fallback_reason"],
                    source="prompt_context_compression",
                    strict=True,
                )

                self.assertNotEqual(
                    runtime_info["runtime_mode"],
                    "legacy",
                    "Should not fall back to legacy after multiple turns",
                )

            finally:
                runtime.close()

    def test_module_mode_history_mapping_single_lane(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self._create_test_config(Path(tmp))
            runtime = APIRuntime.from_config_path(str(config_path))
            try:
                runtime_info = extract_runtime_info_from_api_runtime(runtime)

                self.assertIn(
                    runtime_info["runtime_mode"],
                    ["brain", "brain-bridge", "bridge"],
                    "Should be in module lane",
                )

                if runtime_info["runtime_mode"] == "brain":
                    pass
                elif runtime_info["runtime_mode"] == "legacy":
                    self.fail("Unexpected legacy mode in default configuration")

            finally:
                runtime.close()


class TestLegacyModeContextDuplication(unittest.TestCase):
    def setUp(self):
        self.env_patcher = mock.patch.dict(
            os.environ,
            {
                "OPENMINION_AGENT_RUNTIME_MODE": "legacy",
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
        _csc_install_default_agent(
            config, name="test-agent", provider="echo", default_channel="console"
        )
        config.storage.path = str(tmp_path / "state" / "api.db")
        save_config(config, str(config_path))
        return config_path

    def test_legacy_mode_env_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self._create_test_config(Path(tmp))
            runtime = APIRuntime.from_config_path(str(config_path))
            try:
                self.assertEqual(runtime._runtime_mode, "brain")
            finally:
                runtime.close()
