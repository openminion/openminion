from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable
from collections.abc import Mapping


@runtime_checkable
class TaskCronStoreProtocol(Protocol):
    def add_cron_job(self, **kwargs: Any) -> str: ...

    def delete_cron_job(self, job_id: str) -> None: ...

    def get_cron_job(self, job_id: str) -> dict[str, Any] | None: ...

    def list_cron_jobs(self, *, limit: int = 100) -> list[dict[str, Any]]: ...

    def set_cron_job_enabled(self, job_id: str, enabled: bool) -> None: ...

    def list_cron_runs(
        self,
        *,
        job_id: str | None = None,
        limit: int = 100,
        states: list[str] | None = None,
    ) -> list[dict[str, Any]]: ...

class TaskLifecycleState(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    CANCELLED = "cancelled"
    DONE = "done"
    FAILED = "failed"



_TERMINAL_TASK_STATES = {
    TaskLifecycleState.CANCELLED,
    TaskLifecycleState.DONE,
    TaskLifecycleState.FAILED,
}


_ALLOWED_STATE_TRANSITIONS: dict[TaskLifecycleState, set[TaskLifecycleState]] = {
    TaskLifecycleState.ACTIVE: {
        TaskLifecycleState.PAUSED,
        TaskLifecycleState.CANCELLED,
        TaskLifecycleState.DONE,
        TaskLifecycleState.FAILED,
    },
    TaskLifecycleState.PAUSED: {
        TaskLifecycleState.ACTIVE,
        TaskLifecycleState.CANCELLED,
        TaskLifecycleState.DONE,
        TaskLifecycleState.FAILED,
    },
    TaskLifecycleState.CANCELLED: set(),
    TaskLifecycleState.DONE: set(),
    TaskLifecycleState.FAILED: set(),
}


def _normalize_task_state(value: TaskLifecycleState | str) -> TaskLifecycleState:
    if isinstance(value, TaskLifecycleState):
        return value
    normalized = str(value or "").strip().lower()
    for candidate in TaskLifecycleState:
        if normalized in {candidate.value, candidate.name.lower()}:
            return candidate
    raise ValueError(f"unknown task state: {value!r}")


def _dump_metadata(metadata: Mapping[str, Any] | None) -> str:
    return json.dumps(dict(metadata or {}), ensure_ascii=True, sort_keys=True)


def _load_metadata(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="ignore")
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    if isinstance(raw, Mapping):
        return dict(raw)
    return {}


def _dump_state_blob(state: Mapping[str, Any] | None) -> str:
    return json.dumps(dict(state or {}), ensure_ascii=True, sort_keys=True)


def _load_state_blob(raw: Any) -> dict[str, Any]:
    loaded = _load_metadata(raw)
    return loaded if isinstance(loaded, dict) else {}



@dataclass(frozen=True)
class TaskLifecycleRecord:
    task_id: str
    cron_job_id: str
    agent_id: str | None
    state: TaskLifecycleState
    created_at: str
    updated_at: str
    cancelled_at: str | None
    completed_at: str | None
    failed_at: str | None
    failure_reason: str | None
    metadata: dict[str, Any] = field(default_factory=dict)

def _new_task_id() -> str:
    return str(uuid.uuid4())

class _NullCronRepository:
    def add_cron_job(self, **kwargs: Any) -> str:
        raise NotImplementedError("Cron scheduling is unavailable for linked tasks")

    def delete_cron_job(self, job_id: str) -> None:
        del job_id

    def get_cron_job(self, job_id: str) -> dict[str, Any] | None:
        del job_id
        return None

    def list_cron_jobs(self, *, limit: int = 100) -> list[dict[str, Any]]:
        del limit
        return []

    def set_cron_job_enabled(self, job_id: str, enabled: bool) -> None:
        del job_id, enabled

    def list_cron_runs(
        self,
        *,
        job_id: str | None = None,
        limit: int = 100,
        states: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        del job_id, limit, states
        return []

__all__ = [
    "TaskCronStoreProtocol",
    "TaskLifecycleRecord",
    "TaskLifecycleState",
    "_ALLOWED_STATE_TRANSITIONS",
    "_NullCronRepository",
    "_TERMINAL_TASK_STATES",
    "_dump_metadata",
    "_dump_state_blob",
    "_load_metadata",
    "_load_state_blob",
    "_new_task_id",
    "_normalize_task_state",
]
