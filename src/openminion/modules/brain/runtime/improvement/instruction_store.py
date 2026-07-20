from __future__ import annotations

import json
from pathlib import Path
from threading import RLock
from typing import Any
import uuid

from openminion.base.config.paths import resolve_module_storage_path
from openminion.modules.brain.runtime.improvement.candidates import (
    ImprovementCandidate,
    ImprovementCandidateState,
)
from openminion.modules.brain.runtime.improvement.instructions import (
    InstructionApprovalRecord,
    InstructionOpportunity,
    InstructionProposal,
    InstructionProposalEvent,
    InstructionRollbackRef,
    InstructionTargetSnapshot,
)


class InstructionProposalStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve(strict=False)
        self._lock = RLock()

    @classmethod
    def default(cls, *, home_root: str | Path = ".", data_root: str | None = None):
        path = resolve_module_storage_path(
            Path(home_root).expanduser().resolve(strict=False),
            "brain",
            data_root=data_root,
            subdir="improvement",
            filename="instruction_proposals.json",
        )
        return cls(path)

    def stage_opportunity(self, opportunity: InstructionOpportunity) -> None:
        with self._transaction() as payload:
            payload["opportunities"][opportunity.opportunity_id] = (
                opportunity.model_dump(mode="json")
            )

    def stage_proposal(
        self,
        proposal: InstructionProposal,
        *,
        snapshot: InstructionTargetSnapshot,
        candidate: ImprovementCandidate,
    ) -> None:
        if candidate.target_type != "instruction":
            raise ValueError("candidate_target_type_must_be_instruction")
        if candidate.candidate_id != proposal.candidate_id:
            raise ValueError("candidate_id_mismatch")
        with self._transaction() as payload:
            payload["proposals"][proposal.candidate_id] = proposal.model_dump(
                mode="json"
            )
            payload["candidates"][candidate.candidate_id] = candidate.model_dump(
                mode="json"
            )
            payload["target_snapshots"][proposal.candidate_id] = snapshot.model_dump(
                mode="json"
            )

    def stage_instruction_candidate(
        self,
        candidate: ImprovementCandidate,
    ) -> dict[str, Any]:
        if candidate.target_type != "instruction":
            raise ValueError("candidate_target_type_must_be_instruction")
        with self._transaction() as payload:
            payload["candidates"][candidate.candidate_id] = candidate.model_dump(
                mode="json"
            )
            proposal_exists = candidate.candidate_id in payload["proposals"]
        return {
            "candidate_id": candidate.candidate_id,
            "proposal_staged": proposal_exists,
        }

    def approve(self, approval: InstructionApprovalRecord) -> None:
        with self._transaction() as payload:
            proposal = self._proposal_from(payload, approval.candidate_id)
            if proposal.proposal_hash != approval.proposal_hash:
                raise ValueError("approval_proposal_hash_mismatch")
            if proposal.target_file != approval.target_file:
                raise ValueError("approval_target_file_mismatch")
            if proposal.target_content_hash != approval.target_content_hash:
                raise ValueError("approval_target_hash_mismatch")
            payload["approvals"][approval.approval_id] = approval.model_dump(
                mode="json"
            )

    def get_opportunity(self, opportunity_id: str) -> InstructionOpportunity | None:
        payload = self._read_payload()
        data = payload["opportunities"].get(str(opportunity_id or "").strip())
        return InstructionOpportunity.model_validate(data) if data else None

    def get_proposal(self, candidate_id: str) -> InstructionProposal | None:
        payload = self._read_payload()
        data = payload["proposals"].get(str(candidate_id or "").strip())
        return InstructionProposal.model_validate(data) if data else None

    def get_candidate(self, candidate_id: str) -> ImprovementCandidate | None:
        payload = self._read_payload()
        data = payload["candidates"].get(str(candidate_id or "").strip())
        return ImprovementCandidate.model_validate(data) if data else None

    def get_approval(self, approval_id: str) -> InstructionApprovalRecord | None:
        payload = self._read_payload()
        data = payload["approvals"].get(str(approval_id or "").strip())
        return InstructionApprovalRecord.model_validate(data) if data else None

    def get_snapshot(self, candidate_id: str) -> InstructionTargetSnapshot | None:
        payload = self._read_payload()
        data = payload["target_snapshots"].get(str(candidate_id or "").strip())
        return InstructionTargetSnapshot.model_validate(data) if data else None

    def get_rollback(self, candidate_id: str) -> InstructionRollbackRef | None:
        payload = self._read_payload()
        data = payload["rollback_refs"].get(str(candidate_id or "").strip())
        return InstructionRollbackRef.model_validate(data) if data else None

    def list_proposals(self) -> list[InstructionProposal]:
        payload = self._read_payload()
        proposals = [
            InstructionProposal.model_validate(item)
            for item in payload["proposals"].values()
        ]
        return sorted(proposals, key=lambda item: (item.created_at, item.candidate_id))

    def update_state(
        self,
        candidate_id: str,
        state: ImprovementCandidateState,
        *,
        reason_code: str = "",
    ) -> InstructionProposal:
        with self._transaction() as payload:
            proposal = self._proposal_from(payload, candidate_id).transition(state)
            candidate = self._candidate_from(payload, candidate_id).transition(state)
            payload["proposals"][candidate_id] = proposal.model_dump(mode="json")
            payload["candidates"][candidate_id] = candidate.model_dump(mode="json")
            if reason_code:
                payload["state_reasons"][candidate_id] = str(reason_code)
            return proposal

    def mark_approval_used(self, approval_id: str) -> InstructionApprovalRecord:
        with self._transaction() as payload:
            approval = self._approval_from(payload, approval_id)
            if approval.is_used:
                raise ValueError("approval_already_used")
            updated = approval.mark_used()
            payload["approvals"][approval_id] = updated.model_dump(mode="json")
            return updated

    def put_rollback(self, rollback: InstructionRollbackRef) -> None:
        with self._transaction() as payload:
            payload["rollback_refs"][rollback.candidate_id] = rollback.model_dump(
                mode="json"
            )

    def append_event(self, event: InstructionProposalEvent) -> None:
        with self._transaction() as payload:
            payload["events"].append(event.model_dump(mode="json"))

    def list_events(self) -> list[InstructionProposalEvent]:
        payload = self._read_payload()
        return [
            InstructionProposalEvent.model_validate(item) for item in payload["events"]
        ]

    def _proposal_from(
        self, payload: dict[str, Any], candidate_id: str
    ) -> InstructionProposal:
        data = payload["proposals"].get(str(candidate_id or "").strip())
        if not data:
            raise KeyError(str(candidate_id or "").strip())
        return InstructionProposal.model_validate(data)

    def _candidate_from(
        self, payload: dict[str, Any], candidate_id: str
    ) -> ImprovementCandidate:
        data = payload["candidates"].get(str(candidate_id or "").strip())
        if not data:
            raise KeyError(str(candidate_id or "").strip())
        return ImprovementCandidate.model_validate(data)

    def _approval_from(
        self, payload: dict[str, Any], approval_id: str
    ) -> InstructionApprovalRecord:
        data = payload["approvals"].get(str(approval_id or "").strip())
        if not data:
            raise KeyError(str(approval_id or "").strip())
        return InstructionApprovalRecord.model_validate(data)

    def _read_payload(self) -> dict[str, Any]:
        with self._lock:
            if not self.path.exists():
                return _empty_payload()
            raw = self.path.read_text(encoding="utf-8")
            if not raw.strip():
                return _empty_payload()
            payload = json.loads(raw)
            return _normalize_payload(payload)

    def _write_payload(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        tmp.replace(self.path)

    def _transaction(self) -> "_InstructionStoreTransaction":
        return _InstructionStoreTransaction(self)


class _InstructionStoreTransaction:
    def __init__(self, store: InstructionProposalStore) -> None:
        self._store = store
        self._payload: dict[str, Any] | None = None

    def __enter__(self) -> dict[str, Any]:
        self._store._lock.acquire()
        self._payload = self._store._read_payload()
        return self._payload

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if exc_type is None and self._payload is not None:
                self._store._write_payload(self._payload)
        finally:
            self._store._lock.release()


def _empty_payload() -> dict[str, Any]:
    return {
        "schema_version": "instruction_proposal_store.v1",
        "opportunities": {},
        "proposals": {},
        "candidates": {},
        "approvals": {},
        "target_snapshots": {},
        "rollback_refs": {},
        "events": [],
        "state_reasons": {},
    }


def _normalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = _empty_payload()
    for key in normalized:
        if key in payload and isinstance(payload[key], type(normalized[key])):
            normalized[key] = payload[key]
    return normalized


__all__ = ["InstructionProposalStore"]
