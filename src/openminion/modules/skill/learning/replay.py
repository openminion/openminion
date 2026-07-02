"""Replay/eval proof gates for learned-skill proposals."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from openminion.modules.skill.proposal import queue as proposal_queue
from openminion.modules.skill.proposal.catalog import EmergentSkillCatalogAddition
from openminion.modules.skill.storage.base import SkillStore


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


def apply_proposal_with_replay(
    store: SkillStore,
    *,
    proposal_id: str,
    current_catalog: Iterable[object],
    replay_proof: ReplayProof,
) -> EmergentSkillCatalogAddition:
    """Apply a proposal only after accepted review and passing replay proof."""

    if replay_proof.proposal_id != proposal_id:
        raise ReplayGateError("replay_proof_proposal_mismatch")
    require_replay_passed(replay_proof)
    return proposal_queue.apply_proposal(
        store,
        proposal_id=proposal_id,
        current_catalog=current_catalog,
    )


__all__ = (
    "ReplayGateError",
    "ReplayProof",
    "ReplayStatus",
    "apply_proposal_with_replay",
    "require_replay_passed",
)
