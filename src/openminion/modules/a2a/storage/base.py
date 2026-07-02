from datetime import datetime, timezone
from typing import Protocol

from openminion.modules.a2a.models import (
    AgentDescriptor,
    AuditRecord,
    IdempotencyRecord,
    JobRecord,
)


def idempotency_slot_is_stale(updated_at: str, *, stale_after_sec: int) -> bool:
    """Return True when an in-progress idempotency slot is old enough to reclaim."""
    try:
        stamped = datetime.fromisoformat(str(updated_at).replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return True
    stamped = stamped.astimezone(timezone.utc)
    return (datetime.now(timezone.utc) - stamped).total_seconds() > max(
        1, int(stale_after_sec)
    )


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
