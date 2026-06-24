from __future__ import annotations

from pathlib import Path

from openminion.modules.session.interfaces import ensure_cron_repository_compatibility
from openminion.modules.session.storage.repository import create_sqlite_cron_repository


def test_sqlite_cron_repository_round_trip(tmp_path: Path) -> None:
    repo = create_sqlite_cron_repository(db_path=tmp_path / "sessions.db")
    job_id = repo.add_cron_job(
        name="test-job",
        schedule={"kind": "every", "every_ms": 300000},
        payload={"kind": "agentTurn", "message": "hello"},
        agent_id="agent-adapter",
        session_target="isolated",
    )
    loaded = repo.get_cron_job(job_id)
    jobs = repo.list_cron_jobs(limit=10)
    runs = repo.list_cron_runs(job_id=job_id, limit=5)

    assert job_id
    assert loaded is not None
    assert loaded.get("job_id") == job_id
    assert any(item.get("job_id") == job_id for item in jobs)
    assert isinstance(runs, list)


def test_sqlite_cron_repository_is_contract_compatible(tmp_path: Path) -> None:
    repo = create_sqlite_cron_repository(db_path=tmp_path / "sessions.db")
    ensure_cron_repository_compatibility(repo)
