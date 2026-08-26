from __future__ import annotations

from types import SimpleNamespace

from openminion.modules.brain.execution.validation import normalize_execution_result
from openminion.modules.brain.runner.coordinator import BrainRunner
from openminion.modules.brain.schemas import ActionResult


class _MemoryAPI:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def apply_outcome_feedback(self, **kwargs: object) -> None:
        self.calls.append(dict(kwargs))


def test_execution_result_accepts_explicit_typed_memory_use() -> None:
    result, job = normalize_execution_result(
        command_id="command-1",
        provider="tool",
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
