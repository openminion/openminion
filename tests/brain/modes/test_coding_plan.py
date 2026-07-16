from __future__ import annotations

import pytest

from openminion.modules.brain.loop.strategies.coding.plan import (
    CodingPhase,
    CodingPlan,
    CodingSubtask,
    coding_plan_from_payload,
)


def test_coding_plan_accepts_ordered_phase_prefix() -> None:
    plan = CodingPlan(
        goal="Refactor auth",
        phases=[
            CodingPhase(name="explore", status="active", steps=["inspect files"]),
            CodingPhase(name="plan"),
            CodingPhase(name="implement"),
            CodingPhase(name="verify"),
        ],
        current_phase="explore",
        scratchpad=["watch auth seams"],
    )

    assert plan.goal == "Refactor auth"
    assert [phase.name for phase in plan.phases] == [
        "explore",
        "plan",
        "implement",
        "verify",
    ]
    assert plan.current_phase_entry().name == "explore"
    assert plan.requires_file_change is False


def test_coding_plan_is_read_only_compatible_by_default() -> None:
    plan = CodingPlan(
        goal="Explain the CLI",
        phases=[CodingPhase(name="implement", status="active")],
        current_phase="implement",
    )

    assert plan.requires_file_change is False


def test_coding_fallback_is_read_only_compatible_by_default() -> None:
    plan = CodingPlan.fallback("Explain the CLI")

    assert plan.requires_file_change is False


def test_coding_plan_does_not_infer_contracts_from_goal_text() -> None:
    plan = CodingPlan(
        goal="Build a tiny CLI project under this folder.",
        phases=[CodingPhase(name="implement", status="active")],
        current_phase="implement",
    )

    assert plan.requires_file_change is False


def test_coding_payload_requires_explicit_file_change_contract() -> None:
    plan = coding_plan_from_payload(
        {
            "goal": "Inspect first.",
            "phases": [{"name": "implement", "status": "active"}],
            "current_phase": "implement",
        },
        goal="Create a tiny Python module and test.",
    )

    assert plan.requires_file_change is False


def test_coding_payload_keeps_read_only_goal_read_only() -> None:
    plan = coding_plan_from_payload(
        {
            "goal": "Explain this package.",
            "phases": [{"name": "implement", "status": "active"}],
            "current_phase": "implement",
            "requires_file_change": False,
        },
        goal="Explain this package.",
    )

    assert plan.requires_file_change is False


def test_coding_fallback_can_require_file_change() -> None:
    plan = CodingPlan.fallback("Build a CLI", requires_file_change=True)

    assert plan.requires_file_change is True


def test_coding_plan_rejects_invalid_phase_order() -> None:
    with pytest.raises(ValueError, match="ordered contiguous span"):
        CodingPlan(
            goal="Refactor auth",
            phases=[
                CodingPhase(name="explore"),
                CodingPhase(name="implement"),
            ],
            current_phase="explore",
        )


def test_coding_phase_rejects_invalid_name() -> None:
    with pytest.raises(ValueError):
        CodingPhase.model_validate(
            {"name": "review", "status": "pending", "steps": [], "output": ""}
        )


def test_coding_plan_rejects_missing_goal() -> None:
    with pytest.raises(ValueError):
        CodingPlan.model_validate(
            {
                "phases": [{"name": "implement", "status": "active"}],
                "current_phase": "implement",
            }
        )


def test_coding_plan_advances_one_phase_at_a_time() -> None:
    plan = CodingPlan(
        goal="Refactor auth",
        phases=[
            CodingPhase(name="explore", status="active"),
            CodingPhase(name="plan"),
            CodingPhase(name="implement"),
        ],
        current_phase="explore",
    )

    assert plan.advance_to_next_phase(output="files inspected") is True
    assert plan.current_phase == "plan"
    assert plan.phases[0].status == "done"
    assert plan.phases[0].output == "files inspected"
    assert plan.phases[1].status == "active"


def test_coding_plan_conflicting_subtask_pairs() -> None:
    plan = CodingPlan(
        goal="Split files",
        phases=[CodingPhase(name="implement", status="active")],
        current_phase="implement",
        subtasks=[
            CodingSubtask(goal="Edit A", target_files=["src/a.py"]),
            CodingSubtask(goal="Edit B", target_files=["src/b.py"]),
            CodingSubtask(goal="Edit A helpers", target_files=["src/a.py"]),
        ],
    )

    assert plan.conflicting_subtask_pairs() == [(0, 2)]


def test_coding_plan_from_payload_falls_back_on_invalid_payload() -> None:
    plan = coding_plan_from_payload({"goal": ""}, goal="Do work")

    assert plan.goal == "Do work"
    assert plan.current_phase == "implement"
    assert [phase.name for phase in plan.phases] == ["implement"]
    assert plan.verifier_goal is None
