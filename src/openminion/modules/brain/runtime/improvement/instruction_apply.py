from __future__ import annotations

import uuid
from pathlib import Path

from openminion.modules.brain.runtime.improvement.instruction_store import (
    InstructionProposalStore,
)
from openminion.modules.brain.runtime.improvement.instructions import (
    InstructionApprovalRecord,
    InstructionProposal,
    InstructionProposalEvent,
    InstructionRollbackRef,
    InstructionTargetSnapshot,
)
from openminion.modules.runtime.project_instructions import (
    compute_instruction_content_hash,
    read_instruction_target_snapshot,
)


def apply_instruction_proposal(
    store: InstructionProposalStore,
    *,
    approval_id: str,
) -> InstructionProposal:
    approval = _require_approval(store, approval_id)
    proposal = _require_proposal(store, approval.candidate_id)
    review_snapshot = _require_snapshot(store, proposal.candidate_id)
    _validate_approval(approval, proposal)
    _validate_review_snapshot(review_snapshot, proposal)
    if proposal.state in {"rejected", "suppressed", "promoted"}:
        raise ValueError(f"proposal_not_applyable:{proposal.state}")
    if proposal.proposal_kind == "manual_review":
        updated = store.update_state(
            proposal.candidate_id,
            "suppressed",
            reason_code="manual_review_cannot_auto_apply",
        )
        _append_event(
            store,
            "instruction.unsafe_path_blocked",
            candidate_id=proposal.candidate_id,
            target_file=proposal.target_file,
            state=updated.state,
            reason_code="manual_review_cannot_auto_apply",
        )
        return updated

    snapshot = read_instruction_target_snapshot(
        proposal.target_file,
        project_root=review_snapshot.project_root,
    )
    if snapshot.content_hash != approval.target_content_hash:
        updated = store.update_state(
            proposal.candidate_id,
            "suppressed",
            reason_code="stale_target",
        )
        _append_event(
            store,
            "instruction.stale_target_rejected",
            candidate_id=proposal.candidate_id,
            target_file=proposal.target_file,
            state=updated.state,
            reason_code="stale_target",
        )
        return updated

    before_content = snapshot.content
    after_content = _apply_to_content(before_content, proposal)
    after_hash = compute_instruction_content_hash(after_content)
    rollback = InstructionRollbackRef(
        rollback_id=f"rollback-{uuid.uuid4()}",
        candidate_id=proposal.candidate_id,
        target_file=proposal.target_file,
        before_content=before_content,
        before_hash=snapshot.content_hash,
        after_hash=after_hash,
        newline=snapshot.newline,
        encoding=snapshot.encoding,
        mode=snapshot.mode,
    )
    store.put_rollback(rollback)
    _atomic_write(Path(proposal.target_file), after_content, mode=snapshot.mode)
    store.mark_approval_used(approval.approval_id)
    updated = store.update_state(proposal.candidate_id, "promoted")
    _append_event(
        store,
        "instruction.proposal_applied",
        candidate_id=proposal.candidate_id,
        approval_id=approval.approval_id,
        target_file=proposal.target_file,
        state=updated.state,
    )
    return updated


def reject_instruction_proposal(
    store: InstructionProposalStore,
    *,
    candidate_id: str,
    reason_code: str = "operator_rejected",
) -> InstructionProposal:
    updated = store.update_state(candidate_id, "rejected", reason_code=reason_code)
    _append_event(
        store,
        "instruction.proposal_rejected",
        candidate_id=candidate_id,
        target_file=updated.target_file,
        state=updated.state,
        reason_code=reason_code,
    )
    return updated


def rollback_instruction_proposal(
    store: InstructionProposalStore,
    *,
    candidate_id: str,
) -> InstructionProposal:
    proposal = _require_proposal(store, candidate_id)
    rollback = store.get_rollback(candidate_id)
    if rollback is None:
        updated = store.update_state(
            candidate_id,
            "under_review",
            reason_code="rollback_ref_missing",
        )
        _append_event(
            store,
            "instruction.rollback_review_required",
            candidate_id=candidate_id,
            target_file=proposal.target_file,
            state=updated.state,
            reason_code="rollback_ref_missing",
        )
        return updated
    current = read_instruction_target_snapshot(rollback.target_file)
    if current.content_hash != rollback.after_hash:
        updated = store.update_state(
            candidate_id,
            "under_review",
            reason_code="rollback_target_changed",
        )
        _append_event(
            store,
            "instruction.rollback_review_required",
            candidate_id=candidate_id,
            target_file=rollback.target_file,
            state=updated.state,
            reason_code="rollback_target_changed",
        )
        return updated
    _atomic_write(
        Path(rollback.target_file), rollback.before_content, mode=rollback.mode
    )
    updated = store.update_state(candidate_id, "rolled_back")
    _append_event(
        store,
        "instruction.rollback_completed",
        candidate_id=candidate_id,
        target_file=rollback.target_file,
        state=updated.state,
    )
    return updated


def _require_approval(
    store: InstructionProposalStore,
    approval_id: str,
) -> InstructionApprovalRecord:
    approval = store.get_approval(approval_id)
    if approval is None:
        raise KeyError(str(approval_id or "").strip())
    if approval.is_used:
        raise ValueError("approval_already_used")
    return approval


def _require_proposal(
    store: InstructionProposalStore,
    candidate_id: str,
) -> InstructionProposal:
    proposal = store.get_proposal(candidate_id)
    if proposal is None:
        raise KeyError(str(candidate_id or "").strip())
    return proposal


def _require_snapshot(
    store: InstructionProposalStore,
    candidate_id: str,
) -> InstructionTargetSnapshot:
    snapshot = store.get_snapshot(candidate_id)
    if snapshot is None:
        raise KeyError(str(candidate_id or "").strip())
    return snapshot


def _validate_approval(
    approval: InstructionApprovalRecord,
    proposal: InstructionProposal,
) -> None:
    if approval.candidate_id != proposal.candidate_id:
        raise ValueError("approval_candidate_id_mismatch")
    if approval.proposal_hash != proposal.proposal_hash:
        raise ValueError("approval_proposal_hash_mismatch")
    if approval.target_file != proposal.target_file:
        raise ValueError("approval_target_file_mismatch")
    if approval.target_content_hash != proposal.target_content_hash:
        raise ValueError("approval_target_hash_mismatch")


def _validate_review_snapshot(
    snapshot: InstructionTargetSnapshot,
    proposal: InstructionProposal,
) -> None:
    if snapshot.target_file != proposal.target_file:
        raise ValueError("snapshot_target_file_mismatch")
    if snapshot.target_name != proposal.target_name:
        raise ValueError("snapshot_target_name_mismatch")
    if snapshot.content_hash != proposal.target_content_hash:
        raise ValueError("snapshot_target_hash_mismatch")


def _apply_to_content(content: str, proposal: InstructionProposal) -> str:
    if proposal.suggested_patch.strip():
        raise ValueError("structured_patch_apply_not_supported_v1")
    text = proposal.suggested_text
    if proposal.proposal_kind == "append_bullet":
        line = text.strip()
        if not line.startswith("- "):
            line = f"- {line}"
        return _append_block(content, line)
    if proposal.proposal_kind == "append_section":
        return _append_block(content, text.strip())
    if proposal.proposal_kind == "replace_section":
        return text.rstrip() + "\n"
    raise ValueError(f"unsupported_proposal_kind:{proposal.proposal_kind}")


def _append_block(content: str, block: str) -> str:
    prefix = content.rstrip()
    suffix = block.rstrip()
    if not prefix:
        return suffix + "\n"
    return f"{prefix}\n\n{suffix}\n"


def _atomic_write(path: Path, content: str, *, mode: int | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(content, encoding="utf-8")
    if mode is not None:
        tmp.chmod(mode)
    tmp.replace(path)


def _append_event(
    store: InstructionProposalStore,
    event_type: str,
    *,
    candidate_id: str = "",
    approval_id: str = "",
    target_file: str = "",
    state: str = "",
    reason_code: str = "",
) -> None:
    store.append_event(
        InstructionProposalEvent(
            event_id=f"plip-event-{uuid.uuid4()}",
            event_type=event_type,
            candidate_id=candidate_id,
            approval_id=approval_id,
            target_file=target_file,
            state=state,
            reason_code=reason_code,
        )
    )


__all__ = [
    "apply_instruction_proposal",
    "reject_instruction_proposal",
    "rollback_instruction_proposal",
]
