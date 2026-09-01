from pathlib import Path
from typing import Any, Callable, cast

from openminion.base.config import ConfigManager, OpenMinionConfig
from openminion.modules.artifact.refs import create_default_artifactctl
from openminion.modules.brain.paths import resolve_brain_sessions_db_path
from openminion.modules.memory.backends import (
    instantiate_backend,
    resolve_backend_config,
)
from openminion.modules.memory import (
    RuntimeMemoryAssembly,
    RuntimeMemoryScheduler,
    SophiagraphRecallAdapter,
    memory_runtime_configuration,
)
from openminion.modules.memory.smoke import EphemeralMemorySmokeProvider
from openminion.modules.memory.service import MemoryService
from openminion.modules.brain.adapters.memory.runtime import MemctlAdapter
from openminion.modules.memory.storage import (
    AuditedMemoryStore,
    SQLiteMemoryAuditSink,
    default_memory_audit_db_path,
)
from openminion.modules.memory.storage.factory import resolve_memory_backend
from openminion.services.agent.memory.gateway_adapter import (
    DisabledMemoryGatewayAdapter,
    MemoryServiceGatewayAdapter,
)
from openminion.services.bootstrap.paths import SERVICES_MEMORY_DB_FILENAME
from openminion.services.constants import SERVICES_PROJECT_ID_ENV
from openminion.services.context.session import SessionContextService


def active_runtime_memory_assembly(
    *,
    gateway: Any,
    service: MemoryService,
    agent_id: str,
    vector_adapter: Any | None = None,
    scheduler: RuntimeMemoryScheduler | None = None,
) -> RuntimeMemoryAssembly:
    return RuntimeMemoryAssembly(
        gateway=gateway,
        service=service,
        memctl=MemctlAdapter(service, agent_id=agent_id, owns_backend=False),
        vector_adapter=vector_adapter,
        scheduler=scheduler,
    )


def build_runtime_memory_assembly(
    *,
    config: OpenMinionConfig,
    agent_id: str,
    memory_root: Path,
    logger: Any,
    config_manager: ConfigManager | None = None,
    home_root: Path | None = None,
    data_root: Path | None = None,
    session_context: SessionContextService | None = None,
    retrieve_ctl: Any | None = None,
    storage_path: Path | None = None,
    vector_adapter: Any | None = None,
    scheduler: RuntimeMemoryScheduler | None = None,
) -> RuntimeMemoryAssembly:
    configured_provider = (
        memory_runtime_configuration.resolve_runtime_env_override(
            config_manager=config_manager,
            config=config,
            key="OPENMINION_MEMORY_PROVIDER",
        )
        or str(getattr(config.runtime, "memory_provider", "memory_v2")).strip()
    )
    normalized_provider = (
        memory_runtime_configuration.normalize_runtime_memory_provider(
            configured_provider
        )
    )
    if normalized_provider == "memory_v2_smoke":
        return RuntimeMemoryAssembly(
            gateway=EphemeralMemorySmokeProvider(
                agent_id=agent_id,
                logger=logger,
                enabled=bool(config.runtime.memory_enabled),
            )
        )
    if not bool(config.runtime.memory_enabled):
        return RuntimeMemoryAssembly(
            gateway=DisabledMemoryGatewayAdapter(agent_id=agent_id, logger=logger)
        )
    return build_memory_v2_runtime_assembly(
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
        vector_adapter=vector_adapter,
        scheduler=scheduler,
    )


def _build_memory_v2_gateway_adapter(
    *,
    config: OpenMinionConfig,
    agent_id: str,
    memory_root: Path,
    logger: Any,
    config_manager: ConfigManager | None,
    home_root: Path | None,
    data_root: Path | None,
    session_context: SessionContextService | None,
    retrieve_ctl: Any | None,
    storage_path: Path | None,
    adapter_cls: type[MemoryServiceGatewayAdapter] = MemoryServiceGatewayAdapter,
    resolve_runtime_memory_config_fn: Callable[
        ..., Any
    ] = memory_runtime_configuration.resolve_runtime_memory_config,
    artifactctl_factory: Callable[[], Any] = create_default_artifactctl,
) -> MemoryServiceGatewayAdapter:
    assembly = build_memory_v2_runtime_assembly(
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
        adapter_cls=adapter_cls,
        resolve_runtime_memory_config_fn=resolve_runtime_memory_config_fn,
        artifactctl_factory=artifactctl_factory,
    )
    return cast(MemoryServiceGatewayAdapter, assembly.gateway)


def _build_memory_v2_gateway(
    *,
    adapter_cls: type[MemoryServiceGatewayAdapter],
    service: MemoryService,
    agent_id: str,
    config: OpenMinionConfig,
    config_manager: ConfigManager | None,
    session_context: SessionContextService | None,
    logger: Any,
    retrieve_ctl: Any | None,
    memory_config: Any,
    ranking_config: Any,
    candidate_learning_config: Any,
    backend: Any,
    backend_provider: str,
    storage_path: Path | None,
    vector_adapter: Any | None,
) -> MemoryServiceGatewayAdapter:
    recall_adapter = None
    if backend_provider == "sophiagraph":
        recall_adapter = SophiagraphRecallAdapter(
            backend=backend,
            minimum_confidence=float(
                getattr(ranking_config, "minimum_confidence", 0.0) or 0.0
            ),
            vector_adapter=vector_adapter,
        )
    return adapter_cls(
        service,
        agent_id=agent_id,
        project_id=_resolve_project_id(config_manager=config_manager, config=config),
        session_context=session_context,
        logger=logger.getChild("v2_adapter"),
        retrieval_max_chars=int(
            getattr(config.runtime, "memory_retrieval_max_chars", 2000)
        ),
        log_retention_days=int(
            getattr(config.runtime, "memory_log_retention_days", 30)
        ),
        patch_retention_count=int(
            getattr(config.runtime, "memory_patch_retention_count", 200)
        ),
        max_facts=int(getattr(config.runtime, "memory_max_facts", 200)),
        max_todos=int(getattr(config.runtime, "memory_max_todos", 200)),
        session_summary_max_chars=(
            memory_runtime_configuration.session_summary_max_chars(memory_config)
        ),
        session_handoff_max_summaries=(
            memory_runtime_configuration.session_handoff_max_summaries(memory_config)
        ),
        memory_config=memory_config,
        retrieve_ctl=retrieve_ctl,
        ranking_config=ranking_config,
        candidate_learning_config=candidate_learning_config,
        recall_adapter=recall_adapter,
        brain_sessions_db_path=(
            resolve_brain_sessions_db_path(storage_path=storage_path)
            if storage_path is not None
            else None
        ),
    )


def build_memory_v2_runtime_assembly(
    *,
    config: OpenMinionConfig,
    agent_id: str,
    memory_root: Path,
    logger: Any,
    config_manager: ConfigManager | None,
    home_root: Path | None,
    data_root: Path | None,
    session_context: SessionContextService | None,
    retrieve_ctl: Any | None,
    storage_path: Path | None,
    vector_adapter: Any | None = None,
    scheduler: RuntimeMemoryScheduler | None = None,
    adapter_cls: type[MemoryServiceGatewayAdapter] = MemoryServiceGatewayAdapter,
    resolve_runtime_memory_config_fn: Callable[
        ..., Any
    ] = memory_runtime_configuration.resolve_runtime_memory_config,
    artifactctl_factory: Callable[[], Any] = create_default_artifactctl,
) -> RuntimeMemoryAssembly:
    memory_config = resolve_runtime_memory_config_fn(
        config=config,
        memory_root=memory_root,
        config_manager=config_manager,
        home_root=home_root,
        data_root=data_root,
    )
    backend_config = resolve_backend_config(memory_config)
    if backend_config.provider == "none":
        adapter = DisabledMemoryGatewayAdapter(agent_id=agent_id, logger=logger)
        adapter.disabled_reason = "backend_none"
        return RuntimeMemoryAssembly(gateway=adapter)
    db_path = memory_root / SERVICES_MEMORY_DB_FILENAME
    try:
        artifactctl = artifactctl_factory()
    except Exception:
        artifactctl = None
    resolved = resolve_memory_backend(
        config=memory_config,
        db_path=db_path,
        artifactctl=artifactctl,
    )
    audited_store = AuditedMemoryStore(
        resolved.store,
        sink=SQLiteMemoryAuditSink(default_memory_audit_db_path(db_path)),
    )
    ranking_config = memory_runtime_configuration.merged_ranking_config(
        memory_config=memory_config,
        retrieve_ctl=retrieve_ctl,
    )
    candidate_learning_config = (
        memory_runtime_configuration.merged_candidate_learning_config(
            memory_config=memory_config
        )
    )
    memory_runtime_configuration.register_memory_backend_factories(
        audited_store=audited_store,
        vector_adapter=vector_adapter,
    )
    backend = instantiate_backend(config=backend_config)
    service = MemoryService(
        backend=backend,
        ranking_config=ranking_config,
        vector_adapter=vector_adapter,
        owns_store=True,
    )
    memory_runtime_configuration.configure_memory_service_runtime(
        service=service,
        memory_config=memory_config,
        retrieve_ctl=retrieve_ctl,
        ranking_config=ranking_config,
        candidate_learning_config=candidate_learning_config,
    )
    gateway = _build_memory_v2_gateway(
        adapter_cls=adapter_cls,
        service=service,
        agent_id=agent_id,
        config=config,
        config_manager=config_manager,
        session_context=session_context,
        logger=logger,
        retrieve_ctl=retrieve_ctl,
        memory_config=memory_config,
        ranking_config=ranking_config,
        candidate_learning_config=candidate_learning_config,
        backend=backend,
        backend_provider=backend_config.provider,
        storage_path=storage_path,
        vector_adapter=vector_adapter,
    )
    return active_runtime_memory_assembly(
        gateway=gateway,
        service=service,
        agent_id=agent_id,
        vector_adapter=vector_adapter,
        scheduler=scheduler,
    )


def _resolve_project_id(
    *,
    config_manager: ConfigManager | None,
    config: OpenMinionConfig,
) -> str | None:
    return (
        memory_runtime_configuration.resolve_runtime_env_override(
            config_manager=config_manager,
            config=config,
            key=SERVICES_PROJECT_ID_ENV,
        )
        or None
    )
