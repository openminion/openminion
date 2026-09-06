"""Replay/eval proof gates for learned-skill proposals."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ReplayStatus = Literal["passed", "failed", "blocked", "skipped"]


class ReplayProof(BaseModel):
    """Deterministic proof attached before apply or trust promotion."""

    model_config = ConfigDict(extra="forbid")

    proof_id: str
    proposal_id: str
    shape_id: str
    status: ReplayStatus
    command: str = ""
    evidence_refs: list[str] = Field(default_factory=list)
    summary: str = ""

    @property
    def passed(self) -> bool:
        return self.status == "passed"


class ReplayGateError(ValueError):
    """Raised when replay/eval proof blocks a learned-skill action."""


def require_replay_passed(proof: ReplayProof) -> None:
    if not proof.passed:
        raise ReplayGateError(f"replay_proof_not_passed:{proof.status}")


__all__ = (
    "ReplayGateError",
    "ReplayProof",
    "ReplayStatus",
    "require_replay_passed",
)
