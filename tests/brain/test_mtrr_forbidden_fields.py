from __future__ import annotations

from typing import Any, Callable, Dict, Sequence

import pytest
from pydantic import ValidationError

from openminion.modules.brain.schemas.missions import (
    CapabilityBoundary,
    ExploratoryDisclosure,
    MissionIntakeRecord,
    MissionVerifierExpectation,
)

# Closed-set forbidden field names; mirrors the TGCR + APBR rosters
# verbatim. The three lanes' rosters MUST stay aligned.
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
# Each builder returns a fresh dict so callers can splice in a
# forbidden-field kwarg without mutating shared state.


def _mission_intake_record_kwargs() -> Dict[str, Any]:
    return {
        "mission_type": "coding",
        "classification_source": "slash_command",
        "signal_token": "/code",
    }


def _capability_boundary_kwargs() -> Dict[str, Any]:
    return {
        "reason_code": "tool_unavailable",
        "requested_capability": "code_search",
    }


def _exploratory_disclosure_kwargs() -> Dict[str, Any]:
    return {
        "mission_type": "exploratory",
        "reason": "mission_type_is_exploratory",
    }


def _mission_verifier_expectation_kwargs() -> Dict[str, Any]:
    return {
        "mission_type": "coding",
        "expected_verifier_families": ["artifact_presence"],
        "autonomous_completion_supported": True,
    }


_RECORDS: Sequence[tuple[str, Callable[..., Any], Callable[[], Dict[str, Any]]]] = (
    (
        "MissionIntakeRecord",
        MissionIntakeRecord,
        _mission_intake_record_kwargs,
    ),
    (
        "CapabilityBoundary",
        CapabilityBoundary,
        _capability_boundary_kwargs,
    ),
    (
        "ExploratoryDisclosure",
        ExploratoryDisclosure,
        _exploratory_disclosure_kwargs,
    ),
    (
        "MissionVerifierExpectation",
        MissionVerifierExpectation,
        _mission_verifier_expectation_kwargs,
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


def test_forbidden_field_roster_matches_tgcr_apbr_precedent() -> None:

    from tests.brain.test_apbr_forbidden_fields import (
        FORBIDDEN_FIELDS as APBR_FORBIDDEN_FIELDS,
    )
    from tests.brain.test_tgcr_forbidden_fields import (
        FORBIDDEN_FIELDS as TGCR_FORBIDDEN_FIELDS,
    )

    assert tuple(FORBIDDEN_FIELDS) == tuple(TGCR_FORBIDDEN_FIELDS)
    assert tuple(FORBIDDEN_FIELDS) == tuple(APBR_FORBIDDEN_FIELDS)
