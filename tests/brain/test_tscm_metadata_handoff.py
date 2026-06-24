from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from openminion.modules.brain.loop.adaptive import (
    _adaptive_loop_metadata,
    _postprocess_adaptive_response_trailers,
)
from openminion.modules.brain.loop.tools import (
    ADAPTIVE_TERM_FINAL_TEXT,
    AdaptiveToolLoopOutcome,
    AdaptiveToolLoopState,
)
from openminion.modules.brain.schemas import BudgetCounters, WorkingState
from openminion.modules.brain.trailers import (
    EXPECTED_TRAILERS_METADATA_KEY,
    TRAILER_LANE_MACC,
    TRAILER_LANE_SWSC,
)


@dataclass
class _FakeSessionAPI:
    events: list[dict[str, Any]] = field(default_factory=list)

    def append_event(
        self,
        session_id: str,
        event_type: str,
        payload: dict[str, Any],
        **kwargs: Any,
    ) -> None:
        self.events.append(
            {
                "session_id": session_id,
                "event_type": event_type,
                "payload": payload,
                "kwargs": dict(kwargs),
            }
        )


def _ctx(session_api: _FakeSessionAPI) -> SimpleNamespace:
    state = WorkingState(
        session_id="s-adaptive-metadata",
        agent_id="agent",
        trace_id="trace",
        budgets_remaining=BudgetCounters(
            ticks=10,
            tool_calls=5,
            a2a_calls=0,
            tokens=5000,
            time_ms=120000,
        ),
    )
    return SimpleNamespace(
        state=state,
        _services=SimpleNamespace(
            runner=SimpleNamespace(session_api=session_api),
        ),
    )


def test_adaptive_metadata_handoff_emits_expected_and_emitted_events() -> None:
    session_api = _FakeSessionAPI()
    ctx = _ctx(session_api)
    metadata = _adaptive_loop_metadata(ctx, purpose="act")
    outcome = AdaptiveToolLoopOutcome(
        profile_name="default",
        mode_name="act_adaptive",
        termination_reason=ADAPTIVE_TERM_FINAL_TEXT,
        state=AdaptiveToolLoopState(),
        allowed_tools=frozenset(),
        final_text="done",
        session_work_summary="checkpoint",
    )

    _postprocess_adaptive_response_trailers(
        ctx,
        outcome,
        request_metadata=metadata,
    )

    assert metadata[EXPECTED_TRAILERS_METADATA_KEY] == [
        TRAILER_LANE_MACC,
        TRAILER_LANE_SWSC,
    ]
    expected_event = next(
        event
        for event in session_api.events
        if event["event_type"] == "trailer.expected"
    )
    emitted_event = next(
        event
        for event in session_api.events
        if event["event_type"] == "trailer.emitted"
    )
    assert expected_event["payload"] == {
        "lanes": [TRAILER_LANE_MACC, TRAILER_LANE_SWSC],
        "route": "adaptive_final",
    }
    assert emitted_event["payload"] == {
        "lanes": [TRAILER_LANE_SWSC],
        "route": "adaptive_final",
        "sources": {TRAILER_LANE_SWSC: ["unknown"]},
    }
