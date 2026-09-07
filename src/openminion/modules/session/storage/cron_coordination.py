from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from threading import RLock
from typing import Any

from openminion.modules.session.constants import MAX_CRON_RETRY_BACKOFF_SECONDS
from openminion.modules.storage.record_store import RecordStore
from openminion.modules.task.scheduling.schedule import (
    parse_iso_datetime,
    to_iso_utc,
    utc_now,
)

from .json_utils import parse_json, to_json
from .rows import row_to_cron_run


class CronCoordinationStore:
    """Persistence operations for cron retries and cross-job scope state."""

    _record_store: RecordStore
    _lock: RLock
    _query_one: Callable[
        [str, tuple[Any, ...] | list[Any] | None], dict[str, Any] | None
    ]
    _execute_count: Callable[[str, tuple[Any, ...] | list[Any] | None], int]

    @staticmethod
    def _retry_delay_seconds(*, attempts: int, base_backoff_s: int) -> int:
        multiplier = 2 ** max(0, attempts - 1)
        return min(base_backoff_s * multiplier, MAX_CRON_RETRY_BACKOFF_SECONDS)

    def _scope_has_running_run_locked(
        self,
        concurrency_key: str,
        *,
        excluding_run_id: str,
    ) -> bool:
        row = self._query_one(
            """
            SELECT COUNT(1) AS c
            FROM cron_runs AS r
            JOIN cron_jobs AS j ON j.job_id = r.job_id
            WHERE j.concurrency_key = ?
              AND r.state = 'running'
              AND r.run_id != ?
            """,
            (concurrency_key, excluding_run_id),
        )
        return bool(row and int(row["c"]) > 0)

    def _recover_expired_cron_runs_locked(
        self,
        *,
        now_dt: datetime,
        now_iso: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        rows = self._record_store.query_dicts(
            """
            SELECT r.*, j.max_attempts, j.retry_backoff_s
            FROM cron_runs AS r
            JOIN cron_jobs AS j ON j.job_id = r.job_id
            WHERE r.state = 'running'
              AND r.lease_expires_at IS NOT NULL
              AND r.lease_expires_at <= ?
            ORDER BY r.lease_expires_at ASC
            LIMIT ?
            """,
            (now_iso, max(1, min(limit, 1000))),
        )
        recovered: list[dict[str, Any]] = []
        for row in rows:
            run_id = str(row["run_id"])
            attempts = int(row["attempts"] or 0)
            attempt_limit = max(1, int(row["max_attempts"] or 3))
            if attempts >= attempt_limit:
                error = {
                    "code": "cron_lease_expired_max_attempts",
                    "message": "cron run lease expired after the final attempt",
                    "attempts": attempts,
                    "max_attempts": attempt_limit,
                }
                self._execute_count(
                    """
                    UPDATE cron_runs
                    SET state = 'failed', error_json = ?, lease_owner = NULL,
                        lease_expires_at = NULL, finished_at = ?, updated_at = ?
                    WHERE run_id = ? AND state = 'running'
                    """,
                    (to_json(error), now_iso, now_iso, run_id),
                )
                recovered.append(
                    {
                        "run_id": run_id,
                        "job_id": row["job_id"],
                        "state": "failed",
                        "attempts": attempts,
                        "error": error,
                    }
                )
                continue

            delay_s = self._retry_delay_seconds(
                attempts=attempts,
                base_backoff_s=max(1, int(row["retry_backoff_s"] or 30)),
            )
            available_at = to_iso_utc(now_dt + timedelta(seconds=delay_s))
            error = {
                "code": "cron_lease_expired_retry",
                "message": "cron run lease expired; delayed retry scheduled",
                "attempts": attempts,
                "max_attempts": attempt_limit,
                "retry_delay_s": delay_s,
            }
            self._execute_count(
                """
                UPDATE cron_runs
                SET state = 'queued', available_at = ?, started_at = NULL,
                    error_json = ?, lease_owner = NULL, lease_expires_at = NULL,
                    updated_at = ?
                WHERE run_id = ? AND state = 'running'
                """,
                (available_at, to_json(error), now_iso, run_id),
            )
            recovered.append(
                {
                    "run_id": run_id,
                    "job_id": row["job_id"],
                    "state": "queued",
                    "attempts": attempts,
                    "available_at": available_at,
                    "error": error,
                }
            )
        return recovered

    def recover_expired_cron_runs(
        self,
        *,
        now_iso: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        now_dt = parse_iso_datetime(now_iso) if now_iso else utc_now()
        normalized_now = to_iso_utc(now_dt)
        with self._lock, self._record_store.transaction():
            return self._recover_expired_cron_runs_locked(
                now_dt=now_dt,
                now_iso=normalized_now,
                limit=limit,
            )

    def retry_cron_run(
        self,
        run_id: str,
        *,
        error: dict[str, Any],
        now_iso: str | None = None,
    ) -> dict[str, Any] | None:
        rid = str(run_id or "").strip()
        if not rid:
            raise ValueError("run_id is required")
        now_dt = parse_iso_datetime(now_iso) if now_iso else utc_now()
        now = to_iso_utc(now_dt)
        with self._lock, self._record_store.transaction():
            row = self._query_one(
                """
                SELECT r.*, j.max_attempts, j.retry_backoff_s
                FROM cron_runs AS r
                LEFT JOIN cron_jobs AS j ON j.job_id = r.job_id
                WHERE r.run_id = ?
                """,
                (rid,),
            )
            if row is None or str(row["state"]) != "running":
                return None
            attempts = int(row["attempts"] or 0)
            attempt_limit = max(1, int(row.get("max_attempts") or 1))
            if attempts >= attempt_limit:
                terminal_error = dict(error)
                terminal_error.setdefault("attempts", attempts)
                terminal_error.setdefault("max_attempts", attempt_limit)
                self._execute_count(
                    """
                    UPDATE cron_runs
                    SET state = 'failed', error_json = ?, lease_owner = NULL,
                        lease_expires_at = NULL, finished_at = ?, updated_at = ?
                    WHERE run_id = ? AND state = 'running'
                    """,
                    (to_json(terminal_error), now, now, rid),
                )
            else:
                delay_s = self._retry_delay_seconds(
                    attempts=attempts,
                    base_backoff_s=max(1, int(row.get("retry_backoff_s") or 30)),
                )
                available_at = to_iso_utc(now_dt + timedelta(seconds=delay_s))
                retry_error = dict(error)
                retry_error.update(
                    {
                        "attempts": attempts,
                        "max_attempts": attempt_limit,
                        "retry_delay_s": delay_s,
                    }
                )
                self._execute_count(
                    """
                    UPDATE cron_runs
                    SET state = 'queued', available_at = ?, started_at = NULL,
                        error_json = ?, lease_owner = NULL, lease_expires_at = NULL,
                        updated_at = ?
                    WHERE run_id = ? AND state = 'running'
                    """,
                    (available_at, to_json(retry_error), now, rid),
                )
            updated = self._query_one(
                "SELECT * FROM cron_runs WHERE run_id = ?",
                (rid,),
            )
        return row_to_cron_run(updated) if updated is not None else None

    def get_cron_scope_state(self, concurrency_key: str) -> dict[str, Any] | None:
        key = str(concurrency_key or "").strip()
        if not key:
            raise ValueError("concurrency_key is required")
        with self._lock:
            row = self._query_one(
                "SELECT * FROM cron_scope_state WHERE concurrency_key = ?",
                (key,),
            )
        if row is None:
            return None
        return {
            "concurrency_key": str(row["concurrency_key"]),
            "last_success_run_id": str(row["last_success_run_id"]),
            "last_success_at": str(row["last_success_at"]),
            "watermark": parse_json(row["watermark_json"], {}),
            "updated_at": str(row["updated_at"]),
        }

    def _persist_scope_watermark_locked(
        self,
        *,
        row: dict[str, Any],
        state: str,
        output: dict[str, Any] | None,
        run_id: str,
        now_iso: str,
    ) -> None:
        coordination_key = str(row.get("concurrency_key") or "").strip()
        watermark = (output or {}).get("coordination_watermark")
        if (
            state != "finished"
            or not coordination_key
            or not isinstance(watermark, dict)
        ):
            return
        self._execute_count(
            """
            INSERT INTO cron_scope_state(
              concurrency_key, last_success_run_id, last_success_at,
              watermark_json, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(concurrency_key) DO UPDATE SET
              last_success_run_id = excluded.last_success_run_id,
              last_success_at = excluded.last_success_at,
              watermark_json = excluded.watermark_json,
              updated_at = excluded.updated_at
            """,
            (coordination_key, run_id, now_iso, to_json(watermark), now_iso),
        )

    def _finalize_one_time_job_locked(
        self,
        *,
        row: dict[str, Any],
        state: str,
        job_id: str,
        now_iso: str,
    ) -> None:
        schedule = parse_json(row["schedule_json"], {})
        if state != "finished" or str(schedule.get("kind")) != "at":
            return
        if bool(int(row["delete_after_run"] or 0)):
            self._execute_count("DELETE FROM cron_jobs WHERE job_id = ?", (job_id,))
            return
        self._execute_count(
            """
            UPDATE cron_jobs
            SET enabled = 0, next_due_at = NULL, updated_at = ?
            WHERE job_id = ?
            """,
            (now_iso, job_id),
        )
