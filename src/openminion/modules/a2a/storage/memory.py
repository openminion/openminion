from __future__ import annotations

import copy
import threading
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from typing import Any

from openminion.modules.a2a.config import validate_audit_retention
from openminion.modules.a2a.constants import A2A_IDEMPOTENCY_STATUS_IN_PROGRESS
from openminion.modules.a2a.models import (
    AgentDescriptor,
    AuditRecord,
    IdempotencyRecord,
    JobRecord,
    iso_now,
)
from openminion.modules.a2a.storage.base import (
    AuditStore,
    StateStore,
    audit_record_for_storage,
    idempotency_slot_is_stale,
)


class MemoryStateStore(StateStore):
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._idempotency: dict[tuple[str, str], IdempotencyRecord] = {}
        self._jobs: dict[str, JobRecord] = {}
        self._agents: dict[str, AgentDescriptor] = {}

    def reserve_idempotency(
        self, key: str, scope: str, *, stale_reclaim_after_sec: int | None = None
    ) -> tuple[bool, IdempotencyRecord | None]:
        with self._lock:
            row = self._idempotency.get((scope, key))
            if row is not None:
                if (
                    stale_reclaim_after_sec is not None
                    and row.status == A2A_IDEMPOTENCY_STATUS_IN_PROGRESS
                    and idempotency_slot_is_stale(
                        row.updated_at, stale_after_sec=stale_reclaim_after_sec
                    )
                ):
                    now = iso_now()
                    reclaimed = IdempotencyRecord(
                        key=key,
                        scope=scope,
                        status=A2A_IDEMPOTENCY_STATUS_IN_PROGRESS,
                        created_at=now,
                        updated_at=now,
                    )
                    self._idempotency[(scope, key)] = reclaimed
                    return True, copy.deepcopy(reclaimed)
                return False, copy.deepcopy(row)
            now = iso_now()
            rec = IdempotencyRecord(
                key=key,
                scope=scope,
                status=A2A_IDEMPOTENCY_STATUS_IN_PROGRESS,
                created_at=now,
                updated_at=now,
            )
            self._idempotency[(scope, key)] = rec
            return True, copy.deepcopy(rec)

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
    ) -> IdempotencyRecord:
        with self._lock:
            now = iso_now()
            existing = self._idempotency.get((scope, key))
            if existing is None:
                existing = IdempotencyRecord(key=key, scope=scope, status=status)
            rec = replace(
                existing,
                status=status,
                result_inline=copy.deepcopy(result_inline),
                result_ref=result_ref,
                error=copy.deepcopy(error),
                task_id=task_id,
                updated_at=now,
            )
            if not rec.created_at:
                rec.created_at = now
            self._idempotency[(scope, key)] = rec
            return copy.deepcopy(rec)

    def create_job(self, job: JobRecord) -> str:
        with self._lock:
            self._jobs[job.task_id] = copy.deepcopy(job)
            return job.task_id

    def update_job(self, task_id: str, patch: dict) -> JobRecord:
        with self._lock:
            current = self._jobs.get(task_id)
            if current is None:
                raise KeyError(task_id)
            data = current.to_dict()
            data.update(copy.deepcopy(patch))
            data.setdefault("updated_at", iso_now())
            if "heartbeat_at" not in patch:
                data["heartbeat_at"] = data.get("heartbeat_at") or iso_now()
            next_job = JobRecord(**data)
            self._jobs[task_id] = next_job
            return copy.deepcopy(next_job)

    def get_job(self, task_id: str) -> JobRecord | None:
        with self._lock:
            row = self._jobs.get(task_id)
            return None if row is None else copy.deepcopy(row)

    def list_jobs(self, filter_by: dict | None = None) -> list[JobRecord]:
        with self._lock:
            rows = list(self._jobs.values())
        filter_by = filter_by or {}
        out: list[JobRecord] = []
        states = filter_by.get("states")
        state_set = set(states) if states else None
        state = filter_by.get("state")
        owner_agent_id = str(filter_by.get("owner_agent_id") or "").strip()
        session_id = str(filter_by.get("session_id") or "").strip()
        for row in rows:
            if state and row.state != state:
                continue
            if state_set and row.state not in state_set:
                continue
            if owner_agent_id and row.owner_agent_id != owner_agent_id:
                continue
            if session_id and row.idempotency_scope != (
                f"job.start:{row.agent_id}:{row.method}:{session_id}"
            ):
                continue
            out.append(copy.deepcopy(row))
        if filter_by.get("order") == "recent":
            out.sort(key=lambda item: (item.created_at, item.task_id), reverse=True)
        limit = max(1, min(int(filter_by.get("limit", 500)), 5000))
        return out[:limit]

    def upsert_agent(self, descriptor: AgentDescriptor) -> None:
        with self._lock:
            self._agents[descriptor.agent_id] = copy.deepcopy(descriptor)

    def list_agents(self) -> list[AgentDescriptor]:
        with self._lock:
            items = list(self._agents.values())
        return [copy.deepcopy(item) for item in items]

    def close(self) -> None:
        return None


class MemoryAuditStore(AuditStore):
    def __init__(
        self,
        *,
        capture_payloads: bool = False,
        retention_days: int = 14,
    ) -> None:
        self._lock = threading.RLock()
        self._rows: list[AuditRecord] = []
        self.capture_payloads = bool(capture_payloads)
        self.retention_days, _ = validate_audit_retention(retention_days, 0)

    def append_audit(self, record: AuditRecord) -> None:
        with self._lock:
            self._rows.append(
                audit_record_for_storage(
                    record,
                    capture_payloads=self.capture_payloads,
                )
            )
            self._enforce_retention()

    def query_audit(self, filter_by: dict | None = None) -> list[AuditRecord]:
        with self._lock:
            self._enforce_retention()
            rows = list(self._rows)

        filter_by = dict(filter_by or {})
        trace_id, from_agent, to_agent, method, status = (
            _as_text(filter_by.get(key))
            for key in ("trace_id", "from_agent", "to_agent", "method", "status")
        )
        error_only = bool(filter_by.get("error_only"))
        limit = max(1, min(int(filter_by.get("limit", 1000)), 50_000))

        out: list[AuditRecord] = []
        for row in rows:
            if trace_id and row.trace_id != trace_id:
                continue
            if from_agent and row.from_agent != from_agent:
                continue
            if to_agent and (row.to_agent or "") != to_agent:
                continue
            if method and row.method != method:
                continue
            if status and row.status != status:
                continue
            if error_only and not row.error_code:
                continue
            out.append(copy.deepcopy(row))

        out.sort(key=lambda item: (item.ts, item.msg_id), reverse=True)
        selected = out[:limit]
        selected.reverse()
        return selected

    def close(self) -> None:
        return None

    def _enforce_retention(self) -> None:
        cutoff = datetime.now(timezone.utc).date() - timedelta(days=self.retention_days)
        self._rows = [row for row in self._rows if _record_date(row.ts) >= cutoff]


def _as_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _record_date(value: str) -> date:
    try:
        return (
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            .astimezone(timezone.utc)
            .date()
        )
    except ValueError:
        return datetime.now(timezone.utc).date()
