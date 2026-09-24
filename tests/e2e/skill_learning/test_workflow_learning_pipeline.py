from __future__ import annotations

import sqlite3
import pytest


from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from openminion.modules.artifact.control import ArtifactCtl
from openminion.modules.brain.bootstrap.skill.hints import resolve_skill_hints
from openminion.modules.skill.learning import (
    ReplayEvaluationResult,
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
from openminion.modules.skill.learning.replay import proposal_draft_hash
from openminion.modules.skill.interfaces import SkillIngestAuthority
from openminion.modules.skill.proposal.queue import (
    PROPOSAL_QUEUE_STATE_PENDING,
    create_proposal,
    get_proposal,
    record_proposal_review,
    record_replay_proof,
)
from openminion.modules.skill.storage import SQLiteSkillStore
from openminion.modules.skill.runtime.skill import Skill

pytestmark = pytest.mark.e2e


def _store(tmp_path: Path) -> SQLiteSkillStore:
    return SQLiteSkillStore(tmp_path / ".openminion" / "skill.db", wal=False)


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
            shape_id=result.proposal.source_task_shape_ref,
            candidate_hash=proposal_draft_hash(result.proposal),
            evaluator_id="workflow-evaluator",
            result_ref="replay:passed",
            status="passed",
            evidence_refs=["replay:passed"],
        )
        evaluation = ReplayEvaluationResult.model_validate(
            proof.model_dump(exclude={"result_ref"})
        )
        with ArtifactCtl(
            {
                "blob_store": {"root_dir": str(tmp_path / ".openminion/artifacts")},
                "index": {
                    "sqlite_path": str(tmp_path / ".openminion/artifacts/index.db")
                },
                "views": {"auto_generate": []},
            }
        ) as artifactctl:
            result_ref = artifactctl.ingest_bytes(
                evaluation.model_dump_json().encode(),
                mime="application/json",
                agent_id=evaluation.evaluator_id,
            ).ref
            proof = ReplayProof.model_validate(
                record_replay_proof(
                    store,
                    proposal_id=result.proposal.proposal_id,
                    result_ref=result_ref,
                    artifactctl=artifactctl,
                )
            )
        addition = apply_proposal_with_replay(
            store,
            proposal_id=result.proposal.proposal_id,
            current_catalog=[],
            replay_proof=proof,
        )
        assert addition.added_skill_id.startswith("emergent.")

        config = {
            "skill": {
                "sqlite_path": str(tmp_path / ".openminion" / "skill.db"),
                "blob_root": str(tmp_path / ".openminion" / "blob"),
                "fallback_root": str(tmp_path / ".openminion" / "fallback"),
                "wal": False,
            }
        }
        authority = SkillIngestAuthority.local_operator(
            surface="test", principal_id="operator-e2e"
        )
        skill = Skill(config)
        try:
            assert skill.catalog_summaries("agent-1") == []
            admitted = skill.admit_skill_version(
                skill_id=addition.added_skill_id,
                version_hash=addition.version_hash,
                expected_active_version_hash=None,
                target_status="verified",
                reason="reviewed workflow",
                authority=authority,
            )
            assert admitted["active_version_hash"] == addition.version_hash
        finally:
            skill.close()

        skill = Skill(config)
        try:
            runner = MagicMock()
            runner.skill_api = skill
            runner.profile = SimpleNamespace(
                skill=None,
                skill_catalog=[],
                llm_profiles=SimpleNamespace(
                    act_model="", summarize_model="test-model"
                ),
            )
            runner.session_api.get_slice.return_value = {
                "recent_turns": [],
                "open_tasks": [],
                "recent_tool_events": [],
                "summary_short": "",
            }
            state = SimpleNamespace(
                agent_id="agent-1",
                session_id="session-2",
                trace_id="trace-2",
                active_skill_id=None,
                active_skill_version_hash=None,
                resolved_skill_ids=[],
                resolved_skill_versions={},
                session_skill_loaded=[],
                session_skill_unloaded=[],
                skill_selection_mode=None,
            )
            hints = resolve_skill_hints(
                runner,
                intent="run the learned workflow",
                purpose="plan",
                state=state,
                logger=MagicMock(),
            )
            assert hints["skill_id"] == addition.added_skill_id
            assert hints["skill_version_hash"] == addition.version_hash
            assert state.active_skill_version_hash == addition.version_hash
            runner.llm_api.call_structured.assert_not_called()
            run_id = record_learned_skill_reuse(
                skill,
                session_id="session-2",
                agent_id="agent-1",
                skill_id=addition.added_skill_id,
                version_hash=addition.version_hash,
                evidence_refs=[proof.result_ref],
            )
            with sqlite3.connect(tmp_path / ".openminion" / "skill.db") as conn:
                persisted = conn.execute(
                    "SELECT run_id, version_hash, outcome FROM skill_runs WHERE run_id = ?",
                    (run_id,),
                ).fetchone()
            assert persisted == (run_id, addition.version_hash, "success")
        finally:
            skill.close()

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
