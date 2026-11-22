from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

from openminion.modules.telemetry.usage.types import RunStats
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
        mock.patch(
            "openminion.services.runtime.daemon._emit_chat_phase_timing"
        ) as emit_timing,
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
    emit_timing.assert_called_once()
    assert emit_timing.call_args.kwargs["request"] == "ingress-request"
    assert response.final_text == "shared ingress ok"


@pytest.mark.parametrize(
    ("stats", "metadata", "expected_tokens", "expected_stats"),
    [
        (
            RunStats(input_tokens=11841, output_tokens=430, llm_calls=1),
            {},
            (11841, 430),
            RunStats(input_tokens=11841, output_tokens=430, llm_calls=1).as_payload(),
        ),
        (
            RunStats(input_tokens=12, output_tokens=3, llm_calls=1),
            {"usage_prompt_tokens": 99, "usage_completion_tokens": 88},
            (12, 3),
            RunStats(input_tokens=12, output_tokens=3, llm_calls=1).as_payload(),
        ),
        (RunStats(), {}, (0, 0), {}),
        (
            RunStats(),
            {"usage_prompt_tokens": 99, "usage_completion_tokens": 88},
            (0, 0),
            {},
        ),
        (None, {}, (0, 0), {}),
        (
            None,
            {"usage_prompt_tokens": 99, "usage_completion_tokens": 88},
            (0, 0),
            {},
        ),
    ],
)
def test_execute_turn_telemetry_tokens_match_run_stats(
    stats: RunStats | None,
    metadata: dict[str, int],
    expected_tokens: tuple[int, int],
    expected_stats: dict[str, int],
) -> None:
    request = TurnRequest(
        trace_id="trace-4",
        agent_id="main",
        session_id="session-4",
        input_text="hello",
    )

    with (
        mock.patch(
            "openminion.services.runtime.daemon.runtime_turn_request_from_manager_request",
            return_value=SimpleNamespace(timeout_seconds=2.0),
        ),
        mock.patch(
            "openminion.services.runtime.daemon.execute_runtime_turn",
            return_value=RuntimeTurnResult(
                id="turn-4",
                channel="console",
                target="api-user",
                body="ok",
                metadata=dict(metadata),
                agent_id="main",
                stats=stats,
            ),
        ),
        mock.patch("openminion.services.runtime.daemon._emit_chat_phase_timing"),
    ):
        response = execute_turn(
            runtime=SimpleNamespace(config_path="config.json"),
            request=request,
            emit_chunk=lambda _chunk: None,
            cancel_event=SimpleNamespace(is_set=lambda: False),
        )

    assert (response.telemetry.tokens_in, response.telemetry.tokens_out) == (
        expected_tokens
    )
    assert response.stats == expected_stats
    assert response.metadata == metadata
    assert response.final_text == "ok"
    assert response.telemetry.duration_ms >= 0
    assert response.errors == []


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


def test_execute_turn_cancel_during_execution_returns_cancelled() -> None:
    request = TurnRequest(
        trace_id="trace-3",
        agent_id="main",
        session_id="session-3",
        input_text="hello",
    )
    runtime = SimpleNamespace(config_path="config.json")
    cancel_event = SimpleNamespace(is_set=lambda: True)

    with mock.patch(
        "openminion.services.runtime.daemon._execute_runtime_turn_with_timer",
        side_effect=RuntimeError("Turn cancelled."),
    ):
        response = execute_turn(
            runtime=runtime,
            request=request,
            emit_chunk=lambda _chunk: None,
            cancel_event=cancel_event,
        )

    assert response.errors[0].code == "cancelled"
