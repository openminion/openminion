from pathlib import Path

from openminion.modules.storage.runtime.module_store import BaseModuleSQLiteStore
from openminion.modules.storage.record_store import RecordStore

from .base import GoalStore
from .migrations import list_migrations
from .repository import SqlGoalRepository


def ensure_schema(store: RecordStore) -> None:
    store.execute_count(
        """
        CREATE TABLE IF NOT EXISTS goals (
            goal_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            description TEXT NOT NULL,
            parent_goal_id TEXT,
            apd_plan_id TEXT,
            goal_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            latest_reason TEXT NOT NULL DEFAULT ''
        )
        """
    )
    store.execute_count(
        """
        CREATE INDEX IF NOT EXISTS idx_goals_status_updated
          ON goals(status, updated_at DESC)
        """
    )
    store.execute_count(
        """
        CREATE INDEX IF NOT EXISTS idx_goals_parent_updated
          ON goals(parent_goal_id, updated_at DESC)
        """
    )
    store.execute_count(
        """
        CREATE INDEX IF NOT EXISTS idx_goals_plan
          ON goals(apd_plan_id)
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


class SQLiteGoalStore(BaseModuleSQLiteStore, GoalStore):
    def __init__(self, sqlite_path: str | Path, *, wal: bool = True) -> None:
        super().__init__(sqlite_path, wal=wal)
        self.record_store = self._record_store
        self._repo = SqlGoalRepository(self._record_store)

    def _init_schema(self) -> None:
        ensure_schema(self._record_store)

    def _list_migrations(self) -> list[str]:
        return list_migrations()

    def _module_package(self) -> str:
        return "openminion.modules.brain.storage"

    def create(self, goal):
        return self._repo.create(goal)

    def get(self, goal_id: str):
        return self._repo.get(goal_id)

    def list_active(self):
        return self._repo.list_active()

    def list_by_parent(self, parent_goal_id: str):
        return self._repo.list_by_parent(parent_goal_id)

    def list_by_plan_id(self, plan_id: str):
        return self._repo.list_by_plan_id(plan_id)

    def transition_status(self, goal_id: str, new_status, reason: str = ""):
        return self._repo.transition_status(goal_id, new_status, reason)

    def replace(self, goal, *, reason: str = ""):
        return self._repo.replace(goal, reason=reason)

    def set_apd_plan_id(self, goal_id: str, plan_id: str):
        return self._repo.set_apd_plan_id(goal_id, plan_id)

    def pause(self, goal_id: str, *, reason: str = ""):
        return self._repo.pause(goal_id, reason=reason)

    def resume(self, goal_id: str, *, reason: str = ""):
        return self._repo.resume(goal_id, reason=reason)

    def abort(self, goal_id: str, *, reason: str = ""):
        return self._repo.abort(goal_id, reason=reason)

    def set_owner(self, goal_id: str, owner_agent_id: str | None):
        return self._repo.set_owner(goal_id, owner_agent_id)

    def transfer_owner(
        self, goal_id: str, *, from_agent: str, to_agent: str, reason: str
    ):
        return self._repo.transfer_owner(
            goal_id,
            from_agent=from_agent,
            to_agent=to_agent,
            reason=reason,
        )

    def add_external_blocker(self, goal_id: str, blocker):
        return self._repo.add_external_blocker(goal_id, blocker)

    def clear_external_blocker(self, goal_id: str, blocker_id: str):
        return self._repo.clear_external_blocker(goal_id, blocker_id)

    def list_goal_audit_trail(self, goal_id: str):
        return self._repo.list_goal_audit_trail(goal_id)

    def record_drift_signal_audit(self, signal):
        return self._repo.record_drift_signal_audit(signal)


__all__ = ["SQLiteGoalStore", "ensure_schema"]
