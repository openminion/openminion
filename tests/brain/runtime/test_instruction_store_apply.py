from __future__ import annotations

import json
from pathlib import Path

import pytest

from openminion.modules.brain.runtime.improvement.candidates import (
    ImprovementCandidate,
    stage_candidate_with_default_owners,
)
from openminion.modules.brain.runtime.improvement.instruction_apply import (
    apply_instruction_proposal,
    reject_instruction_proposal,
    rollback_instruction_proposal,
)
from openminion.modules.brain.runtime.improvement.instruction_store import (
    InstructionProposalStore,
)
from openminion.modules.brain.runtime.improvement.instructions import (
    InstructionApprovalRecord,
    InstructionTargetSnapshot,
    build_instruction_proposal,
)
from openminion.modules.runtime.project_instructions import (
    resolve_project_instruction_target,
)


def _stage(
    tmp_path: Path,
    *,
    candidate_id: str = "cand-1",
    proposal_kind: str = "append_bullet",
    text: str = "Run the focused test.",
) -> tuple[InstructionProposalStore, str]:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    (root / "OPENMINION.md").write_text("# Repo\n", encoding="utf-8")
    target = resolve_project_instruction_target(root)
    store = InstructionProposalStore(tmp_path / "store.json")
    proposal = build_instruction_proposal(
        candidate_id=candidate_id,
        target_file=str(target.path),
        target_name=target.target_name,
        proposal_kind=proposal_kind,  # type: ignore[arg-type]
        summary="Add testing guidance",
        evidence_refs=["note:1"],
        author_source="operator",
        suggested_text=text,
        target_content_hash=target.content_hash,
    )
    store.stage_proposal(
        proposal,
        snapshot=InstructionTargetSnapshot(
            target_file=str(target.path),
            target_name=target.target_name,
            project_root=str(target.project_root),
            content_hash=target.content_hash,
            newline=target.newline,
            encoding=target.encoding,
            mode=target.mode,
            content=target.content,
        ),
        candidate=ImprovementCandidate(
            candidate_id=candidate_id,
            target_type="instruction",
            target_owner="project_instructions",
            summary=proposal.summary,
            evidence_refs=list(proposal.evidence_refs),
        ),
    )
    approval = InstructionApprovalRecord(
        approval_id="approval-1",
        candidate_id=candidate_id,
        proposal_hash=proposal.proposal_hash,
        target_file=proposal.target_file,
        target_content_hash=proposal.target_content_hash,
        actor_id="operator",
        session_id="session",
        approval_source="cli_confirm",
    )
    store.approve(approval)
    return store, str(target.path)


def test_instruction_store_persists_proposal_across_instances(tmp_path: Path) -> None:
    store, _target = _stage(tmp_path)

    reopened = InstructionProposalStore(store.path)

    assert reopened.get_proposal("cand-1") is not None
    assert reopened.get_candidate("cand-1") is not None
    assert reopened.get_approval("approval-1") is not None


def test_apply_requires_trusted_approval_and_mutates_target(tmp_path: Path) -> None:
    store, target = _stage(tmp_path)

    applied = apply_instruction_proposal(store, approval_id="approval-1")

    assert applied.state == "promoted"
    assert "- Run the focused test." in Path(target).read_text(encoding="utf-8")
    approval = store.get_approval("approval-1")
    assert approval is not None
    assert approval.is_used is True


def test_apply_rejects_stale_target_without_writing(tmp_path: Path) -> None:
    store, target = _stage(tmp_path)
    Path(target).write_text("# Repo\nchanged\n", encoding="utf-8")

    result = apply_instruction_proposal(store, approval_id="approval-1")

    assert result.state == "suppressed"
    assert "Run the focused test." not in Path(target).read_text(encoding="utf-8")
    assert any(
        event.event_type == "instruction.stale_target_rejected"
        for event in store.list_events()
    )


def test_apply_rejects_snapshot_target_drift(tmp_path: Path) -> None:
    store, target = _stage(tmp_path)
    payload = json.loads(store.path.read_text(encoding="utf-8"))
    payload["target_snapshots"]["cand-1"]["target_file"] = str(
        tmp_path / "OPENMINION.md"
    )
    store.path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="snapshot_target_file_mismatch"):
        apply_instruction_proposal(store, approval_id="approval-1")
    assert Path(target).read_text(encoding="utf-8") == "# Repo\n"


def test_reject_does_not_mutate_project_context(tmp_path: Path) -> None:
    store, target = _stage(tmp_path)

    rejected = reject_instruction_proposal(store, candidate_id="cand-1")

    assert rejected.state == "rejected"
    assert Path(target).read_text(encoding="utf-8") == "# Repo\n"


def test_rollback_restores_prior_content(tmp_path: Path) -> None:
    store, target = _stage(tmp_path)
    apply_instruction_proposal(store, approval_id="approval-1")

    rolled_back = rollback_instruction_proposal(store, candidate_id="cand-1")

    assert rolled_back.state == "rolled_back"
    assert Path(target).read_text(encoding="utf-8") == "# Repo\n"


def test_instruction_candidate_adapter_uses_instruction_owner(tmp_path: Path) -> None:
    store = InstructionProposalStore(tmp_path / "adapter-store.json")

    result = stage_candidate_with_default_owners(
        ImprovementCandidate(
            candidate_id="cand-instruction",
            target_type="instruction",
            target_owner="project_instructions",
            summary="Improve instructions",
            evidence_refs=["trace:1"],
        ),
        instruction_store=store,
    )

    assert result.status == "staged"
    assert result.owner_result["candidate_id"] == "cand-instruction"
    assert result.owner_result["proposal_staged"] is False
