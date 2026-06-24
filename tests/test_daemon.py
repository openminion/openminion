from __future__ import annotations
from tests._csc_fixtures import _csc_install_default_agent


import os
import tempfile
import unittest
import json
from pathlib import Path
from unittest import mock
from types import SimpleNamespace

from tests.helpers import (
    assert_module_lane,
    extract_runtime_info_from_api_runtime,
)

from openminion.base.config import OpenMinionConfig, save_config
from openminion.api.runtime import APIRuntime
from openminion.services.runtime.daemon import (
    _tool_artifact_refs,
    build_turn_request,
    execute_turn,
)
from openminion.services.runtime.ingress import RuntimeTurnResult


class TestDaemonNoLegacyParity(unittest.TestCase):
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

    def test_daemon_mode_default_is_module(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self._create_test_config(Path(tmp))

            runtime = APIRuntime.from_config_path(str(config_path))
            try:
                runtime_info = extract_runtime_info_from_api_runtime(runtime)

                assert_module_lane(
                    runtime_mode=runtime_info["runtime_mode"],
                    fallback_reason=runtime_info["fallback_reason"],
                    source="daemon_parity_default",
                    strict=True,
                )

                self.assertNotEqual(
                    runtime_info["runtime_mode"],
                    "legacy",
                    "Daemon default mode should not be legacy",
                )
            finally:
                runtime.close()

    def test_daemon_in_process_equivalent_runtime_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self._create_test_config(Path(tmp))

            inproc_runtime = APIRuntime.from_config_path(str(config_path))
            try:
                inproc_info = extract_runtime_info_from_api_runtime(inproc_runtime)
            finally:
                inproc_runtime.close()

            self.assertEqual(
                inproc_info["runtime_mode"], "brain", "In-process should be brain mode"
            )

    def test_daemon_tool_catalog_parity(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self._create_test_config(Path(tmp))

            inproc_runtime = APIRuntime.from_config_path(str(config_path))
            try:
                inproc_tools = sorted(
                    [spec.name for spec in inproc_runtime.tools.provider_specs()]
                )
                inproc_info = extract_runtime_info_from_api_runtime(inproc_runtime)
            finally:
                inproc_runtime.close()

            assert_module_lane(
                runtime_mode=inproc_info["runtime_mode"],
                fallback_reason=inproc_info["fallback_reason"],
                source="daemon_parity_tools",
                strict=True,
            )

            self.assertGreater(len(inproc_tools), 0, "No tools in in-process catalog")

    def test_daemon_no_implicit_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self._create_test_config(Path(tmp))

            runtime = APIRuntime.from_config_path(str(config_path))
            try:
                runtime_info = extract_runtime_info_from_api_runtime(runtime)

                self.assertFalse(
                    runtime_info["fallback_reason"],
                    f"Unexpected fallback: {runtime_info['fallback_reason']}",
                )

                self.assertEqual(
                    runtime_info["runtime_mode"],
                    "brain",
                    "Expected brain mode, not fallback",
                )
            finally:
                runtime.close()

    def test_daemon_execute_turn_uses_shared_runtime_ingress(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self._create_test_config(Path(tmp))
            runtime = APIRuntime.from_config_path(str(config_path))
            called: dict[str, bool] = {"ingress": False}

            def _fake_execute_runtime_turn(**_kwargs):
                called["ingress"] = True
                return RuntimeTurnResult(
                    id="daemon-turn",
                    channel="console",
                    target="api-user",
                    body="daemon ok",
                    metadata={},
                    agent_id="openminion",
                )

            request = build_turn_request(
                {
                    "message": "daemon orchestrator check",
                    "session_id": "daemon-orchestrator",
                    "agent_id": "openminion",
                },
                default_agent_id="openminion",
            )
            cancel_event = SimpleNamespace(is_set=lambda: False)

            try:
                with mock.patch(
                    "openminion.services.runtime.daemon.execute_runtime_turn",
                    new=_fake_execute_runtime_turn,
                ):
                    response = execute_turn(
                        runtime=runtime,
                        request=request,
                        emit_chunk=lambda _chunk: None,
                        cancel_event=cancel_event,
                    )
                self.assertTrue(called["ingress"], "Expected shared ingress execution")
                self.assertEqual(response.final_text, "daemon ok")
            finally:
                runtime.close()

    def test_daemon_execute_turn_forwards_cron_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self._create_test_config(Path(tmp))
            runtime = APIRuntime.from_config_path(str(config_path))
            captured: dict[str, object] = {}

            def _fake_execute_runtime_turn(**kwargs):
                captured["request"] = kwargs.get("request")
                return RuntimeTurnResult(
                    id="daemon-turn",
                    channel="console",
                    target="api-user",
                    body="daemon ok",
                    metadata={},
                    agent_id="openminion",
                )

            request = build_turn_request(
                {
                    "message": "cron metadata check",
                    "session_id": "daemon-cron-metadata",
                    "agent_id": "openminion",
                    "meta": {
                        "cron_job_id": "job-123",
                        "cron_run_id": "run-456",
                        "scheduled_for": "2026-03-20T00:00:00Z",
                    },
                },
                default_agent_id="openminion",
            )
            cancel_event = SimpleNamespace(is_set=lambda: False)

            try:
                with mock.patch(
                    "openminion.services.runtime.daemon.execute_runtime_turn",
                    new=_fake_execute_runtime_turn,
                ):
                    response = execute_turn(
                        runtime=runtime,
                        request=request,
                        emit_chunk=lambda _chunk: None,
                        cancel_event=cancel_event,
                    )
                self.assertEqual(response.final_text, "daemon ok")
                ingress_request = captured.get("request")
                inbound_metadata = (
                    dict(getattr(ingress_request, "inbound_metadata", {}) or {})
                    if ingress_request is not None
                    else {}
                )
                self.assertEqual(inbound_metadata.get("cron_job_id"), "job-123")
                self.assertEqual(inbound_metadata.get("cron_run_id"), "run-456")
                self.assertEqual(
                    inbound_metadata.get("scheduled_for"), "2026-03-20T00:00:00Z"
                )
            finally:
                runtime.close()

    def test_daemon_execute_turn_preserves_turn_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self._create_test_config(Path(tmp))
            runtime = APIRuntime.from_config_path(str(config_path))

            tool_results = json.dumps(
                [
                    {
                        "tool_name": "plan.update",
                        "data": {
                            "plan": {
                                "session_id": "sess-daemon",
                                "items": [
                                    {
                                        "index": 0,
                                        "text": "Read config",
                                        "status": "done",
                                    }
                                ],
                                "summary": "1/1 done, 0 in progress",
                            }
                        },
                    }
                ],
                sort_keys=True,
            )

            def _fake_execute_runtime_turn(**_kwargs):
                return RuntimeTurnResult(
                    id="daemon-turn",
                    channel="console",
                    target="api-user",
                    body="daemon ok",
                    metadata={"tool_results": tool_results, "trace_id": "trace-plan"},
                    agent_id="openminion",
                )

            request = build_turn_request(
                {
                    "message": "plan metadata check",
                    "session_id": "sess-daemon",
                    "agent_id": "openminion",
                },
                default_agent_id="openminion",
            )
            cancel_event = SimpleNamespace(is_set=lambda: False)

            try:
                with mock.patch(
                    "openminion.services.runtime.daemon.execute_runtime_turn",
                    new=_fake_execute_runtime_turn,
                ):
                    response = execute_turn(
                        runtime=runtime,
                        request=request,
                        emit_chunk=lambda _chunk: None,
                        cancel_event=cancel_event,
                    )
                self.assertEqual(response.metadata.get("trace_id"), "trace-plan")
                self.assertEqual(response.metadata.get("tool_results"), tool_results)
            finally:
                runtime.close()


class TestDaemonEndpointValidation(unittest.TestCase):
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

    def test_daemon_endpoint_runtime_mode_consistency(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self._create_test_config(Path(tmp))

            runtime = APIRuntime.from_config_path(str(config_path))
            try:
                runtime_info = extract_runtime_info_from_api_runtime(runtime)

                assert_module_lane(
                    runtime_mode=runtime_info["runtime_mode"],
                    fallback_reason=runtime_info["fallback_reason"],
                    source="daemon_endpoint_consistency",
                    strict=True,
                )

                self.assertTrue(
                    runtime_info["brain_bridge_active"],
                    "Brain bridge should be active in brain mode",
                )
            finally:
                runtime.close()


def test_tool_artifact_refs_prefers_canonical_and_local_refs_over_synthesized() -> None:
    raw = json.dumps(
        [
            {
                "tool_name": "fetch.get",
                "artifact_refs": [
                    {"ref": "artifact://sha256/" + ("a" * 64)},
                    {"ref": "artifacts/fetch/body.txt"},
                ],
            }
        ]
    )

    refs = _tool_artifact_refs(raw, session_id="sess", trace_id="trace")

    assert refs == [
        {
            "ref": "artifact://sha256/" + ("a" * 64),
            "type": "tool_result",
            "tool": "fetch.get",
        },
        {
            "ref": "artifacts/fetch/body.txt",
            "type": "tool_result",
            "tool": "fetch.get",
        },
    ]


def test_tool_artifact_refs_falls_back_to_synthesized_ref_when_none_exist() -> None:
    raw = json.dumps([{"tool_name": "fetch.get", "artifact_refs": []}])

    refs = _tool_artifact_refs(raw, session_id="sess", trace_id="trace")

    assert len(refs) == 1
    assert refs[0]["tool"] == "fetch.get"
    assert refs[0]["ref"].startswith("artifact://tool/sess/trace/")
