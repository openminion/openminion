from __future__ import annotations
from tests._csc_fixtures import _csc_install_default_agent


import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Callable
from unittest import mock

from openminion.base.config import OpenMinionConfig, save_config
from openminion.modules.llm.providers.base import (
    LLMProvider,
    ProviderRequest,
    ProviderResponse,
    ProviderToolCall,
)
from openminion.api.runtime import APIRuntime
from openminion.api.queries.sessions import list_session_messages
from openminion.api.turns import run_turn

_TOOL_FEEDBACK_PREFIX = "Tool execution results:\n"
_ProviderStep = Callable[[ProviderRequest], ProviderResponse]


class _ScriptedProvider(LLMProvider):
    name = "integration-scripted"

    def __init__(self, *, steps: list[_ProviderStep]) -> None:
        self._steps = list(steps)
        self._index = 0
        self.requests: list[ProviderRequest] = []

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.requests.append(request)
        if self._index >= len(self._steps):
            raise AssertionError(
                f"received more provider calls than scripted steps ({len(self._steps)})"
            )
        step = self._steps[self._index]
        self._index += 1
        return step(request)


def _tool_response(*tool_calls: ProviderToolCall) -> ProviderResponse:
    return ProviderResponse(
        text="",
        model="integration-scripted-model",
        tool_calls=list(tool_calls),
        finish_reason="tool_calls",
    )


def _latest_tool_feedback(request: ProviderRequest) -> list[dict]:
    for item in reversed(request.history):
        if item.role != "user":
            continue
        if not str(item.content).startswith(_TOOL_FEEDBACK_PREFIX):
            continue
        payload = str(item.content)[len(_TOOL_FEEDBACK_PREFIX) :]
        parsed = json.loads(payload)
        if not isinstance(parsed, list):
            raise AssertionError("tool feedback payload is not a list")
        return parsed
    raise AssertionError("missing tool feedback in provider history")


def _tool_results(turn_payload: dict) -> list[dict]:
    metadata = turn_payload.get("metadata", {})
    if not isinstance(metadata, dict):
        return []
    raw = str(metadata.get("tool_results", "[]"))
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict)]


class GatewayAPIBreakFixIntegrationTests(unittest.TestCase):
    def _write_config(self, tmp_path: Path) -> Path:
        config_path = tmp_path / "config.json"
        config = OpenMinionConfig()
        _csc_install_default_agent(config)  # type: ignore[attr-defined]
        config.runtime.log_level = "ERROR"
        _csc_install_default_agent(config, provider="echo")
        config.security.tool_policy.default_required_scopes = []
        config.storage.path = str(tmp_path / "state" / "integration.db")
        save_config(config, str(config_path))
        return config_path

    def _run_turn_with_provider(
        self,
        *,
        config_path: Path,
        workspace_root: Path,
        provider: LLMProvider,
        payload: dict,
    ) -> tuple[dict, dict]:
        with (
            mock.patch.dict(
                os.environ,
                {
                    "OPENMINION_AGENT_RUNTIME_MODE": "legacy",
                    "OPENMINION_DISABLE_SECURITY_POLICY": "true",
                },
                clear=False,
            ),
            mock.patch(
                "openminion.api.runtime.build_provider",
                return_value=provider,
            ),
        ):
            runtime = APIRuntime.from_config_path(str(config_path))
        try:
            payload = dict(payload)
            inbound_metadata = dict(payload.get("inbound_metadata") or {})
            inbound_metadata["workspace_root"] = str(workspace_root)
            payload["inbound_metadata"] = inbound_metadata
            turn_payload = run_turn(
                str(config_path),
                payload,
                runtime=runtime,
            )
            transcript = list_session_messages(
                str(config_path),
                session_id=str(payload.get("session_id", "")),
                runtime=runtime,
            )
            return turn_payload, transcript
        finally:
            runtime.close()

    def test_api_turn_break_then_fix_write_file(self) -> None:
        if True:
            self.skipTest(
                "Legacy scripted tool-call integration is disabled in brain runtime."
            )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace_root = root / "workspace"
            workspace_root.mkdir(parents=True, exist_ok=True)
            config_path = self._write_config(root)
            session_id = "integration-write-break-fix"

            broken_provider = _ScriptedProvider(
                steps=[
                    lambda _request: _tool_response(
                        ProviderToolCall(
                            id="call-write-broken",
                            name="write_file",
                            arguments={
                                "path": "../outside.txt",
                                "content": "should fail",
                            },
                            source="native",
                        )
                    )
                ]
            )
            broken_turn, broken_transcript = self._run_turn_with_provider(
                config_path=config_path,
                workspace_root=workspace_root,
                provider=broken_provider,
                payload={
                    "message": "integration break write_file",
                    "session_id": session_id,
                    "channel": "console",
                    "target": "integration",
                    "deliver": False,
                },
            )
            broken_results = _tool_results(broken_turn)
            self.assertEqual(len(broken_results), 1)
            self.assertEqual(str(broken_results[0].get("tool_name", "")), "write_file")
            self.assertFalse(bool(broken_results[0].get("ok")))
            self.assertIn(
                "path escapes workspace root", str(broken_results[0].get("error", ""))
            )
            self.assertEqual(
                broken_turn.get("metadata", {}).get("tool_loop_termination_reason"),
                "tool_no_success",
            )
            self.assertEqual(len(broken_transcript.get("messages", [])), 2)

            def _fixed_write(_request: ProviderRequest) -> ProviderResponse:
                return _tool_response(
                    ProviderToolCall(
                        id="call-write-fixed",
                        name="write_file",
                        arguments={
                            "path": "integration/write.txt",
                            "content": "integration-write-ok\n",
                            "append": False,
                            "create_dirs": True,
                        },
                        source="native",
                    )
                )

            def _fixed_finish(request: ProviderRequest) -> ProviderResponse:
                feedback = _latest_tool_feedback(request)
                self.assertEqual(len(feedback), 1)
                self.assertEqual(str(feedback[0].get("tool_name", "")), "write_file")
                self.assertTrue(bool(feedback[0].get("ok")))
                return ProviderResponse(
                    text="write_file integration fixed",
                    model="integration-scripted-model",
                )

            fixed_provider = _ScriptedProvider(steps=[_fixed_write, _fixed_finish])
            fixed_turn, fixed_transcript = self._run_turn_with_provider(
                config_path=config_path,
                workspace_root=workspace_root,
                provider=fixed_provider,
                payload={
                    "message": "integration fix write_file",
                    "session_id": session_id,
                    "channel": "console",
                    "target": "integration",
                    "deliver": False,
                },
            )
            fixed_results = _tool_results(fixed_turn)
            self.assertEqual(len(fixed_results), 1)
            self.assertEqual(str(fixed_results[0].get("tool_name", "")), "write_file")
            self.assertTrue(bool(fixed_results[0].get("ok")))
            self.assertEqual(
                fixed_turn.get("metadata", {}).get("tool_loop_termination_reason"),
                "model_final",
            )
            self.assertIn(
                "write_file integration fixed", str(fixed_turn.get("body", ""))
            )
            self.assertEqual(len(fixed_transcript.get("messages", [])), 4)

            target_file = workspace_root / "integration" / "write.txt"
            self.assertTrue(target_file.exists())
            self.assertIn(
                "integration-write-ok", target_file.read_text(encoding="utf-8")
            )

    def test_api_turn_break_then_fix_run_command(self) -> None:
        if True:
            self.skipTest(
                "Legacy scripted tool-call integration is disabled in brain runtime."
            )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace_root = root / "workspace"
            workspace_root.mkdir(parents=True, exist_ok=True)
            config_path = self._write_config(root)
            session_id = "integration-command-break-fix"

            broken_provider = _ScriptedProvider(
                steps=[
                    lambda _request: _tool_response(
                        ProviderToolCall(
                            id="call-run-broken",
                            name="run_command",
                            arguments={"command": "rm -rf /tmp/bad", "workdir": "."},
                            source="native",
                        )
                    )
                ]
            )
            broken_turn, _broken_transcript = self._run_turn_with_provider(
                config_path=config_path,
                workspace_root=workspace_root,
                provider=broken_provider,
                payload={
                    "message": "integration break run_command",
                    "session_id": session_id,
                    "channel": "console",
                    "target": "integration",
                    "deliver": False,
                },
            )
            broken_results = _tool_results(broken_turn)
            self.assertEqual(len(broken_results), 1)
            self.assertEqual(str(broken_results[0].get("tool_name", "")), "run_command")
            self.assertFalse(bool(broken_results[0].get("ok")))
            self.assertIn("blocked executable", str(broken_results[0].get("error", "")))
            self.assertEqual(
                broken_turn.get("metadata", {}).get("tool_loop_termination_reason"),
                "tool_no_success",
            )

            def _fixed_run(_request: ProviderRequest) -> ProviderResponse:
                return _tool_response(
                    ProviderToolCall(
                        id="call-run-fixed",
                        name="run_command",
                        arguments={
                            "command": "echo integration-command-ok",
                            "workdir": ".",
                        },
                        source="native",
                    )
                )

            def _fixed_finish(request: ProviderRequest) -> ProviderResponse:
                feedback = _latest_tool_feedback(request)
                self.assertEqual(len(feedback), 1)
                self.assertEqual(str(feedback[0].get("tool_name", "")), "run_command")
                self.assertTrue(bool(feedback[0].get("ok")))
                stdout = str((feedback[0].get("data") or {}).get("stdout", ""))
                self.assertIn("integration-command-ok", stdout)
                return ProviderResponse(
                    text="run_command integration fixed",
                    model="integration-scripted-model",
                )

            fixed_provider = _ScriptedProvider(steps=[_fixed_run, _fixed_finish])
            fixed_turn, fixed_transcript = self._run_turn_with_provider(
                config_path=config_path,
                workspace_root=workspace_root,
                provider=fixed_provider,
                payload={
                    "message": "integration fix run_command",
                    "session_id": session_id,
                    "channel": "console",
                    "target": "integration",
                    "deliver": False,
                },
            )
            fixed_results = _tool_results(fixed_turn)
            self.assertEqual(len(fixed_results), 1)
            self.assertEqual(str(fixed_results[0].get("tool_name", "")), "run_command")
            self.assertTrue(bool(fixed_results[0].get("ok")))
            self.assertEqual(
                fixed_turn.get("metadata", {}).get("tool_loop_termination_reason"),
                "model_final",
            )
            self.assertIn(
                "run_command integration fixed", str(fixed_turn.get("body", ""))
            )
            self.assertEqual(len(fixed_transcript.get("messages", [])), 4)
