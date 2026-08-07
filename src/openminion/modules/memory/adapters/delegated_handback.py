"""Typed child proposal handback with parent-owned candidate persistence."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import uuid4

from openminion.modules.memory.adapters.contracts import DelegatedRunContextView
from sophiagraph.models import (
    DelegatedCandidateProvenance,
    MemoryCandidate,
    MemoryNamespace,
    MemoryType,
)


class CandidateSubmissionStore(Protocol):
    def put_candidate(self, candidate: MemoryCandidate) -> str: ...


@dataclass(frozen=True, slots=True)
class DelegatedMemoryProposal:
    """Structured proposal returned by a child without storage authority."""

    content: dict[str, Any] | str
    type: MemoryType
    namespace: MemoryNamespace
    workspace_id: str
    proposed_scope: str
    source_record_ids: tuple[str, ...] = ()
    title: str | None = None
    key: str | None = None
    tags: tuple[str, ...] = ()
    confidence: float = 0.5
    metadata: dict[str, Any] = field(default_factory=dict)


def submit_delegated_memory_proposal(
    store: CandidateSubmissionStore,
    proposal: DelegatedMemoryProposal,
    *,
    run_context: DelegatedRunContextView,
    submitting_parent_agent_id: str,
) -> str:
    """Persist a child proposal only when the authoritative parent submits it."""

    if run_context.memory_posture != "read_only_bounded":
        raise PermissionError("delegated proposal requires bounded memory posture")
    if run_context.cancelled:
        raise PermissionError("cancelled delegated runs cannot submit proposals")
    if submitting_parent_agent_id != run_context.parent_agent_id:
        raise PermissionError("only the parent agent may submit a child proposal")
    grant_id = run_context.memory_grant_id
    if not grant_id:
        raise PermissionError("delegated proposal requires grant provenance")
    provenance = DelegatedCandidateProvenance(
        parent_agent_id=run_context.parent_agent_id,
        child_agent_id=run_context.child_agent_id,
        parent_run_id=run_context.parent_run_id,
        child_run_id=run_context.child_run_id,
        trace_parent_id=run_context.trace_parent_id,
        grant_id=grant_id,
        workspace_id=proposal.workspace_id,
        namespace=proposal.namespace,
        source_record_ids=proposal.source_record_ids,
    )
    candidate = MemoryCandidate(
        candidate_id=f"delegated-candidate-{uuid4().hex}",
        session_id=run_context.parent_run_id,
        proposed_scope=proposal.proposed_scope,
        type=proposal.type,
        content=proposal.content,
        tags=list(proposal.tags),
        confidence=proposal.confidence,
        key=proposal.key,
        title=proposal.title,
        meta=dict(proposal.metadata),
        namespace=proposal.namespace,
        delegation_provenance=provenance,
    )
    return store.put_candidate(candidate)


__all__ = [
    "CandidateSubmissionStore",
    "DelegatedMemoryProposal",
    "submit_delegated_memory_proposal",
]
