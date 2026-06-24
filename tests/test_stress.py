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


class TestLongHorizonContinuityStress(unittest.TestCase):
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

    def test_20_turn_continuity_preserves_module_lane(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self._create_test_config(Path(tmp))
            runtime = APIRuntime.from_config_path(str(config_path))
            try:
                session_id = "stress-test-20-turns"

                initial_runtime_info = extract_runtime_info_from_api_runtime(runtime)
                assert_module_lane(
                    runtime_mode=initial_runtime_info["runtime_mode"],
                    fallback_reason=initial_runtime_info["fallback_reason"],
                    source="stress_test_initial",
                    strict=True,
                )

                for turn_num in range(20):
                    run_turn(
                        str(config_path),
                        {
                            "message": f"Turn {turn_num}: Remember this is a test message number {turn_num}",
                            "session_id": session_id,
                            "agent_id": "test-agent",
                        },
                    )

                    runtime_info = extract_runtime_info_from_api_runtime(runtime)
                    assert_module_lane(
                        runtime_mode=runtime_info["runtime_mode"],
                        fallback_reason=runtime_info["fallback_reason"],
                        source=f"stress_turn_{turn_num}",
                        strict=True,
                    )

                    self.assertNotEqual(
                        runtime_info["runtime_mode"],
                        "legacy",
                        f"Turn {turn_num} should not fall back to legacy",
                    )

            finally:
                runtime.close()

    def test_multi_turn_with_memory_cue_preserves_module_lane(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self._create_test_config(Path(tmp))
            runtime = APIRuntime.from_config_path(str(config_path))
            try:
                session_id = "stress-test-memory-cue"

                run_turn(
                    str(config_path),
                    {
                        "message": "Please remember the keyword: AZURE PHOENIX",
                        "session_id": session_id,
                        "agent_id": "test-agent",
                    },
                )

                for i in range(10):
                    run_turn(
                        str(config_path),
                        {
                            "message": f"What was the keyword I asked you to remember? (turn {i})",
                            "session_id": session_id,
                            "agent_id": "test-agent",
                        },
                    )

                    runtime_info = extract_runtime_info_from_api_runtime(runtime)
                    assert_module_lane(
                        runtime_mode=runtime_info["runtime_mode"],
                        fallback_reason=runtime_info["fallback_reason"],
                        source=f"memory_cue_turn_{i}",
                        strict=True,
                    )

            finally:
                runtime.close()

    def test_context_compression_preserves_module_lane(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self._create_test_config(Path(tmp))
            runtime = APIRuntime.from_config_path(str(config_path))
            try:
                session_id = "stress-test-compression"

                for turn_num in range(25):
                    run_turn(
                        str(config_path),
                        {
                            "message": f"Long message number {turn_num}. " * 20,
                            "session_id": session_id,
                            "agent_id": "test-agent",
                        },
                    )

                    runtime_info = extract_runtime_info_from_api_runtime(runtime)
                    assert_module_lane(
                        runtime_mode=runtime_info["runtime_mode"],
                        fallback_reason=runtime_info["fallback_reason"],
                        source=f"compression_turn_{turn_num}",
                        strict=True,
                    )

            finally:
                runtime.close()

    def test_context_retrieval_preserves_module_lane(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self._create_test_config(Path(tmp))
            runtime = APIRuntime.from_config_path(str(config_path))
            try:
                session_id = "stress-test-retrieval"

                run_turn(
                    str(config_path),
                    {
                        "message": "Important fact: The capital of France is Paris. Remember this.",
                        "session_id": session_id,
                        "agent_id": "test-agent",
                    },
                )

                for i in range(15):
                    run_turn(
                        str(config_path),
                        {
                            "message": f"Tell me the important fact from earlier. (query {i})",
                            "session_id": session_id,
                            "agent_id": "test-agent",
                        },
                    )

                    runtime_info = extract_runtime_info_from_api_runtime(runtime)
                    assert_module_lane(
                        runtime_mode=runtime_info["runtime_mode"],
                        fallback_reason=runtime_info["fallback_reason"],
                        source=f"retrieval_turn_{i}",
                        strict=True,
                    )

            finally:
                runtime.close()

    def test_no_duplicate_legacy_context_sections(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self._create_test_config(Path(tmp))
            runtime = APIRuntime.from_config_path(str(config_path))
            try:
                session_id = "stress-test-no-dupe"

                for turn_num in range(12):
                    run_turn(
                        str(config_path),
                        {
                            "message": f"Message {turn_num} with unique identifier U{turn_num}",
                            "session_id": session_id,
                            "agent_id": "test-agent",
                        },
                    )

                    runtime_info = extract_runtime_info_from_api_runtime(runtime)

                    self.assertIn(
                        runtime_info["runtime_mode"],
                        ["brain", "brain-bridge", "bridge"],
                        f"Turn {turn_num} should be in module lane",
                    )

            finally:
                runtime.close()


class TestContinuityWithDifferentSessions(unittest.TestCase):
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

    def test_multiple_sessions_maintain_module_lane(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self._create_test_config(Path(tmp))
            runtime = APIRuntime.from_config_path(str(config_path))
            try:
                session_ids = ["session-a", "session-b", "session-c"]

                for session_id in session_ids:
                    for turn in range(5):
                        run_turn(
                            str(config_path),
                            {
                                "message": f"Message for {session_id} turn {turn}",
                                "session_id": session_id,
                                "agent_id": "test-agent",
                            },
                        )

                        runtime_info = extract_runtime_info_from_api_runtime(runtime)
                        assert_module_lane(
                            runtime_mode=runtime_info["runtime_mode"],
                            fallback_reason=runtime_info["fallback_reason"],
                            source=f"multi_session_{session_id}_turn_{turn}",
                            strict=True,
                        )

            finally:
                runtime.close()
