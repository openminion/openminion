from pathlib import Path
from typing import Any
from collections.abc import Mapping

from openminion.modules.task.scheduling.interfaces import CRON_INTERFACE_VERSION
from ..interfaces import (
    SESSION_REPOSITORY_INTERFACE_VERSION,
    ensure_cron_repository_compatibility,
)
from .sqlite_store import SQLiteSessionStore


class SQLiteCronRepository:
    """Cron repository adapter backed by SQLite session store."""

    contract_version = CRON_INTERFACE_VERSION
    repository_contract_version = SESSION_REPOSITORY_INTERFACE_VERSION

    def __init__(self, *, db_path: str | Path) -> None:
        self.db_path = Path(db_path).expanduser().resolve(strict=False)
        self._store = SQLiteSessionStore(self.db_path)

    def add_cron_job(
        self,
        *,
        name: str,
        schedule: Mapping[str, Any],
        payload: Mapping[str, Any],
        description: str | None = None,
        enabled: bool = True,
        agent_id: str | None = None,
        session_target: str | None = None,
        wake_mode: str | None = None,
        delivery: Mapping[str, Any] | None = None,
        delete_after_run: bool | None = None,
        misfire_policy: str | Mapping[str, Any] | None = None,
        max_lateness_s: int = 600,
        max_concurrency: int = 1,
        job_id: str | None = None,
    ) -> str:
        return self._store.add_cron_job(
            name=name,
            schedule=schedule,
            payload=payload,
            description=description,
            enabled=enabled,
            agent_id=agent_id,
            session_target=session_target,
            wake_mode=wake_mode,
            delivery=delivery,
            delete_after_run=delete_after_run,
            misfire_policy=misfire_policy,
            max_lateness_s=max_lateness_s,
            max_concurrency=max_concurrency,
            job_id=job_id,
        )

    def get_cron_job(self, job_id: str) -> dict[str, Any] | None:
        return self._store.get_cron_job(job_id)

    def list_cron_jobs(
        self, *, enabled: bool | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        return self._store.list_cron_jobs(enabled=enabled, limit=limit)

    def delete_cron_job(self, job_id: str) -> None:
        self._store.delete_cron_job(job_id)

    def replace_cron_job_payload(
        self,
        job_id: str,
        payload: Mapping[str, Any],
    ) -> None:
        self._store.replace_cron_job_payload(job_id, payload)

    def set_cron_job_enabled(self, job_id: str, enabled: bool) -> None:
        self._store.set_cron_job_enabled(job_id, enabled)

    def trigger_cron_run(
        self,
        job_id: str,
        *,
        due_at: str | None = None,
        lease_owner: str | None = None,
        lease_ttl_s: int = 60,
    ) -> str:
        return self._store.trigger_cron_run(
            job_id,
            due_at=due_at,
            lease_owner=lease_owner,
            lease_ttl_s=lease_ttl_s,
        )

    def list_cron_runs(
        self,
        *,
        job_id: str | None = None,
        limit: int = 100,
        states: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        return self._store.list_cron_runs(job_id=job_id, limit=limit, states=states)

    def enqueue_due_cron_runs(
        self,
        daemon_id: str,
        *,
        lease_ttl_s: int = 60,
        max_jobs: int = 50,
        now_iso: str | None = None,
    ) -> list[dict[str, Any]]:
        return self._store.enqueue_due_cron_runs(
            daemon_id,
            lease_ttl_s=lease_ttl_s,
            max_jobs=max_jobs,
            now_iso=now_iso,
        )

    def acquire_cron_runs(
        self,
        daemon_id: str,
        *,
        lease_ttl_s: int = 60,
        limit: int = 10,
        now_iso: str | None = None,
    ) -> list[dict[str, Any]]:
        return self._store.acquire_cron_runs(
            daemon_id,
            lease_ttl_s=lease_ttl_s,
            limit=limit,
            now_iso=now_iso,
        )

    def renew_cron_run_lease(
        self,
        run_id: str,
        *,
        daemon_id: str,
        lease_ttl_s: int = 60,
        now_iso: str | None = None,
    ) -> bool:
        return self._store.renew_cron_run_lease(
            run_id,
            daemon_id=daemon_id,
            lease_ttl_s=lease_ttl_s,
            now_iso=now_iso,
        )

    def finish_cron_run(
        self,
        run_id: str,
        *,
        state: str,
        summary: str | None = None,
        artifact_refs: list[dict[str, Any]] | None = None,
        error: dict[str, Any] | None = None,
        isolated_session_id: str | None = None,
        now_iso: str | None = None,
    ) -> dict[str, Any] | None:
        return self._store.finish_cron_run(
            run_id,
            state=state,
            summary=summary,
            artifact_refs=artifact_refs,
            error=error,
            isolated_session_id=isolated_session_id,
            now_iso=now_iso,
        )


def create_sqlite_cron_repository(*, db_path: str | Path) -> SQLiteCronRepository:
    repo = SQLiteCronRepository(db_path=db_path)
    ensure_cron_repository_compatibility(repo)
    return repo
