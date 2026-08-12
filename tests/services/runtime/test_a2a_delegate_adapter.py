from __future__ import annotations

from typing import Any
import uuid

from openminion.modules.brain.constants import (
    BRAIN_ACTION_STATUS_FAILED,
    BRAIN_ACTION_STATUS_SUCCESS,
    BRAIN_JOB_STATUS_RUNNING,
)
from openminion.services.runtime.a2a_delegate import A2aRuntimeDelegateAdapter
from openminion.modules.a2a.models import is_valid_traceparent


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


class _RecordingTelemetry:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def emit_canonical_event(
        self,
        session_id: str,
        turn_id: str,
        event_type: str,
        payload: dict[str, Any],
        **_kwargs: Any,
    ) -> None:
        assert session_id == "session-1"
        assert turn_id == "turn-1"
        self.events.append((event_type, dict(payload)))


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
    adapter.delegate(
        agent_id="researcher",
        instruction="find X",
        timeout_seconds=30,
        permission_mode="bypass",
        workspace_root="/repo",
        cwd="/repo/subdir",
    )

    assert call.command is not None
    assert call.command["target_agent_id"] == "researcher"
    assert call.command["method"] == "delegate"
    # Instruction reaches the configured-agent handler via params["goal"].
    assert call.command["params"]["goal"] == "find X"
    assert call.command["params"]["instruction"] == "find X"
    assert call.command["params"]["timeout_seconds"] == 30
    assert call.command["params"]["permission_mode"] == "bypass"
    assert call.command["params"]["workspace_root"] == "/repo"
    assert call.command["params"]["cwd"] == "/repo/subdir"
    assert call.command["timeout_ms"] == 30_000
    # Deterministic idempotency key (replay-safe across identical retries).
    assert call.command["idempotency_key"].startswith("task-delegate:")


def test_delegate_sends_typed_observability_and_emits_handoff_lifecycle() -> None:
    call = _RecordingCall({"status": BRAIN_ACTION_STATUS_SUCCESS, "summary": "ok"})
    telemetry = _RecordingTelemetry()
    adapter = A2aRuntimeDelegateAdapter(
        a2a_call=call,
        parent_agent_id="parent",
        telemetryctl=telemetry,
    )
    adapter.bind_observability(
        session_id="session-1",
        turn_id="turn-1",
        invocation_id="11111111-1111-4111-8111-111111111111",
        execution_id="21111111-1111-4111-8111-111111111111",
    )
    adapter.delegate(
        agent_id="researcher",
        instruction="find X",
        timeout_seconds=30,
    )

    assert call.command is not None
    observability = call.command["observability"]
    assert observability["schema_version"] == "openminion.a2a_observability.v1"
    assert observability["invocation_id"] == "11111111-1111-4111-8111-111111111111"
    assert observability["execution_id"] == "21111111-1111-4111-8111-111111111111"
    uuid.UUID(observability["handoff_id"])
    assert is_valid_traceparent(observability["traceparent"])
    assert [event_type for event_type, _payload in telemetry.events] == [
        "agent.handoff.started",
        "agent.handoff.completed",
    ]


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
    assert result.error_code == "A2A_DELEGATE_ASYNC_UNREQUESTED"
    assert result.task_id == "j1"


def test_async_delegate_returns_resumable_running_handle() -> None:
    call = _RecordingCall(
        {"status": BRAIN_JOB_STATUS_RUNNING, "summary": "job started", "task_id": "j1"}
    )
    adapter = A2aRuntimeDelegateAdapter(a2a_call=call, parent_agent_id="parent")

    result = adapter.delegate(
        agent_id="worker",
        instruction="sleep",
        timeout_seconds=10,
        mode="async",
    )

    assert result.ok is True
    assert result.status == "running"
    assert result.task_id == "j1"
    assert result.error_code == ""
    assert call.command is not None
    assert call.command["expect_async"] is True
    assert call.command["params"]["mode"] == "async"


def test_async_status_and_cancel_route_through_a2a_lifecycle() -> None:
    class _LifecycleCall(_RecordingCall):
        def __init__(self) -> None:
            super().__init__({})
            self.polled: list[str] = []
            self.cancelled: list[str] = []

        def poll_task(self, *, task_id, session_id, trace_id):
            self.polled.append(task_id)
            return {
                "status": "running",
                "task_id": task_id,
                "trace_id": trace_id,
                "summary": "still running",
            }

        def cancel_task(self, *, task_id, session_id, trace_id):
            self.cancelled.append(task_id)
            return {
                "status": "canceled",
                "task_id": task_id,
                "trace_id": trace_id,
                "summary": "cancelled",
            }

    call = _LifecycleCall()
    adapter = A2aRuntimeDelegateAdapter(a2a_call=call, parent_agent_id="parent")

    status = adapter.status(task_id="job-1")
    resumed = adapter.resume(task_id="job-1")
    cancelled = adapter.cancel(task_id="job-1")

    assert status.ok is True
    assert status.status == "running"
    assert resumed.ok is True
    assert call.polled == ["job-1", "job-1"]
    assert cancelled.ok is True
    assert cancelled.status == "canceled"
    assert call.cancelled == ["job-1"]


def test_lifecycle_methods_resolve_from_bound_call_owner() -> None:
    class _LifecycleOwner:
        def call(self, *, command, session_id, trace_id):
            del command, session_id, trace_id
            return {"status": "success"}

        def poll_task(self, *, task_id, session_id, trace_id):
            del session_id, trace_id
            return {"status": "running", "task_id": task_id}

        def cancel_task(self, *, task_id, session_id, trace_id):
            del session_id, trace_id
            return {"status": "canceled", "task_id": task_id}

    owner = _LifecycleOwner()
    adapter = A2aRuntimeDelegateAdapter(
        a2a_call=owner.call,
        parent_agent_id="parent",
    )

    assert adapter.status(task_id="job-1").status == "running"
    assert adapter.cancel(task_id="job-1").status == "canceled"


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
