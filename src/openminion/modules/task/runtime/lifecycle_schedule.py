# mypy: ignore-errors
from __future__ import annotations

from typing import Any
from collections.abc import Mapping

from .lifecycle_models import TaskLifecycleRecord, TaskLifecycleState


class TaskManagerScheduleMixin:
    def schedule_task(
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
    ) -> TaskLifecycleRecord:
        created_job_id = self._cron_repository.add_cron_job(
            name=name,
            schedule=schedule,
            payload=payload,
            description=description,
            enabled=enabled,
            agent_id=agent_id,
            session_target=session_target,
            wake_mode=wake_mode,
            delivery=delivery,
            delete_after_run=delete_after_run,
            misfire_policy=misfire_policy,
            max_lateness_s=max_lateness_s,
            max_concurrency=max_concurrency,
            job_id=job_id,
        )
        try:
            return self.ensure_task_record(
                cron_job_id=created_job_id,
                agent_id=agent_id,
                task_id=created_job_id,
            )
        except Exception:
            try:
                self._cron_repository.delete_cron_job(created_job_id)
            except Exception:
                pass
            raise

    def get_scheduled_job(self, task_id: str) -> dict[str, Any] | None:
        return self._cron_repository.get_cron_job(task_id)

    def list_scheduled_jobs(self, *, limit: int) -> list[dict[str, Any]]:
        return self._cron_repository.list_cron_jobs(limit=limit)

    def set_scheduled_job_enabled(
        self, task_id: str, *, enabled: bool
    ) -> dict[str, Any]:
        record = self.get_task(task_id) or self.get_task_by_job(task_id)
        job = self.get_scheduled_job(task_id)
        if record is None and job is not None:
            record = self.ensure_task_record_for_job(job)
        if record is None:
            raise KeyError(f"task not found: {task_id}")
        self._cron_repository.set_cron_job_enabled(record.cron_job_id, enabled)
        refreshed = self.get_scheduled_job(record.cron_job_id)
        if refreshed is None:
            raise KeyError(f"task not found: {task_id}")
        return refreshed

    def list_scheduled_runs(
        self,
        *,
        job_id: str | None = None,
        limit: int = 100,
        states: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        return self._cron_repository.list_cron_runs(
            job_id=job_id,
            limit=limit,
            states=states,
        )

    def create_cron_job(
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
        return self._cron_repository.add_cron_job(
            name=name,
            schedule=schedule,
            payload=payload,
            description=description,
            enabled=enabled,
            agent_id=agent_id,
            session_target=session_target,
            wake_mode=wake_mode,
            delivery=delivery,
            delete_after_run=delete_after_run,
            misfire_policy=misfire_policy,
            max_lateness_s=max_lateness_s,
            max_concurrency=max_concurrency,
            job_id=job_id,
        )

    def replace_cron_job_payload(
        self,
        job_id: str,
        payload: Mapping[str, Any],
    ) -> None:
        replacer = getattr(self._cron_repository, "replace_cron_job_payload", None)
        if not callable(replacer):
            raise NotImplementedError("Cron payload replacement is unavailable")
        replacer(job_id, payload)

    def delete_scheduled_job(self, job_id: str) -> None:
        normalized = str(job_id or "").strip()
        if not normalized:
            return
        try:
            self._cron_repository.delete_cron_job(normalized)
        except Exception:
            pass

    def get_linked_cron_job(self, task_id: str) -> str | None:
        record = self.get_task(task_id)
        if record is None:
            return None
        linked = str(record.metadata.get("linked_cron_job_id") or "").strip()
        return linked or None

    def find_task_by_linked_cron_job(
        self, cron_job_id: str
    ) -> TaskLifecycleRecord | None:
        normalized = str(cron_job_id or "").strip()
        if not normalized:
            return None
        for record in self._lifecycle_repository.list(limit=1000):
            linked = str(record.metadata.get("linked_cron_job_id") or "").strip()
            if linked == normalized:
                return record
        return None

    def cancel_task(self, task_id: str) -> TaskLifecycleRecord:
        record = self.get_task(task_id) or self.get_task_by_job(task_id)
        if record is None:
            job = self.get_scheduled_job(task_id)
            if job is None:
                raise KeyError(f"task not found: {task_id}")
            record = self.ensure_task_record_for_job(job)
        self._cron_repository.delete_cron_job(record.cron_job_id)
        return self.transition_task(
            task_id=record.task_id,
            to_state=TaskLifecycleState.CANCELLED,
        )

    def pause_task(self, task_id: str) -> tuple[TaskLifecycleRecord, dict[str, Any]]:
        record = self.get_task(task_id) or self.get_task_by_job(task_id)
        job = self.get_scheduled_job(task_id)
        if record is None and job is not None:
            record = self.ensure_task_record_for_job(job)
        if record is None:
            raise KeyError(f"task not found: {task_id}")
        self._cron_repository.set_cron_job_enabled(record.cron_job_id, False)
        record = self.transition_task(
            task_id=record.task_id,
            to_state=TaskLifecycleState.PAUSED,
        )
        refreshed = self.get_scheduled_job(record.cron_job_id)
        if refreshed is None:
            raise KeyError(f"task not found: {task_id}")
        return record, refreshed

    def resume_task(self, task_id: str) -> tuple[TaskLifecycleRecord, dict[str, Any]]:
        record = self.get_task(task_id) or self.get_task_by_job(task_id)
        job = self.get_scheduled_job(task_id)
        if record is None and job is not None:
            record = self.ensure_task_record_for_job(job)
        if record is None:
            raise KeyError(f"task not found: {task_id}")
        self._cron_repository.set_cron_job_enabled(record.cron_job_id, True)
        record = self.transition_task(
            task_id=record.task_id,
            to_state=TaskLifecycleState.ACTIVE,
        )
        refreshed = self.get_scheduled_job(record.cron_job_id)
        if refreshed is None:
            raise KeyError(f"task not found: {task_id}")
        return record, refreshed

    def _cleanup_linked_cron_job(self, *, task_id: str) -> None:
        record = self.get_task(task_id)
        if record is None:
            return
        metadata = dict(record.metadata)
        linked_cron_job_id = str(metadata.get("linked_cron_job_id") or "").strip()
        if not linked_cron_job_id:
            return
        try:
            self.delete_scheduled_job(linked_cron_job_id)
        finally:
            metadata.pop("linked_cron_job_id", None)
            self.update_task_metadata(task_id=task_id, metadata=metadata)
