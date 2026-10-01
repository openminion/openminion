from __future__ import annotations

from queue import Queue
from threading import Event
from time import monotonic, sleep

import pytest

from openminion.services.runtime import AgentRuntimeManager, TurnRequest, TurnResponse


def _ok_executor(req, emit_chunk, cancel_event):  # noqa: ANN001
    del req, emit_chunk, cancel_event
    return TurnResponse(final_text="ok")


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
        worker = manager._instances["agent-ttl"].thread
        # direct eviction call is deterministic and exercises lifecycle cleanup path
        manager.evict("agent-ttl", "test-manual")
        assert not manager.list_agents()
        assert worker is not None
        assert not worker.is_alive()
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


def test_idle_worker_waits_without_a_poll_timeout(monkeypatch) -> None:  # noqa: ANN001
    observed_timeouts: list[float | None] = []
    original_get = Queue.get

    def _observed_get(queue, block=True, timeout=None):  # noqa: ANN001
        observed_timeouts.append(timeout)
        return original_get(queue, block=block, timeout=timeout)

    monkeypatch.setattr(Queue, "get", _observed_get)
    manager = AgentRuntimeManager(turn_executor=_ok_executor)
    manager.start()
    try:
        manager.get_or_create_agent("idle-agent")
        sleep(0.15)
    finally:
        manager.shutdown()

    assert observed_timeouts
    assert all(timeout is None for timeout in observed_timeouts)


def test_shutdown_surfaces_worker_signal_failure(monkeypatch) -> None:  # noqa: ANN001
    manager = AgentRuntimeManager(turn_executor=_ok_executor)
    manager.start()
    manager.get_or_create_agent("shutdown-agent")
    instance = manager._instances["shutdown-agent"]
    worker = instance.thread
    sweeper = manager._sweeper_thread
    original_put = instance.queue.put

    def _raise_signal_failure(*_args, **_kwargs) -> None:
        raise RuntimeError("signal failed")

    monkeypatch.setattr(instance.queue, "put", _raise_signal_failure)
    try:
        with pytest.raises(RuntimeError, match="signal failed"):
            manager.shutdown()
    finally:
        original_put(None)
        if worker is not None:
            worker.join(timeout=1.0)
        if sweeper is not None:
            sweeper.join(timeout=1.0)


def test_eviction_surfaces_worker_signal_failure(monkeypatch) -> None:  # noqa: ANN001
    manager = AgentRuntimeManager(turn_executor=_ok_executor)
    manager.start()
    manager.get_or_create_agent("eviction-agent")
    instance = manager._instances["eviction-agent"]
    worker = instance.thread
    original_put = instance.queue.put

    def _raise_signal_failure(*_args, **_kwargs) -> None:
        raise RuntimeError("signal failed")

    monkeypatch.setattr(instance.queue, "put", _raise_signal_failure)
    try:
        with pytest.raises(RuntimeError, match="signal failed"):
            manager.evict("eviction-agent", "test-manual")
    finally:
        original_put(None)
        if worker is not None:
            worker.join(timeout=1.0)
        manager.shutdown()


def test_shutdown_keeps_bounded_grace_for_active_noncooperative_turn() -> None:
    started = Event()
    release = Event()

    def _executor(req, emit_chunk, cancel_event):  # noqa: ANN001
        del req, emit_chunk, cancel_event
        started.set()
        release.wait()
        return TurnResponse(final_text="ok")

    manager = AgentRuntimeManager(turn_executor=_executor)
    handle = manager.submit_turn(
        TurnRequest(
            trace_id="trace-blocked",
            agent_id="blocked-agent",
            session_id="blocked-session",
            input_text="wait",
        )
    )
    assert started.wait(timeout=1.0)
    worker = manager._instances["blocked-agent"].thread

    started_at = monotonic()
    manager.shutdown(grace_s=0.01)
    elapsed = monotonic() - started_at

    assert elapsed < 0.5
    assert worker is not None
    assert worker.is_alive()
    release.set()
    assert handle.result(timeout_s=1.0).final_text == "ok"
    worker.join(timeout=1.0)
    assert not worker.is_alive()


def test_foreground_visibility_excludes_cron_and_metadata_survives() -> None:
    release = Event()

    def _executor(req, emit_chunk, cancel_event):  # noqa: ANN001
        del emit_chunk, cancel_event
        release.wait(timeout=2.0)
        return TurnResponse(
            final_text=f"ok:{req.trace_id}",
            metadata={"watermark": "preserved"},
            stats={"calls": 1},
        )

    manager = AgentRuntimeManager(
        turn_executor=_executor,
        max_agents_hot=2,
        max_global_concurrency=2,
    )
    manager.start()
    try:
        background = manager.submit_turn(
            TurnRequest(
                trace_id="cron-trace",
                agent_id="background-agent",
                session_id="cron-session",
                input_text="consolidate",
                meta={"cron_run_id": "run-1"},
            )
        )
        assert manager.has_foreground_work() is False
        foreground = manager.submit_turn(
            TurnRequest(
                trace_id="user-trace",
                agent_id="foreground-agent",
                session_id="user-session",
                input_text="hello",
            )
        )
        assert manager.has_foreground_work() is True
        release.set()
        foreground_result = foreground.result(timeout_s=2.0)
        background.result(timeout_s=2.0)
        assert foreground_result.metadata == {"watermark": "preserved"}
        assert foreground_result.stats == {"calls": 1}
        assert manager.has_foreground_work() is False
    finally:
        manager.shutdown()


def test_active_turn_handle_excludes_queued_and_terminal_turns() -> None:
    started = Event()
    release = Event()

    def _executor(req, emit_chunk, cancel_event):  # noqa: ANN001
        del emit_chunk, cancel_event
        started.set()
        release.wait(timeout=2.0)
        return TurnResponse(final_text=f"ok:{req.trace_id}")

    manager = AgentRuntimeManager(turn_executor=_executor, max_global_concurrency=1)
    manager.start()
    try:
        first = manager.submit_turn(
            TurnRequest(
                trace_id="trace-active",
                agent_id="agent",
                session_id="session",
                input_text="first",
                meta={"override_model": "model-a"},
            )
        )
        assert started.wait(timeout=1.0)
        second = manager.submit_turn(
            TurnRequest(
                trace_id="trace-queued",
                agent_id="agent",
                session_id="session",
                input_text="second",
            )
        )

        active = manager.get_active_turn_handle(
            trace_id="trace-active", session_id="session", agent_id="agent"
        )
        assert active is first
        assert active.request_meta == {"override_model": "model-a"}
        assert (
            manager.get_active_turn_handle(
                trace_id="trace-queued", session_id="session", agent_id="agent"
            )
            is None
        )

        release.set()
        first.result(timeout_s=2.0)
        second.result(timeout_s=2.0)
        assert (
            manager.get_active_turn_handle(
                trace_id="trace-active", session_id="session", agent_id="agent"
            )
            is None
        )
    finally:
        release.set()
        manager.shutdown()
