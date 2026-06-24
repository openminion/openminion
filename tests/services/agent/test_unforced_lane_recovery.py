from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from openminion.base.constants import STATE_KEY_FINALIZATION_STATUS
from openminion.base.types import AgentResponse, Message
from openminion.modules.llm.providers.base import (
    ProviderResponse,
    ProviderToolCall,
    ProviderToolSpec,
)
from openminion.modules.tool.base import ToolExecutionResult
from openminion.modules.tool.registry import ToolExecutionBatch
from openminion.services.agent.constants import TERMINATION_REASON_LOOP_NO_PROGRESS
from openminion.services.agent.execution.deps import ExecutorDeps
from openminion.services.agent.execution.unforced_lane.follow_up import (
    denied_tool_recovery_hint,
)
from openminion.services.agent.execution.unforced_lane.loop import (
    handle_unforced_tool_calls,
)


def _tool_batch_metadata(
    *, batch: ToolExecutionBatch, tool_calls_count: int
) -> dict[str, str]:
    payload = [
        {
            "tool_name": item.tool_name,
            "ok": item.ok,
            "verified": item.verified,
            "content": item.content,
            "error": item.error,
            "data": dict(item.data),
            "call_id": item.call_id,
            "source": item.source,
        }
        for item in list(batch.results or [])
    ]
    return {
        "tool_results": json.dumps(payload, sort_keys=True),
        "tool_calls_count": str(tool_calls_count),
        "tool_execution_count": str(len(payload)),
        "tool_verified": str(all(bool(item["verified"]) for item in payload)).lower(),
    }


def _deps() -> ExecutorDeps:
    return ExecutorDeps(
        finalize_response=lambda response: response,
        tool_calls_payload=lambda calls: json.dumps(
            [
                {"name": call.name, "arguments": dict(call.arguments)}
                for call in list(calls or [])
            ],
            sort_keys=True,
        ),
        looks_like_tool_call_envelope=lambda text: False,
        identity_metadata=lambda: {},
        tool_batch_metadata=_tool_batch_metadata,
        collect_missing_required_args=lambda *args, **kwargs: {},
        is_tool_argument_error=lambda result: False,
        extract_missing_argument_fields=lambda results: "",
        canonical_tool_name=lambda name: str(name or ""),
    )


def _deps_with_tool_argument_errors() -> ExecutorDeps:
    return ExecutorDeps(
        finalize_response=lambda response: response,
        tool_calls_payload=lambda calls: json.dumps(
            [
                {"name": call.name, "arguments": dict(call.arguments)}
                for call in list(calls or [])
            ],
            sort_keys=True,
        ),
        looks_like_tool_call_envelope=lambda text: False,
        identity_metadata=lambda: {},
        tool_batch_metadata=_tool_batch_metadata,
        collect_missing_required_args=lambda *args, **kwargs: {},
        is_tool_argument_error=lambda result: (
            (getattr(result, "data", {}) or {}).get("error_code")
            == "INVALID_TOOL_ARGUMENTS"
        ),
        extract_missing_argument_fields=lambda results: ",".join(
            str(field)
            for result in list(results or [])
            for field in (
                (getattr(result, "data", {}) or {}).get("missing_fields") or []
            )
        ),
        canonical_tool_name=lambda name: str(name or ""),
    )


def _final_answer_response(
    text: str, *, model: str = "fake-model", status: str = "final_answer"
) -> ProviderResponse:
    response = ProviderResponse(
        text=text,
        model=model,
        finish_reason="stop",
    )
    setattr(
        response,
        STATE_KEY_FINALIZATION_STATUS,
        {
            "status": status,
            "reasoning": text,
            "remaining_work": "",
            "blocking_reason": "",
        },
    )
    return response


@dataclass
class _FakeRuntimeOps:
    execute_batches: list[tuple[ToolExecutionBatch, list[dict[str, str]], bool]]
    provider_responses: list[ProviderResponse]
    execute_index: int = 0
    provider_index: int = 0
    provider_requests: list[Any] = field(default_factory=list)

    async def execute_tool_calls(self, response_tool_calls, **kwargs):
        del response_tool_calls, kwargs
        batch, security_events, denied = self.execute_batches[self.execute_index]
        self.execute_index += 1
        return batch, security_events, denied

    async def call_provider(self, request, *, tool_call_strategy: str):
        del tool_call_strategy
        self.provider_requests.append(request)
        response = self.provider_responses[self.provider_index]
        self.provider_index += 1
        return response

    def record_self_improvement(
        self, *, user_message: str, tool_results: list[ToolExecutionResult]
    ) -> None:
        del user_message, tool_results

    def _collect_batch_output(self, batch: ToolExecutionBatch) -> str:
        return "\n".join(
            item.content for item in batch.results if str(item.content or "").strip()
        )


class _FakeToolRegistry:
    def model_provider_specs(self) -> list[ProviderToolSpec]:
        return [
            ProviderToolSpec(
                name="file.write",
                description="write a file",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                },
            )
        ]


def test_denied_tool_recovery_hint_surfaces_suggested_structured_tool() -> None:
    batch = ToolExecutionBatch(
        results=[
            ToolExecutionResult(
                tool_name="exec.run",
                ok=False,
                verified=False,
                content="",
                error="security_deny",
                data={
                    "error_code": "POLICY_DENIED",
                    "error_details": {
                        "suggested_tool": "file.write",
                        "suggested_fix": "Write the target file directly; parent directories are created automatically.",
                    },
                },
                source="policy",
            )
        ]
    )

    hint = denied_tool_recovery_hint(batch)

    assert hint is not None
    assert "Do not repeat it" in hint
    assert "file.write" in hint


def test_denied_tool_recovery_hint_accepts_native_error_details_shape() -> None:
    batch = ToolExecutionBatch(
        results=[
            ToolExecutionResult(
                tool_name="exec.run",
                ok=False,
                verified=False,
                content="",
                error="security_deny",
                data={
                    "error_code": "POLICY_DENIED",
                    "error": {
                        "code": "POLICY_DENIED",
                        "details": {
                            "suggested_tool": "exec.run",
                            "suggested_fix": "Pass the directory with workdir instead of `cd ... &&`.",
                        },
                    },
                },
                source="native",
            )
        ]
    )

    hint = denied_tool_recovery_hint(batch)

    assert hint is not None
    assert "exec.run" in hint
    assert "workdir" in hint


def test_unforced_lane_retries_once_after_policy_denial_with_suggested_tool() -> None:
    denied_batch = ToolExecutionBatch(
        results=[
            ToolExecutionResult(
                tool_name="exec.run",
                ok=False,
                verified=False,
                content="",
                error="security_deny",
                data={
                    "error_code": "POLICY_DENIED",
                    "error": {
                        "code": "POLICY_DENIED",
                        "details": {
                            "suggested_tool": "file.write",
                            "suggested_fix": "If you are scaffolding files or folders, write the target file directly with file.write.",
                        },
                    },
                },
                call_id="call-1",
                source="policy",
            )
        ]
    )
    success_batch = ToolExecutionBatch(
        results=[
            ToolExecutionResult(
                tool_name="file.write",
                ok=True,
                verified=True,
                content="wrote README.md",
                data={"path": "/tmp/project/README.md"},
                call_id="call-2",
                source="native",
            )
        ]
    )
    runtime_ops = _FakeRuntimeOps(
        execute_batches=[
            (
                denied_batch,
                [{"event_kind": "policy_denied", "tool_name": "exec.run"}],
                True,
            ),
            (success_batch, [], False),
        ],
        provider_responses=[
            ProviderResponse(
                text="",
                model="fake-model",
                tool_calls=[
                    ProviderToolCall(
                        name="file.write",
                        arguments={
                            "path": "/tmp/project/README.md",
                            "content": "# Demo\n",
                        },
                    )
                ],
                finish_reason="tool_calls",
            ),
            _final_answer_response(
                "Created the project file and recovered from the blocked shell scaffolding attempt."
            ),
        ],
    )
    runner = SimpleNamespace(
        runtime_ops=runtime_ops,
        runtime=SimpleNamespace(
            inbound=Message(channel="console", target="cli", body="make a project"),
            system_prompt="system",
            provider_history=[],
            user_message="make a project",
        ),
        service_port=SimpleNamespace(
            config=SimpleNamespace(runtime=SimpleNamespace(agent_loop_max_steps=4))
        ),
    )
    initial_response = ProviderResponse(
        text="",
        model="fake-model",
        tool_calls=[
            ProviderToolCall(
                name="exec.run",
                arguments={"command": "mkdir -p /tmp/project"},
            )
        ],
        finish_reason="tool_calls",
    )

    response = asyncio.run(
        handle_unforced_tool_calls(
            runner,
            initial_response=initial_response,
            intent_category="coding",
            tool_call_strategy="auto",
            tool_budget_state=None,
            deps=_deps(),
        )
    )

    assert isinstance(response, AgentResponse)
    assert response.metadata.get("tool_loop_termination_reason") == "model_final"
    assert response.metadata.get("tool_execution_count") == "1"
    assert runtime_ops.provider_index == 2
    assert runtime_ops.execute_index == 2
    assert runtime_ops.provider_requests
    assert "file.write" in runtime_ops.provider_requests[0].history[-1].content
    assert "Do not repeat it" in runtime_ops.provider_requests[0].history[-1].content


def test_repeated_policy_denial_after_recovery_hint_stops_as_loop_no_progress() -> None:
    first_denied_batch = ToolExecutionBatch(
        results=[
            ToolExecutionResult(
                tool_name="exec.run",
                ok=False,
                verified=False,
                content="",
                error="security_deny",
                data={
                    "error_code": "POLICY_DENIED",
                    "error": {
                        "code": "POLICY_DENIED",
                        "details": {
                            "suggested_tool": "file.list_dir",
                            "suggested_fix": "Use file.list_dir and file.read instead.",
                        },
                    },
                },
                call_id="call-1",
                source="policy",
            )
        ]
    )
    second_denied_batch = ToolExecutionBatch(
        results=[
            ToolExecutionResult(
                tool_name="exec.run",
                ok=False,
                verified=False,
                content="",
                error="security_deny",
                data={
                    "error_code": "POLICY_DENIED",
                    "error": {"code": "POLICY_DENIED", "details": {}},
                },
                call_id="call-2",
                source="policy",
            )
        ]
    )
    runtime_ops = _FakeRuntimeOps(
        execute_batches=[
            (
                first_denied_batch,
                [{"event_kind": "policy_denied", "tool_name": "exec.run"}],
                True,
            ),
            (
                second_denied_batch,
                [{"event_kind": "policy_denied", "tool_name": "exec.run"}],
                True,
            ),
        ],
        provider_responses=[
            ProviderResponse(
                text="",
                model="fake-model",
                tool_calls=[
                    ProviderToolCall(
                        name="exec.run",
                        arguments={"command": "python -m pytest -q 2>&1"},
                    )
                ],
                finish_reason="tool_calls",
            ),
        ],
    )
    runner = SimpleNamespace(
        runtime_ops=runtime_ops,
        runtime=SimpleNamespace(
            inbound=Message(channel="console", target="cli", body="test project"),
            system_prompt="system",
            provider_history=[],
            user_message="test project",
        ),
        service_port=SimpleNamespace(
            config=SimpleNamespace(runtime=SimpleNamespace(agent_loop_max_steps=4))
        ),
    )
    initial_response = ProviderResponse(
        text="",
        model="fake-model",
        tool_calls=[
            ProviderToolCall(
                name="exec.run",
                arguments={"command": "ls -la && cat pyproject.toml 2>/dev/null"},
            )
        ],
        finish_reason="tool_calls",
    )

    response = asyncio.run(
        handle_unforced_tool_calls(
            runner,
            initial_response=initial_response,
            intent_category="coding",
            tool_call_strategy="auto",
            tool_budget_state=None,
            deps=_deps(),
        )
    )

    assert response.metadata.get("tool_loop_termination_reason") == (
        TERMINATION_REASON_LOOP_NO_PROGRESS
    )
    assert response.metadata.get("loop_no_progress_reason") == "repeated_tool_failure"
    assert response.metadata.get("loop_no_progress_tool_name") == "exec.run"
    assert response.metadata.get("loop_no_progress_error_code") == "POLICY_DENIED"
    assert response.metadata.get("loop_no_progress_count") == "2"
    assert "repeated no-progress failures" in (response.text or "")
    assert runtime_ops.provider_index == 1
    assert runtime_ops.execute_index == 2


def test_unforced_lane_retries_once_after_tool_argument_error() -> None:
    invalid_batch = ToolExecutionBatch(
        results=[
            ToolExecutionResult(
                tool_name="weather.openmeteo.current",
                ok=False,
                verified=False,
                content="",
                error="missing required field: location",
                data={
                    "error_code": "INVALID_TOOL_ARGUMENTS",
                    "missing_fields": ["location"],
                },
                call_id="call-1",
                source="native",
            )
        ]
    )
    success_batch = ToolExecutionBatch(
        results=[
            ToolExecutionResult(
                tool_name="weather.openmeteo.current",
                ok=True,
                verified=True,
                content="weather ok: Tokyo",
                call_id="call-2",
                source="native",
            )
        ]
    )
    runtime_ops = _FakeRuntimeOps(
        execute_batches=[
            (invalid_batch, [], False),
            (success_batch, [], False),
        ],
        provider_responses=[
            ProviderResponse(
                text="",
                model="fake-model",
                tool_calls=[
                    ProviderToolCall(
                        name="weather.openmeteo.current",
                        arguments={"location": "Tokyo"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            _final_answer_response(
                "Weather lookup completed after correcting location."
            ),
        ],
    )
    runner = SimpleNamespace(
        runtime_ops=runtime_ops,
        runtime=SimpleNamespace(
            inbound=Message(channel="console", target="cli", body="weather"),
            system_prompt="system",
            provider_history=[],
            user_message="weather",
        ),
        service_port=SimpleNamespace(
            config=SimpleNamespace(runtime=SimpleNamespace(agent_loop_max_steps=4))
        ),
    )
    initial_response = ProviderResponse(
        text="",
        model="fake-model",
        tool_calls=[
            ProviderToolCall(
                name="weather.openmeteo.current",
                arguments={},
            )
        ],
        finish_reason="tool_calls",
    )

    response = asyncio.run(
        handle_unforced_tool_calls(
            runner,
            initial_response=initial_response,
            intent_category="utility",
            tool_call_strategy="auto",
            tool_budget_state=None,
            deps=_deps_with_tool_argument_errors(),
        )
    )

    assert response.metadata.get("tool_loop_termination_reason") == "model_final"
    assert "Weather lookup completed" in (response.text or "")
    assert runtime_ops.execute_index == 2
    assert runtime_ops.provider_index == 2
    assert "Missing required field(s): location" in (
        runtime_ops.provider_requests[0].history[-1].content
    )


def test_unforced_lane_recovers_embedded_follow_up_tool_calls_from_text() -> None:
    failed_batch = ToolExecutionBatch(
        results=[
            ToolExecutionResult(
                tool_name="exec.run",
                ok=False,
                verified=False,
                content="7 failed, 2 passed",
                error="command exited with code 1",
                data={
                    "error_code": "EXEC_ERROR",
                    "exit_code": 1,
                    "stdout": "7 failed, 2 passed",
                },
                call_id="call-1",
                source="native",
            )
        ]
    )
    repair_batch = ToolExecutionBatch(
        results=[
            ToolExecutionResult(
                tool_name="file.write",
                ok=True,
                verified=True,
                content="rewrote tests/test_report.py",
                data={"path": "/tmp/project/tests/test_report.py"},
                call_id="call-2",
                source="fallback",
            )
        ]
    )
    runtime_ops = _FakeRuntimeOps(
        execute_batches=[
            (failed_batch, [], False),
            (repair_batch, [], False),
        ],
        provider_responses=[
            ProviderResponse(
                text=(
                    "I'll overwrite the failing test file with the missing import.\n\n"
                    "<toolcall>\n"
                    '{"tool_name":"file.write","tool_input":{"path":"/tmp/project/tests/test_report.py","content":"import csv\\n"}}\n'
                    "</toolcall>"
                ),
                model="MiniMax-M2.7",
                finish_reason="stop",
            ),
            _final_answer_response(
                "I repaired the failing test file after the pytest failure.",
                model="MiniMax-M2.7",
            ),
        ],
    )
    runner = SimpleNamespace(
        runtime_ops=runtime_ops,
        runtime=SimpleNamespace(
            inbound=Message(channel="console", target="cli", body="fix the project"),
            system_prompt="system",
            provider_history=[],
            user_message="fix the project",
        ),
        service_port=SimpleNamespace(
            config=SimpleNamespace(runtime=SimpleNamespace(agent_loop_max_steps=4)),
            tools=_FakeToolRegistry(),
        ),
    )
    initial_response = ProviderResponse(
        text="",
        model="MiniMax-M2.7",
        tool_calls=[
            ProviderToolCall(
                name="exec.run",
                arguments={"command": "python -m pytest -q tests"},
            )
        ],
        finish_reason="tool_calls",
    )

    response = asyncio.run(
        handle_unforced_tool_calls(
            runner,
            initial_response=initial_response,
            intent_category="coding",
            tool_call_strategy="auto",
            tool_budget_state=None,
            deps=_deps(),
        )
    )

    assert isinstance(response, AgentResponse)
    assert "repaired the failing test file" in (response.text or "")
    assert runtime_ops.execute_index == 2
    assert runtime_ops.provider_index == 2
    assert runtime_ops.provider_requests
    assert runtime_ops.provider_requests[0].tools


def test_unforced_lane_recovers_minimax_tool_json_follow_up_from_text() -> None:
    inspection_batch = ToolExecutionBatch(
        results=[
            ToolExecutionResult(
                tool_name="file.list_dir",
                ok=True,
                verified=True,
                content="workspace seeded",
                data={"count": 8},
                call_id="call-1",
                source="native",
            )
        ]
    )
    rewrite_batch = ToolExecutionBatch(
        results=[
            ToolExecutionResult(
                tool_name="web.fetch",
                ok=True,
                verified=True,
                content="fetched PyPA packaging guide",
                data={
                    "url": "https://packaging.python.org/en/latest/guides/writing-pyproject-toml/"
                },
                call_id="fetch1",
                source="fallback",
            ),
            ToolExecutionResult(
                tool_name="file.write",
                ok=True,
                verified=True,
                content="rewrote pyproject.toml",
                data={"path": "/tmp/project/pyproject.toml"},
                call_id="write1",
                source="fallback",
            ),
        ]
    )
    runtime_ops = _FakeRuntimeOps(
        execute_batches=[
            (inspection_batch, [], False),
            (rewrite_batch, [], False),
        ],
        provider_responses=[
            ProviderResponse(
                text=(
                    "I'll complete the required first batch now.\n"
                    '{"tool":"web.fetch","tool_call_id":"fetch1","url":"https://packaging.python.org/en/latest/guides/writing-pyproject-toml/"}\n'
                    '{"tool":"file.write","tool_call_id":"write1","path":"/tmp/project/pyproject.toml","content":"[project]\\nname=\\"demo\\"\\n"}'
                ),
                model="MiniMax-M2.7",
                finish_reason="stop",
            ),
            _final_answer_response(
                "I updated the packaging metadata after verifying the seeded workspace.",
                model="MiniMax-M2.7",
            ),
        ],
    )
    runner = SimpleNamespace(
        runtime_ops=runtime_ops,
        runtime=SimpleNamespace(
            inbound=Message(channel="console", target="cli", body="update packaging"),
            system_prompt="system",
            provider_history=[],
            user_message="update packaging",
        ),
        service_port=SimpleNamespace(
            config=SimpleNamespace(runtime=SimpleNamespace(agent_loop_max_steps=4)),
            tools=SimpleNamespace(
                model_provider_specs=lambda: [
                    ProviderToolSpec(
                        name="web.fetch",
                        description="fetch a URL",
                        parameters={
                            "type": "object",
                            "properties": {"url": {"type": "string"}},
                            "required": ["url"],
                        },
                    ),
                    ProviderToolSpec(
                        name="file.write",
                        description="write a file",
                        parameters={
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "content": {"type": "string"},
                            },
                            "required": ["path", "content"],
                        },
                    ),
                ]
            ),
        ),
    )
    initial_response = ProviderResponse(
        text="",
        model="MiniMax-M2.7",
        tool_calls=[
            ProviderToolCall(
                name="file.list_dir",
                arguments={"path": "/tmp/project"},
            )
        ],
        finish_reason="tool_calls",
    )

    response = asyncio.run(
        handle_unforced_tool_calls(
            runner,
            initial_response=initial_response,
            intent_category="research",
            tool_call_strategy="auto",
            tool_budget_state=None,
            deps=_deps(),
        )
    )

    assert isinstance(response, AgentResponse)
    assert "updated the packaging metadata" in (response.text or "")
    assert runtime_ops.execute_index == 2
    assert runtime_ops.provider_index == 2
