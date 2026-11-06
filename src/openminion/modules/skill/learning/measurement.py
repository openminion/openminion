"""Frozen paired-transfer evidence for an operator-evaluated learned procedure."""

from __future__ import annotations

from collections import Counter
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from openminion.modules.skill.models import stable_hash

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Verdict = Literal[
    "demonstrated_gain", "no_demonstrated_gain", "regression", "inconclusive"
]


class MeasurementIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_revision: Text
    proposal_id: Text
    shape_id: Text
    agent_id: Text
    candidate_hash: Text
    skill_id: Text
    package_hash: Text
    corpus_digest: Text
    oracle_digest: Text
    model: Text
    provider: Text
    config_digest: Text
    permission_digest: Text


class MeasurementLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    turn_tokens: Literal[40000] = 40000
    turn_tools: Literal[40] = 40
    turn_seconds: Literal[300] = 300
    campaign_tokens: Literal[480000] = 480000
    campaign_seconds: Literal[3600] = 3600
    attempt_tokens: Literal[760000] = 760000
    attempt_seconds: Literal[5700] = 5700


class TrialKey(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: Text
    arm: Literal["baseline", "candidate"]
    repeat: Literal[1, 2]


def paired_trial_order(case_ids: list[str]) -> list[TrialKey]:
    repeats: tuple[Literal[1, 2], ...] = (1, 2)
    arms: tuple[Literal["baseline", "candidate"], ...] = ("baseline", "candidate")
    return [
        TrialKey(case_id=case_id, arm=arm, repeat=repeat)
        for repeat in repeats
        for index, case_id in enumerate(case_ids)
        for arm in (arms if (index + repeat) % 2 else tuple(reversed(arms)))
    ]


class MeasurementManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["verified-learning-manifest/v1"] = "verified-learning-manifest/v1"
    identity: MeasurementIdentity
    cases: dict[Text, bool]
    acquisition_evidence_refs: list[Text] = Field(min_length=2)
    limits: MeasurementLimits = Field(default_factory=MeasurementLimits)
    trial_order: list[TrialKey]
    rule: Literal["paired-correctness-with-125pct-token-ceiling/v1"] = (
        "paired-correctness-with-125pct-token-ceiling/v1"
    )

    @model_validator(mode="after")
    def validate_design(self) -> MeasurementManifest:
        if len(set(self.acquisition_evidence_refs)) != len(
            self.acquisition_evidence_refs
        ):
            raise ValueError(
                "acquisition evidence must name distinct development traces"
            )
        if len(self.cases) != 3 or sum(self.cases.values()) != 2:
            raise ValueError(
                "measurement requires two applicable and one inapplicable case"
            )
        if self.trial_order != paired_trial_order(list(self.cases)):
            raise ValueError(
                "measurement trial order must match the frozen paired design"
            )
        return self


class MeasurementObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: Text
    trace_id: str | None = None
    trace_ref: str | None = None
    command_ref: Text
    result_ref: Text
    selected_skills: dict[str, str] = Field(default_factory=dict)
    outcome: Literal["correct", "incorrect", "unavailable"]
    tokens: int | None = Field(default=None, ge=0)
    tool_calls: int = Field(ge=0)
    elapsed_seconds: float = Field(ge=0, allow_inf_nan=False)
    interventions: int = Field(default=0, ge=0)
    cost: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    contaminated: bool = False


class MeasurementTrial(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: TrialKey
    applicable: bool
    observation: MeasurementObservation


class MeasurementAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phase: Literal["acquisition", "authoring", "replay"]
    observation: MeasurementObservation


class MeasurementReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["verified-learning-measurement/v1"] = (
        "verified-learning-measurement/v1"
    )
    identity: MeasurementIdentity
    manifest_ref: Text
    manifest_digest: Text
    trials: list[MeasurementTrial]
    attempts: list[MeasurementAttempt]
    verdict: Verdict


def _within_turn_limits(
    observation: MeasurementObservation, limits: MeasurementLimits
) -> bool:
    return (
        observation.tokens is not None
        and observation.tokens <= limits.turn_tokens
        and observation.tool_calls <= limits.turn_tools
        and observation.elapsed_seconds <= limits.turn_seconds
        and not observation.contaminated
        and bool(observation.trace_id and observation.trace_ref)
        and observation.outcome != "unavailable"
    )


def measurement_verdict(
    manifest: MeasurementManifest, report: MeasurementReport
) -> Verdict:
    pairs: dict[tuple[str, int], dict[str, MeasurementObservation]] = {}
    for trial in report.trials:
        pairs.setdefault((trial.key.case_id, trial.key.repeat), {})[trial.key.arm] = (
            trial.observation
        )
    # Preserve an observed regression even when a different trial is incomplete.
    if any(
        pair.get("baseline") is not None
        and pair.get("candidate") is not None
        and pair["baseline"].outcome == "correct"
        and pair["candidate"].outcome == "incorrect"
        for pair in pairs.values()
    ):
        return "regression"
    if not _complete_measurement(manifest, report):
        return "inconclusive"
    baseline = [t.observation for t in report.trials if t.key.arm == "baseline"]
    candidate = [t.observation for t in report.trials if t.key.arm == "candidate"]
    gain = sum(
        int(pair["candidate"].outcome == "correct")
        - int(pair["baseline"].outcome == "correct")
        for (case_id, _), pair in pairs.items()
        if manifest.cases[case_id]
    )
    inapplicable_correct = all(
        t.observation.outcome == "correct"
        and manifest.identity.skill_id not in t.observation.selected_skills
        for t in report.trials
        if not t.applicable
    )
    if (
        gain >= 1
        and inapplicable_correct
        and any(
            t.key.arm == "candidate"
            and t.applicable
            and t.observation.outcome == "correct"
            and t.observation.selected_skills.get(manifest.identity.skill_id)
            == manifest.identity.package_hash
            for t in report.trials
        )
        and sum(o.interventions for o in candidate)
        <= sum(o.interventions for o in baseline)
        and sum(o.tokens or 0 for o in candidate)
        <= 1.25 * sum(o.tokens or 0 for o in baseline)
    ):
        return "demonstrated_gain"
    return "no_demonstrated_gain"


def _complete_measurement(
    manifest: MeasurementManifest, report: MeasurementReport
) -> bool:
    trials = report.trials
    if report.identity != manifest.identity:
        return False
    if [t.key for t in trials] != manifest.trial_order:
        return False
    if any(t.applicable != manifest.cases[t.key.case_id] for t in trials):
        return False
    counts = Counter(a.phase for a in report.attempts)
    if counts["acquisition"] > 3 or counts["authoring"] != 1 or counts["replay"] != 3:
        return False
    if any(
        a.observation.outcome != "correct"
        for a in report.attempts
        if a.phase in {"authoring", "replay"}
    ):
        return False
    observations = [t.observation for t in trials]
    all_observations = observations + [a.observation for a in report.attempts]
    if len({o.session_id for o in all_observations}) != len(all_observations):
        return False
    if not all(_within_turn_limits(o, manifest.limits) for o in all_observations):
        return False
    if len({o.trace_id for o in all_observations}) != len(all_observations) or len(
        {o.trace_ref for o in all_observations}
    ) != len(all_observations):
        return False
    if any(
        manifest.identity.skill_id in t.observation.selected_skills
        and (
            t.key.arm == "baseline"
            or t.observation.selected_skills[manifest.identity.skill_id]
            != manifest.identity.package_hash
        )
        for t in trials
    ):
        return False
    return (
        sum(o.tokens or 0 for o in observations) <= manifest.limits.campaign_tokens
        and sum(o.elapsed_seconds for o in observations)
        <= manifest.limits.campaign_seconds
        and sum(o.tokens or 0 for o in all_observations)
        <= manifest.limits.attempt_tokens
        and sum(o.elapsed_seconds for o in all_observations)
        <= manifest.limits.attempt_seconds
    )


def validate_measurement_attachment(
    artifactctl: Any,
    *,
    measurement_ref: str,
    manifest_ref: str,
    identity: dict[str, str],
    evaluator_id: str,
    acquisition_evidence_refs: list[str] | None = None,
) -> MeasurementReport:
    for ref in (measurement_ref, manifest_ref):
        metadata = artifactctl.get(ref)
        if metadata.deleted_at:
            raise ValueError("measurement evidence is deleted")
        if metadata.agent_id != evaluator_id:
            raise ValueError("measurement evaluator provenance mismatch")
    report = MeasurementReport.model_validate_json(
        artifactctl.read_bytes(measurement_ref)
    )
    manifest = MeasurementManifest.model_validate_json(
        artifactctl.read_bytes(manifest_ref)
    )
    if report.manifest_ref != manifest_ref or report.manifest_digest != stable_hash(
        manifest.model_dump(mode="json")
    ):
        raise ValueError("measurement manifest mismatch")
    if report.identity != manifest.identity or any(
        report.identity.model_dump()[key] != value for key, value in identity.items()
    ):
        raise ValueError("measurement candidate identity mismatch")
    if acquisition_evidence_refs is not None and not set(
        manifest.acquisition_evidence_refs
    ).issubset(acquisition_evidence_refs):
        raise ValueError(
            "measurement acquisition evidence does not match the stored proposal"
        )
    if [t.key for t in report.trials] != manifest.trial_order:
        raise ValueError("measurement requires the complete frozen paired cases")
    observations = [t.observation for t in report.trials] + [
        a.observation for a in report.attempts
    ]
    refs = {
        ref
        for observation in observations
        for ref in (
            observation.trace_ref,
            observation.command_ref,
            observation.result_ref,
        )
        if ref is not None
    }
    for ref in refs:
        if artifactctl.get(ref).deleted_at:
            raise ValueError("measurement trial evidence is deleted")
        artifactctl.read_bytes(ref)
    if report.verdict != measurement_verdict(manifest, report):
        raise ValueError("measurement verdict does not match retained outcomes")
    return report
