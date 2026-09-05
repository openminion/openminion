from __future__ import annotations

from types import SimpleNamespace

import pytest

from openminion.modules.brain.execution.runtime.closure.evaluator import (
    _validate_closure_memory_use_refs,
)
from openminion.modules.brain.execution.validation import normalize_execution_result
from openminion.modules.brain.runner.coordinator import BrainRunner
from openminion.modules.brain.schemas import ActionResult, ClosureJudgment


class _MemoryAPI:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def apply_outcome_feedback(self, **kwargs: object) -> None:
        self.calls.append(dict(kwargs))


def test_execution_result_accepts_explicit_typed_memory_use() -> None:
    result, job = normalize_execution_result(
        command_id="command-1",
        provider="tool",
        tool_name="weather.lookup",
        raw={
            "status": "success",
            "memory_refs": ["produced-memory"],
            "memory_use_refs": [
                {
                    "record_id": "used-memory",
                    "use_kind": "used",
                    "producer_kind": "tool",
                    "producer_id": "weather.lookup",
                }
            ],
        },
    )

    assert job is None
    assert result.memory_refs == ["produced-memory"]
    assert result.memory_use_refs[0].record_id == "used-memory"


@pytest.mark.parametrize(
    ("producer_kind", "producer_id"),
    [("model", "weather.lookup"), ("tool", "other.tool")],
)
def test_execution_result_rejects_wrong_memory_use_producer(
    producer_kind: str,
    producer_id: str,
) -> None:
    with pytest.raises(
        ValueError,
        match="memory use attribution producer does not match execution",
    ):
        normalize_execution_result(
            command_id="command-1",
            provider="tool",
            tool_name="weather.lookup",
            raw={
                "status": "success",
                "memory_use_refs": [
                    {
                        "record_id": "used-memory",
                        "use_kind": "used",
                        "producer_kind": producer_kind,
                        "producer_id": producer_id,
                    }
                ],
            },
        )


def test_delegated_execution_memory_use_matches_action_command() -> None:
    result, job = normalize_execution_result(
        command_id="command-2",
        provider="a2actl",
        raw={
            "status": "success",
            "memory_use_refs": [
                {
                    "record_id": "used-memory",
                    "use_kind": "used",
                    "producer_kind": "action",
                    "producer_id": "command-2",
                }
            ],
        },
    )

    assert job is None
    assert result.memory_use_refs[0].producer_id == "command-2"


def test_tool_memory_use_requires_execution_identity() -> None:
    with pytest.raises(
        ValueError,
        match="tool memory use attribution requires tool identity",
    ):
        normalize_execution_result(
            command_id="command-3",
            provider="tool",
            raw={
                "status": "success",
                "memory_use_refs": [
                    {
                        "record_id": "used-memory",
                        "use_kind": "used",
                        "producer_kind": "tool",
                        "producer_id": "weather.lookup",
                    }
                ],
            },
        )


def test_only_typed_use_receives_outcome_feedback() -> None:
    memory_api = _MemoryAPI()
    runner = BrainRunner.__new__(BrainRunner)
    runner.memory_api = memory_api

    runner._apply_typed_memory_outcome(  # noqa: SLF001
        SimpleNamespace(
            action_result=ActionResult(
                command_id="command-1",
                status="failed",
                memory_refs=["exposed-or-produced-only"],
                memory_use_refs=[
                    {
                        "record_id": "used-memory",
                        "use_kind": "cited",
                        "producer_kind": "model",
                        "producer_id": "act-1",
                    },
                    {
                        "record_id": "used-memory",
                        "use_kind": "used",
                        "producer_kind": "action",
                        "producer_id": "command-1",
                    },
                ],
            )
        )
    )

    assert len(memory_api.calls) == 1
    assert memory_api.calls[0]["record_ids"] == ["used-memory"]
    assert memory_api.calls[0]["outcome"] == "failed"
    assert memory_api.calls[0]["feedback_delta"] == -0.1


def test_exposure_without_typed_use_does_not_change_utility() -> None:
    memory_api = _MemoryAPI()
    runner = BrainRunner.__new__(BrainRunner)
    runner.memory_api = memory_api

    runner._apply_typed_memory_outcome(  # noqa: SLF001
        SimpleNamespace(
            action_result=ActionResult(
                command_id="command-2",
                status="success",
                memory_refs=["retrieved-memory"],
            )
        )
    )

    assert memory_api.calls == []


def test_closure_memory_use_receives_success_feedback() -> None:
    memory_api = _MemoryAPI()
    runner = BrainRunner.__new__(BrainRunner)
    runner.memory_api = memory_api
    judgment = ClosureJudgment(
        final_answer="Answer grounded in memory.",
        memory_use_refs=[
            {
                "record_id": "used-memory",
                "use_kind": "used",
                "producer_kind": "model",
                "producer_id": "judge-1",
            }
        ],
    )

    runner._apply_typed_closure_memory_outcome(  # noqa: SLF001
        memory_use_refs=judgment.memory_use_refs,
        command_id="judge-1",
    )

    assert memory_api.calls == [
        {
            "record_ids": ["used-memory"],
            "outcome": "success",
            "command_id": "judge-1",
            "observed_at": memory_api.calls[0]["observed_at"],
            "feedback_delta": 0.1,
        }
    ]


def test_closure_memory_use_must_match_available_context() -> None:
    state = SimpleNamespace(decision_memory_refs=["available-memory"])
    judgment = ClosureJudgment(
        final_answer="Answer grounded in memory.",
        memory_use_refs=[
            {
                "record_id": "other-memory",
                "use_kind": "cited",
                "producer_kind": "model",
                "producer_id": "judge-1",
            }
        ],
    )

    with pytest.raises(
        ValueError,
        match="judge memory use references unavailable memory",
    ):
        _validate_closure_memory_use_refs(
            state=state,
            judgment=judgment,
            llm_call_id="judge-1",
        )


def test_closure_memory_use_must_match_closure_call() -> None:
    state = SimpleNamespace(decision_memory_refs=["available-memory"])
    judgment = ClosureJudgment(
        final_answer="Answer grounded in memory.",
        memory_use_refs=[
            {
                "record_id": "available-memory",
                "use_kind": "used",
                "producer_kind": "model",
                "producer_id": "other-call",
            }
        ],
    )

    with pytest.raises(
        ValueError,
        match="judge memory use producer does not match closure call",
    ):
        _validate_closure_memory_use_refs(
            state=state,
            judgment=judgment,
            llm_call_id="judge-1",
        )
