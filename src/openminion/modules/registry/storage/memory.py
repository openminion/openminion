from __future__ import annotations

from typing import Any

from openminion.modules.registry.models import (
    AgentDescriptor,
    AgentRecord,
    AgentStatus,
    MethodIndexRow,
    RegistrySource,
    extract_method_rows,
    iso_now,
)
from openminion.modules.registry.storage.base import RegistryStore


class InMemoryRegistryStore(RegistryStore):
    def __init__(self) -> None:
        self._agents: dict[str, AgentRecord] = {}
        self._status: dict[str, AgentStatus] = {}
        self._methods_by_agent: dict[str, dict[str, MethodIndexRow]] = {}
        self._agents_by_method: dict[str, set[str]] = {}

    def upsert_agent(self, descriptor: AgentDescriptor, source: RegistrySource) -> None:
        now = iso_now()
        rec = AgentRecord(
            agent_id=descriptor.agent_id,
            descriptor=descriptor,
            source=source,
            updated_at=now,
        )
        self._agents[descriptor.agent_id] = rec

        old_rows = self._methods_by_agent.pop(descriptor.agent_id, {})
        for method in old_rows:
            ids = self._agents_by_method.get(method)
            if ids is not None:
                ids.discard(descriptor.agent_id)
                if not ids:
                    self._agents_by_method.pop(method, None)

        rows = {row.method: row for row in extract_method_rows(descriptor)}
        self._methods_by_agent[descriptor.agent_id] = rows
        for method in rows:
            self._agents_by_method.setdefault(method, set()).add(descriptor.agent_id)

    def delete_agent(self, agent_id: str) -> None:
        self._agents.pop(agent_id, None)
        self._status.pop(agent_id, None)
        old_rows = self._methods_by_agent.pop(agent_id, {})
        for method in old_rows:
            ids = self._agents_by_method.get(method)
            if ids is not None:
                ids.discard(agent_id)
                if not ids:
                    self._agents_by_method.pop(method, None)

    def get_agent(self, agent_id: str) -> AgentDescriptor | None:
        rec = self._agents.get(agent_id)
        if rec is None:
            return None
        return rec.descriptor.model_copy(deep=True)

    def get_agent_record(self, agent_id: str) -> AgentRecord | None:
        rec = self._agents.get(agent_id)
        if rec is None:
            return None
        return rec.model_copy(deep=True)

    def list_agent_records(
        self, filters: dict[str, Any] | None = None
    ) -> list[AgentRecord]:
        filters = filters or {}
        source = filters.get("source")
        agent_ids = (
            set(filters.get("agent_ids", [])) if filters.get("agent_ids") else None
        )

        out: list[AgentRecord] = []
        for rec in self._agents.values():
            if source and rec.source != source:
                continue
            if agent_ids is not None and rec.agent_id not in agent_ids:
                continue
            out.append(rec.model_copy(deep=True))
        out.sort(key=lambda item: item.agent_id)
        return out

    def upsert_status(self, agent_id: str, status: AgentStatus) -> None:
        data = status.model_dump(mode="python")
        data["agent_id"] = agent_id
        self._status[agent_id] = AgentStatus.model_validate(data)

    def get_status(self, agent_id: str) -> AgentStatus | None:
        status = self._status.get(agent_id)
        if status is None:
            return None
        return status.model_copy(deep=True)

    def list_status(self, filters: dict[str, Any] | None = None) -> list[AgentStatus]:
        filters = filters or {}
        state = filters.get("state")
        agent_ids = (
            set(filters.get("agent_ids", [])) if filters.get("agent_ids") else None
        )

        out: list[AgentStatus] = []
        for status in self._status.values():
            if state and status.state != state:
                continue
            if agent_ids is not None and status.agent_id not in agent_ids:
                continue
            out.append(status.model_copy(deep=True))
        out.sort(key=lambda item: item.agent_id)
        return out

    def find_agent_ids_by_method(self, method: str) -> list[str]:
        return sorted(self._agents_by_method.get(method, set()))

    def get_method_rows(self, method: str) -> list[MethodIndexRow]:
        ids = self.find_agent_ids_by_method(method)
        out: list[MethodIndexRow] = []
        for agent_id in ids:
            row = self._methods_by_agent.get(agent_id, {}).get(method)
            if row is not None:
                out.append(row.model_copy(deep=True))
        return out
