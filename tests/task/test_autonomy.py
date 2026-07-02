from __future__ import annotations

import json
from pathlib import Path

import pytest

from openminion.modules.task.autonomy import (
    AutonomyRunError,
    AutonomyRunPhase,
    AutonomyRunStatus,
    AutonomyRunStore,
    EvidenceStatus,
    build_autonomy_run,
    build_local_workspace_ref,
    build_terminal_proof_packet,
)


def _run(tmp_path: Path):
    return build_autonomy_run(
        goal_text="Ship a small change",
        goal_id="goal-1",
        session_id="session-1",
        workspace_ref=build_local_workspace_ref(tmp_path),
        max_iterations=1,
    )


def test_autonomy_run_store_round_trips_and_lists(tmp_path: Path) -> None:
    store = AutonomyRunStore(tmp_path / "autonomy")
    run = store.create(_run(tmp_path))

    loaded = store.require(run.run_id)
    assert loaded.run_id == run.run_id
    assert loaded.status == AutonomyRunStatus.QUEUED
    assert loaded.memory_candidate_refs == ()
    assert loaded.skill_candidate_refs == ()

    listed = store.list_runs()
    assert [item.run_id for item in listed] == [run.run_id]


def test_autonomy_run_transition_validation_blocks_terminal_regression(
    tmp_path: Path,
) -> None:
    store = AutonomyRunStore(tmp_path / "autonomy")
    run = store.create(_run(tmp_path))

    running = store.transition(
        run.run_id,
        status=AutonomyRunStatus.RUNNING,
        phase=AutonomyRunPhase.EXECUTE,
    )
    completed = store.transition(
        running.run_id,
        status=AutonomyRunStatus.COMPLETED,
        phase=AutonomyRunPhase.CLOSED,
    )

    assert completed.completed_at_ms is not None
    with pytest.raises(ValueError, match="invalid autonomy transition"):
        store.transition(completed.run_id, status=AutonomyRunStatus.RUNNING)


def test_autonomy_terminal_proof_packet_updates_run_ref(tmp_path: Path) -> None:
    store = AutonomyRunStore(tmp_path / "autonomy")
    run = store.create(_run(tmp_path))
    running = store.transition(run.run_id, status=AutonomyRunStatus.RUNNING)
    completed = store.transition(
        running.run_id,
        status=AutonomyRunStatus.COMPLETED,
        phase=AutonomyRunPhase.CLOSED,
        operator_summary="done",
    )
    packet = build_terminal_proof_packet(
        completed,
        validation_summary="validated",
        final_operator_summary="done",
    )

    proof_path = store.write_proof_packet(packet)
    refreshed = store.require(run.run_id)

    assert refreshed.proof_packet_ref == str(proof_path)
    payload = json.loads(proof_path.read_text(encoding="utf-8"))
    assert payload["run_id"] == run.run_id
    assert payload["status"] == "completed"
    assert payload["commands_run"] == []
    assert payload["tests_run"] == []


def test_blocked_run_preserves_error_in_proof_packet(tmp_path: Path) -> None:
    store = AutonomyRunStore(tmp_path / "autonomy")
    run = store.create(_run(tmp_path))
    blocked = store.transition(
        run.run_id,
        status=AutonomyRunStatus.BLOCKED,
        phase=AutonomyRunPhase.CLOSED,
        error=AutonomyRunError(code="BUDGET_EXHAUSTED", message="budget exhausted"),
    )
    packet = build_terminal_proof_packet(
        blocked,
        validation_summary="blocked",
        final_operator_summary="blocked",
    )

    assert packet.failure_or_blocker is not None
    assert packet.failure_or_blocker.code == "BUDGET_EXHAUSTED"


def test_local_workspace_ref_marks_git_or_unknown_state(tmp_path: Path) -> None:
    ref = build_local_workspace_ref(tmp_path)

    assert ref.startswith(f"local:{tmp_path.resolve(strict=False)}")
    assert "commit=" in ref
    assert "dirty=" in ref


def test_command_evidence_schema_rejects_unknown_fields() -> None:
    from openminion.modules.task.autonomy import CommandEvidence

    with pytest.raises(ValueError):
        CommandEvidence(
            command=("openminion", "autonomy"),
            cwd_ref="/tmp",
            started_at_ms=1,
            ended_at_ms=2,
            exit_code=0,
            status=EvidenceStatus.SUCCEEDED,
            summary="ok",
            unexpected=True,  # type: ignore[call-arg]
        )
