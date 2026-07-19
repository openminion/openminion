from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from ..errors import ToolRuntimeError

ExposureTier = Literal["read", "plan", "apply"]
ExposureState = Literal["visible", "hidden", "blocked"]
ExposureReason = Literal[
    "approval_required",
    "credential_missing",
    "dependency_missing",
    "profile_inactive",
    "risk_denied",
    "target_missing",
]


@dataclass(frozen=True)
class ToolRiskAnnotations:
    tier: ExposureTier = "read"
    requires_approval: bool = False
    mutates_state: bool = False

    def __post_init__(self) -> None:
        if self.mutates_state and self.tier == "read":
            raise ToolRuntimeError(
                "INVALID_ARGUMENT", "read-tier tools cannot mutate state"
            )
        if self.tier == "apply" and not self.requires_approval:
            raise ToolRuntimeError(
                "INVALID_ARGUMENT", "apply-tier tools must require approval"
            )


@dataclass(frozen=True)
class ToolCatalogCard:
    profile_id: str
    title: str
    summary: str
    tool_names: tuple[str, ...]
    tier: ExposureTier = "read"
    target_ids: tuple[str, ...] = ()
    expires_at: float | None = None
    evidence_expectations: tuple[str, ...] = ()
    stop_rules: tuple[str, ...] = ()
    guidance_names: tuple[str, ...] = ()
    activation_hint: str = ""


@dataclass(frozen=True)
class ToolExposureProfile:
    profile_id: str
    title: str
    summary: str
    tool_names: frozenset[str]
    risk: ToolRiskAnnotations = field(default_factory=ToolRiskAnnotations)
    target_kinds: frozenset[str] = field(default_factory=frozenset)
    credential_scopes: frozenset[str] = field(default_factory=frozenset)
    dependencies: frozenset[str] = field(default_factory=frozenset)
    evidence_expectations: tuple[str, ...] = ()
    stop_rules: tuple[str, ...] = ()
    guidance_names: tuple[str, ...] = ()
    default_active: bool = False
    activation_hint: str = ""

    def __post_init__(self) -> None:
        profile_id = self.profile_id.strip()
        if not profile_id:
            raise ToolRuntimeError("INVALID_ARGUMENT", "profile_id is required")
        if profile_id != self.profile_id or not all(
            char.isalnum() or char in {"_", "-"} for char in profile_id
        ):
            raise ToolRuntimeError(
                "INVALID_ARGUMENT", "profile_id must be a normalized identifier"
            )
        if not self.title.strip():
            raise ToolRuntimeError("INVALID_ARGUMENT", "profile title is required")
        if not self.tool_names or any(not name.strip() for name in self.tool_names):
            raise ToolRuntimeError(
                "INVALID_ARGUMENT", "profile tool_names cannot be empty"
            )
        if self.default_active and self.risk.tier == "apply":
            raise ToolRuntimeError(
                "INVALID_ARGUMENT",
                "apply-tier profiles cannot be active by default",
            )

    def card(
        self,
        activation: ToolExposureSession | None = None,
    ) -> ToolCatalogCard:
        return ToolCatalogCard(
            profile_id=self.profile_id,
            title=self.title,
            summary=self.summary,
            tool_names=tuple(sorted(self.tool_names)),
            tier=self.risk.tier,
            target_ids=(activation.target_id,)
            if activation and activation.target_id
            else (),
            expires_at=activation.expires_at if activation else None,
            evidence_expectations=self.evidence_expectations,
            stop_rules=self.stop_rules,
            guidance_names=self.guidance_names,
            activation_hint=self.activation_hint,
        )


@dataclass(frozen=True)
class ToolExposureSession:
    profile_id: str
    session_id: str
    task_id: str = ""
    target_id: str = ""
    audit_id: str = ""
    expires_at: float | None = None
    activation_reason: str = ""
    approved_by: str = ""
    policy_source: str = ""


@dataclass(frozen=True)
class ToolExposureDecision:
    tool_name: str
    state: ExposureState
    profile_id: str = ""
    reason_code: ExposureReason | None = None
    activation_id: str = ""
    target_id: str = ""


__all__ = [
    "ExposureReason",
    "ExposureState",
    "ExposureTier",
    "ToolCatalogCard",
    "ToolExposureDecision",
    "ToolExposureProfile",
    "ToolExposureSession",
    "ToolRiskAnnotations",
]
