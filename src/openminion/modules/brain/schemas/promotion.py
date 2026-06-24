"""Brain schema models for promotion decisions."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from openminion.modules.brain.schemas.missions import MissionType

if TYPE_CHECKING:
    from openminion.modules.brain.runtime.verification.policy import VerifierResult


def _resolve_verifier_result_class() -> type:
    """Lazy import of TGCR ``VerifierResult`` to break the import cycle."""

    from openminion.modules.brain.runtime.verification.policy import (
        VerifierResult as _VerifierResult,
    )

    return _VerifierResult


PromotionCandidateOutcome = Literal[
    "rejected",
    "advisory_pattern_only",
    "promoted_to_catalog",
]


CatalogRetirementSignal = Literal[
    "stale",
    "superseded_by",
    "failing_outcome",
    "operator_decommissioned",
]


class RepetitionShape(BaseModel):
    """Typed projection of an RTSE ``RecurringTaskShape`` row."""

    model_config = ConfigDict(extra="forbid")

    recurring_task_shape_ref: str = Field(min_length=1)
    strategy_id: str = Field(min_length=1)
    capability_category: str = Field(min_length=1)
    intent_category: str = Field(min_length=1)
    recurrence_count: int = Field(ge=0)
    performance_entry_refs: list[str] = Field(default_factory=list)
    failure_pattern_refs: list[str] = Field(default_factory=list)

    @field_validator(
        "recurring_task_shape_ref",
        "strategy_id",
        "capability_category",
        "intent_category",
        mode="before",
    )
    @classmethod
    def _strip_required_text(cls, value: Any) -> str:
        return str(value or "").strip()


def repetition_shape_from_recurring_task_shape(
    recurring_task_shape: Any,
) -> RepetitionShape:
    """Project an RTSE ``RecurringTaskShape`` into a typed ``RepetitionShape``."""

    def _field(name: str) -> Any:
        if isinstance(recurring_task_shape, dict):
            return recurring_task_shape.get(name)
        return getattr(recurring_task_shape, name, None)

    return RepetitionShape(
        recurring_task_shape_ref=str(_field("task_shape_ref") or "").strip(),
        strategy_id=str(_field("strategy_id") or "").strip(),
        capability_category=str(_field("capability_category") or "").strip(),
        intent_category=str(_field("intent_category") or "").strip(),
        recurrence_count=int(_field("recurrence_count") or 0),
        performance_entry_refs=list(_field("performance_entry_refs") or []),
        failure_pattern_refs=list(_field("failure_pattern_refs") or []),
    )


class ContradictoryFailureBlockConfig(BaseModel):
    """Operator-declared structural rule for contradictory-failure blocking."""

    model_config = ConfigDict(extra="forbid")

    blocking_signatures: list[str] = Field(default_factory=list)
    min_contradicting_failures: int = Field(default=1, ge=1)


class ContradictoryFailureBlock(BaseModel):
    """Typed structural block emitted instead of a ``PromotionCandidate``."""

    model_config = ConfigDict(extra="forbid")

    repetition_shape: RepetitionShape
    intersecting_signatures: list[str] = Field(min_length=1)
    block_policy_id: str = Field(min_length=1)


class PromotionCandidate(BaseModel):
    """Typed promotion candidate."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(min_length=1)
    repetition_shape: RepetitionShape
    proposal_ref: str | None = None
    mission_type: MissionType | None = None
    positive_outcome_refs: list[str] = Field(min_length=1)
    proposer_policy_id: str = Field(min_length=1)
    proposed_at: str = Field(min_length=1)

    @field_validator(
        "candidate_id",
        "proposer_policy_id",
        "proposed_at",
        mode="before",
    )
    @classmethod
    def _strip_required_text(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("proposal_ref", mode="before")
    @classmethod
    def _strip_optional_text(cls, value: Any) -> str | None:
        text = str(value or "").strip()
        return text or None


class PromotionCandidateOutcomeRecord(BaseModel):
    """Typed outcome record for one ``PromotionCandidate``."""

    model_config = ConfigDict(extra="forbid")

    candidate_ref: str = Field(min_length=1)
    outcome: PromotionCandidateOutcome
    review_ref: str = Field(min_length=1)
    decided_at: str = Field(min_length=1)
    catalog_entry_id: str | None = None

    @field_validator(
        "candidate_ref",
        "review_ref",
        "decided_at",
        mode="before",
    )
    @classmethod
    def _strip_required_text(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("catalog_entry_id", mode="before")
    @classmethod
    def _strip_optional_text(cls, value: Any) -> str | None:
        text = str(value or "").strip()
        return text or None

    @model_validator(mode="after")
    def _validate_catalog_entry_pairing(self) -> "PromotionCandidateOutcomeRecord":
        if self.outcome == "promoted_to_catalog" and not self.catalog_entry_id:
            raise ValueError(
                "PromotionCandidateOutcomeRecord with outcome="
                "'promoted_to_catalog' requires catalog_entry_id (the SECA "
                "write target)."
            )
        if self.outcome != "promoted_to_catalog" and self.catalog_entry_id:
            raise ValueError(
                "PromotionCandidateOutcomeRecord.catalog_entry_id is only "
                "valid for outcome='promoted_to_catalog'."
            )
        return self


class StaleRetirementThreshold(BaseModel):
    """Operator-declared typed threshold for ``CatalogRetirementSignal.stale``."""

    model_config = ConfigDict(extra="forbid")

    unused_for_days: int = Field(ge=0)


class CatalogPerformanceRecord(BaseModel):
    """Typed catalog-performance record keyed to a catalog entry id."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    catalog_entry_id: str = Field(min_length=1)
    verifier_results: list[Any] = Field(default_factory=list)
    success_count: int = Field(ge=0)
    failure_count: int = Field(ge=0)
    last_invoked_at: str = Field(min_length=1)

    @field_validator(
        "catalog_entry_id",
        "last_invoked_at",
        mode="before",
    )
    @classmethod
    def _strip_required_text(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("verifier_results", mode="before")
    @classmethod
    def _validate_verifier_results_typed(cls, value: Any) -> list[Any]:
        """Require TGCR `VerifierResult` instances verbatim."""

        if value is None:
            return []
        items = list(value)
        verifier_result_cls = _resolve_verifier_result_class()
        for item in items:
            if not isinstance(item, verifier_result_cls):
                raise ValueError(
                    "CatalogPerformanceRecord.verifier_results elements must "
                    "be TGCR VerifierResult instances (no prose, no dicts)."
                )
        return items

    @model_validator(mode="after")
    def _validate_counts_match_results(self) -> "CatalogPerformanceRecord":
        if self.verifier_results:
            tallied_success = sum(1 for r in self.verifier_results if r.passed)
            tallied_failure = sum(1 for r in self.verifier_results if not r.passed)
            if (
                tallied_success != self.success_count
                or tallied_failure != self.failure_count
            ):
                raise ValueError(
                    "CatalogPerformanceRecord success_count / failure_count "
                    "must match the structural tally of verifier_results."
                )
        return self


class CatalogRetirementRecord(BaseModel):
    """Typed retirement signal emission for one catalog entry."""

    model_config = ConfigDict(extra="forbid")

    catalog_entry_id: str = Field(min_length=1)
    signal: CatalogRetirementSignal
    superseded_by: str | None = None
    stale_threshold: StaleRetirementThreshold | None = None
    operator_decision_ref: str | None = None
    failing_performance_ref: str | None = None
    recorded_at: str = Field(min_length=1)

    @field_validator(
        "catalog_entry_id",
        "recorded_at",
        mode="before",
    )
    @classmethod
    def _strip_required_text(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator(
        "superseded_by",
        "operator_decision_ref",
        "failing_performance_ref",
        mode="before",
    )
    @classmethod
    def _strip_optional_text(cls, value: Any) -> str | None:
        text = str(value or "").strip()
        return text or None

    @model_validator(mode="after")
    def _validate_signal_pairing(self) -> "CatalogRetirementRecord":
        if self.signal == "superseded_by" and not self.superseded_by:
            raise ValueError(
                "CatalogRetirementRecord with signal='superseded_by' "
                "requires the superseded_by catalog entry id."
            )
        if self.signal != "superseded_by" and self.superseded_by:
            raise ValueError(
                "CatalogRetirementRecord.superseded_by is only valid for "
                "signal='superseded_by'."
            )
        if self.signal == "stale" and self.stale_threshold is None:
            raise ValueError(
                "CatalogRetirementRecord with signal='stale' requires "
                "the operator-declared StaleRetirementThreshold."
            )
        if self.signal == "operator_decommissioned" and not self.operator_decision_ref:
            raise ValueError(
                "CatalogRetirementRecord with signal='operator_decommissioned' "
                "requires operator_decision_ref."
            )
        if self.signal == "failing_outcome" and not self.failing_performance_ref:
            raise ValueError(
                "CatalogRetirementRecord with signal='failing_outcome' "
                "requires failing_performance_ref."
            )
        return self


def _candidate_id_from_signature(
    repetition_shape: RepetitionShape,
    *,
    proposer_policy_id: str,
) -> str:
    """Structural hash over the repetition signature plus operator-pin."""

    signature = "|".join(
        (
            repetition_shape.strategy_id,
            repetition_shape.capability_category,
            repetition_shape.intent_category,
            repetition_shape.recurring_task_shape_ref,
            str(proposer_policy_id or "").strip(),
        )
    )
    digest = hashlib.sha256(signature.encode("utf-8")).hexdigest()
    return f"promotion_candidate:{digest[:32]}"


def _signature_intersection(
    repetition_shape: RepetitionShape,
    block_config: ContradictoryFailureBlockConfig,
) -> list[str]:
    """Intersect failure refs with operator-declared blocking signatures."""

    blocking = set(block_config.blocking_signatures)
    return sorted(
        ref for ref in repetition_shape.failure_pattern_refs if ref in blocking
    )


def compose_promotion_candidate(
    *,
    repetition_shape: RepetitionShape,
    positive_outcome_refs: list[str],
    proposer_policy_id: str,
    proposed_at: str,
    block_config: ContradictoryFailureBlockConfig,
    proposal_ref: str | None = None,
    mission_type: MissionType | None = None,
) -> PromotionCandidate | ContradictoryFailureBlock:
    """Compose a typed promotion candidate or contradictory-failure block."""

    intersecting = _signature_intersection(repetition_shape, block_config)
    if len(intersecting) >= block_config.min_contradicting_failures:
        return ContradictoryFailureBlock(
            repetition_shape=repetition_shape,
            intersecting_signatures=intersecting,
            block_policy_id=str(proposer_policy_id or "").strip(),
        )

    candidate_id = _candidate_id_from_signature(
        repetition_shape, proposer_policy_id=proposer_policy_id
    )
    return PromotionCandidate(
        candidate_id=candidate_id,
        repetition_shape=repetition_shape,
        proposal_ref=proposal_ref,
        mission_type=mission_type,
        positive_outcome_refs=list(positive_outcome_refs),
        proposer_policy_id=proposer_policy_id,
        proposed_at=proposed_at,
    )


def compose_catalog_performance_record(
    *,
    catalog_entry_id: str,
    verifier_results: list["VerifierResult"],
    last_invoked_at: str,
) -> CatalogPerformanceRecord:
    """Compose a typed ``CatalogPerformanceRecord`` from typed
    ``VerifierResult`` rows. Counts are derived structurally."""

    success_count = sum(1 for result in verifier_results if result.passed)
    failure_count = sum(1 for result in verifier_results if not result.passed)
    return CatalogPerformanceRecord(
        catalog_entry_id=catalog_entry_id,
        verifier_results=list(verifier_results),
        success_count=success_count,
        failure_count=failure_count,
        last_invoked_at=last_invoked_at,
    )


__all__ = [
    "CatalogPerformanceRecord",
    "CatalogRetirementRecord",
    "CatalogRetirementSignal",
    "ContradictoryFailureBlock",
    "ContradictoryFailureBlockConfig",
    "PromotionCandidate",
    "PromotionCandidateOutcome",
    "PromotionCandidateOutcomeRecord",
    "RepetitionShape",
    "StaleRetirementThreshold",
    "compose_catalog_performance_record",
    "compose_promotion_candidate",
    "repetition_shape_from_recurring_task_shape",
]
