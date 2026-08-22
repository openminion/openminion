from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from threading import Event, RLock
from time import sleep

from openminion.modules.session.storage.sqlite_store import SQLiteSessionStore
from openminion.modules.task.scheduling.schedule import to_iso_utc, utc_now
from openminion.services.cron import CronScheduler


def test_expired_consolidation_yields_then_persists_scope_watermark(
    tmp_path: Path,
) -> None:
    store = SQLiteSessionStore(tmp_path / "session.db")
    key = "memory-consolidation:agent:agent-a"
    first_job_id = store.add_cron_job(
        name="consolidate-a-1",
        schedule={"kind": "every", "every_ms": 60_000},
        payload={"kind": "agentTurn", "message": "consolidate"},
        session_target="isolated",
        delivery={"mode": "none"},
        concurrency_key=key,
        retry_backoff_s=1,
    )
    second_job_id = store.add_cron_job(
        name="consolidate-a-2",
        schedule={"kind": "every", "every_ms": 60_000},
        payload={"kind": "agentTurn", "message": "consolidate"},
        session_target="isolated",
        delivery={"mode": "none"},
        concurrency_key=key,
        retry_backoff_s=1,
    )
    run_id = store.trigger_cron_run(first_job_id)
    store.acquire_cron_runs("dead-daemon", lease_ttl_s=1, limit=1)
    overdue = to_iso_utc(utc_now() - timedelta(seconds=5))
    store._conn.execute(
        "UPDATE cron_runs SET lease_expires_at = ? WHERE run_id = ?",
        (overdue, run_id),
    )
    store._conn.execute(
        "UPDATE cron_jobs SET next_due_at = ? WHERE job_id = ?",
        (overdue, second_job_id),
    )
    store._conn.commit()

    foreground_clear = Event()
    finished = Event()
    events: list[str] = []
    active = 0
    max_active = 0
    lock = RLock()
    watermark = {
        "target_scope": "agent:agent-a",
        "candidate_ids": ["cand-1", "cand-2"],
        "state_hash": "state-123",
        "completed_at": "2026-08-22T12:00:00+00:00",
    }

    def _execute(_job: dict, _run: dict) -> dict:
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        try:
            return {
                "summary": "consolidated",
                "output": {"coordination_watermark": watermark},
            }
        finally:
            with lock:
                active -= 1

    def _on_event(event_type: str, _payload: dict) -> None:
        events.append(event_type)
        if event_type == "cron.run.finished":
            finished.set()

    scheduler = CronScheduler(
        store=store,
        daemon_id="live-daemon",
        tick_seconds=0.02,
        lease_ttl_seconds=2,
        max_concurrent_runs=2,
        execute_agent_turn=_execute,
        can_start_background_work=foreground_clear.is_set,
        on_event=_on_event,
    )
    scheduler.start()
    try:
        sleep(0.1)
        assert "cron.run.lease_recovered" in events
        assert "cron.scheduler.foreground_deferred" in events
        assert not finished.is_set()
        foreground_clear.set()
        assert finished.wait(timeout=4.0)
    finally:
        scheduler.shutdown(grace_s=1.0)

    persisted = store.list_cron_runs(job_id=first_job_id, limit=1)[0]
    scope_state = store.get_cron_scope_state(key)
    assert persisted["run_id"] == run_id
    assert persisted["state"] == "finished"
    assert persisted["attempts"] == 2
    assert persisted["output"]["coordination_watermark"] == watermark
    assert scope_state is not None
    assert scope_state["watermark"] == watermark
    assert max_active == 1
    store.close()
