from __future__ import annotations

import importlib
import sqlite3
from pathlib import Path

import pytest

from openminion.modules.session.storage.repository import create_sqlite_cron_repository
from openminion.modules.task import TaskLifecycleState, TaskManager


def _manager(tmp_path: Path) -> TaskManager:
    repo = create_sqlite_cron_repository(db_path=tmp_path / "sessions.db")
    return TaskManager.from_cron_repository(repo)


def test_schedule_creates_lifecycle_record_and_schema(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    record = manager.schedule_task(
        name="health-check",
        schedule={"kind": "every", "every_ms": 60_000},
        payload={"kind": "agentTurn", "message": "check health"},
        agent_id="agent-a",
        session_target="isolated",
        misfire_policy="skip",
    )

    assert record.task_id
    assert record.task_id == record.cron_job_id
    assert record.agent_id == "agent-a"
    assert record.state == TaskLifecycleState.ACTIVE
    assert manager.get_task(record.task_id) is not None

    conn = sqlite3.connect(str(tmp_path / "sessions.db"))
    row = conn.execute(
        "SELECT state FROM scheduled_tasks WHERE task_id = ?",
        (record.task_id,),
    ).fetchone()
    conn.close()
    assert row is not None
    assert str(row[0]) == "active"


def test_lifecycle_state_transitions_and_terminal_guard(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    created = manager.schedule_task(
        name="status-flow",
        schedule={"kind": "every", "every_ms": 60_000},
        payload={"kind": "agentTurn", "message": "flow"},
        agent_id="agent-a",
    )

    paused = manager.transition_task(
        task_id=created.task_id, to_state=TaskLifecycleState.PAUSED
    )
    assert paused.state == TaskLifecycleState.PAUSED

    resumed = manager.transition_task(
        task_id=created.task_id, to_state=TaskLifecycleState.ACTIVE
    )
    assert resumed.state == TaskLifecycleState.ACTIVE

    completed = manager.transition_task(
        task_id=created.task_id, to_state=TaskLifecycleState.DONE
    )
    assert completed.state == TaskLifecycleState.DONE
    assert completed.completed_at is not None

    with pytest.raises(ValueError, match="invalid task state transition"):
        manager.transition_task(
            task_id=created.task_id, to_state=TaskLifecycleState.ACTIVE
        )


def test_cancel_marks_lifecycle_and_deletes_cron_job(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    created = manager.schedule_task(
        name="cancel-flow",
        schedule={"kind": "every", "every_ms": 60_000},
        payload={"kind": "agentTurn", "message": "cancel"},
        agent_id="agent-a",
    )

    cancelled = manager.cancel_task(created.task_id)
    assert cancelled.state == TaskLifecycleState.CANCELLED
    assert cancelled.cancelled_at is not None

    repo_job = manager.get_task_by_job(created.cron_job_id)
    assert repo_job is not None
    assert repo_job.state == TaskLifecycleState.CANCELLED


def test_pause_resume_updates_lifecycle_without_deleting_job(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    created = manager.schedule_task(
        name="pause-flow",
        schedule={"kind": "every", "every_ms": 60_000},
        payload={"kind": "agentTurn", "message": "pause"},
        agent_id="agent-a",
    )

    paused_record, paused_job = manager.pause_task(created.task_id)
    assert paused_record.state == TaskLifecycleState.PAUSED
    assert paused_job["enabled"] is False
    assert manager.get_scheduled_job(created.task_id) is not None

    resumed_record, resumed_job = manager.resume_task(created.task_id)
    assert resumed_record.state == TaskLifecycleState.ACTIVE
    assert resumed_job["enabled"] is True
    assert resumed_job["next_due_at"] is not None


def test_schedule_rolls_back_cron_job_when_lifecycle_insert_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(tmp_path)

    original_create = manager.lifecycle_repository.create

    def _boom(*args, **kwargs):  # type: ignore[no-untyped-def]
        del args, kwargs
        raise RuntimeError("insert failed")

    monkeypatch.setattr(manager.lifecycle_repository, "create", _boom)
    with pytest.raises(RuntimeError, match="insert failed"):
        manager.schedule_task(
            name="rollback",
            schedule={"kind": "every", "every_ms": 60_000},
            payload={"kind": "agentTurn", "message": "rollback"},
            agent_id="agent-a",
        )
    monkeypatch.setattr(manager.lifecycle_repository, "create", original_create)

    cron_jobs = getattr(manager, "_cron_repository").list_cron_jobs(limit=10)
    assert cron_jobs == []
    assert manager.lifecycle_repository.list(limit=10) == []


def test_legacy_services_task_surface_is_removed() -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("openminion.services.task")
