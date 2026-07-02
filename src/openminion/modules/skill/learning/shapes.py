"""Typed contracts for workflow evidence, shapes, and execution trust."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from openminion.modules.skill.models import normalize_text_list, slugify, stable_hash

WorkflowOutcome = Literal["success", "partial", "failure"]
WorkflowRisk = Literal["low", "medium", "high"]
WorkflowRedactionStatus = Literal["redacted", "no_sensitive_fields", "unsafe"]
WorkflowTrustState = Literal[
    "candidate",
    "pending_review",
    "catalog_applied",
    "suggest_only",
    "trusted_for_manual",
    "trusted_for_low_risk",
    "execution_downgraded",
    "catalog_review_required",
]

_SECRET_MARKERS = (
    "api_key",
    "apikey",
    "token",
    "secret",
    "password",
    "authorization",
)


def normalize_category(owner: str, value: str) -> str:
    """Normalize owner-scoped category values without collapsing vocabularies."""

    owner_slug = slugify(owner, fallback="owner")
    value_slug = slugify(value, fallback="unknown")
    return f"{owner_slug}:{value_slug}"


def _unique_sorted(values: Iterable[str]) -> list[str]:
    return sorted(normalize_text_list([str(item) for item in values]))


def _redact_token(token: str) -> str:
    text = str(token or "")
    lowered = text.lower()
    if any(marker in lowered for marker in _SECRET_MARKERS):
        return "<redacted-secret>"
    if "=" in text:
        key, _sep, value = text.partition("=")
        if any(marker in key.lower() for marker in _SECRET_MARKERS):
            return f"{key}=<redacted-secret>"
        if value.startswith(("/", "~")):
            return f"{key}=<path>"
    if text.startswith(("/", "~")):
        return "<path>"
    return text


def command_fingerprint(command: tuple[str, ...] | list[str] | str) -> str:
    """Build a stable command-family fingerprint with secrets and paths removed."""

    if isinstance(command, str):
        parts = tuple(part for part in command.split() if part)
    else:
        parts = tuple(str(part) for part in command)
    redacted = tuple(_redact_token(part) for part in parts)
    return stable_hash({"command": redacted})[:16]


class WorkflowEvidenceBundle(BaseModel):
    """Redacted evidence extracted from one completed or corrected workflow run."""

    model_config = ConfigDict(extra="forbid")

    bundle_id: str = ""
    source_run_refs: list[str] = Field(default_factory=list)
    proof_packet_refs: list[str] = Field(default_factory=list)
    skill_run_refs: list[str] = Field(default_factory=list)
    strategy_outcome_refs: list[str] = Field(default_factory=list)
    user_correction_refs: list[str] = Field(default_factory=list)
    replay_refs: list[str] = Field(default_factory=list)
    tool_names: list[str] = Field(default_factory=list)
    command_fingerprints: list[str] = Field(default_factory=list)
    test_fingerprints: list[str] = Field(default_factory=list)
    artifact_types: list[str] = Field(default_factory=list)
    validation_summary: str = ""
    outcome: WorkflowOutcome
    redaction_status: WorkflowRedactionStatus = "redacted"
    risk_flags: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    provenance_checksum: str = ""
    intent_category: str
    capability_category: str
    strategy_id: str
    explicit_save: bool = False
    actor_id: str = ""
    observed_at: str = ""

    @field_validator(
        "source_run_refs",
        "proof_packet_refs",
        "skill_run_refs",
        "strategy_outcome_refs",
        "user_correction_refs",
        "replay_refs",
        "tool_names",
        "command_fingerprints",
        "test_fingerprints",
        "artifact_types",
        "risk_flags",
        "evidence_refs",
        mode="before",
    )
    @classmethod
    def _normalize_lists(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, tuple):
            value = list(value)
        if not isinstance(value, list):
            return []
        return _unique_sorted(str(item) for item in value)

    @model_validator(mode="after")
    def _fill_ids(self) -> "WorkflowEvidenceBundle":
        checksum_payload = {
            "source_run_refs": self.source_run_refs,
            "proof_packet_refs": self.proof_packet_refs,
            "skill_run_refs": self.skill_run_refs,
            "strategy_outcome_refs": self.strategy_outcome_refs,
            "user_correction_refs": self.user_correction_refs,
            "replay_refs": self.replay_refs,
            "tool_names": self.tool_names,
            "command_fingerprints": self.command_fingerprints,
            "test_fingerprints": self.test_fingerprints,
            "artifact_types": self.artifact_types,
            "outcome": self.outcome,
            "intent_category": self.intent_category,
            "capability_category": self.capability_category,
            "strategy_id": self.strategy_id,
            "explicit_save": self.explicit_save,
        }
        checksum = stable_hash(checksum_payload)
        bundle_id = self.bundle_id or f"wlev-{checksum[:16]}"
        evidence_refs = self.evidence_refs or [bundle_id]
        object.__setattr__(self, "bundle_id", bundle_id)
        object.__setattr__(self, "evidence_refs", evidence_refs)
        object.__setattr__(
            self,
            "provenance_checksum",
            self.provenance_checksum or checksum,
        )
        return self


class WorkflowShape(BaseModel):
    """A recurring structural workflow pattern, not a catalog skill."""

    model_config = ConfigDict(extra="forbid")

    shape_id: str = ""
    task_shape_ref: str = ""
    intent_category: str
    capability_category: str
    strategy_id: str
    tool_names: list[str] = Field(default_factory=list)
    command_fingerprints: list[str] = Field(default_factory=list)
    test_fingerprints: list[str] = Field(default_factory=list)
    artifact_types: list[str] = Field(default_factory=list)
    success_count: int = Field(default=0, ge=0)
    partial_count: int = Field(default=0, ge=0)
    failure_count: int = Field(default=0, ge=0)
    evidence_refs: list[str] = Field(default_factory=list)
    performance_entry_refs: list[str] = Field(default_factory=list)
    failure_pattern_refs: list[str] = Field(default_factory=list)
    knowledge_record_refs: list[str] = Field(default_factory=list)
    risk_level: WorkflowRisk = "low"
    first_seen_at: str = ""
    last_seen_at: str = ""
    explicit_save_count: int = Field(default=0, ge=0)

    @field_validator(
        "tool_names",
        "command_fingerprints",
        "test_fingerprints",
        "artifact_types",
        "evidence_refs",
        "performance_entry_refs",
        "failure_pattern_refs",
        "knowledge_record_refs",
        mode="before",
    )
    @classmethod
    def _normalize_lists(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, tuple):
            value = list(value)
        if not isinstance(value, list):
            return []
        return _unique_sorted(str(item) for item in value)

    @model_validator(mode="after")
    def _fill_shape_refs(self) -> "WorkflowShape":
        structural_payload = {
            "intent_category": self.intent_category,
            "capability_category": self.capability_category,
            "strategy_id": self.strategy_id,
            "tool_names": self.tool_names,
            "command_fingerprints": self.command_fingerprints,
            "test_fingerprints": self.test_fingerprints,
            "artifact_types": self.artifact_types,
        }
        digest = stable_hash(structural_payload)[:16]
        shape_id = self.shape_id or f"wlsh-{digest}"
        task_shape_ref = self.task_shape_ref or f"workflow_shape:{shape_id}"
        object.__setattr__(self, "shape_id", shape_id)
        object.__setattr__(self, "task_shape_ref", task_shape_ref)
        return self


__all__ = (
    "WorkflowEvidenceBundle",
    "WorkflowOutcome",
    "WorkflowRedactionStatus",
    "WorkflowRisk",
    "WorkflowShape",
    "WorkflowTrustState",
    "command_fingerprint",
    "normalize_category",
)
