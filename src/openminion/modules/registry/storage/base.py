from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from openminion.modules.registry.models import (
    AgentDescriptor,
    AgentRecord,
    AgentStatus,
    MethodIndexRow,
    RegistrySource,
)


class RegistryStore(ABC):
    @abstractmethod
    def upsert_agent(self, descriptor: AgentDescriptor, source: RegistrySource) -> None:
        raise NotImplementedError

    @abstractmethod
    def delete_agent(self, agent_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_agent(self, agent_id: str) -> AgentDescriptor | None:
        raise NotImplementedError

    @abstractmethod
    def get_agent_record(self, agent_id: str) -> AgentRecord | None:
        raise NotImplementedError

    @abstractmethod
    def list_agent_records(
        self, filters: dict[str, Any] | None = None
    ) -> list[AgentRecord]:
        raise NotImplementedError

    def list_agents(
        self, filters: dict[str, Any] | None = None
    ) -> list[AgentDescriptor]:
        return [row.descriptor for row in self.list_agent_records(filters=filters)]

    @abstractmethod
    def upsert_status(self, agent_id: str, status: AgentStatus) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_status(self, agent_id: str) -> AgentStatus | None:
        raise NotImplementedError

    @abstractmethod
    def list_status(self, filters: dict[str, Any] | None = None) -> list[AgentStatus]:
        raise NotImplementedError

    @abstractmethod
    def find_agent_ids_by_method(self, method: str) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    def get_method_rows(self, method: str) -> list[MethodIndexRow]:
        raise NotImplementedError

    def close(self) -> None:
        return None
