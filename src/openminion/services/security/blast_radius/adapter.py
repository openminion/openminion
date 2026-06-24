from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from openminion.modules.runtime.credentials import CredentialRef
from openminion.modules.tool.runtime.blast_radius import (
    CompositionAuditLog,
    CompositionBoundaryEvent,
    CompositionPolicy,
    ToolBlastRadiusProfile,
    classify_composed_blast_radius,
    classify_tool_blast_radius,
    record_composition_boundary_event,
    requires_composition_approval,
)


@dataclass
class CompositionBoundaryAdapter:
    """One seam's composition-boundary owner."""

    policy: CompositionPolicy
    audit_log: CompositionAuditLog
    seam_id: str
    prior_profiles: list[ToolBlastRadiusProfile] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.policy.frozen:
            raise ValueError(
                f"CompositionPolicy {self.policy.policy_id!r} is not marked frozen; "
                "the adapter requires a frozen-construction policy."
            )
        label = str(self.seam_id or "").strip()
        if not label:
            raise ValueError(
                "seam_id must be a caller-declared static label; "
                "the adapter never synthesizes it."
            )
        self.seam_id = label

    def step(
        self,
        tool_spec: Any,
        *,
        credential_ref: Optional[CredentialRef] = None,
    ) -> tuple[ToolBlastRadiusProfile, bool, CompositionBoundaryEvent]:
        """Run one ``classify → compose → guard → record`` step."""
        # 1. classify
        profile = classify_tool_blast_radius(tool_spec, credential_ref=credential_ref)
        # 2. compose (advances the running prior-profiles list only
        composed_before = classify_composed_blast_radius(self.prior_profiles)
        # 3. guard
        needs_approval = requires_composition_approval(
            self.policy, self.prior_profiles, profile
        )
        # 4. record (audit event flows to the canonical-events stream).
        event = record_composition_boundary_event(
            self.policy,
            self.prior_profiles,
            profile,
            seam_id=self.seam_id,
            audit_log=self.audit_log,
        )
        # Advance running prior profiles only after the audit event is
        self.prior_profiles.append(profile)
        # composed_before is exposed via the returned event's
        _ = composed_before
        return profile, needs_approval, event

    def composed_radius(self) -> str:
        """Return the current composed blast radius for the turn so far."""
        return classify_composed_blast_radius(self.prior_profiles)

    def reset(self) -> None:
        """Reset the running prior-profiles list (e.g. between turns)."""
        self.prior_profiles.clear()


def build_composition_boundary_adapter(
    *,
    policy: CompositionPolicy,
    audit_log: CompositionAuditLog,
    seam_id: str,
    prior_profiles: Sequence[ToolBlastRadiusProfile] = (),
) -> CompositionBoundaryAdapter:
    """Build a typed :class:`CompositionBoundaryAdapter`.

    ``seam_id`` must be a caller-declared static label.
    """
    return CompositionBoundaryAdapter(
        policy=policy,
        audit_log=audit_log,
        seam_id=seam_id,
        prior_profiles=list(prior_profiles),
    )


__all__ = [
    "CompositionBoundaryAdapter",
    "build_composition_boundary_adapter",
]
