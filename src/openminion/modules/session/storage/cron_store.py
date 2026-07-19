from __future__ import annotations

from datetime import timedelta
from threading import RLock
from typing import Any, Mapping
from uuid import uuid4

from openminion.modules.task.scheduling.schedule import (
    compute_next_due,
    default_delete_after_run,
    default_session_target_for_payload,
    encode_misfire_policy,
    normalize_delivery,
    normalize_misfire_policy,
    normalize_payload,
    normalize_schedule,
    _select_due_points_for_job,
    normalize_session_target,
    normalize_wake_mode,
    parse_iso_datetime,
    to_iso_utc,
    utc_now,
    validate_target_payload_pair,
)
from openminion.modules.task.constants import (
    DEFAULT_TASK_MIN_EVERY_MS,
    TASK_INTERNAL_PAUSE_REASON_KEY,
    TASK_INTERNAL_PAUSE_SOURCE_KEY,
    TASK_REASON_SCHEDULE_INTERVAL_TOO_SHORT,
)
from openminion.modules.storage.record_store import RecordStore

from .json_utils import parse_json, to_json
from .rows import row_to_cron_job, row_to_cron_run


class CronStore:
    def __init__(self, record_store: RecordStore, lock: RLock) -> None:
        self._record_store = record_store
        self._lock = lock

    def _query_one(
        self,
        sql: str,
        params: tuple[Any, ...] | list[Any] | None = None,
    ) -> dict[str, Any] | None:
        rows = self._record_store.query_dicts(sql, params)
        return rows[0] if rows else None

    def _query_all(
        self,
        sql: str,
        params: tuple[Any, ...] | list[Any] | None = None,
    ) -> list[dict[str, Any]]:
        return self._record_store.query_dicts(sql, params)

    def _execute_count(
        self,
        sql: str,
        params: tuple[Any, ...] | list[Any] | None = None,
    ) -> int:
        return self._record_store.execute_count(sql, params)

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
        normalized_name = str(name or "").strip()
        if not normalized_name:
            raise ValueError("cron job name is required")

        normalized_schedule = normalize_schedule(schedule)
        normalized_payload = normalize_payload(payload)
        normalized_target = (
            default_session_target_for_payload(str(normalized_payload["kind"]))
            if session_target is None
            else normalize_session_target(session_target)
        )
        validate_target_payload_pair(
            session_target=normalized_target,
            payload_kind=str(normalized_payload["kind"]),
        )
        normalized_wake_mode = normalize_wake_mode(wake_mode)
        delivery_default = {"mode": "none"}
        if normalized_target == "isolated":
            delivery_default = {"mode": "announce", "channel": "last", "to": "last"}
        normalized_delivery = normalize_delivery(
            delivery if delivery is not None else delivery_default
        )
        delete_flag = (
            default_delete_after_run(str(normalized_schedule["kind"]))
            if delete_after_run is None
            else bool(delete_after_run)
        )
        policy = normalize_misfire_policy(misfire_policy)
        lateness = max(0, int(max_lateness_s))
        concurrency = max(1, int(max_concurrency))

        jid = (job_id or "").strip() or uuid4().hex
        now_dt = utc_now()
        now = to_iso_utc(now_dt)
        if str(normalized_schedule["kind"]) == "at":
            next_due = str(normalized_schedule["at"])
        else:
            next_due_dt = compute_next_due(
                schedule=normalized_schedule,
                after=now_dt,
                job_id=jid,
                last_due=None,
            )
            next_due = to_iso_utc(next_due_dt) if next_due_dt is not None else None

        with self._lock, self._record_store.transaction():
            self._execute_count(
                """
                INSERT INTO cron_jobs(
                  job_id, name, description, enabled, agent_id, schedule_json, payload_json,
                  delivery_json, session_target, wake_mode, delete_after_run, misfire_policy,
                  max_lateness_s, max_concurrency, next_due_at, last_run_at, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    jid,
                    normalized_name,
                    description,
                    1 if enabled else 0,
                    str(agent_id or "").strip() or None,
                    to_json(normalized_schedule),
                    to_json(normalized_payload),
                    to_json(normalized_delivery),
                    normalized_target,
                    normalized_wake_mode,
                    1 if delete_flag else 0,
                    encode_misfire_policy(policy),
                    lateness,
                    concurrency,
                    next_due if enabled else None,
                    None,
                    now,
                    now,
                ),
            )
        return jid

    def get_cron_job(self, job_id: str) -> dict[str, Any] | None:
        jid = str(job_id or "").strip()
        if not jid:
            raise ValueError("job_id is required")
        with self._lock:
            row = self._query_one(
                "SELECT * FROM cron_jobs WHERE job_id = ?",
                (jid,),
            )
        if row is None:
            return None
        return row_to_cron_job(row)

    def list_cron_jobs(
        self, *, enabled: bool | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 1000))
        clauses: list[str] = []
        params: list[Any] = []
        if enabled is not None:
            clauses.append("enabled = ?")
            params.append(1 if enabled else 0)
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            rows = self._query_all(
                f"""
                SELECT *
                FROM cron_jobs
                {where_sql}
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (*params, safe_limit),
            )
        return [row_to_cron_job(row) for row in rows]

    def set_cron_job_enabled(self, job_id: str, enabled: bool) -> None:
        job = self.get_cron_job(job_id)
        if job is None:
            raise ValueError(f"cron job not found: {job_id}")

        now_dt = utc_now()
        now = to_iso_utc(now_dt)
        next_due: str | None = None
        if bool(enabled):
            schedule = normalize_schedule(job["schedule"])
            if str(schedule["kind"]) == "at":
                next_due = str(schedule["at"])
            else:
                next_due_dt = compute_next_due(
                    schedule=schedule,
                    after=now_dt,
                    job_id=str(job["job_id"]),
                    last_due=None,
                )
                next_due = to_iso_utc(next_due_dt) if next_due_dt is not None else None

        with self._lock, self._record_store.transaction():
            self._execute_count(
                """
                UPDATE cron_jobs
                SET enabled = ?, next_due_at = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (1 if enabled else 0, next_due, now, str(job["job_id"])),
            )

    def delete_cron_job(self, job_id: str) -> None:
        jid = str(job_id or "").strip()
        if not jid:
            raise ValueError("job_id is required")
        with self._lock, self._record_store.transaction():
            self._execute_count("DELETE FROM cron_jobs WHERE job_id = ?", (jid,))

    def replace_cron_job_payload(
        self,
        job_id: str,
        payload: Mapping[str, Any],
    ) -> None:
        jid = str(job_id or "").strip()
        if not jid:
            raise ValueError("job_id is required")
        normalized_payload = normalize_payload(payload)
        with self._lock, self._record_store.transaction():
            updated = self._execute_count(
                """
                UPDATE cron_jobs
                SET payload_json = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (to_json(normalized_payload), to_iso_utc(utc_now()), jid),
            )
        if updated <= 0:
            raise ValueError(f"cron job not found: {jid}")

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
        job = self.get_cron_job(job_id)
        if job is None:
            raise ValueError(f"cron job not found: {job_id}")

        now_dt = utc_now()
        now = to_iso_utc(now_dt)
        due_dt = parse_iso_datetime(due_at) if due_at else now_dt
        expires_dt = now_dt
        if lease_owner:
            expires_dt = now_dt + timedelta(seconds=max(1, int(lease_ttl_s)))
        run_id = uuid4().hex
        with self._lock, self._record_store.transaction():
            self._execute_count(
                """
                INSERT INTO cron_runs(
                  run_id, job_id, state, due_at, started_at, finished_at, isolated_session_id,
                  summary, artifact_refs_json, error_json, lease_owner, lease_expires_at,
                  delivery_targets_json, attempts, created_at, updated_at
                )
                VALUES (?, ?, 'queued', ?, NULL, NULL, NULL, NULL, '[]', NULL, ?, ?, '[]', 0, ?, ?)
                """,
                (
                    run_id,
                    str(job["job_id"]),
                    to_iso_utc(due_dt),
                    str(lease_owner or "").strip() or None,
                    to_iso_utc(expires_dt) if lease_owner else None,
                    now,
                    now,
                ),
            )
        return run_id

    def list_cron_runs(
        self,
        *,
        job_id: str | None = None,
        limit: int = 100,
        states: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 2000))
        clauses: list[str] = []
        params: list[Any] = []
        if job_id is not None:
            clauses.append("job_id = ?")
            params.append(str(job_id))
        if states:
            normalized_states = [
                str(item).strip() for item in states if str(item).strip()
            ]
            if normalized_states:
                placeholders = ",".join("?" for _ in normalized_states)
                clauses.append(f"state IN ({placeholders})")
                params.extend(normalized_states)
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            rows = self._query_all(
                f"""
                SELECT *
                FROM cron_runs
                {where_sql}
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (*params, safe_limit),
            )
        return [row_to_cron_run(row) for row in rows]

    def enqueue_due_cron_runs(
        self,
        daemon_id: str,
        *,
        lease_ttl_s: int = 60,
        max_jobs: int = 50,
        now_iso: str | None = None,
    ) -> list[dict[str, Any]]:
        owner = str(daemon_id or "").strip()
        if not owner:
            raise ValueError("daemon_id is required")
        safe_limit = max(1, min(int(max_jobs), 500))
        now_dt = parse_iso_datetime(now_iso) if now_iso else utc_now()
        now = to_iso_utc(now_dt)
        lease_expires = to_iso_utc(now_dt + timedelta(seconds=max(1, int(lease_ttl_s))))

        queued: list[dict[str, Any]] = []
        with self._lock, self._record_store.transaction():
            rows = self._query_all(
                """
                SELECT *
                FROM cron_jobs
                WHERE enabled = 1
                  AND next_due_at IS NOT NULL
                  AND next_due_at <= ?
                ORDER BY next_due_at ASC
                LIMIT ?
                """,
                (now, safe_limit),
            )
            if not rows:
                fallback_rows = self._query_all(
                    """
                    SELECT *
                    FROM cron_jobs
                    WHERE enabled = 1
                      AND next_due_at IS NOT NULL
                    ORDER BY next_due_at ASC
                    """,
                )
                due_fallback: list[dict[str, Any]] = []
                for row in fallback_rows:
                    try:
                        due_dt = parse_iso_datetime(str(row["next_due_at"]))
                    except Exception:
                        continue
                    if due_dt <= now_dt:
                        due_fallback.append(row)
                if len(due_fallback) > safe_limit:
                    due_fallback = due_fallback[:safe_limit]
                rows = due_fallback

            for row in rows:
                job = row_to_cron_job(row)
                schedule = dict(job.get("schedule") or {})
                if (
                    str(schedule.get("kind") or "").strip() == "every"
                    and int(schedule.get("every_ms", 0) or 0)
                    < DEFAULT_TASK_MIN_EVERY_MS
                ):
                    self._auto_pause_legacy_short_interval_job(job=job, now_iso=now)
                    continue
                active = self._query_one(
                    """
                    SELECT COUNT(1) AS c
                    FROM cron_runs
                    WHERE job_id = ?
                      AND state IN ('queued', 'running')
                    """,
                    (str(job["job_id"]),),
                )
                active_count = int(active["c"]) if active is not None else 0
                max_for_job = max(1, int(job.get("max_concurrency", 1)))
                if active_count >= max_for_job:
                    continue

                due_points, next_due = _select_due_points_for_job(
                    job=job, now_dt=now_dt
                )
                room = max(0, max_for_job - active_count)
                if room > 0 and len(due_points) > room:
                    due_points = due_points[:room]

                self._execute_count(
                    """
                    UPDATE cron_jobs
                    SET next_due_at = ?, updated_at = ?
                    WHERE job_id = ?
                    """,
                    (
                        to_iso_utc(next_due) if next_due is not None else None,
                        now,
                        str(job["job_id"]),
                    ),
                )

                for due_dt in due_points:
                    run_id = uuid4().hex
                    due_iso = to_iso_utc(due_dt)
                    inserted = self._execute_count(
                        """
                        INSERT INTO cron_runs(
                          run_id, job_id, state, due_at, started_at, finished_at, isolated_session_id,
                          summary, artifact_refs_json, error_json, lease_owner, lease_expires_at,
                          delivery_targets_json, attempts, created_at, updated_at
                        )
                        VALUES (?, ?, 'queued', ?, NULL, NULL, NULL, NULL, '[]', NULL, ?, ?, '[]', 0, ?, ?)
                        ON CONFLICT(job_id, due_at) DO NOTHING
                        """,
                        (
                            run_id,
                            str(job["job_id"]),
                            due_iso,
                            owner,
                            lease_expires,
                            now,
                            now,
                        ),
                    )
                    if inserted <= 0:
                        continue
                    queued.append(
                        {
                            "run_id": run_id,
                            "job_id": str(job["job_id"]),
                            "state": "queued",
                            "due_at": due_iso,
                            "lease_owner": owner,
                            "lease_expires_at": lease_expires,
                            "attempts": 0,
                            "created_at": now,
                            "updated_at": now,
                        }
                    )
        return queued

    def acquire_cron_runs(
        self,
        daemon_id: str,
        *,
        lease_ttl_s: int = 60,
        limit: int = 10,
        now_iso: str | None = None,
    ) -> list[dict[str, Any]]:
        owner = str(daemon_id or "").strip()
        if not owner:
            raise ValueError("daemon_id is required")
        safe_limit = max(1, min(int(limit), 500))
        now_dt = parse_iso_datetime(now_iso) if now_iso else utc_now()
        now = to_iso_utc(now_dt)
        lease_expires = to_iso_utc(now_dt + timedelta(seconds=max(1, int(lease_ttl_s))))

        acquired: list[dict[str, Any]] = []
        with self._lock, self._record_store.transaction():
            candidates = self._query_all(
                """
                SELECT *
                FROM cron_runs
                WHERE state = 'queued'
                  AND (
                    lease_owner IS NULL
                    OR lease_owner = ?
                    OR lease_expires_at IS NULL
                    OR lease_expires_at <= ?
                  )
                ORDER BY due_at ASC
                LIMIT ?
                """,
                (owner, now, safe_limit),
            )

            for row in candidates:
                run_id = str(row["run_id"])
                updated = self._execute_count(
                    """
                    UPDATE cron_runs
                    SET state = 'running',
                        started_at = COALESCE(started_at, ?),
                        lease_owner = ?,
                        lease_expires_at = ?,
                        attempts = attempts + 1,
                        updated_at = ?
                    WHERE run_id = ?
                      AND state = 'queued'
                    """,
                    (now, owner, lease_expires, now, run_id),
                )
                if updated <= 0:
                    continue
                run_row = self._query_one(
                    "SELECT * FROM cron_runs WHERE run_id = ?",
                    (run_id,),
                )
                if run_row is None:
                    continue
                acquired.append(row_to_cron_run(run_row))
        return acquired

    def renew_cron_run_lease(
        self,
        run_id: str,
        *,
        daemon_id: str,
        lease_ttl_s: int = 60,
        now_iso: str | None = None,
    ) -> bool:
        rid = str(run_id or "").strip()
        owner = str(daemon_id or "").strip()
        if not rid:
            raise ValueError("run_id is required")
        if not owner:
            raise ValueError("daemon_id is required")
        now_dt = parse_iso_datetime(now_iso) if now_iso else utc_now()
        now = to_iso_utc(now_dt)
        lease_expires = to_iso_utc(now_dt + timedelta(seconds=max(1, int(lease_ttl_s))))
        with self._lock, self._record_store.transaction():
            updated = self._execute_count(
                """
                UPDATE cron_runs
                SET lease_expires_at = ?, updated_at = ?
                WHERE run_id = ?
                  AND state = 'running'
                  AND lease_owner = ?
                """,
                (lease_expires, now, rid, owner),
            )
            return updated > 0

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
        terminal = {"finished", "failed", "cancelled", "timed_out"}
        normalized_state = str(state or "").strip()
        if normalized_state not in terminal:
            raise ValueError(f"invalid terminal state: {normalized_state}")

        rid = str(run_id or "").strip()
        if not rid:
            raise ValueError("run_id is required")

        now_dt = parse_iso_datetime(now_iso) if now_iso else utc_now()
        now = to_iso_utc(now_dt)

        with self._lock, self._record_store.transaction():
            row = self._query_one(
                """
                SELECT r.*, j.schedule_json, j.delete_after_run
                FROM cron_runs AS r
                LEFT JOIN cron_jobs AS j ON j.job_id = r.job_id
                WHERE r.run_id = ?
                """,
                (rid,),
            )
            if row is None:
                return None

            self._execute_count(
                """
                UPDATE cron_runs
                SET state = ?,
                    summary = ?,
                    artifact_refs_json = ?,
                    error_json = ?,
                    isolated_session_id = ?,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    finished_at = ?,
                    updated_at = ?
                WHERE run_id = ?
                """,
                (
                    normalized_state,
                    summary,
                    to_json(artifact_refs or []),
                    to_json(error) if error is not None else None,
                    str(isolated_session_id or "").strip() or None,
                    now,
                    now,
                    rid,
                ),
            )

            job_id_raw = row["job_id"]
            if job_id_raw is not None:
                job_id = str(job_id_raw)
                self._execute_count(
                    """
                    UPDATE cron_jobs
                    SET last_run_at = ?, updated_at = ?
                    WHERE job_id = ?
                    """,
                    (now, now, job_id),
                )

                schedule = parse_json(row["schedule_json"], {})
                if normalized_state == "finished" and str(schedule.get("kind")) == "at":
                    if bool(int(row["delete_after_run"] or 0)):
                        self._execute_count(
                            "DELETE FROM cron_jobs WHERE job_id = ?", (job_id,)
                        )
                    else:
                        self._execute_count(
                            """
                            UPDATE cron_jobs
                            SET enabled = 0, next_due_at = NULL, updated_at = ?
                            WHERE job_id = ?
                            """,
                            (now, job_id),
                        )

            updated_row = self._query_one(
                "SELECT * FROM cron_runs WHERE run_id = ?",
                (rid,),
            )
        if updated_row is None:
            return None
        return row_to_cron_run(updated_row)

    def delete_old_cron_runs(self, before_iso: str) -> int:
        cutoff = str(before_iso or "").strip()
        if not cutoff:
            raise ValueError("before_iso is required")
        with self._lock, self._record_store.transaction():
            return self._execute_count(
                """
                DELETE FROM cron_runs
                WHERE created_at < ?
                  AND state IN ('finished', 'failed', 'cancelled', 'timed_out')
                """,
                (cutoff,),
            )

    def mark_cron_delivery_target(self, run_id: str, *, target: str) -> bool:
        rid = str(run_id or "").strip()
        normalized_target = str(target or "").strip()
        if not rid:
            raise ValueError("run_id is required")
        if not normalized_target:
            raise ValueError("target is required")
        with self._lock, self._record_store.transaction():
            row = self._query_one(
                "SELECT delivery_targets_json FROM cron_runs WHERE run_id = ?",
                (rid,),
            )
            if row is None:
                raise ValueError(f"cron run not found: {rid}")
            targets = parse_json(row["delivery_targets_json"], [])
            if normalized_target in targets:
                return False
            targets.append(normalized_target)
            self._execute_count(
                "UPDATE cron_runs SET delivery_targets_json = ?, updated_at = ? WHERE run_id = ?",
                (to_json(targets), to_iso_utc(utc_now()), rid),
            )
        return True
