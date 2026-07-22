# mypy: ignore-errors
from __future__ import annotations

from pathlib import Path
from typing import Any
from collections.abc import Mapping

from .lifecycle_progress import TaskManagerProgressMixin
from .lifecycle_schedule import TaskManagerScheduleMixin
from .lifecycle_models import (
    _NullCronRepository,
    _TERMINAL_TASK_STATES,
    _new_task_id,
    TaskCronStoreProtocol,
    TaskLifecycleRecord,
    TaskLifecycleState,
)
from .lifecycle_repository import TaskLifecycleRepository


class TaskManager(TaskManagerProgressMixin, TaskManagerScheduleMixin):
    """Lifecycle layer for scheduled tasks and task-backed runtime work."""

    def __init__(
        self,
        *,
        cron_repository: TaskCronStoreProtocol,
        lifecycle_repository: TaskLifecycleRepository,
    ) -> None:
        self._cron_repository = cron_repository
        self._lifecycle_repository = lifecycle_repository

    @property
    def lifecycle_repository(self) -> TaskLifecycleRepository:
        return self._lifecycle_repository

    @classmethod
    def from_cron_repository(
        cls,
        cron_repository: TaskCronStoreProtocol,
        *,
        db_path: str | Path | None = None,
    ) -> TaskManager:
        path_hint = db_path or getattr(cron_repository, "db_path", None)
        if path_hint is None:
            path_hint = ":memory:"
        return cls(
            cron_repository=cron_repository,
            lifecycle_repository=TaskLifecycleRepository(db_path=path_hint),
        )

    @classmethod
    def for_lifecycle_db(cls, *, db_path: str | Path) -> TaskManager:
        return cls(
            cron_repository=_NullCronRepository(),  # type: ignore[arg-type]
            lifecycle_repository=TaskLifecycleRepository(db_path=db_path),
        )

    def ensure_task_record(
        self,
        *,
        cron_job_id: str,
        agent_id: str | None,
        task_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> TaskLifecycleRecord:
        effective_task_id = str(task_id or cron_job_id).strip()
        existing = self._lifecycle_repository.get(effective_task_id)
        if existing is not None:
            return existing
        return self._lifecycle_repository.create(
            task_id=effective_task_id,
            cron_job_id=str(cron_job_id).strip(),
            agent_id=agent_id,
            state=TaskLifecycleState.ACTIVE,
            metadata=metadata,
        )

    def ensure_task_record_for_job(self, job: Mapping[str, Any]) -> TaskLifecycleRecord:
        job_id = str(job.get("job_id") or "").strip()
        if not job_id:
            raise ValueError("cron job payload is missing job_id")
        return self.ensure_task_record(
            cron_job_id=job_id,
            agent_id=str(job.get("agent_id") or "").strip() or None,
            task_id=job_id,
        )

    def create_linked_task(
        self,
        *,
        linked_job_id: str,
        agent_id: str | None,
        metadata: Mapping[str, Any] | None = None,
        task_id: str | None = None,
    ) -> TaskLifecycleRecord:
        normalized_job_id = str(linked_job_id or "").strip()
        if not normalized_job_id:
            raise ValueError("linked_job_id is required")
        return self._lifecycle_repository.create(
            task_id=str(task_id or _new_task_id()).strip(),
            cron_job_id=normalized_job_id,
            agent_id=agent_id,
            state=TaskLifecycleState.ACTIVE,
            metadata=metadata,
        )

    def create_task(
        self,
        *,
        session_id: str,
        mode_name: str,
        goal: str,
        agent_id: str | None,
        metadata: Mapping[str, Any] | None = None,
        task_id: str | None = None,
    ) -> TaskLifecycleRecord:
        normalized_session_id = str(session_id or "").strip()
        normalized_mode_name = str(mode_name or "").strip()
        normalized_goal = str(goal or "").strip()
        if not normalized_session_id:
            raise ValueError("session_id is required")
        if not normalized_mode_name:
            raise ValueError("mode_name is required")
        if not normalized_goal:
            raise ValueError("goal is required")
        effective_task_id = str(task_id or _new_task_id()).strip()
        merged_metadata = {
            "kind": "mode",
            "parent_session_id": normalized_session_id,
            "mode_name": normalized_mode_name,
            "goal": normalized_goal,
            "resume_count": 0,
        }
        if metadata:
            merged_metadata.update(dict(metadata))
        return self._lifecycle_repository.create(
            task_id=effective_task_id,
            cron_job_id=effective_task_id,
            agent_id=agent_id,
            state=TaskLifecycleState.ACTIVE,
            metadata=merged_metadata,
        )

    def get_task(self, task_id: str) -> TaskLifecycleRecord | None:
        return self._lifecycle_repository.get(task_id)

    def get_task_by_job(self, cron_job_id: str) -> TaskLifecycleRecord | None:
        return self._lifecycle_repository.get_by_cron_job_id(cron_job_id)

    def list_open_tasks_for_session(
        self,
        session_id: str,
        *,
        mode_name: str | None = None,
        limit: int = 100,
    ) -> list[TaskLifecycleRecord]:
        normalized_session_id = str(session_id or "").strip()
        normalized_mode_name = str(mode_name or "").strip() or None
        if not normalized_session_id:
            raise ValueError("session_id is required")
        records = self._lifecycle_repository.list(limit=max(1, min(int(limit), 1000)))
        open_states = {TaskLifecycleState.ACTIVE, TaskLifecycleState.PAUSED}
        matched: list[TaskLifecycleRecord] = []
        for record in records:
            if record.state not in open_states:
                continue
            parent_session_id = str(
                record.metadata.get("parent_session_id")
                or record.metadata.get("session_id")
                or ""
            ).strip()
            if parent_session_id != normalized_session_id:
                continue
            record_mode_name = (
                str(record.metadata.get("mode_name") or "").strip() or None
            )
            if (
                normalized_mode_name is not None
                and record_mode_name != normalized_mode_name
            ):
                continue
            matched.append(record)
        return matched

    def transition_task(
        self,
        *,
        task_id: str,
        to_state: TaskLifecycleState | str,
        failure_reason: str | None = None,
    ) -> TaskLifecycleRecord:
        record = self._lifecycle_repository.transition(
            task_id=task_id,
            to_state=to_state,
            failure_reason=failure_reason,
        )
        if record.state in _TERMINAL_TASK_STATES:
            self._cleanup_linked_cron_job(task_id=record.task_id)
            refreshed = self.get_task(record.task_id)
            if refreshed is not None:
                return refreshed
        return record


__all__ = ["TaskManager"]
