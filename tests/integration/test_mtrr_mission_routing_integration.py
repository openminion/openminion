from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import pytest

from openminion.modules.brain.schemas.goals import VerifierFamily
from openminion.modules.brain.schemas.missions import (
    CapabilityBoundary,
    CapabilityBoundaryReason,
    ExploratoryDisclosure,
    MissionIntakeClassificationSource,
    MissionIntakeRecord,
    MissionType,
    MissionVerifierExpectation,
    get_mission_verifier_expectation,
    should_emit_exploratory_disclosure,
)


# Test fixtures: structural intake-signal table.


@dataclass(frozen=True)
class _IntakeFixture:
    mission_type: MissionType
    classification_source: MissionIntakeClassificationSource
    signal_token: str


# One fixture per closed-set MissionType value, plus an extra row that
# verifies the ``default`` classification source path (used when no
# stronger structural signal is present).
_INTAKE_FIXTURES: Sequence[_IntakeFixture] = (
    _IntakeFixture(
        mission_type="coding",
        classification_source="slash_command",
        signal_token="/code",
    ),
    _IntakeFixture(
        mission_type="research",
        classification_source="kickoff_structure",
        signal_token="research-kickoff",
    ),
    _IntakeFixture(
        mission_type="operations",
        classification_source="operator_config",
        signal_token="ops-runbook-key",
    ),
    _IntakeFixture(
        mission_type="exploratory",
        classification_source="default",
        signal_token="no-structural-signal",
    ),
    _IntakeFixture(
        mission_type="coding",
        classification_source="file_path_signal",
        signal_token=".py",
    ),
)


# Phase 1: intake → MissionType classification.


@pytest.mark.parametrize("fixture", _INTAKE_FIXTURES)
def test_intake_builds_typed_record(fixture: _IntakeFixture) -> None:
    record = MissionIntakeRecord(
        mission_type=fixture.mission_type,
        classification_source=fixture.classification_source,
        signal_token=fixture.signal_token,
    )
    assert record.mission_type == fixture.mission_type
    assert record.classification_source == fixture.classification_source
    assert record.signal_token == fixture.signal_token


# Phase 2: capability check → typed CapabilityBoundary record.


@pytest.mark.parametrize(
    "structural_status,reason_code",
    [
        ("forced_tool_unavailable", "forced_tool_unavailable"),
        ("capability_tool_unavailable", "capability_tool_unavailable"),
    ],
)
def test_capability_boundary_lifts_existing_structural_status(
    structural_status: str,
    reason_code: CapabilityBoundaryReason,
) -> None:

    boundary = CapabilityBoundary(
        reason_code=reason_code,
        requested_capability="code_search",
    )
    assert boundary.reason_code == structural_status


def test_capability_boundary_composes_tool_reversibility_axis() -> None:

    boundary = CapabilityBoundary(
        reason_code="policy_denied",
        requested_capability="destructive_action",
        tool_reversibility="irreversible",
        suggested_alternative="dry_run_action",
    )
    assert boundary.tool_reversibility == "irreversible"
    assert boundary.suggested_alternative == "dry_run_action"


# Phase 3: verifier-expectation lookup.


@pytest.mark.parametrize(
    "mission_type,expected_supported",
    [
        ("coding", True),
        ("research", True),
        ("operations", True),
        ("exploratory", False),
    ],
)
def test_verifier_expectation_lookup_is_structural(
    mission_type: MissionType, expected_supported: bool
) -> None:
    expectation = get_mission_verifier_expectation(mission_type)
    assert isinstance(expectation, MissionVerifierExpectation)
    assert expectation.mission_type == mission_type
    assert expectation.autonomous_completion_supported is expected_supported


def test_verifier_expectation_consumes_tgcr_families_verbatim() -> None:

    tgcr_families = set(_get_literal_args(VerifierFamily))
    for mission_type in ("coding", "research", "operations", "exploratory"):
        expectation = get_mission_verifier_expectation(mission_type)
        for family in expectation.expected_verifier_families:
            assert family in tgcr_families


# Phase 4: exploratory disclosure emission.


def test_exploratory_mission_emits_disclosure_at_run_start() -> None:
    disclosure = should_emit_exploratory_disclosure("exploratory")
    assert disclosure is not None
    assert disclosure.mission_type == "exploratory"
    assert disclosure.reason == "mission_type_is_exploratory"
    assert disclosure.emitted_at_run_start is True


@pytest.mark.parametrize("mission_type", ["coding", "research", "operations"])
def test_supported_missions_do_not_emit_disclosure(
    mission_type: MissionType,
) -> None:

    assert should_emit_exploratory_disclosure(mission_type) is None


# Full-flow integration: all four phases compose.


@pytest.mark.parametrize("fixture", _INTAKE_FIXTURES)
def test_full_mission_routing_flow_composes_typed_surfaces(
    fixture: _IntakeFixture,
) -> None:

    # Phase 1: intake.
    intake = MissionIntakeRecord(
        mission_type=fixture.mission_type,
        classification_source=fixture.classification_source,
        signal_token=fixture.signal_token,
    )

    # Phase 2: lookup.
    expectation = get_mission_verifier_expectation(intake.mission_type)
    assert expectation.mission_type == intake.mission_type

    # Phase 3: structural disclosure decider.
    disclosure = should_emit_exploratory_disclosure(intake.mission_type)
    if fixture.mission_type == "exploratory":
        assert disclosure is not None
        assert isinstance(disclosure, ExploratoryDisclosure)
        assert expectation.autonomous_completion_supported is False
    else:
        assert disclosure is None
        assert expectation.autonomous_completion_supported is True
        assert len(expectation.expected_verifier_families) >= 1

    # Phase 4: structural refusal path (composes ``ToolReversibility``).
    refusal = CapabilityBoundary(
        reason_code="capability_tool_unavailable",
        requested_capability=f"capability_for_{fixture.mission_type}",
        tool_reversibility="unknown",
    )
    assert refusal.reason_code == "capability_tool_unavailable"
    assert refusal.tool_reversibility == "unknown"


# MTRR-03 ToolReversibility adoption audit.


def test_tool_reversibility_axis_is_alias_not_new_enum() -> None:
    from openminion.modules.brain.schemas.missions import ToolReversibility
    from openminion.modules.tool.plugin_contract import RiskReversibility

    assert ToolReversibility is RiskReversibility


def test_existing_risk_specs_carry_explicit_reversibility_when_present() -> None:

    from openminion.modules.tool.plugin_contract import RiskSpec

    spec = RiskSpec(risk_class="read")
    # The schema's default value is itself a closed-set Literal value —
    # adoption is structurally complete because reversibility is never
    # ``None`` and never absent from a constructed ``RiskSpec``.
    assert spec.reversibility in {
        "reversible",
        "partially_reversible",
        "irreversible",
        "unknown",
    }


def _get_literal_args(literal_type: object) -> tuple[object, ...]:

    import typing

    return typing.get_args(literal_type)
