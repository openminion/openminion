import copy
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any
from typing import Protocol

from openminion.modules.a2a.models import (
    A2AObservabilityContext,
    AgentDescriptor,
    AuditRecord,
    EnvelopeValidationError,
    IdempotencyRecord,
    JobRecord,
)


def idempotency_slot_is_stale(updated_at: str, *, stale_after_sec: int) -> bool:
    """Return True when an in-progress idempotency slot is old enough to reclaim."""
    try:
        stamped = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    age = datetime.now(timezone.utc) - stamped.astimezone(timezone.utc)
    return age.total_seconds() > max(1, stale_after_sec)


def audit_record_for_storage(
    record: AuditRecord, *, capture_payloads: bool
) -> AuditRecord:
    if capture_payloads:
        return copy.deepcopy(record)
    return replace(
        record,
        error_message=None,
        envelope=_structural_observability(record.envelope),
        data=None,
    )


def _structural_observability(
    envelope: dict[str, Any] | None,
) -> dict[str, Any] | None:
    raw = envelope.get("observability") if isinstance(envelope, dict) else None
    if not isinstance(raw, dict):
        return None
    context = A2AObservabilityContext.from_dict(raw)
    try:
        context.validate()
    except EnvelopeValidationError:
        return None
    payload = context.to_dict()
    payload.pop("tracestate", None)
    return {"observability": payload}


class StateStore(Protocol):
    def reserve_idempotency(
        self, key: str, scope: str, *, stale_reclaim_after_sec: int | None = None
    ) -> tuple[bool, IdempotencyRecord | None]: ...

    def set_idempotency_result(
        self,
        key: str,
        scope: str,
        status: str,
        *,
        result_inline: dict | None = None,
        result_ref: str | None = None,
        error: dict | None = None,
        task_id: str | None = None,
    ) -> IdempotencyRecord: ...

    def create_job(self, job: JobRecord) -> str: ...

    def update_job(self, task_id: str, patch: dict) -> JobRecord: ...

    def get_job(self, task_id: str) -> JobRecord | None: ...

    def list_jobs(self, filter_by: dict | None = None) -> list[JobRecord]: ...

    def upsert_agent(self, descriptor: AgentDescriptor) -> None: ...

    def list_agents(self) -> list[AgentDescriptor]: ...

    def close(self) -> None: ...


class AuditStore(Protocol):
    def append_audit(self, record: AuditRecord) -> None: ...

    def query_audit(self, filter_by: dict | None = None) -> list[AuditRecord]: ...

    def close(self) -> None: ...
