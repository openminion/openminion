"""Agent registry facade backed by the configured registry store."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openminion.modules.registry.constants import (
    DEFAULT_MANIFEST_FILENAME,
    STATUS_ORDER,
)
from openminion.modules.registry.errors import AgentRegError
from openminion.modules.registry.interfaces import REGISTRY_INTERFACE_VERSION
from openminion.modules.registry.manifest import load_manifest
from openminion.modules.registry.models import (
    AgentDescriptor,
    AgentStatus,
    ResolveConstraints,
    ResolvedRoute,
    TransportEndpoint,
    tier_gte,
    tier_lte,
    iso_now,
)
from openminion.modules.registry.storage.base import RegistryStore


@dataclass
class _RankedEndpoint:
    agent: AgentDescriptor
    endpoint: TransportEndpoint
    state: str
    sort_key: tuple[int, int, int, int, int, str, str]
    reason: str


class AgentRegistry:
    contract_version = REGISTRY_INTERFACE_VERSION

    def __init__(
        self,
        *,
        manifest_path: str | Path = DEFAULT_MANIFEST_FILENAME,
        store: RegistryStore,
        allow_runtime_override: bool = True,
        builtin_agents: list[AgentDescriptor] | None = None,
    ) -> None:
        self.manifest_path = Path(manifest_path).expanduser().resolve(strict=False)
        self.store = store
        self.allow_runtime_override = allow_runtime_override
        self._builtin_agents = builtin_agents or []

    def load(self) -> None:
        manifest_agents = load_manifest(self.manifest_path)
        self._merge_source(manifest_agents, source="manifest", cleanup=True)
        self._merge_source(self._builtin_agents, source="builtin", cleanup=True)

    def reload(self) -> None:
        self.load()

    def close(self) -> None:
        self.store.close()

    def list(self, filters: dict[str, Any] | None = None) -> list[AgentDescriptor]:
        filters = filters or {}
        source = filters.get("source")
        records = self.store.list_agent_records({"source": source} if source else None)
        return _apply_descriptor_filters([row.descriptor for row in records], filters)

    def get(self, agent_id: str) -> AgentDescriptor | None:
        return self.store.get_agent(agent_id)

    def register(
        self,
        descriptor: AgentDescriptor | dict[str, Any],
        source: str = "runtime",
        overwrite: bool = False,
    ) -> None:
        parsed = (
            descriptor
            if isinstance(descriptor, AgentDescriptor)
            else AgentDescriptor.model_validate(descriptor)
        )
        if source not in {"manifest", "runtime", "builtin"}:
            raise AgentRegError(
                "INVALID_ARGUMENT", f"Unsupported registration source: {source}"
            )

        existing = self.store.get_agent_record(parsed.agent_id)
        if existing is not None and not overwrite:
            if source == "runtime" and self.allow_runtime_override:
                pass
            else:
                raise AgentRegError(
                    "ALREADY_EXISTS",
                    f"Agent {parsed.agent_id} already exists from source={existing.source}",
                )

        self.store.upsert_agent(parsed, source=source)

    def unregister(self, agent_id: str) -> None:
        self.store.delete_agent(agent_id)

    def find_by_method(
        self, method: str, filters: dict[str, Any] | None = None
    ) -> list[AgentDescriptor]:
        agent_ids = self.store.find_agent_ids_by_method(method)
        filtered = _apply_descriptor_filters(
            [
                descriptor
                for agent_id in agent_ids
                if (descriptor := self.store.get_agent(agent_id)) is not None
            ],
            filters,
        )
        return [agent for agent in filtered if agent.supports_method(method)]

    def find_by_capability(
        self, capability: str, filters: dict[str, Any] | None = None
    ) -> list[AgentDescriptor]:
        all_agents = self.list(filters=filters)
        return [
            agent
            for agent in all_agents
            if any(cap.name == capability for cap in agent.capabilities)
        ]

    def resolve_method(
        self,
        method: str,
        constraints: dict[str, Any] | ResolveConstraints | None = None,
    ) -> ResolvedRoute | None:
        constraints_obj = ResolveConstraints.from_any(constraints)
        candidates = [
            agent
            for agent in self.find_by_method(method)
            if self._agent_matches_constraints(
                agent, method=method, constraints=constraints_obj
            )
        ]

        ranked = self._rank_endpoints(
            candidates, method=method, constraints=constraints_obj
        )
        if not ranked:
            return None

        best = ranked[0]
        return ResolvedRoute(
            agent_id=best.agent.agent_id,
            method=method,
            endpoint=best.endpoint,
            auth=best.agent.auth,
            selection_reason=best.reason,
        )

    def resolve_agent(
        self,
        agent_id: str,
        method: str | None = None,
        constraints: dict[str, Any] | ResolveConstraints | None = None,
    ) -> ResolvedRoute | None:
        constraints_obj = ResolveConstraints.from_any(constraints)
        agent = self.get(agent_id)
        if agent is None:
            return None
        if method and not agent.supports_method(method):
            return None
        if not self._agent_matches_constraints(
            agent, method=method, constraints=constraints_obj
        ):
            return None

        ranked = self._rank_endpoints(
            [agent], method=method, constraints=constraints_obj
        )
        if not ranked:
            return None

        best = ranked[0]
        return ResolvedRoute(
            agent_id=agent.agent_id,
            method=method,
            endpoint=best.endpoint,
            auth=agent.auth,
            selection_reason=best.reason,
        )

    def heartbeat(self, agent_id: str, status_patch: dict[str, Any]) -> None:
        patch = dict(status_patch)
        patch["agent_id"] = agent_id
        if "last_heartbeat_at" not in patch:
            patch["last_heartbeat_at"] = iso_now()
        status = AgentStatus.model_validate(
            {**self.get_status(agent_id).model_dump(mode="python"), **patch}
        )
        self.store.upsert_status(agent_id, status)

    def get_status(self, agent_id: str) -> AgentStatus:
        existing = self.store.get_status(agent_id)
        if existing is not None:
            return existing
        return AgentStatus(agent_id=agent_id, state="unknown")

    def set_status(
        self, agent_id: str, state: str, error: dict[str, Any] | None = None
    ) -> None:
        patch: dict[str, Any] = {
            "state": state,
            "last_heartbeat_at": iso_now(),
        }
        if error is not None:
            patch["last_error"] = error
        self.heartbeat(agent_id, patch)

    def explain_resolution(
        self,
        method: str,
        constraints: dict[str, Any] | ResolveConstraints | None = None,
    ) -> dict[str, Any]:
        constraints_obj = ResolveConstraints.from_any(constraints)
        candidates = self.find_by_method(method)
        filtered = [
            agent
            for agent in candidates
            if self._agent_matches_constraints(
                agent, method=method, constraints=constraints_obj
            )
        ]
        ranked = self._rank_endpoints(
            filtered, method=method, constraints=constraints_obj
        )

        selected = None
        if ranked:
            selected = {
                "agent_id": ranked[0].agent.agent_id,
                "endpoint_id": ranked[0].endpoint.endpoint_id,
                "transport": ranked[0].endpoint.transport,
                "reason": ranked[0].reason,
            }

        return {
            "method": method,
            "constraints": constraints_obj.model_dump(mode="python"),
            "candidate_agents": [agent.agent_id for agent in candidates],
            "filtered_agents": [agent.agent_id for agent in filtered],
            "ranked_candidates": [
                {
                    "rank": i + 1,
                    "agent_id": row.agent.agent_id,
                    "endpoint_id": row.endpoint.endpoint_id,
                    "transport": row.endpoint.transport,
                    "enabled": row.endpoint.enabled,
                    "agent_state": row.state,
                    "sort_key": list(row.sort_key),
                    "reason": row.reason,
                }
                for i, row in enumerate(ranked)
            ],
            "selected": selected,
        }

    def _merge_source(
        self, descriptors: list[AgentDescriptor], *, source: str, cleanup: bool
    ) -> None:
        existing_source_ids = {
            row.agent_id
            for row in self.store.list_agent_records(filters={"source": source})
        }
        incoming_ids: set[str] = set()

        for descriptor in descriptors:
            incoming_ids.add(descriptor.agent_id)
            existing = self.store.get_agent_record(descriptor.agent_id)

            if existing is not None:
                if (
                    existing.source == "runtime"
                    and source in {"manifest", "builtin"}
                    and self.allow_runtime_override
                ):
                    continue
                if source == "builtin" and existing.source == "manifest":
                    continue

            self.store.upsert_agent(descriptor, source=source)

        if cleanup:
            for stale in sorted(existing_source_ids - incoming_ids):
                existing = self.store.get_agent_record(stale)
                if existing is not None and existing.source == source:
                    self.store.delete_agent(stale)

    def _agent_matches_constraints(
        self,
        agent: AgentDescriptor,
        *,
        method: str | None,
        constraints: ResolveConstraints,
    ) -> bool:
        tags = set(agent.tags)

        if constraints.agent_allowlist and agent.agent_id not in set(
            constraints.agent_allowlist
        ):
            return False

        if constraints.require_tags and not set(constraints.require_tags).issubset(
            tags
        ):
            return False

        if constraints.avoid_tags and set(constraints.avoid_tags).intersection(tags):
            return False

        if constraints.require_transport:
            has_transport = any(
                ep.transport == constraints.require_transport for ep in agent.endpoints
            )
            if not has_transport:
                return False

        if method is None:
            return True

        method_caps = agent.capability_matches_method(method)
        if not method_caps:
            return False

        for cap in method_caps:
            if not tier_gte(cap.quality_tier, constraints.min_quality_tier):
                continue
            if not tier_lte(cap.cost_tier, constraints.max_cost_tier):
                continue
            return True
        return False

    def _rank_endpoints(
        self,
        agents: list[AgentDescriptor],
        *,
        method: str | None,
        constraints: ResolveConstraints,
    ) -> list[_RankedEndpoint]:
        ranked: list[_RankedEndpoint] = []

        for agent in agents:
            status = self.get_status(agent.agent_id)
            state_rank = STATUS_ORDER.get(status.state, STATUS_ORDER["unknown"])

            for endpoint in agent.endpoints:
                if (
                    constraints.require_transport
                    and endpoint.transport != constraints.require_transport
                ):
                    continue

                enabled_rank = 0 if endpoint.enabled else 1
                default_rank = (
                    0
                    if agent.default_endpoint
                    and endpoint.endpoint_id == agent.default_endpoint
                    else 1
                )
                prefer_rank = 0
                if constraints.prefer_transport is not None:
                    prefer_rank = (
                        0 if endpoint.transport == constraints.prefer_transport else 1
                    )

                sort_key = (
                    enabled_rank,
                    state_rank,
                    int(endpoint.priority),
                    default_rank,
                    prefer_rank,
                    agent.agent_id,
                    endpoint.endpoint_id,
                )
                reason = (
                    f"enabled={endpoint.enabled} state={status.state} priority={endpoint.priority} "
                    f"default_match={default_rank == 0} transport={endpoint.transport}"
                )

                ranked.append(
                    _RankedEndpoint(
                        agent=agent,
                        endpoint=endpoint,
                        state=status.state,
                        sort_key=sort_key,
                        reason=reason,
                    )
                )

        ranked.sort(key=lambda row: row.sort_key)
        return ranked


def _apply_descriptor_filters(
    descriptors: list[AgentDescriptor],
    filters: dict[str, Any] | None,
) -> list[AgentDescriptor]:
    filters = filters or {}

    tag = str(filters.get("tag", "")).strip()
    tags: set[str] = set(filters.get("tags", []) or [])
    require_tags: set[str] = set(filters.get("require_tags", []) or [])
    avoid_tags: set[str] = set(filters.get("avoid_tags", []) or [])
    ids: set[str] = set(filters.get("agent_ids", []) or [])
    name_query = str(filters.get("name", "")).strip().lower()

    if tag:
        tags.add(tag)

    out: list[AgentDescriptor] = []
    for descriptor in descriptors:
        descriptor_tags = set(descriptor.tags)

        if ids and descriptor.agent_id not in ids:
            continue
        if tags and not tags.issubset(descriptor_tags):
            continue
        if require_tags and not require_tags.issubset(descriptor_tags):
            continue
        if avoid_tags and avoid_tags.intersection(descriptor_tags):
            continue
        if (
            name_query
            and name_query not in descriptor.display_name.lower()
            and name_query not in descriptor.agent_id.lower()
        ):
            continue

        out.append(descriptor)

    out.sort(key=lambda item: item.agent_id)
    return out
