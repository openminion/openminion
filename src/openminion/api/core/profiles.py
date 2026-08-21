"""Resolve and cache agent-specific runtime services behind APIRuntime."""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from sqlite3 import Connection, Error as SQLiteError
from threading import RLock
from types import SimpleNamespace
from typing import Any, cast

from openminion.base.channel import ChannelRegistry
from openminion.base.config import (
    AgentProfileConfig,
    ConfigManager,
    OpenMinionConfig,
    RunProfileOverrides,
    build_capability_runtime_diagnostics,
    build_runtime_config,
    combine_run_profile_overrides,
    resolve_runtime_profile,
)
from openminion.modules.llm import RuntimeLLMHandle
from openminion.modules.memory.interfaces import MemoryNamespaceQueryInterface
from openminion.modules.storage.runtime import (
    IdempotencyStore,
    RuntimeStorageContext,
    SessionStore,
)
from openminion.modules.telemetry.service import TelemetryCtl, TelemetryService
from openminion.modules.tool import ToolRegistry
from openminion.tools.ops import OpsService
from openminion.services.agent import AgentService
from openminion.services.channel.authenticity import build_channel_authenticity_policy
from openminion.services.gateway import GatewayService
from openminion.services.runtime.bootstrap import (
    build_agent_runtime_service,
    build_gateway_service,
)
from openminion.services.runtime.plugins import PluginRegistry
from openminion.services.runtime.turn_input import TurnInputQueue
from openminion.services.lifecycle.self_improvement import SelfImprovementEngine
from openminion.modules.policy import SecurityPolicyEngine

from .infrastructure import (
    bind_mcp_sampling_executor,
    build_runtime_llm_handle,
    scoped_tools_for_agent,
)
from .lifecycle import RuntimeFinalizer


@dataclass(frozen=True)
class AgentDiscoveryRecord:
    agent_id: str
    display_name: str = ""
    configured: bool = False
    registry_present: bool = False
    hot: bool = False
    heartbeat_active: bool = False
    registry_status: str = ""
    process_status: str = ""
    pid: int = 0
    host: str = ""
    port: int = 0
    active_run_id: str = ""
    capabilities: tuple[str, ...] = ()

    @property
    def available(self) -> bool:
        return self.configured or self.registry_present or self.hot

    @property
    def running(self) -> bool:
        return self.heartbeat_active or self.hot

    @property
    def stopped(self) -> bool:
        return self.available and not self.running

    @property
    def unknown(self) -> bool:
        return not self.available

    @property
    def state(self) -> str:
        if self.running:
            return self.process_status or "running"
        if self.configured:
            return "configured"
        if self.registry_present:
            return self.registry_status or "stopped"
        return "unknown"

    def as_payload(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "display_name": self.display_name,
            "configured": self.configured,
            "registry_present": self.registry_present,
            "hot": self.hot,
            "heartbeat_active": self.heartbeat_active,
            "available": self.available,
            "running": self.running,
            "stopped": self.stopped,
            "unknown": self.unknown,
            "state": self.state,
            "registry_status": self.registry_status,
            "process_status": self.process_status,
            "pid": self.pid,
            "host": self.host,
            "port": self.port,
            "active_run_id": self.active_run_id,
            "capabilities": list(self.capabilities),
        }


def _load_agent_registry_facts(
    storage_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        from openminion.modules.storage.runtime.registry_store import (
            AgentRegistryStore,
        )

        registry = AgentRegistryStore(str(storage_path))
        records = {item.agent_id: item for item in registry.list_agents()}
        heartbeats = {
            item.agent_id: item
            for item in registry.list_heartbeats()
            if not registry.is_agent_stale(item.agent_id)
        }
        return records, heartbeats
    except (ImportError, OSError, RuntimeError, ValueError, SQLiteError):
        return {}, {}


def _build_agent_discovery_record(
    *,
    agent_id: str,
    configured_profile: Any | None,
    registry_record: Any | None,
    heartbeat_record: Any | None,
    hot: bool,
) -> AgentDiscoveryRecord:
    configured_name = str(getattr(configured_profile, "name", "") or "").strip()
    display_name = (
        str(getattr(registry_record, "display_name", "") or "").strip()
        or configured_name
        or agent_id
    )
    heartbeat_active = heartbeat_record is not None
    capabilities = (
        ("delegate.sync",)
        if configured_profile is not None or registry_record is not None
        else ()
    )
    return AgentDiscoveryRecord(
        agent_id=agent_id,
        display_name=display_name,
        configured=configured_profile is not None,
        registry_present=registry_record is not None,
        hot=hot,
        heartbeat_active=heartbeat_active,
        registry_status=str(getattr(registry_record, "status", "") or "").strip(),
        process_status=str(getattr(heartbeat_record, "status", "") or "").strip(),
        pid=int(getattr(heartbeat_record, "pid", 0) or 0),
        host=str(getattr(heartbeat_record, "host", "") or "").strip(),
        port=int(getattr(heartbeat_record, "port", 0) or 0),
        active_run_id=str(getattr(heartbeat_record, "active_run_id", "") or "").strip(),
        capabilities=capabilities,
    )


@dataclass
class RuntimeProfilesMixin:
    config: OpenMinionConfig
    config_path: Path
    home_root: Path
    data_root: Path
    storage_path: Path
    memory_root: Path
    tool_workspace_root: Path | None
    runtime_storage: RuntimeStorageContext
    storage_connection: Connection
    telemetry_service: TelemetryService
    telemetryctl: TelemetryCtl
    sessions: SessionStore
    idempotency: IdempotencyStore
    channels: ChannelRegistry
    channel_supervisor: object | None
    plugins: PluginRegistry
    logger: logging.Logger
    provider: object
    llm_runtime: RuntimeLLMHandle
    tools: ToolRegistry
    security_policy: SecurityPolicyEngine
    self_improvement: SelfImprovementEngine
    agent: AgentService
    gateway: GatewayService
    memory_queries: MemoryNamespaceQueryInterface
    action_policy: object | None
    retrieve_ctl: object | None
    knowledge_graphs: object | None
    sandbox_runner: object | None
    authored_tools: object | None
    ops_service: OpsService | None
    runtime_manager: object
    config_manager: ConfigManager | None
    _agent_services: dict[str, AgentService]
    _gateways: dict[str, GatewayService]
    turn_input_queue: TurnInputQueue = field(default_factory=TurnInputQueue)
    run_profile_overrides: RunProfileOverrides = field(
        default_factory=RunProfileOverrides
    )
    _agent_runtime_modes: dict[str, str] = field(
        default_factory=dict, init=False, repr=False
    )
    _agent_runtime_fallback_reasons: dict[str, str] = field(
        default_factory=dict, init=False, repr=False
    )
    _agent_runtime_lock: RLock = field(default_factory=RLock, init=False, repr=False)
    _closed: bool = field(default=False, init=False, repr=False)
    _runtime_mode: str = field(default="brain", init=False, repr=False)
    _brain_bridge_active: bool = field(default=False, init=False, repr=False)
    _last_bridge_fallback_reason: str = field(default="", init=False, repr=False)
    _finalizer: RuntimeFinalizer | None = field(default=None, init=False, repr=False)

    @staticmethod
    def _bind_runtime_handle(agent_service: object, runtime: object) -> None:
        binder = getattr(agent_service, "bind_runtime_handle", None)
        if callable(binder):
            binder(runtime)

    def _combined_run_profile_overrides(
        self,
        overrides: RunProfileOverrides | None,
    ) -> RunProfileOverrides:
        return combine_run_profile_overrides(self.run_profile_overrides, overrides)

    @staticmethod
    def _runtime_cache_key(
        *,
        agent_name: str,
        overrides: RunProfileOverrides,
    ) -> str:
        return f"{agent_name}||{overrides.cache_key()}"

    def resolve_agent_profile(
        self,
        agent_id: str | None = None,
        overrides: RunProfileOverrides | None = None,
    ) -> AgentProfileConfig:
        return resolve_runtime_profile(
            self.config,
            agent_id=agent_id,
            overrides=self._combined_run_profile_overrides(overrides),
        )

    def capability_runtime_diagnostics(
        self,
        agent_id: str | None = None,
        overrides: RunProfileOverrides | None = None,
    ) -> dict[str, Any]:
        return build_capability_runtime_diagnostics(
            self.config,
            agent_id=agent_id,
            overrides=self._combined_run_profile_overrides(overrides),
        )

    def resolve_agent_service(
        self,
        agent_id: str | None = None,
        overrides: RunProfileOverrides | None = None,
    ) -> AgentService:
        effective_overrides = self._combined_run_profile_overrides(overrides)
        profile = self.resolve_agent_profile(agent_id, overrides=overrides)
        cache_key = self._runtime_cache_key(
            agent_name=profile.name,
            overrides=effective_overrides,
        )
        with self._agent_runtime_lock:
            cached = self._agent_services.get(cache_key)
            if cached is not None:
                return cached
            runtime_config = build_runtime_config(
                self.config,
                agent_id=agent_id,
                overrides=effective_overrides,
            )
            llm_runtime = build_runtime_llm_handle(
                runtime_config,
                self.logger.getChild(f"provider.{profile.name}"),
            )
            provider = SimpleNamespace(
                name=llm_runtime.name,
                model=llm_runtime.model,
                tool_call_strategy=llm_runtime.tool_call_strategy,
            )
            service, runtime_mode, fallback_reason = build_agent_runtime_service(
                config=runtime_config,
                plugins=self.plugins,
                provider=provider,
                llm_runtime=llm_runtime,
                logger=self.logger.getChild(f"agent.{profile.name}"),
                tools=scoped_tools_for_agent(self.tools, profile),
                security_policy=self.security_policy,
                self_improvement=self.self_improvement,
                storage_path=self.storage_path,
                home_root=self.home_root,
                data_root=self.data_root,
                config_path=self.config_path,
                config_manager=self.config_manager,
                retrieve_service=self.retrieve_ctl,
                action_policy_service=self.action_policy,
                telemetryctl=self.telemetryctl,
            )
            agent_service = cast(AgentService, service)
            self._bind_runtime_handle(agent_service, self)
            bind_mcp_sampling_executor(self.tools, agent_service)
            self._agent_services[cache_key] = agent_service
            self._agent_runtime_modes[cache_key] = runtime_mode
            self._agent_runtime_fallback_reasons[cache_key] = fallback_reason
            return agent_service

    def get_agent_runtime_info(
        self,
        agent_id: str | None = None,
        overrides: RunProfileOverrides | None = None,
    ) -> dict[str, object]:
        effective_overrides = self._combined_run_profile_overrides(overrides)
        profile = self.resolve_agent_profile(agent_id, overrides=overrides)
        cache_key = self._runtime_cache_key(
            agent_name=profile.name,
            overrides=effective_overrides,
        )
        runtime_mode = self._agent_runtime_modes.get(cache_key, "")
        return {
            "runtime_mode": runtime_mode or "unknown",
            "fallback_reason": self._agent_runtime_fallback_reasons.get(cache_key, ""),
            "brain_bridge_active": runtime_mode == "brain",
        }

    def resolve_gateway(
        self,
        agent_id: str | None = None,
        overrides: RunProfileOverrides | None = None,
    ) -> GatewayService:
        effective_overrides = self._combined_run_profile_overrides(overrides)
        profile = self.resolve_agent_profile(agent_id, overrides=overrides)
        cache_key = self._runtime_cache_key(
            agent_name=profile.name,
            overrides=effective_overrides,
        )
        with self._agent_runtime_lock:
            cached = self._gateways.get(cache_key)
            if cached is not None:
                return cached
            runtime_config = build_runtime_config(
                self.config,
                agent_id=agent_id,
                overrides=effective_overrides,
            )
            gateway = build_gateway_service(
                agent_service=self.resolve_agent_service(
                    profile.name, overrides=overrides
                ),
                profile_name=profile.name,
                config=runtime_config,
                channels=self.channels,
                sessions=self.sessions,
                idempotency=self.idempotency,
                security_policy=self.security_policy,
                channel_authenticity_policy=build_channel_authenticity_policy(
                    self.config.channel_authenticity
                ),
                config_path=self.config_path,
                storage_path=self.storage_path,
                memory_root=self.memory_root,
                home_root=self.home_root,
                data_root=self.data_root,
                logger=self.logger,
                config_manager=self.config_manager,
                knowledge_graphs=self.knowledge_graphs,
                retrieve_ctl=self.retrieve_ctl,
            )
            self._gateways[cache_key] = gateway
            return gateway

    def evict_agent_runtime(self, *, agent_id: str, reason: str) -> None:
        normalized = str(agent_id or "").strip()
        if not normalized:
            return
        evicted_services: list[AgentService] = []
        with self._agent_runtime_lock:
            for cache_key in tuple(self._gateways):
                if cache_key == normalized or cache_key.startswith(f"{normalized}||"):
                    self._gateways.pop(cache_key, None)
            for cache_key in tuple(self._agent_services):
                if cache_key == normalized or cache_key.startswith(f"{normalized}||"):
                    service = self._agent_services.pop(cache_key, None)
                    if service is not None:
                        evicted_services.append(service)
        for service in evicted_services:
            service.close()
        self.logger.getChild("runtime").info(
            "evicted agent runtime cache agent_id=%s reason=%s",
            normalized,
            reason,
        )

    def list_registered_agents(self) -> list[str]:
        return sorted(
            agent_id for item in self.config.agents if (agent_id := str(item).strip())
        )

    def list_hot_agents(self) -> list[str]:
        with self._agent_runtime_lock:
            return sorted(
                {
                    str(agent_id).split("||", 1)[0]
                    for agent_id in self._agent_services
                    if str(agent_id).strip()
                }
            )

    def agent_discovery_snapshot(self) -> list[dict[str, Any]]:
        configured = {
            str(agent_id).strip(): profile
            for agent_id, profile in self.config.agents.items()
            if str(agent_id).strip()
        }
        hot = set(self.list_hot_agents())
        registry_records, heartbeats = _load_agent_registry_facts(self.storage_path)
        all_ids = sorted(
            set(configured) | set(registry_records) | hot | set(heartbeats)
        )
        return [
            _build_agent_discovery_record(
                agent_id=agent_id,
                configured_profile=configured.get(agent_id),
                registry_record=registry_records.get(agent_id),
                heartbeat_record=heartbeats.get(agent_id),
                hot=agent_id in hot,
            ).as_payload()
            for agent_id in all_ids
        ]
