from __future__ import annotations

from time import sleep

from openminion.services.runtime import (
    AgentRuntimeManager,
    TurnChunk,
    TurnError,
    TurnRequest,
    TurnResponse,
)
from openminion.modules.runtime.contracts import TURN_STREAM_SCHEMA_VERSION
from openminion.services.runtime.constants import TURN_STREAM_HISTORY_LIMIT


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
        all_chunks = list(handle.stream(timeout_s=5))
        assert any(c.kind == "partial" for c in all_chunks)

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
        assert second_pass == [], f"Expected empty second pass, got: {second_pass}"
        assert first_pass, "First pass should have chunks"
    finally:
        manager.shutdown()


def test_turn_chunks_have_stable_sequence_and_terminal_status() -> None:
    def _executor(req, emit_chunk, cancel_event):  # noqa: ANN001
        del cancel_event
        emit_chunk(TurnChunk(trace_id=req.trace_id, kind="token", data={"text": "a"}))
        return TurnResponse(final_text="done")

    manager = AgentRuntimeManager(turn_executor=_executor)
    manager.start()
    try:
        handle = manager.submit_turn(
            TurnRequest(
                trace_id="stream-contract",
                agent_id="stream-agent",
                session_id="sess",
                input_text="go",
            )
        )
        chunks = list(handle.stream(timeout_s=5))

        assert [chunk.sequence for chunk in chunks] == list(range(1, len(chunks) + 1))
        assert all(
            chunk.schema_version == TURN_STREAM_SCHEMA_VERSION for chunk in chunks
        )
        assert [chunk.event_id for chunk in chunks] == [
            f"stream-contract:{sequence}" for sequence in range(1, len(chunks) + 1)
        ]
        terminal = chunks[-1]
        assert terminal.kind == "status"
        assert terminal.data["status_key"] == "completed"
        assert terminal.data["terminal"] is True
        assert terminal.data["facts"]["status_key"] == "completed"
    finally:
        manager.shutdown()


def test_turn_subscribers_replay_without_competing_for_chunks() -> None:
    def _executor(req, emit_chunk, cancel_event):  # noqa: ANN001
        del cancel_event
        emit_chunk(TurnChunk(trace_id=req.trace_id, kind="token", data={"text": "a"}))
        emit_chunk(TurnChunk(trace_id=req.trace_id, kind="token", data={"text": "b"}))
        return TurnResponse(final_text="ab")

    manager = AgentRuntimeManager(turn_executor=_executor)
    manager.start()
    try:
        handle = manager.submit_turn(
            TurnRequest(
                trace_id="stream-replay",
                agent_id="stream-agent",
                session_id="sess",
                input_text="go",
            )
        )
        handle.result(timeout_s=5)
        first = list(handle.subscribe(timeout_s=1))
        second = list(handle.subscribe(timeout_s=1))
        suffix = list(handle.subscribe(after_sequence=2, timeout_s=1))

        assert first == second
        assert suffix == [chunk for chunk in first if chunk.sequence > 2]
    finally:
        manager.shutdown()


def test_turn_replay_history_is_bounded() -> None:
    def _executor(req, emit_chunk, cancel_event):  # noqa: ANN001
        del cancel_event
        for index in range(TURN_STREAM_HISTORY_LIMIT + 40):
            emit_chunk(
                TurnChunk(trace_id=req.trace_id, kind="token", data={"index": index})
            )
        return TurnResponse(final_text="done")

    manager = AgentRuntimeManager(turn_executor=_executor)
    manager.start()
    try:
        handle = manager.submit_turn(
            TurnRequest(
                trace_id="stream-bounded",
                agent_id="stream-agent",
                session_id="sess",
                input_text="go",
            )
        )
        handle.result(timeout_s=5)
        retained = list(handle.subscribe(timeout_s=1))

        assert len(retained) == TURN_STREAM_HISTORY_LIMIT
        assert handle.replay_floor_sequence == retained[0].sequence
        assert retained[-1].kind == "status"
    finally:
        manager.shutdown()


def test_failed_turn_emits_typed_terminal_error_status() -> None:
    def _executor(req, emit_chunk, cancel_event):  # noqa: ANN001
        del req, emit_chunk, cancel_event
        return TurnResponse(
            final_text="",
            errors=[TurnError(code="provider_failed", message="provider unavailable")],
        )

    manager = AgentRuntimeManager(turn_executor=_executor)
    manager.start()
    try:
        handle = manager.submit_turn(
            TurnRequest(
                trace_id="stream-error",
                agent_id="stream-agent",
                session_id="sess",
                input_text="go",
            )
        )
        chunks = list(handle.stream(timeout_s=5))

        terminal = chunks[-1]
        assert terminal.kind == "status"
        assert terminal.data["status_key"] == "error"
        assert terminal.data["terminal"] is True
        assert terminal.data["detail_text"] == "provider unavailable"
    finally:
        manager.shutdown()
