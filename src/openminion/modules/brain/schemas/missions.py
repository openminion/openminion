from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .goals import VerifierFamily

from openminion.modules.tool.plugin_contract import RiskReversibility

ToolReversibility = RiskReversibility


MissionType = Literal[
    "coding",
    "research",
    "operations",
    "exploratory",
]


MissionIntakeClassificationSource = Literal[
    "operator_config",
    "slash_command",
    "kickoff_structure",
    "file_path_signal",
    "default",
]


CapabilityBoundaryReason = Literal[
    "tool_unavailable",
    "forced_tool_unavailable",
    "capability_tool_unavailable",
    "policy_denied",
    "missing_credential",
    "capability_not_registered",
]


ExploratoryDisclosureReason = Literal[
    "no_verifier_expectation_registered",
    "mission_type_is_exploratory",
]


class MissionIntakeRecord(BaseModel):
    """Runtime-owned structural record of how a mission entered the loop."""

    model_config = ConfigDict(extra="forbid")

    mission_type: MissionType
    classification_source: MissionIntakeClassificationSource
    signal_token: str = Field(
        min_length=1,
        description=(
            "Structural token that drove classification — e.g. the slash "
            "command name, the operator-config key, or the file-extension "
            "pattern. Never free prose; structural tokens only."
        ),
    )
    clarify_request_id: str | None = Field(
        default=None,
        description=(
            "Optional reference to a ``ClarifyRequest.id`` if intake "
            "clarification fired before the mission type stabilized. "
            "Composes with existing clarify flow; does not replace it."
        ),
    )
    apd_plan_id: str | None = Field(
        default=None,
        description=(
            "Optional APD plan id when the intake produced a structured "
            "plan. Cross-references the same ``TaskPlan.plan_id`` surface "
            "TGCR's ``Goal.apd_plan_id`` references."
        ),
    )

    @field_validator("signal_token", mode="before")
    @classmethod
    def _strip_signal_token(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("clarify_request_id", "apd_plan_id", mode="before")
    @classmethod
    def _strip_optional_text(cls, value: Any) -> str | None:
        text = str(value or "").strip()
        return text or None


class CapabilityBoundary(BaseModel):
    """Typed capability-boundary fact."""

    model_config = ConfigDict(extra="forbid")

    reason_code: CapabilityBoundaryReason
    requested_capability: str = Field(
        min_length=1,
        description=(
            "Structural identifier of the capability that was requested — "
            "tool name, capability category, or registered capability id. "
            "Free prose is rejected."
        ),
    )
    suggested_alternative: str | None = Field(
        default=None,
        description=(
            "Optional structural identifier of an alternative tool / "
            "capability the runtime could surface instead. NEVER prose."
        ),
    )
    tool_reversibility: ToolReversibility | None = Field(
        default=None,
        description=(
            "Optional reversibility classification of the requested "
            "capability when available — composes the existing "
            "``RiskReversibility`` axis into the mission-routing owner per "
            "MTRR-03 task contract."
        ),
    )

    @field_validator("requested_capability", mode="before")
    @classmethod
    def _strip_capability(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("suggested_alternative", mode="before")
    @classmethod
    def _strip_alternative(cls, value: Any) -> str | None:
        text = str(value or "").strip()
        return text or None


class ExploratoryDisclosure(BaseModel):
    """Typed disclosure that no autonomous-completion verifier is expected."""

    model_config = ConfigDict(extra="forbid")

    mission_type: MissionType
    reason: ExploratoryDisclosureReason
    emitted_at_run_start: bool = Field(
        default=True,
        description=(
            "True when the disclosure was emitted at run start (MTRR-Q4 "
            "default). A future extension may emit retrospective "
            "disclosures at run completion; the field exists so consumers "
            "can distinguish without a schema change."
        ),
    )


class MissionVerifierExpectation(BaseModel):
    """Typed per-``MissionType`` verifier-expectation record."""

    model_config = ConfigDict(extra="forbid")

    mission_type: MissionType
    expected_verifier_families: list[VerifierFamily] = Field(
        default_factory=list,
        description=(
            "Closed-set list of TGCR ``VerifierFamily`` values this "
            "mission type expects. Empty list is legal only when "
            "``autonomous_completion_supported is False``."
        ),
    )
    autonomous_completion_supported: bool = Field(
        description=(
            "Explicit structural flag: ``True`` when at least one "
            "verifier family is expected to confirm completion; ``False`` "
            "when the mission terminates on user/model termination "
            "(triggers ``ExploratoryDisclosure`` at run start)."
        ),
    )

    @model_validator(mode="after")
    def _validate_expectation_consistency(self) -> "MissionVerifierExpectation":
        if self.autonomous_completion_supported and not self.expected_verifier_families:
            raise ValueError(
                "MissionVerifierExpectation with "
                "`autonomous_completion_supported=True` must list at "
                "least one expected_verifier_family."
            )
        if not self.autonomous_completion_supported and self.expected_verifier_families:
            raise ValueError(
                "MissionVerifierExpectation with "
                "`autonomous_completion_supported=False` must have an "
                "empty expected_verifier_families list."
            )
        if len(set(self.expected_verifier_families)) != len(
            self.expected_verifier_families
        ):
            raise ValueError(
                "MissionVerifierExpectation.expected_verifier_families "
                "must have unique values."
            )
        return self


_DEFAULT_MISSION_VERIFIER_EXPECTATIONS: dict[str, MissionVerifierExpectation] = {
    "coding": MissionVerifierExpectation(
        mission_type="coding",
        expected_verifier_families=["artifact_presence", "success_criteria_match"],
        autonomous_completion_supported=True,
    ),
    "research": MissionVerifierExpectation(
        mission_type="research",
        expected_verifier_families=["artifact_presence", "freshness"],
        autonomous_completion_supported=True,
    ),
    "operations": MissionVerifierExpectation(
        mission_type="operations",
        expected_verifier_families=["structural", "success_criteria_match"],
        autonomous_completion_supported=True,
    ),
    "exploratory": MissionVerifierExpectation(
        mission_type="exploratory",
        expected_verifier_families=[],
        autonomous_completion_supported=False,
    ),
}


def get_mission_verifier_expectation(
    mission_type: MissionType,
) -> MissionVerifierExpectation:
    """Return the typed ``MissionVerifierExpectation`` for ``mission_type``."""

    record = _DEFAULT_MISSION_VERIFIER_EXPECTATIONS.get(str(mission_type))
    if record is None:
        raise KeyError(
            f"Unknown MissionType: {mission_type!r}. "
            f"Allowed: {sorted(_DEFAULT_MISSION_VERIFIER_EXPECTATIONS)}"
        )
    return record


def should_emit_exploratory_disclosure(
    mission_type: MissionType,
) -> ExploratoryDisclosure | None:
    """Return a disclosure when the mission type has no completion verifier."""

    expectation = get_mission_verifier_expectation(mission_type)
    if expectation.autonomous_completion_supported:
        return None
    reason: ExploratoryDisclosureReason = (
        "mission_type_is_exploratory"
        if mission_type == "exploratory"
        else "no_verifier_expectation_registered"
    )
    return ExploratoryDisclosure(
        mission_type=mission_type,
        reason=reason,
        emitted_at_run_start=True,
    )


__all__ = [
    "CapabilityBoundary",
    "CapabilityBoundaryReason",
    "ExploratoryDisclosure",
    "ExploratoryDisclosureReason",
    "MissionIntakeClassificationSource",
    "MissionIntakeRecord",
    "MissionType",
    "MissionVerifierExpectation",
    "ToolReversibility",
    "get_mission_verifier_expectation",
    "should_emit_exploratory_disclosure",
]
