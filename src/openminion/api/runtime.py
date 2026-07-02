"""Runtime bootstrap and API-facing runtime facade."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, cast
import logging
from threading import RLock
from types import SimpleNamespace
import weakref

from openminion.base.channel import ChannelRegistry
from openminion.base.config import (
    AgentProfileConfig,
    EnvironmentConfig,
    OpenMinionConfig,
    RunProfileOverrides,
    build_capability_runtime_diagnostics,
    build_runtime_config,
    combine_run_profile_overrides,
    resolve_runtime_profile,
    resolve_config_path,
)
from openminion.base.config.core import resolve_default_agent_id
from openminion.base.config import ConfigManager
from openminion.base.logging import configure_logging
from openminion.modules.llm.providers.factory import (
    RuntimeLLMHandle,
    build_runtime_llm_handle,
)
from openminion.modules.storage.runtime.context import (
    RuntimeStorageContext,
    build_runtime_storage,
)
from openminion.modules.storage.runtime.idempotency_store import IdempotencyStore
from openminion.modules.storage.runtime.session_store import SessionStore
from openminion.modules.storage.runtime.sqlite import resolve_database_path
from openminion.modules.telemetry import storage_hook
from openminion.modules.telemetry.service import TelemetryService
from openminion.modules.tool import ToolRegistry
from openminion.services.agent import AgentService
from openminion.services.agent.memory import resolve_memory_root
from openminion.services.channel.authenticity import build_channel_authenticity_policy
from openminion.services.bootstrap.config import bootstrap_config_manager
from openminion.services.gateway import GatewayService
from openminion.services.runtime.plugins import PluginRegistry
from openminion.services.runtime.bootstrap import (
    build_action_policy_service,
    build_agent_memory_service,
    build_agent_runtime_service,
    build_daytona_runner,
    build_gateway_service,
    build_knowledge_graph_source_service,
    build_session_context_service,
    build_tool_authoring_service,
    enforce_plugin_activation_policy,
)
from openminion.services.brain.factory.retrieve import build_retrieve_service
from openminion.services.runtime.daemon import build_runtime_manager
from openminion.services.runtime.env import apply_runtime_environment
from openminion.services.runtime.lifecycle import LifecycleService
from openminion.services.runtime.turn_input import TurnInputQueue
from openminion.services.security.policy import SecurityPolicyEngine, ToolBudgetPolicy
from openminion.services.lifecycle.self_improvement import SelfImprovementEngine

_OPENMINION_DISABLE_SECURITY_POLICY_ENV = "OPENMINION_DISABLE_SECURITY_POLICY"
_CANONICAL_TURN_PATH = (
    "services/runtime/ingress.run_turn_payload",
    "services/request_orchestrator.run_turn",
    "GatewayService.run_once",
    "BrainBridgeService.run_turn",
    "BrainRunner.run",
)
_CANONICAL_TURN_PATH_REF = "openminion.api.runtime.APIRuntime.runtime_posture"
_EXECUTION_BOUNDARY_POLICY_REF = "openminion.services.security.tool_execution.build_execution_boundary_policy_adapter"
_CAPABILITY_LAYERING_REF = (
    "openminion.api.queries.runtime_reports.build_runtime_posture_report"
)


def _scoped_tools_for_agent(tools: Any, profile: AgentProfileConfig) -> Any:
    from openminion.tools.mcp.exposure import scoped_mcp_registry_view

    return scoped_mcp_registry_view(tools, getattr(profile, "mcp_exposure", None))


def _bind_mcp_sampling_executor(tools: Any, agent_service: Any) -> None:
    manager = getattr(tools, "mcp_manager", None)
    bind = getattr(manager, "bind_sampling_executor", None)
    executor = getattr(agent_service, "_invoke_provider_request", None)
    if callable(bind) and callable(executor):
        bind(executor)


def _resolve_runtime_bootstrap_paths(
    *,
    manager: ConfigManager,
    base_config: OpenMinionConfig,
    home_root: Path | None,
    data_root: Path | None,
    config_path: Path | None,
) -> tuple[Path, Path, Path, Path, Path]:
    resolved_home_root = home_root or manager.home_root
    resolved_data_root = data_root or manager.data_root
    resolved_config_path = config_path or manager.config_path
    if resolved_config_path is None:
        resolved_config_path = resolve_config_path(None, home_root=resolved_home_root)
    storage_path = resolve_database_path(
        base_config.storage.path,
        env=manager.env,
    )
    memory_root = resolve_memory_root(
        config=base_config,
        config_path=resolved_config_path,
        storage_path=storage_path,
        data_root=resolved_data_root,
    )
    return (
        resolved_home_root,
        resolved_data_root,
        resolved_config_path,
        storage_path,
        memory_root,
    )


def _build_runtime_infrastructure(
    *,
    base_config: OpenMinionConfig,
    manager: ConfigManager,
    resolved_home_root: Path,
    resolved_data_root: Path,
    resolved_config_path: Path,
    storage_path: Path,
    memory_root: Path,
    effective_run_profile_overrides: RunProfileOverrides,
    logging_mode: str,
) -> dict[str, object]:
    apply_runtime_environment(base_config.runtime.env)
    telemetry_service = TelemetryService(
        home_root=resolved_home_root,
        env=getattr(base_config.runtime, "env", None),
        otel_exporter_config=getattr(base_config.runtime, "telemetry_exporter", None),
    )
    runtime_storage = build_runtime_storage(
        storage_path,
        env=manager.env,
        record_backend=base_config.storage.record_backend(),
        record_backend_options=base_config.storage.record_backend_options(),
        telemetry_hook=storage_hook.TelemetryServiceStorageHook(telemetry_service),
    )
    logger = configure_logging(base_config.runtime.log_level, mode=logging_mode)
    security_policy = SecurityPolicyEngine(
        tool_budget_policy=ToolBudgetPolicy(
            max_calls_per_run=base_config.security.tool_policy.max_calls_per_run,
            max_calls_per_tool=base_config.security.tool_policy.max_calls_per_tool,
            max_budget_cost_per_run=base_config.security.tool_policy.max_budget_cost_per_run,
        ),
        default_tool_required_scopes=frozenset(
            base_config.security.tool_policy.default_required_scopes
        ),
    )
    disable_security_policy = EnvironmentConfig.from_sources(
        runtime_env=getattr(base_config.runtime, "env", None),
    ).get_bool(_OPENMINION_DISABLE_SECURITY_POLICY_ENV, False)
    agent_security_policy = None if disable_security_policy else security_policy
    extension_runtime = LifecycleService.from_config(
        base_config,
        config_path=str(resolved_config_path),
        logger=logger,
    ).build(
        security_policy=security_policy,
        on_before_activate=lambda manifest: enforce_plugin_activation_policy(
            security_policy=security_policy,
            agent_id=resolve_default_agent_id(base_config),
            manifest=manifest,
        ),
        load_tool_plugins=False,
    )
    default_config = build_runtime_config(
        base_config,
        overrides=effective_run_profile_overrides,
    )
    default_agent = default_config.agents[resolve_default_agent_id(default_config)]
    llm_runtime = build_runtime_llm_handle(default_config, logger.getChild("provider"))
    tools = extension_runtime.tools
    action_policy = build_action_policy_service(
        config=base_config,
        tool_registry=tools,
        data_root=resolved_data_root,
    )
    try:
        retrieve_config = manager.get("retrieve")
    except Exception:
        retrieve_config = None
    retrieve_ctl = build_retrieve_service(
        home_root=resolved_home_root,
        vector_adapter=None,
        config=retrieve_config,
        logger=logger.getChild("retrieve"),
    )
    session_context = build_session_context_service(
        config=base_config,
        sessions=runtime_storage.sessions,
        logger=logger.getChild("gateway.session_context"),
        config_path=resolved_config_path,
        storage_path=storage_path,
        memory_root=memory_root,
        data_root=resolved_data_root,
        retrieve_ctl=retrieve_ctl,
    )
    agent_memory = build_agent_memory_service(
        config=base_config,
        agent_id=default_agent.name,
        memory_root=memory_root,
        logger=logger.getChild("gateway.agent_memory"),
        config_manager=manager,
        home_root=resolved_home_root,
        data_root=resolved_data_root,
        session_context=session_context,
        retrieve_ctl=retrieve_ctl,
        storage_path=storage_path,
    )
    knowledge_graphs = build_knowledge_graph_source_service(config=base_config)
    sandbox_runner = build_daytona_runner(
        config=base_config,
        config_manager=manager,
    )
    authored_tools = build_tool_authoring_service(
        config=base_config,
        data_root=resolved_data_root,
        tool_registry=tools,
        sandbox_runner=sandbox_runner,
        policy_ctl=action_policy,
    )
    return {
        "telemetry_service": telemetry_service,
        "runtime_storage": runtime_storage,
        "logger": logger,
        "security_policy": security_policy,
        "agent_security_policy": agent_security_policy,
        "channels": extension_runtime.channels,
        "plugins": extension_runtime.plugins,
        "default_config": default_config,
        "default_agent": default_agent,
        "llm_runtime": llm_runtime,
        "provider": SimpleNamespace(
            name=llm_runtime.name,
            model=llm_runtime.model,
            tool_call_strategy=llm_runtime.tool_call_strategy,
        ),
        "tools": tools,
        "self_improvement": SelfImprovementEngine.from_config(base_config),
        "channel_authenticity_policy": build_channel_authenticity_policy(
            base_config.channel_authenticity
        ),
        "action_policy": action_policy,
        "retrieve_ctl": retrieve_ctl,
        "session_context": session_context,
        "agent_memory": agent_memory,
        "knowledge_graphs": knowledge_graphs,
        "sandbox_runner": sandbox_runner,
        "authored_tools": authored_tools,
    }


def _build_default_runtime_stack(
    *,
    base_config: OpenMinionConfig,
    manager: ConfigManager,
    resolved_home_root: Path,
    resolved_data_root: Path,
    resolved_config_path: Path,
    storage_path: Path,
    memory_root: Path,
    infrastructure: dict[str, object],
) -> tuple[AgentService, GatewayService, str, str]:
    logger = cast(logging.Logger, infrastructure["logger"])
    runtime_storage = cast(RuntimeStorageContext, infrastructure["runtime_storage"])
    default_agent = cast(AgentProfileConfig, infrastructure["default_agent"])
    agent, runtime_mode, fallback_reason = build_agent_runtime_service(
        config=infrastructure["default_config"],
        plugins=infrastructure["plugins"],
        provider=infrastructure["provider"],
        llm_runtime=infrastructure["llm_runtime"],
        logger=logger.getChild("agent"),
        tools=_scoped_tools_for_agent(infrastructure["tools"], default_agent),
        security_policy=infrastructure["agent_security_policy"],
        self_improvement=infrastructure["self_improvement"],
        storage_path=storage_path,
        home_root=resolved_home_root,
        data_root=resolved_data_root,
        config_path=resolved_config_path,
        config_manager=manager,
        retrieve_service=infrastructure["retrieve_ctl"],
        action_policy_service=infrastructure["action_policy"],
    )
    _bind_mcp_sampling_executor(infrastructure["tools"], agent)
    gateway = build_gateway_service(
        agent_service=agent,
        profile_name=infrastructure["default_agent"].name,
        config=infrastructure["default_config"],
        channels=infrastructure["channels"],
        sessions=runtime_storage.sessions,
        idempotency=runtime_storage.idempotency,
        security_policy=infrastructure["agent_security_policy"],
        channel_authenticity_policy=infrastructure["channel_authenticity_policy"],
        config_path=resolved_config_path,
        storage_path=storage_path,
        memory_root=memory_root,
        home_root=resolved_home_root,
        data_root=resolved_data_root,
        logger=logger,
        config_manager=manager,
        session_context=infrastructure["session_context"],
        agent_memory=infrastructure["agent_memory"],
        knowledge_graphs=infrastructure["knowledge_graphs"],
        retrieve_ctl=infrastructure["retrieve_ctl"],
    )
    return agent, gateway, runtime_mode, fallback_reason


def _finalize_runtime_instance(
    *,
    cls,
    base_config: OpenMinionConfig,
    manager: ConfigManager,
    resolved_home_root: Path,
    resolved_data_root: Path,
    resolved_config_path: Path,
    storage_path: Path,
    memory_root: Path,
    infrastructure: dict[str, object],
    agent: AgentService,
    gateway: GatewayService,
    runtime_mode: str,
    fallback_reason: str,
    effective_run_profile_overrides: RunProfileOverrides,
) -> "APIRuntime":
    runtime_storage = cast(RuntimeStorageContext, infrastructure["runtime_storage"])
    runtime = cls(
        config=base_config,
        config_path=resolved_config_path,
        home_root=resolved_home_root,
        data_root=resolved_data_root,
        storage_path=storage_path,
        memory_root=memory_root,
        tool_workspace_root=(
            Path(base_config.runtime.tool_workspace_root).expanduser()
            if base_config.runtime.tool_workspace_root
            else None
        ),
        telemetry_service=infrastructure["telemetry_service"],
        runtime_storage=runtime_storage,
        storage_connection=runtime_storage.connection,
        sessions=runtime_storage.sessions,
        idempotency=runtime_storage.idempotency,
        channels=infrastructure["channels"],
        plugins=infrastructure["plugins"],
        logger=infrastructure["logger"],
        provider=infrastructure["provider"],
        llm_runtime=infrastructure["llm_runtime"],
        tools=infrastructure["tools"],
        security_policy=infrastructure["agent_security_policy"],
        self_improvement=infrastructure["self_improvement"],
        agent=agent,
        gateway=gateway,
        action_policy=infrastructure["action_policy"],
        retrieve_ctl=infrastructure["retrieve_ctl"],
        knowledge_graphs=infrastructure["knowledge_graphs"],
        sandbox_runner=infrastructure["sandbox_runner"],
        authored_tools=infrastructure["authored_tools"],
        runtime_manager=None,
        config_manager=manager,
        _agent_services={},
        _gateways={},
        run_profile_overrides=effective_run_profile_overrides,
    )
    runtime._runtime_mode = runtime_mode
    runtime._brain_bridge_active = runtime_mode == "brain"
    runtime._last_bridge_fallback_reason = fallback_reason
    runtime.runtime_manager = build_runtime_manager(runtime)
    runtime._bind_runtime_handle(agent, runtime)
    default_cache_key = runtime._runtime_cache_key(
        agent_name=infrastructure["default_agent"].name,
        overrides=effective_run_profile_overrides,
    )
    runtime._agent_services[default_cache_key] = agent
    runtime._gateways[default_cache_key] = gateway
    runtime._agent_runtime_modes[default_cache_key] = runtime_mode
    runtime._agent_runtime_fallback_reasons[default_cache_key] = fallback_reason
    return runtime


def _safe_close_resource(resource: object | None) -> None:
    if resource is None:
        return
    close = getattr(resource, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            return


def _shutdown_runtime_manager(manager: object | None) -> None:
    if manager is None:
        return
    shutdown = getattr(manager, "shutdown", None)
    if callable(shutdown):
        try:
            shutdown(grace_s=2)
        except Exception:
            return


def _close_runtime_components(
    *,
    retrieve_ctl: object | None,
    action_policy: object | None,
    runtime_manager: object | None,
    lifecycle_bridge: object | None,
    tools: object | None,
    runtime_storage: object | None,
    sandbox_runner: object | None = None,
    authored_tools: object | None = None,
    telemetry_service: object | None = None,
) -> None:
    _safe_close_resource(retrieve_ctl)
    _safe_close_resource(action_policy)
    _shutdown_runtime_manager(runtime_manager)
    _safe_close_resource(lifecycle_bridge)
    _safe_close_resource(sandbox_runner)
    _safe_close_resource(authored_tools)
    _safe_close_resource(getattr(tools, "mcp_manager", None))
    # close storage BEFORE telemetry so the periodic pool-health
    _safe_close_resource(runtime_storage)
    if telemetry_service is not None:
        close_sync = getattr(telemetry_service, "close_sync", None)
        if callable(close_sync):
            try:
                close_sync()
            except Exception:
                return


@dataclass
class APIRuntime:
    config: OpenMinionConfig
    config_path: Path
    home_root: Path
    data_root: Path
    storage_path: Path
    memory_root: Path
    tool_workspace_root: Path | None
    runtime_storage: RuntimeStorageContext
    storage_connection: object
    telemetry_service: TelemetryService
    sessions: SessionStore
    idempotency: IdempotencyStore
    channels: ChannelRegistry
    plugins: PluginRegistry
    logger: logging.Logger
    provider: object
    llm_runtime: RuntimeLLMHandle
    tools: ToolRegistry
    security_policy: SecurityPolicyEngine
    self_improvement: SelfImprovementEngine
    agent: AgentService
    gateway: GatewayService
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
    _finalizer: weakref.finalize | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self._finalizer = weakref.finalize(
            self,
            _close_runtime_components,
            retrieve_ctl=getattr(self, "retrieve_ctl", None),
            action_policy=getattr(self, "action_policy", None),
            runtime_manager=getattr(self, "runtime_manager", None),
            lifecycle_bridge=getattr(self, "_lifecycle_event_bridge", None),
            tools=getattr(self, "tools", None),
            runtime_storage=getattr(self, "runtime_storage", None),
            sandbox_runner=getattr(self, "sandbox_runner", None),
            authored_tools=getattr(self, "authored_tools", None),
            telemetry_service=getattr(self, "telemetry_service", None),
        )

    @property
    def llm(self) -> RuntimeLLMHandle:
        return self.llm_runtime

    @staticmethod
    def _bind_runtime_handle(agent_service: object, runtime: "APIRuntime") -> None:
        binder = getattr(agent_service, "bind_runtime_handle", None)
        if callable(binder):
            binder(runtime)

    @classmethod
    def from_config_path(
        cls,
        config_path: Optional[str],
        *,
        home_root: Optional[str] = None,
        data_root: Optional[str] = None,
        run_profile_overrides: RunProfileOverrides | None = None,
        logging_mode: str = "default",
    ) -> "APIRuntime":
        resolved_home_root = (
            Path(home_root).expanduser().resolve()
            if home_root and str(home_root).strip()
            else None
        )
        resolved_data_root = (
            Path(data_root).expanduser().resolve()
            if data_root and str(data_root).strip()
            else None
        )
        manager = ConfigManager.load(
            config_path,
            home_root=resolved_home_root,
            data_root=resolved_data_root,
        )
        return cls.from_manager(
            manager,
            run_profile_overrides=run_profile_overrides,
            logging_mode=logging_mode,
        )

    @classmethod
    def from_manager(
        cls,
        manager: ConfigManager,
        *,
        config: OpenMinionConfig | None = None,
        home_root: Path | None = None,
        data_root: Path | None = None,
        config_path: Path | None = None,
        run_profile_overrides: RunProfileOverrides | None = None,
        logging_mode: str = "default",
    ) -> "APIRuntime":
        bootstrap_config_manager(manager)
        base_config = config or manager.base_config
        effective_run_profile_overrides = run_profile_overrides or RunProfileOverrides()
        (
            resolved_home_root,
            resolved_data_root,
            resolved_config_path,
            storage_path,
            memory_root,
        ) = _resolve_runtime_bootstrap_paths(
            manager=manager,
            base_config=base_config,
            home_root=home_root,
            data_root=data_root,
            config_path=config_path,
        )
        infrastructure = _build_runtime_infrastructure(
            base_config=base_config,
            manager=manager,
            resolved_home_root=resolved_home_root,
            resolved_data_root=resolved_data_root,
            resolved_config_path=resolved_config_path,
            storage_path=storage_path,
            memory_root=memory_root,
            effective_run_profile_overrides=effective_run_profile_overrides,
            logging_mode=logging_mode,
        )
        agent, gateway, runtime_mode, fallback_reason = _build_default_runtime_stack(
            base_config=base_config,
            manager=manager,
            resolved_home_root=resolved_home_root,
            resolved_data_root=resolved_data_root,
            resolved_config_path=resolved_config_path,
            storage_path=storage_path,
            memory_root=memory_root,
            infrastructure=infrastructure,
        )
        return _finalize_runtime_instance(
            cls=cls,
            base_config=base_config,
            manager=manager,
            resolved_home_root=resolved_home_root,
            resolved_data_root=resolved_data_root,
            resolved_config_path=resolved_config_path,
            storage_path=storage_path,
            memory_root=memory_root,
            infrastructure=infrastructure,
            agent=agent,
            gateway=gateway,
            runtime_mode=runtime_mode,
            fallback_reason=fallback_reason,
            effective_run_profile_overrides=effective_run_profile_overrides,
        )

    @classmethod
    def from_config(
        cls,
        *,
        config: OpenMinionConfig,
        home_root: Path,
        data_root: Path,
        config_path: Optional[Path] = None,
        manager: ConfigManager | None = None,
        run_profile_overrides: RunProfileOverrides | None = None,
    ) -> "APIRuntime":
        resolved_config_path = (
            config_path
            if config_path is not None
            else resolve_config_path(None, home_root=home_root)
        )
        effective_manager = manager
        if effective_manager is None:
            effective_manager = ConfigManager(
                base_config=config,
                home_root=home_root,
                data_root=data_root,
                config_path=resolved_config_path,
            )
        return cls.from_manager(
            effective_manager,
            config=config,
            home_root=home_root,
            data_root=data_root,
            config_path=resolved_config_path,
            run_profile_overrides=run_profile_overrides,
        )

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
        effective_overrides = self._combined_run_profile_overrides(overrides)
        return resolve_runtime_profile(
            self.config,
            agent_id=agent_id,
            overrides=effective_overrides,
        )

    def capability_runtime_diagnostics(
        self,
        agent_id: Optional[str] = None,
        overrides: RunProfileOverrides | None = None,
    ) -> dict[str, Any]:
        effective_overrides = self._combined_run_profile_overrides(overrides)
        return build_capability_runtime_diagnostics(
            self.config,
            agent_id=agent_id,
            overrides=effective_overrides,
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
            agent_service, runtime_mode, fallback_reason = build_agent_runtime_service(
                config=runtime_config,
                plugins=self.plugins,
                provider=provider,
                llm_runtime=llm_runtime,
                logger=self.logger.getChild(f"agent.{profile.name}"),
                tools=_scoped_tools_for_agent(self.tools, profile),
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
            self._bind_runtime_handle(agent_service, self)
            _bind_mcp_sampling_executor(self.tools, agent_service)
            self._agent_services[cache_key] = agent_service
            self._agent_runtime_modes[cache_key] = runtime_mode
            self._agent_runtime_fallback_reasons[cache_key] = fallback_reason
            return agent_service

    def get_agent_runtime_info(
        self,
        agent_id: Optional[str] = None,
        overrides: RunProfileOverrides | None = None,
    ) -> dict:
        """ARTR-03: Get runtime mode and fallback reason for an agent."""
        effective_overrides = self._combined_run_profile_overrides(overrides)
        profile = self.resolve_agent_profile(agent_id, overrides=overrides)
        cache_key = self._runtime_cache_key(
            agent_name=profile.name,
            overrides=effective_overrides,
        )
        return {
            "runtime_mode": self._agent_runtime_modes.get(cache_key, "unknown"),
            "fallback_reason": self._agent_runtime_fallback_reasons.get(cache_key, ""),
            "brain_bridge_active": self._agent_runtime_modes.get(cache_key, "")
            == "brain",
        }

    def tool_inventory_report(self) -> list[dict[str, Any]]:
        from openminion.api.queries.runtime_reports import build_tool_inventory_report

        return build_tool_inventory_report(
            self,
        )

    def tool_schema_report(self, *, tool_name: str) -> dict[str, Any] | None:
        from openminion.api.queries.runtime_reports import build_tool_schema_report

        return build_tool_schema_report(self, tool_name=tool_name)

    def capability_report(
        self,
        agent_id: Optional[str] = None,
        overrides: RunProfileOverrides | None = None,
    ) -> dict[str, Any]:
        from openminion.api.queries.runtime_reports import build_capability_report

        return build_capability_report(
            self,
            agent_id=agent_id,
            overrides=overrides,
        )

    def runtime_posture(
        self,
        agent_id: Optional[str] = None,
        overrides: RunProfileOverrides | None = None,
    ) -> dict[str, Any]:
        from openminion.api.queries.runtime_reports import (
            build_runtime_posture_report,
        )

        return build_runtime_posture_report(
            self,
            agent_id=agent_id,
            overrides=overrides,
            canonical_turn_path=_CANONICAL_TURN_PATH,
            canonical_turn_path_ref=_CANONICAL_TURN_PATH_REF,
            execution_boundary_policy_ref=_EXECUTION_BOUNDARY_POLICY_REF,
            capability_layering_ref=_CAPABILITY_LAYERING_REF,
        )

    def runtime_self_model(
        self,
        agent_id: Optional[str] = None,
        overrides: RunProfileOverrides | None = None,
    ) -> dict[str, Any]:
        from openminion.api.queries.self_model import build_runtime_self_model

        snapshot = build_runtime_self_model(
            self,
            agent_id=agent_id,
            overrides=overrides,
        )
        self._emit_runtime_self_model_snapshot(snapshot.model_dump(mode="json"))
        return snapshot.model_dump(mode="json")

    def _emit_runtime_self_model_snapshot(self, snapshot: dict[str, Any]) -> None:
        try:
            from openminion.modules.telemetry.self_awareness import (
                build_self_model_snapshot_event,
            )
            from openminion.modules.telemetry.schemas import TelemetryEvent

            telemetry_service = getattr(self, "telemetry_service", None)
            record_sync = getattr(telemetry_service, "record_event_sync", None)
            if record_sync is None:
                return
            event_type, data = build_self_model_snapshot_event(snapshot)
            record_sync(
                TelemetryEvent(
                    session_id="runtime",
                    turn_id="self-model",
                    event_type=event_type,
                    data=data,
                )
            )
        except Exception:
            return

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
                    profile.name,
                    overrides=overrides,
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
            for cache_key in tuple(self._gateways):
                if cache_key == normalized or cache_key.startswith(f"{normalized}||"):
                    self._gateways.pop(cache_key, None)
            for cache_key in tuple(self._agent_services):
                if cache_key == normalized or cache_key.startswith(f"{normalized}||"):
                    self._agent_services.pop(cache_key, None)
        self.logger.getChild("runtime").info(
            "evicted agent runtime cache agent_id=%s reason=%s",
            normalized,
            reason,
        )

    def list_registered_agents(self) -> list[str]:
        configured = {
            str(agent_id).strip()
            for agent_id in self.config.agents.keys()
            if str(agent_id).strip()
        }
        return sorted(item for item in configured if item)

    def list_hot_agents(self) -> list[str]:
        with self._agent_runtime_lock:
            return sorted(
                {
                    str(agent_id).split("||", 1)[0]
                    for agent_id in self._agent_services.keys()
                    if str(agent_id).strip()
                }
            )

    def run_turn(
        self,
        *,
        payload: dict[str, object],
        request_id: str | None = None,
        progress_callback=None,  # noqa: ANN001
        approval_callback=None,  # noqa: ANN001
    ) -> dict[str, object]:
        """Run one turn through the gateway, forwarding optional callbacks."""

        from openminion.services.runtime.ingress import run_turn_payload

        return run_turn_payload(
            runtime=self,
            payload=dict(payload),
            request_id=request_id,
            progress_callback=progress_callback,
            approval_callback=approval_callback,
        )

    def submit_turn(
        self,
        *,
        payload: dict[str, object],
    ):
        from openminion.services.runtime.ingress import submit_turn_payload

        return submit_turn_payload(
            runtime=self,
            payload=dict(payload),
        )

    def evict_agent(self, agent_id: str, *, reason: str = "manual") -> bool:
        normalized = str(agent_id or "").strip()
        if not normalized:
            return False
        with self._agent_runtime_lock:
            had_gateway = normalized in self._gateways
            had_agent = normalized in self._agent_services
        self.evict_agent_runtime(agent_id=normalized, reason=reason)
        return bool(had_gateway or had_agent)

    def close(self) -> None:
        if self._closed:
            return
        finalizer = getattr(self, "_finalizer", None)
        if finalizer is not None and finalizer.alive:
            finalizer.detach()
        _close_runtime_components(
            retrieve_ctl=getattr(self, "retrieve_ctl", None),
            action_policy=getattr(self, "action_policy", None),
            runtime_manager=getattr(self, "runtime_manager", None),
            lifecycle_bridge=getattr(self, "_lifecycle_event_bridge", None),
            tools=getattr(self, "tools", None),
            runtime_storage=getattr(self, "runtime_storage", None),
            sandbox_runner=getattr(self, "sandbox_runner", None),
            authored_tools=getattr(self, "authored_tools", None),
            telemetry_service=getattr(self, "telemetry_service", None),
        )
        self._closed = True


def _bootstrap_openminion_brain_import_path() -> None:
    """Compatibility hook kept after module migration in-tree."""
    return
