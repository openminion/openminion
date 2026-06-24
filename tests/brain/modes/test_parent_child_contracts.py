from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from openminion.modules.brain.constants import (
    BRAIN_INTERNAL_MODE_ACT_RESEARCH,
    BRAIN_INTERNAL_MODE_EXECUTION_TARGET_DELEGATED,
)
from openminion.modules.brain.execution.child_tasks import (
    ChildResultCollector,
    ChildTaskPromoter,
    ChildTaskResult,
    TaskWaitPolicy,
    SubtaskResult,
    SubtaskSpec,
)
from openminion.modules.brain.execution.orchestrate.strategies import (
    AllInlinePromoter,
    BlockingWait,
    HeuristicPromoter,
    InlineAndPromotedCollector,
)


def test_parent_child_contract_types_are_runtime_checkable() -> None:
    assert isinstance(AllInlinePromoter(), ChildTaskPromoter)
    assert isinstance(BlockingWait(), TaskWaitPolicy)
    assert isinstance(InlineAndPromotedCollector(), ChildResultCollector)


def test_child_task_result_validates_required_fields() -> None:
    result = ChildTaskResult(
        subtask_id="subtask-1",
        task_id="task-1",
        was_promoted=True,
        result=SubtaskResult(
            subtask_id="subtask-1",
            goal="research",
            status="completed",
            mode_used="research",
            output="done",
        ),
    )
    assert result.was_promoted is True
    with pytest.raises(ValidationError):
        ChildTaskResult(
            subtask_id="",
            task_id=None,
            was_promoted=False,
            result=SubtaskResult(
                subtask_id="subtask-2",
                goal="x",
                status="completed",
                mode_used="respond",
            ),
        )


def test_all_inline_promoter_never_promotes() -> None:
    promoter = AllInlinePromoter()
    assert promoter.should_promote(SubtaskSpec(goal="quick response")) is False
    with pytest.raises(NotImplementedError):
        promoter.promote(SubtaskSpec(goal="x"), "parent-1", SimpleNamespace())


def test_heuristic_promoter_uses_mode_and_goal_length() -> None:
    promoter = HeuristicPromoter(goal_length_threshold=20)
    assert (
        promoter.should_promote(
            SubtaskSpec(
                goal="Research topic",
                suggested_mode=BRAIN_INTERNAL_MODE_ACT_RESEARCH,
            )
        )
        is True
    )
    assert (
        promoter.should_promote(
            SubtaskSpec(
                goal="Delegate topic",
                suggested_mode=BRAIN_INTERNAL_MODE_EXECUTION_TARGET_DELEGATED,
            )
        )
        is True
    )
    assert (
        promoter.should_promote(SubtaskSpec(goal="short", suggested_mode="respond"))
        is False
    )
    assert (
        promoter.should_promote(SubtaskSpec(goal="x" * 25, suggested_mode="act"))
        is True
    )


def test_heuristic_promoter_creates_task_via_context_service() -> None:
    calls: list[dict[str, object]] = []

    def _create_task(**kwargs):
        calls.append(dict(kwargs))
        return SimpleNamespace(task_id="task-123")

    ctx = SimpleNamespace(
        state=SimpleNamespace(session_id="session-1", agent_id="agent-1"),
        create_task=_create_task,
    )
    task_id = HeuristicPromoter().promote(
        SubtaskSpec(
            goal="Research topic",
            subtask_id="subtask-1",
            suggested_mode=BRAIN_INTERNAL_MODE_ACT_RESEARCH,
        ),
        "parent-1",
        ctx,
    )

    assert task_id == "task-123"
    assert calls[0]["session_id"] == "session-1"
    assert calls[0]["metadata"]["parent_task_id"] == "parent-1"


def test_inline_and_promoted_collector_normalizes_mixed_results() -> None:
    collector = InlineAndPromotedCollector()
    inline = ChildTaskResult(
        subtask_id="subtask-inline",
        task_id=None,
        was_promoted=False,
        result=SubtaskResult(
            subtask_id="subtask-inline",
            goal="inline",
            status="completed",
            mode_used="respond",
            output="inline done",
        ),
    )
    promoted = ChildTaskResult(
        subtask_id="subtask-promoted",
        task_id="task-2",
        was_promoted=True,
        result=SubtaskResult(
            subtask_id="subtask-promoted",
            goal="promoted",
            status="completed",
            mode_used="research",
            output="promoted done",
        ),
    )

    normalized = collector.collect([inline, promoted])

    assert [item.subtask_id for item in normalized] == [
        "subtask-inline",
        "subtask-promoted",
    ]
    assert normalized[1].mode_used == "research"


def test_blocking_wait_reads_persisted_child_result() -> None:
    waiter = BlockingWait()
    ctx = SimpleNamespace(
        get_task=lambda *, task_id: SimpleNamespace(
            state="done",
            failure_reason=None,
            metadata={
                "subtask_id": "subtask-1",
                "subtask_goal": "Research topic",
                "mode_name": "research",
                "progress": {
                    "child_task_result": {
                        "subtask_id": "subtask-1",
                        "goal": "Research topic",
                        "status": "completed",
                        "mode_used": "research",
                        "output": "finished",
                        "tokens_used": 11,
                    }
                },
            },
        )
    )

    result = waiter.wait_for_child("task-1", ctx, None)

    assert result.was_promoted is True
    assert result.task_id == "task-1"
    assert result.result.output == "finished"
