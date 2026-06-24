from __future__ import annotations
from tests._csc_fixtures import _csc_install_default_agent


import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import asyncio
from types import SimpleNamespace

from tests.helpers import (
    assert_module_lane,
    extract_runtime_info_from_api_runtime,
)

from openminion.base.config import OpenMinionConfig, save_config
from openminion.api.runtime import APIRuntime
from openminion.api.turns import run_turn
from openminion.services.lifecycle.request_orchestrator import TurnTimeoutError


class TestAPIGatewayNoLegacy(unittest.TestCase):
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

    def test_api_turns_module_lane(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self._create_test_config(Path(tmp))
            runtime = APIRuntime.from_config_path(str(config_path))
            try:
                # Run turn via API
                result = run_turn(
                    str(config_path),
                    {
                        "message": "test api turn",
                        "session_id": "api-test-session",
                        "agent_id": "openminion",
                    },
                    runtime=runtime,
                )

                # Verify turn completed
                self.assertIsNotNone(result)
                self.assertIn("run_id", result)

                # Verify runtime mode after turn
                runtime_info = extract_runtime_info_from_api_runtime(runtime)
                assert_module_lane(
                    runtime_mode=runtime_info["runtime_mode"],
                    fallback_reason=runtime_info["fallback_reason"],
                    source="api_turns_endpoint",
                    strict=True,
                )
            finally:
                runtime.close()

    def test_api_turns_delegate_to_request_orchestrator(self):
        import openminion.api.turns as api_turns
        import openminion.services.lifecycle.request_orchestrator as orchestrator

        self.assertIs(
            api_turns._run_turn,
            orchestrator.run_turn,
            "API /turns must use the canonical request orchestrator",
        )

    def test_api_forced_tools_forwarded(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self._create_test_config(Path(tmp))
            runtime = APIRuntime.from_config_path(str(config_path))
            calls: list[dict] = []

            async def _fake_gateway_once(**kwargs):
                calls.append(kwargs)
                return SimpleNamespace(
                    id="forced-tools",
                    channel="console",
                    target="api-user",
                    body="ok",
                    metadata={},
                )

            try:
                with mock.patch(
                    "openminion.services.runtime.ingress._run_gateway_once",
                    new=_fake_gateway_once,
                ):
                    result = run_turn(
                        str(config_path),
                        {
                            "message": "run with explicit tools",
                            "session_id": "api-forced-tools",
                            "agent_id": "openminion",
                            "forced_tools": ["tool.example"],
                        },
                        runtime=runtime,
                    )
                self.assertIsNotNone(result)
                self.assertTrue(calls, "Expected orchestrator gateway invocation")
                self.assertIn("tool.example", calls[0].get("forced_tools", []))
            finally:
                runtime.close()

    def test_api_timeout_surfaces_turn_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self._create_test_config(Path(tmp))
            runtime = APIRuntime.from_config_path(str(config_path))

            async def _slow_gateway_once(**_kwargs):
                await asyncio.sleep(0.05)
                return SimpleNamespace(
                    id="slow-turn",
                    channel="console",
                    target="api-user",
                    body="late",
                    metadata={},
                )

            try:
                with (
                    mock.patch(
                        "openminion.services.runtime.ingress._run_gateway_once",
                        new=_slow_gateway_once,
                    ),
                    self.assertRaises(TurnTimeoutError),
                ):
                    run_turn(
                        str(config_path),
                        {
                            "message": "timeout check",
                            "session_id": "api-timeout",
                            "agent_id": "openminion",
                            "timeout_seconds": 0.001,
                        },
                        runtime=runtime,
                    )
            finally:
                runtime.close()

    def test_location_prompt_does_not_runtime_force_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self._create_test_config(Path(tmp))
            runtime = APIRuntime.from_config_path(str(config_path))
            calls: list[dict] = []

            async def _fake_gateway_once(**kwargs):
                calls.append(kwargs)
                return SimpleNamespace(
                    id="location-no-forced-tools",
                    channel="console",
                    target="api-user",
                    body="ok",
                    metadata={},
                )

            try:
                with mock.patch(
                    "openminion.services.runtime.ingress._run_gateway_once",
                    new=_fake_gateway_once,
                ):
                    result = run_turn(
                        str(config_path),
                        {
                            "message": "what city am i near right now?",
                            "session_id": "api-location-no-forced-tools",
                            "agent_id": "openminion",
                        },
                        runtime=runtime,
                    )
                self.assertIsNotNone(result)
                self.assertTrue(calls, "Expected orchestrator gateway invocation")
                self.assertIn(calls[0].get("capability_category"), {None, "location"})
                self.assertFalse(calls[0].get("forced_tools"))
            finally:
                runtime.close()

    def test_gateway_service_module_lane(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self._create_test_config(Path(tmp))
            runtime = APIRuntime.from_config_path(str(config_path))
            try:
                # Get gateway service
                gateway = runtime.gateway
                self.assertIsNotNone(gateway)

                # Verify runtime mode
                runtime_info = extract_runtime_info_from_api_runtime(runtime)
                assert_module_lane(
                    runtime_mode=runtime_info["runtime_mode"],
                    fallback_reason=runtime_info["fallback_reason"],
                    source="gateway_service",
                    strict=True,
                )

                # Verify brain integration mode
                brain_mode = getattr(gateway, "_brain_integration_mode", "unknown")
                self.assertEqual(
                    brain_mode,
                    "contextctl_authoritative",
                    f"Unexpected brain integration mode: {brain_mode}",
                )
            finally:
                runtime.close()

    def test_tool_invocation_gateway_module_lane(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self._create_test_config(Path(tmp))
            runtime = APIRuntime.from_config_path(str(config_path))
            try:
                # Get tool registry
                tools = runtime.tools
                tool_names = [spec.name for spec in tools.provider_specs()]

                # Verify tools available
                self.assertGreater(len(tool_names), 0, "No tools in gateway registry")

                # Verify runtime mode
                runtime_info = extract_runtime_info_from_api_runtime(runtime)
                assert_module_lane(
                    runtime_mode=runtime_info["runtime_mode"],
                    fallback_reason=runtime_info["fallback_reason"],
                    source="gateway_tool_invocation",
                    strict=True,
                )
            finally:
                runtime.close()

    def test_api_turns_no_silent_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self._create_test_config(Path(tmp))
            runtime = APIRuntime.from_config_path(str(config_path))
            try:
                # Run multiple turns
                for i in range(3):
                    result = run_turn(
                        str(config_path),
                        {
                            "message": f"turn {i}",
                            "session_id": "no-fallback-session",
                            "agent_id": "openminion",
                        },
                        runtime=runtime,
                    )
                    self.assertIsNotNone(result)

                # Verify no fallback occurred
                runtime_info = extract_runtime_info_from_api_runtime(runtime)
                self.assertFalse(
                    runtime_info["fallback_reason"],
                    f"Silent fallback detected: {runtime_info['fallback_reason']}",
                )

                assert_module_lane(
                    runtime_mode=runtime_info["runtime_mode"],
                    fallback_reason=runtime_info["fallback_reason"],
                    source="api_turns_no_silent_fallback",
                    strict=True,
                )
            finally:
                runtime.close()
