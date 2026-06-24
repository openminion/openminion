from __future__ import annotations

from types import SimpleNamespace

from openminion.modules.brain.execution.memory import (
    write_post_completion_critique_memory,
)
from openminion.modules.brain.schemas import (
    BudgetCounters,
    ClosureJudgment,
    IntentExecutionState,
    PostCompletionCritique,
    WorkingState,
)


class _MemoryAPI:
    def __init__(self) -> None:
        self.records: list[dict[str, object]] = []

    def put_record(self, **kwargs):
        self.records.append(dict(kwargs))
        return f"critique-{len(self.records)}"


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
