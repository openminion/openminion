from __future__ import annotations

import importlib
import json
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

from openminion.modules.session.storage.repository import create_sqlite_cron_repository
from openminion.modules.task import (
    TaskLifecycleRepository,
    TaskLifecycleState,
    TaskManager,
)
from openminion.modules.task.constants import (
    TASK_INTERNAL_PAUSE_REASON_KEY,
    TASK_INTERNAL_PAUSE_SOURCE_KEY,
)
from openminion.modules.task.scheduling.coordination import ExpiredOneShotTaskError
from openminion.modules.task.scheduling.schedule import to_iso_utc, utc_now


def _manager(tmp_path: Path) -> TaskManager:
    repo = create_sqlite_cron_repository(db_path=tmp_path / "sessions.db")
    return TaskManager.from_cron_repository(repo)


def test_task_manager_closes_only_owned_lifecycle_repository() -> None:
    close_calls: list[str] = []

    class LifecycleRepository:
        def close(self) -> None:
            close_calls.append("close")

    borrowed = TaskManager(
        cron_repository=object(),  # type: ignore[arg-type]
        lifecycle_repository=LifecycleRepository(),  # type: ignore[arg-type]
    )
    borrowed.close()
    assert close_calls == []

    owned = TaskManager(
        cron_repository=object(),  # type: ignore[arg-type]
        lifecycle_repository=LifecycleRepository(),  # type: ignore[arg-type]
        owns_lifecycle_repository=True,
    )
    owned.close()
    owned.close()
    assert close_calls == ["close"]


def test_resume_rejects_expired_one_time_task_without_mutation(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    record = manager.schedule_task(
        name="expired",
        schedule={"kind": "at", "at": "2000-01-01T00:00:00Z"},
        payload={"kind": "agentTurn", "message": "work"},
        enabled=False,
        agent_id="agent-a",
    )
    manager.transition_task(
        task_id=record.task_id,
        to_state=TaskLifecycleState.PAUSED,
    )

    with pytest.raises(ExpiredOneShotTaskError):
        manager.resume_task(record.task_id)

    assert manager.get_task(record.task_id).state == TaskLifecycleState.PAUSED
    assert manager.get_scheduled_job(record.cron_job_id)["enabled"] is False


def test_resume_clears_internal_pause_metadata(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    record = manager.schedule_task(
        name="recurring",
        schedule={"kind": "every", "every_ms": 60_000},
        payload={
            "kind": "agentTurn",
            "message": "work",
            TASK_INTERNAL_PAUSE_REASON_KEY: "legacy interval",
            TASK_INTERNAL_PAUSE_SOURCE_KEY: "migration",
        },
        agent_id="agent-a",
    )
    manager.pause_task(record.task_id)

    _, job = manager.resume_task(record.task_id)

    assert TASK_INTERNAL_PAUSE_REASON_KEY not in job["payload"]
    assert TASK_INTERNAL_PAUSE_SOURCE_KEY not in job["payload"]


def test_sqlite_cron_repository_closes_private_store_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    close_calls: list[str] = []

    class Store:
        def __init__(self, db_path: Path) -> None:
            self.db_path = db_path

        def close(self) -> None:
            close_calls.append("close")

    monkeypatch.setattr(
        "openminion.modules.session.storage.repository.SQLiteSessionStore",
        Store,
    )
    repository = create_sqlite_cron_repository(db_path=tmp_path / "cron.db")
    repository.close()

    assert close_calls == ["close"]


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


def test_cancel_marks_lifecycle_and_retains_disabled_cron_job(tmp_path: Path) -> None:
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
    job = manager.get_scheduled_job(created.cron_job_id)
    assert job is not None
    assert job["enabled"] is False

    repo_job = manager.get_task_by_job(created.cron_job_id)
    assert repo_job is not None
    assert repo_job.state == TaskLifecycleState.CANCELLED


def test_cancel_cancels_queued_run_without_deleting_history(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    created = manager.schedule_task(
        name="cancel-queued",
        schedule={"kind": "every", "every_ms": 60_000},
        payload={"kind": "agentTurn", "message": "cancel"},
        agent_id="agent-a",
    )
    run_id = getattr(manager, "_cron_repository").trigger_cron_run(created.cron_job_id)

    manager.cancel_task(created.task_id)

    runs = manager.list_scheduled_runs(job_id=created.cron_job_id, limit=10)
    assert runs[0]["run_id"] == run_id
    assert runs[0]["state"] == "cancelled"


def test_cancel_wins_when_completion_interleaves_with_job_disable(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    created = manager.schedule_task(
        name="cancel-race",
        schedule={"kind": "at", "at": to_iso_utc(utc_now() + timedelta(hours=1))},
        payload={"kind": "agentTurn", "message": "run"},
        agent_id="agent-a",
    )
    conn = getattr(manager, "_cron_repository")._store._conn
    conn.execute(
        """
        CREATE TRIGGER complete_task_on_job_disable
        AFTER UPDATE OF enabled ON cron_jobs
        WHEN NEW.enabled = 0
        BEGIN
          UPDATE scheduled_tasks
          SET state = 'done'
          WHERE cron_job_id = NEW.job_id AND state = 'active';
        END
        """
    )
    conn.commit()

    cancelled = manager.cancel_task(created.task_id)

    assert cancelled.state == TaskLifecycleState.CANCELLED
    job = manager.get_scheduled_job(created.cron_job_id)
    assert job is not None
    assert job["enabled"] is False


def test_cancel_uses_cron_repository_when_lifecycle_db_is_separate(
    tmp_path: Path,
) -> None:
    cron_repository = create_sqlite_cron_repository(db_path=tmp_path / "cron.db")
    manager = TaskManager(
        cron_repository=cron_repository,
        lifecycle_repository=TaskLifecycleRepository(db_path=tmp_path / "lifecycle.db"),
        owns_lifecycle_repository=True,
    )
    created = manager.schedule_task(
        name="separate-stores",
        schedule={"kind": "every", "every_ms": 60_000},
        payload={"kind": "agentTurn", "message": "run"},
        agent_id="agent-a",
    )
    run_id = cron_repository.trigger_cron_run(created.cron_job_id)

    cancelled = manager.cancel_task(created.task_id)

    assert cancelled.state == TaskLifecycleState.CANCELLED
    assert manager.get_scheduled_job(created.cron_job_id)["enabled"] is False
    runs = manager.list_scheduled_runs(job_id=created.cron_job_id, limit=10)
    assert runs[0]["run_id"] == run_id
    assert runs[0]["state"] == "cancelled"


def test_cancel_fails_closed_when_lifecycle_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(tmp_path)
    created = manager.schedule_task(
        name="cancel-compensation",
        schedule={"kind": "every", "every_ms": 60_000},
        payload={"kind": "agentTurn", "message": "run"},
        agent_id="agent-a",
    )
    run_id = getattr(manager, "_cron_repository").trigger_cron_run(created.cron_job_id)

    def _fail_cancel(**_kwargs) -> None:
        raise RuntimeError("lifecycle unavailable")

    monkeypatch.setattr(
        manager.lifecycle_repository,
        "cancel_scheduled_task",
        _fail_cancel,
    )

    with pytest.raises(RuntimeError, match="lifecycle unavailable"):
        manager.cancel_task(created.task_id)

    assert manager.get_task(created.task_id).state == TaskLifecycleState.ACTIVE
    assert manager.get_scheduled_job(created.cron_job_id)["enabled"] is False
    assert (
        manager.list_scheduled_runs(job_id=created.cron_job_id, limit=1)[0]["run_id"]
        == run_id
    )
    assert (
        manager.list_scheduled_runs(job_id=created.cron_job_id, limit=1)[0]["state"]
        == "queued"
    )


def test_cancel_failure_does_not_reenable_task_paused_concurrently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(tmp_path)
    created = manager.schedule_task(
        name="cancel-pause-race",
        schedule={"kind": "every", "every_ms": 60_000},
        payload={"kind": "agentTurn", "message": "run"},
        agent_id="agent-a",
    )

    def _pause_then_fail(**_kwargs) -> None:
        manager.lifecycle_repository.transition(
            task_id=created.task_id,
            to_state=TaskLifecycleState.PAUSED,
        )
        raise RuntimeError("cancel lost race")

    monkeypatch.setattr(
        manager.lifecycle_repository,
        "cancel_scheduled_task",
        _pause_then_fail,
    )

    with pytest.raises(RuntimeError, match="cancel lost race"):
        manager.cancel_task(created.task_id)

    assert manager.get_task(created.task_id).state == TaskLifecycleState.PAUSED
    assert manager.get_scheduled_job(created.cron_job_id)["enabled"] is False


def test_reconcile_uses_cron_repository_when_lifecycle_db_is_separate(
    tmp_path: Path,
) -> None:
    cron_repository = create_sqlite_cron_repository(db_path=tmp_path / "cron.db")
    manager = TaskManager(
        cron_repository=cron_repository,
        lifecycle_repository=TaskLifecycleRepository(db_path=tmp_path / "lifecycle.db"),
        owns_lifecycle_repository=True,
    )
    created = manager.schedule_task(
        name="separate-reconciliation",
        schedule={"kind": "at", "at": to_iso_utc(utc_now() + timedelta(hours=1))},
        payload={"kind": "agentTurn", "message": "run"},
        agent_id="agent-a",
    )
    run_id = cron_repository.trigger_cron_run(created.cron_job_id)
    cron_repository.finish_cron_run(run_id, state="finished")

    assert manager.reconcile_scheduled_outcomes() == 1
    assert manager.get_task(created.task_id).state == TaskLifecycleState.DONE


def test_one_time_terminal_outcome_updates_lifecycle_and_retains_job(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    created = manager.schedule_task(
        name="one-time",
        schedule={"kind": "at", "at": to_iso_utc(utc_now() + timedelta(hours=1))},
        payload={"kind": "agentTurn", "message": "run once"},
        agent_id="agent-a",
    )
    job = manager.get_scheduled_job(created.cron_job_id)
    assert job is not None
    assert job["delete_after_run"] is False
    run_id = getattr(manager, "_cron_repository").trigger_cron_run(created.cron_job_id)
    getattr(manager, "_cron_repository").finish_cron_run(
        run_id,
        state="finished",
        summary="complete",
    )

    assert manager.reconcile_scheduled_outcomes(created.cron_job_id) == 1
    record = manager.get_task(created.task_id)
    assert record is not None
    assert record.state == TaskLifecycleState.DONE
    assert record.metadata["last_run"]["run_id"] == run_id
    retained_job = manager.get_scheduled_job(created.cron_job_id)
    assert retained_job is not None
    assert retained_job["enabled"] is False
    assert manager.reconcile_scheduled_outcomes(created.cron_job_id) == 0

    manager.close()
    reopened = _manager(tmp_path)
    reopened_record = reopened.get_task(created.task_id)
    assert reopened_record is not None
    assert reopened_record.state == TaskLifecycleState.DONE


@pytest.mark.parametrize(
    ("schedule", "run_state", "expected_state"),
    [
        (
            {"kind": "at", "at": to_iso_utc(utc_now() + timedelta(hours=1))},
            "finished",
            TaskLifecycleState.DONE,
        ),
        (
            {"kind": "every", "every_ms": 60_000},
            "failed",
            TaskLifecycleState.PAUSED,
        ),
    ],
)
def test_paused_task_adopts_only_the_allowed_in_flight_outcome(
    tmp_path: Path,
    schedule: dict[str, object],
    run_state: str,
    expected_state: TaskLifecycleState,
) -> None:
    manager = _manager(tmp_path)
    created = manager.schedule_task(
        name="paused-in-flight",
        schedule=schedule,
        payload={"kind": "agentTurn", "message": "run"},
        agent_id="agent-a",
    )
    repository = getattr(manager, "_cron_repository")
    run_id = repository.trigger_cron_run(created.cron_job_id)
    repository.acquire_cron_runs("daemon-a", limit=1)
    manager.pause_task(created.task_id)
    repository.finish_cron_run(
        run_id,
        state=run_state,
        error=(
            {"code": "provider_failed", "message": "provider failed"}
            if run_state == "failed"
            else None
        ),
    )

    assert manager.reconcile_scheduled_outcomes(created.cron_job_id) == 1
    record = manager.get_task(created.task_id)
    assert record is not None
    assert record.state == expected_state
    assert record.metadata["last_run"]["run_id"] == run_id


def test_paused_queued_one_time_task_ignores_administrative_cancellation(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    created = manager.schedule_task(
        name="paused-before-start",
        schedule={"kind": "at", "at": to_iso_utc(utc_now() + timedelta(hours=1))},
        payload={"kind": "agentTurn", "message": "run"},
        agent_id="agent-a",
    )
    repository = getattr(manager, "_cron_repository")
    run_id = repository.trigger_cron_run(created.cron_job_id)

    manager.pause_task(created.task_id)

    assert manager.reconcile_scheduled_outcomes(created.cron_job_id) == 0
    record = manager.get_task(created.task_id)
    assert record is not None
    assert record.state == TaskLifecycleState.PAUSED
    assert "last_run" not in record.metadata
    run = manager.list_scheduled_runs(job_id=created.cron_job_id, limit=1)[0]
    assert run["run_id"] == run_id
    assert run["state"] == "cancelled"
    assert run["attempts"] == 0


def test_reconcile_completes_same_run_after_partial_legacy_write(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    created = manager.schedule_task(
        name="partial-outcome",
        schedule={"kind": "at", "at": to_iso_utc(utc_now() + timedelta(hours=1))},
        payload={"kind": "agentTurn", "message": "run"},
        agent_id="agent-a",
    )
    repository = getattr(manager, "_cron_repository")
    run_id = repository.trigger_cron_run(created.cron_job_id)
    repository.acquire_cron_runs("daemon-a", limit=1)
    repository.finish_cron_run(run_id, state="finished", summary="done")
    manager.update_task_metadata(
        task_id=created.task_id,
        metadata={
            "last_run": {
                "run_id": run_id,
                "state": "finished",
                "summary": "done",
            }
        },
    )

    assert manager.reconcile_scheduled_outcomes(created.cron_job_id) == 1
    record = manager.get_task(created.task_id)
    assert record is not None
    assert record.state == TaskLifecycleState.DONE


def test_cancelled_task_keeps_late_run_visible_without_reviving(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    created = manager.schedule_task(
        name="cancel-active",
        schedule={"kind": "every", "every_ms": 60_000},
        payload={"kind": "agentTurn", "message": "run"},
        agent_id="agent-a",
    )
    repository = getattr(manager, "_cron_repository")
    run_id = repository.trigger_cron_run(created.cron_job_id)
    repository.acquire_cron_runs("daemon-a", limit=1)
    manager.cancel_task(created.task_id)
    repository.finish_cron_run(run_id, state="finished", summary="late completion")

    assert manager.reconcile_scheduled_outcomes(created.cron_job_id) == 1
    record = manager.get_task(created.task_id)
    assert record is not None
    assert record.state == TaskLifecycleState.CANCELLED
    assert record.metadata["last_run"]["run_id"] == run_id
    assert record.metadata["last_run"]["summary"] == "late completion"
    assert (
        manager.list_scheduled_runs(job_id=created.cron_job_id, limit=1)[0]["state"]
        == "finished"
    )

    old_created_at = to_iso_utc(utc_now() - timedelta(days=8))
    repository._store._conn.execute(
        "UPDATE cron_runs SET created_at = ? WHERE run_id = ?",
        (old_created_at, run_id),
    )
    repository._store._conn.commit()
    assert repository._store.delete_old_cron_runs(to_iso_utc(utc_now())) == 1
    retained = manager.get_task(created.task_id)
    assert retained is not None
    assert retained.metadata["last_run"]["run_id"] == run_id


def test_terminal_metadata_survives_cron_run_pruning(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    created = manager.schedule_task(
        name="pruned-run",
        schedule={"kind": "at", "at": to_iso_utc(utc_now() + timedelta(hours=1))},
        payload={"kind": "agentTurn", "message": "run"},
        agent_id="agent-a",
    )
    repository = getattr(manager, "_cron_repository")
    run_id = repository.trigger_cron_run(created.cron_job_id)
    repository.finish_cron_run(run_id, state="finished", summary="retained")
    manager.reconcile_scheduled_outcomes(created.cron_job_id)

    old_created_at = to_iso_utc(utc_now() - timedelta(days=8))
    repository._store._conn.execute(
        "UPDATE cron_runs SET created_at = ? WHERE run_id = ?",
        (old_created_at, run_id),
    )
    repository._store._conn.commit()
    assert repository._store.delete_old_cron_runs(to_iso_utc(utc_now())) == 1

    record = manager.get_task(created.task_id)
    assert record is not None
    assert record.metadata["last_run"]["run_id"] == run_id
    assert record.metadata["last_run"]["summary"] == "retained"


def test_recurring_failure_stays_active_with_bounded_error(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    created = manager.schedule_task(
        name="recurring",
        schedule={"kind": "every", "every_ms": 60_000},
        payload={"kind": "agentTurn", "message": "repeat"},
        agent_id="agent-a",
    )
    run_id = getattr(manager, "_cron_repository").trigger_cron_run(created.cron_job_id)
    getattr(manager, "_cron_repository").finish_cron_run(
        run_id,
        state="failed",
        error={
            "code": "provider_failed",
            "message": "Bearer abcdefghijklmnopqrstuvwxyz " + "x" * 800,
            "details": {
                "authorization": "Bearer abcdefghijklmnopqrstuvwxyz",
                "nested": {"api_key": "sk-abcdefghijklmnopqrstuvwxyz"},
                "large": "y" * 10_000,
            },
        },
    )

    assert manager.reconcile_scheduled_outcomes(created.cron_job_id) == 1
    record = manager.get_task(created.task_id)
    assert record is not None
    assert record.state == TaskLifecycleState.ACTIVE
    assert record.metadata["last_run"]["last_error"]["code"] == "provider_failed"
    assert len(record.metadata["last_run"]["last_error"]["message"]) == 500
    persisted_error = record.metadata["last_run"]["last_error"]
    encoded_error = json.dumps(persisted_error)
    assert "abcdefghijklmnopqrstuvwxyz" not in encoded_error
    assert "[REDACTED]" in encoded_error
    assert len(encoded_error) <= 3_000


def test_stale_one_time_skip_records_failed_run_and_task(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    due_at = to_iso_utc(utc_now() - timedelta(hours=1))
    created = manager.schedule_task(
        name="missed",
        schedule={"kind": "at", "at": due_at},
        payload={"kind": "agentTurn", "message": "missed"},
        agent_id="agent-a",
        misfire_policy="skip",
        max_lateness_s=1,
    )

    getattr(manager, "_cron_repository").enqueue_due_cron_runs(
        "daemon-a",
        now_iso=to_iso_utc(utc_now()),
    )
    assert manager.reconcile_scheduled_outcomes(created.cron_job_id) == 1

    record = manager.get_task(created.task_id)
    assert record is not None
    assert record.state == TaskLifecycleState.FAILED
    assert record.failure_reason == "schedule_missed"
    runs = manager.list_scheduled_runs(job_id=created.cron_job_id, limit=10)
    assert len(runs) == 1
    assert runs[0]["attempts"] == 0
    assert runs[0]["error"]["code"] == "schedule_missed"


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


def test_pause_fails_closed_when_lifecycle_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(tmp_path)
    created = manager.schedule_task(
        name="pause-lifecycle-failure",
        schedule={"kind": "every", "every_ms": 60_000},
        payload={"kind": "agentTurn", "message": "run"},
        agent_id="agent-a",
    )
    run_id = getattr(manager, "_cron_repository").trigger_cron_run(created.cron_job_id)

    def _fail_transition(**_kwargs) -> None:
        raise RuntimeError("lifecycle unavailable")

    monkeypatch.setattr(manager, "transition_task", _fail_transition)

    with pytest.raises(RuntimeError, match="lifecycle unavailable"):
        manager.pause_task(created.task_id)

    assert manager.get_task(created.task_id).state == TaskLifecycleState.ACTIVE
    assert manager.get_scheduled_job(created.cron_job_id)["enabled"] is False
    assert (
        manager.list_scheduled_runs(job_id=created.cron_job_id, limit=1)[0]["run_id"]
        == run_id
    )
    assert (
        manager.list_scheduled_runs(job_id=created.cron_job_id, limit=1)[0]["state"]
        == "queued"
    )


def test_pause_failure_does_not_reenable_task_cancelled_concurrently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(tmp_path)
    created = manager.schedule_task(
        name="pause-cancel-race",
        schedule={"kind": "every", "every_ms": 60_000},
        payload={"kind": "agentTurn", "message": "run"},
        agent_id="agent-a",
    )

    def _cancel_then_fail(**_kwargs) -> None:
        manager.lifecycle_repository.cancel_scheduled_task(
            task_id=created.task_id,
            expected_state=TaskLifecycleState.ACTIVE,
        )
        raise RuntimeError("pause lost race")

    monkeypatch.setattr(manager, "transition_task", _cancel_then_fail)

    with pytest.raises(RuntimeError, match="pause lost race"):
        manager.pause_task(created.task_id)

    assert manager.get_task(created.task_id).state == TaskLifecycleState.CANCELLED
    assert manager.get_scheduled_job(created.cron_job_id)["enabled"] is False


def test_resume_cancels_queue_left_by_failed_pause_finalization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(tmp_path)
    created = manager.schedule_task(
        name="pause-finalization-failure",
        schedule={"kind": "every", "every_ms": 60_000},
        payload={"kind": "agentTurn", "message": "run"},
        agent_id="agent-a",
    )
    repository = getattr(manager, "_cron_repository")
    run_id = repository.trigger_cron_run(created.cron_job_id)
    original_set_enabled = repository.set_cron_job_enabled
    disable_calls = 0

    def _fail_second_disable(job_id, enabled, *, cancel_queued=False):
        nonlocal disable_calls
        if not enabled:
            disable_calls += 1
        if disable_calls == 2:
            raise RuntimeError("queue finalization failed")
        original_set_enabled(job_id, enabled, cancel_queued=cancel_queued)

    monkeypatch.setattr(repository, "set_cron_job_enabled", _fail_second_disable)

    with pytest.raises(RuntimeError, match="queue finalization failed"):
        manager.pause_task(created.task_id)

    assert manager.get_task(created.task_id).state == TaskLifecycleState.PAUSED
    assert (
        repository.list_cron_runs(job_id=created.cron_job_id, limit=1)[0]["state"]
        == "queued"
    )

    monkeypatch.setattr(repository, "set_cron_job_enabled", original_set_enabled)
    manager.resume_task(created.task_id)

    assert (
        repository.list_cron_runs(job_id=created.cron_job_id, limit=1)[0]["run_id"]
        == run_id
    )
    assert (
        repository.list_cron_runs(job_id=created.cron_job_id, limit=1)[0]["state"]
        == "cancelled"
    )
    assert repository.acquire_cron_runs("daemon-a", limit=1) == []


def test_resume_does_not_reenable_task_cancelled_during_resume(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(tmp_path)
    created = manager.schedule_task(
        name="resume-cancel-race",
        schedule={"kind": "every", "every_ms": 60_000},
        payload={"kind": "agentTurn", "message": "run"},
        agent_id="agent-a",
    )
    manager.pause_task(created.task_id)
    cron_repository = getattr(manager, "_cron_repository")
    original_set_enabled = cron_repository.set_cron_job_enabled
    cancelled = False

    def _set_enabled(job_id: str, enabled: bool, **kwargs) -> None:
        nonlocal cancelled
        original_set_enabled(job_id, enabled, **kwargs)
        if enabled and not cancelled:
            cancelled = True
            manager.cancel_task(created.task_id)

    monkeypatch.setattr(cron_repository, "set_cron_job_enabled", _set_enabled)

    with pytest.raises(ValueError, match="cancelled -> active"):
        manager.resume_task(created.task_id)

    assert manager.get_task(created.task_id).state == TaskLifecycleState.CANCELLED
    assert manager.get_scheduled_job(created.cron_job_id)["enabled"] is False


def test_resume_payload_failure_leaves_task_paused_and_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(tmp_path)
    created = manager.schedule_task(
        name="resume-payload-failure",
        schedule={"kind": "every", "every_ms": 60_000},
        payload={
            "kind": "agentTurn",
            "message": "run",
            TASK_INTERNAL_PAUSE_REASON_KEY: "legacy",
        },
        agent_id="agent-a",
    )
    manager.pause_task(created.task_id)

    def _fail_payload_write(*_args, **_kwargs) -> None:
        raise RuntimeError("write failed")

    monkeypatch.setattr(manager, "replace_cron_job_payload", _fail_payload_write)

    with pytest.raises(RuntimeError, match="write failed"):
        manager.resume_task(created.task_id)

    assert manager.get_task(created.task_id).state == TaskLifecycleState.PAUSED
    assert manager.get_scheduled_job(created.cron_job_id)["enabled"] is False


def test_resume_transition_failure_restores_pause_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(tmp_path)
    created = manager.schedule_task(
        name="resume-transition-failure",
        schedule={"kind": "every", "every_ms": 60_000},
        payload={
            "kind": "agentTurn",
            "message": "run",
            TASK_INTERNAL_PAUSE_REASON_KEY: "legacy",
            TASK_INTERNAL_PAUSE_SOURCE_KEY: "migration",
        },
        agent_id="agent-a",
    )
    manager.pause_task(created.task_id)

    def _fail_transition(**_kwargs) -> None:
        raise RuntimeError("lifecycle unavailable")

    monkeypatch.setattr(manager, "transition_task", _fail_transition)

    with pytest.raises(RuntimeError, match="lifecycle unavailable"):
        manager.resume_task(created.task_id)

    job = manager.get_scheduled_job(created.cron_job_id)
    assert job["enabled"] is False
    assert job["payload"][TASK_INTERNAL_PAUSE_REASON_KEY] == "legacy"
    assert job["payload"][TASK_INTERNAL_PAUSE_SOURCE_KEY] == "migration"


def test_resume_rejects_terminal_one_time_task_without_enabling_job(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    created = manager.schedule_task(
        name="completed-once",
        schedule={"kind": "at", "at": to_iso_utc(utc_now() + timedelta(hours=1))},
        payload={"kind": "agentTurn", "message": "run once"},
        agent_id="agent-a",
    )
    repository = getattr(manager, "_cron_repository")
    run_id = repository.trigger_cron_run(created.cron_job_id)
    repository.finish_cron_run(run_id, state="finished")
    manager.reconcile_scheduled_outcomes(created.cron_job_id)

    with pytest.raises(ValueError, match="invalid task state transition"):
        manager.resume_task(created.task_id)

    job = manager.get_scheduled_job(created.cron_job_id)
    assert job is not None
    assert job["enabled"] is False
    assert job["next_due_at"] is None


def test_reconcile_does_not_starve_older_unresolved_task(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    oldest = manager.schedule_task(
        name="oldest-unresolved",
        schedule={"kind": "at", "at": to_iso_utc(utc_now() + timedelta(hours=1))},
        payload={"kind": "agentTurn", "message": "run"},
        agent_id="agent-a",
    )
    repository = getattr(manager, "_cron_repository")
    run_id = repository.trigger_cron_run(oldest.cron_job_id)
    repository.finish_cron_run(run_id, state="finished")
    for index in range(100):
        newer = manager.schedule_task(
            name=f"newer-terminal-{index}",
            schedule={"kind": "every", "every_ms": 60_000},
            payload={"kind": "agentTurn", "message": "done"},
            agent_id="agent-a",
        )
        manager.transition_task(
            task_id=newer.task_id,
            to_state=TaskLifecycleState.DONE,
        )

    assert manager.reconcile_scheduled_outcomes(limit=100) == 1
    reconciled = manager.get_task(oldest.task_id)
    assert reconciled is not None
    assert reconciled.state == TaskLifecycleState.DONE


def test_reconcile_cursor_advances_past_tasks_without_terminal_runs(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    for index in range(100):
        manager.schedule_task(
            name=f"unresolved-{index}",
            schedule={"kind": "every", "every_ms": 60_000},
            payload={"kind": "agentTurn", "message": "wait"},
            agent_id="agent-a",
            job_id=f"a-{index:03d}",
        )
    target = manager.schedule_task(
        name="terminal-after-page",
        schedule={"kind": "at", "at": to_iso_utc(utc_now() + timedelta(hours=1))},
        payload={"kind": "agentTurn", "message": "run"},
        agent_id="agent-a",
        job_id="z-target",
    )
    repository = getattr(manager, "_cron_repository")
    run_id = repository.trigger_cron_run(target.cron_job_id)
    repository.finish_cron_run(run_id, state="finished")

    assert manager.reconcile_scheduled_outcomes(limit=1) == 0
    assert manager.reconcile_scheduled_outcomes(limit=1) == 1
    assert manager.get_task(target.task_id).state == TaskLifecycleState.DONE


def test_task_owned_one_time_job_is_retained_with_separate_databases(
    tmp_path: Path,
) -> None:
    cron_repository = create_sqlite_cron_repository(db_path=tmp_path / "cron.db")
    manager = TaskManager(
        cron_repository=cron_repository,
        lifecycle_repository=TaskLifecycleRepository(db_path=tmp_path / "lifecycle.db"),
        owns_lifecycle_repository=True,
    )
    created = manager.schedule_task(
        name="retained-one-time",
        schedule={"kind": "at", "at": to_iso_utc(utc_now() + timedelta(hours=1))},
        payload={"kind": "agentTurn", "message": "run"},
        agent_id="agent-a",
    )
    repository = getattr(manager, "_cron_repository")
    assert manager.get_scheduled_job(created.cron_job_id)["delete_after_run"] is False
    run_id = repository.trigger_cron_run(created.cron_job_id)
    repository.finish_cron_run(run_id, state="finished", summary="done")

    job = manager.get_scheduled_job(created.cron_job_id)
    assert job is not None
    assert job["enabled"] is False
    assert manager.reconcile_scheduled_outcomes(created.cron_job_id) == 1
    record = manager.get_task(created.task_id)
    assert record is not None
    assert record.state == TaskLifecycleState.DONE


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
