from pathlib import Path
from typing import Any, Callable

from openminion.base.config import ConfigManager, OpenMinionConfig
from openminion.base.config.env import EnvironmentConfig
from openminion.modules.artifact.refs import create_default_artifactctl
from openminion.modules.brain.paths import resolve_brain_sessions_db_path
from openminion.modules.memory.backends import (
    BuiltinKnowledgeBackend,
    KnowledgeBackend,
    NoneKnowledgeBackend,
    instantiate_backend,
    register_backend_factory,
    resolve_backend_config,
)
from openminion.modules.memory.backends.external import resolve_external_backend
from openminion.modules.memory.config import (
    from_base_config as memory_from_base_config,
    merge_candidate_learning_config as merge_memory_candidate_learning_config,
    merge_ranking_config as merge_memory_ranking_config,
)
from openminion.modules.memory.service import MemoryService
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
from openminion.services.config import resolve_services_env
from openminion.services.constants import SERVICES_PROJECT_ID_ENV
from openminion.services.context.session import SessionContextService


def _resolve_runtime_memory_config(
    *,
    config: OpenMinionConfig,
    memory_root: Path,
    config_manager: ConfigManager | None = None,
    home_root: Path | None = None,
    data_root: Path | None = None,
) -> Any:
    if config_manager is not None:
        try:
            return config_manager.get("memory")
        except Exception:
            pass

    if home_root is not None and data_root is not None:
        return memory_from_base_config(
            base_config=config,
            home_root=home_root,
            data_root=data_root,
        )

    return {
        "store": {
            "backend": "sqlite",
            "sqlite_path": str(
                (memory_root / SERVICES_MEMORY_DB_FILENAME).resolve(strict=False)
            ),
            "sqlite": {
                "wal_mode": True,
                "busy_timeout_ms": 5000,
                "fts5_enabled": True,
            },
        }
    }


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
    adapter_cls: type[Any] = MemoryServiceGatewayAdapter,
    resolve_runtime_memory_config_fn: Callable[
        ..., Any
    ] = _resolve_runtime_memory_config,
    artifactctl_factory: Callable[[], Any] = create_default_artifactctl,
) -> MemoryServiceGatewayAdapter:
    memory_config = resolve_runtime_memory_config_fn(
        config=config,
        memory_root=memory_root,
        config_manager=config_manager,
        home_root=home_root,
        data_root=data_root,
    )
    backend_config = resolve_backend_config(memory_config)
    if backend_config.provider == "none":
        return DisabledMemoryGatewayAdapter(agent_id=agent_id, logger=logger)
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
    ranking_config = _merge_ranking_config(
        memory_config=memory_config,
        retrieve_ctl=retrieve_ctl,
    )
    candidate_learning_config = _merge_candidate_learning_config(
        memory_config=memory_config
    )
    _register_memory_backend_factories(audited_store=audited_store)
    backend = instantiate_backend(config=backend_config)
    service = MemoryService(backend=backend, ranking_config=ranking_config)
    _configure_memory_service_runtime(
        service=service,
        memory_config=memory_config,
        retrieve_ctl=retrieve_ctl,
        ranking_config=ranking_config,
        candidate_learning_config=candidate_learning_config,
    )
    return adapter_cls(
        service,
        agent_id=agent_id,
        project_id=_resolve_project_id(config_manager=config_manager, config=config),
        session_context=session_context,
        logger=logger.getChild("v2_adapter"),
        retrieval_max_chars=int(getattr(config.runtime, "memory_retrieval_max_chars", 2000)),
        log_retention_days=int(getattr(config.runtime, "memory_log_retention_days", 30)),
        patch_retention_count=int(getattr(config.runtime, "memory_patch_retention_count", 200)),
        max_facts=int(getattr(config.runtime, "memory_max_facts", 200)),
        max_todos=int(getattr(config.runtime, "memory_max_todos", 200)),
        session_summary_max_chars=_session_summary_max_chars(memory_config),
        session_handoff_max_summaries=_session_handoff_max_summaries(memory_config),
        memory_config=memory_config,
        retrieve_ctl=retrieve_ctl,
        ranking_config=ranking_config,
        candidate_learning_config=candidate_learning_config,
        brain_sessions_db_path=(
            resolve_brain_sessions_db_path(storage_path=storage_path)
            if storage_path is not None
            else None
        ),
    )


def _register_memory_backend_factories(*, audited_store: Any) -> None:
    def _build_sophiagraph_backend(**kwargs: Any) -> KnowledgeBackend:
        return BuiltinKnowledgeBackend(
            audited_store,
            export_snapshot_fn=_export_bundle_snapshot_placeholder,
            import_snapshot_fn=_import_bundle_snapshot_placeholder,
        )

    def _build_none_backend(**kwargs: Any) -> KnowledgeBackend:
        return NoneKnowledgeBackend()

    def _build_external_backend(**kwargs: Any) -> KnowledgeBackend:
        config = kwargs.get("config")
        provider = getattr(config, "external_adapter", None) or "<unset>"
        backend, _report = resolve_external_backend(
            adapter=str(provider),
            config=config,
            strict=True,
        )
        return backend

    register_backend_factory("sophiagraph", _build_sophiagraph_backend)
    register_backend_factory("none", _build_none_backend)
    register_backend_factory("external", _build_external_backend)


def _export_bundle_snapshot_placeholder(options: Any) -> Any:
    raise NotImplementedError(
        "builtin sophiagraph backend export_snapshot wiring lands in a later KCE slice"
    )


def _import_bundle_snapshot_placeholder(snapshot: Any, options: Any) -> Any:
    raise NotImplementedError(
        "builtin sophiagraph backend import_snapshot wiring lands in a later KCE slice"
    )


def _configure_memory_service_runtime(
    *,
    service: MemoryService,
    memory_config: Any,
    retrieve_ctl: Any | None,
    ranking_config: Any,
    candidate_learning_config: Any,
) -> None:
    if hasattr(service, "set_candidate_learning_config"):
        try:
            service.set_candidate_learning_config(candidate_learning_config)
        except Exception:
            pass
    retention_config = None
    if isinstance(memory_config, dict):
        retention_config = memory_config.get("retention")
    else:
        retention_config = getattr(memory_config, "retention", None)
    if retention_config is not None and hasattr(service, "set_tiering_config"):
        try:
            service.set_tiering_config(retention_config)
        except Exception:
            pass
    if retrieve_ctl is not None and hasattr(retrieve_ctl, "set_ranking_config"):
        try:
            retrieve_ctl.set_ranking_config(ranking_config)
        except Exception:
            pass


def _resolve_project_id(
    *,
    config_manager: ConfigManager | None,
    config: OpenMinionConfig,
) -> str | None:
    return (
        _resolve_env_override(
            config_manager=config_manager,
            config=config,
            key=SERVICES_PROJECT_ID_ENV,
        )
        or None
    )


def _session_summary_max_chars(memory_config: Any) -> int:
    return int(
        getattr(
            getattr(memory_config, "retention", None),
            "session_summary_max_chars",
            500,
        )
    )


def _session_handoff_max_summaries(memory_config: Any) -> int:
    return int(
        getattr(
            getattr(memory_config, "retrieval", None),
            "session_handoff_max_summaries",
            5,
        )
    )


def _merge_ranking_config(
    *,
    memory_config: Any,
    retrieve_ctl: Any | None,
) -> Any:
    retrieve_defaults = getattr(getattr(retrieve_ctl, "config", None), "defaults", None)
    return merge_memory_ranking_config(
        getattr(memory_config, "ranking", None),
        retrieval=getattr(memory_config, "retrieval", None),
        retrieve_defaults=retrieve_defaults,
    )


def _merge_candidate_learning_config(*, memory_config: Any) -> Any:
    return merge_memory_candidate_learning_config(
        getattr(memory_config, "candidate_learning", None),
        promotion=getattr(memory_config, "promotion", None),
    )


def _resolve_env_override(
    *,
    config_manager: ConfigManager | None,
    config: OpenMinionConfig,
    key: str,
) -> str:
    if config_manager is not None and isinstance(config_manager.env, EnvironmentConfig):
        return str(config_manager.env.get(key, "") or "").strip()
    runtime_env = getattr(getattr(config, "runtime", None), "env", {})
    if not isinstance(runtime_env, dict):
        runtime_env = {}
    env = resolve_services_env(runtime_env=runtime_env)
    return str(env.get(key, "") or "").strip()


def _normalize_runtime_memory_provider(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"", "memory_v2"}:
        return "memory_v2"
    if normalized in {"memory_v2_smoke", "memory_v2_hello_world"}:
        return "memory_v2_smoke"
    raise ValueError(
        "Unsupported runtime.memory_provider="
        f"{value!r}. Supported providers: memory_v2, memory_v2_smoke (memory_v2_hello_world is a legacy alias)."
    )
