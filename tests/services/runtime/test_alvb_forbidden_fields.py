from __future__ import annotations

import inspect
from typing import Any, Callable, Dict, Sequence

import pytest
from pydantic import ValidationError

from openminion.modules.brain.runtime.verification.policy import (
    VerifierInvocation,
    VerifierResult,
)
from openminion.modules.brain.schemas import (
    Deliverable,
    FailureCondition,
    Goal,
    SuccessCriterion,
)
from openminion.services.runtime.run_status import Run, RunCheckpoint
from openminion.services.runtime.verifier_binding import (
    bind_run_terminal_event,
    derive_run_terminal_state,
)


# Closed-set forbidden field names; mirrors the TGCR + APBR + MTRR +
# ASRR + AATR + SPRR rosters verbatim. The seven lanes' rosters MUST
# stay aligned.
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


# Builders mirror the TGCR forbidden-fields suite verbatim.


def _success_criterion_kwargs() -> Dict[str, Any]:
    return {
        "criterion_id": "c1",
        "description": "placeholder",
        "structural_check": "artifact_present",
    }


def _deliverable_kwargs() -> Dict[str, Any]:
    return {
        "deliverable_id": "d1",
        "description": "placeholder",
        "verification_hint": "artifact_presence",
    }


def _failure_condition_kwargs() -> Dict[str, Any]:
    return {
        "condition_id": "f1",
        "kind": "deliverable_missing",
        "description": "placeholder",
    }


def _goal_kwargs() -> Dict[str, Any]:
    return {
        "goal_id": "g1",
        "description": "placeholder",
        "success_criteria": [SuccessCriterion(**_success_criterion_kwargs())],
        "deliverables": [Deliverable(**_deliverable_kwargs())],
        "failure_conditions": [FailureCondition(**_failure_condition_kwargs())],
    }


def _verifier_invocation_kwargs() -> Dict[str, Any]:
    return {
        "invocation_id": "vi1",
        "verifier_family": "structural",
        "target_id": "c1",
    }


def _verifier_result_kwargs() -> Dict[str, Any]:
    return {
        "invocation_id": "vi1",
        "verifier_family": "structural",
        "target_id": "c1",
        "passed": True,
    }


def _run_kwargs() -> Dict[str, Any]:
    return {
        "run_id": "r1",
        "session_id": "s1",
        "goal_id": "g1",
        "state": "queued",
    }


def _run_checkpoint_kwargs() -> Dict[str, Any]:
    return {
        "checkpoint_id": "cp1",
        "run_id": "r1",
        "goal_id": "g1",
        "turn_index": 0,
        "summary": "placeholder",
    }


# Each record exposes (constructor, builder, expected-exception-type).
# Pydantic BaseModel → ValidationError. Frozen dataclass → TypeError.
_RECORDS: Sequence[
    tuple[str, Callable[..., Any], Callable[[], Dict[str, Any]], type[Exception]]
] = (
    ("SuccessCriterion", SuccessCriterion, _success_criterion_kwargs, ValidationError),
    ("Deliverable", Deliverable, _deliverable_kwargs, ValidationError),
    ("FailureCondition", FailureCondition, _failure_condition_kwargs, ValidationError),
    ("Goal", Goal, _goal_kwargs, ValidationError),
    ("VerifierInvocation", VerifierInvocation, _verifier_invocation_kwargs, TypeError),
    ("VerifierResult", VerifierResult, _verifier_result_kwargs, TypeError),
    ("Run", Run, _run_kwargs, TypeError),
    ("RunCheckpoint", RunCheckpoint, _run_checkpoint_kwargs, TypeError),
)


@pytest.mark.parametrize("record_name,ctor,kwargs_fn,exc_type", _RECORDS)
@pytest.mark.parametrize("forbidden_field", FORBIDDEN_FIELDS)
def test_typed_record_rejects_forbidden_prose_field(
    record_name: str,
    ctor: Callable[..., Any],
    kwargs_fn: Callable[[], Dict[str, Any]],
    forbidden_field: str,
    exc_type: type[Exception],
) -> None:
    kwargs = kwargs_fn()
    kwargs[forbidden_field] = "any-prose-shaped-value"
    with pytest.raises(exc_type):
        ctor(**kwargs)


def test_forbidden_field_list_is_non_empty_and_unique() -> None:
    assert len(FORBIDDEN_FIELDS) > 0
    assert len(set(FORBIDDEN_FIELDS)) == len(FORBIDDEN_FIELDS)


def test_forbidden_field_roster_matches_six_lane_precedent() -> None:

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

    assert tuple(FORBIDDEN_FIELDS) == tuple(TGCR_FORBIDDEN_FIELDS)
    assert tuple(FORBIDDEN_FIELDS) == tuple(APBR_FORBIDDEN_FIELDS)
    assert tuple(FORBIDDEN_FIELDS) == tuple(MTRR_FORBIDDEN_FIELDS)
    assert tuple(FORBIDDEN_FIELDS) == tuple(ASRR_FORBIDDEN_FIELDS)
    assert tuple(FORBIDDEN_FIELDS) == tuple(AATR_FORBIDDEN_FIELDS)
    assert tuple(FORBIDDEN_FIELDS) == tuple(PROMOTION_FORBIDDEN_FIELDS)


def test_derive_run_terminal_state_accepts_only_typed_inputs() -> None:
    sig = inspect.signature(derive_run_terminal_state)
    param_names = list(sig.parameters.keys())
    assert param_names == ["goal", "verifier_results", "fired_failure_conditions"], (
        f"derive_run_terminal_state signature must be typed-only; got {param_names!r}"
    )
    # The forbidden field names must not appear as parameter names.
    for name in FORBIDDEN_FIELDS:
        assert name not in param_names


def test_bind_run_terminal_event_has_no_prose_parameter() -> None:
    sig = inspect.signature(bind_run_terminal_event)
    param_names = list(sig.parameters.keys())
    for name in FORBIDDEN_FIELDS:
        assert name not in param_names, (
            f"bind_run_terminal_event must not accept forbidden prose "
            f"field {name!r} as a parameter name"
        )
