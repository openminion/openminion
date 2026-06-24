from __future__ import annotations

from types import SimpleNamespace

import pytest

from openminion.modules.brain.runtime.performance import (
    PerformanceRegistry,
    PerformanceRegistryEntry,
    RankingDecision,
    aggregate_performance_registry,
    project_strategy_outcome_records_to_entries,
    rank_candidates,
)


def _record(
    *,
    strategy_id: str = "research",
    capability_category: str = "live_information",
    intent_category: str = "latest_news",
    outcome_status: str = "success",
    turn_id: str = "trace-1",
    created_at: str = "2026-05-13T10:00:00Z",
) -> SimpleNamespace:
    return SimpleNamespace(
        content={
            "strategy_id": strategy_id,
            "capability_category": capability_category,
            "intent_category": intent_category,
            "outcome_status": outcome_status,
            "turn_id": turn_id,
            "created_at": created_at,
        }
    )


def test_projection_groups_by_strategy_id_axis() -> None:
    records = [
        _record(strategy_id="research", outcome_status="success"),
        _record(strategy_id="research", outcome_status="failure"),
        _record(strategy_id="coding", outcome_status="success"),
    ]
    entries = project_strategy_outcome_records_to_entries(
        records, group_by="strategy_id"
    )
    by_id = {e.subject_id: e for e in entries}
    assert by_id["research"].success_count == 1
    assert by_id["research"].failure_count == 1
    assert by_id["coding"].success_count == 1
    assert all(e.subject_kind == "strategy" for e in entries)


def test_projection_groups_by_composite_axis() -> None:
    records = [
        _record(
            strategy_id="research",
            capability_category="live_information",
            outcome_status="success",
        ),
        _record(
            strategy_id="research",
            capability_category="general",
            outcome_status="success",
        ),
    ]
    entries = project_strategy_outcome_records_to_entries(
        records, group_by="strategy_id_capability"
    )
    subject_ids = sorted(e.subject_id for e in entries)
    assert subject_ids == ["research|general", "research|live_information"]


def test_projection_unknown_axis_raises() -> None:
    with pytest.raises(KeyError):
        project_strategy_outcome_records_to_entries(
            [_record()],
            group_by="totally_not_a_real_axis",  # type: ignore[arg-type]
        )


def test_projection_skips_records_with_missing_subject_fields() -> None:
    records = [
        _record(strategy_id="research"),
        SimpleNamespace(content={"outcome_status": "success"}),  # no strategy_id
        SimpleNamespace(content=None),  # no content
        SimpleNamespace(),  # no content attr
    ]
    entries = project_strategy_outcome_records_to_entries(
        records, group_by="strategy_id"
    )
    assert [e.subject_id for e in entries] == ["research"]


def test_projection_skips_records_with_blank_required_fields() -> None:
    records = [
        _record(strategy_id="research"),
        _record(strategy_id="", outcome_status="success"),  # blank
        _record(strategy_id="   ", outcome_status="success"),  # whitespace
    ]
    entries = project_strategy_outcome_records_to_entries(
        records, group_by="strategy_id"
    )
    assert [e.subject_id for e in entries] == ["research"]


def test_projection_workflow_axis_produces_workflow_subject_kind() -> None:
    records = [_record(strategy_id="research")]
    entries = project_strategy_outcome_records_to_entries(
        records, group_by="workflow_id"
    )
    assert entries == []


def test_arbitrary_outcome_status_falls_to_other_bucket() -> None:
    records = [
        _record(outcome_status="success"),
        _record(outcome_status="failure"),
        _record(outcome_status="weird"),
        _record(outcome_status=""),
        _record(outcome_status="partial-ish"),
    ]
    entries = project_strategy_outcome_records_to_entries(
        records, group_by="strategy_id"
    )
    assert len(entries) == 1
    entry = entries[0]
    assert entry.success_count == 1
    assert entry.failure_count == 1
    assert entry.other_count == 3


def test_outcome_status_normalization_handles_case() -> None:
    records = [
        _record(outcome_status="SUCCESS"),
        _record(outcome_status="Failure"),
    ]
    entries = project_strategy_outcome_records_to_entries(
        records, group_by="strategy_id"
    )
    assert entries[0].success_count == 1
    assert entries[0].failure_count == 1


def test_distinct_traces_dedups_blank_trace_ids() -> None:
    records = [
        _record(turn_id="trace-1"),
        _record(turn_id="trace-1"),
        _record(turn_id="trace-2"),
        _record(turn_id=""),  # blank trace excluded
    ]
    entries = project_strategy_outcome_records_to_entries(
        records, group_by="strategy_id"
    )
    assert entries[0].distinct_traces == 2


def test_earliest_latest_timestamps_sorted() -> None:
    records = [
        _record(created_at="2026-05-13T12:00:00Z"),
        _record(created_at="2026-05-13T10:00:00Z"),
        _record(created_at="2026-05-13T11:00:00Z"),
    ]
    entries = project_strategy_outcome_records_to_entries(
        records, group_by="strategy_id"
    )
    assert entries[0].earliest_observed_at == "2026-05-13T10:00:00Z"
    assert entries[0].latest_observed_at == "2026-05-13T12:00:00Z"


def test_aggregate_preserves_entry_order_and_counts() -> None:
    entries = [
        PerformanceRegistryEntry(
            subject_kind="strategy",
            subject_id="research",
            success_count=3,
            failure_count=1,
        ),
        PerformanceRegistryEntry(
            subject_kind="strategy",
            subject_id="coding",
            success_count=2,
        ),
    ]
    registry = aggregate_performance_registry(entries)
    assert registry.entries == entries
    assert registry.total_records_scanned == 6
    assert registry.registry_version == "1"


def test_aggregate_is_deterministic() -> None:
    entries = [
        PerformanceRegistryEntry(
            subject_kind="strategy", subject_id="research", success_count=5
        ),
    ]
    a = aggregate_performance_registry(entries)
    b = aggregate_performance_registry(entries)
    assert a.model_dump() == b.model_dump()


def test_aggregate_empty_returns_empty_registry() -> None:
    registry = aggregate_performance_registry([])
    assert registry.entries == []
    assert registry.total_records_scanned == 0


def test_projection_sort_descending_by_total_observations() -> None:
    records = [
        _record(strategy_id="rare", outcome_status="success"),
        _record(strategy_id="common", outcome_status="success"),
        _record(strategy_id="common", outcome_status="failure"),
        _record(strategy_id="common", outcome_status="success"),
    ]
    entries = project_strategy_outcome_records_to_entries(
        records, group_by="strategy_id"
    )
    assert [e.subject_id for e in entries] == ["common", "rare"]


def test_rank_candidates_returns_one_decision_per_candidate() -> None:
    registry = aggregate_performance_registry(
        [
            PerformanceRegistryEntry(
                subject_kind="strategy",
                subject_id="research",
                success_count=10,
                failure_count=2,
            ),
            PerformanceRegistryEntry(
                subject_kind="strategy",
                subject_id="coding",
                success_count=3,
                failure_count=1,
            ),
        ]
    )
    decisions = rank_candidates(
        registry,
        candidate_ids=["research", "coding"],
        policy_id="outcome_weighted_v1",
    )
    assert len(decisions) == 2
    assert all(d.policy_id == "outcome_weighted_v1" for d in decisions)
    # Research has more observations → sorted first.
    assert decisions[0].subject_id == "research"


def test_rank_candidates_emits_no_evidence_decision_for_missing() -> None:
    registry = aggregate_performance_registry(
        [
            PerformanceRegistryEntry(
                subject_kind="strategy", subject_id="research", success_count=5
            ),
        ]
    )
    decisions = rank_candidates(
        registry,
        candidate_ids=["research", "uncharted"],
        policy_id="outcome_weighted_v1",
    )
    assert len(decisions) == 2
    by_id = {d.subject_id: d for d in decisions}
    assert by_id["research"].supporting_entry_refs == ["research"]
    assert by_id["uncharted"].supporting_entry_refs == []
    # No-evidence decisions sort to the end.
    assert decisions[-1].subject_id == "uncharted"


def test_rank_candidates_does_not_mutate_registry() -> None:
    entries = [
        PerformanceRegistryEntry(
            subject_kind="strategy", subject_id="research", success_count=5
        ),
    ]
    registry = aggregate_performance_registry(entries)
    before = registry.model_dump()
    rank_candidates(registry, candidate_ids=["research"], policy_id="default_v1")
    assert registry.model_dump() == before


# --- Anti-LLM regression --------------------------------------------------


def test_schemas_do_not_expose_recommendation_or_feels_fields() -> None:
    forbidden_substrings = (
        "feels_",
        "seems_",
        "appears_",
        "recommended",
        "best",
        "narrative",
        "summary",
    )
    schema_fields = (
        set(PerformanceRegistryEntry.model_fields.keys())
        | set(PerformanceRegistry.model_fields.keys())
        | set(RankingDecision.model_fields.keys())
    )
    for field_name in schema_fields:
        for forbidden in forbidden_substrings:
            assert forbidden not in field_name, (
                f"SWPC discipline violation: field {field_name!r} contains "
                f"forbidden substring {forbidden!r}."
            )


def test_no_synthetic_unknown_subject_id_bucket() -> None:
    records = [
        _record(strategy_id="research"),
        _record(strategy_id=""),
        _record(strategy_id=""),
    ]
    entries = project_strategy_outcome_records_to_entries(
        records, group_by="strategy_id"
    )
    subject_ids = {e.subject_id for e in entries}
    assert subject_ids == {"research"}
    assert "unknown" not in subject_ids


def test_evidence_window_is_carried_verbatim() -> None:
    window = {"agent_id": "agent-1", "time_range": "last-7d"}
    records = [_record()]
    entries = project_strategy_outcome_records_to_entries(
        records, group_by="strategy_id", evidence_window=window
    )
    assert entries[0].evidence_window == window
