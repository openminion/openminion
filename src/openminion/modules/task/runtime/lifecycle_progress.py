# mypy: ignore-errors
from __future__ import annotations

from typing import Any, Mapping

from .lifecycle_models import TaskLifecycleRecord


class TaskManagerProgressMixin:
    def save_checkpoint(
        self,
        task_id: str,
        checkpoint_id: str,
        state: Mapping[str, Any],
    ) -> None:
        self._lifecycle_repository.save_checkpoint(
            task_id=task_id,
            checkpoint_id=checkpoint_id,
            state=state,
        )
        record = self.get_task(task_id)
        if record is None:
            raise KeyError(f"task not found: {task_id}")
        metadata = dict(record.metadata)
        metadata["last_checkpoint_id"] = str(checkpoint_id)
        self._lifecycle_repository.update_metadata(task_id=task_id, metadata=metadata)

    def get_latest_checkpoint(self, task_id: str) -> tuple[str, dict[str, Any]] | None:
        return self._lifecycle_repository.get_latest_checkpoint(task_id=task_id)

    def get_checkpoint(self, task_id: str, checkpoint_id: str) -> dict[str, Any] | None:
        return self._lifecycle_repository.get_checkpoint(
            task_id=task_id,
            checkpoint_id=checkpoint_id,
        )

    def list_checkpoints(self, task_id: str) -> list[str]:
        return self._lifecycle_repository.list_checkpoints(task_id=task_id)

    def update_progress(self, task_id: str, progress: Mapping[str, Any]) -> None:
        record = self.get_task(task_id)
        if record is None:
            raise KeyError(f"task not found: {task_id}")
        metadata = dict(record.metadata)
        progress_payload = dict(metadata.get("progress", {}) or {})
        progress_payload.update(dict(progress))
        metadata["progress"] = progress_payload
        checkpoint_id = str(progress_payload.get("last_checkpoint_id") or "").strip()
        if checkpoint_id:
            metadata["last_checkpoint_id"] = checkpoint_id
        self._lifecycle_repository.update_metadata(task_id=task_id, metadata=metadata)

    def update_task_metadata(
        self,
        *,
        task_id: str,
        metadata: Mapping[str, Any],
    ) -> TaskLifecycleRecord:
        return self._lifecycle_repository.update_metadata(
            task_id=task_id,
            metadata=metadata,
        )
