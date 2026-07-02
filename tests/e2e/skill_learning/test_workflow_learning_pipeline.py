from __future__ import annotations

from pathlib import Path

from openminion.modules.skill.learning import (
    ReplayProof,
    SkillExecutionTrustRecord,
    WorkflowShapeMiner,
    apply_proposal_with_replay,
    bundle_from_autonomy_proof_packet,
    promote_execution_trust,
    record_learned_skill_reuse,
    record_skill_run_outcome,
    stage_shape_as_skill_proposal,
)
from openminion.modules.skill.proposal.queue import (
    PROPOSAL_QUEUE_STATE_PENDING,
    create_proposal,
    get_proposal,
    record_proposal_review,
)
from openminion.modules.skill.storage import SQLiteSkillStore


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
        addition = apply_proposal_with_replay(
            store,
            proposal_id=result.proposal.proposal_id,
            current_catalog=[],
            replay_proof=proof,
        )
        assert addition.added_skill_id.startswith("emergent.")

        class Runtime:
            def __init__(self) -> None:
                self.runs: list[dict[str, object]] = []

            def log_run(self, **kwargs: object) -> str:
                run_id = f"skill-run-{len(self.runs) + 1}"
                self.runs.append({"run_id": run_id, **kwargs})
                return run_id

        runtime = Runtime()
        run_id = record_learned_skill_reuse(
            runtime,
            session_id="session-1",
            agent_id="agent-1",
            skill_id=addition.added_skill_id,
            version_hash="v1",
            evidence_refs=["replay:passed"],
        )
        assert runtime.runs[0]["outcome"] == "success"

        trust = SkillExecutionTrustRecord(
            skill_id=addition.added_skill_id,
            shape_id=shape.shape_id,
            trust_state="catalog_applied",
        )
        trust = promote_execution_trust(trust, "suggest_only")
        trust = record_skill_run_outcome(trust, outcome="success", evidence_ref=run_id)
        trust = promote_execution_trust(trust, "trusted_for_manual")
        trust = record_skill_run_outcome(
            trust, outcome="fail", evidence_ref="run-fail-1"
        )
        trust = record_skill_run_outcome(
            trust, outcome="fail", evidence_ref="run-fail-2"
        )

        assert trust.trust_state == "execution_downgraded"
        applied = get_proposal(store, proposal_id=result.proposal.proposal_id)
        assert applied is not None
        assert applied["applied_addition"]["added_skill_id"] == addition.added_skill_id
    finally:
        store.close()
