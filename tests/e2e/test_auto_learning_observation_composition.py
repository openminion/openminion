from __future__ import annotations

from pathlib import Path

from openminion.modules.brain import improvement as improvement_api
from openminion.modules.runtime.project_instructions import (
    resolve_project_instruction_target,
)
from openminion.modules.skill.learning import (
    WorkflowEvidenceBundle,
    WorkflowShapeMiner,
    stage_shape_as_skill_proposal,
)
from openminion.modules.skill.proposal.queue import PROPOSAL_QUEUE_STATE_PENDING
from openminion.modules.skill.storage import SQLiteSkillStore


def _workflow_bundle(run_id: str, summary: str) -> WorkflowEvidenceBundle:
    return WorkflowEvidenceBundle(
        source_run_refs=[run_id],
        proof_packet_refs=[f"autonomy_proof:{run_id}"],
        tool_names=["exec"],
        command_fingerprints=["pytest-command"],
        test_fingerprints=["pytest-result"],
        artifact_types=["report"],
        validation_summary=summary,
        outcome="success",
        intent_category="task:test-cleanup",
        capability_category="capability:cleanup",
        strategy_id="strategy:focused-tests",
    )


def test_auto_learning_public_owner_composition_is_review_gated(
    tmp_path: Path,
) -> None:
    skill_store = SQLiteSkillStore(tmp_path / "skills.db", wal=False)
    try:
        shape = WorkflowShapeMiner().skill_ready_shapes(
            [
                _workflow_bundle("run-1", "first successful run"),
                _workflow_bundle("run-2", "different prose, same structure"),
            ]
        )[0]
        skill_result = stage_shape_as_skill_proposal(
            shape,
            store=skill_store,
            current_catalog=[],
        )
        assert skill_result.status == "staged"
        assert skill_result.proposal is not None
        assert skill_result.queue_record["queue_state"] == PROPOSAL_QUEUE_STATE_PENDING
        assert skill_store.list_latest_skills() == []

        project_root = tmp_path / "project"
        project_root.mkdir()
        (project_root / ".git").mkdir()
        target_path = project_root / "OPENMINION.md"
        target_path.write_text("# Project\n", encoding="utf-8")
        target = resolve_project_instruction_target(project_root)
        instruction_store = improvement_api.InstructionProposalStore(
            tmp_path / "instruction-proposals.json"
        )
        opportunity = improvement_api.InstructionOpportunity(
            opportunity_id="instruction-opportunity-1",
            source_kind="operator_signal",
            evidence_refs=["run:2"],
        )
        instruction_store.stage_opportunity(opportunity)
        proposal = improvement_api.build_instruction_proposal(
            candidate_id="instruction-candidate-1",
            opportunity_id=opportunity.opportunity_id,
            target_file=str(target.path),
            target_name=target.target_name,
            proposal_kind="append_bullet",
            summary="Record focused validation guidance",
            evidence_refs=["run:2"],
            author_source="operator",
            suggested_text="Run focused validation before closeout.",
            target_content_hash=target.content_hash,
        )
        instruction_store.stage_proposal(
            proposal,
            snapshot=improvement_api.InstructionTargetSnapshot(
                target_file=str(target.path),
                target_name=target.target_name,
                project_root=str(target.project_root),
                content_hash=target.content_hash,
                newline=target.newline,
                encoding=target.encoding,
                mode=target.mode,
                content=target.content,
            ),
            candidate=improvement_api.ImprovementCandidate(
                candidate_id=proposal.candidate_id,
                target_type="instruction",
                target_owner="project_instructions",
                summary=proposal.summary,
                evidence_refs=proposal.evidence_refs,
            ),
        )
        assert instruction_store.get_proposal(proposal.candidate_id) is not None
        assert target_path.read_text(encoding="utf-8") == "# Project\n"
        rejected = improvement_api.reject_instruction_proposal(
            instruction_store,
            candidate_id=proposal.candidate_id,
        )
        assert rejected.state == "rejected"
        assert target_path.read_text(encoding="utf-8") == "# Project\n"

        memory_calls: list[dict] = []

        class MemoryService:
            def stage_candidate(self, **kwargs):
                memory_calls.append(kwargs)
                return "memory-candidate-1"

            def promote_candidate(self, *args, **kwargs):
                raise AssertionError(f"unexpected promotion: {args}, {kwargs}")

        memory_service = MemoryService()
        memory_result = improvement_api.stage_learning_memory_candidate(
            improvement_api.ImprovementCandidate(
                candidate_id="memory-learning-1",
                target_type="memory",
                target_owner="openminion-memory",
                summary="Use focused validation for similar work",
                evidence_refs=["run:1", "run:2"],
                semantic_author_source="llm",
            ),
            memory_service=memory_service,
            session_id="session-1",
            agent_id="agent-1",
        )
        assert memory_result.status == "staged"
        assert memory_result.owner_result["candidate_ids"] == ["memory-candidate-1"]
        assert len(memory_calls) == 1

        unauthorized = improvement_api.stage_learning_memory_candidate(
            improvement_api.ImprovementCandidate(
                candidate_id="memory-learning-unauthorized",
                target_type="memory",
                target_owner="openminion-memory",
                summary="runtime-authored semantic lesson",
            ),
            memory_service=memory_service,
            session_id="session-1",
            agent_id="agent-1",
        )
        assert unauthorized.status == "skipped"
        assert unauthorized.reason_code == "semantic_author_source_required"
        assert len(memory_calls) == 1
    finally:
        skill_store.close()
