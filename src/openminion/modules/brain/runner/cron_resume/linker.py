from typing import Any

from .contracts import CronJobLinker


def normalized_text(value: Any) -> str:
    return str(value or "").strip()


class DefaultCronJobLinker(CronJobLinker):
    def __init__(self, *, task_manager: Any) -> None:
        self._task_manager = task_manager

    def link(self, task_id: str, cron_job_id: str) -> None:
        normalized_task_id = normalized_text(task_id)
        normalized_cron_job_id = normalized_text(cron_job_id)
        if not normalized_task_id:
            raise ValueError("task_id is required")
        if not normalized_cron_job_id:
            raise ValueError("cron_job_id is required")
        record = self._task_manager.get_task(normalized_task_id)
        if record is None:
            raise KeyError(f"task not found: {normalized_task_id}")
        metadata = dict(getattr(record, "metadata", {}) or {})
        metadata["linked_cron_job_id"] = normalized_cron_job_id
        self._task_manager.update_task_metadata(
            task_id=normalized_task_id,
            metadata=metadata,
        )
        job = self._task_manager.get_scheduled_job(normalized_cron_job_id)
        if not isinstance(job, dict):
            return
        payload = dict(job.get("payload", {}) or {})
        payload["linked_task_id"] = normalized_task_id
        replace_payload = getattr(self._task_manager, "replace_cron_job_payload", None)
        if callable(replace_payload):
            replace_payload(normalized_cron_job_id, payload)

    def unlink_and_delete(self, task_id: str) -> None:
        normalized_task_id = normalized_text(task_id)
        if not normalized_task_id:
            return
        record = self._task_manager.get_task(normalized_task_id)
        if record is None:
            return
        metadata = dict(getattr(record, "metadata", {}) or {})
        linked_cron_job_id = normalized_text(metadata.get("linked_cron_job_id"))
        if not linked_cron_job_id:
            return
        try:
            self._task_manager.delete_scheduled_job(linked_cron_job_id)
        finally:
            metadata.pop("linked_cron_job_id", None)
            self._task_manager.update_task_metadata(
                task_id=normalized_task_id,
                metadata=metadata,
            )

    def get_linked_task(self, cron_job_id: str) -> str | None:
        normalized_cron_job_id = normalized_text(cron_job_id)
        if not normalized_cron_job_id:
            return None
        job = self._task_manager.get_scheduled_job(normalized_cron_job_id)
        if isinstance(job, dict):
            payload = dict(job.get("payload", {}) or {})
            linked_task_id = normalized_text(payload.get("linked_task_id"))
            if linked_task_id:
                return linked_task_id
        record = self._task_manager.find_task_by_linked_cron_job(normalized_cron_job_id)
        if record is None:
            return None
        return normalized_text(getattr(record, "task_id", None)) or None

    def get_linked_cron_job(self, task_id: str) -> str | None:
        normalized_task_id = normalized_text(task_id)
        if not normalized_task_id:
            return None
        record = self._task_manager.get_task(normalized_task_id)
        if record is None:
            return None
        metadata = dict(getattr(record, "metadata", {}) or {})
        linked_cron_job_id = normalized_text(metadata.get("linked_cron_job_id"))
        return linked_cron_job_id or None


def cleanup_linked_cron_job_for_task(*, task_manager: Any, task_id: str) -> None:
    DefaultCronJobLinker(task_manager=task_manager).unlink_and_delete(task_id)


__all__ = [
    "DefaultCronJobLinker",
    "cleanup_linked_cron_job_for_task",
    "normalized_text",
]
