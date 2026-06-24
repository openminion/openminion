from __future__ import annotations

from threading import Event
from types import SimpleNamespace
from unittest import mock

from openminion.services.runtime.daemon import execute_turn
from openminion.services.runtime.manager import TurnRequest


def test_execute_turn_maps_structural_progress_payloads_to_chunk_kinds() -> None:
    emitted = []

    def _emit(chunk):  # noqa: ANN001
        emitted.append(chunk)

    request = TurnRequest(
        trace_id="trace-stream",
        agent_id="main",
        session_id="sess-1",
        input_text="hello",
        stream=True,
    )

    def _fake_execute_runtime_turn(*, runtime, request, progress_callback):  # noqa: ANN001
        del runtime, request
        progress_callback(
            {
                "kind": "tool_started",
                "tool_name": "web.search",
                "args": {"q": "hello"},
            }
        )
        progress_callback(
            {
                "kind": "tool_completed",
                "tool_name": "web.search",
                "args": {"q": "hello"},
                "ok": True,
                "duration_ms": 18,
                "content": "ok",
            }
        )
        progress_callback(
            {
                "kind": "budget_event",
                "event_type": "budget.extended",
                "cap": 8,
            }
        )
        return SimpleNamespace(
            body="final",
            metadata={},
            stats=SimpleNamespace(has_any_data=False, as_payload=lambda: {}),
        )

    with (
        mock.patch(
            "openminion.services.runtime.daemon.runtime_turn_request_from_manager_request",
            return_value=SimpleNamespace(),
        ),
        mock.patch(
            "openminion.services.runtime.daemon.execute_runtime_turn",
            side_effect=_fake_execute_runtime_turn,
        ),
    ):
        response = execute_turn(
            runtime=SimpleNamespace(),
            request=request,
            emit_chunk=_emit,
            cancel_event=Event(),
        )

    assert response.final_text == "final"
    assert [chunk.kind for chunk in emitted] == [
        "tool_started",
        "tool_completed",
        "budget_event",
    ]
