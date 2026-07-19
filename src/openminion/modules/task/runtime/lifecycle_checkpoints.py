# mypy: ignore-errors
from __future__ import annotations

from typing import Any, Mapping

from openminion.base.time import utc_now_iso as _utc_now_iso

from .lifecycle_models import _dump_state_blob, _load_state_blob


class TaskLifecycleRepositoryCheckpointMixin:
    def save_checkpoint(
        self,
        *,
        task_id: str,
        checkpoint_id: str,
        state: Mapping[str, Any],
    ) -> None:
        normalized_task_id = str(task_id or "").strip()
        normalized_checkpoint_id = str(checkpoint_id or "").strip()
        if not normalized_task_id:
            raise ValueError("task_id is required")
        if not normalized_checkpoint_id:
            raise ValueError("checkpoint_id is required")
        if self.get(normalized_task_id) is None:
            raise KeyError(f"task not found: {normalized_task_id}")
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO task_checkpoints(
                    task_id,
                    checkpoint_id,
                    created_at,
                    state_json
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    normalized_task_id,
                    normalized_checkpoint_id,
                    _utc_now_iso(),
                    _dump_state_blob(state),
                ),
            )
            self._conn.commit()

    def get_latest_checkpoint(
        self, *, task_id: str
    ) -> tuple[str, dict[str, Any]] | None:
        normalized = str(task_id or "").strip()
        if not normalized:
            raise ValueError("task_id is required")
        with self._lock:
            row = self._conn.execute(
                """
                SELECT checkpoint_id, state_json
                FROM task_checkpoints
                WHERE task_id = ?
                ORDER BY created_at DESC, checkpoint_id DESC
                LIMIT 1
                """,
                (normalized,),
            ).fetchone()
        if row is None:
            return None
        return str(row["checkpoint_id"]), _load_state_blob(row["state_json"])

    def get_checkpoint(
        self,
        *,
        task_id: str,
        checkpoint_id: str,
    ) -> dict[str, Any] | None:
        normalized_task_id = str(task_id or "").strip()
        normalized_checkpoint_id = str(checkpoint_id or "").strip()
        if not normalized_task_id:
            raise ValueError("task_id is required")
        if not normalized_checkpoint_id:
            raise ValueError("checkpoint_id is required")
        with self._lock:
            row = self._conn.execute(
                """
                SELECT state_json
                FROM task_checkpoints
                WHERE task_id = ?
                  AND checkpoint_id = ?
                LIMIT 1
                """,
                (normalized_task_id, normalized_checkpoint_id),
            ).fetchone()
        if row is None:
            return None
        return _load_state_blob(row["state_json"])

    def list_checkpoints(self, *, task_id: str) -> list[str]:
        normalized = str(task_id or "").strip()
        if not normalized:
            raise ValueError("task_id is required")
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT checkpoint_id
                FROM task_checkpoints
                WHERE task_id = ?
                ORDER BY created_at ASC, checkpoint_id ASC
                """,
                (normalized,),
            ).fetchall()
        return [str(row["checkpoint_id"]) for row in rows]
