from __future__ import annotations

from time import sleep

from openminion.services.runtime import (
    AgentRuntimeManager,
    TurnChunk,
    TurnRequest,
    TurnResponse,
)


def test_chunk_order() -> None:

    def _executor(req, emit_chunk, cancel_event):  # noqa: ANN001
        del cancel_event
        for i in range(5):
            emit_chunk(
                TurnChunk(trace_id=req.trace_id, kind="token", data={"index": i})
            )
            sleep(0.01)
        return TurnResponse(final_text="done")

    manager = AgentRuntimeManager(turn_executor=_executor)
    manager.start()
    try:
        handle = manager.submit_turn(
            TurnRequest(
                trace_id="stream-1",
                agent_id="stream-agent",
                session_id="sess",
                input_text="go",
                stream=True,
            )
        )
        chunks = list(handle.stream(timeout_s=5))
        # Filter executor-pushed token chunks
        token_chunks = [c for c in chunks if c.kind == "token"]
        indices = [c.data["index"] for c in token_chunks]
        assert indices == list(range(5)), f"Out-of-order chunks: {indices}"
    finally:
        manager.shutdown()


def test_result_after_stream() -> None:

    def _executor(req, emit_chunk, cancel_event):  # noqa: ANN001
        del cancel_event
        emit_chunk(
            TurnChunk(trace_id=req.trace_id, kind="partial", data={"text": "hel"})
        )
        emit_chunk(
            TurnChunk(trace_id=req.trace_id, kind="partial", data={"text": "lo"})
        )
        return TurnResponse(final_text="hello")

    manager = AgentRuntimeManager(turn_executor=_executor)
    manager.start()
    try:
        handle = manager.submit_turn(
            TurnRequest(
                trace_id="stream-2",
                agent_id="stream-agent",
                session_id="sess",
                input_text="go",
            )
        )
        # Exhaust the stream first
        all_chunks = list(handle.stream(timeout_s=5))
        assert any(c.kind == "partial" for c in all_chunks)

        # Then get the result — must be available
        result = handle.result(timeout_s=1)
        assert result.final_text == "hello"
    finally:
        manager.shutdown()


def test_stream_receives_manager_status_chunks() -> None:

    def _executor(req, emit_chunk, cancel_event):  # noqa: ANN001
        del emit_chunk, cancel_event
        return TurnResponse(final_text="ok")

    manager = AgentRuntimeManager(turn_executor=_executor)
    manager.start()
    try:
        handle = manager.submit_turn(
            TurnRequest(
                trace_id="stream-3",
                agent_id="stream-agent",
                session_id="sess",
                input_text="hi",
            )
        )
        chunks = list(handle.stream(timeout_s=5))
        kinds = {c.kind for c in chunks}
        # Manager always pushes "status" and "final_text" chunks
        assert "status" in kinds, f"Missing status chunk, got kinds: {kinds}"
        assert "final_text" in kinds, f"Missing final_text chunk, got kinds: {kinds}"
        status_chunk = next(c for c in chunks if c.kind == "status")
        assert status_chunk.data["status_key"] == "working"
        assert status_chunk.data["label"] == "Working..."
    finally:
        manager.shutdown()


def test_no_chunks_after_stream_closed() -> None:

    def _executor(req, emit_chunk, cancel_event):  # noqa: ANN001
        del emit_chunk, cancel_event
        sleep(0.05)
        return TurnResponse(final_text="ok")

    manager = AgentRuntimeManager(turn_executor=_executor)
    manager.start()
    try:
        handle = manager.submit_turn(
            TurnRequest(
                trace_id="stream-4",
                agent_id="stream-agent",
                session_id="sess",
                input_text="hi",
            )
        )
        first_pass = list(handle.stream(timeout_s=5))
        second_pass = list(handle.stream(timeout_s=1))
        # Second pass should yield nothing (stream already closed)
        assert second_pass == [], f"Expected empty second pass, got: {second_pass}"
        # First pass had content
        assert first_pass, "First pass should have chunks"
    finally:
        manager.shutdown()
