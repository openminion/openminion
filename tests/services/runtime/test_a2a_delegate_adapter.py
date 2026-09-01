from __future__ import annotations

from typing import Any
import uuid

import pytest

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


def test_current_turn_approval_callback_reaches_bound_a2a_adapter() -> None:
    initial_callback = object()
    current_callback = object()

    class _CallOwner:
        def __init__(self) -> None:
            self.callback: Any = initial_callback

        def set_approval_callback(self, callback: Any) -> Any:
            previous = self.callback
            self.callback = callback
            return previous

        def call(self, *, command, session_id, trace_id) -> dict[str, Any]:
            del command, session_id, trace_id
            return {"status": BRAIN_ACTION_STATUS_SUCCESS, "summary": "ok"}

    owner = _CallOwner()
    adapter = A2aRuntimeDelegateAdapter(
        a2a_call=owner.call,
        parent_agent_id="parent",
    )

    previous = adapter.set_approval_callback(current_callback)

    assert previous is initial_callback
    assert owner.callback is current_callback


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


def test_fixed_readonly_reviewer_returns_typed_findings_and_child_identity() -> None:
    call = _RecordingCall(
        {
            "status": BRAIN_ACTION_STATUS_SUCCESS,
            "summary": "review complete",
            "outputs": {
                "child_agent_id": "readonly-reviewer",
                "findings": [
                    {
                        "priority": "P1",
                        "owner": "project plan lineage",
                        "message": "Add a stale predecessor test.",
                    }
                ],
                "verifier_refs": ["pytest:plan-lineage"],
            },
        }
    )
    result = A2aRuntimeDelegateAdapter(
        a2a_call=call,
        parent_agent_id="parent",
    ).review_readonly(
        reviewer_agent_id="readonly-reviewer",
        objective="preserve project plan lineage",
        criteria=["no P0 findings", "no P1 findings"],
        worktree="/repo",
        diff="git diff -- src tests",
        verifier_refs=["pytest:plan-lineage"],
        repository_instructions="AGENTS.md",
        timeout_seconds=30,
    )

    assert result.ok is True
    assert result.target_agent_id == "readonly-reviewer"
    assert result.outputs["child_agent_id"] == "readonly-reviewer"
    assert result.outputs["findings"][0]["priority"] == "P1"
    assert call.command is not None
    instruction = (
        "Review objective: preserve project plan lineage\n"
        "Criteria: no P0 findings, no P1 findings\n"
        "Worktree: /repo\n"
        "Diff: git diff -- src tests\n"
        "Verifier refs: pytest:plan-lineage\n"
        "Repository instructions: AGENTS.md"
    )
    assert call.command["params"] == {
        "goal": instruction,
        "instruction": instruction,
        "timeout_seconds": 30,
        "mode": "sync",
        "permission_mode": "readonly",
        "workspace_root": "/repo",
        "cwd": "/repo",
    }


def test_fixed_readonly_reviewer_preserves_typed_mutation_denial() -> None:
    call = _RecordingCall(
        {
            "status": BRAIN_ACTION_STATUS_FAILED,
            "summary": "reviewer mutation denied",
            "error": {
                "code": "POLICY_DENIED",
                "message": "readonly reviewer cannot write files",
            },
        }
    )
    result = A2aRuntimeDelegateAdapter(
        a2a_call=call,
        parent_agent_id="parent",
    ).review_readonly(
        reviewer_agent_id="readonly-reviewer",
        objective="review app.py",
        criteria=["report findings only"],
        worktree="/repo",
        diff="git diff -- app.py",
        verifier_refs=["pytest:app"],
        repository_instructions="AGENTS.md",
        timeout_seconds=30,
    )

    assert result.ok is False
    assert result.error_code == "POLICY_DENIED"
    assert result.error_message == "readonly reviewer cannot write files"
    assert call.command is not None
    assert call.command["params"]["permission_mode"] == "readonly"


@pytest.mark.parametrize(
    "outputs",
    (
        {"child_agent_id": "", "findings": []},
        {"child_agent_id": "child-1", "findings": {}},
        {"child_agent_id": "child-1", "findings": [42]},
        {
            "child_agent_id": "child-1",
            "findings": [{"priority": "P1", "owner": "", "message": "gap"}],
        },
    ),
)
def test_fixed_readonly_reviewer_rejects_invalid_result(
    outputs: dict[str, Any],
) -> None:
    call = _RecordingCall(
        {
            "status": BRAIN_ACTION_STATUS_SUCCESS,
            "summary": "review complete",
            "outputs": outputs,
        }
    )
    result = A2aRuntimeDelegateAdapter(
        a2a_call=call,
        parent_agent_id="parent",
    ).review_readonly(
        reviewer_agent_id="readonly-reviewer",
        objective="review app.py",
        criteria=["report findings only"],
        worktree="/repo",
        diff="git diff -- app.py",
        verifier_refs=["pytest:app"],
        repository_instructions="AGENTS.md",
        timeout_seconds=30,
    )

    assert result.ok is False
    assert result.error_code == "A2A_REVIEW_INVALID_RESULT"
    assert result.target_agent_id == "readonly-reviewer"


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


def test_delegate_isolates_identical_inputs_between_parent_sessions() -> None:
    call = _RecordingCall({"status": BRAIN_ACTION_STATUS_SUCCESS, "summary": "ok"})
    adapter = A2aRuntimeDelegateAdapter(a2a_call=call, parent_agent_id="parent")

    adapter.bind_observability(
        session_id="session-1",
        turn_id="turn-1",
        invocation_id="11111111-1111-4111-8111-111111111111",
        execution_id="21111111-1111-4111-8111-111111111111",
    )
    adapter.delegate(agent_id="a", instruction="do x", timeout_seconds=10)
    first_key = call.command["idempotency_key"]
    first_session = call.session_id

    adapter.bind_observability(
        session_id="session-2",
        turn_id="turn-1",
        invocation_id="31111111-1111-4111-8111-111111111111",
        execution_id="41111111-1111-4111-8111-111111111111",
    )
    adapter.delegate(agent_id="a", instruction="do x", timeout_seconds=10)

    assert call.command["idempotency_key"] != first_key
    assert call.session_id != first_session
    assert call.session_id == "task-delegate::session-2"


def test_followup_turn_reuses_child_session_without_replaying_prior_result() -> None:
    call = _RecordingCall({"status": BRAIN_ACTION_STATUS_SUCCESS, "summary": "ok"})
    adapter = A2aRuntimeDelegateAdapter(a2a_call=call, parent_agent_id="parent")

    adapter.bind_observability(
        session_id="session-1",
        turn_id="turn-1",
        invocation_id="11111111-1111-4111-8111-111111111111",
        execution_id="21111111-1111-4111-8111-111111111111",
    )
    adapter.delegate(agent_id="a", instruction="draft", timeout_seconds=10)
    first_key = call.command["idempotency_key"]
    first_session = call.session_id

    adapter.bind_observability(
        session_id="session-1",
        turn_id="turn-2",
        invocation_id="31111111-1111-4111-8111-111111111111",
        execution_id="41111111-1111-4111-8111-111111111111",
    )
    adapter.delegate(agent_id="a", instruction="revise", timeout_seconds=10)

    assert call.session_id == first_session == "task-delegate::session-1"
    assert call.command["idempotency_key"] != first_key


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
                "status": "RUNNING",
                "task_id": task_id,
                "trace_id": trace_id,
                "summary": "still running",
            }

        def cancel_task(self, *, task_id, session_id, trace_id):
            self.cancelled.append(task_id)
            return {
                "status": "CANCELED",
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


def test_async_resume_normalizes_durable_completion_status() -> None:
    class _LifecycleCall(_RecordingCall):
        def poll_task(self, *, task_id, session_id, trace_id):
            del session_id
            return {
                "status": "COMPLETED",
                "task_id": task_id,
                "trace_id": trace_id,
                "summary": "delegated work completed",
                "result_inline": {"body": "result text"},
            }

    adapter = A2aRuntimeDelegateAdapter(
        a2a_call=_LifecycleCall({}),
        parent_agent_id="parent",
    )

    result = adapter.resume(task_id="job-1")

    assert result.ok is True
    assert result.status == "completed"
    assert result.content == "delegated work completed"
    assert result.outputs["outputs"] == {"body": "result text"}


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
