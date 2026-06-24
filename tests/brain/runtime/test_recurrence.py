from __future__ import annotations

from types import SimpleNamespace

from openminion.modules.brain.runtime.failures import (
    FailurePatternBucket,
    FailurePatternReadout,
)
from openminion.modules.brain.runtime.performance import (
    PerformanceRegistry,
    PerformanceRegistryEntry,
)
from openminion.modules.brain.runtime.recurrence import (
    RecurringTaskShape,
    TaskShapeRecurrenceWindow,
    project_recurring_task_shapes,
)


def test_projection_emits_shape_for_closed_triple_key() -> None:
    performance = PerformanceRegistry(
        entries=[
            PerformanceRegistryEntry(
                subject_kind="strategy",
                subject_id="research|live_information|latest_news",
                success_count=2,
                failure_count=1,
            )
        ]
    )
    failure = FailurePatternReadout(rows=[])
    shapes = project_recurring_task_shapes(
        performance,
        failure,
        None,
        window=TaskShapeRecurrenceWindow(min_recurrence_threshold=2),
    )
    assert len(shapes) == 1
    shape = shapes[0]
    assert shape.task_shape_ref == "task_shape:research|live_information|latest_news"
    assert shape.strategy_id == "research"
    assert shape.capability_category == "live_information"
    assert shape.intent_category == "latest_news"
    assert shape.recurrence_count == 3
    assert shape.performance_entry_refs == [
        "performance:research|live_information|latest_news"
    ]


def test_projection_threshold_filters_nonrecurring_shapes() -> None:
    performance = PerformanceRegistry(
        entries=[
            PerformanceRegistryEntry(
                subject_kind="strategy",
                subject_id="research|live_information|latest_news",
                success_count=1,
            )
        ]
    )
    shapes = project_recurring_task_shapes(
        performance,
        FailurePatternReadout(rows=[]),
        None,
        window={"min_recurrence_threshold": 2},
    )
    assert shapes == []


def test_projection_skips_non_triple_or_non_strategy_entries() -> None:
    performance = PerformanceRegistry(
        entries=[
            PerformanceRegistryEntry(
                subject_kind="workflow",
                subject_id="workflow.research",
                success_count=5,
            ),
            PerformanceRegistryEntry(
                subject_kind="strategy",
                subject_id="research",
                success_count=5,
            ),
        ]
    )
    shapes = project_recurring_task_shapes(
        performance,
        FailurePatternReadout(rows=[]),
        None,
        window={"min_recurrence_threshold": 1},
    )
    assert shapes == []


def test_projection_is_deterministic() -> None:
    performance = PerformanceRegistry(
        entries=[
            PerformanceRegistryEntry(
                subject_kind="strategy",
                subject_id="research|live_information|latest_news",
                success_count=2,
            )
        ]
    )
    failure = FailurePatternReadout(
        rows=[
            FailurePatternBucket(
                seam_id="strategy_outcome",
                reason_code="strategy_outcome_failure",
                recurrence_count=2,
            )
        ]
    )
    window = TaskShapeRecurrenceWindow(
        time_range="last_30d",
        agent_id="agent-1",
        min_recurrence_threshold=2,
    )
    a = project_recurring_task_shapes(performance, failure, None, window=window)
    b = project_recurring_task_shapes(performance, failure, None, window=window)
    assert [item.model_dump(mode="json") for item in a] == [
        item.model_dump(mode="json") for item in b
    ]


def test_projection_carries_failure_and_optional_knowledge_refs() -> None:
    performance = PerformanceRegistry(
        entries=[
            PerformanceRegistryEntry(
                subject_kind="strategy",
                subject_id="research|live_information|latest_news",
                success_count=2,
            )
        ]
    )
    failure = FailurePatternReadout(
        rows=[
            FailurePatternBucket(
                seam_id="strategy_outcome",
                reason_code="strategy_outcome_failure",
                recurrence_count=2,
            ),
            FailurePatternBucket(
                seam_id="github_policy",
                reason_code="POLICY_DENIED_REPO",
                recurrence_count=2,
            ),
        ]
    )
    knowledge = {"rows": [SimpleNamespace(record_id="k-1")]}
    shapes = project_recurring_task_shapes(
        performance,
        failure,
        knowledge,
        window={"min_recurrence_threshold": 2},
    )
    assert shapes[0].failure_pattern_refs == [
        "failure:github_policy|POLICY_DENIED_REPO",
        "failure:strategy_outcome|strategy_outcome_failure",
    ]
    assert shapes[0].knowledge_record_refs == ["knowledge:k-1"]


def test_projection_operates_when_knowledge_readout_is_none() -> None:
    performance = PerformanceRegistry(
        entries=[
            PerformanceRegistryEntry(
                subject_kind="strategy",
                subject_id="research|live_information|latest_news",
                success_count=2,
            )
        ]
    )
    shapes = project_recurring_task_shapes(
        performance,
        FailurePatternReadout(rows=[]),
        None,
        window={"min_recurrence_threshold": 1},
    )
    assert shapes[0].knowledge_record_refs == []


def test_schemas_do_not_expose_prose_or_similarity_fields() -> None:
    forbidden_substrings = (
        "summary",
        "label",
        "narrative",
        "similarity",
        "guess",
        "proposed",
    )
    schema_fields = set(RecurringTaskShape.model_fields.keys()) | set(
        TaskShapeRecurrenceWindow.model_fields.keys()
    )
    for field_name in schema_fields:
        for forbidden in forbidden_substrings:
            assert forbidden not in field_name
