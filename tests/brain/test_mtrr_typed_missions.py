from __future__ import annotations

import typing

import pytest
from pydantic import ValidationError

from openminion.modules.brain.schemas.goals import VerifierFamily
from openminion.modules.brain.schemas.missions import (
    CapabilityBoundary,
    CapabilityBoundaryReason,
    ExploratoryDisclosure,
    ExploratoryDisclosureReason,
    MissionIntakeClassificationSource,
    MissionIntakeRecord,
    MissionType,
    MissionVerifierExpectation,
    ToolReversibility,
    get_mission_verifier_expectation,
    should_emit_exploratory_disclosure,
)
from openminion.modules.tool.plugin_contract import RiskReversibility




def test_mission_type_is_closed_set_of_four_values() -> None:

    values = set(typing.get_args(MissionType))
    assert values == {"coding", "research", "operations", "exploratory"}


def test_mission_type_invalid_value_is_rejected_via_intake_record() -> None:

    with pytest.raises(ValidationError):
        MissionIntakeRecord(
            mission_type="speculative",  # type: ignore[arg-type]
            classification_source="slash_command",
            signal_token="/speculate",
        )




def test_intake_record_constructs_with_minimal_valid_kwargs() -> None:
    record = MissionIntakeRecord(
        mission_type="coding",
        classification_source="slash_command",
        signal_token="/code",
    )
    assert record.mission_type == "coding"
    assert record.classification_source == "slash_command"
    assert record.signal_token == "/code"
    assert record.clarify_request_id is None
    assert record.apd_plan_id is None


def test_intake_record_strips_signal_token() -> None:
    record = MissionIntakeRecord(
        mission_type="research",
        classification_source="kickoff_structure",
        signal_token="  research-kickoff  ",
    )
    assert record.signal_token == "research-kickoff"


def test_intake_record_rejects_blank_signal_token() -> None:
    with pytest.raises(ValidationError):
        MissionIntakeRecord(
            mission_type="research",
            classification_source="kickoff_structure",
            signal_token="   ",
        )


def test_intake_classification_source_closed_set() -> None:

    values = set(typing.get_args(MissionIntakeClassificationSource))
    assert values == {
        "operator_config",
        "slash_command",
        "kickoff_structure",
        "file_path_signal",
        "default",
    }
    assert "model_inference" not in values


def test_intake_record_rejects_unknown_classification_source() -> None:
    with pytest.raises(ValidationError):
        MissionIntakeRecord(
            mission_type="coding",
            classification_source="model_inference",  # type: ignore[arg-type]
            signal_token="/code",
        )




def test_capability_boundary_constructs_with_minimal_valid_kwargs() -> None:
    boundary = CapabilityBoundary(
        reason_code="tool_unavailable",
        requested_capability="code_search",
    )
    assert boundary.reason_code == "tool_unavailable"
    assert boundary.requested_capability == "code_search"
    assert boundary.suggested_alternative is None
    assert boundary.tool_reversibility is None


def test_capability_boundary_reason_code_closed_set_mirrors_tool_resolution_statuses() -> (
    None
):

    values = set(typing.get_args(CapabilityBoundaryReason))
    # Existing structural refusal-status strings in tool_resolution.py
    assert "forced_tool_unavailable" in values
    assert "capability_tool_unavailable" in values
    # Plus the small typed extension for adjacent structural causes.
    assert {
        "tool_unavailable",
        "policy_denied",
        "missing_credential",
        "capability_not_registered",
    } <= values


def test_capability_boundary_rejects_prose_reason_code() -> None:

    with pytest.raises(ValidationError):
        CapabilityBoundary(
            reason_code="the tool was busy and please try again",  # type: ignore[arg-type]
            requested_capability="code_search",
        )


def test_capability_boundary_composes_tool_reversibility() -> None:

    boundary = CapabilityBoundary(
        reason_code="policy_denied",
        requested_capability="rm_rf_root",
        tool_reversibility="irreversible",
    )
    assert boundary.tool_reversibility == "irreversible"




def test_tool_reversibility_is_alias_of_risk_reversibility() -> None:

    assert ToolReversibility is RiskReversibility
    assert set(typing.get_args(ToolReversibility)) == {
        "reversible",
        "partially_reversible",
        "irreversible",
        "unknown",
    }




def test_exploratory_disclosure_constructs_with_minimal_valid_kwargs() -> None:
    disclosure = ExploratoryDisclosure(
        mission_type="exploratory",
        reason="mission_type_is_exploratory",
    )
    assert disclosure.mission_type == "exploratory"
    assert disclosure.reason == "mission_type_is_exploratory"
    assert disclosure.emitted_at_run_start is True


def test_exploratory_disclosure_reason_closed_set() -> None:
    values = set(typing.get_args(ExploratoryDisclosureReason))
    assert values == {
        "no_verifier_expectation_registered",
        "mission_type_is_exploratory",
    }




def test_verifier_expectation_consumes_tgcr_verifier_family_verbatim() -> None:

    expectation = MissionVerifierExpectation(
        mission_type="coding",
        expected_verifier_families=["artifact_presence", "success_criteria_match"],
        autonomous_completion_supported=True,
    )
    expected_families = typing.get_args(VerifierFamily)
    assert all(
        family in expected_families for family in expectation.expected_verifier_families
    )


def test_verifier_expectation_supported_requires_non_empty_families() -> None:

    with pytest.raises(ValidationError):
        MissionVerifierExpectation(
            mission_type="coding",
            expected_verifier_families=[],
            autonomous_completion_supported=True,
        )


def test_verifier_expectation_unsupported_requires_empty_families() -> None:

    with pytest.raises(ValidationError):
        MissionVerifierExpectation(
            mission_type="exploratory",
            expected_verifier_families=["structural"],
            autonomous_completion_supported=False,
        )


def test_verifier_expectation_rejects_duplicate_families() -> None:
    with pytest.raises(ValidationError):
        MissionVerifierExpectation(
            mission_type="coding",
            expected_verifier_families=["artifact_presence", "artifact_presence"],
            autonomous_completion_supported=True,
        )




@pytest.mark.parametrize(
    "mission_type",
    ["coding", "research", "operations", "exploratory"],
)
def test_default_registry_has_record_for_every_mission_type(
    mission_type: MissionType,
) -> None:

    expectation = get_mission_verifier_expectation(mission_type)
    assert expectation.mission_type == mission_type


def test_exploratory_mission_returns_unsupported_expectation() -> None:
    expectation = get_mission_verifier_expectation("exploratory")
    assert expectation.autonomous_completion_supported is False
    assert expectation.expected_verifier_families == []


@pytest.mark.parametrize("mission_type", ["coding", "research", "operations"])
def test_non_exploratory_missions_return_expected_outcome(
    mission_type: MissionType,
) -> None:
    expectation = get_mission_verifier_expectation(mission_type)
    assert expectation.autonomous_completion_supported is True
    assert len(expectation.expected_verifier_families) >= 1


def test_should_emit_exploratory_disclosure_for_exploratory_mission() -> None:

    disclosure = should_emit_exploratory_disclosure("exploratory")
    assert disclosure is not None
    assert disclosure.mission_type == "exploratory"
    assert disclosure.reason == "mission_type_is_exploratory"
    assert disclosure.emitted_at_run_start is True


@pytest.mark.parametrize("mission_type", ["coding", "research", "operations"])
def test_should_not_emit_exploratory_disclosure_for_supported_missions(
    mission_type: MissionType,
) -> None:
    assert should_emit_exploratory_disclosure(mission_type) is None
