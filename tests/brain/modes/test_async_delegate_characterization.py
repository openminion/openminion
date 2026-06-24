from __future__ import annotations

from types import SimpleNamespace

from openminion.modules.brain.adapters.a2a import A2actlAdapter


class _FakeA2ARuntime:
    def __init__(self) -> None:
        self.started: list[object] = []
        self.called: list[object] = []
        self.status_requests: list[str] = []
        self.cancel_requests: list[str] = []

    def job_start(self, envelope):  # type: ignore[no-untyped-def]
        self.started.append(envelope)
        return "job-async-1"

    def call(self, envelope):  # type: ignore[no-untyped-def]
        self.called.append(envelope)
        raise AssertionError(
            "expect_async should route through job_start(), not call()"
        )

    def job_status(self, task_id: str) -> object:
        self.status_requests.append(task_id)
        return SimpleNamespace(
            task_id=task_id,
            state="RUNNING",
            result_inline=None,
            error=None,
        )

    def job_cancel(self, task_id: str) -> object:
        self.cancel_requests.append(task_id)
        return SimpleNamespace(
            task_id=task_id,
            state="CANCELED",
            result_inline=None,
            error={"code": "A2A_JOB_CANCELLED", "message": "Job canceled"},
        )


def _adapter(runtime: _FakeA2ARuntime) -> A2actlAdapter:
    adapter = A2actlAdapter(agent_id="router-agent")
    adapter._ensure_runtime = lambda: runtime  # type: ignore[method-assign]
    return adapter


def test_expect_async_routes_a2a_commands_through_job_start() -> None:
    runtime = _FakeA2ARuntime()
    adapter = _adapter(runtime)

    result = adapter.call(
        command={
            "command_id": "cmd-1",
            "target_agent_id": "agent.worker",
            "method": "delegate",
            "params": {"goal": "check status"},
            "expect_async": True,
            "timeout_ms": 2500,
            "idempotency_key": "delegate-key-1",
        },
        session_id="s-async-delegate",
        trace_id="t-async-delegate",
    )

    assert result["status"] == "running"
    assert result["task_id"] == "job-async-1"
    assert len(runtime.started) == 1
    assert runtime.called == []


def test_poll_task_reads_status_for_known_async_job() -> None:
    runtime = _FakeA2ARuntime()
    adapter = _adapter(runtime)

    result = adapter.poll_task(
        task_id="job-async-1",
        session_id="s-async-delegate",
        trace_id="t-async-delegate",
    )

    assert result["status"] == "running"
    assert result["task_id"] == "job-async-1"
    assert runtime.status_requests == ["job-async-1"]


def test_cancel_task_cancels_running_async_job() -> None:
    runtime = _FakeA2ARuntime()
    adapter = _adapter(runtime)

    result = adapter.cancel_task(
        task_id="job-async-1",
        session_id="s-async-delegate",
        trace_id="t-async-delegate",
    )

    assert result["status"] == "cancelled"
    assert result["error"]["code"] == "A2A_JOB_CANCELLED"
    assert runtime.cancel_requests == ["job-async-1"]
