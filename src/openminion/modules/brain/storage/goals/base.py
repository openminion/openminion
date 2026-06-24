from abc import ABC, abstractmethod

from openminion.modules.brain.schemas import (
    ExternalBlocker,
    Goal,
    GoalStatus,
    LifecycleAuditRecord,
)
from openminion.modules.brain.schemas.goals import GoalDriftSignal
from openminion.modules.storage.record_store import RecordStore


class GoalStore(ABC):
    record_store: RecordStore

    @abstractmethod
    def create(self, goal: Goal) -> Goal: ...

    @abstractmethod
    def get(self, goal_id: str) -> Goal | None: ...

    @abstractmethod
    def list_active(self) -> list[Goal]: ...

    @abstractmethod
    def list_by_parent(self, parent_goal_id: str) -> list[Goal]: ...

    @abstractmethod
    def list_by_plan_id(self, plan_id: str) -> list[Goal]: ...

    @abstractmethod
    def transition_status(
        self, goal_id: str, new_status: GoalStatus | str, reason: str = ""
    ) -> Goal: ...

    @abstractmethod
    def replace(self, goal: Goal, *, reason: str = "") -> Goal: ...

    @abstractmethod
    def set_apd_plan_id(self, goal_id: str, plan_id: str) -> Goal: ...

    @abstractmethod
    def pause(self, goal_id: str, *, reason: str = "") -> Goal: ...

    @abstractmethod
    def resume(self, goal_id: str, *, reason: str = "") -> Goal: ...

    @abstractmethod
    def abort(self, goal_id: str, *, reason: str = "") -> Goal: ...

    @abstractmethod
    def set_owner(self, goal_id: str, owner_agent_id: str | None) -> Goal: ...

    @abstractmethod
    def transfer_owner(
        self,
        goal_id: str,
        *,
        from_agent: str,
        to_agent: str,
        reason: str,
    ) -> Goal: ...

    @abstractmethod
    def add_external_blocker(self, goal_id: str, blocker: ExternalBlocker) -> Goal: ...

    @abstractmethod
    def clear_external_blocker(self, goal_id: str, blocker_id: str) -> Goal: ...

    @abstractmethod
    def list_goal_audit_trail(self, goal_id: str) -> list[LifecycleAuditRecord]: ...

    @abstractmethod
    def record_drift_signal_audit(self, signal: GoalDriftSignal) -> None: ...
