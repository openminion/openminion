"""Typed contracts for the action approval verifier."""

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


ApprovalDecision = Literal["approve", "reject", "escalate"]


@dataclass(frozen=True)
class ApprovalVerdict:
    """Typed pre-action approval verdict."""

    decision: ApprovalDecision
    rationale: str
    model: str = ""
    latency_ms: int = 0


@dataclass(frozen=True)
class ApprovalCriteria:
    """Per-tool-family approval criteria."""

    tool_id: str
    action: str
    criteria_text: str
    metadata: dict[str, Any] = field(default_factory=dict)


class ActionApprovalVerifier(Protocol):
    """Protocol for a cheap-model approval verifier."""

    def verify(
        self,
        *,
        action: dict[str, Any],
        state: dict[str, Any],
        criteria: ApprovalCriteria,
    ) -> ApprovalVerdict:  # pragma: no cover - Protocol
        ...


__all__ = [
    "ActionApprovalVerifier",
    "ApprovalCriteria",
    "ApprovalDecision",
    "ApprovalVerdict",
]
