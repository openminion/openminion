from __future__ import annotations

from pathlib import Path

from openminion.modules.brain.improvement import (
    ProofPacketLearningStore,
    ProofPacketLearningSubmission,
    ingest_proof_packet_learning_submission,
)


def _submission(submission_id: str, *, summary: str = "Use focused validation"):
    return ProofPacketLearningSubmission(
        submission_id=submission_id,
        proof_packet_ref=f"autonomy_proof:{submission_id}",
        run_id=submission_id,
        target_type="memory",
        target_owner="openminion-memory",
        semantic_summary=summary,
        semantic_author_source="llm",
        evidence_refs=[f"run:{submission_id}"],
        validation_refs=[f"pytest:{submission_id}"],
    )


def test_proof_packet_ingestion_dedupes_and_persists_until_threshold(
    tmp_path: Path,
) -> None:
    store = ProofPacketLearningStore(tmp_path / "proof-learning.json")

    first = ingest_proof_packet_learning_submission(
        _submission("run-1"),
        store=store,
        min_observations=2,
    )
    duplicate = ingest_proof_packet_learning_submission(
        _submission("run-1"),
        store=store,
        min_observations=2,
    )
    reloaded = ProofPacketLearningStore(tmp_path / "proof-learning.json")
    second = ingest_proof_packet_learning_submission(
        _submission("run-2"),
        store=reloaded,
        min_observations=2,
    )

    assert first.status == "threshold_not_met"
    assert duplicate.status == "duplicate_observation"
    assert second.bundle is not None
    assert second.bundle.observation_count == 2
    assert second.bundle.submission_ids == ["run-1", "run-2"]


def test_repeated_authored_memory_evidence_stages_review_candidate(
    tmp_path: Path,
) -> None:
    calls: list[dict] = []

    class MemoryService:
        def stage_candidate(self, **kwargs):
            calls.append(kwargs)
            return "memory-candidate-1"

        def promote_candidate(self, *args, **kwargs):
            raise AssertionError(f"unexpected promotion: {args}, {kwargs}")

    store = ProofPacketLearningStore(tmp_path / "proof-learning.json")
    ingest_proof_packet_learning_submission(
        _submission("run-1"),
        store=store,
        memory_service=MemoryService(),
        session_id="session-1",
        agent_id="agent-1",
        min_observations=2,
    )
    result = ingest_proof_packet_learning_submission(
        _submission("run-2"),
        store=store,
        memory_service=MemoryService(),
        session_id="session-1",
        agent_id="agent-1",
        min_observations=2,
    )

    assert result.status == "staged"
    assert result.stage_result is not None
    assert result.stage_result.owner_result["candidate_ids"] == ["memory-candidate-1"]
    assert len(calls) == 1
    assert calls[0]["record_type"] == "fact"
    assert "evidence:autonomy_proof:run-1" in calls[0]["tags"]
    assert "evidence:autonomy_proof:run-2" in calls[0]["tags"]


def test_disabled_ingestion_is_a_noop(tmp_path: Path) -> None:
    store = ProofPacketLearningStore(tmp_path / "proof-learning.json")

    result = ingest_proof_packet_learning_submission(
        _submission("run-1"),
        store=store,
        enabled=False,
    )

    assert result.status == "disabled"
    assert result.reason_code == "ingestion_disabled"
    assert not store.path.exists()


def test_missing_author_or_target_does_not_call_owner(tmp_path: Path) -> None:
    class MemoryService:
        def stage_candidate(self, **kwargs):
            raise AssertionError(f"unexpected memory owner call: {kwargs}")

    store = ProofPacketLearningStore(tmp_path / "proof-learning.json")
    missing_author = _submission("run-1").model_copy(
        update={"semantic_author_source": None}
    )
    missing_target = _submission("run-2").model_copy(update={"target_type": None})

    author_result = ingest_proof_packet_learning_submission(
        missing_author,
        store=store,
        memory_service=MemoryService(),
    )
    target_result = ingest_proof_packet_learning_submission(
        missing_target,
        store=store,
        memory_service=MemoryService(),
    )

    assert author_result.status == "skipped"
    assert author_result.reason_code == "semantic_author_source_required"
    assert target_result.status == "skipped"
    assert target_result.reason_code == "learning_target_required"
    assert not store.path.exists()


def test_rejected_or_unsupported_target_does_not_call_owner(tmp_path: Path) -> None:
    class MemoryService:
        def stage_candidate(self, **kwargs):
            raise AssertionError(f"unexpected memory owner call: {kwargs}")

    store = ProofPacketLearningStore(tmp_path / "proof-learning.json")
    rejected = _submission("run-1").model_copy(update={"candidate_state": "rejected"})
    skill_target = _submission("run-2").model_copy(
        update={"target_type": "skill", "target_owner": "openminion-skill"}
    )

    rejected_result = ingest_proof_packet_learning_submission(
        rejected,
        store=store,
        memory_service=MemoryService(),
    )
    skill_result = ingest_proof_packet_learning_submission(
        skill_target,
        store=store,
        memory_service=MemoryService(),
    )

    assert rejected_result.status == "skipped"
    assert rejected_result.reason_code == "candidate_state_not_stageable"
    assert skill_result.status == "skipped"
    assert skill_result.reason_code == "unsupported_target_owner"
    assert not store.path.exists()


def test_prose_only_proof_text_cannot_select_owner(tmp_path: Path) -> None:
    class MemoryService:
        def stage_candidate(self, **kwargs):
            raise AssertionError(f"unexpected memory owner call: {kwargs}")

    store = ProofPacketLearningStore(tmp_path / "proof-learning.json")
    submission = ProofPacketLearningSubmission(
        submission_id="run-1",
        proof_packet_ref="autonomy_proof:run-1",
        run_id="run-1",
        semantic_summary="The proof text mentions memory and skill repeatedly.",
        semantic_author_source="operator",
    )

    result = ingest_proof_packet_learning_submission(
        submission,
        store=store,
        memory_service=MemoryService(),
    )

    assert result.status == "skipped"
    assert result.reason_code == "learning_target_required"
    assert not store.path.exists()
