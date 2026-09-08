from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable
from collections.abc import Mapping

from openminion.base.redaction import redact_mapping, redact_sensitive_text
from openminion.modules.task.constants import TASK_RUN_ERROR_DETAILS_MAX_JSON_CHARS


@runtime_checkable
class TaskCronStoreProtocol(Protocol):
    def add_cron_job(self, **kwargs: Any) -> str: ...

    def delete_cron_job(self, job_id: str) -> None: ...

    def get_cron_job(self, job_id: str) -> dict[str, Any] | None: ...

    def list_cron_jobs(self, *, limit: int = 100) -> list[dict[str, Any]]: ...

    def set_cron_job_enabled(
        self,
        job_id: str,
        enabled: bool,
        *,
        cancel_queued: bool = False,
    ) -> None: ...

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


def bounded_task_run_error(error: Any) -> dict[str, Any] | None:
    if not isinstance(error, Mapping):
        return None
    code, _ = redact_sensitive_text(str(error.get("code") or "failed"))
    message, _ = redact_sensitive_text(str(error.get("message") or ""))
    result: dict[str, Any] = {
        "code": code[:100],
        "message": message[:500],
    }
    details = error.get("details")
    if isinstance(details, Mapping):
        redacted, _ = redact_mapping(details)
        encoded = json.dumps(redacted, ensure_ascii=True, default=str, sort_keys=True)
        encoded, _ = redact_sensitive_text(encoded)
        if len(encoded) > TASK_RUN_ERROR_DETAILS_MAX_JSON_CHARS:
            result["details"] = {
                "summary": encoded[:TASK_RUN_ERROR_DETAILS_MAX_JSON_CHARS],
                "truncated": True,
            }
        else:
            result["details"] = json.loads(encoded)
    return result


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
    return _load_metadata(raw)


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


@dataclass(frozen=True)
class ProjectCycleClaim:
    task_id: str
    owner_id: str
    fence_token: int
    expected_checkpoint_id: str | None
    expires_at: str


class ProjectCycleClaimUnavailable(RuntimeError):
    """Raised when another live project cycle owns the expected checkpoint."""


class StaleProjectCycleClaim(RuntimeError):
    """Raised when a project cycle no longer has checkpoint commit authority."""


def _new_task_id() -> str:
    return str(uuid.uuid4())


class _NullCronRepository:
    def add_cron_job(self, **kwargs: Any) -> str:
        raise NotImplementedError("Cron scheduling is unavailable for linked tasks")

    def delete_cron_job(self, job_id: str) -> None:
        pass

    def get_cron_job(self, job_id: str) -> dict[str, Any] | None:
        return None

    def list_cron_jobs(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return []

    def set_cron_job_enabled(
        self,
        job_id: str,
        enabled: bool,
        *,
        cancel_queued: bool = False,
    ) -> None:
        del cancel_queued
        pass

    def list_cron_runs(
        self,
        *,
        job_id: str | None = None,
        limit: int = 100,
        states: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        return []


__all__ = [
    "ProjectCycleClaim",
    "ProjectCycleClaimUnavailable",
    "StaleProjectCycleClaim",
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
