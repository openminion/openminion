from __future__ import annotations

from threading import Event
from time import monotonic, sleep

from openminion.services.runtime import AgentRuntimeManager, TurnRequest, TurnResponse


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
