import pytest

from openminion.modules.session.storage.repository import create_sqlite_cron_repository
from openminion.modules.task import TaskManager
from openminion.modules.task.errors import TaskControlError
from openminion.modules.task.scheduling.coordination import (
    recover_and_acquire_cron_runs,
)


def _manager(tmp_path) -> TaskManager:
    return TaskManager.from_cron_repository(
        create_sqlite_cron_repository(db_path=tmp_path / "tasks.db")
    )


def _scheduled(manager: TaskManager):
    created = manager.schedule_user_task(
        name="daily check",
        instruction="check status",
        schedule={"kind": "every", "every_ms": 60_000},
        agent_id="agent-a",
    )
    return created["record"], created["job"]


def test_run_now_enforces_active_limit_and_preserves_recurrence(tmp_path) -> None:
    manager = _manager(tmp_path)
    record, before = _scheduled(manager)

    run = manager.run_task_now(record.task_id)

    assert run["state"] == "queued"
    assert (
        manager.get_scheduled_job(record.cron_job_id)["next_due_at"]
        == before["next_due_at"]
    )
    with pytest.raises(TaskControlError) as raised:
        manager.run_task_now(record.task_id)
    assert raised.value.code == "task_busy"


def test_update_uses_revision_and_retains_history(tmp_path) -> None:
    manager = _manager(tmp_path)
    record, _ = _scheduled(manager)
    run = manager.run_task_now(record.task_id)
    repository = manager._cron_repository
    repository.finish_cron_run(run["run_id"], state="finished", summary="done")
    current = manager.get_scheduled_job(record.cron_job_id)
    assert current is not None

    updated = manager.update_scheduled_task(
        record.task_id,
        instruction="check status and report",
        schedule={"kind": "every", "every_ms": 120_000},
        expected_updated_at=current["updated_at"],
    )

    assert updated["payload"]["message"] == "check status and report"
    assert updated["schedule"]["kind"] == "every"
    assert updated["schedule"]["every_ms"] == 120_000
    assert len(manager.list_scheduled_runs(job_id=record.cron_job_id)) == 1
    with pytest.raises(TaskControlError) as raised:
        manager.update_scheduled_task(
            record.task_id,
            instruction="stale",
            schedule={"kind": "every", "every_ms": 180_000},
            expected_updated_at=current["updated_at"],
        )
    assert raised.value.code == "task_revision_conflict"


@pytest.mark.parametrize(
    "private_key",
    ["_openminion_watch", "_openminion_memory_consolidation"],
)
def test_update_rejects_specialized_task_payloads(tmp_path, private_key: str) -> None:
    manager = _manager(tmp_path)
    record = manager.schedule_task(
        name="specialized",
        schedule={"kind": "every", "every_ms": 60_000},
        payload={
            "kind": "agentTurn",
            "message": "specialized task",
            private_key: {"enabled": True},
        },
        agent_id="agent-a",
    )
    current = manager.get_scheduled_job(record.cron_job_id)
    assert current is not None

    with pytest.raises(TaskControlError) as raised:
        manager.update_scheduled_task(
            record.task_id,
            instruction="replacement",
            schedule={"kind": "every", "every_ms": 120_000},
            expected_updated_at=current["updated_at"],
        )

    assert raised.value.code == "task_unsupported_kind"
    unchanged = manager.get_scheduled_job(record.cron_job_id)
    assert unchanged is not None
    assert unchanged["payload"] == current["payload"]
    assert unchanged["schedule"] == current["schedule"]


def test_history_deletion_removes_terminal_rows_only(tmp_path) -> None:
    manager = _manager(tmp_path)
    record, _ = _scheduled(manager)
    finished = manager.run_task_now(record.task_id)
    manager._cron_repository.finish_cron_run(
        finished["run_id"], state="finished", summary="done"
    )
    queued = manager.run_task_now(record.task_id)

    assert manager.delete_terminal_run_history(record.task_id) == 1
    runs = manager.list_scheduled_runs(job_id=record.cron_job_id)
    assert [(run["run_id"], run["state"]) for run in runs] == [
        (queued["run_id"], "queued")
    ]


def test_scheduler_control_is_persisted_and_revision_guarded(tmp_path) -> None:
    manager = _manager(tmp_path)
    initial = manager.scheduler_control_status()

    paused = manager.pause_scheduler(
        reason="maintenance",
        expected_updated_at=initial["updated_at"],
        actor="agent-a",
    )

    assert paused["paused"] is True
    assert paused["reason"] == "maintenance"
    assert paused["actor"] == "agent-a"
    with pytest.raises(TaskControlError) as raised:
        manager.resume_scheduler(
            expected_updated_at=initial["updated_at"], actor="agent-a"
        )
    assert raised.value.code == "scheduler_revision_conflict"
    resumed = manager.resume_scheduler(
        expected_updated_at=paused["updated_at"], actor="agent-a"
    )
    assert resumed["paused"] is False


def test_scheduler_pause_prevents_new_enqueue_and_acquisition(tmp_path) -> None:
    manager = _manager(tmp_path)
    record, _ = _scheduled(manager)
    initial = manager.scheduler_control_status()
    manager.pause_scheduler(
        reason="maintenance",
        expected_updated_at=initial["updated_at"],
        actor="agent-a",
    )
    events: list[str] = []

    acquired = recover_and_acquire_cron_runs(
        store=manager._cron_repository,
        daemon_id="daemon-a",
        lease_ttl_seconds=30,
        capacity=1,
        can_start_background_work=lambda: True,
        emit=lambda event, _payload: events.append(event),
    )

    assert acquired == []
    assert manager.list_scheduled_runs(job_id=record.cron_job_id) == []
    assert "cron.scheduler.paused" in events


def test_scheduler_pause_rejects_manual_run_now(tmp_path) -> None:
    manager = _manager(tmp_path)
    record, _ = _scheduled(manager)
    initial = manager.scheduler_control_status()
    manager.pause_scheduler(
        reason="maintenance",
        expected_updated_at=initial["updated_at"],
        actor="agent-a",
    )

    with pytest.raises(TaskControlError) as raised:
        manager.run_task_now(record.task_id)

    assert raised.value.code == "scheduler_paused"
    assert manager.list_scheduled_runs(job_id=record.cron_job_id) == []
