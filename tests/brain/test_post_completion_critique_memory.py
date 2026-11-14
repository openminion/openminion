from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from openminion.modules.brain.execution.closure import apply_closure_judgment
from openminion.modules.brain.execution.memory import (
    write_post_completion_critique_memory,
)
from openminion.modules.brain.adapters.context.bridges.memory import BridgeMemoryClient
from openminion.modules.brain.schemas import (
    BudgetCounters,
    ClosureJudgment,
    IntentExecutionState,
    MissionState,
    PostCompletionCritique,
    WorkingState,
)
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore


class _MemoryAPI:
    def __init__(self) -> None:
        self.records: list[dict[str, object]] = []
        self.record_ids_by_key: dict[str, str] = {}

    def write_record(self, **kwargs):
        key = str(kwargs.get("key", ""))
        if key in self.record_ids_by_key:
            return self.record_ids_by_key[key]
        record_id = f"critique-{len(self.records) + 1}"
        self.records.append(dict(kwargs))
        self.record_ids_by_key[key] = record_id
        return record_id

    def put_record(self, **kwargs):
        self.records.append(dict(kwargs))
        return f"critique-{len(self.records)}"


class _SessionAPI:
    def append_event(self, *args, **kwargs) -> str:
        return "event-1"


def _runner(memory: _MemoryAPI) -> SimpleNamespace:
    return SimpleNamespace(
        memory_api=memory,
        session_api=_SessionAPI(),
        llm_api=None,
        profile=SimpleNamespace(
            agent_id="agent-pccm",
            structural_tool_results=False,
        ),
        options=SimpleNamespace(max_replans=2),
    )


def _critique_judgment(*, satisfied: bool, next_action: str) -> ClosureJudgment:
    return ClosureJudgment(
        satisfied=satisfied,
        reason="reviewed",
        next_action=next_action,
        post_completion_critique=PostCompletionCritique(
            intent_id="intent-weather",
            summary="Use the verified result next time.",
        ),
    )


class _Logger:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object], str]] = []

    def emit(self, event: str, payload: dict[str, object], **kwargs) -> None:
        self.events.append((event, payload, str(kwargs.get("status", "info"))))


def _state() -> WorkingState:
    return WorkingState(
        session_id="sess-pccm",
        agent_id="agent-pccm",
        trace_id="trace-pccm",
        budgets_remaining=BudgetCounters(
            ticks=5,
            tool_calls=5,
            a2a_calls=1,
            tokens=4000,
            time_ms=60_000,
        ),
        active_mode_name="act",
        decision_sub_intents=["intent-weather"],
        intent_execution_states=[
            IntentExecutionState(
                intent_id="intent-weather",
                description="check weather",
                status="succeeded",
            )
        ],
    )


def test_write_post_completion_critique_memory_persists_verbatim() -> None:
    memory = _MemoryAPI()
    logger = _Logger()
    runner = SimpleNamespace(
        memory_api=memory,
        profile=SimpleNamespace(agent_id="agent-pccm"),
    )
    judgment = ClosureJudgment(
        satisfied=True,
        reason="done",
        next_action="close",
        post_completion_critique=PostCompletionCritique(
            intent_id="intent-weather",
            summary="I should validate the location before searching.",
            lessons=["Check required inputs before making the tool call."],
            next_time_action="Ask for the city if it is missing.",
        ),
    )

    record_ids = write_post_completion_critique_memory(
        runner,
        state=_state(),
        judgment=judgment,
        logger=logger,
    )

    assert record_ids == ["critique-1"]
    stored = memory.records[0]
    assert stored["record_type"] == "post_completion_critique"
    assert stored["key"] == "trace-pccm:intent-weather"
    assert stored["content"]["intent_id"] == "intent-weather"
    assert stored["content"]["summary"] == (
        "I should validate the location before searching."
    )
    assert stored["content"]["lessons"] == [
        "Check required inputs before making the tool call."
    ]
    assert stored["content"]["next_time_action"] == "Ask for the city if it is missing."
    assert logger.events[-1][0] == "brain.post_completion_critique.completed"


def test_write_post_completion_critique_memory_rejects_invalid_intent_link() -> None:
    memory = _MemoryAPI()
    logger = _Logger()
    runner = SimpleNamespace(
        memory_api=memory,
        profile=SimpleNamespace(agent_id="agent-pccm"),
    )
    judgment = ClosureJudgment(
        satisfied=True,
        reason="done",
        next_action="close",
        post_completion_critique=PostCompletionCritique(
            intent_id="intent-other",
            summary="This critique should not be linked.",
            lessons=["Do not guess a linkage."],
        ),
    )

    record_ids = write_post_completion_critique_memory(
        runner,
        state=_state(),
        judgment=judgment,
        logger=logger,
    )

    assert record_ids == []
    assert memory.records == []
    assert logger.events[-1][0] == "brain.post_completion_critique.skipped"
    assert logger.events[-1][1]["reason"] == "critique_link_invalid"


def test_write_post_completion_critique_memory_is_backward_compatible_when_absent() -> (
    None
):
    memory = _MemoryAPI()
    logger = _Logger()
    runner = SimpleNamespace(
        memory_api=memory,
        profile=SimpleNamespace(agent_id="agent-pccm"),
    )
    judgment = ClosureJudgment(
        satisfied=True,
        reason="done",
        next_action="close",
    )

    record_ids = write_post_completion_critique_memory(
        runner,
        state=_state(),
        judgment=judgment,
        logger=logger,
    )

    assert record_ids == []
    assert memory.records == []
    assert logger.events == []


def test_closure_writes_critique_only_after_accepted_transition() -> None:
    memory = _MemoryAPI()
    state = _state()

    disposition = apply_closure_judgment(
        _runner(memory),
        state=state,
        judgment=_critique_judgment(satisfied=True, next_action="close"),
    )

    assert disposition == "close"
    assert state.status == "done"
    assert len(memory.records) == 1


def test_replayed_accepted_close_keeps_one_completion_critique() -> None:
    memory = _MemoryAPI()
    runner = _runner(memory)
    state = _state()
    judgment = _critique_judgment(satisfied=True, next_action="close")

    apply_closure_judgment(runner, state=state, judgment=judgment)
    apply_closure_judgment(runner, state=state, judgment=judgment)

    assert len(memory.records) == 1


def test_completion_critique_survives_restart_and_stays_agent_scoped(tmp_path) -> None:
    memory_path = tmp_path / "memory.db"
    service = MemoryService(
        store=SQLiteMemoryStore(memory_path),
        owns_store=True,
    )
    runner = _runner(service)

    apply_closure_judgment(
        runner,
        state=_state(),
        judgment=_critique_judgment(satisfied=True, next_action="close"),
    )
    service.close()

    restarted = BridgeMemoryClient(SimpleNamespace(_path=tmp_path / "session.db"))
    try:
        matching = restarted.list_cross_session_memory_cards_by_type(
            agent_id="agent-pccm",
            record_types=["post_completion_critique"],
            limit=5,
        )
        foreign = restarted.list_cross_session_memory_cards_by_type(
            agent_id="agent-other",
            record_types=["post_completion_critique"],
            limit=5,
        )
    finally:
        restarted.close()

    assert [card.meta["intent_id"] for card in matching] == ["intent-weather"]
    assert foreign == []


def test_non_close_judgment_does_not_write_completion_critique() -> None:
    memory = _MemoryAPI()

    disposition = apply_closure_judgment(
        _runner(memory),
        state=_state(),
        judgment=_critique_judgment(satisfied=False, next_action="continue"),
    )

    assert disposition == "continue"
    assert memory.records == []


def test_failed_terminal_transition_does_not_write_completion_critique() -> None:
    memory = _MemoryAPI()
    state = _state()
    state.status = "error"

    with pytest.raises(RuntimeError):
        apply_closure_judgment(
            _runner(memory),
            state=state,
            judgment=_critique_judgment(satisfied=True, next_action="close"),
        )

    assert memory.records == []


def test_mission_veto_does_not_write_completion_critique() -> None:
    memory = _MemoryAPI()
    state = _state()
    state.mission = MissionState.model_validate(
        {
            "mission_id": "mission-1",
            "objective": "finish the mission",
            "status": "active",
            "budget": {
                "total_remaining": state.budgets_remaining.model_dump(),
                "per_turn_max": state.budgets_remaining.model_dump(),
            },
            "latest_judgment": {"outcome": "continue", "reason": "not done"},
        }
    )

    with (
        patch(
            "openminion.modules.brain.execution.closure.base.mission_is_active",
            return_value=True,
        ),
        patch(
            "openminion.modules.brain.execution.closure.base._apply_mission_closure_judgment",
            return_value="ask_user",
        ),
    ):
        disposition = apply_closure_judgment(
            _runner(memory),
            state=state,
            judgment=_critique_judgment(satisfied=True, next_action="close"),
        )

    assert disposition == "ask_user"
    assert memory.records == []
