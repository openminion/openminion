from abc import ABC, abstractmethod

from openminion.modules.brain.schemas import (
    LifecycleAuditRecord,
    MissionState,
    MissionLifecycleStatus,
)
from openminion.modules.storage.record_store import RecordStore


class MissionStateStore(ABC):
    record_store: RecordStore

    @abstractmethod
    def create(self, state: MissionState) -> MissionState: ...

    @abstractmethod
    def get(self, mission_id: str) -> MissionState | None: ...

    @abstractmethod
    def list_active(self) -> list[MissionState]: ...

    @abstractmethod
    def transition_status(
        self,
        mission_id: str,
        new_status: MissionLifecycleStatus | str,
        reason: str = "",
    ) -> MissionState: ...

    @abstractmethod
    def pause(self, mission_id: str, *, reason: str = "") -> MissionState: ...

    @abstractmethod
    def resume(self, mission_id: str, *, reason: str = "") -> MissionState: ...

    @abstractmethod
    def abort(self, mission_id: str, *, reason: str = "") -> MissionState: ...

    @abstractmethod
    def list_mission_audit_trail(
        self,
        mission_id: str,
    ) -> list[LifecycleAuditRecord]: ...
