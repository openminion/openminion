"""Replay/eval proof gates for learned-skill proposals."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from openminion.modules.skill.proposal import queue as proposal_queue
from openminion.modules.skill.proposal.base import SkillProposal
from openminion.modules.skill.proposal.catalog import EmergentSkillCatalogAddition
from openminion.modules.skill.models import stable_hash
from openminion.modules.skill.storage.base import SkillStore


ReplayStatus = Literal["passed", "failed", "blocked", "skipped"]


class ReplayProof(BaseModel):
    """Deterministic proof attached before apply or trust promotion."""

    model_config = ConfigDict(extra="forbid")

    proof_id: str
    proposal_id: str
    shape_id: str
    candidate_hash: str
    evaluator_id: str
    result_ref: str
    status: ReplayStatus
    command: str = ""
    evidence_refs: list[str] = Field(default_factory=list)
    summary: str = ""

    @property
    def passed(self) -> bool:
        return self.status == "passed"


class ReplayGateError(ValueError):
    """Raised when replay/eval proof blocks a learned-skill action."""


def proposal_draft_hash(proposal: SkillProposal) -> str:
    return stable_hash(proposal.proposed_skill_definition.model_dump(mode="json"))


def require_replay_passed(proof: ReplayProof, proposal: SkillProposal) -> None:
    if proof.proposal_id != proposal.proposal_id:
        raise ReplayGateError("replay_proof_proposal_mismatch")
    if proof.shape_id != proposal.source_task_shape_ref:
        raise ReplayGateError("replay_proof_shape_mismatch")
    if proof.candidate_hash != proposal_draft_hash(proposal):
        raise ReplayGateError("replay_proof_candidate_mismatch")
    if proof.evaluator_id.strip().lower() in {
        "",
        "runtime",
        "system",
        "auto",
        "automatic",
        "self",
    }:
        raise ReplayGateError("replay_proof_evaluator_required")
    if not proof.result_ref.strip() or proof.result_ref not in proof.evidence_refs:
        raise ReplayGateError("replay_proof_result_required")
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

    return proposal_queue.apply_proposal(
        store,
        proposal_id=proposal_id,
        current_catalog=current_catalog,
        replay_proof=replay_proof,
    )


__all__ = (
    "ReplayGateError",
    "ReplayProof",
    "ReplayStatus",
    "apply_proposal_with_replay",
    "proposal_draft_hash",
    "require_replay_passed",
)
