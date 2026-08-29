from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from openminion.modules.task import TaskManager
from openminion.modules.task.runtime.lifecycle import (
    ProjectCycleClaimUnavailable,
    StaleProjectCycleClaim,
)


def _manager(tmp_path) -> TaskManager:
    manager = TaskManager.for_lifecycle_db(db_path=tmp_path / "tasks.db")
    manager.create_task(
        session_id="session-1",
        mode_name="project",
        goal="complete a durable project",
        agent_id="agent-1",
        task_id="task-1",
    )
    return manager


def test_project_cycle_claim_fences_checkpoint_commit(tmp_path) -> None:
    manager = _manager(tmp_path)
    repository = manager.lifecycle_repository
    now = datetime(2026, 8, 19, tzinfo=timezone.utc)
    claim = repository.acquire_project_cycle_claim(
        task_id="task-1",
        owner_id="worker-1",
        expected_checkpoint_id=None,
        now=now,
    )

    repository.commit_project_cycle_checkpoint(
        claim,
        checkpoint_id="checkpoint-1",
        state={"kind": "project_run", "cycle": 1},
        now=now + timedelta(seconds=1),
    )

    assert manager.get_latest_checkpoint("task-1") == (
        "checkpoint-1",
        {"kind": "project_run", "cycle": 1},
    )
    assert manager.get_task("task-1").metadata["last_checkpoint_id"] == "checkpoint-1"
    with pytest.raises(StaleProjectCycleClaim, match="expected checkpoint"):
        repository.commit_project_cycle_checkpoint(
            claim,
            checkpoint_id="checkpoint-2",
            state={"kind": "project_run", "cycle": 2},
            now=now + timedelta(seconds=2),
        )


def test_project_cycle_claim_can_commit_after_legacy_default_window(tmp_path) -> None:
    manager = _manager(tmp_path)
    repository = manager.lifecycle_repository
    now = datetime(2026, 8, 19, tzinfo=timezone.utc)
    claim = repository.acquire_project_cycle_claim(
        task_id="task-1",
        owner_id="worker-1",
        expected_checkpoint_id=None,
        ttl_seconds=2_700,
        now=now,
    )

    repository.commit_project_cycle_checkpoint(
        claim,
        checkpoint_id="checkpoint-1",
        state={"kind": "project_run", "cycle": 1},
        now=now + timedelta(seconds=121),
    )

    assert manager.get_latest_checkpoint("task-1") == (
        "checkpoint-1",
        {"kind": "project_run", "cycle": 1},
    )


def test_project_cycle_claim_blocks_live_owner_and_allows_expiry_takeover(
    tmp_path,
) -> None:
    repository = _manager(tmp_path).lifecycle_repository
    now = datetime(2026, 8, 19, tzinfo=timezone.utc)
    first = repository.acquire_project_cycle_claim(
        task_id="task-1",
        owner_id="worker-1",
        expected_checkpoint_id=None,
        ttl_seconds=10,
        now=now,
    )

    with pytest.raises(ProjectCycleClaimUnavailable):
        repository.acquire_project_cycle_claim(
            task_id="task-1",
            owner_id="worker-2",
            expected_checkpoint_id=None,
            now=now + timedelta(seconds=5),
        )

    second = repository.acquire_project_cycle_claim(
        task_id="task-1",
        owner_id="worker-2",
        expected_checkpoint_id=None,
        now=now + timedelta(seconds=11),
    )

    assert second.fence_token == first.fence_token + 1
    with pytest.raises(StaleProjectCycleClaim, match="stale"):
        repository.refresh_project_cycle_claim(
            first,
            now=now + timedelta(seconds=12),
        )


def test_project_cycle_claim_release_preserves_monotonic_fence(tmp_path) -> None:
    repository = _manager(tmp_path).lifecycle_repository
    now = datetime(2026, 8, 19, tzinfo=timezone.utc)
    first = repository.acquire_project_cycle_claim(
        task_id="task-1",
        owner_id="worker-1",
        expected_checkpoint_id=None,
        now=now,
    )
    repository.release_project_cycle_claim(first, now=now + timedelta(seconds=1))

    second = repository.acquire_project_cycle_claim(
        task_id="task-1",
        owner_id="worker-2",
        expected_checkpoint_id=None,
        now=now + timedelta(seconds=2),
    )

    assert second.fence_token == first.fence_token + 1
