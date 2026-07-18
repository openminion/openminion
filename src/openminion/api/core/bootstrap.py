"""Assemble APIRuntime from resolved configuration and infrastructure."""

from __future__ import annotations

import logging
from os import PathLike
from pathlib import Path
from typing import Any, cast

from openminion.base.config import (
    AgentProfileConfig,
    ConfigManager,
    OpenMinionConfig,
    RunProfileOverrides,
    resolve_config_path,
)
from openminion.services.agent import AgentService
from openminion.services.bootstrap.config import bootstrap_config_manager
from openminion.services.gateway import GatewayService
from openminion.services.runtime.bootstrap import (
    build_agent_runtime_service,
    build_gateway_service,
)
from openminion.services.runtime.daemon import build_runtime_manager

from .infrastructure import (
    RuntimePaths,
    bind_mcp_sampling_executor,
    build_runtime_infrastructure,
    resolve_runtime_bootstrap_paths,
    scoped_tools_for_agent,
)


def _resolve_root(value: str | PathLike[str] | None) -> Path | None:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return Path(value).expanduser().resolve()


def build_default_runtime_stack(
    *,
    manager: ConfigManager,
    paths: RuntimePaths,
    infrastructure: dict[str, object],
) -> tuple[AgentService, GatewayService, str, str]:
    logger = cast(logging.Logger, infrastructure["logger"])
    runtime_storage = cast(Any, infrastructure["runtime_storage"])
    default_agent = cast(AgentProfileConfig, infrastructure["default_agent"])
    agent, runtime_mode, fallback_reason = build_agent_runtime_service(
        config=infrastructure["default_config"],
        plugins=infrastructure["plugins"],
        provider=infrastructure["provider"],
        llm_runtime=infrastructure["llm_runtime"],
        logger=logger.getChild("agent"),
        tools=scoped_tools_for_agent(infrastructure["tools"], default_agent),
        security_policy=infrastructure["agent_security_policy"],
        self_improvement=infrastructure["self_improvement"],
        storage_path=paths.storage,
        home_root=paths.home,
        data_root=paths.data,
        config_path=paths.config,
        config_manager=manager,
        retrieve_service=infrastructure["retrieve_ctl"],
        action_policy_service=infrastructure["action_policy"],
    )
    bind_mcp_sampling_executor(infrastructure["tools"], agent)
    gateway = build_gateway_service(
        agent_service=agent,
        profile_name=default_agent.name,
        config=infrastructure["default_config"],
        channels=infrastructure["channels"],
        sessions=runtime_storage.sessions,
        idempotency=runtime_storage.idempotency,
        security_policy=infrastructure["agent_security_policy"],
        channel_authenticity_policy=infrastructure["channel_authenticity_policy"],
        config_path=paths.config,
        storage_path=paths.storage,
        memory_root=paths.memory,
        home_root=paths.home,
        data_root=paths.data,
        logger=logger,
        config_manager=manager,
        session_context=infrastructure["session_context"],
        agent_memory=infrastructure["agent_memory"],
        knowledge_graphs=infrastructure["knowledge_graphs"],
        retrieve_ctl=infrastructure["retrieve_ctl"],
    )
    return agent, gateway, runtime_mode, fallback_reason


def finalize_runtime_instance(
    *,
    cls: type,
    base_config: OpenMinionConfig,
    manager: ConfigManager,
    paths: RuntimePaths,
    infrastructure: dict[str, object],
    agent: AgentService,
    gateway: GatewayService,
    runtime_mode: str,
    fallback_reason: str,
    effective_run_profile_overrides: RunProfileOverrides,
) -> Any:
    runtime_storage = cast(Any, infrastructure["runtime_storage"])
    runtime = cls(
        config=base_config,
        config_path=paths.config,
        home_root=paths.home,
        data_root=paths.data,
        storage_path=paths.storage,
        memory_root=paths.memory,
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
        memory_queries=infrastructure["agent_memory"],
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
    cache_key = runtime._runtime_cache_key(
        agent_name=cast(AgentProfileConfig, infrastructure["default_agent"]).name,
        overrides=effective_run_profile_overrides,
    )
    runtime._agent_services[cache_key] = agent
    runtime._gateways[cache_key] = gateway
    runtime._agent_runtime_modes[cache_key] = runtime_mode
    runtime._agent_runtime_fallback_reasons[cache_key] = fallback_reason
    return runtime


class RuntimeBootstrapMixin:
    @classmethod
    def from_config_path(
        cls,
        config_path: str | None,
        *,
        home_root: str | PathLike[str] | None = None,
        data_root: str | PathLike[str] | None = None,
        run_profile_overrides: RunProfileOverrides | None = None,
        logging_mode: str = "default",
    ) -> Any:
        resolved_home_root = _resolve_root(home_root)
        resolved_data_root = _resolve_root(data_root)
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
    ) -> Any:
        bootstrap_config_manager(manager)
        base_config = config or manager.base_config
        effective_overrides = run_profile_overrides or RunProfileOverrides()
        paths = resolve_runtime_bootstrap_paths(
            manager=manager,
            base_config=base_config,
            home_root=home_root,
            data_root=data_root,
            config_path=config_path,
        )
        security_policy_disabled = cls._security_policy_disabled(
            getattr(base_config.runtime, "env", None)
        )
        infrastructure = build_runtime_infrastructure(
            base_config=base_config,
            manager=manager,
            paths=paths,
            effective_run_profile_overrides=effective_overrides,
            disable_security_policy=security_policy_disabled,
            logging_mode=logging_mode,
        )
        agent, gateway, mode, fallback = build_default_runtime_stack(
            manager=manager,
            paths=paths,
            infrastructure=infrastructure,
        )
        return finalize_runtime_instance(
            cls=cls,
            base_config=base_config,
            manager=manager,
            paths=paths,
            infrastructure=infrastructure,
            agent=agent,
            gateway=gateway,
            runtime_mode=mode,
            fallback_reason=fallback,
            effective_run_profile_overrides=effective_overrides,
        )

    @classmethod
    def from_config(
        cls,
        *,
        config: OpenMinionConfig,
        home_root: Path,
        data_root: Path,
        config_path: Path | None = None,
        manager: ConfigManager | None = None,
        run_profile_overrides: RunProfileOverrides | None = None,
    ) -> Any:
        resolved_path = config_path or resolve_config_path(None, home_root=home_root)
        effective_manager = manager or ConfigManager(
            base_config=config,
            home_root=home_root,
            data_root=data_root,
            config_path=resolved_path,
        )
        return cls.from_manager(
            effective_manager,
            config=config,
            home_root=home_root,
            data_root=data_root,
            config_path=resolved_path,
            run_profile_overrides=run_profile_overrides,
        )
