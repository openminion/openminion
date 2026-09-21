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


class ReplayEvaluationResult(BaseModel):
    """Content-addressed evaluator result retained by the artifact owner."""

    model_config = ConfigDict(extra="forbid")

    proof_id: str
    proposal_id: str
    shape_id: str
    candidate_hash: str
    evaluator_id: str
    status: ReplayStatus
    command: str = ""
    evidence_refs: list[str] = Field(default_factory=list)
    summary: str = ""

    def to_proof(self, *, result_ref: str) -> ReplayProof:
        return ReplayProof(
            **self.model_dump(mode="python", exclude={"evidence_refs"}),
            result_ref=result_ref,
            evidence_refs=list(dict.fromkeys([result_ref, *self.evidence_refs])),
        )


class ReplayGateError(ValueError):
    """Raised when replay/eval proof blocks a learned-skill action."""


def proposal_draft_hash(proposal: SkillProposal) -> str:
    return str(stable_hash(proposal.proposed_skill_definition.model_dump(mode="json")))


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
    replay_proof: ReplayProof | None = None,
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
    "ReplayEvaluationResult",
    "ReplayProof",
    "ReplayStatus",
    "apply_proposal_with_replay",
    "proposal_draft_hash",
    "require_replay_passed",
)
