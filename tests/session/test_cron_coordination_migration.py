from __future__ import annotations

import sqlite3
from pathlib import Path

from openminion.modules.session.storage.migrations import run_migrations
from openminion.modules.session.storage.sqlite_store import SQLiteSessionStore


def test_cron_coordination_upgrade_preserves_pre_v2_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "session.db"
    store = SQLiteSessionStore(db_path)
    job_id = store.add_cron_job(
        name="legacy-job",
        schedule={"kind": "every", "every_ms": 60_000},
        payload={"kind": "agentTurn", "message": "legacy"},
        session_target="isolated",
    )
    run_id = store.trigger_cron_run(job_id)
    store.close()

    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("DROP TABLE cron_scope_state")
        conn.execute(
            """
            CREATE TABLE cron_jobs_legacy AS
            SELECT job_id, name, description, enabled, agent_id, schedule_json,
                   payload_json, delivery_json, session_target, wake_mode,
                   delete_after_run, misfire_policy, max_lateness_s,
                   max_concurrency, next_due_at, last_run_at, created_at, updated_at
            FROM cron_jobs
            """
        )
        conn.execute(
            """
            CREATE TABLE cron_runs_legacy AS
            SELECT run_id, job_id, state, due_at, started_at, finished_at,
                   isolated_session_id, summary, artifact_refs_json, error_json,
                   lease_owner, lease_expires_at, delivery_targets_json, attempts,
                   created_at, updated_at
            FROM cron_runs
            """
        )
        conn.execute("DROP TABLE cron_runs")
        conn.execute("DROP TABLE cron_jobs")
        conn.execute("ALTER TABLE cron_jobs_legacy RENAME TO cron_jobs")
        conn.execute("ALTER TABLE cron_runs_legacy RENAME TO cron_runs")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS alembic_version(version_num TEXT PRIMARY KEY)"
        )
        conn.execute("DELETE FROM alembic_version")
        conn.execute(
            "INSERT INTO alembic_version(version_num) VALUES ('0002_run_invocation')"
        )
        conn.commit()

    run_migrations(db_path)

    upgraded = SQLiteSessionStore(db_path)
    try:
        job = upgraded.get_cron_job(job_id)
        run = upgraded.list_cron_runs(job_id=job_id, limit=1)[0]
        assert job is not None
        assert job["concurrency_key"] is None
        assert job["max_attempts"] == 3
        assert job["retry_backoff_s"] == 30
        assert run["run_id"] == run_id
        assert run["available_at"] is None
        assert run["output"] == {}
        assert upgraded.get_cron_scope_state("legacy-scope") is None
    finally:
        upgraded.close()
