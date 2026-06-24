from __future__ import annotations

import inspect
from typing import Any, Callable, Dict, Sequence

import pytest
from pydantic import ValidationError

from openminion.services.gateway.turn_intent import (
    BenchmarkHarnessTurnIntent,
    FreeformChatTurnIntent,
    MissionRunnerTurnIntent,
    ScriptedCliTurnIntent,
    TuiTaskTurnIntent,
    resolve_typed_goal,
)

FORBIDDEN_FIELDS: Sequence[str] = (
    "verdict",
    "reasoning",
    "narrative",
    "judgment",
    "description_text",
    "completion_summary",
    "summary_text",
    "notes",
)


def _mission_runner_kwargs() -> Dict[str, Any]:
    return {
        "kind": "mission_runner",
        "goal_id": "goal-1",
        "mission_id": "mission-1",
        "description": "Run mission",
        "mission_type": "coding",
        "success_criteria": (
            {
                "criterion_id": "c1",
                "description": "tests pass",
                "structural_check": "success_criteria.tests_passed=true",
            },
        ),
        "deliverables": (
            {
                "deliverable_id": "d1",
                "description": "patch",
                "verification_hint": "artifact_presence",
            },
        ),
    }


def _benchmark_kwargs() -> Dict[str, Any]:
    return {
        "kind": "benchmark_harness",
        "goal_id": "goal-1",
        "corpus_task_id": "ameb-coding-01",
        "description": "Run benchmark task",
        "mission_type": "coding",
        "success_criteria": (
            {
                "criterion_id": "c1",
                "description": "tests pass",
                "structural_check": "success_criteria.tests_passed=true",
            },
        ),
        "deliverables": (
            {
                "deliverable_id": "d1",
                "description": "patch",
                "verification_hint": "artifact_presence",
            },
        ),
    }


def _scripted_cli_kwargs() -> Dict[str, Any]:
    return {
        "kind": "scripted_cli",
        "goal_id": "goal-1",
        "command_name": "openminion mission run",
        "description": "CLI task",
        "mission_type": "research",
        "success_criteria": (
            {
                "criterion_id": "c1",
                "description": "coverage",
                "structural_check": "success_criteria.source_count_ge_2=true",
            },
        ),
        "deliverables": (
            {
                "deliverable_id": "d1",
                "description": "findings",
                "verification_hint": "artifact_presence",
            },
        ),
    }


def _tui_task_kwargs() -> Dict[str, Any]:
    return {
        "kind": "tui_task",
        "goal_id": "goal-1",
        "task_id": "task-1",
        "description": "TUI task",
        "mission_type": "operations",
        "success_criteria": (
            {
                "criterion_id": "c1",
                "description": "healthy",
                "structural_check": "success_criteria.state_recovered=true",
            },
        ),
        "deliverables": (
            {
                "deliverable_id": "d1",
                "description": "status artifact",
                "verification_hint": "artifact_presence",
            },
        ),
    }


def _freeform_chat_kwargs() -> Dict[str, Any]:
    return {"kind": "freeform_chat"}


_RECORDS: Sequence[tuple[str, Callable[..., Any], Callable[[], Dict[str, Any]]]] = (
    ("MissionRunnerTurnIntent", MissionRunnerTurnIntent, _mission_runner_kwargs),
    ("BenchmarkHarnessTurnIntent", BenchmarkHarnessTurnIntent, _benchmark_kwargs),
    ("ScriptedCliTurnIntent", ScriptedCliTurnIntent, _scripted_cli_kwargs),
    ("TuiTaskTurnIntent", TuiTaskTurnIntent, _tui_task_kwargs),
    ("FreeformChatTurnIntent", FreeformChatTurnIntent, _freeform_chat_kwargs),
)


@pytest.mark.parametrize("record_name,ctor,kwargs_fn", _RECORDS)
@pytest.mark.parametrize("forbidden_field", FORBIDDEN_FIELDS)
def test_typed_turn_intent_records_reject_forbidden_prose_field(
    record_name: str,
    ctor: Callable[..., Any],
    kwargs_fn: Callable[[], Dict[str, Any]],
    forbidden_field: str,
) -> None:
    kwargs = kwargs_fn()
    kwargs[forbidden_field] = "any-prose-shaped-value"
    with pytest.raises(ValidationError):
        ctor(**kwargs)


def test_forbidden_field_list_is_non_empty_and_unique() -> None:
    assert len(FORBIDDEN_FIELDS) > 0
    assert len(set(FORBIDDEN_FIELDS)) == len(FORBIDDEN_FIELDS)


def test_forbidden_field_roster_matches_seven_lane_precedent() -> None:
    from tests.brain.test_aatr_forbidden_fields import (
        FORBIDDEN_FIELDS as AATR_FORBIDDEN_FIELDS,
    )
    from tests.brain.test_apbr_forbidden_fields import (
        FORBIDDEN_FIELDS as APBR_FORBIDDEN_FIELDS,
    )
    from tests.brain.test_asrr_forbidden_fields import (
        FORBIDDEN_FIELDS as ASRR_FORBIDDEN_FIELDS,
    )
    from tests.brain.test_mtrr_forbidden_fields import (
        FORBIDDEN_FIELDS as MTRR_FORBIDDEN_FIELDS,
    )
    from tests.brain.test_promotion_forbidden_fields import (
        FORBIDDEN_FIELDS as PROMOTION_FORBIDDEN_FIELDS,
    )
    from tests.brain.test_tgcr_forbidden_fields import (
        FORBIDDEN_FIELDS as TGCR_FORBIDDEN_FIELDS,
    )
    from tests.services.runtime.test_alvb_forbidden_fields import (
        FORBIDDEN_FIELDS as ALVB_FORBIDDEN_FIELDS,
    )

    assert tuple(FORBIDDEN_FIELDS) == tuple(TGCR_FORBIDDEN_FIELDS)
    assert tuple(FORBIDDEN_FIELDS) == tuple(APBR_FORBIDDEN_FIELDS)
    assert tuple(FORBIDDEN_FIELDS) == tuple(MTRR_FORBIDDEN_FIELDS)
    assert tuple(FORBIDDEN_FIELDS) == tuple(ASRR_FORBIDDEN_FIELDS)
    assert tuple(FORBIDDEN_FIELDS) == tuple(AATR_FORBIDDEN_FIELDS)
    assert tuple(FORBIDDEN_FIELDS) == tuple(PROMOTION_FORBIDDEN_FIELDS)
    assert tuple(FORBIDDEN_FIELDS) == tuple(ALVB_FORBIDDEN_FIELDS)


def test_resolve_typed_goal_accepts_only_typed_parameter_name() -> None:
    param_names = list(inspect.signature(resolve_typed_goal).parameters.keys())
    assert param_names == ["turn_intent"]
    for name in FORBIDDEN_FIELDS:
        assert name not in param_names
