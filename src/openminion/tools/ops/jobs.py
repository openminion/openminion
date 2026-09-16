from __future__ import annotations

import sqlite3
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import cast

from openminion.base.time import utc_now, utc_now_iso

from .contracts import (
    AttemptPhase,
    CancelStatus,
    JobStatus,
    OperationJob,
    OperationRequest,
    RemoteOutcome,
)


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
                lease_owner TEXT NOT NULL DEFAULT '',
                plan_id TEXT NOT NULL DEFAULT '',
                attempt_phase TEXT NOT NULL DEFAULT '',
                claim_token TEXT NOT NULL DEFAULT '',
                cancel_requested INTEGER NOT NULL DEFAULT 0,
                cancel_status TEXT NOT NULL DEFAULT '',
                remote_outcome TEXT NOT NULL DEFAULT 'unknown',
                approval_id TEXT NOT NULL DEFAULT '',
                policy_grant_id TEXT NOT NULL DEFAULT '',
                policy_invocation_hash TEXT NOT NULL DEFAULT '',
                interrupted_at TEXT NOT NULL DEFAULT '',
                interruption_reason TEXT NOT NULL DEFAULT '',
                interrupted_by TEXT NOT NULL DEFAULT ''
            )"""
        )
        self._add_missing_columns()
        self._connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_operation_jobs_plan "
            "ON operation_jobs(plan_id) WHERE plan_id != ''"
        )
        self._connection.commit()

    def _add_missing_columns(self) -> None:
        columns = {
            str(row[1])
            for row in self._connection.execute(
                "PRAGMA table_info(operation_jobs)"
            ).fetchall()
        }
        definitions = {
            "expires_at": "TEXT NOT NULL DEFAULT ''",
            "lease_owner": "TEXT NOT NULL DEFAULT ''",
            "plan_id": "TEXT NOT NULL DEFAULT ''",
            "attempt_phase": "TEXT NOT NULL DEFAULT ''",
            "claim_token": "TEXT NOT NULL DEFAULT ''",
            "cancel_requested": "INTEGER NOT NULL DEFAULT 0",
            "cancel_status": "TEXT NOT NULL DEFAULT ''",
            "remote_outcome": "TEXT NOT NULL DEFAULT 'unknown'",
            "approval_id": "TEXT NOT NULL DEFAULT ''",
            "policy_grant_id": "TEXT NOT NULL DEFAULT ''",
            "policy_invocation_hash": "TEXT NOT NULL DEFAULT ''",
            "interrupted_at": "TEXT NOT NULL DEFAULT ''",
            "interruption_reason": "TEXT NOT NULL DEFAULT ''",
            "interrupted_by": "TEXT NOT NULL DEFAULT ''",
        }
        for name, definition in definitions.items():
            if name not in columns:
                self._connection.execute(
                    f"ALTER TABLE operation_jobs ADD COLUMN {name} {definition}"
                )
        self._connection.execute(
            "UPDATE operation_jobs SET plan_id = "
            "json_extract(request_json, '$.operation_id') "
            "WHERE plan_id = '' "
            "AND json_extract(request_json, '$.profile_id') = 'command.run'"
        )

    def submit(
        self,
        request: OperationRequest,
        *,
        target_revision: int,
        target_limit: int | None = None,
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
            limit = target_limit or self._per_target_limit
            if active is not None and int(active[0]) >= limit:
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
                ", plan_id, attempt_phase, claim_token, cancel_requested, "
                "cancel_status, remote_outcome, approval_id, policy_grant_id, "
                "policy_invocation_hash, interrupted_at, interruption_reason, "
                "interrupted_by "
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
            plan_id=str(row[10]),
            attempt_phase=cast(AttemptPhase, str(row[11])),
            claim_token=str(row[12]),
            cancel_requested=bool(row[13]),
            cancel_status=cast(CancelStatus, str(row[14])),
            remote_outcome=cast(RemoteOutcome, str(row[15])),
            approval_id=str(row[16]),
            policy_grant_id=str(row[17]),
            policy_invocation_hash=str(row[18]),
            interrupted_at=str(row[19]),
            interruption_reason=str(row[20]),
            interrupted_by=str(row[21]),
        )
        if not job.expires_at and self._ttl_seconds > 0:
            expires = datetime.fromisoformat(job.created_at) + timedelta(
                seconds=self._ttl_seconds
            )
            job = job.model_copy(update={"expires_at": expires.isoformat()})
        return job

    def claim_plan_attempt(
        self,
        request: OperationRequest,
        *,
        plan_id: str,
        target_revision: int,
    ) -> tuple[OperationJob, str]:
        """Return the sole durable attempt for a plan and claim it when available."""
        token = f"claim-{uuid.uuid4().hex}"
        with self._lock, self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            row = self._connection.execute(
                "SELECT job_id, status, attempt_phase FROM operation_jobs "
                "WHERE plan_id = ?",
                (plan_id,),
            ).fetchone()
            if row is not None:
                job_id, status, phase = map(str, row)
                if status == "queued" and phase == "awaiting_approval":
                    updated = self._connection.execute(
                        "UPDATE operation_jobs SET attempt_phase = 'claimed', "
                        "claim_token = ?, updated_at = ? WHERE job_id = ? "
                        "AND status = 'queued' "
                        "AND attempt_phase = 'awaiting_approval'",
                        (token, utc_now_iso(), job_id),
                    ).rowcount
                    return self.get(job_id), token if updated == 1 else ""
                return self.get(job_id), ""
            now = utc_now_iso()
            expires_at = (utc_now() + timedelta(seconds=self._ttl_seconds)).isoformat()
            job_id = f"opjob-{uuid.uuid4().hex}"
            self._connection.execute(
                "INSERT INTO operation_jobs "
                "(job_id, request_json, target_revision, status, created_at, "
                "updated_at, evidence_id, error, idempotency_key, expires_at, "
                "lease_owner, plan_id, attempt_phase, claim_token, "
                "remote_outcome) VALUES (?, ?, ?, 'queued', ?, ?, '', '', ?, ?, "
                "'', ?, 'claimed', ?, 'not_dispatched')",
                (
                    job_id,
                    request.model_dump_json(),
                    target_revision,
                    now,
                    now,
                    request.idempotency_key,
                    expires_at,
                    plan_id,
                    token,
                ),
            )
            return self.get(job_id), token

    def await_approval(
        self, job_id: str, *, claim_token: str, approval_id: str
    ) -> OperationJob:
        with self._lock:
            updated = self._connection.execute(
                "UPDATE operation_jobs SET attempt_phase = 'awaiting_approval', "
                "claim_token = '', approval_id = ?, updated_at = ? "
                "WHERE job_id = ? AND status = 'queued' "
                "AND attempt_phase = 'claimed' AND claim_token = ? "
                "AND cancel_requested = 0",
                (approval_id, utc_now_iso(), job_id, claim_token),
            ).rowcount
            self._connection.commit()
        if updated != 1:
            raise RuntimeError("command attempt claim is no longer active")
        return self.get(job_id)

    def reserve_plan_capacity(
        self,
        job_id: str,
        *,
        claim_token: str,
        target_limit: int,
    ) -> OperationJob:
        with self._lock, self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            current = self.get(job_id)
            if (
                current.status != "queued"
                or current.attempt_phase != "claimed"
                or current.claim_token != claim_token
                or current.cancel_requested
            ):
                raise RuntimeError("command attempt claim is no longer active")
            active = self._active_count(current.request.target_id)
            if active >= target_limit:
                raise RuntimeError("target operation concurrency limit reached")
            self._connection.execute(
                "UPDATE operation_jobs SET status = 'running', updated_at = ? "
                "WHERE job_id = ? AND status = 'queued' "
                "AND attempt_phase = 'claimed' AND claim_token = ? "
                "AND cancel_requested = 0",
                (utc_now_iso(), job_id, claim_token),
            )
            return self.get(job_id)

    def record_dispatch_intent(
        self,
        job_id: str,
        *,
        claim_token: str,
        approval_id: str,
        policy_grant_id: str,
        policy_invocation_hash: str,
    ) -> OperationJob:
        with self._lock:
            updated = self._connection.execute(
                "UPDATE operation_jobs SET attempt_phase = 'dispatch_intent', "
                "approval_id = ?, policy_grant_id = ?, policy_invocation_hash = ?, "
                "remote_outcome = 'unknown', updated_at = ? "
                "WHERE job_id = ? AND status = 'running' "
                "AND attempt_phase = 'claimed' AND claim_token = ? "
                "AND cancel_requested = 0",
                (
                    approval_id,
                    policy_grant_id,
                    policy_invocation_hash,
                    utc_now_iso(),
                    job_id,
                    claim_token,
                ),
            ).rowcount
            self._connection.commit()
        if updated != 1:
            current = self.get(job_id)
            if current.cancel_requested and current.attempt_phase != "dispatch_intent":
                return self.finish_plan_attempt(
                    job_id,
                    claim_token=claim_token,
                    status="cancelled",
                    error="command cancelled before dispatch",
                    remote_outcome="not_dispatched",
                )
            raise RuntimeError("command dispatch intent was not recorded")
        return self.get(job_id)

    def finish_plan_attempt(
        self,
        job_id: str,
        *,
        claim_token: str,
        status: JobStatus,
        evidence_id: str = "",
        error: str = "",
        remote_outcome: RemoteOutcome,
    ) -> OperationJob:
        if status not in {"succeeded", "failed", "cancelled"}:
            raise ValueError("command attempt terminal status is required")
        with self._lock:
            updated = self._connection.execute(
                "UPDATE operation_jobs SET status = ?, attempt_phase = 'terminal', "
                "claim_token = '', updated_at = ?, evidence_id = ?, error = ?, "
                "remote_outcome = ? WHERE job_id = ? "
                "AND attempt_phase != 'terminal' AND claim_token = ?",
                (
                    status,
                    utc_now_iso(),
                    evidence_id,
                    error,
                    remote_outcome,
                    job_id,
                    claim_token,
                ),
            ).rowcount
            if updated == 0 and evidence_id:
                self._connection.execute(
                    "UPDATE operation_jobs SET evidence_id = CASE "
                    "WHEN evidence_id = '' THEN ? ELSE evidence_id END, updated_at = ? "
                    "WHERE job_id = ?",
                    (evidence_id, utc_now_iso(), job_id),
                )
            self._connection.commit()
        return self.get(job_id)

    def _active_count(self, target_id: str) -> int:
        row = self._connection.execute(
            "SELECT COUNT(*) FROM operation_jobs "
            "WHERE json_extract(request_json, '$.target_id') = ? AND ("
            "(plan_id = '' AND status IN ('queued', 'running')) OR "
            "(plan_id != '' AND status = 'running' "
            "AND attempt_phase IN ('claimed', 'dispatch_intent')))",
            (target_id,),
        ).fetchone()
        return int(row[0]) if row is not None else 0

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
        remote_outcome: RemoteOutcome | None = None,
    ) -> OperationJob:
        with self._lock:
            current = self.get(job_id)
            if current.status in {"succeeded", "failed", "cancelled"}:
                return current
            outcome = remote_outcome or current.remote_outcome
            phase = (
                "terminal"
                if current.plan_id
                and status
                in {
                    "succeeded",
                    "failed",
                    "cancelled",
                }
                else current.attempt_phase
            )
            self._connection.execute(
                "UPDATE operation_jobs SET status = ?, updated_at = ?, evidence_id = ?, "
                "error = ?, remote_outcome = ?, attempt_phase = ? WHERE job_id = ?",
                (
                    status,
                    utc_now_iso(),
                    evidence_id,
                    error,
                    outcome,
                    phase,
                    job_id,
                ),
            )
            self._connection.commit()
            return self.get(job_id)

    def request_cancel(
        self,
        job_id: str,
        *,
        target_id: str = "",
        session_id: str = "",
    ) -> OperationJob:
        with self._lock, self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            current = self.get(job_id)
            if target_id and current.request.target_id != target_id:
                raise PermissionError("operation job belongs to another target")
            if session_id and current.request.session_id != session_id:
                raise PermissionError("operation job belongs to another session")
            if current.status in {"succeeded", "failed", "cancelled"}:
                return current
            if current.status == "queued" or current.attempt_phase == "claimed":
                self._connection.execute(
                    "UPDATE operation_jobs SET status = 'cancelled', "
                    "attempt_phase = CASE WHEN plan_id != '' THEN 'terminal' "
                    "ELSE attempt_phase END, claim_token = '', "
                    "cancel_requested = 1, "
                    "cancel_status = 'cancel_not_delivered', "
                    "remote_outcome = 'not_dispatched', updated_at = ? "
                    "WHERE job_id = ?",
                    (utc_now_iso(), job_id),
                )
            else:
                self._connection.execute(
                    "UPDATE operation_jobs SET cancel_requested = 1, "
                    "cancel_status = 'cancel_requested', updated_at = ? "
                    "WHERE job_id = ? AND status = 'running'",
                    (utc_now_iso(), job_id),
                )
            return self.get(job_id)

    def record_cancel_delivery(self, job_id: str, *, delivered: bool) -> OperationJob:
        with self._lock:
            self._connection.execute(
                "UPDATE operation_jobs SET cancel_status = ?, updated_at = ? "
                "WHERE job_id = ? AND status = 'running' AND cancel_requested = 1",
                (
                    "cancel_delivered" if delivered else "cancel_not_delivered",
                    utc_now_iso(),
                    job_id,
                ),
            )
            self._connection.commit()
            return self.get(job_id)

    def mark_interrupted(
        self,
        job_id: str,
        *,
        reason: str,
        actor: str,
    ) -> tuple[OperationJob, bool, str, str]:
        reason = reason.strip()
        actor = actor.strip()
        if not reason:
            raise ValueError("interruption reason is required")
        if not actor:
            raise ValueError("interruption actor is required")
        now = utc_now_iso()
        with self._lock, self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            current = self.get(job_id)
            prior_status = current.status
            prior_phase = current.attempt_phase
            if current.status in {"succeeded", "failed", "cancelled"}:
                return current, False, prior_status, prior_phase
            updated = self._connection.execute(
                "UPDATE operation_jobs SET status = 'failed', "
                "attempt_phase = CASE WHEN plan_id != '' THEN 'terminal' "
                "ELSE attempt_phase END, claim_token = '', remote_outcome = 'unknown', "
                "error = ?, interrupted_at = ?, interruption_reason = ?, "
                "interrupted_by = ?, updated_at = ? WHERE job_id = ? "
                "AND (status = 'running' OR attempt_phase = 'claimed')",
                (reason, now, reason, actor, now, job_id),
            ).rowcount
            return self.get(job_id), updated == 1, prior_status, prior_phase

    def attach_evidence(self, job_id: str, evidence_id: str) -> OperationJob:
        with self._lock:
            self._connection.execute(
                "UPDATE operation_jobs SET evidence_id = CASE "
                "WHEN evidence_id = '' THEN ? ELSE evidence_id END, updated_at = ? "
                "WHERE job_id = ?",
                (evidence_id, utc_now_iso(), job_id),
            )
            self._connection.commit()
            return self.get(job_id)

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

    def close(self) -> None:
        with self._lock:
            self._connection.close()
