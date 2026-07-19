"""Build the service and module infrastructure used by the API runtime."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from openminion.base.config import (
    AgentProfileConfig,
    ConfigManager,
    OpenMinionConfig,
    RunProfileOverrides,
    build_runtime_config,
)
from openminion.base.config.core import resolve_default_agent_id
from openminion.base.logging import configure_logging
from openminion.modules.llm.providers.factory import build_runtime_llm_handle
from openminion.modules.storage.runtime import (
    build_runtime_storage,
    resolve_database_path,
)
from openminion.modules.telemetry import storage_hook
from openminion.modules.telemetry.service import TelemetryService
from openminion.services.agent.memory import resolve_memory_root
from openminion.services.brain.factory.retrieve import build_retrieve_service
from openminion.services.channel.authenticity import build_channel_authenticity_policy
from openminion.services.lifecycle.self_improvement import SelfImprovementEngine
from openminion.services.runtime.bootstrap import (
    build_action_policy_service,
    build_agent_memory_service,
    build_daytona_runner,
    build_knowledge_graph_source_service,
    build_session_context_service,
    build_tool_authoring_service,
    enforce_plugin_activation_policy,
)
from openminion.services.runtime.env import apply_runtime_environment
from openminion.services.runtime.lifecycle import LifecycleService
from openminion.modules.policy import SecurityPolicyEngine, ToolBudgetPolicy


@dataclass(frozen=True)
class RuntimePaths:
    home: Path
    data: Path
    config: Path
    storage: Path
    memory: Path


def scoped_tools_for_agent(tools: Any, profile: AgentProfileConfig) -> Any:
    from openminion.tools.mcp.exposure import scoped_mcp_registry_view

    return scoped_mcp_registry_view(tools, getattr(profile, "mcp_exposure", None))


def bind_mcp_sampling_executor(tools: Any, agent_service: Any) -> None:
    manager = getattr(tools, "mcp_manager", None)
    bind = getattr(manager, "bind_sampling_executor", None)
    executor = getattr(agent_service, "_invoke_provider_request", None)
    if callable(bind) and callable(executor):
        bind(executor)


def resolve_runtime_bootstrap_paths(
    *,
    manager: ConfigManager,
    base_config: OpenMinionConfig,
    home_root: Path | None,
    data_root: Path | None,
    config_path: Path | None,
) -> RuntimePaths:
    resolved_home_root = home_root or manager.home_root
    resolved_data_root = data_root or manager.data_root
    resolved_config_path = config_path or manager.config_path
    if resolved_config_path is None:
        from openminion.base.config import resolve_config_path

        resolved_config_path = resolve_config_path(None, home_root=resolved_home_root)
    storage_path = resolve_database_path(base_config.storage.path, env=manager.env)
    memory_root = resolve_memory_root(
        config=base_config,
        config_path=resolved_config_path,
        storage_path=storage_path,
        data_root=resolved_data_root,
    )
    return RuntimePaths(
        resolved_home_root,
        resolved_data_root,
        resolved_config_path,
        storage_path,
        memory_root,
    )


def build_runtime_infrastructure(
    *,
    base_config: OpenMinionConfig,
    manager: ConfigManager,
    paths: RuntimePaths,
    effective_run_profile_overrides: RunProfileOverrides,
    disable_security_policy: bool,
    logging_mode: str,
) -> dict[str, object]:
    apply_runtime_environment(base_config.runtime.env)
    telemetry_service = TelemetryService(
        home_root=paths.home,
        env=getattr(base_config.runtime, "env", None),
        otel_exporter_config=getattr(base_config.runtime, "telemetry_exporter", None),
    )
    runtime_storage = build_runtime_storage(
        paths.storage,
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
    agent_security_policy = None if disable_security_policy else security_policy
    extension_runtime = LifecycleService.from_config(
        base_config,
        config_path=str(paths.config),
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
    _bind_channel_supervisor_telemetry(extension_runtime, telemetry_service)
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
        data_root=paths.data,
    )
    support = _build_runtime_support(
        base_config=base_config,
        manager=manager,
        paths=paths,
        logger=logger,
        default_agent=default_agent,
        runtime_storage=runtime_storage,
        tools=tools,
        action_policy=action_policy,
    )
    return {
        "telemetry_service": telemetry_service,
        "runtime_storage": runtime_storage,
        "logger": logger,
        "security_policy": security_policy,
        "agent_security_policy": agent_security_policy,
        **_extension_runtime_components(extension_runtime),
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
        **support,
    }


def _bind_channel_supervisor_telemetry(
    extension_runtime: Any,
    telemetry_service: TelemetryService,
) -> None:
    if extension_runtime.channel_supervisor is not None:
        extension_runtime.channel_supervisor.bind_telemetry_service(telemetry_service)


def _extension_runtime_components(extension_runtime: Any) -> dict[str, object]:
    return {
        "channels": extension_runtime.channels,
        "channel_supervisor": extension_runtime.channel_supervisor,
        "plugins": extension_runtime.plugins,
    }


def _build_runtime_support(
    *,
    base_config: OpenMinionConfig,
    manager: ConfigManager,
    paths: RuntimePaths,
    logger: Any,
    default_agent: AgentProfileConfig,
    runtime_storage: Any,
    tools: Any,
    action_policy: Any,
) -> dict[str, object]:
    try:
        retrieve_config = manager.get("retrieve")
    except Exception:
        retrieve_config = None
    retrieve_ctl = build_retrieve_service(
        home_root=paths.home,
        vector_adapter=None,
        config=retrieve_config,
        logger=logger.getChild("retrieve"),
    )
    session_context = build_session_context_service(
        config=base_config,
        sessions=runtime_storage.sessions,
        logger=logger.getChild("gateway.session_context"),
        config_path=paths.config,
        storage_path=paths.storage,
        memory_root=paths.memory,
        data_root=paths.data,
        retrieve_ctl=retrieve_ctl,
    )
    agent_memory = build_agent_memory_service(
        config=base_config,
        agent_id=default_agent.name,
        memory_root=paths.memory,
        logger=logger.getChild("gateway.agent_memory"),
        config_manager=manager,
        home_root=paths.home,
        data_root=paths.data,
        session_context=session_context,
        retrieve_ctl=retrieve_ctl,
        storage_path=paths.storage,
    )
    knowledge_graphs = build_knowledge_graph_source_service(config=base_config)
    sandbox_runner = build_daytona_runner(config=base_config, config_manager=manager)
    authored_tools = build_tool_authoring_service(
        config=base_config,
        data_root=paths.data,
        tool_registry=tools,
        sandbox_runner=sandbox_runner,
        policy_ctl=action_policy,
    )
    return {
        "retrieve_ctl": retrieve_ctl,
        "session_context": session_context,
        "agent_memory": agent_memory,
        "knowledge_graphs": knowledge_graphs,
        "sandbox_runner": sandbox_runner,
        "authored_tools": authored_tools,
    }
