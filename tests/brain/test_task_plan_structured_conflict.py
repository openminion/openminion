from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from openminion.modules.brain.loop.adaptive import _stage_task_plan_events
from openminion.modules.brain.loop.tools import (
    ADAPTIVE_TERM_FINAL_TEXT,
    PLAN_TOOL_ATTEMPTED_SCRATCHPAD_KEY,
    PLAN_TOOL_USED_SCRATCHPAD_KEY,
    AdaptiveToolLoopOutcome,
    AdaptiveToolLoopState,
)
from openminion.modules.brain.schemas import BudgetCounters, WorkingState


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

    def get_slice(
        self,
        session_id: str,
        *,
        purpose: str,
        limits: dict[str, Any],
    ) -> dict[str, Any]:
        del session_id, purpose, limits
        return {}


def _ctx(session_api: _FakeSessionAPI) -> SimpleNamespace:
    state = WorkingState(
        session_id="s-task-plan-conflict",
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


def _outcome(*, plan_tool_used: bool) -> AdaptiveToolLoopOutcome:
    scratchpad = {PLAN_TOOL_USED_SCRATCHPAD_KEY: True} if plan_tool_used else {}
    return AdaptiveToolLoopOutcome(
        profile_name="default",
        mode_name="act_adaptive",
        termination_reason=ADAPTIVE_TERM_FINAL_TEXT,
        state=AdaptiveToolLoopState(scratchpad=scratchpad),
        allowed_tools=frozenset(),
        final_text="done",
        task_plan={
            "plan_id": "legacy-trailer-plan",
            "objective": "Legacy trailer should lose",
            "steps": [
                {
                    "step_id": "entry",
                    "description": "Legacy step",
                    "depends_on": [],
                    "tool_families": ["web"],
                }
            ],
        },
    )


def test_task_plan_trailer_is_not_staged_after_structured_plan_tool_used() -> None:
    session_api = _FakeSessionAPI()

    _stage_task_plan_events(_ctx(session_api), _outcome(plan_tool_used=True))

    assert session_api.events == []


def test_task_plan_trailer_still_stages_when_structured_plan_tool_was_not_used() -> (
    None
):
    session_api = _FakeSessionAPI()

    _stage_task_plan_events(_ctx(session_api), _outcome(plan_tool_used=False))

    assert [event["event_type"] for event in session_api.events] == [
        "task_plan.declared"
    ]


def test_task_plan_trailer_is_not_staged_after_structured_plan_tool_attempted() -> None:
    session_api = _FakeSessionAPI()
    outcome = _outcome(plan_tool_used=False)
    outcome.state.scratchpad[PLAN_TOOL_ATTEMPTED_SCRATCHPAD_KEY] = True

    _stage_task_plan_events(_ctx(session_api), outcome)

    assert session_api.events == []
