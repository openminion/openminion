from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from openminion.services.cron.scheduling import to_iso_utc, utc_now
from openminion.modules.session.storage.sqlite_store import SQLiteSessionStore


@pytest.fixture()
def store(tmp_path: Path) -> SQLiteSessionStore:
    db_path = tmp_path / "sess-cron.db"
    session_store = SQLiteSessionStore(db_path)
    yield session_store
    session_store.close()


def _make_overdue_job(store: SQLiteSessionStore) -> str:
    job_id = store.add_cron_job(
        name="cron-test",
        schedule={"kind": "every", "every_ms": 60000},
        payload={"kind": "agentTurn", "message": "ping"},
        session_target="isolated",
    )
    overdue = to_iso_utc(utc_now() - timedelta(seconds=5))
    store._conn.execute(
        "UPDATE cron_jobs SET next_due_at = ? WHERE job_id = ?",
        (overdue, job_id),
    )
    store._conn.commit()
    return job_id


def test_enqueue_acquire_renew_finish_and_delete_old(store: SQLiteSessionStore) -> None:
    job_id = _make_overdue_job(store)

    queued = store.enqueue_due_cron_runs("daemon-a", lease_ttl_s=15, max_jobs=10)
    assert queued

    acquired = store.acquire_cron_runs("daemon-a", lease_ttl_s=15, limit=5)
    assert acquired
    run_id = acquired[0]["run_id"]

    assert store.renew_cron_run_lease(run_id, daemon_id="daemon-a", lease_ttl_s=15)

    finished = store.finish_cron_run(run_id, state="finished", summary="ok")
    assert finished is not None
    assert finished["state"] == "finished"

    # Delete completed runs with cutoff in the future.
    cutoff = to_iso_utc(utc_now() + timedelta(days=1))
    deleted = store.delete_old_cron_runs(cutoff)
    assert deleted >= 1

    refreshed_job = store.get_cron_job(job_id)
    assert refreshed_job is not None


def test_acquire_cron_runs_handles_expired_lease(store: SQLiteSessionStore) -> None:
    job_id = _make_overdue_job(store)
    run_id = store.trigger_cron_run(job_id, lease_owner="daemon-old", lease_ttl_s=1)

    expired = to_iso_utc(utc_now() - timedelta(seconds=5))
    store._conn.execute(
        "UPDATE cron_runs SET lease_expires_at = ? WHERE run_id = ?",
        (expired, run_id),
    )
    store._conn.commit()

    acquired = store.acquire_cron_runs("daemon-new", lease_ttl_s=10, limit=5)
    assert any(run["run_id"] == run_id for run in acquired)
