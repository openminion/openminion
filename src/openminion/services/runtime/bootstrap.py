from pathlib import Path
from typing import Any, Optional, cast
import logging

from openminion.base.channel import ChannelRegistry
from openminion.base.config import AgentProfileConfig, OpenMinionConfig
from openminion.base.config import ConfigManager
from openminion.base.config.core import resolve_default_agent_id
from openminion.base.config.env import EnvironmentConfig
from openminion.modules.artifact.refs import create_default_artifactctl
from openminion.services.runtime.plugins import PluginManifest, PluginRegistry
from openminion.modules.storage.runtime.idempotency_store import IdempotencyStore
from openminion.modules.storage.runtime.session_store import SessionStore
from openminion.modules.tool import ToolRegistry
from openminion.services.agent.memory.gateway_adapter import (
    DisabledMemoryGatewayAdapter,
    MemoryServiceGatewayAdapter,
)
from openminion.modules.memory.smoke import (
    EphemeralMemorySmokeProvider,
)
from openminion.modules.context.knowledge import (
    KNOWLEDGE_GRAPHS_CONFIG_KEY,
    KnowledgeGraphProviderFactory,
    KnowledgeGraphRegistry,
    PROVIDER_GRAPHIFY,
    PROVIDER_PRAGMAGRAPH,
)
from openminion.modules.context.knowledge.adapters.graphify import (
    GraphifyKnowledgeGraphSource,
)
from openminion.modules.context.knowledge.adapters.pragmagraph import (
    PragmaGraphKnowledgeGraphSource,
)
from openminion.modules.context.knowledge.service import (
    KnowledgeGraphService,
    build_knowledge_graph_service as build_configured_knowledge_graph_service,
    empty_knowledge_graph_service,
)
from openminion.modules.tool.authoring import (
    ToolAuthoringService,
    build_authored_tool_store,
    default_tool_authoring_audit_db_path,
)
from openminion.modules.tool.authoring.config import TOOL_AUTHORING_ALLOWED_DEPS_ENV
from openminion.modules.tool.authoring.storage import SQLiteToolAuthoringAuditSink
from openminion.services.context.session import (
    SessionContextService,
    resolve_session_archive_root,
)
from openminion.modules.brain.paths import resolve_brain_runtime_db_path
from openminion.modules.policy import (
    DECISION_ALLOW,
    SecurityPolicyAction,
    SecurityPolicyCheck,
    SecurityPolicyContext,
    SecurityPolicyEngine,
    default_internal_actor,
    derive_plugin_activation_risk,
    evaluate_plugin_trust_policy,
)
from openminion.services.gateway import GatewayService
from openminion.services.lifecycle.self_improvement import SelfImprovementEngine
from openminion.services.config import resolve_services_env
from openminion.base.config.action_policy import map_action_policy_mode
from openminion.modules.policy.runtime.action_policy import (
    policy_config_from_action_policy,
)
from openminion.modules.runtime.sandboxes.daytona import (
    DaytonaClient,
    DaytonaConfig,
    DaytonaRunner,
)
from openminion.services.runtime.errors import (
    PluginActivationError,
    RuntimeBootstrapError,
)
from openminion.services.runtime.memory import (
    _build_memory_v2_gateway_adapter as _build_bootstrap_memory_v2_gateway_adapter_impl,
    _normalize_runtime_memory_provider,
    _resolve_env_override,
    _resolve_runtime_memory_config as _resolve_bootstrap_memory_config_impl,
)


def _map_action_policy_mode(mode: str) -> str:
    return map_action_policy_mode(mode)


def build_action_policy_service(
    *,
    config: OpenMinionConfig,
    tool_registry: ToolRegistry,
    data_root: Path,
) -> Any | None:
    """Build the canonical policy service once per runtime bootstrap."""
    from openminion.modules.policy.runtime.service import PolicyCtl

    policy_dir = data_root / "policy"
    policy_dir.mkdir(parents=True, exist_ok=True)
    db_path = policy_dir / "policy.db"

    action_policy = config.action_policy
    policy_ctl = PolicyCtl.with_sqlite(
        db_path,
        config=policy_config_from_action_policy(action_policy),
    )

    for tool_name, tool in tool_registry.list().items():
        derived = _derive_tool_risk_spec(tool_name=tool_name, tool=tool)
        policy_ctl.register_risk(tool_name, derived)
        # Runner policy adapter maps single-token tool names to `<tool>.default`.
        if "." not in tool_name:
            policy_ctl.register_risk(f"{tool_name}.default", derived)

    return policy_ctl


def build_daytona_runner(
    *,
    config: OpenMinionConfig,
    config_manager: ConfigManager | None = None,
) -> DaytonaRunner | None:
    runtime_env = getattr(getattr(config, "runtime", None), "env", {})
    if not isinstance(runtime_env, dict):
        runtime_env = {}
    merged_env = resolve_services_env(runtime_env=runtime_env).snapshot()
    if config_manager is not None and isinstance(config_manager.env, EnvironmentConfig):
        for key in (
            "OPENMINION_DAYTONA_ENDPOINT",
            "OPENMINION_DAYTONA_API_KEY",
            "OPENMINION_DAYTONA_API_KEY_ENV",
            "OPENMINION_DAYTONA_IMAGE",
            "OPENMINION_DAYTONA_CONNECT_TIMEOUT_S",
            "OPENMINION_DAYTONA_COMMAND_TIMEOUT_S",
            "OPENMINION_DAYTONA_MAX_OUTPUT_BYTES",
            "OPENMINION_DAYTONA_VERIFY_TLS",
        ):
            value = str(config_manager.env.get(key, "") or "").strip()
            if value:
                merged_env[key] = value
    daytona_config = DaytonaConfig.from_environment(merged_env)
    if daytona_config is None:
        return None
    return DaytonaRunner(client=DaytonaClient(config=daytona_config))


def build_tool_authoring_service(
    *,
    config: OpenMinionConfig,
    data_root: Path,
    tool_registry: ToolRegistry,
    sandbox_runner: Any | None,
    policy_ctl: Any | None,
) -> ToolAuthoringService:
    runtime_env = getattr(getattr(config, "runtime", None), "env", {})
    if not isinstance(runtime_env, dict):
        runtime_env = {}
    merged_env = resolve_services_env(runtime_env=runtime_env).snapshot()
    raw_allowed = str(merged_env.get(TOOL_AUTHORING_ALLOWED_DEPS_ENV, "") or "").strip()
    allowed_dependencies = {
        token.strip() for token in raw_allowed.split(",") if token.strip()
    }
    store_dir = data_root / "authored_tools"
    store_path = store_dir / "store.sqlite"
    store = build_authored_tool_store(sqlite_path=store_path)
    audit_sink = SQLiteToolAuthoringAuditSink(
        default_tool_authoring_audit_db_path(store_path)
    )
    service = ToolAuthoringService(
        store=store,
        audit_sink=audit_sink,
        sandbox_runner=sandbox_runner,
        tool_registry=tool_registry,
        policy_ctl=policy_ctl,
        allowed_dependencies=allowed_dependencies,
    )
    service.register_runtime_tools(tool_registry)
    return service


def _derive_tool_risk_spec(*, tool_name: str, tool: Any) -> Any:
    from openminion.modules.policy.models import RiskSpec

    min_scope = (
        str(getattr(tool, "min_scope", "READ_ONLY") or "READ_ONLY").strip().upper()
    )
    dangerous = bool(getattr(tool, "dangerous", False))
    idempotent = bool(getattr(tool, "idempotent", True))

    policy = getattr(tool, "policy", None)
    policy_risk = str(getattr(policy, "risk", "") or "").strip().lower()
    if policy_risk in {"high", "critical"}:
        dangerous = True

    if dangerous:
        return RiskSpec(
            risk_class="destructive",
            side_effects="local",
            reversibility="irreversible",
            default_confirm=True,
        )
    if min_scope == "READ_ONLY":
        return RiskSpec(
            risk_class="read",
            side_effects="none",
            reversibility="reversible",
            default_confirm=False,
        )
    if min_scope == "WRITE_SAFE":
        return RiskSpec(
            risk_class="write",
            side_effects="local",
            reversibility="reversible" if idempotent else "unknown",
            default_confirm=not idempotent,
        )
    if min_scope in {"POWER_USER", "UI_AUTOMATION"}:
        return RiskSpec(
            risk_class="exec",
            side_effects="local",
            reversibility="unknown",
            default_confirm=True,
        )
    return RiskSpec(
        risk_class="write",
        side_effects="local",
        reversibility="unknown",
        default_confirm=not idempotent,
    )


def resolve_default_agent(config: OpenMinionConfig) -> AgentProfileConfig:
    from openminion.base.config import resolve_agent_config

    return resolve_agent_config(config, None)


def replace_agent(
    config: OpenMinionConfig, agent: AgentProfileConfig
) -> OpenMinionConfig:
    from dataclasses import replace

    default_id = resolve_default_agent_id(config)
    new_agents = dict(config.agents)
    new_agents[default_id] = agent
    return replace(config, agents=new_agents)


def build_knowledge_graph_source_service(
    *,
    config: OpenMinionConfig,
) -> KnowledgeGraphService:
    """Build active knowledge-graph sources from OpenMinion config."""
    if not getattr(config, "module_configs", {}).get(KNOWLEDGE_GRAPHS_CONFIG_KEY):
        return empty_knowledge_graph_service()
    registry = KnowledgeGraphRegistry()
    registry.register(
        PROVIDER_GRAPHIFY,
        cast(KnowledgeGraphProviderFactory, GraphifyKnowledgeGraphSource),
    )
    registry.register(
        PROVIDER_PRAGMAGRAPH,
        cast(KnowledgeGraphProviderFactory, PragmaGraphKnowledgeGraphSource),
    )
    return build_configured_knowledge_graph_service(config, registry=registry)


def build_session_context_service(
    *,
    config: OpenMinionConfig,
    sessions: SessionStore,
    logger: logging.Logger,
    config_path: Path,
    storage_path: Path,
    memory_root: Path,
    data_root: Path,
    retrieve_ctl: Any | None = None,
) -> SessionContextService:
    archive_root = resolve_session_archive_root(
        config=config,
        config_path=config_path,
        storage_path=storage_path,
        memory_root=memory_root,
        data_root=data_root,
    )
    return SessionContextService(
        sessions,
        logger=logger,
        keep_recent_messages=config.runtime.session_keep_recent_messages,
        max_compact_per_turn=config.runtime.session_max_compact_per_turn,
        summary_max_chars=config.runtime.session_summary_max_chars,
        archive_enabled=bool(config.runtime.session_archive_enabled),
        archive_root=archive_root,
        archive_ref_limit=config.runtime.session_archive_ref_limit,
        token_budget=max(0, int(config.runtime.session_context_token_budget)),
        chars_per_token=max(0.1, float(config.runtime.session_context_chars_per_token)),
        summary_enrichment_enabled=bool(
            getattr(config.runtime, "session_summary_enrichment_enabled", False)
        ),
        retrieve_ctl=retrieve_ctl,
    )


def build_agent_memory_service(
    *,
    config: OpenMinionConfig,
    agent_id: str,
    memory_root: Path,
    logger: logging.Logger,
    config_manager: ConfigManager | None = None,
    home_root: Path | None = None,
    data_root: Path | None = None,
    session_context: SessionContextService | None = None,
    retrieve_ctl: Any | None = None,
    storage_path: Path | None = None,
) -> (
    EphemeralMemorySmokeProvider
    | MemoryServiceGatewayAdapter
    | DisabledMemoryGatewayAdapter
):
    env_provider = _resolve_env_override(
        config_manager=config_manager,
        config=config,
        key="OPENMINION_MEMORY_PROVIDER",
    )
    configured_provider = (
        env_provider
        or str(getattr(config.runtime, "memory_provider", "memory_v2")).strip()
    )
    normalized_provider = _normalize_runtime_memory_provider(configured_provider)

    # memory_v2_smoke: ephemeral smoke memory provider; memory_v2_hello_world remains a legacy alias
    if normalized_provider == "memory_v2_smoke":
        return EphemeralMemorySmokeProvider(
            agent_id=agent_id,
            logger=logger,
            enabled=bool(config.runtime.memory_enabled),
        )

    # memory_v2: V2 SQLite-backed adapter (new default)
    if normalized_provider == "memory_v2":
        if not bool(config.runtime.memory_enabled):
            return DisabledMemoryGatewayAdapter(agent_id=agent_id, logger=logger)
        return _build_bootstrap_memory_v2_gateway_adapter(
            config=config,
            agent_id=agent_id,
            memory_root=memory_root,
            logger=logger,
            config_manager=config_manager,
            home_root=home_root,
            data_root=data_root,
            session_context=session_context,
            retrieve_ctl=retrieve_ctl,
            storage_path=storage_path,
        )

    raise ValueError(
        "Unsupported runtime.memory_provider="
        f"{configured_provider!r}. Supported providers: memory_v2, memory_v2_smoke (memory_v2_hello_world is a legacy alias)."
    )


def _resolve_bootstrap_memory_config(
    *,
    config: OpenMinionConfig,
    memory_root: Path,
    config_manager: ConfigManager | None = None,
    home_root: Path | None = None,
    data_root: Path | None = None,
) -> Any:
    return _resolve_bootstrap_memory_config_impl(
        config=config,
        memory_root=memory_root,
        config_manager=config_manager,
        home_root=home_root,
        data_root=data_root,
    )


def _build_bootstrap_memory_v2_gateway_adapter(
    *,
    config: OpenMinionConfig,
    agent_id: str,
    memory_root: Path,
    logger: logging.Logger,
    config_manager: ConfigManager | None,
    home_root: Path | None,
    data_root: Path | None,
    session_context: SessionContextService | None,
    retrieve_ctl: Any | None,
    storage_path: Path | None,
) -> MemoryServiceGatewayAdapter:
    return _build_bootstrap_memory_v2_gateway_adapter_impl(
        config=config,
        agent_id=agent_id,
        memory_root=memory_root,
        logger=logger,
        config_manager=config_manager,
        home_root=home_root,
        data_root=data_root,
        session_context=session_context,
        retrieve_ctl=retrieve_ctl,
        storage_path=storage_path,
        adapter_cls=MemoryServiceGatewayAdapter,
        resolve_runtime_memory_config_fn=_resolve_bootstrap_memory_config,
        artifactctl_factory=create_default_artifactctl,
    )


def enforce_plugin_activation_policy(
    *,
    security_policy: SecurityPolicyEngine,
    agent_id: str,
    manifest: PluginManifest,
) -> None:
    trust_decision = evaluate_plugin_trust_policy(
        trust_tier=manifest.trust_tier,
        requested_capabilities=set(manifest.requested_capabilities),
        provenance_source=manifest.provenance_source,
        provenance_verified=manifest.provenance_verified,
        provenance_publisher=manifest.provenance_publisher,
        policy_version=security_policy.policy_version,
    )
    if trust_decision.decision != DECISION_ALLOW:
        raise PluginActivationError(
            "plugin trust policy blocked activation "
            f"(plugin={manifest.id}, decision={trust_decision.decision}, reason={trust_decision.reason_code})"
        )

    decision = security_policy.evaluate(
        SecurityPolicyCheck(
            actor=default_internal_actor(agent_id, include_admin=True),
            action=SecurityPolicyAction(
                resource="plugin",
                verb="activate",
                risk=derive_plugin_activation_risk(
                    trust_tier=manifest.trust_tier,
                    requested_capabilities=set(manifest.requested_capabilities),
                ),
            ),
            context=SecurityPolicyContext(
                origin=f"plugin-manifest:{manifest.id}",
            ),
        )
    )
    if decision.decision == DECISION_ALLOW:
        return
    raise PluginActivationError(
        "security policy blocked plugin activation "
        f"(plugin={manifest.id}, decision={decision.decision}, reason={decision.reason_code})"
    )


def build_gateway_service(
    *,
    agent_service: Any,
    profile_name: str,
    config: OpenMinionConfig,
    channels: ChannelRegistry,
    sessions: SessionStore,
    idempotency: IdempotencyStore,
    security_policy: SecurityPolicyEngine,
    channel_authenticity_policy: Any,
    config_path: Path,
    storage_path: Path,
    memory_root: Path,
    home_root: Path,
    data_root: Path,
    logger: logging.Logger,
    config_manager: ConfigManager | None = None,
    session_context: Optional[SessionContextService] = None,
    agent_memory: Any = None,
    knowledge_graphs: Any | None = None,
    identity_ctl: Any = None,
    retrieve_ctl: Any | None = None,
) -> GatewayService:
    resolved_session_context = session_context or build_session_context_service(
        config=config,
        sessions=sessions,
        logger=logger.getChild(f"gateway.{profile_name}.session_context"),
        config_path=config_path,
        storage_path=storage_path,
        memory_root=memory_root,
        data_root=data_root,
        retrieve_ctl=retrieve_ctl,
    )
    resolved_memory = agent_memory or build_agent_memory_service(
        config=config,
        agent_id=profile_name,
        memory_root=memory_root,
        logger=logger.getChild(f"gateway.{profile_name}.agent_memory"),
        config_manager=config_manager,
        home_root=home_root,
        data_root=data_root,
        session_context=resolved_session_context,
        retrieve_ctl=retrieve_ctl,
        storage_path=storage_path,
    )

    # Seed identity pins into V2 memory at boot when profile is available
    if identity_ctl is not None and isinstance(
        resolved_memory, MemoryServiceGatewayAdapter
    ):
        _try_seed_identity(
            memory_adapter=resolved_memory,
            identity_ctl=identity_ctl,
            agent_id=profile_name,
            logger=logger,
        )
    if isinstance(resolved_memory, MemoryServiceGatewayAdapter):
        build_structurer = getattr(
            agent_service,
            "build_session_summary_structurer",
            None,
        )
        if callable(build_structurer):
            try:
                resolved_memory.configure_session_summary_structurer(build_structurer())
            except Exception as exc:
                logger.warning(
                    "session summary structurer unavailable for profile=%s error=%s",
                    profile_name,
                    exc,
                )

    return GatewayService(
        agent_service,
        channels,
        logger.getChild(f"gateway.{profile_name}"),
        sessions=sessions,
        idempotency=idempotency,
        agent_id=profile_name,
        security_policy=security_policy,
        channel_authenticity_policy=channel_authenticity_policy,
        session_context=resolved_session_context,
        agent_memory=resolved_memory,
        knowledge_graphs=knowledge_graphs,
        brain_integration_mode=getattr(
            config.gateway,
            "brain_integration_mode",
            "contextctl_authoritative",
        ),
    )


def _try_seed_identity(
    *,
    memory_adapter: Any,
    identity_ctl: Any,
    agent_id: str,
    logger: logging.Logger,
) -> None:
    """MV2-07: Try to seed identity pins; log and swallow errors."""
    try:
        from openminion.modules.memory.runtime.identity_seeder import seed_identity_pins

        profile = None
        get_profile = getattr(identity_ctl, "get_profile", None)
        if callable(get_profile):
            try:
                profile = get_profile(agent_id)
            except Exception as exc:
                logger.debug(
                    "identity_seeder: get_profile failed agent_id=%s error=%s",
                    agent_id,
                    exc,
                )
        if profile is None:
            load_profile = getattr(identity_ctl, "load_profile", None)
            if callable(load_profile):
                try:
                    profile = load_profile(agent_id)
                except Exception as exc:
                    logger.debug(
                        "identity_seeder: load_profile failed agent_id=%s error=%s",
                        agent_id,
                        exc,
                    )

        if profile is None:
            return

        service = getattr(memory_adapter, "_service", None)
        if service is None:
            return

        count = seed_identity_pins(
            profile=profile,
            memory_service=service,
            agent_id=agent_id,
        )
        logger.debug("identity_seeder: seeded %d pins for agent_id=%s", count, agent_id)
    except Exception as exc:
        logger.warning(
            "identity_seeder: failed to seed identity pins agent_id=%s error=%s",
            agent_id,
            exc,
        )


def build_brain_runner_bundle(service: Any) -> Any:
    """BBSE-02: canonical bootstrap path for the bridge's runner bundle."""
    from pathlib import Path as _Path

    import openminion.services.brain.service as bridge_module
    from openminion.base.config import configured_agent_ids
    from openminion.base.config.core import resolve_default_agent_id
    from openminion.modules.session.storage.repository import (
        create_sqlite_cron_repository,
    )
    from openminion.modules.brain.checkpoint import CheckpointManager
    from openminion.modules.brain.runtime.goal.long_running import (
        LongRunningGoalRuntime,
    )
    from openminion.modules.brain.storage.goals import SQLiteGoalStore
    from openminion.modules.brain.storage.missions import SQLiteMissionStateStore
    from openminion.modules.task import TaskManager
    from openminion.services.brain.service import _RuntimeProviderAdapter
    from openminion.services.brain.metadata import (
        resolve_agent_budgets,
        resolve_llm_profiles,
        resolve_runner_options,
    )
    from openminion.services.brain.factory.vector import init_vector_adapter
    from openminion.modules.tool.exposure import get_model_exposure_specs
    from openminion.modules.brain.schemas import AgentProfile

    config = service._config
    llm_config = service._get_manager_config("llm")
    llm_payload = llm_config if llm_config is not None else {}

    llm_api = bridge_module.create_llm_adapter(
        mode=service.mode,
        config=llm_payload,
        telemetryctl=service._telemetryctl,
    )

    if hasattr(service, "_provider") and service._provider:
        from openminion.modules.brain.adapters.llm import LlmctlAdapter
        from openminion.services.brain.client import OpenMinionLLMClient

        runtime_tool_specs: list[Any] = []
        if service._tools is not None:
            runtime_tool_specs = get_model_exposure_specs(service._tools)
        runtime_provider = _RuntimeProviderAdapter(service)
        llm_api = LlmctlAdapter(
            OpenMinionLLMClient(
                runtime_provider,
                invoke_provider_request=service._invoke_provider_request,
                runtime_tools=runtime_tool_specs,
                telemetryctl=service._telemetryctl,
                home_root=service._context.home_paths.home_root,
            )
        )

    session_api = bridge_module.create_session_api(
        mode=service.mode,
        db_path=service.db_path,
        telemetryctl=service._telemetryctl,
    )

    a2a_config = service._get_manager_config("a2a")
    default_agent_id = resolve_default_agent_id(config)
    default_profile = config.agents[default_agent_id]
    a2a_api = bridge_module.create_a2a_api(
        mode=service.mode,
        home_root=service._context.home_paths.home_root,
        agent_name=str(getattr(default_profile, "name", "") or ""),
        config=a2a_config,
        env=service._env,
        runtime_resolver=lambda: service._runtime_handle,
    )

    db_dir = (
        _Path(service.db_path).parent
        if _Path(service.db_path).suffix
        else _Path(service.db_path)
    )

    vector_adapter, service._vector_sync = init_vector_adapter(
        config=config,
        db_dir=db_dir,
        logger=service._logger,
    )

    skill_config = service._get_manager_config("skill")
    context_api = bridge_module.create_context_api(
        mode=service.mode,
        session_store=session_api,
        system_prompt=default_profile.system_prompt,
        identity_budget_config=getattr(
            getattr(config, "context", None),
            "identity_budget",
            None,
        ),
        runtime_token_budget=int(
            getattr(
                getattr(config, "runtime", object()),
                "session_context_token_budget",
                0,
            )
            or 0
        ),
        vector_adapter=vector_adapter,
        telemetryctl=service._telemetryctl,
        skill_config=skill_config,
        skill_home_root=service._context.home_paths.home_root,
    )

    memory_config = service._get_manager_config("memory")
    memory_api = bridge_module.create_memory_api(
        mode=service.mode,
        db_dir=db_dir,
        config=memory_config,
        vector_adapter=vector_adapter,
        telemetryctl=service._telemetryctl,
        agent_id=str(default_profile.name or default_agent_id),
    )
    resolved_action_policy = (
        default_profile.action_policy
        if default_profile.action_policy is not None
        else config.action_policy
    )
    policy_api = bridge_module.create_policy_api(
        mode=service.mode,
        db_dir=db_dir,
        policy_service=service._action_policy_service,
        action_policy_config=resolved_action_policy,
    )

    safety_api = bridge_module.create_safety_api(mode=service.mode)

    retrieve_api = bridge_module.init_retrieve_adapter(
        mode=service.mode,
        home_root=service._context.home_paths.home_root,
        vector_adapter=vector_adapter,
        config=service._get_manager_config("retrieve"),
        logger=service._logger,
        retrieve_service=service._retrieve_service,
        telemetryctl=service._telemetryctl,
    )

    skill_api = bridge_module.create_skill_api(
        mode=service.mode,
        db_dir=db_dir,
        home_root=service._context.home_paths.home_root,
        config=skill_config,
        telemetryctl=service._telemetryctl,
    )
    rlm_api = bridge_module.init_rlm_adapter(
        mode=service.mode,
        config=config,
        session_api=session_api,
        context_api=context_api,
        llm_api=llm_api,
        memory_api=memory_api,
        skill_api=skill_api,
        retrieve_api=retrieve_api,
        logger=service._logger,
    )

    compress_api = bridge_module.create_compress_api(
        mode=service.mode,
        db_dir=db_dir,
        telemetryctl=service._telemetryctl,
    )
    tool_api = bridge_module.create_tool_api(
        mode=service.mode,
        workspace_root=service._context.workspace_root,
        runtime_config=config.runtime,
        runtime_registry=service._tools,
        agent_name=default_profile.name or default_agent_id,
        skill_api=skill_api,
        agent_profile=default_profile,
    )

    service._validate_adapter_contracts(
        session_api=session_api,
        context_api=context_api,
        llm_api=llm_api,
        tool_api=tool_api,
        a2a_api=a2a_api,
        memory_api=memory_api,
        policy_api=policy_api,
        safety_api=safety_api,
        rlm_api=rlm_api,
        retrieve_api=retrieve_api,
    )

    # route runner-metadata derivation through the canonical
    llm_profiles = resolve_llm_profiles(
        config,
        override_value=service._resolve_override_value,
    )
    budgets = resolve_agent_budgets(
        config,
        override_value=service._resolve_override_value,
    )

    pre_resolved_brain_config = service._resolve_brain_config()
    profile_pae_config = (
        pre_resolved_brain_config.proactive_autonomous_entrypoint.model_copy(deep=True)
        if pre_resolved_brain_config is not None
        and getattr(
            pre_resolved_brain_config,
            "proactive_autonomous_entrypoint",
            None,
        )
        is not None
        else None
    )
    profile_afe_config = (
        pre_resolved_brain_config.auto_fact_extraction.model_copy(deep=True)
        if pre_resolved_brain_config is not None
        and getattr(pre_resolved_brain_config, "auto_fact_extraction", None) is not None
        else None
    )
    profile_aib_config = (
        pre_resolved_brain_config.adaptive_budget.model_copy(deep=True)
        if pre_resolved_brain_config is not None
        and getattr(pre_resolved_brain_config, "adaptive_budget", None) is not None
        else None
    )

    from openminion.services.brain.service import _runtime_mode_config_from_agent

    profile_kwargs: dict[str, Any] = dict(
        agent_id=default_profile.name or default_agent_id,
        role="general",
        thinking=str(default_profile.thinking or "") or "minimal",
        llm_profiles=llm_profiles,
        default_act_profile=str(default_profile.default_act_profile or "") or None,
        skill=default_profile.skill,
        skill_catalog=list(default_profile.skill_catalog or []),
        budgets=budgets,
        model_capability_overrides=dict(
            getattr(default_profile, "model_capability_overrides", {}) or {}
        ),
        mode_config=_runtime_mode_config_from_agent(config),
    )
    if pre_resolved_brain_config is not None:
        profile_kwargs.update(
            tool_policy=pre_resolved_brain_config.tool_policy,
            memory_read_scopes=list(pre_resolved_brain_config.memory_read_scopes),
            memory_write_scopes=dict(pre_resolved_brain_config.memory_write_scopes),
            max_skills_per_session=int(
                pre_resolved_brain_config.max_skills_per_session
            ),
            outcome_attribution=pre_resolved_brain_config.outcome_attribution.model_copy(
                deep=True
            ),
            success_memory=pre_resolved_brain_config.success_memory.model_copy(
                deep=True
            ),
        )
    if profile_pae_config is not None:
        profile_kwargs["proactive_autonomous_entrypoint"] = profile_pae_config
    if profile_afe_config is not None:
        profile_kwargs["auto_fact_extraction"] = profile_afe_config
    if profile_aib_config is not None:
        profile_kwargs["adaptive_budget"] = profile_aib_config
    profile = AgentProfile(**profile_kwargs)

    options = resolve_runner_options(
        config,
        brain_config=pre_resolved_brain_config,
        override_value=service._resolve_override_value,
        logger=service._logger,
    )

    cron_repository = create_sqlite_cron_repository(db_path=service.db_path)
    runner = bridge_module.BrainRunner(
        profile=profile,
        session_api=session_api,
        context_api=context_api,
        llm_api=llm_api,
        tool_api=tool_api,
        a2a_api=a2a_api,
        memory_api=memory_api,
        policy_api=policy_api,
        meta_api=None,
        skill_api=skill_api,
        retrieve_api=retrieve_api,
        rlm_api=rlm_api,
        compress_api=compress_api,
        telemetryctl=service._telemetryctl,
        task_manager=TaskManager.from_cron_repository(cron_repository),
        cron_api=cron_repository,
        options=options,
    )
    brain_runtime_db_path = resolve_brain_runtime_db_path(
        storage_path=_Path(service.db_path)
    )
    goal_store = SQLiteGoalStore(str(brain_runtime_db_path))
    mission_store = SQLiteMissionStateStore(str(brain_runtime_db_path))
    runner.goal_runtime = LongRunningGoalRuntime(
        goal_store=goal_store,
        mission_store=mission_store,
        checkpoint_manager=CheckpointManager(task_service=runner.task_manager),
    )
    runner._self_improvement_engine = service._self_improvement  # noqa: SLF001
    runner._configured_agent_ids = configured_agent_ids(config)  # noqa: SLF001
    service._validate_runner_contract(runner)
    service._llm_wrapper = service._resolve_llm_wrapper(llm_api)
    return runner


def build_agent_runtime_service(
    *,
    config: OpenMinionConfig,
    plugins: PluginRegistry,
    provider: object,
    llm_runtime: object | None = None,
    logger: logging.Logger,
    tools: ToolRegistry,
    security_policy: SecurityPolicyEngine | None,
    self_improvement: SelfImprovementEngine,
    storage_path: Path,
    home_root: Path,
    data_root: Path,
    config_path: Path,
    config_manager: ConfigManager | None = None,
    retrieve_service: Any | None = None,
    action_policy_service: Any | None = None,
) -> tuple[object, str, str]:
    from openminion.modules.brain.paths import resolve_brain_sessions_db_path

    fallback_reason = ""
    try:
        from openminion.services.brain.service import BrainBridgeService
    except Exception as exc:  # noqa: BLE001
        fallback_reason = str(exc)
        logger.error("Brain runtime mode failed. Error: %s", exc)
        raise RuntimeBootstrapError(f"Brain runtime mode failed. Error: {exc}") from exc

    brain_storage_path = resolve_brain_sessions_db_path(storage_path=storage_path)
    return (
        BrainBridgeService(
            config=config,
            plugins=plugins,
            provider=provider,
            llm_runtime=llm_runtime,
            logger=logger,
            tools=tools,
            security_policy=security_policy,
            self_improvement=self_improvement,
            db_path=str(brain_storage_path),
            home_root=home_root,
            data_root=data_root,
            config_path=config_path,
            config_manager=config_manager,
            retrieve_service=retrieve_service,
            action_policy_service=action_policy_service,
        ),
        "brain",
        fallback_reason,
    )
