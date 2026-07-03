from __future__ import annotations

from typing import Any

from openminion.modules.brain.runtime.recurrence import (
    TaskShapeRecurrenceWindow,
)
from openminion.modules.memory.storage.audit import InMemoryMemoryAuditSink
from openminion.modules.skill.config import SkillConfig
from openminion.services.agent.memory.skill_promotion import (
    SKILL_PROMOTION_PASS_EVENT_TYPE,
    SkillPromotionRunResult,
    run_skill_promotion_cadence_once,
)


class _StubMemoryAPI:
    def __init__(
        self,
        *,
        shapes: list[dict[str, Any]] | None = None,
        catalog: list[dict[str, Any]] | None = None,
    ) -> None:
        self._shapes = list(shapes or [])
        self._catalog = list(catalog or [])
        self.recorded_proposals: list[Any] = []
        self.recorded_reviews: list[Any] = []

    def get_recurring_task_shapes(self) -> list[Any]:
        return list(self._shapes)

    def get_current_skill_catalog(self) -> list[Any]:
        return list(self._catalog)

    def record_promotion_proposal(self, proposal: Any) -> None:
        self.recorded_proposals.append(proposal)

    def record_promotion_review(self, review: Any) -> None:
        self.recorded_reviews.append(review)


def _shape(
    *,
    strategy_id: str = "research_strategy",
    capability_category: str = "live_information",
    intent_category: str = "latest_news",
    success_count: int = 5,
    utility_score: float = 0.9,
) -> dict[str, Any]:
    return {
        "task_shape_ref": (
            f"task_shape:{strategy_id}|{capability_category}|{intent_category}"
        ),
        "strategy_id": strategy_id,
        "capability_category": capability_category,
        "intent_category": intent_category,
        "recurrence_count": success_count,
        "success_count": success_count,
        "utility_score": utility_score,
        "performance_entry_refs": [
            f"performance:{strategy_id}|{capability_category}|{intent_category}"
        ],
        "failure_pattern_refs": [],
        "knowledge_record_refs": [],
        "evidence_window": TaskShapeRecurrenceWindow().model_dump(mode="json"),
    }


def test_promotion_runner_no_op_when_cadence_disabled() -> None:
    config = SkillConfig()
    audit_sink = InMemoryMemoryAuditSink()
    memory = _StubMemoryAPI(
        shapes=[_shape(success_count=10, utility_score=0.95)], catalog=[]
    )

    result = run_skill_promotion_cadence_once(
        config=config,
        memory_api=memory,
        audit_sink=audit_sink,
    )

    assert isinstance(result, SkillPromotionRunResult)
    assert result.enabled is False
    assert result.report is None
    assert memory.recorded_proposals == []
    assert memory.recorded_reviews == []
    assert audit_sink.events == []


def test_promotion_runner_emits_audit_event_when_enabled() -> None:
    config = SkillConfig(
        promotion_cadence_enabled=True,
        promotion_cadence_success_threshold=3,
        promotion_cadence_utility_threshold=0.7,
    )
    audit_sink = InMemoryMemoryAuditSink()
    memory = _StubMemoryAPI(
        shapes=[_shape(success_count=10, utility_score=0.9)], catalog=[]
    )

    result = run_skill_promotion_cadence_once(
        config=config,
        memory_api=memory,
        audit_sink=audit_sink,
    )

    assert result.enabled is True
    assert result.dry_run is False
    assert result.report is not None
    assert result.report.candidates_considered == 1
    assert result.report.pending_operator_review == 1
    assert len(memory.recorded_proposals) == 1
    assert memory.recorded_reviews == []
    assert len(audit_sink.events) == 1
    event = audit_sink.events[0]
    assert event.event_type == SKILL_PROMOTION_PASS_EVENT_TYPE
    assert event.target_kind == "batch"
    assert event.details["candidates_considered"] == 1
    assert event.details["proposals_drafted"] == 1
    assert event.details["pending_operator_review"] == 1
    assert event.details["auto_approved_structural_duplicates"] == 0
    assert event.details["dry_run"] is False


def test_promotion_runner_force_enabled_overrides_config_flag() -> None:
    config = SkillConfig()
    memory = _StubMemoryAPI(
        shapes=[_shape(success_count=10, utility_score=0.9)], catalog=[]
    )

    result = run_skill_promotion_cadence_once(
        config=config,
        memory_api=memory,
        audit_sink=None,
        force_enabled=True,
    )

    assert result.enabled is True
    assert result.report is not None
    assert len(memory.recorded_proposals) == 1


def test_promotion_runner_no_audit_sink_does_not_crash() -> None:
    config = SkillConfig(promotion_cadence_enabled=True)
    memory = _StubMemoryAPI(
        shapes=[_shape(success_count=10, utility_score=0.9)], catalog=[]
    )
    result = run_skill_promotion_cadence_once(
        config=config,
        memory_api=memory,
        audit_sink=None,
    )
    assert result.enabled is True
    assert result.report is not None
