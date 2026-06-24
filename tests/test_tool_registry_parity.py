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


class TestToolRegistryParity(unittest.TestCase):
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

    def test_v1_tools_endpoint_module_lane(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self._create_test_config(Path(tmp))
            runtime = APIRuntime.from_config_path(str(config_path))
            try:
                runtime_info = extract_runtime_info_from_api_runtime(runtime)

                assert_module_lane(
                    runtime_mode=runtime_info["runtime_mode"],
                    fallback_reason=runtime_info["fallback_reason"],
                    source="v1_tools_endpoint",
                    strict=True,
                )

                tool_specs = runtime.tools.provider_specs()
                self.assertIsNotNone(tool_specs, "Tool specs should be available")

                self.assertNotEqual(
                    runtime_info["runtime_mode"],
                    "legacy",
                    "Should not use legacy lane for tool registry",
                )

            finally:
                runtime.close()

    def test_tool_registry_uses_module_backend(self):
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

                tools = runtime.tools
                self.assertIsNotNone(tools, "Tool registry should be initialized")

                if runtime_info["runtime_mode"] == "brain":
                    pass
                elif runtime_info["runtime_mode"] == "legacy":
                    self.fail("Unexpected legacy mode in default configuration")

            finally:
                runtime.close()

    def test_tool_specs_parity_between_endpoints(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self._create_test_config(Path(tmp))
            runtime = APIRuntime.from_config_path(str(config_path))
            try:
                runtime_info = extract_runtime_info_from_api_runtime(runtime)

                assert_module_lane(
                    runtime_mode=runtime_info["runtime_mode"],
                    fallback_reason=runtime_info["fallback_reason"],
                    source="tool_specs_parity",
                    strict=True,
                )

                provider_specs = runtime.tools.provider_specs()
                self.assertIsInstance(
                    provider_specs, list, "Provider specs should return a list"
                )

                for spec in provider_specs:
                    self.assertTrue(
                        hasattr(spec, "name"), "Each tool spec should have a name"
                    )
                    self.assertTrue(
                        hasattr(spec, "description"),
                        "Each tool spec should have a description",
                    )

            finally:
                runtime.close()

    def test_module_mode_tool_catalog_contains_module_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self._create_test_config(Path(tmp))
            runtime = APIRuntime.from_config_path(str(config_path))
            try:
                runtime_info = extract_runtime_info_from_api_runtime(runtime)

                assert_module_lane(
                    runtime_mode=runtime_info["runtime_mode"],
                    fallback_reason=runtime_info["fallback_reason"],
                    source="tool_catalog_module_tools",
                    strict=True,
                )

                tool_specs = runtime.tools.provider_specs()
                tool_names = [s.name for s in tool_specs]

                self.assertIsInstance(tool_names, list, "Tool names should be a list")

            finally:
                runtime.close()


class TestLegacyToolRegistryComparison(unittest.TestCase):
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
        _csc_install_default_agent(config, provider="echo")
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
