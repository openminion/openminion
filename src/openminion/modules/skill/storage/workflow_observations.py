from __future__ import annotations

import sqlite3
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from openminion.modules.storage.record_store import RecordStore


class WorkflowObservationStorageError(RuntimeError):
    pass


class WorkflowObservationStoreMixin:
    _record_store: RecordStore

    def insert_workflow_observation(
        self,
        *,
        agent_id: str,
        source_run_ref: str,
        bundle_id: str,
        provenance_checksum: str,
        bundle_json: str,
        created_at: str,
    ) -> bool:
        try:
            return bool(
                self._record_store.insert_if_absent(
                    "skill_workflow_observations",
                    {
                        "agent_id": agent_id,
                        "source_run_ref": source_run_ref,
                        "bundle_id": bundle_id,
                        "provenance_checksum": provenance_checksum,
                        "bundle_json": bundle_json,
                        "created_at": created_at,
                    },
                    conflict_columns=("agent_id", "source_run_ref"),
                )
            )
        except (sqlite3.Error, SQLAlchemyError) as exc:
            raise WorkflowObservationStorageError from exc

    def get_workflow_observation(
        self, *, agent_id: str, source_run_ref: str
    ) -> dict[str, Any] | None:
        try:
            rows = self._record_store.query_rows(
                "skill_workflow_observations",
                where={"agent_id": agent_id, "source_run_ref": source_run_ref},
                limit=1,
            )
        except (sqlite3.Error, SQLAlchemyError) as exc:
            raise WorkflowObservationStorageError from exc
        return dict(rows[0]) if rows else None

    def list_workflow_observations(self, *, agent_id: str) -> list[dict[str, Any]]:
        try:
            return [
                dict(row)
                for row in self._record_store.query_rows(
                    "skill_workflow_observations",
                    where={"agent_id": agent_id},
                    order="created_at ASC, source_run_ref ASC",
                )
            ]
        except (sqlite3.Error, SQLAlchemyError) as exc:
            raise WorkflowObservationStorageError from exc
