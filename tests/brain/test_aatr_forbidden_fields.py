from __future__ import annotations

from typing import Any, Callable, Dict, Sequence

import pytest
from pydantic import ValidationError

from openminion.modules.brain.schemas.autonomy.progress import (
    SbspBudgetCeilings,
    SbspBudgetUsage,
)
from openminion.modules.brain.schemas.autonomy.threshold import (
    AutonomyThresholdConfig,
    ClarificationTrigger,
    CostAxisConfig,
    CostAxisInput,
    FactualPrerequisiteAxisConfig,
    FactualPrerequisiteAxisInput,
    ReversibilityAxisConfig,
    ReversibilityAxisInput,
    StylisticPreferenceAxisConfig,
    evaluate_autonomy_threshold,
)

# Closed-set forbidden field names; mirrors the TGCR + APBR + MTRR +
# ASRR rosters verbatim. The five lanes' rosters MUST stay aligned.
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


# Builders for the minimal-valid form of each typed record.


def _autonomy_threshold_config_kwargs() -> Dict[str, Any]:
    return {}  # all fields default


def _reversibility_axis_config_kwargs() -> Dict[str, Any]:
    return {}  # all fields default


def _cost_axis_config_kwargs() -> Dict[str, Any]:
    return {"ceilings": SbspBudgetCeilings()}


def _factual_prerequisite_axis_config_kwargs() -> Dict[str, Any]:
    return {}  # all fields default


def _stylistic_preference_axis_config_kwargs() -> Dict[str, Any]:
    return {}  # all fields default


def _reversibility_axis_input_kwargs() -> Dict[str, Any]:
    return {}  # all fields default


def _cost_axis_input_kwargs() -> Dict[str, Any]:
    return {"usage": SbspBudgetUsage()}


def _factual_prerequisite_axis_input_kwargs() -> Dict[str, Any]:
    return {}  # all fields default


def _clarification_trigger_kwargs() -> Dict[str, Any]:
    return {"triggered": False}


_RECORDS: Sequence[tuple[str, Callable[..., Any], Callable[[], Dict[str, Any]]]] = (
    (
        "AutonomyThresholdConfig",
        AutonomyThresholdConfig,
        _autonomy_threshold_config_kwargs,
    ),
    (
        "ReversibilityAxisConfig",
        ReversibilityAxisConfig,
        _reversibility_axis_config_kwargs,
    ),
    ("CostAxisConfig", CostAxisConfig, _cost_axis_config_kwargs),
    (
        "FactualPrerequisiteAxisConfig",
        FactualPrerequisiteAxisConfig,
        _factual_prerequisite_axis_config_kwargs,
    ),
    (
        "StylisticPreferenceAxisConfig",
        StylisticPreferenceAxisConfig,
        _stylistic_preference_axis_config_kwargs,
    ),
    (
        "ReversibilityAxisInput",
        ReversibilityAxisInput,
        _reversibility_axis_input_kwargs,
    ),
    ("CostAxisInput", CostAxisInput, _cost_axis_input_kwargs),
    (
        "FactualPrerequisiteAxisInput",
        FactualPrerequisiteAxisInput,
        _factual_prerequisite_axis_input_kwargs,
    ),
    (
        "ClarificationTrigger",
        ClarificationTrigger,
        _clarification_trigger_kwargs,
    ),
)


@pytest.mark.parametrize("record_name,ctor,kwargs_fn", _RECORDS)
@pytest.mark.parametrize("forbidden_field", FORBIDDEN_FIELDS)
def test_typed_record_rejects_forbidden_prose_field(
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


def test_forbidden_field_roster_matches_sibling_lane_precedent() -> None:

    from tests.brain.test_apbr_forbidden_fields import (
        FORBIDDEN_FIELDS as APBR_FORBIDDEN_FIELDS,
    )
    from tests.brain.test_asrr_forbidden_fields import (
        FORBIDDEN_FIELDS as ASRR_FORBIDDEN_FIELDS,
    )
    from tests.brain.test_mtrr_forbidden_fields import (
        FORBIDDEN_FIELDS as MTRR_FORBIDDEN_FIELDS,
    )
    from tests.brain.test_tgcr_forbidden_fields import (
        FORBIDDEN_FIELDS as TGCR_FORBIDDEN_FIELDS,
    )

    assert tuple(FORBIDDEN_FIELDS) == tuple(TGCR_FORBIDDEN_FIELDS)
    assert tuple(FORBIDDEN_FIELDS) == tuple(APBR_FORBIDDEN_FIELDS)
    assert tuple(FORBIDDEN_FIELDS) == tuple(MTRR_FORBIDDEN_FIELDS)
    assert tuple(FORBIDDEN_FIELDS) == tuple(ASRR_FORBIDDEN_FIELDS)


def test_composer_constructed_records_also_reject_forbidden_fields() -> None:

    trigger = evaluate_autonomy_threshold(
        config=AutonomyThresholdConfig(),
        reversibility_input=ReversibilityAxisInput(action_reversibility="irreversible"),
    )
    assert isinstance(trigger, ClarificationTrigger)
    dumped = trigger.model_dump()
    dumped["narrative"] = "free-form prose"
    with pytest.raises(ValidationError):
        ClarificationTrigger(**dumped)
