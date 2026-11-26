"""A failed usage-event read is distinguishable from an empty turn history."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from openminion.services.brain.post_execution.postprocess_metadata import (
    _build_turn_response_metadata,
)


class _SessionAPI:
    def __init__(
        self, *, events_by_type: dict[str, list[dict[str, Any]]], fail: set[str]
    ):
        self._events_by_type = events_by_type
        self._fail = fail

    def list_events(
        self,
        session_id: str,
        *,
        event_type: str,
        limit: Any = None,
        **_: Any,
    ) -> list[dict[str, Any]]:
        if event_type in self._fail:
            raise OSError("event store unavailable")
        return self._events_by_type.get(event_type, [])

    def get_active_task_plan(self, session_id: str) -> dict[str, Any] | None:
        return None


def _metadata(
    *, events_by_type: dict[str, list[dict[str, Any]]], fail: set[str]
) -> dict[str, str]:
    runner = SimpleNamespace(
        session_api=_SessionAPI(events_by_type=events_by_type, fail=fail)
    )
    stub = SimpleNamespace(
        _config=SimpleNamespace(
            agents={"openminion": SimpleNamespace(name="openminion")},
            default_agent="openminion",
        ),
        _provider=SimpleNamespace(name="stub"),
    )
    return _build_turn_response_metadata(
        stub,
        runner=runner,
        step_out=SimpleNamespace(action_result=None, status="done"),
        session_id="session-1",
        request_id="trace-1",
        elapsed_ms=1.0,
        llm_steps=1,
        termination_reason="model_final",
    )


def _completed(*, trace_id: str = "trace-1") -> dict[str, Any]:
    return {
        "type": "llm.call.completed",
        "trace_id": trace_id,
        "payload": {
            "llm_call_id": "call-1",
            "usage": {"input_tokens": 31, "output_tokens": 7},
        },
    }


def test_a_failed_kind_keeps_the_known_totals_and_flags_the_metadata() -> None:
    metadata = _metadata(
        events_by_type={"llm.call.completed": [_completed()]},
        fail={"llm.call.failed"},
    )

    assert metadata["llm_usage_events_incomplete"] == "true"
    assert metadata["total_tokens_used"] == "38"
    assert metadata["llm_calls_count"] == "1"


def test_both_kinds_failing_also_flags_the_metadata() -> None:
    metadata = _metadata(
        events_by_type={}, fail={"llm.call.completed", "llm.call.failed"}
    )

    assert metadata["llm_usage_events_incomplete"] == "true"


def test_an_empty_history_is_not_flagged() -> None:
    metadata = _metadata(events_by_type={}, fail=set())

    assert "llm_usage_events_incomplete" not in metadata


def test_a_successful_read_is_not_flagged_and_keeps_its_totals() -> None:
    metadata = _metadata(
        events_by_type={"llm.call.completed": [_completed()]}, fail=set()
    )

    assert "llm_usage_events_incomplete" not in metadata
    assert metadata["total_input_tokens_used"] == "31"


def test_another_traces_calls_are_still_isolated() -> None:
    metadata = _metadata(
        events_by_type={"llm.call.completed": [_completed(trace_id="trace-2")]},
        fail=set(),
    )

    assert "llm_usage_events_incomplete" not in metadata
    # The count falls back to llm_steps because no event survives the trace
    # filter. The totals carry the isolation proof: the 38-token case above is
    # the positive control, so any other-trace usage showing up here fails.
    assert metadata["llm_calls_count"] == "1"
    assert metadata["total_tokens_used"] == "0"
    assert metadata["total_input_tokens_used"] == "0"
