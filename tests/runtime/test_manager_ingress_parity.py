from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from openminion.services.runtime import TurnRequest
from openminion.services.runtime.daemon import execute_turn
from openminion.services.runtime.ingress import RuntimeTurnResult


def test_execute_turn_enters_shared_runtime_ingress() -> None:
    request = TurnRequest(
        trace_id="trace-1",
        agent_id="main",
        session_id="session-1",
        input_text="hello",
        meta={"channel": "console", "user": "api-user"},
    )
    runtime = SimpleNamespace(config_path="config.json")
    cancel_event = SimpleNamespace(is_set=lambda: False)

    with (
        mock.patch(
            "openminion.services.runtime.daemon.runtime_turn_request_from_manager_request",
            return_value="ingress-request",
        ) as build_request,
        mock.patch(
            "openminion.services.runtime.daemon.execute_runtime_turn",
            return_value=RuntimeTurnResult(
                id="turn-1",
                channel="console",
                target="api-user",
                body="shared ingress ok",
                metadata={},
                agent_id="main",
            ),
        ) as execute_ingress,
    ):
        response = execute_turn(
            runtime=runtime,
            request=request,
            emit_chunk=lambda _chunk: None,
            cancel_event=cancel_event,
        )

    build_request.assert_called_once_with(runtime=runtime, request=request)
    execute_ingress.assert_called_once()
    assert execute_ingress.call_args.kwargs["request"] == "ingress-request"
    assert response.final_text == "shared ingress ok"


def test_execute_turn_cancel_before_execution_skips_ingress() -> None:
    request = TurnRequest(
        trace_id="trace-2",
        agent_id="main",
        session_id="session-2",
        input_text="hello",
    )
    runtime = SimpleNamespace(config_path="config.json")
    cancel_event = SimpleNamespace(is_set=lambda: True)

    with mock.patch(
        "openminion.services.runtime.daemon.execute_runtime_turn"
    ) as execute_ingress:
        response = execute_turn(
            runtime=runtime,
            request=request,
            emit_chunk=lambda _chunk: None,
            cancel_event=cancel_event,
        )

    execute_ingress.assert_not_called()
    assert response.errors[0].code == "cancelled"
