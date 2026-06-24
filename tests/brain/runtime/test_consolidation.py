from __future__ import annotations

from openminion.modules.brain.runtime.consolidation import (
    ConsolidatedKnowledgeRecord,
    ConsolidationDecision,
    KnowledgeConsolidationCandidate,
    apply_consolidation,
    decide_consolidation,
    project_source_records_to_candidates,
)


def test_project_candidates_from_strategy_outcome_records() -> None:
    records = {
        "strategy_outcome": [
            {
                "record_id": "mem_1",
                "scope": "agent:alpha",
                "content": {"strategy_id": "research"},
            },
            {
                "record_id": "mem_2",
                "scope": "agent:alpha",
                "content": {"strategy_id": "research"},
            },
        ]
    }
    candidates = project_source_records_to_candidates(
        records,
        criteria=["same_strategy_id_high_frequency"],
    )
    assert candidates == [
        KnowledgeConsolidationCandidate(
            criterion_id="same_strategy_id_high_frequency",
            source_record_refs=["mem_1", "mem_2"],
            evidence_window={
                "family": "strategy_outcome",
                "count": 2,
                "target_scope": "agent:alpha",
            },
            proposed_consolidated_kind="strategy_knowledge",
            proposed_signature="strategy::research",
        )
    ]


def test_project_candidates_from_recurring_improvement_note_records() -> None:
    candidates = project_source_records_to_candidates(
        {
            "improvement_note": [
                {
                    "record_id": "mem_note_1",
                    "scope": "agent:alpha",
                    "signature": "search_rate_limit",
                    "occurrence_count": 3,
                    "tags": ["tool:search", "error:rate_limit"],
                }
            ]
        },
        criteria=["same_tool_failure_pattern_recurring"],
    )
    assert candidates[0].criterion_id == "same_tool_failure_pattern_recurring"
    assert candidates[0].source_record_refs == ["mem_note_1"]
    assert candidates[0].proposed_signature == "tool:search::search_rate_limit"


def test_project_candidates_from_goal_subtree_records() -> None:
    candidates = project_source_records_to_candidates(
        {
            "declared_goal": [
                {
                    "record_id": "mem_goal_1",
                    "scope": "agent:alpha",
                    "content": {"parent_goal_id": "goal-parent"},
                },
                {
                    "record_id": "mem_goal_2",
                    "scope": "agent:alpha",
                    "content": {"parent_goal_id": "goal-parent"},
                },
            ]
        },
        criteria=["same_goal_subtree_completed"],
    )
    assert candidates[0].criterion_id == "same_goal_subtree_completed"
    assert candidates[0].source_record_refs == ["mem_goal_1", "mem_goal_2"]


def test_decide_consolidation_emits_typed_decision() -> None:
    candidate = KnowledgeConsolidationCandidate(
        criterion_id="same_strategy_id_high_frequency",
        source_record_refs=["mem_1", "mem_2"],
        evidence_window={"target_scope": "agent:alpha"},
        proposed_consolidated_kind="strategy_knowledge",
        proposed_signature="strategy::research",
    )
    decision = decide_consolidation(
        candidate,
        policy_id="kcon_policy_v1",
    )
    assert isinstance(decision, ConsolidationDecision)
    assert decision.status == "accepted"
    assert decision.policy_id == "kcon_policy_v1"
    assert decision.candidate_ref.startswith("kcon::")


def test_apply_consolidation_persists_record_and_supersedes_sources() -> None:
    class MemoryAPI:
        def __init__(self) -> None:
            self.writes: list[dict[str, object]] = []
            self.supersedes: list[tuple[str, str, str]] = []

        def write_record(self, **kwargs):  # type: ignore[no-untyped-def]
            self.writes.append(kwargs)
            return "mem_consolidated_1"

        def supersede_by_contradiction(
            self, old_record_id: str, new_record_id: str, reason: str = ""
        ) -> None:
            self.supersedes.append((old_record_id, new_record_id, reason))

    memory_api = MemoryAPI()
    decision = decide_consolidation(
        KnowledgeConsolidationCandidate(
            criterion_id="same_strategy_id_high_frequency",
            source_record_refs=["mem_1", "mem_2"],
            evidence_window={"target_scope": "agent:alpha", "count": 2},
            proposed_consolidated_kind="strategy_knowledge",
            proposed_signature="strategy::research",
        ),
        policy_id="kcon_policy_v1",
    )
    record = apply_consolidation(decision, memory_api=memory_api)
    assert isinstance(record, ConsolidatedKnowledgeRecord)
    assert record.record_id == "mem_consolidated_1"
    assert record.record_type == "consolidated_knowledge"
    assert record.source_record_lineage == ["mem_1", "mem_2"]
    assert memory_api.writes[0]["record_type"] == "consolidated_knowledge"
    assert memory_api.supersedes == [
        ("mem_1", "mem_consolidated_1", "same_strategy_id_high_frequency"),
        ("mem_2", "mem_consolidated_1", "same_strategy_id_high_frequency"),
    ]


def test_apply_consolidation_is_inert_for_non_accepted_status() -> None:
    decision = ConsolidationDecision(
        candidate_ref='kcon::{"criterion_id":"same_strategy_id_high_frequency","evidence_window":{},"proposed_consolidated_kind":"strategy_knowledge","proposed_signature":"strategy::research","source_record_refs":["mem_1"]}',
        status="deferred",
        policy_id="kcon_policy_v1",
        decided_at="2026-05-13T00:00:00Z",
    )
    assert apply_consolidation(decision, memory_api=object()) is None


def test_kcon_schema_fields_do_not_drift_into_summary_sludge() -> None:
    schema_fields = (
        set(KnowledgeConsolidationCandidate.model_fields.keys())
        | set(ConsolidatedKnowledgeRecord.model_fields.keys())
        | set(ConsolidationDecision.model_fields.keys())
    )
    forbidden = ("summary", "narrative", "lesson", "blob", "synthesis")
    for field_name in schema_fields:
        for fragment in forbidden:
            assert fragment not in field_name
