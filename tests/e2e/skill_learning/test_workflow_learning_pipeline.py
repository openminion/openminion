from __future__ import annotations

import pytest


from pathlib import Path

from openminion.modules.skill.learning import (
    ReplayProof,
    WorkflowShapeMiner,
    apply_proposal_with_replay,
    bundle_from_autonomy_proof_packet,
    stage_shape_as_skill_proposal,
)
from openminion.modules.skill.proposal.queue import (
    PROPOSAL_QUEUE_STATE_PENDING,
    create_proposal,
    get_proposal,
    record_proposal_review,
)
from openminion.modules.skill.storage import SQLiteSkillStore

pytestmark = pytest.mark.e2e


def _store(tmp_path: Path) -> SQLiteSkillStore:
    return SQLiteSkillStore(tmp_path / "skill.db", wal=False)


def _proof(run_id: str) -> dict[str, object]:
    return {
        "run_id": run_id,
        "status": "completed",
        "started_at_ms": 1,
        "ended_at_ms": 2,
        "artifact_refs": (f"artifact://{run_id}.md",),
        "commands_run": (
            {
                "command": ("python", "-m", "pytest", "tests/skill"),
                "cwd_ref": "workspace",
                "started_at_ms": 1,
                "ended_at_ms": 2,
                "exit_code": 0,
                "status": "succeeded",
                "summary": "ran focused tests",
            },
        ),
        "tests_run": (
            {
                "command": ("pytest", "tests/skill"),
                "cwd_ref": "workspace",
                "started_at_ms": 2,
                "ended_at_ms": 3,
                "exit_code": 0,
                "passed": 12,
                "failed": 0,
                "skipped": 0,
                "status": "passed",
                "summary": "passed",
            },
        ),
        "validation_summary": "passed",
        "final_operator_summary": "workflow succeeded",
    }


def test_observe_to_apply_to_reuse_to_downgrade(tmp_path: Path) -> None:
    bundles = [
        bundle_from_autonomy_proof_packet(
            _proof("run-1"),
            intent_category="test cleanup",
            capability_category="cleanup",
            strategy_id="test cleanup",
            tool_names=["exec"],
        ),
        bundle_from_autonomy_proof_packet(
            _proof("run-2"),
            intent_category="test cleanup",
            capability_category="cleanup",
            strategy_id="test cleanup",
            tool_names=["exec"],
        ),
    ]
    shape = WorkflowShapeMiner().skill_ready_shapes(bundles)[0]
    store = _store(tmp_path)
    try:
        result = stage_shape_as_skill_proposal(
            shape,
            store=store,
            current_catalog=[],
        )
        assert result.status == "staged"
        assert result.proposal is not None
        assert result.queue_record["queue_state"] == PROPOSAL_QUEUE_STATE_PENDING

        # Re-creating the same proposal is idempotent and stays pending.
        create_proposal(store, result.proposal)
        record_proposal_review(
            store,
            proposal_id=result.proposal.proposal_id,
            reviewer_id="operator-e2e",
            review_policy_id="workflow_learning_review",
            criterion_decisions=[
                {
                    "criterion_id": "fit",
                    "status": "accepted",
                    "comment": "recurring workflow evidence is sufficient",
                }
            ],
        )
        proof = ReplayProof(
            proof_id="replay-proof-1",
            proposal_id=result.proposal.proposal_id,
            shape_id=shape.shape_id,
            status="passed",
            evidence_refs=["replay:passed"],
        )
        with pytest.raises(ValueError, match="complete skill Markdown"):
            apply_proposal_with_replay(
                store,
                proposal_id=result.proposal.proposal_id,
                current_catalog=[],
                replay_proof=proof,
            )
        reviewed = get_proposal(store, proposal_id=result.proposal.proposal_id)
        assert reviewed is not None
        assert reviewed["queue_state"] == "reviewed"
        assert reviewed["applied_addition"] is None
    finally:
        store.close()
