"""Resolve and cache agent-specific runtime services behind APIRuntime."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from sqlite3 import Connection
from threading import RLock
from types import SimpleNamespace
from typing import Any, Optional, cast

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
from openminion.modules.telemetry.service import TelemetryService
from openminion.modules.tool import ToolRegistry
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
        agent_id: Optional[str] = None,
        overrides: RunProfileOverrides | None = None,
    ) -> AgentProfileConfig:
        return resolve_runtime_profile(
            self.config,
            agent_id=agent_id,
            overrides=self._combined_run_profile_overrides(overrides),
        )

    def capability_runtime_diagnostics(
        self,
        agent_id: Optional[str] = None,
        overrides: RunProfileOverrides | None = None,
    ) -> dict[str, Any]:
        return build_capability_runtime_diagnostics(
            self.config,
            agent_id=agent_id,
            overrides=self._combined_run_profile_overrides(overrides),
        )

    def resolve_agent_service(
        self,
        agent_id: Optional[str] = None,
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
        agent_id: Optional[str] = None,
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
        agent_id: Optional[str] = None,
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
        with self._agent_runtime_lock:
            for cache in (self._gateways, self._agent_services):
                for cache_key in tuple(cache):
                    if cache_key == normalized or cache_key.startswith(
                        f"{normalized}||"
                    ):
                        cache.pop(cache_key, None)
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
