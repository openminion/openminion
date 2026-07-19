from __future__ import annotations

from threading import Event
from time import monotonic, sleep

from openminion.services.runtime import AgentRuntimeManager, TurnRequest, TurnResponse


def _slow_executor(sleep_s: float = 0.1):
    def _executor(req, emit_chunk, cancel_event):  # noqa: ANN001
        del emit_chunk
        for _ in range(20):
            if cancel_event.is_set():
                break
            sleep(sleep_s / 20)
        return TurnResponse(final_text=f"done:{req.trace_id}")

    return _executor


def test_global_concurrency_limit() -> None:
    active_peak = [0]
    active_now = [0]
    gate = Event()

    def _executor(req, emit_chunk, cancel_event):  # noqa: ANN001
        del emit_chunk
        active_now[0] += 1
        if active_now[0] > active_peak[0]:
            active_peak[0] = active_now[0]
        gate.wait(timeout=2)
        sleep(0.01)
        active_now[0] -= 1
        return TurnResponse(final_text=f"ok:{req.trace_id}")

    concurrency_limit = 2
    manager = AgentRuntimeManager(
        turn_executor=_executor,
        max_agents_hot=10,
        max_global_concurrency=concurrency_limit,
    )
    manager.start()
    try:
        handles = []
        # Submit to different agents to bypass per-agent FIFO
        for i in range(5):
            h = manager.submit_turn(
                TurnRequest(
                    trace_id=f"trace-{i}",
                    agent_id=f"agent-{i}",
                    session_id="sess",
                    input_text="x",
                )
            )
            handles.append(h)

        sleep(0.05)  # let workers hit the gate
        gate.set()

        for h in handles:
            h.result(timeout_s=4)

        assert active_peak[0] <= concurrency_limit, (
            f"peak concurrency {active_peak[0]} exceeded limit {concurrency_limit}"
        )
    finally:
        manager.shutdown()


def test_set_limits_updates_live() -> None:
    counters = {"calls": 0}

    def _executor(req, emit_chunk, cancel_event):  # noqa: ANN001
        del emit_chunk, cancel_event
        counters["calls"] += 1
        sleep(0.02)
        return TurnResponse(final_text=f"ok:{req.trace_id}")

    manager = AgentRuntimeManager(
        turn_executor=_executor,
        max_agents_hot=4,
        max_global_concurrency=1,
    )
    manager.start()
    try:
        manager.set_limits(max_agents_hot=4, max_global_concurrency=4)

        handles = []
        for i in range(4):
            h = manager.submit_turn(
                TurnRequest(
                    trace_id=f"sl-{i}",
                    agent_id=f"sl-agent-{i}",
                    session_id="sess",
                    input_text="x",
                )
            )
            handles.append(h)

        start = monotonic()
        for h in handles:
            h.result(timeout_s=5)
        elapsed = monotonic() - start

        # With concurrency=4 and 4 turns of 20ms each, total should be < 200ms
        assert elapsed < 0.5, (
            f"elapsed={elapsed:.3f}s should be much less than 0.5s with concurrency=4"
        )
    finally:
        manager.shutdown()


def test_concurrency_limit_one_serializes_across_agents() -> None:
    order: list[str] = []

    def _executor(req, emit_chunk, cancel_event):  # noqa: ANN001
        del emit_chunk, cancel_event
        order.append(req.trace_id)
        sleep(0.05)
        return TurnResponse(final_text=f"ok:{req.trace_id}")

    manager = AgentRuntimeManager(
        turn_executor=_executor,
        max_agents_hot=4,
        max_global_concurrency=1,
    )
    manager.start()
    try:
        handles = [
            manager.submit_turn(
                TurnRequest(
                    trace_id=f"trace-{i}",
                    agent_id=f"agent-{i}",
                    session_id="sess",
                    input_text="x",
                )
            )
            for i in range(3)
        ]
        for h in handles:
            h.result(timeout_s=5)

        assert len(order) == 3
    finally:
        manager.shutdown()
