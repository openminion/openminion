from __future__ import annotations

import pytest
from pydantic import ValidationError

from openminion.modules.brain.schemas import (
    Deliverable,
    FailureCondition,
    Goal,
    GoalStatus,
    SuccessCriterion,
    validate_goal_status_transition,
)


def _criterion(criterion_id: str = "c1") -> SuccessCriterion:
    return SuccessCriterion(
        criterion_id=criterion_id,
        description="placeholder",
        structural_check="artifact_present",
    )


def _deliverable(deliverable_id: str = "d1") -> Deliverable:
    return Deliverable(
        deliverable_id=deliverable_id,
        description="placeholder",
    )


class TestGoalConstruction:
    def test_minimal_goal_constructs(self) -> None:
        goal = Goal(
            goal_id="g1",
            description="ship the typed contract",
            success_criteria=[_criterion()],
            deliverables=[_deliverable()],
        )
        assert goal.goal_id == "g1"
        assert goal.status == GoalStatus.ACTIVE
        assert len(goal.success_criteria) == 1
        assert len(goal.deliverables) == 1
        assert goal.failure_conditions == []
        assert goal.apd_plan_id is None
        assert goal.parent_goal_id is None

    def test_goal_requires_success_criteria(self) -> None:
        with pytest.raises(ValidationError):
            Goal(
                goal_id="g1",
                description="x",
                success_criteria=[],
                deliverables=[_deliverable()],
            )

    def test_goal_requires_deliverables(self) -> None:
        with pytest.raises(ValidationError):
            Goal(
                goal_id="g1",
                description="x",
                success_criteria=[_criterion()],
                deliverables=[],
            )

    def test_goal_id_required(self) -> None:
        with pytest.raises(ValidationError):
            Goal(
                goal_id="",
                description="x",
                success_criteria=[_criterion()],
                deliverables=[_deliverable()],
            )

    def test_apd_plan_id_reference_stored(self) -> None:
        goal = Goal(
            goal_id="g1",
            description="x",
            success_criteria=[_criterion()],
            deliverables=[_deliverable()],
            apd_plan_id="plan-7",
        )
        assert goal.apd_plan_id == "plan-7"

    def test_apd_plan_id_blank_becomes_none(self) -> None:
        goal = Goal(
            goal_id="g1",
            description="x",
            success_criteria=[_criterion()],
            deliverables=[_deliverable()],
            apd_plan_id="   ",
        )
        assert goal.apd_plan_id is None


class TestNonConflation:
    def test_success_criterion_ids_must_be_unique(self) -> None:
        with pytest.raises(ValidationError) as exc:
            Goal(
                goal_id="g1",
                description="x",
                success_criteria=[_criterion("c1"), _criterion("c1")],
                deliverables=[_deliverable()],
            )
        assert "unique criterion_id" in str(exc.value)

    def test_deliverable_ids_must_be_unique(self) -> None:
        with pytest.raises(ValidationError) as exc:
            Goal(
                goal_id="g1",
                description="x",
                success_criteria=[_criterion()],
                deliverables=[_deliverable("d1"), _deliverable("d1")],
            )
        assert "unique deliverable_id" in str(exc.value)

    def test_failure_condition_ids_must_be_unique(self) -> None:
        condition = FailureCondition(
            condition_id="f1",
            kind="deliverable_missing",
            description="d missing",
        )
        with pytest.raises(ValidationError) as exc:
            Goal(
                goal_id="g1",
                description="x",
                success_criteria=[_criterion()],
                deliverables=[_deliverable()],
                failure_conditions=[condition, condition],
            )
        assert "unique condition_id" in str(exc.value)


class TestDeliverableVerificationHint:
    def test_default_hint_is_artifact_presence(self) -> None:
        d = _deliverable()
        assert d.verification_hint == "artifact_presence"

    def test_accepts_typed_families(self) -> None:
        for family in (
            "structural",
            "freshness",
            "artifact_presence",
            "success_criteria_match",
        ):
            d = Deliverable(
                deliverable_id="d1",
                description="x",
                verification_hint=family,
            )
            assert d.verification_hint == family

    def test_rejects_unknown_family(self) -> None:
        with pytest.raises(ValidationError):
            Deliverable(
                deliverable_id="d1",
                description="x",
                verification_hint="model_judge",  # type: ignore[arg-type]
            )


class TestFailureConditionKinds:
    @pytest.mark.parametrize(
        "kind",
        [
            "deliverable_missing",
            "success_criterion_unmet",
            "budget_exhausted",
            "blocker_unresolved",
            "capability_boundary",
            "operator_cancelled",
        ],
    )
    def test_accepts_known_kinds(self, kind: str) -> None:
        c = FailureCondition(condition_id="f1", kind=kind, description="x")  # type: ignore[arg-type]
        assert c.kind == kind

    def test_rejects_unknown_kind(self) -> None:
        with pytest.raises(ValidationError):
            FailureCondition(
                condition_id="f1",
                kind="model_judged_failure",  # type: ignore[arg-type]
                description="x",
            )


class TestGoalStatusTransitions:
    def test_allows_additive_default_status(self) -> None:
        goal = Goal(
            goal_id="g-status",
            description="status baseline",
            success_criteria=[_criterion()],
            deliverables=[_deliverable()],
        )
        assert goal.status == GoalStatus.ACTIVE

    def test_accepts_legal_status_transition(self) -> None:
        normalized = validate_goal_status_transition(
            GoalStatus.ACTIVE,
            GoalStatus.PAUSED,
        )
        assert normalized == GoalStatus.PAUSED

    def test_rejects_illegal_status_transition(self) -> None:
        with pytest.raises(ValueError, match="Illegal Goal.status transition"):
            validate_goal_status_transition(
                GoalStatus.COMPLETED,
                GoalStatus.ACTIVE,
            )
