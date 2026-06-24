from __future__ import annotations

from typing import Any

from openminion.modules.brain.constants import (
    BRAIN_ACTION_STATUS_FAILED,
    BRAIN_ACTION_STATUS_SUCCESS,
    BRAIN_JOB_STATUS_RUNNING,
)
from openminion.services.runtime.a2a_delegate import A2aRuntimeDelegateAdapter


class _RecordingCall:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.command: dict[str, Any] | None = None
        self.session_id: str = ""
        self.trace_id: str = ""

    def __call__(self, *, command, session_id, trace_id) -> dict[str, Any]:
        self.command = command
        self.session_id = session_id
        self.trace_id = trace_id
        return self.response


def test_success_status_maps_to_ok_result() -> None:
    call = _RecordingCall(
        {
            "status": BRAIN_ACTION_STATUS_SUCCESS,
            "summary": "delegated turn completed",
            "outputs": {"body": "result text"},
        }
    )
    adapter = A2aRuntimeDelegateAdapter(a2a_call=call, parent_agent_id="parent")
    result = adapter.delegate(
        agent_id="researcher", instruction="find X", timeout_seconds=30
    )
    assert result.ok is True
    assert result.status == "success"
    assert result.content == "delegated turn completed"
    assert result.outputs == {"body": "result text"}
    assert result.target_agent_id == "researcher"


def test_command_shape_carries_model_named_target_and_instruction() -> None:
    call = _RecordingCall({"status": BRAIN_ACTION_STATUS_SUCCESS, "summary": "ok"})
    adapter = A2aRuntimeDelegateAdapter(a2a_call=call, parent_agent_id="parent")
    adapter.delegate(agent_id="researcher", instruction="find X", timeout_seconds=30)

    assert call.command is not None
    assert call.command["target_agent_id"] == "researcher"
    assert call.command["method"] == "delegate"
    # Instruction reaches the configured-agent handler via params["goal"].
    assert call.command["params"]["goal"] == "find X"
    assert call.command["params"]["instruction"] == "find X"
    assert call.command["params"]["timeout_seconds"] == 30
    assert call.command["timeout_ms"] == 30_000
    # Deterministic idempotency key (replay-safe across identical retries).
    assert call.command["idempotency_key"].startswith("task-delegate:")


def test_idempotency_key_is_stable_for_same_inputs() -> None:
    call = _RecordingCall({"status": BRAIN_ACTION_STATUS_SUCCESS, "summary": "ok"})
    adapter = A2aRuntimeDelegateAdapter(a2a_call=call, parent_agent_id="parent")
    adapter.delegate(agent_id="a", instruction="do x", timeout_seconds=10)
    key1 = call.command["idempotency_key"]
    adapter.delegate(agent_id="a", instruction="do x", timeout_seconds=10)
    key2 = call.command["idempotency_key"]
    assert key1 == key2


def test_failed_status_maps_to_typed_failure() -> None:
    call = _RecordingCall(
        {
            "status": BRAIN_ACTION_STATUS_FAILED,
            "summary": "boom",
            "error": {"code": "ROUTE_NOT_FOUND", "message": "no such agent"},
        }
    )
    adapter = A2aRuntimeDelegateAdapter(a2a_call=call, parent_agent_id="parent")
    result = adapter.delegate(agent_id="ghost", instruction="do x", timeout_seconds=10)
    assert result.ok is False
    assert result.status == "failed"
    assert result.error_code == "ROUTE_NOT_FOUND"
    assert result.error_message == "no such agent"


def test_running_status_maps_to_async_unsupported() -> None:
    call = _RecordingCall(
        {"status": BRAIN_JOB_STATUS_RUNNING, "summary": "job started", "task_id": "j1"}
    )
    adapter = A2aRuntimeDelegateAdapter(a2a_call=call, parent_agent_id="parent")
    result = adapter.delegate(
        agent_id="worker", instruction="sleep", timeout_seconds=10
    )
    assert result.ok is False
    assert result.status == "running"
    assert result.error_code == "A2A_DELEGATE_ASYNC_UNSUPPORTED"
    assert result.task_id == "j1"


def test_empty_args_short_circuit_without_calling_a2a() -> None:
    call = _RecordingCall({"status": BRAIN_ACTION_STATUS_SUCCESS})
    adapter = A2aRuntimeDelegateAdapter(a2a_call=call, parent_agent_id="parent")
    result = adapter.delegate(agent_id="", instruction="x", timeout_seconds=10)
    assert result.ok is False
    assert result.error_code == "TASK_DELEGATE_INVALID_ARGS"
    assert call.command is None  # never reached the A2A runtime


def test_call_exception_maps_to_runtime_error_result() -> None:
    def _boom(*, command, session_id, trace_id):
        raise RuntimeError("a2a down")

    adapter = A2aRuntimeDelegateAdapter(a2a_call=_boom, parent_agent_id="parent")
    result = adapter.delegate(agent_id="a", instruction="x", timeout_seconds=10)
    assert result.ok is False
    assert result.error_code == "A2A_RUNTIME_ERROR"
    assert "a2a down" in result.error_message
