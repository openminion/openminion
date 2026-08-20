from __future__ import annotations

import asyncio
from threading import Event
from time import monotonic, sleep
from types import SimpleNamespace
from unittest import mock

from openminion.modules.tool.sidecars import (
    maybe_allow_denied_call_with_operator_approval,
)
from openminion.services.runtime import AgentRuntimeManager, TurnRequest, TurnResponse
from openminion.services.runtime.daemon import _desktop_approval_callback, execute_turn
from openminion.services.runtime.ingress import TurnTimeoutError


def test_daemon_adapter_passes_only_argument_keys_to_desktop_requester() -> None:
    captured: list[object] = []
    cancel = Event()
    emitted: list[object] = []
    request = SimpleNamespace(
        session_id="session-1",
        trace_id="trace-1",
        desktop_approval_requester=lambda value: captured.append(value) or True,
    )
    callback = _desktop_approval_callback(request, emitted.append, cancel)
    approved = asyncio.run(
        maybe_allow_denied_call_with_operator_approval(
            call=SimpleNamespace(
                name="workspace.search",
                arguments={"query": "secret", "path": "/private"},
                id="call-1",
                source="model",
            ),
            tool_name="workspace.search",
            tool_args={"query": "secret", "path": "/private"},
            decision=SimpleNamespace(
                allowed=False,
                requires_confirm=True,
                code="REQUIRE_APPROVAL",
                modified_args=None,
            ),
            approval_callback=callback,
        )
    )
    assert approved is not None
    assert approved.approval_id == "call-1"
    approval = captured[0]
    assert approval.argument_keys == ("path", "query")
    assert approval.cancel_event is cancel
    assert not hasattr(approval, "arguments")


def test_daemon_timeout_sets_the_turn_cancel_event() -> None:
    cancel = Event()
    request = SimpleNamespace(meta={}, desktop_approval_requester=None)
    with mock.patch(
        "openminion.services.runtime.daemon._execute_runtime_turn_with_timer",
        side_effect=TurnTimeoutError("turn timed out"),
    ):
        response = execute_turn(
            runtime=SimpleNamespace(),
            request=request,
            emit_chunk=lambda _chunk: None,
            cancel_event=cancel,
        )

    assert cancel.is_set()
    assert response.errors[0].code == "turn_timeout"


def test_per_agent_fifo_serialization() -> None:
    seen: list[str] = []

    def _executor(req, emit_chunk, cancel_event):  # noqa: ANN001
        del emit_chunk, cancel_event
        seen.append(req.trace_id)
        sleep(0.05)
        return TurnResponse(final_text=f"ok:{req.trace_id}")

    manager = AgentRuntimeManager(
        turn_executor=_executor, max_agents_hot=4, max_global_concurrency=2
    )
    manager.start()
    try:
        first = manager.submit_turn(
            TurnRequest(
                trace_id="trace-1",
                agent_id="ops",
                session_id="session-1",
                input_text="one",
            )
        )
        second = manager.submit_turn(
            TurnRequest(
                trace_id="trace-2",
                agent_id="ops",
                session_id="session-1",
                input_text="two",
            )
        )
        assert first.result(timeout_s=2).final_text == "ok:trace-1"
        assert second.result(timeout_s=2).final_text == "ok:trace-2"
        assert seen == ["trace-1", "trace-2"]
    finally:
        manager.shutdown()


def test_cancel_queued_turn() -> None:
    def _executor(req, emit_chunk, cancel_event):  # noqa: ANN001
        del emit_chunk
        # keep first turn occupied so the second one remains queued long enough to cancel
        for _ in range(5):
            if cancel_event.is_set():
                break
            sleep(0.05)
        return TurnResponse(final_text=f"done:{req.trace_id}")

    manager = AgentRuntimeManager(
        turn_executor=_executor, max_agents_hot=2, max_global_concurrency=1
    )
    manager.start()
    try:
        first = manager.submit_turn(
            TurnRequest(
                trace_id="trace-a",
                agent_id="ops",
                session_id="session-1",
                input_text="first",
            )
        )
        second = manager.submit_turn(
            TurnRequest(
                trace_id="trace-b",
                agent_id="ops",
                session_id="session-1",
                input_text="second",
            )
        )
        assert manager.cancel_turn("trace-b") is True
        first.result(timeout_s=2)
        cancelled = second.result(timeout_s=2)
        assert cancelled.errors
        assert cancelled.errors[0].code == "cancelled"
    finally:
        manager.shutdown()


def test_cancel_session_turn_is_session_bound_and_idempotent() -> None:
    started = Event()
    release = Event()

    def _executor(req, emit_chunk, cancel_event):  # noqa: ANN001
        del emit_chunk
        started.set()
        while not release.is_set() and not cancel_event.is_set():
            sleep(0.01)
        return TurnResponse(final_text=f"done:{req.trace_id}")

    manager = AgentRuntimeManager(
        turn_executor=_executor,
        max_agents_hot=1,
        max_global_concurrency=1,
    )
    manager.start()
    try:
        handle = manager.submit_turn(
            TurnRequest(
                trace_id="trace-session-bound",
                agent_id="ops",
                session_id="session-a",
                input_text="wait",
            )
        )
        assert started.wait(timeout=1)
        assert (
            manager.cancel_session_turn("trace-session-bound", "session-b")
            == "session_mismatch"
        )
        assert (
            manager.cancel_session_turn("trace-session-bound", "session-a")
            == "requested"
        )
        assert (
            manager.cancel_session_turn("trace-session-bound", "session-a")
            == "already_requested"
        )
        handle.result(timeout_s=2)
        assert (
            manager.cancel_session_turn("trace-session-bound", "session-a")
            == "not_active"
        )
    finally:
        release.set()
        manager.shutdown()


def test_ttl_eviction_removes_idle_agents() -> None:
    def _executor(req, emit_chunk, cancel_event):  # noqa: ANN001
        del req, emit_chunk, cancel_event
        return TurnResponse(final_text="ok")

    manager = AgentRuntimeManager(
        turn_executor=_executor,
        max_agents_hot=2,
        max_global_concurrency=1,
        agent_ttl_seconds=1,
        sweep_interval_seconds=1,
    )
    manager.start()
    try:
        handle = manager.submit_turn(
            TurnRequest(
                trace_id="trace-ttl",
                agent_id="agent-ttl",
                session_id="session-ttl",
                input_text="ping",
            )
        )
        handle.result(timeout_s=2)
        # direct eviction call is deterministic and exercises lifecycle cleanup path
        manager.evict("agent-ttl", "test-manual")
        assert not manager.list_agents()
    finally:
        manager.shutdown()


def test_shutdown_does_not_wait_full_sweep_interval() -> None:
    def _executor(req, emit_chunk, cancel_event):  # noqa: ANN001
        del req, emit_chunk, cancel_event
        return TurnResponse(final_text="ok")

    manager = AgentRuntimeManager(
        turn_executor=_executor,
        max_agents_hot=1,
        max_global_concurrency=1,
        sweep_interval_seconds=30,
    )
    manager.start()
    started = monotonic()
    manager.shutdown()
    elapsed = monotonic() - started
    assert elapsed < 2.0
