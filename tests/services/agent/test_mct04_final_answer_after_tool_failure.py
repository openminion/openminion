from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from openminion.base.constants import STATE_KEY_FINALIZATION_STATUS
from openminion.base.types import AgentResponse, Message
from openminion.modules.llm.providers.base import ProviderResponse, ProviderToolCall
from openminion.modules.tool.base import ToolExecutionResult
from openminion.modules.tool.registry import ToolExecutionBatch
from openminion.services.agent.execution.deps import ExecutorDeps
from openminion.services.agent.execution.unforced_lane.loop import (
    handle_unforced_tool_calls,
)


def _final_answer_response(
    text: str, *, status: str = "final_answer"
) -> ProviderResponse:
    response = ProviderResponse(
        text=text,
        model="fake-model",
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


def _runner(runtime_ops: _FakeRuntimeOps, user_body: str) -> SimpleNamespace:
    return SimpleNamespace(
        runtime_ops=runtime_ops,
        runtime=SimpleNamespace(
            inbound=Message(channel="console", target="cli", body=user_body),
            system_prompt="system",
            provider_history=[],
            user_message=user_body,
        ),
        service_port=SimpleNamespace(
            config=SimpleNamespace(runtime=SimpleNamespace(agent_loop_max_steps=4))
        ),
    )


# non-denied tool failure → follow-up call → model_final answer.


def test_non_denied_tool_failure_triggers_follow_up_for_final_answer() -> None:
    failed_batch = ToolExecutionBatch(
        results=[
            ToolExecutionResult(
                tool_name="exec.run",
                ok=False,
                verified=False,
                content="command exited with code 1\n... pytest output...",
                error="command exited with code 1",
                data={
                    "error_code": "EXEC_ERROR",
                    "exit_code": 1,
                    "stdout": "1 failed, 6 passed",
                },
                call_id="call-1",
                source="native",
            )
        ]
    )
    runtime_ops = _FakeRuntimeOps(
        # NB: denied=False — the tool ran and reported a runtime failure.
        execute_batches=[(failed_batch, [], False)],
        provider_responses=[
            _final_answer_response(
                "I created the project scaffold (pyproject.toml, README.md, "
                "sample_tasks.csv, task_summary/report.py, "
                "tests/test_report.py) and ran pytest, which showed 6 passed "
                "and 1 failed in test_highest_priority_open_items. The "
                "implementation needs a fix to exclude closed tasks."
            ),
        ],
    )
    initial_response = ProviderResponse(
        text="",
        model="fake-model",
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
            _runner(runtime_ops, "create a tiny Python project and run pytest"),
            initial_response=initial_response,
            intent_category="coding",
            tool_call_strategy="auto",
            tool_budget_state=None,
            deps=_deps(),
        )
    )

    assert isinstance(response, AgentResponse)
    # MCT-04 contract: the final answer is the MODEL's plain-text
    # summary, not the canned "status=error: tool execution blocked".
    assert "tool execution blocked" not in (response.text or "")
    assert "pytest" in (response.text or "")
    assert "test_highest_priority_open_items" in (response.text or "")
    # The follow-up provider call DID fire (1 follow-up after the
    # initial tool emission).
    assert runtime_ops.provider_index == 1
    # The tool batch was executed exactly once.
    assert runtime_ops.execute_index == 1


def test_non_denied_tool_failure_termination_reason_is_model_final() -> None:
    failed_batch = ToolExecutionBatch(
        results=[
            ToolExecutionResult(
                tool_name="exec.run",
                ok=False,
                verified=False,
                content="command exited with code 1",
                error="command exited with code 1",
                data={"error_code": "EXEC_ERROR", "exit_code": 1},
                call_id="call-1",
                source="native",
            )
        ]
    )
    runtime_ops = _FakeRuntimeOps(
        execute_batches=[(failed_batch, [], False)],
        provider_responses=[
            _final_answer_response("I attempted X but it failed. Here is the summary."),
        ],
    )
    initial_response = ProviderResponse(
        text="",
        model="fake-model",
        tool_calls=[
            ProviderToolCall(
                name="exec.run",
                arguments={"command": "false"},
            )
        ],
        finish_reason="tool_calls",
    )

    response = asyncio.run(
        handle_unforced_tool_calls(
            _runner(runtime_ops, "run a command"),
            initial_response=initial_response,
            intent_category="coding",
            tool_call_strategy="auto",
            tool_budget_state=None,
            deps=_deps(),
        )
    )

    assert response.metadata.get("tool_loop_termination_reason") == "model_final"


# Existing behavior preservation: denied=True still short-circuits.


def test_policy_denied_tool_still_routes_to_blocked_tool_response() -> None:
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
                },
                call_id="call-1",
                source="policy",
            )
        ]
    )
    runtime_ops = _FakeRuntimeOps(
        execute_batches=[(denied_batch, [], True)],  # denied=True
        provider_responses=[],
    )
    initial_response = ProviderResponse(
        text="",
        model="fake-model",
        tool_calls=[
            ProviderToolCall(
                name="exec.run",
                arguments={"command": "rm -rf /"},
            )
        ],
        finish_reason="tool_calls",
    )

    response = asyncio.run(
        handle_unforced_tool_calls(
            _runner(runtime_ops, "delete everything"),
            initial_response=initial_response,
            intent_category="coding",
            tool_call_strategy="auto",
            tool_budget_state=None,
            deps=_deps(),
        )
    )

    # Hard block: tool-aware blocked_tool_response body + tool_no_success
    # termination preserved.
    assert "exec.run" in response.text
    assert "POLICY_DENIED" in response.text
    assert "status=error: tool execution blocked" not in response.text
    assert response.metadata.get("tool_loop_termination_reason") == "tool_no_success"
    # NO follow-up provider call fired for denied case.
    assert runtime_ops.provider_index == 0


def test_denied_recovery_path_still_works_when_recovery_hint_available() -> None:
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
                            "suggested_fix": "Write the target file directly.",
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
            (denied_batch, [{"event_kind": "policy_denied"}], True),
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
            _final_answer_response("Created the project file via file.write."),
        ],
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
            _runner(runtime_ops, "make a project"),
            initial_response=initial_response,
            intent_category="coding",
            tool_call_strategy="auto",
            tool_budget_state=None,
            deps=_deps(),
        )
    )

    assert isinstance(response, AgentResponse)
    assert response.metadata.get("tool_loop_termination_reason") == "model_final"
    assert "Created the project file" in (response.text or "")
    # 2 LLM calls + 2 tool batches: denied-recovery worked as before.
    assert runtime_ops.provider_index == 2
    assert runtime_ops.execute_index == 2


# Cross-batch finalization guardrail.


def _successful_batch(prefix: str, count: int) -> ToolExecutionBatch:
    return ToolExecutionBatch(
        results=[
            ToolExecutionResult(
                tool_name="file.write",
                ok=True,
                verified=True,
                content=f"wrote {prefix}-{index}.txt",
                data={"path": f"/tmp/project/{prefix}-{index}.txt"},
                call_id=f"{prefix}-{index}",
                source="native",
            )
            for index in range(count)
        ]
    )


def _tool_call_response(*, prefix: str, count: int) -> ProviderResponse:
    return ProviderResponse(
        text="",
        model="fake-model",
        tool_calls=[
            ProviderToolCall(
                name="file.write",
                arguments={
                    "path": f"/tmp/project/{prefix}-{index}.txt",
                    "content": f"{prefix}-{index}\n",
                },
            )
            for index in range(count)
        ],
        finish_reason="tool_calls",
    )


def test_cumulative_successful_batches_require_finalization_contract() -> None:

    runtime_ops = _FakeRuntimeOps(
        execute_batches=[
            (_successful_batch("first", 2), [], False),
            (_successful_batch("second", 2), [], False),
        ],
        provider_responses=[
            _tool_call_response(prefix="second", count=2),
            ProviderResponse(
                text="I finished the files but omitted the typed trailer.",
                model="fake-model",
                finish_reason="stop",
            ),
            ProviderResponse(
                text="Still missing the typed trailer.",
                model="fake-model",
                finish_reason="stop",
            ),
        ],
    )

    response = asyncio.run(
        handle_unforced_tool_calls(
            _runner(runtime_ops, "create four project files"),
            initial_response=_tool_call_response(prefix="first", count=2),
            intent_category="coding",
            tool_call_strategy="auto",
            tool_budget_state=None,
            deps=_deps(),
        )
    )

    assert response.metadata.get("tool_loop_termination_reason") == (
        "finalization_contract_missing"
    )
    assert "required typed finalization_status contract" in (response.text or "")
    assert runtime_ops.execute_index == 2
    assert runtime_ops.provider_index == 3


def test_cumulative_successful_batches_accept_typed_finalization_contract() -> None:
    runtime_ops = _FakeRuntimeOps(
        execute_batches=[
            (_successful_batch("first", 2), [], False),
            (_successful_batch("second", 2), [], False),
        ],
        provider_responses=[
            _tool_call_response(prefix="second", count=2),
            _final_answer_response("Created and verified all four files."),
        ],
    )

    response = asyncio.run(
        handle_unforced_tool_calls(
            _runner(runtime_ops, "create four project files"),
            initial_response=_tool_call_response(prefix="first", count=2),
            intent_category="coding",
            tool_call_strategy="auto",
            tool_budget_state=None,
            deps=_deps(),
        )
    )

    assert response.metadata.get("tool_loop_termination_reason") == "model_final"
    assert "Created and verified" in (response.text or "")
    assert response.metadata.get(STATE_KEY_FINALIZATION_STATUS)
    assert runtime_ops.execute_index == 2
    assert runtime_ops.provider_index == 2


# Structural guardrail: the fix operates on the `denied` flag only.


def test_immediate_tool_result_response_only_blocks_when_denied() -> None:
    from openminion.services.agent.execution.unforced_lane import loop

    source = open(loop.__file__).read()
    # The exact early-return pattern is the load-bearing contract;
    # if anyone widens the short-circuit back to `not batch.has_success`
    # this test fails fast.
    assert "if denied:\n        return blocked_tool_response(" in source
    # And the wide condition must NOT be present any more.
    assert (
        "if denied or not batch.has_success:\n        return blocked_tool_response"
        not in source
    )


def test_no_prose_keyword_heuristic_in_mct04_branch() -> None:
    import ast

    from openminion.services.agent.execution.unforced_lane import loop

    source = open(loop.__file__).read()
    tree = ast.parse(source)
    func = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name == "_immediate_tool_result_response"
    )
    # Walk every Constant string node inside the function body
    # (excluding the docstring) and assert none is a tool-output
    # keyword. The docstring is the first Expr.Constant child.
    body_nodes = list(func.body)
    if (
        body_nodes
        and isinstance(body_nodes[0], ast.Expr)
        and isinstance(body_nodes[0].value, ast.Constant)
        and isinstance(body_nodes[0].value.value, str)
    ):
        body_nodes = body_nodes[1:]
    forbidden_keywords = {"pytest", "exit_code", "EXEC_ERROR", "stderr_preview"}
    for node in body_nodes:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                assert sub.value not in forbidden_keywords, (
                    f"MCT-04 must not branch on tool-output keyword {sub.value!r}"
                )
