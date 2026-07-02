from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from openminion.modules.skill.learning import (
    SkillDraftError,
    SkillExecutionTrustRecord,
    WorkflowShapeMiner,
    apply_proposal_with_replay,
    bundle_from_autonomy_proof_packet,
    promote_execution_trust,
    record_learned_skill_reuse,
    record_skill_run_outcome,
    render_skill_markdown,
    stage_shape_as_skill_proposal,
    workflow_learning_event,
)
from openminion.modules.skill.learning.reuse import matching_catalog_entries
from openminion.modules.skill.learning.replay import ReplayGateError, ReplayProof
from openminion.modules.skill.learning.shapes import (
    WorkflowEvidenceBundle,
    WorkflowShape,
    command_fingerprint,
)
from openminion.modules.skill.proposal.queue import (
    PROPOSAL_QUEUE_STATE_PENDING,
    PROPOSAL_QUEUE_STATE_REVIEWED,
    create_proposal,
    get_proposal,
    record_proposal_review,
)
from openminion.modules.skill.proposal import SkillProposal, SkillProposalDraft
from openminion.modules.skill.storage import SQLiteSkillStore
from openminion.modules.telemetry.events.catalog import WORKFLOW_TRUST_DOWNGRADED


def _store(tmp_path: Path) -> SQLiteSkillStore:
    return SQLiteSkillStore(tmp_path / "skill.db", wal=False)


def _proof_packet(run_id: str = "run-1") -> dict[str, object]:
    return {
        "run_id": run_id,
        "status": "completed",
        "started_at_ms": 1,
        "ended_at_ms": 2,
        "artifact_refs": ("artifact://summary.md",),
        "commands_run": (
            {
                "command": ("python", "/tmp/project/run.py", "--api-key=abc123"),
                "cwd_ref": "workspace",
                "started_at_ms": 1,
                "ended_at_ms": 2,
                "exit_code": 0,
                "status": "succeeded",
                "summary": "ran command",
            },
        ),
        "tests_run": (
            {
                "command": ("pytest", "tests/test_smoke.py"),
                "cwd_ref": "workspace",
                "started_at_ms": 2,
                "ended_at_ms": 3,
                "exit_code": 0,
                "passed": 1,
                "failed": 0,
                "skipped": 0,
                "status": "passed",
                "summary": "passed",
            },
        ),
        "validation_summary": "ok",
        "final_operator_summary": "done",
    }


def _shape(success_count: int = 2, explicit_save_count: int = 0) -> WorkflowShape:
    return WorkflowShape(
        intent_category="task:test_cleanup",
        capability_category="capability:cleanup",
        strategy_id="strategy:test_cleanup",
        tool_names=["exec"],
        command_fingerprints=[command_fingerprint(("pytest", "tests"))],
        test_fingerprints=[command_fingerprint(("ruff", "check", "."))],
        artifact_types=["md"],
        success_count=success_count,
        evidence_refs=["proof:1", "proof:2"],
        performance_entry_refs=["proof:1", "proof:2"],
        knowledge_record_refs=["proof:1", "proof:2"],
        explicit_save_count=explicit_save_count,
    )


def _proposal(proposal_id: str = "wlsk-proposal") -> SkillProposal:
    return SkillProposal(
        proposal_id=proposal_id,
        source_task_shape_ref="workflow_shape:wlsh-test",
        proposed_skill_definition=SkillProposalDraft(
            name="test-cleanup-playbook",
            display_name="Test Cleanup Playbook",
            short_description="From workflow learning evidence.",
            tools=[],
            tags=["strategy:test_cleanup", "capability:cleanup", "task:test_cleanup"],
            risk_class="low",
            applies_to={"intents": ["task:test_cleanup"], "steps": []},
            inputs_schema=[],
            verification_rules=["workflow_replay_passed:wlsh-test"],
        ),
        evidence_refs=["proof:1"],
        proposer_policy_id="workflow_learning_review_first",
        proposed_at="",
    )


def test_evidence_bundle_redacts_and_round_trips() -> None:
    bundle = bundle_from_autonomy_proof_packet(
        _proof_packet(),
        intent_category="test cleanup",
        capability_category="cleanup",
        strategy_id="test cleanup",
        tool_names=["exec"],
    )

    assert bundle.outcome == "success"
    assert bundle.redaction_status == "redacted"
    assert bundle.command_fingerprints
    assert "/tmp/project" not in bundle.model_dump_json()
    assert "abc123" not in bundle.model_dump_json()
    assert WorkflowEvidenceBundle.model_validate_json(bundle.model_dump_json()) == bundle


def test_shape_contract_rejects_unknown_trust_state() -> None:
    with pytest.raises(ValidationError):
        SkillExecutionTrustRecord(
            skill_id="skill.x",
            shape_id="shape.x",
            trust_state="trusted_for_everything",
        )


def test_miner_groups_structural_runs_and_rejects_prose_only() -> None:
    first = bundle_from_autonomy_proof_packet(
        _proof_packet("run-1"),
        intent_category="test cleanup",
        capability_category="cleanup",
        strategy_id="test cleanup",
        tool_names=["exec"],
        observed_at="2026-07-02T00:00:00Z",
    )
    second = first.model_copy(update={"source_run_refs": ["run-2"], "bundle_id": ""})
    prose_only = WorkflowEvidenceBundle(
        intent_category="task:test_cleanup",
        capability_category="capability:cleanup",
        strategy_id="strategy:test_cleanup",
        outcome="success",
        validation_summary="same prose but no structural fields",
    )

    shapes = WorkflowShapeMiner().mine([first, second, prose_only])

    assert len(shapes) == 1
    assert shapes[0].success_count == 2
    assert WorkflowShapeMiner().is_skill_ready(shapes[0])


def test_user_save_signal_can_create_candidate_shape() -> None:
    bundle = bundle_from_autonomy_proof_packet(
        _proof_packet("run-save"),
        intent_category="cleanup",
        capability_category="cleanup",
        strategy_id="cleanup",
        tool_names=["exec"],
        explicit_save=True,
        actor_id="operator-1",
    )

    ready = WorkflowShapeMiner().skill_ready_shapes([bundle])

    assert len(ready) == 1
    assert ready[0].explicit_save_count == 1


def test_stage_shape_uses_proposal_queue_and_suppresses_duplicates(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        result = stage_shape_as_skill_proposal(
            _shape(),
            store=store,
            current_catalog=[],
        )
        assert result.status == "staged"
        assert result.queue_record["queue_state"] == PROPOSAL_QUEUE_STATE_PENDING
        assert result.candidate is not None
        assert result.candidate["target_type"] == "skill"

        duplicate = stage_shape_as_skill_proposal(
            _shape(),
            store=store,
            current_catalog=[
                {
                    "skill_id": "strategy_test_cleanup",
                    "name": "Test Cleanup",
                    "tags": ["capability:cleanup"],
                    "applies_to": {"intents": ["task:test_cleanup"]},
                }
            ],
        )
        assert duplicate.status == "skipped_duplicate"
    finally:
        store.close()


def test_skill_draft_rejects_forbidden_prose_and_requires_validation() -> None:
    shape = _shape()
    with pytest.raises(SkillDraftError):
        render_skill_markdown(
            shape,
            title="Cleanup",
            description="This can bypass approval.",
            steps=["Run cleanup"],
            validation_rules=["pytest tests"],
        )
    with pytest.raises(SkillDraftError):
        render_skill_markdown(
            shape,
            title="Cleanup",
            description="Cleanup safely.",
            steps=["Run cleanup"],
            validation_rules=[],
            source_changing=True,
        )

    rendered = render_skill_markdown(
        shape,
        title="Cleanup",
        description="Cleanup safely.",
        steps=["Run cleanup"],
        validation_rules=["pytest tests"],
    )
    assert "# Validation" in rendered
    assert "pytest tests" in rendered


def test_replay_proof_blocks_apply_until_passed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        create_proposal(store, _proposal())
        record_proposal_review(
            store,
            proposal_id="wlsk-proposal",
            reviewer_id="operator-1",
            review_policy_id="workflow_learning_review",
            criterion_decisions=[
                {"criterion_id": "fit", "status": "accepted", "comment": "ok"}
            ],
        )
        record = get_proposal(store, proposal_id="wlsk-proposal")
        assert record is not None
        assert record["queue_state"] == PROPOSAL_QUEUE_STATE_REVIEWED

        with pytest.raises(ReplayGateError):
            apply_proposal_with_replay(
                store,
                proposal_id="wlsk-proposal",
                current_catalog=[],
                replay_proof=ReplayProof(
                    proof_id="proof-1",
                    proposal_id="wlsk-proposal",
                    shape_id="wlsh-test",
                    status="failed",
                ),
            )

        addition = apply_proposal_with_replay(
            store,
            proposal_id="wlsk-proposal",
            current_catalog=[],
            replay_proof=ReplayProof(
                proof_id="proof-2",
                proposal_id="wlsk-proposal",
                shape_id="wlsh-test",
                status="passed",
            ),
        )
        assert addition.added_skill_id == "emergent.test-cleanup-playbook"
    finally:
        store.close()


def test_reuse_records_through_existing_log_run_owner_and_trust_demotes() -> None:
    calls: list[dict[str, object]] = []

    class Runtime:
        def log_run(self, **kwargs: object) -> str:
            calls.append(dict(kwargs))
            return "skill-run-1"

    run_id = record_learned_skill_reuse(
        Runtime(),
        session_id="s1",
        agent_id="a1",
        skill_id="emergent.cleanup",
        version_hash="v1",
        outcome="success",
        evidence_refs=["proof:1"],
    )
    assert run_id == "skill-run-1"
    assert calls[0]["outcome"] == "success"

    record = SkillExecutionTrustRecord(
        skill_id="emergent.cleanup",
        shape_id="wlsh-test",
        trust_state="catalog_applied",
    )
    record = promote_execution_trust(record, "suggest_only")
    record = record_skill_run_outcome(record, outcome="success", evidence_ref=run_id)
    record = promote_execution_trust(record, "trusted_for_manual")
    record = record_skill_run_outcome(record, outcome="fail", evidence_ref="fail-1")
    record = record_skill_run_outcome(record, outcome="fail", evidence_ref="fail-2")

    assert record.trust_state == "execution_downgraded"
    assert record.failure_count_after_apply == 2


def test_matching_catalog_entries_accepts_dict_and_object_entries() -> None:
    class Entry:
        tags = ["cleanup", "test_cleanup"]
        applies_to = {"intents": ["test_cleanup"]}

    shape = _shape()
    matches = matching_catalog_entries(
        shape,
        [
            {"tags": ["cleanup", "test_cleanup"], "applies_to": {"intents": ["test_cleanup"]}},
            {
                "tags": ["capability:cleanup", "strategy:test_cleanup"],
                "applies_to": {"intents": ["task:test_cleanup"]},
            },
            Entry(),
            {"tags": ["other"], "applies_to": {"intents": ["test_cleanup"]}},
        ],
    )

    assert len(matches) == 3


def test_workflow_learning_telemetry_is_registered_and_redacted() -> None:
    event = workflow_learning_event(
        WORKFLOW_TRUST_DOWNGRADED,
        shape_id="wlsh-test",
        raw_transcript="secret chat",
        path="/Users/j/repos/base/agent-frameworks",
    )

    assert event["event_type"] == WORKFLOW_TRUST_DOWNGRADED
    assert "raw_transcript" not in event["payload"]
    assert event["payload"]["path"] == "<path>"
