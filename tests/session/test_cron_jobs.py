from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from openminion.modules.session.storage.sqlite_store import SQLiteSessionStore
from openminion.modules.task.constants import (
    TASK_INTERNAL_PAUSE_REASON_KEY,
    TASK_REASON_SCHEDULE_INTERVAL_TOO_SHORT,
)
from openminion.modules.task.scheduling.schedule import (
    parse_iso_datetime,
    to_iso_utc,
    utc_now,
)


@pytest.fixture
def store(tmp_path: Path):
    session_store = SQLiteSessionStore(tmp_path / "sessctl-cron.db")
    try:
        yield session_store
    finally:
        session_store.close()


def test_add_cron_job_defaults_and_constraints(store: SQLiteSessionStore) -> None:
    at_time = to_iso_utc(utc_now() + timedelta(minutes=5))
    job_id = store.add_cron_job(
        name="main reminder",
        schedule={"kind": "at", "at": at_time},
        payload={"kind": "systemEvent", "event_text": "Reminder"},
    )
    job = store.get_cron_job(job_id)
    assert job is not None
    assert job["session_target"] == "main"
    assert job["wake_mode"] == "now"
    assert job["delete_after_run"] is True
    assert job["delivery"]["mode"] == "none"
    assert job["concurrency_key"] is None
    assert job["max_attempts"] == 3
    assert job["retry_backoff_s"] == 30

    with pytest.raises(ValueError):
        store.add_cron_job(
            name="invalid",
            schedule={"kind": "every", "every_ms": 1000},
            payload={"kind": "agentTurn", "message": "hello"},
            session_target="main",
        )


def test_isolated_jobs_default_to_announce_delivery(
    store: SQLiteSessionStore,
) -> None:
    job_id = store.add_cron_job(
        name="isolated-default-delivery",
        schedule={"kind": "every", "every_ms": 60000},
        payload={"kind": "agentTurn", "message": "summarize"},
    )
    job = store.get_cron_job(job_id)
    assert job is not None
    assert job["session_target"] == "isolated"
    assert job["delivery"]["mode"] == "announce"
    assert job["delivery"]["channel"] == "last"
    assert job["delivery"]["to"] == "last"


def test_enqueue_acquire_and_finish_recurring_job(store: SQLiteSessionStore) -> None:
    job_id = store.add_cron_job(
        name="hourly summary",
        schedule={"kind": "every", "every_ms": 3600000},
        payload={"kind": "agentTurn", "message": "summarize updates"},
        session_target="isolated",
    )
    overdue = to_iso_utc(utc_now() - timedelta(seconds=5))
    store._conn.execute(
        "UPDATE cron_jobs SET next_due_at = ? WHERE job_id = ?",
        (overdue, job_id),
    )
    store._conn.commit()

    queued = store.enqueue_due_cron_runs("daemon-a", lease_ttl_s=30, max_jobs=10)
    assert len(queued) >= 1
    acquired = store.acquire_cron_runs("daemon-a", lease_ttl_s=30, limit=2)
    assert len(acquired) >= 1
    run = acquired[0]
    assert run["state"] == "running"
    assert store.renew_cron_run_lease(
        run["run_id"], daemon_id="daemon-a", lease_ttl_s=30
    )
    finished = store.finish_cron_run(run["run_id"], state="finished", summary="ok")
    assert finished is not None
    assert finished["state"] == "finished"
    assert finished["summary"] == "ok"

    refreshed_job = store.get_cron_job(job_id)
    assert refreshed_job is not None
    assert refreshed_job["enabled"] is True
    assert refreshed_job["next_due_at"] is not None


def test_one_shot_delete_after_run_and_disable_modes(
    store: SQLiteSessionStore,
) -> None:
    stale_at = to_iso_utc(utc_now() - timedelta(minutes=1))
    delete_job_id = store.add_cron_job(
        name="delete-on-success",
        schedule={"kind": "at", "at": stale_at},
        payload={"kind": "systemEvent", "event_text": "one shot"},
        delete_after_run=True,
    )
    store.enqueue_due_cron_runs("daemon-b", lease_ttl_s=30, max_jobs=10)
    first_run = store.acquire_cron_runs("daemon-b", lease_ttl_s=30, limit=5)[0]
    store.finish_cron_run(first_run["run_id"], state="finished", summary="done")
    assert store.get_cron_job(delete_job_id) is None

    deleted_run = [
        item
        for item in store.list_cron_runs(limit=20)
        if item["run_id"] == first_run["run_id"]
    ][0]
    assert deleted_run["job_id"] is None

    disable_job_id = store.add_cron_job(
        name="disable-on-success",
        schedule={"kind": "at", "at": stale_at},
        payload={"kind": "systemEvent", "event_text": "disable one shot"},
        delete_after_run=False,
    )
    store.enqueue_due_cron_runs("daemon-c", lease_ttl_s=30, max_jobs=10)
    second_run = store.acquire_cron_runs("daemon-c", lease_ttl_s=30, limit=5)[0]
    store.finish_cron_run(second_run["run_id"], state="finished", summary="done")
    disabled_job = store.get_cron_job(disable_job_id)
    assert disabled_job is not None
    assert disabled_job["enabled"] is False
    assert disabled_job["next_due_at"] is None


def test_misfire_skip_skips_stale_occurrence(store: SQLiteSessionStore) -> None:
    job_id = store.add_cron_job(
        name="skip-misfire",
        schedule={"kind": "every", "every_ms": 15_000},
        payload={"kind": "agentTurn", "message": "noop"},
        session_target="isolated",
        misfire_policy="skip",
        max_lateness_s=1,
    )
    stale_due = to_iso_utc(utc_now() - timedelta(minutes=3))
    store._conn.execute(
        "UPDATE cron_jobs SET next_due_at = ? WHERE job_id = ?",
        (stale_due, job_id),
    )
    store._conn.commit()

    queued = store.enqueue_due_cron_runs("daemon-d", lease_ttl_s=30, max_jobs=10)
    assert len(queued) <= 1
    refreshed = store.get_cron_job(job_id)
    assert refreshed is not None
    assert refreshed["next_due_at"] is not None
    assert refreshed["next_due_at"] != stale_due


def test_delivery_target_dedup(store: SQLiteSessionStore) -> None:
    job_id = store.add_cron_job(
        name="delivery",
        schedule={"kind": "every", "every_ms": 1000},
        payload={"kind": "agentTurn", "message": "deliver"},
        session_target="isolated",
    )
    run_id = store.trigger_cron_run(job_id)
    assert store.mark_cron_delivery_target(run_id, target="cli:ops") is True
    assert store.mark_cron_delivery_target(run_id, target="cli:ops") is False


def test_pause_resume_preserves_history_and_resumes_without_burst(
    store: SQLiteSessionStore,
) -> None:
    job_id = store.add_cron_job(
        name="resume-safe",
        schedule={"kind": "every", "every_ms": 60_000},
        payload={"kind": "agentTurn", "message": "resume-safe"},
        session_target="isolated",
    )
    initial_run_id = store.trigger_cron_run(job_id)
    store.finish_cron_run(initial_run_id, state="finished", summary="done")

    store.set_cron_job_enabled(job_id, False)
    paused = store.get_cron_job(job_id)
    assert paused is not None
    assert paused["enabled"] is False
    assert paused["next_due_at"] is None
    assert len(store.list_cron_runs(job_id=job_id, limit=10)) == 1

    store.set_cron_job_enabled(job_id, True)
    resumed = store.get_cron_job(job_id)
    assert resumed is not None
    assert resumed["enabled"] is True
    assert resumed["next_due_at"] is not None

    queued = store.enqueue_due_cron_runs("daemon-resume", lease_ttl_s=30, max_jobs=10)
    assert queued == []
    assert len(store.list_cron_runs(job_id=job_id, limit=10)) == 1


def test_legacy_short_interval_job_auto_pauses_before_dispatch(
    store: SQLiteSessionStore,
) -> None:
    job_id = store.add_cron_job(
        name="legacy-short",
        schedule={"kind": "every", "every_ms": 1_000},
        payload={"kind": "agentTurn", "message": "legacy-short"},
        session_target="isolated",
    )
    overdue = to_iso_utc(utc_now() - timedelta(seconds=5))
    store._conn.execute(
        "UPDATE cron_jobs SET next_due_at = ? WHERE job_id = ?",
        (overdue, job_id),
    )
    store._conn.commit()

    queued = store.enqueue_due_cron_runs("daemon-short", lease_ttl_s=30, max_jobs=10)
    assert queued == []
    job = store.get_cron_job(job_id)
    assert job is not None
    assert job["enabled"] is False
    assert job["next_due_at"] is None
    assert (
        job["payload"].get(TASK_INTERNAL_PAUSE_REASON_KEY)
        == TASK_REASON_SCHEDULE_INTERVAL_TOO_SHORT
    )


def test_expired_running_run_requeues_after_persisted_backoff(
    store: SQLiteSessionStore,
) -> None:
    job_id = store.add_cron_job(
        name="recoverable",
        schedule={"kind": "every", "every_ms": 60_000},
        payload={"kind": "agentTurn", "message": "recover"},
        session_target="isolated",
        max_attempts=3,
        retry_backoff_s=30,
    )
    run_id = store.trigger_cron_run(job_id)
    started_at = to_iso_utc(utc_now())
    acquired = store.acquire_cron_runs(
        "daemon-old", lease_ttl_s=1, limit=1, now_iso=started_at
    )
    assert acquired[0]["run_id"] == run_id

    recovery_at = to_iso_utc(utc_now() + timedelta(seconds=5))
    store._conn.execute(
        "UPDATE cron_runs SET lease_expires_at = ? WHERE run_id = ?",
        (to_iso_utc(utc_now() - timedelta(seconds=1)), run_id),
    )
    store._conn.commit()

    recovered = store.recover_expired_cron_runs(now_iso=recovery_at)
    assert recovered == [
        {
            "run_id": run_id,
            "job_id": job_id,
            "state": "queued",
            "attempts": 1,
            "available_at": to_iso_utc(
                parse_iso_datetime(recovery_at) + timedelta(seconds=30)
            ),
            "error": {
                "code": "cron_lease_expired_retry",
                "message": "cron run lease expired; delayed retry scheduled",
                "attempts": 1,
                "max_attempts": 3,
                "retry_delay_s": 30,
            },
        }
    ]
    assert store.acquire_cron_runs("daemon-new", limit=1, now_iso=recovery_at) == []
    retry_at = to_iso_utc(parse_iso_datetime(recovery_at) + timedelta(seconds=31))
    reacquired = store.acquire_cron_runs("daemon-new", limit=1, now_iso=retry_at)
    assert reacquired[0]["run_id"] == run_id
    assert reacquired[0]["attempts"] == 2


def test_expired_running_run_fails_after_attempt_limit(
    store: SQLiteSessionStore,
) -> None:
    job_id = store.add_cron_job(
        name="exhausted",
        schedule={"kind": "every", "every_ms": 60_000},
        payload={"kind": "agentTurn", "message": "fail"},
        session_target="isolated",
        max_attempts=1,
    )
    run_id = store.trigger_cron_run(job_id)
    store.acquire_cron_runs("daemon-old", lease_ttl_s=1, limit=1)
    store._conn.execute(
        "UPDATE cron_runs SET lease_expires_at = ? WHERE run_id = ?",
        (to_iso_utc(utc_now() - timedelta(seconds=1)), run_id),
    )
    store._conn.commit()

    recovered = store.recover_expired_cron_runs()
    assert recovered[0]["state"] == "failed"
    assert recovered[0]["error"]["code"] == "cron_lease_expired_max_attempts"
    persisted = store.list_cron_runs(job_id=job_id, limit=1)[0]
    assert persisted["state"] == "failed"


def test_worker_failure_uses_same_persisted_retry_policy(
    store: SQLiteSessionStore,
) -> None:
    job_id = store.add_cron_job(
        name="worker-retry",
        schedule={"kind": "every", "every_ms": 60_000},
        payload={"kind": "agentTurn", "message": "retry"},
        session_target="isolated",
        max_attempts=2,
        retry_backoff_s=10,
    )
    run_id = store.trigger_cron_run(job_id)
    started_at = to_iso_utc(utc_now())
    store.acquire_cron_runs("daemon-worker", limit=1, now_iso=started_at)
    failed_at = to_iso_utc(utc_now() + timedelta(seconds=1))
    retried = store.retry_cron_run(
        run_id,
        error={"code": "cron_failed", "message": "provider unavailable"},
        now_iso=failed_at,
    )

    assert retried is not None
    assert retried["state"] == "queued"
    assert retried["error"]["code"] == "cron_failed"
    assert retried["error"]["retry_delay_s"] == 10
    assert store.acquire_cron_runs("daemon-worker", limit=1, now_iso=failed_at) == []


def test_coordination_key_excludes_other_jobs_in_same_scope(
    store: SQLiteSessionStore,
) -> None:
    overdue = to_iso_utc(utc_now() - timedelta(seconds=5))
    job_ids = [
        store.add_cron_job(
            name=f"scope-{index}",
            schedule={"kind": "every", "every_ms": 60_000},
            payload={"kind": "agentTurn", "message": "consolidate"},
            session_target="isolated",
            concurrency_key=(
                "memory-consolidation:agent:a"
                if index < 2
                else "memory-consolidation:agent:b"
            ),
        )
        for index in range(3)
    ]
    store._conn.executemany(
        "UPDATE cron_jobs SET next_due_at = ? WHERE job_id = ?",
        [(overdue, job_id) for job_id in job_ids],
    )
    store._conn.commit()

    queued = store.enqueue_due_cron_runs("daemon-scope", max_jobs=10)
    assert len(queued) == 2
    queued_job_ids = {item["job_id"] for item in queued}
    assert job_ids[2] in queued_job_ids
    assert len(queued_job_ids & set(job_ids[:2])) == 1

    with pytest.raises(ValueError, match="coordination scope is busy"):
        store.trigger_cron_run(job_ids[1])


def test_successful_run_persists_scope_watermark_atomically(
    store: SQLiteSessionStore,
) -> None:
    key = "memory-consolidation:agent:a"
    job_id = store.add_cron_job(
        name="watermark",
        schedule={"kind": "every", "every_ms": 60_000},
        payload={"kind": "agentTurn", "message": "consolidate"},
        session_target="isolated",
        concurrency_key=key,
    )
    run_id = store.trigger_cron_run(job_id)
    store.acquire_cron_runs("daemon-watermark", limit=1)
    watermark = {
        "target_scope": "agent:a",
        "candidate_ids": ["cand-1", "cand-2"],
        "state_hash": "abc123",
        "completed_at": "2026-08-22T12:00:00+00:00",
    }
    finished = store.finish_cron_run(
        run_id,
        state="finished",
        output={"coordination_watermark": watermark},
    )

    assert finished is not None
    assert finished["output"]["coordination_watermark"] == watermark
    scope_state = store.get_cron_scope_state(key)
    assert scope_state is not None
    assert scope_state["last_success_run_id"] == run_id
    assert scope_state["watermark"] == watermark

    failed_run = store.trigger_cron_run(job_id)
    store.acquire_cron_runs("daemon-watermark", limit=1)
    store.finish_cron_run(
        failed_run,
        state="failed",
        output={"coordination_watermark": {**watermark, "candidate_ids": ["wrong"]}},
    )
    assert store.get_cron_scope_state(key) == scope_state
