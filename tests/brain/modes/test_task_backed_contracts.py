from __future__ import annotations

from pathlib import Path

import pytest

from openminion.modules.brain.loop.strategies.research import (
    RESEARCH_MODE,
    ResearchMode,
)
from openminion.modules.brain.checkpoint.contracts import (
    TaskBackedModeContract,
    TaskProgress,
)
from openminion.modules.task import TaskManager


def _manager(tmp_path: Path) -> TaskManager:
    return TaskManager.for_lifecycle_db(db_path=tmp_path / "tasks.db")


def test_task_progress_validates_and_round_trips_json() -> None:
    progress = TaskProgress(
        phase="gather_sources",
        completion_pct=0.25,
        partial_results=["Found three candidate sources."],
        last_checkpoint_id="research-task-1-phase-1",
        message="Completed gather sources.",
    )

    encoded = progress.model_dump_json()
    decoded = TaskProgress.model_validate_json(encoded)

    assert decoded == progress

    with pytest.raises(Exception):
        TaskProgress(
            phase="bad-progress",
            completion_pct=1.5,
            partial_results=[],
        )


def test_task_backed_protocols_are_runtime_checkable(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    assert callable(getattr(manager, "create_task", None))
    assert callable(getattr(manager, "save_checkpoint", None))
    assert isinstance(ResearchMode(), TaskBackedModeContract)


def test_task_manager_checkpoint_round_trip_and_listing(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    record = manager.create_task(
        session_id="s-task-backed",
        mode_name=RESEARCH_MODE,
        goal="Research durable modes",
        agent_id="router-agent",
    )

    manager.save_checkpoint(
        record.task_id,
        "research-checkpoint-1",
        {"next_phase_index": 1, "partial_results": ["phase-1"]},
    )
    manager.save_checkpoint(
        record.task_id,
        "research-checkpoint-2",
        {"next_phase_index": 2, "partial_results": ["phase-1", "phase-2"]},
    )

    latest = manager.get_latest_checkpoint(record.task_id)

    assert latest is not None
    assert latest[0] == "research-checkpoint-2"
    assert latest[1]["next_phase_index"] == 2
    assert manager.list_checkpoints(record.task_id) == [
        "research-checkpoint-1",
        "research-checkpoint-2",
    ]
    assert manager.get_latest_checkpoint("missing-task") is None


def test_task_manager_progress_updates_task_record_metadata(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    record = manager.create_task(
        session_id="s-progress",
        mode_name=RESEARCH_MODE,
        goal="Research progress metadata",
        agent_id="router-agent",
    )

    manager.update_progress(
        record.task_id,
        {
            "phase": "read_sources",
            "completion_pct": 0.5,
            "partial_results": ["phase-1", "phase-2"],
            "last_checkpoint_id": "research-checkpoint-2",
            "message": "Completed read sources.",
        },
    )

    loaded = manager.get_task(record.task_id)
    assert loaded is not None
    assert loaded.metadata["progress"]["phase"] == "read_sources"
    assert loaded.metadata["last_checkpoint_id"] == "research-checkpoint-2"
