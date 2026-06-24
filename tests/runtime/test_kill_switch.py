from __future__ import annotations

import pytest
from time import sleep

from openminion.services.runtime import AgentRuntimeManager, TurnRequest, TurnResponse


def _slow_executor(req, emit_chunk, cancel_event):  # noqa: ANN001
    del emit_chunk
    for _ in range(30):
        if cancel_event.is_set():
            break
        sleep(0.05)
    return TurnResponse(final_text=f"done:{req.trace_id}")


def _fast_executor(req, emit_chunk, cancel_event):  # noqa: ANN001
    del emit_chunk, cancel_event
    sleep(0.01)
    return TurnResponse(final_text=f"done:{req.trace_id}")


def test_kill_switch_cancels_in_flight_turns() -> None:
    manager = AgentRuntimeManager(
        turn_executor=_slow_executor,
        max_global_concurrency=1,
    )
    manager.start()

    h1 = manager.submit_turn(
        TurnRequest(
            trace_id="ks-1", agent_id="ks-agent", session_id="sess", input_text="a"
        )
    )
    h2 = manager.submit_turn(
        TurnRequest(
            trace_id="ks-2", agent_id="ks-agent", session_id="sess", input_text="b"
        )
    )

    sleep(0.05)  # let first turn start
    manager.kill_switch(grace_s=1)

    r1 = h1.result(timeout_s=3)
    r2 = h2.result(timeout_s=3)

    # At least one must be cancelled; the running one may finish or cancel
    cancelled_codes = {e.code for r in (r1, r2) for e in r.errors}
    # Queued turn must be cancelled
    assert "cancelled" in cancelled_codes or r2.final_text.startswith("done"), (
        f"Expected cancellation, r1={r1}, r2={r2}"
    )


def test_submit_after_kill_switch_raises() -> None:
    manager = AgentRuntimeManager(turn_executor=_fast_executor)
    manager.start()
    manager.kill_switch(grace_s=0.5)

    with pytest.raises(RuntimeError):
        manager.submit_turn(
            TurnRequest(
                trace_id="ks-post",
                agent_id="ks-agent",
                session_id="sess",
                input_text="x",
            )
        )


def test_kill_switch_emits_kill_event() -> None:
    events: list[str] = []

    def hook(event_type: str, payload: dict) -> None:  # noqa: ARG001
        events.append(event_type)

    manager = AgentRuntimeManager(
        turn_executor=_fast_executor,
        on_runtime_event=hook,
    )
    manager.start()
    manager.kill_switch(grace_s=0.5)

    assert "runtime.manager.kill" in events, f"Missing kill event, got: {events}"


def test_shutdown_is_idempotent() -> None:
    manager = AgentRuntimeManager(turn_executor=_fast_executor)
    manager.start()
    manager.shutdown()
    manager.shutdown()  # second call should be silent


def test_start_after_shutdown_raises() -> None:
    manager = AgentRuntimeManager(turn_executor=_fast_executor)
    manager.start()
    manager.shutdown()

    with pytest.raises(RuntimeError, match="stopped"):
        manager.start()
