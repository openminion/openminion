from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from openminion.modules.task import TaskManager
from openminion.modules.task.replay_commands import (
    ReplayCommandResult,
    branch_task_from_checkpoint,
    compare_task_checkpoint,
    list_task_checkpoints,
    replay_task_checkpoint,
    rewind_task_to_checkpoint,
)


def _manager(tmp_path: Path) -> TaskManager:
    return TaskManager.for_lifecycle_db(db_path=tmp_path / "tasks.db")


def _task_with_checkpoint(tmp_path: Path) -> tuple[TaskManager, str]:
    manager = _manager(tmp_path)
    record = manager.create_task(
        session_id="s1",
        mode_name="project",
        goal="ship replay fixture",
        agent_id="agent-a",
        task_id="task-1",
    )
    manager.save_checkpoint(
        record.task_id,
        "cp-1",
        {
            "initial_state": {"counter": 1},
            "event_log": [
                {
                    "event_id": "e1",
                    "event_type": "tool.completed",
                    "seq": 1,
                    "payload": {"ok": True},
                    "timestamp": datetime(2026, 7, 19, tzinfo=timezone.utc).isoformat(),
                }
            ],
            "expected_event_payloads": {"e1": {"ok": True}},
        },
    )
    return manager, record.task_id


def test_replay_command_result_serializes_source_refs() -> None:
    result = ReplayCommandResult(
        ok=True,
        action="branch",
        task_id="task-1",
        checkpoint_id="cp-1",
        branch_task_id="branch-1",
        source_refs=("task:task-1", "checkpoint:task-1:cp-1"),
    )

    payload = result.to_dict()

    assert payload["ok"] is True
    assert payload["source_refs"] == ["task:task-1", "checkpoint:task-1:cp-1"]


def test_list_task_checkpoints_returns_existing_checkpoint_ids(tmp_path: Path) -> None:
    manager, task_id = _task_with_checkpoint(tmp_path)

    result = list_task_checkpoints(manager, task_id=task_id)

    assert result.ok is True
    assert result.checkpoints == ("cp-1",)
    assert result.source_refs == ("task:task-1",)


def test_replay_task_checkpoint_uses_recorded_events_without_provider_calls(
    tmp_path: Path,
) -> None:
    manager, task_id = _task_with_checkpoint(tmp_path)

    result = replay_task_checkpoint(manager, task_id=task_id, checkpoint_id="cp-1")

    assert result.ok is True
    assert result.events_replayed == 1
    assert result.divergences == ()
    assert "providers and tools are not re-invoked" in result.nondeterminism_notes[0]


def test_compare_task_checkpoint_reports_state_divergence(tmp_path: Path) -> None:
    manager, task_id = _task_with_checkpoint(tmp_path)
    manager.save_checkpoint(task_id, "cp-2", {"counter": 2})

    result = compare_task_checkpoint(
        manager,
        task_id=task_id,
        checkpoint_id="cp-1",
        expected_checkpoint_id="cp-2",
    )

    assert result.ok is False
    assert result.divergences[0]["divergence_kind"] == "state_mismatch"


def test_branch_task_from_checkpoint_creates_additive_record(tmp_path: Path) -> None:
    manager, task_id = _task_with_checkpoint(tmp_path)

    result = branch_task_from_checkpoint(
        manager,
        task_id=task_id,
        checkpoint_id="cp-1",
        branch_task_id="branch-1",
    )

    assert result.ok is True
    assert result.branch_task_id == "branch-1"
    source = manager.get_task(task_id)
    branch = manager.get_task("branch-1")
    assert source is not None
    assert branch is not None
    assert source.metadata.get("source_checkpoint_id") is None
    assert branch.metadata["source_task_id"] == task_id
    assert branch.metadata["source_checkpoint_id"] == "cp-1"


def test_rewind_task_to_checkpoint_creates_before_checkpoint_branch(
    tmp_path: Path,
) -> None:
    manager, task_id = _task_with_checkpoint(tmp_path)

    result = rewind_task_to_checkpoint(
        manager,
        task_id=task_id,
        checkpoint_id="cp-1",
        branch_task_id="rewind-1",
    )

    branch = manager.get_task("rewind-1")
    assert result.action == "rewind"
    assert branch is not None
    assert branch.metadata["replay_action"] == "rewind"
    assert branch.metadata["branch_mode"] == "before_checkpoint"


def test_branch_task_from_checkpoint_rejects_unknown_branch_mode(tmp_path: Path) -> None:
    manager, task_id = _task_with_checkpoint(tmp_path)

    with pytest.raises(ValueError, match="unsupported branch mode"):
        branch_task_from_checkpoint(
            manager,
            task_id=task_id,
            checkpoint_id="cp-1",
            branch_mode="mutate_history",  # type: ignore[arg-type]
        )
