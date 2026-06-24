from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from openminion.modules.brain.execution import entry as entry_module
from openminion.modules.brain.execution.entry import build_execution_entry_request
from openminion.modules.brain.execution.loop_contracts import ExecutionResult
from openminion.modules.brain.execution.memory import write_decision_memory
from openminion.modules.brain.schemas import BudgetCounters, WorkingState
from openminion.modules.brain.schemas.decisions import (
    ActDecision,
    ExecutionTargetPayload,
    FinalizationStatus,
    RespondDecision,
)


class _MemoryAPI:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def write_record(self, **kwargs: Any) -> str:
        self.records.append(dict(kwargs))
        return f"mem_decision_{len(self.records)}"


class _PutOnlyMemoryAPI:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def put_record(self, **kwargs: Any) -> str:
        self.records.append(dict(kwargs))
        return f"mem_put_decision_{len(self.records)}"


class _FailingMemoryAPI:
    def __init__(self) -> None:
        self.write_attempts = 0

    def write_record(self, **kwargs: Any) -> str:
        del kwargs
        self.write_attempts += 1
        raise RuntimeError("memory unavailable")


class _Logger:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(
        self,
        event: str,
        payload: dict[str, Any],
        *,
        trace_id: str | None = None,
        status: str = "info",
        **_: Any,
    ) -> None:
        self.events.append(
            {"event": event, "payload": payload, "trace_id": trace_id, "status": status}
        )


def _state(**overrides: Any) -> SimpleNamespace:
    payload = {
        "session_id": "s-drm-01",
        "trace_id": "trace-drm-01",
        "turn_id": "turn-7",
        "turn_index": 7,
        "goal": "budget for japan trip should not become user_intent",
        "last_user_input": "budget for japan trip should not become user_intent",
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


def _working_state(session_id: str = "s-drm-02") -> WorkingState:
    return WorkingState(
        session_id=session_id,
        agent_id="agent-drm",
        trace_id=f"trace-{session_id}",
        budgets_remaining=BudgetCounters(
            ticks=5,
            tool_calls=4,
            a2a_calls=2,
            tokens=4000,
            time_ms=60_000,
        ),
    )


def test_write_decision_memory_transports_typed_decision_fields_only() -> None:
    memory = _MemoryAPI()
    logger = _Logger()
    runner = SimpleNamespace(memory_api=memory)
    decision = ActDecision(
        confidence=0.82,
        reason_code="delegate_requested_exact_target",
        sub_intents=["trip_budget", "delegate_planning"],
        rationale="The model chose an act route because structured execution is needed.",
        act_profile="coding",
        execution_target=ExecutionTargetPayload(
            kind="delegated",
            target_agent_id="planner-safe",
            expect_async=True,
        ),
        finalization_status=FinalizationStatus(status="incomplete"),
    )

    result = write_decision_memory(
        runner,
        state=_state(),
        decision=decision,
        logger=logger,
    )

    assert result == ["mem_decision_1"]
    assert len(memory.records) == 1
    record = memory.records[0]
    assert record["scope"] == "session:s-drm-01"
    assert record["record_type"] == "decision"
    assert record["title"] == "Decision: act"
    assert record["tags"] == ["decision", "route:act"]
    content = record["content"]
    assert content["route_chosen"] == "act"
    assert content["reason_code"] == "delegate_requested_exact_target"
    assert content["sub_intents"] == ["trip_budget", "delegate_planning"]
    assert content["rationale"] == (
        "The model chose an act route because structured execution is needed."
    )
    assert content["act_profile"] == "coding"
    assert content["execution_target_kind"] == "delegated"
    assert content["target_agent_id"] == "planner-safe"
    assert content["expect_async"] is True
    assert content["finalization_status"] == "incomplete"
    assert content["session_id"] == "s-drm-01"
    assert content["turn_id"] == "turn-7"
    assert content["turn_index"] == 7
    assert "user_intent" not in content
    assert "budget for japan trip" not in str(content)
    assert logger.events[-1]["event"] == "brain.decision_memory.completed"


def test_write_decision_memory_supports_brain_put_record_adapter() -> None:
    memory = _PutOnlyMemoryAPI()
    runner = SimpleNamespace(memory_api=memory)
    decision = RespondDecision(
        respond_kind="answer",
        reason_code="entry_text_response",
        rationale="model-authored rationale",
        answer="done",
    )

    result = write_decision_memory(runner, state=_state(), decision=decision)

    assert result == ["mem_put_decision_1"]
    assert memory.records[0]["record_type"] == "decision"
    assert "confidence" not in memory.records[0]


def test_write_decision_memory_writes_empty_rationale_without_filtering() -> None:
    memory = _MemoryAPI()
    runner = SimpleNamespace(memory_api=memory)
    decision = RespondDecision(
        respond_kind="answer",
        confidence=0.4,
        reason_code="entry_text_response",
        sub_intents=[],
        rationale="",
        answer="plain answer",
    )

    result = write_decision_memory(runner, state=_state(), decision=decision)

    assert result == ["mem_decision_1"]
    content = memory.records[0]["content"]
    assert content["route_chosen"] == "respond"
    assert content["respond_kind"] == "answer"
    assert content["rationale"] == ""
    assert "important" not in content
    assert "memorable" not in content


def test_write_decision_memory_bounds_rationale_without_semantic_rewrite() -> None:
    memory = _MemoryAPI()
    runner = SimpleNamespace(memory_api=memory)
    long_rationale = "verified-by-model " * 40
    decision = RespondDecision(
        respond_kind="answer",
        reason_code="long_rationale",
        rationale=long_rationale,
        answer="done",
    )

    write_decision_memory(runner, state=_state(), decision=decision)

    stored = memory.records[0]["content"]["rationale"]
    assert len(stored) <= 280
    assert stored == long_rationale.strip()[:280].rstrip()
    assert "..." not in stored


def test_write_decision_memory_skips_without_memory_api() -> None:
    logger = _Logger()
    decision = RespondDecision(
        respond_kind="answer",
        reason_code="entry_text_response",
        rationale="model-authored rationale",
        answer="done",
    )

    result = write_decision_memory(
        SimpleNamespace(memory_api=None),
        state=_state(),
        decision=decision,
        logger=logger,
    )

    assert result == []
    assert logger.events[-1]["event"] == "brain.decision_memory.skipped"
    assert logger.events[-1]["payload"]["reason"] == "memory_api_unavailable"


def test_dispatch_writes_decision_memory_once_after_finalized_decision(
    monkeypatch,
) -> None:
    memory = _MemoryAPI()
    logger = _Logger()
    decision = RespondDecision(
        respond_kind="answer",
        confidence=0.91,
        reason_code="entry_text_response",
        rationale="The model selected direct response.",
        answer="hello",
    )
    runner = SimpleNamespace(
        memory_api=memory,
        profile=None,
        options=SimpleNamespace(),
        _decide=lambda **_: decision,
        _evaluate_meta=lambda **_: None,
    )
    state = _working_state()

    monkeypatch.setattr(entry_module, "prepare_decision_direct", lambda *_, **__: None)
    monkeypatch.setattr(entry_module, "validate_decision_direct", lambda *_, **__: None)
    monkeypatch.setattr(
        entry_module,
        "invoke_decision_direct",
        lambda *_, **__: ExecutionResult(
            status="done",
            working_state=state,
            message="ok",
        ),
    )

    output = entry_module.dispatch(
        runner=runner,
        state=state,
        logger=logger,
        request=build_execution_entry_request(
            user_input="hello",
            forced_tools=None,
            capability_category=None,
        ),
    )

    assert output.status == "done"
    assert output.message == "ok"
    assert len(memory.records) == 1
    assert state.decision_memory_refs == ["mem_decision_1"]
    assert memory.records[0]["content"]["reason_code"] == "entry_text_response"
    assert memory.records[0]["content"]["rationale"] == (
        "The model selected direct response."
    )


def test_dispatch_decision_memory_failure_does_not_change_route(
    monkeypatch,
) -> None:
    memory = _FailingMemoryAPI()
    logger = _Logger()
    decision = RespondDecision(
        respond_kind="answer",
        reason_code="entry_text_response",
        rationale="The model selected direct response.",
        answer="hello",
    )
    runner = SimpleNamespace(
        memory_api=memory,
        profile=None,
        options=SimpleNamespace(),
        _decide=lambda **_: decision,
        _evaluate_meta=lambda **_: None,
    )
    state = _working_state("s-drm-02-fail")

    monkeypatch.setattr(entry_module, "prepare_decision_direct", lambda *_, **__: None)
    monkeypatch.setattr(entry_module, "validate_decision_direct", lambda *_, **__: None)
    monkeypatch.setattr(
        entry_module,
        "invoke_decision_direct",
        lambda *_, **__: ExecutionResult(
            status="done",
            working_state=state,
            message="ok",
        ),
    )

    output = entry_module.dispatch(
        runner=runner,
        state=state,
        logger=logger,
        request=build_execution_entry_request(
            user_input="hello",
            forced_tools=None,
            capability_category=None,
        ),
    )

    assert output.status == "done"
    assert output.message == "ok"
    assert memory.write_attempts == 1
    assert state.decision_memory_refs == []
    assert logger.events[-1]["event"] == "brain.decision_memory.skipped"
    assert logger.events[-1]["payload"]["reason"] == "write_failed"
