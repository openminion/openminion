from __future__ import annotations

import sqlite3
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import cast

from openminion.base.time import utc_now, utc_now_iso

from .schemas import JobStatus, OperationJob, OperationRequest


class OperationJobStore:
    """Durable state for OpenMinion operations, never arbitrary host processes."""

    def __init__(
        self,
        path: Path | str = ":memory:",
        *,
        ttl_seconds: int = 86400,
        per_target_limit: int = 4,
    ) -> None:
        self._path = str(path)
        self._lock = threading.RLock()
        self._ttl_seconds = ttl_seconds
        self._per_target_limit = per_target_limit
        self._connection = sqlite3.connect(self._path, check_same_thread=False)
        self._connection.execute(
            """CREATE TABLE IF NOT EXISTS operation_jobs (
                job_id TEXT PRIMARY KEY,
                request_json TEXT NOT NULL,
                target_revision INTEGER NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                evidence_id TEXT NOT NULL,
                error TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                expires_at TEXT NOT NULL DEFAULT '',
                lease_owner TEXT NOT NULL DEFAULT ''
            )"""
        )
        self._add_missing_columns()
        self._connection.commit()

    def _add_missing_columns(self) -> None:
        columns = {
            str(row[1])
            for row in self._connection.execute(
                "PRAGMA table_info(operation_jobs)"
            ).fetchall()
        }
        for name in ("expires_at", "lease_owner"):
            if name not in columns:
                self._connection.execute(
                    f"ALTER TABLE operation_jobs ADD COLUMN {name} "
                    "TEXT NOT NULL DEFAULT ''"
                )

    def submit(
        self, request: OperationRequest, *, target_revision: int
    ) -> OperationJob:
        with self._lock:
            if request.idempotency_key:
                row = self._connection.execute(
                    "SELECT job_id FROM operation_jobs WHERE idempotency_key = ? "
                    "AND json_extract(request_json, '$.target_id') = ? "
                    "AND json_extract(request_json, '$.session_id') = ?",
                    (request.idempotency_key, request.target_id, request.session_id),
                ).fetchone()
                if row is not None:
                    return self.get(str(row[0]))
            active = self._connection.execute(
                "SELECT COUNT(*) FROM operation_jobs WHERE status IN ('queued', 'running') "
                "AND json_extract(request_json, '$.target_id') = ?",
                (request.target_id,),
            ).fetchone()
            if active is not None and int(active[0]) >= self._per_target_limit:
                raise RuntimeError("target operation concurrency limit reached")
            now = utc_now_iso()
            expires_at = (utc_now() + timedelta(seconds=self._ttl_seconds)).isoformat()
            job = OperationJob(
                job_id=f"opjob-{uuid.uuid4().hex}",
                request=request,
                target_revision=target_revision,
                status="queued",
                created_at=now,
                updated_at=now,
                expires_at=expires_at,
            )
            self._connection.execute(
                "INSERT INTO operation_jobs "
                "(job_id, request_json, target_revision, status, created_at, "
                "updated_at, evidence_id, error, idempotency_key, expires_at, "
                "lease_owner) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    job.job_id,
                    request.model_dump_json(),
                    target_revision,
                    job.status,
                    now,
                    now,
                    "",
                    "",
                    request.idempotency_key,
                    expires_at,
                    "",
                ),
            )
            self._connection.commit()
            return job

    def get(self, job_id: str) -> OperationJob:
        with self._lock:
            row = self._connection.execute(
                "SELECT job_id, request_json, target_revision, status, created_at, "
                "updated_at, evidence_id, error, expires_at, lease_owner "
                "FROM operation_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"unknown operation job: {job_id}")
        job = OperationJob(
            job_id=str(row[0]),
            request=OperationRequest.model_validate_json(str(row[1])),
            target_revision=int(row[2]),
            status=cast(JobStatus, str(row[3])),
            created_at=str(row[4]),
            updated_at=str(row[5]),
            evidence_id=str(row[6]),
            error=str(row[7]),
            expires_at=str(row[8]),
            lease_owner=str(row[9]),
        )
        if not job.expires_at and self._ttl_seconds > 0:
            expires = datetime.fromisoformat(job.created_at) + timedelta(
                seconds=self._ttl_seconds
            )
            job = job.model_copy(update={"expires_at": expires.isoformat()})
        return job

    def acquire_lease(self, job_id: str, *, owner: str) -> OperationJob:
        if not owner.strip():
            raise ValueError("operation job lease owner is required")
        with self._lock:
            current = self.get(job_id)
            if current.lease_owner and current.lease_owner != owner:
                raise RuntimeError("operation job already has a lease")
            self._connection.execute(
                "UPDATE operation_jobs SET lease_owner = ?, updated_at = ? "
                "WHERE job_id = ?",
                (owner, utc_now_iso(), job_id),
            )
            self._connection.commit()
            return self.get(job_id)

    def release_lease(self, job_id: str, *, owner: str) -> OperationJob:
        with self._lock:
            current = self.get(job_id)
            if current.lease_owner != owner:
                raise PermissionError("operation job lease belongs to another owner")
            self._connection.execute(
                "UPDATE operation_jobs SET lease_owner = '', updated_at = ? "
                "WHERE job_id = ?",
                (utc_now_iso(), job_id),
            )
            self._connection.commit()
            return self.get(job_id)

    def update(
        self,
        job_id: str,
        *,
        status: JobStatus,
        evidence_id: str = "",
        error: str = "",
    ) -> OperationJob:
        with self._lock:
            current = self.get(job_id)
            if current.status in {"succeeded", "failed", "cancelled"}:
                return current
            self._connection.execute(
                "UPDATE operation_jobs SET status = ?, updated_at = ?, evidence_id = ?, "
                "error = ? WHERE job_id = ?",
                (status, utc_now_iso(), evidence_id, error, job_id),
            )
            self._connection.commit()
            return self.get(job_id)

    def cancel(
        self,
        job_id: str,
        *,
        target_id: str = "",
        session_id: str = "",
    ) -> OperationJob:
        current = self.get(job_id)
        if target_id and current.request.target_id != target_id:
            raise PermissionError("operation job belongs to another target")
        if session_id and current.request.session_id != session_id:
            raise PermissionError("operation job belongs to another session")
        return self.update(job_id, status="cancelled")

    def recover_running(self) -> int:
        with self._lock:
            cursor = self._connection.execute(
                "UPDATE operation_jobs SET status = 'failed', updated_at = ?, "
                "error = 'operation interrupted before reconnect' WHERE status = 'running'",
                (utc_now_iso(),),
            )
            self._connection.commit()
            return int(cursor.rowcount)

    def prune_expired(self) -> int:
        with self._lock:
            cursor = self._connection.execute(
                "DELETE FROM operation_jobs WHERE expires_at != '' AND expires_at < ? "
                "AND status NOT IN ('queued', 'running')",
                (utc_now_iso(),),
            )
            self._connection.commit()
            return int(cursor.rowcount)

    def list(self) -> tuple[OperationJob, ...]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT job_id FROM operation_jobs ORDER BY created_at, job_id"
            ).fetchall()
        return tuple(self.get(str(row[0])) for row in rows)
