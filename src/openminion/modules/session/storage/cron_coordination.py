from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from threading import RLock
from typing import Any, Protocol, cast
from uuid import uuid4

from openminion.modules.session.constants import MAX_CRON_RETRY_BACKOFF_SECONDS
from openminion.modules.storage.record_store import RecordStore
from openminion.modules.task.scheduling.schedule import (
    compute_next_due,
    normalize_schedule,
    parse_iso_datetime,
    to_iso_utc,
    utc_now,
)
from openminion.modules.task.constants import (
    TASK_INTERNAL_PAUSE_REASON_KEY,
    TASK_INTERNAL_PAUSE_SOURCE_KEY,
    TASK_REASON_BUSY,
    TASK_REASON_NOT_ACTIVE,
    TASK_REASON_PAST_DUE,
    TASK_REASON_REVISION_CONFLICT,
    TASK_REASON_SCHEDULE_INTERVAL_TOO_SHORT,
    TASK_REASON_SCHEDULER_PAUSED,
    TASK_REASON_SCHEDULER_REVISION_CONFLICT,
    TASK_REASON_UNSUPPORTED_KIND,
)
from openminion.modules.task.errors import TaskControlError

from .json_utils import parse_json, to_json
from .rows import row_to_cron_job, row_to_cron_run


def _initial_next_due(
    schedule: Mapping[str, Any], *, now: datetime, job_id: str
) -> str | None:
    if str(schedule["kind"]) == "at":
        return str(schedule["at"])
    next_due = compute_next_due(
        schedule=schedule,
        after=now,
        job_id=job_id,
        last_due=None,
    )
    return to_iso_utc(next_due) if next_due is not None else None


class CronCoordinationStore:
    """Persistence operations for cron retries and cross-job scope state."""

    _record_store: RecordStore
    _lock: RLock

    class _QueryOne(Protocol):
        def __call__(
            self,
            sql: str,
            params: tuple[Any, ...] | list[Any] | None = None,
        ) -> dict[str, Any] | None: ...

    _query_one: _QueryOne
    _execute_count: Callable[[str, tuple[Any, ...] | list[Any] | None], int]

    def update_task_cron_job(
        self,
        job_id: str,
        *,
        instruction: str,
        schedule: Mapping[str, Any],
        expected_updated_at: str,
    ) -> dict[str, Any]:
        jid = str(job_id or "").strip()
        message = str(instruction or "").strip()
        expected = str(expected_updated_at or "").strip()
        if not jid or not message or not expected:
            raise ValueError(
                "job_id, instruction, and expected_updated_at are required"
            )
        normalized_schedule = normalize_schedule(schedule)
        now_dt = utc_now()
        if (
            normalized_schedule["kind"] == "at"
            and parse_iso_datetime(str(normalized_schedule["at"])) <= now_dt
        ):
            raise TaskControlError(
                TASK_REASON_PAST_DUE,
                "one-time task schedule must be in the future",
                task_id=jid,
            )
        now = to_iso_utc(now_dt)
        with self._lock, self._record_store.transaction():
            row = self._query_one("SELECT * FROM cron_jobs WHERE job_id = ?", (jid,))
            if row is None:
                raise ValueError(f"cron job not found: {jid}")
            payload = parse_json(row["payload_json"], {})
            if (
                str(payload.get("kind") or "") != "agentTurn"
                or "_openminion_watch" in payload
                or "_openminion_memory_consolidation" in payload
            ):
                raise TaskControlError(
                    TASK_REASON_UNSUPPORTED_KIND,
                    "only plain agentTurn tasks can be updated",
                    task_id=jid,
                )
            active = self._query_one(
                """
                SELECT COUNT(1) AS c FROM cron_runs
                WHERE job_id = ? AND state IN ('queued', 'running')
                """,
                (jid,),
            )
            if active is not None and int(active["c"]) > 0:
                raise TaskControlError(
                    TASK_REASON_BUSY,
                    "task has queued or running work",
                    task_id=jid,
                )
            payload["message"] = message
            enabled = bool(row["enabled"])
            next_due = (
                _initial_next_due(normalized_schedule, now=now_dt, job_id=jid)
                if enabled
                else None
            )
            updated = self._execute_count(
                """
                UPDATE cron_jobs
                SET schedule_json = ?, payload_json = ?, next_due_at = ?, updated_at = ?
                WHERE job_id = ? AND updated_at = ?
                """,
                (
                    to_json(normalized_schedule),
                    to_json(payload),
                    next_due,
                    now,
                    jid,
                    expected,
                ),
            )
            if updated <= 0:
                raise TaskControlError(
                    TASK_REASON_REVISION_CONFLICT,
                    "task revision changed; refresh and retry",
                    task_id=jid,
                )
            refreshed = self._query_one(
                "SELECT * FROM cron_jobs WHERE job_id = ?", (jid,)
            )
        if refreshed is None:
            raise ValueError(f"cron job not found: {jid}")
        return row_to_cron_job(refreshed)

    def trigger_task_run_now(self, job_id: str) -> dict[str, Any]:
        jid = str(job_id or "").strip()
        if not jid:
            raise ValueError("job_id is required")
        now_dt = utc_now()
        now = to_iso_utc(now_dt)
        with self._lock, self._record_store.transaction():
            control = self._query_one(
                "SELECT paused FROM cron_scheduler_control WHERE singleton_id = 1"
            )
            if control is not None and bool(control["paused"]):
                raise TaskControlError(
                    TASK_REASON_SCHEDULER_PAUSED,
                    "task scheduler is paused",
                    task_id=jid,
                )
            row = self._query_one("SELECT * FROM cron_jobs WHERE job_id = ?", (jid,))
            if row is None:
                raise ValueError(f"cron job not found: {jid}")
            if not bool(row["enabled"]):
                raise TaskControlError(
                    TASK_REASON_NOT_ACTIVE,
                    "task is not active",
                    task_id=jid,
                )
            active = self._query_one(
                """
                SELECT COUNT(1) AS c FROM cron_runs
                WHERE job_id = ? AND state IN ('queued', 'running')
                """,
                (jid,),
            )
            if active is not None and int(active["c"]) >= int(row["max_concurrency"]):
                raise TaskControlError(
                    TASK_REASON_BUSY,
                    "task is already at its active-run limit",
                    task_id=jid,
                )
            run_id = uuid4().hex
            self._execute_count(
                """
                INSERT INTO cron_runs(
                  run_id, job_id, state, due_at, available_at, started_at, finished_at,
                  isolated_session_id, summary, artifact_refs_json, error_json,
                  output_json, lease_owner, lease_expires_at, delivery_targets_json,
                  attempts, created_at, updated_at
                )
                VALUES (?, ?, 'queued', ?, ?, NULL, NULL, NULL, NULL, '[]', NULL,
                        '{}', NULL, NULL, '[]', 0, ?, ?)
                """,
                (run_id, jid, now, now, now, now),
            )
            run = self._query_one("SELECT * FROM cron_runs WHERE run_id = ?", (run_id,))
        if run is None:
            raise RuntimeError("queued run was not persisted")
        return row_to_cron_run(run)

    def delete_terminal_cron_runs(self, job_id: str) -> int:
        jid = str(job_id or "").strip()
        if not jid:
            raise ValueError("job_id is required")
        with self._lock, self._record_store.transaction():
            return self._execute_count(
                """
                DELETE FROM cron_runs
                WHERE job_id = ?
                  AND state IN ('finished', 'failed', 'cancelled', 'timed_out')
                """,
                (jid,),
            )

    def get_cron_scheduler_control(self) -> dict[str, Any]:
        with self._lock, self._record_store.transaction():
            row = self._query_one(
                "SELECT * FROM cron_scheduler_control WHERE singleton_id = 1"
            )
            if row is None:
                now = to_iso_utc(utc_now())
                self._execute_count(
                    """
                    INSERT INTO cron_scheduler_control(
                      singleton_id, paused, reason, actor, updated_at
                    ) VALUES (1, 0, NULL, NULL, ?)
                    """,
                    (now,),
                )
                row = self._query_one(
                    "SELECT * FROM cron_scheduler_control WHERE singleton_id = 1"
                )
        if row is None:
            raise RuntimeError("scheduler control state was not persisted")
        return {
            "paused": bool(row["paused"]),
            "reason": row["reason"],
            "actor": row["actor"],
            "updated_at": row["updated_at"],
        }

    def set_cron_scheduler_control(
        self,
        *,
        paused: bool,
        reason: str | None,
        actor: str,
        expected_updated_at: str,
    ) -> dict[str, Any]:
        self.get_cron_scheduler_control()
        now = to_iso_utc(utc_now())
        with self._lock, self._record_store.transaction():
            updated = self._execute_count(
                """
                UPDATE cron_scheduler_control
                SET paused = ?, reason = ?, actor = ?, updated_at = ?
                WHERE singleton_id = 1 AND updated_at = ?
                """,
                (
                    1 if paused else 0,
                    str(reason or "").strip() or None,
                    str(actor or "").strip() or None,
                    now,
                    str(expected_updated_at or "").strip(),
                ),
            )
            if updated <= 0:
                raise TaskControlError(
                    TASK_REASON_SCHEDULER_REVISION_CONFLICT,
                    "scheduler revision changed; refresh and retry",
                )
        return self.get_cron_scheduler_control()

    def _auto_pause_legacy_short_interval_job(
        self,
        *,
        job: Mapping[str, Any],
        now_iso: str,
    ) -> None:
        payload = dict(job.get("payload") or {})
        payload[TASK_INTERNAL_PAUSE_REASON_KEY] = (
            TASK_REASON_SCHEDULE_INTERVAL_TOO_SHORT
        )
        payload[TASK_INTERNAL_PAUSE_SOURCE_KEY] = "scheduler"
        self._execute_count(
            """
            UPDATE cron_jobs
            SET enabled = 0,
                next_due_at = NULL,
                payload_json = ?,
                updated_at = ?
            WHERE job_id = ?
            """,
            (
                to_json(payload),
                now_iso,
                str(job["job_id"]),
            ),
        )

    def trigger_cron_run(
        self,
        job_id: str,
        *,
        due_at: str | None = None,
        lease_owner: str | None = None,
        lease_ttl_s: int = 60,
    ) -> str:
        job_row = self._query_one("SELECT * FROM cron_jobs WHERE job_id = ?", (job_id,))
        if job_row is None:
            raise ValueError(f"cron job not found: {job_id}")
        job = row_to_cron_job(job_row)

        now_dt = utc_now()
        now = to_iso_utc(now_dt)
        due_dt = parse_iso_datetime(due_at) if due_at else now_dt
        expires_dt = now_dt
        if lease_owner:
            expires_dt = now_dt + timedelta(seconds=max(1, lease_ttl_s))
        run_id = uuid4().hex
        with self._lock, self._record_store.transaction():
            coordination_key = str(job.get("concurrency_key") or "").strip()
            if coordination_key:
                active = self._query_one(
                    """
                    SELECT COUNT(1) AS c
                    FROM cron_runs AS r
                    JOIN cron_jobs AS j ON j.job_id = r.job_id
                    WHERE j.concurrency_key = ?
                      AND r.state IN ('queued', 'running')
                    """,
                    (coordination_key,),
                )
                if active is not None and int(active["c"]) > 0:
                    raise ValueError(
                        f"cron coordination scope is busy: {coordination_key}"
                    )
            self._execute_count(
                """
                INSERT INTO cron_runs(
                  run_id, job_id, state, due_at, available_at, started_at, finished_at,
                  isolated_session_id, summary, artifact_refs_json, error_json, output_json,
                  lease_owner, lease_expires_at,
                  delivery_targets_json, attempts, created_at, updated_at
                )
                VALUES (?, ?, 'queued', ?, ?, NULL, NULL, NULL, NULL, '[]', NULL, '{}',
                        ?, ?, '[]', 0, ?, ?)
                """,
                (
                    run_id,
                    str(job["job_id"]),
                    to_iso_utc(due_dt),
                    to_iso_utc(due_dt),
                    str(lease_owner or "").strip() or None,
                    to_iso_utc(expires_dt) if lease_owner else None,
                    now,
                    now,
                ),
            )
        return run_id

    @staticmethod
    def _retry_delay_seconds(*, attempts: int, base_backoff_s: int) -> int:
        multiplier = 2 ** max(0, attempts - 1)
        return int(min(base_backoff_s * multiplier, MAX_CRON_RETRY_BACKOFF_SECONDS))

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
        daemon_id: str | None = None,
        expected_attempts: int | None = None,
        now_iso: str | None = None,
    ) -> dict[str, Any] | None:
        rid = str(run_id or "").strip()
        if not rid:
            raise ValueError("run_id is required")
        if (daemon_id is None) != (expected_attempts is None):
            raise ValueError(
                "daemon_id and expected_attempts must be provided together"
            )
        now_dt = parse_iso_datetime(now_iso) if now_iso else utc_now()
        now = to_iso_utc(now_dt)
        with self._lock, self._record_store.transaction():
            fence_sql = ""
            fence_params: tuple[Any, ...] = ()
            if daemon_id is not None:
                fence_sql = " AND r.lease_owner = ? AND r.attempts = ?"
                fence_params = (str(daemon_id).strip(), cast(int, expected_attempts))
            row = self._query_one(
                f"""
                SELECT r.*, j.max_attempts, j.retry_backoff_s
                FROM cron_runs AS r
                LEFT JOIN cron_jobs AS j ON j.job_id = r.job_id
                WHERE r.run_id = ?
                  AND r.state = 'running'
                  {fence_sql}
                """,
                (rid, *fence_params),
            )
            if row is None:
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
                      AND lease_owner = ? AND attempts = ?
                    """,
                    (
                        to_json(terminal_error),
                        now,
                        now,
                        rid,
                        str(row.get("lease_owner") or ""),
                        attempts,
                    ),
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
                      AND lease_owner = ? AND attempts = ?
                    """,
                    (
                        available_at,
                        to_json(retry_error),
                        now,
                        rid,
                        str(row.get("lease_owner") or ""),
                        attempts,
                    ),
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
