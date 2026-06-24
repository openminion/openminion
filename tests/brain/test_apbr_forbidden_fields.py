from __future__ import annotations

from typing import Any, Callable, Dict, Sequence

import pytest
from pydantic import ValidationError

from openminion.modules.brain.schemas.autonomy.progress import (
    BudgetExtensionTrigger,
    MissionProgressCheckpoint,
    NoProgressWatchdogConfig,
    NoProgressWatchdogCounters,
    NoProgressWatchdogTrigger,
    ProgressSignal,
    SbspBudgetCeilings,
    SbspBudgetUsage,
)

# Closed-set forbidden field names; mirrors the TGCR roster.
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


def _progress_signal_kwargs() -> Dict[str, Any]:
    return {
        "turn_index": 0,
    }


def _sbsp_budget_ceilings_kwargs() -> Dict[str, Any]:
    return {}  # all fields optional


def _sbsp_budget_usage_kwargs() -> Dict[str, Any]:
    return {}  # all fields optional


def _budget_extension_trigger_kwargs() -> Dict[str, Any]:
    return {
        "may_extend": False,
        "progress": ProgressSignal(**_progress_signal_kwargs()),
        "ceilings": SbspBudgetCeilings(),
        "usage": SbspBudgetUsage(),
    }


def _mission_progress_checkpoint_kwargs() -> Dict[str, Any]:
    return {
        "checkpoint_id": "cp1",
        "turn_index": 0,
        "progress": ProgressSignal(**_progress_signal_kwargs()),
    }


def _no_progress_watchdog_config_kwargs() -> Dict[str, Any]:
    return {}  # all fields have defaults


def _no_progress_watchdog_counters_kwargs() -> Dict[str, Any]:
    return {}  # all fields have defaults


def _no_progress_watchdog_trigger_kwargs() -> Dict[str, Any]:
    return {
        "fired": False,
        "counters": NoProgressWatchdogCounters(),
        "config": NoProgressWatchdogConfig(),
    }


_RECORDS: Sequence[tuple[str, Callable[..., Any], Callable[[], Dict[str, Any]]]] = (
    ("ProgressSignal", ProgressSignal, _progress_signal_kwargs),
    ("SbspBudgetCeilings", SbspBudgetCeilings, _sbsp_budget_ceilings_kwargs),
    ("SbspBudgetUsage", SbspBudgetUsage, _sbsp_budget_usage_kwargs),
    (
        "BudgetExtensionTrigger",
        BudgetExtensionTrigger,
        _budget_extension_trigger_kwargs,
    ),
    (
        "MissionProgressCheckpoint",
        MissionProgressCheckpoint,
        _mission_progress_checkpoint_kwargs,
    ),
    (
        "NoProgressWatchdogConfig",
        NoProgressWatchdogConfig,
        _no_progress_watchdog_config_kwargs,
    ),
    (
        "NoProgressWatchdogCounters",
        NoProgressWatchdogCounters,
        _no_progress_watchdog_counters_kwargs,
    ),
    (
        "NoProgressWatchdogTrigger",
        NoProgressWatchdogTrigger,
        _no_progress_watchdog_trigger_kwargs,
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
