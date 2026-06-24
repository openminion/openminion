from pathlib import Path

from openminion.modules.storage.runtime.module_store import BaseModuleSQLiteStore
from openminion.modules.storage.record_store import RecordStore

from .base import MissionStateStore
from .migrations import list_migrations
from .repository import SqlMissionStateRepository


def ensure_schema(store: RecordStore) -> None:
    store.execute_count(
        """
        CREATE TABLE IF NOT EXISTS mission_states (
            mission_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            objective TEXT NOT NULL,
            task_id TEXT,
            mission_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            latest_reason TEXT NOT NULL DEFAULT ''
        )
        """
    )
    store.execute_count(
        """
        CREATE INDEX IF NOT EXISTS idx_mission_states_status_updated
          ON mission_states(status, updated_at DESC)
        """
    )
    store.execute_count(
        """
        CREATE INDEX IF NOT EXISTS idx_mission_states_task
          ON mission_states(task_id)
        """
    )
    store.execute_count(
        """
        CREATE TABLE IF NOT EXISTS goal_audit_trail (
            audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_kind TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            goal_id TEXT,
            mission_id TEXT,
            timestamp TEXT NOT NULL,
            prior_status TEXT,
            new_status TEXT,
            reason TEXT NOT NULL DEFAULT '',
            actor TEXT NOT NULL DEFAULT '',
            action_authorization_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    store.execute_count(
        """
        CREATE INDEX IF NOT EXISTS idx_goal_audit_entity_time
          ON goal_audit_trail(entity_kind, entity_id, timestamp, audit_id)
        """
    )


class SQLiteMissionStateStore(BaseModuleSQLiteStore, MissionStateStore):
    def __init__(self, sqlite_path: str | Path, *, wal: bool = True) -> None:
        super().__init__(sqlite_path, wal=wal)
        self.record_store = self._record_store
        self._repo = SqlMissionStateRepository(self._record_store)

    def _init_schema(self) -> None:
        ensure_schema(self._record_store)

    def _list_migrations(self) -> list[str]:
        return list_migrations()

    def _module_package(self) -> str:
        return "openminion.modules.brain.storage"

    def create(self, state):
        return self._repo.create(state)

    def get(self, mission_id: str):
        return self._repo.get(mission_id)

    def list_active(self):
        return self._repo.list_active()

    def transition_status(self, mission_id: str, new_status, reason: str = ""):
        return self._repo.transition_status(mission_id, new_status, reason)

    def pause(self, mission_id: str, *, reason: str = ""):
        return self._repo.pause(mission_id, reason=reason)

    def resume(self, mission_id: str, *, reason: str = ""):
        return self._repo.resume(mission_id, reason=reason)

    def abort(self, mission_id: str, *, reason: str = ""):
        return self._repo.abort(mission_id, reason=reason)

    def list_mission_audit_trail(self, mission_id: str):
        return self._repo.list_mission_audit_trail(mission_id)


__all__ = ["SQLiteMissionStateStore", "ensure_schema"]
