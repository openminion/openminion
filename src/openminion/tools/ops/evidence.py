from __future__ import annotations

import hashlib
import sqlite3
import threading
import uuid
from pathlib import Path

from openminion.base.time import utc_now_iso

from .contracts import ClaimStatus, EvidenceRecord, OperationRequest, TransportResult


def build_evidence(
    request: OperationRequest,
    result: TransportResult,
    *,
    redactions: tuple[str, ...] = (),
    target_revision: int = 0,
    transport: str = "",
    policy_outcome: str = "allow",
    approval_id: str = "",
    retention_until: str = "",
    before_facts: dict[str, str] | None = None,
    after_facts: dict[str, str] | None = None,
    rollback_state: str = "",
) -> EvidenceRecord:
    stdout = _redact(result.stdout, redactions)
    stderr = _redact(result.stderr, redactions)
    payload = f"{result.return_code}\0{stdout}\0{stderr}".encode()
    status: ClaimStatus
    if result.cancelled:
        status, reason = "unknown", "operation cancelled before observation"
    elif result.timed_out:
        status, reason = "partial", "operation timed out"
    elif result.return_code != 0:
        status, reason = "failed", "transport command returned non-zero"
    elif not stdout.strip() and not stderr.strip():
        status, reason = "unknown", "command returned no observable output"
    else:
        status, reason = "observed", "output captured"
    return EvidenceRecord(
        evidence_id=f"evidence-{uuid.uuid4()}",
        operation_id=request.operation_id,
        session_id=request.session_id,
        target_id=request.target_id,
        target_revision=target_revision,
        transport=transport,
        profile_id=request.profile_id,
        skill_id=request.skill_id,
        tool_id=request.tool_id,
        claim_status=status,
        collected_at=utc_now_iso(),
        output_digest=hashlib.sha256(payload).hexdigest(),
        stdout_preview=stdout[:4000],
        stderr_preview=stderr[:4000],
        return_code=result.return_code,
        reason=reason,
        policy_outcome=policy_outcome,
        approval_id=approval_id,
        command_hash=hashlib.sha256("\0".join(result.argv).encode()).hexdigest(),
        retention_until=retention_until,
        redacted_parameters={
            key: _redact(str(value), redactions)
            for key, value in request.parameters.items()
        },
        before_facts=before_facts or {},
        after_facts=after_facts or {},
        failure=stderr if status in {"failed", "partial"} else "",
        rollback_state=rollback_state,
    )


def _redact(value: str, secrets: tuple[str, ...]) -> str:
    redacted = value
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "[REDACTED]")
    return redacted


class EvidenceStore:
    """Durable index for redacted evidence envelopes."""

    def __init__(self, path: Path | str = ":memory:") -> None:
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(str(path), check_same_thread=False)
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS operation_evidence "
            "(evidence_id TEXT PRIMARY KEY, record_json TEXT NOT NULL)"
        )
        self._connection.commit()

    def put(self, record: EvidenceRecord) -> EvidenceRecord:
        with self._lock:
            self._connection.execute(
                "INSERT OR REPLACE INTO operation_evidence VALUES (?, ?)",
                (record.evidence_id, record.model_dump_json()),
            )
            self._connection.commit()
        return record

    def get(self, evidence_id: str) -> EvidenceRecord:
        row = self._connection.execute(
            "SELECT record_json FROM operation_evidence WHERE evidence_id = ?",
            (evidence_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown operation evidence: {evidence_id}")
        return EvidenceRecord.model_validate_json(str(row[0]))

    def list(
        self, *, target_id: str = "", session_id: str = ""
    ) -> tuple[EvidenceRecord, ...]:
        rows = self._connection.execute(
            "SELECT record_json FROM operation_evidence ORDER BY evidence_id"
        ).fetchall()
        records = tuple(EvidenceRecord.model_validate_json(str(row[0])) for row in rows)
        return tuple(
            record
            for record in records
            if (not target_id or record.target_id == target_id)
            and (not session_id or record.session_id == session_id)
        )
