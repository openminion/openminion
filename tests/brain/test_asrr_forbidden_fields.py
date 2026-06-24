from __future__ import annotations

from typing import Any, Callable, Dict, Sequence

import pytest
from pydantic import ValidationError

from openminion.modules.brain.schemas.autonomy.progress import (
    NoProgressWatchdogCounters,
    NoProgressWatchdogTrigger,
    ProgressSignal,
    compose_progress_signal,
    evaluate_no_progress_watchdog,
    NoProgressWatchdogConfig,
)
from openminion.modules.brain.schemas.autonomy.strategy import (
    PivotAuthorization,
    ResearchConvergenceConfig,
    ResearchConvergenceCounters,
    ResearchConvergenceSignal,
    StrategyPivotEvent,
)

# Closed-set forbidden field names; mirrors the TGCR + APBR + MTRR
# rosters verbatim. The four lanes' rosters MUST stay aligned.
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


def _pivot_authorization_kwargs() -> Dict[str, Any]:
    return {
        "authorized": False,
    }


def _strategy_pivot_event_kwargs() -> Dict[str, Any]:
    return {
        "from_route": "respond",
        "to_route": "act",
        "authorization": PivotAuthorization(authorized=False),
        "turn_index": 0,
    }


def _research_convergence_config_kwargs() -> Dict[str, Any]:
    return {}  # all fields default


def _research_convergence_counters_kwargs() -> Dict[str, Any]:
    return {}  # all fields default


def _research_convergence_signal_kwargs() -> Dict[str, Any]:
    return {
        "converged": False,
        "counters": ResearchConvergenceCounters(),
        "config": ResearchConvergenceConfig(),
        "progress": ProgressSignal(turn_index=0),
    }


_RECORDS: Sequence[tuple[str, Callable[..., Any], Callable[[], Dict[str, Any]]]] = (
    ("PivotAuthorization", PivotAuthorization, _pivot_authorization_kwargs),
    ("StrategyPivotEvent", StrategyPivotEvent, _strategy_pivot_event_kwargs),
    (
        "ResearchConvergenceConfig",
        ResearchConvergenceConfig,
        _research_convergence_config_kwargs,
    ),
    (
        "ResearchConvergenceCounters",
        ResearchConvergenceCounters,
        _research_convergence_counters_kwargs,
    ),
    (
        "ResearchConvergenceSignal",
        ResearchConvergenceSignal,
        _research_convergence_signal_kwargs,
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
    from tests.brain.test_mtrr_forbidden_fields import (
        FORBIDDEN_FIELDS as MTRR_FORBIDDEN_FIELDS,
    )
    from tests.brain.test_tgcr_forbidden_fields import (
        FORBIDDEN_FIELDS as TGCR_FORBIDDEN_FIELDS,
    )

    assert tuple(FORBIDDEN_FIELDS) == tuple(TGCR_FORBIDDEN_FIELDS)
    assert tuple(FORBIDDEN_FIELDS) == tuple(APBR_FORBIDDEN_FIELDS)
    assert tuple(FORBIDDEN_FIELDS) == tuple(MTRR_FORBIDDEN_FIELDS)


def test_composer_constructed_records_also_reject_forbidden_fields() -> None:

    watchdog_trigger = evaluate_no_progress_watchdog(
        counters=NoProgressWatchdogCounters(),
        config=NoProgressWatchdogConfig(),
    )
    assert isinstance(watchdog_trigger, NoProgressWatchdogTrigger)
    progress = compose_progress_signal(turn_index=0)
    assert isinstance(progress, ProgressSignal)

    # PivotAuthorization round-trip with forbidden field MUST fail.
    auth = PivotAuthorization(authorized=False)
    dumped = auth.model_dump()
    dumped["narrative"] = "free-form prose"
    with pytest.raises(ValidationError):
        PivotAuthorization(**dumped)
