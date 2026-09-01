from pathlib import Path
from typing import Any, cast

from openminion.base.config import ConfigManager, OpenMinionConfig
from openminion.base.config.manager import ConfigManagerError
from openminion.base.config.env import EnvironmentConfig, resolve_environment_config

from openminion.modules.memory.backends import (
    BuiltinKnowledgeBackend,
    KnowledgeBackend,
    NoneKnowledgeBackend,
    register_backend_factory,
)
from openminion.modules.memory.backends.external import resolve_external_backend
from openminion.modules.memory.config import (
    ConfigError,
    from_base_config,
    merge_candidate_learning_config,
    merge_ranking_config,
)
from openminion.modules.memory.service import MemoryService


def register_memory_backend_factories(
    *, audited_store: Any, vector_adapter: Any | None = None
) -> None:
    def build_sophiagraph_backend(**kwargs: Any) -> KnowledgeBackend:
        portability_service = MemoryService(store=audited_store)
        return BuiltinKnowledgeBackend(
            audited_store,
            export_snapshot_fn=portability_service.export_bundle_snapshot,
            import_snapshot_fn=portability_service.import_bundle_snapshot,
            vector_adapter=vector_adapter,
        )

    def build_none_backend(**kwargs: Any) -> KnowledgeBackend:
        return NoneKnowledgeBackend()

    def build_external_backend(**kwargs: Any) -> KnowledgeBackend:
        config = kwargs.get("config")
        provider = getattr(config, "external_adapter", None) or "<unset>"
        backend, _report = resolve_external_backend(
            adapter=str(provider),
            config=config,
            strict=True,
        )
        return cast(KnowledgeBackend, backend)

    register_backend_factory("sophiagraph", build_sophiagraph_backend)
    register_backend_factory("none", build_none_backend)
    register_backend_factory("external", build_external_backend)


def configure_memory_service_runtime(
    *,
    service: MemoryService,
    memory_config: Any,
    retrieve_ctl: Any | None,
    ranking_config: Any,
    candidate_learning_config: Any,
) -> None:
    service.set_candidate_learning_config(candidate_learning_config)
    retention_config = (
        memory_config.get("retention")
        if isinstance(memory_config, dict)
        else getattr(memory_config, "retention", None)
    )
    if retention_config is not None:
        service.set_tiering_config(retention_config)
    if retrieve_ctl is not None:
        retrieve_ctl.set_ranking_config(ranking_config)


def session_summary_max_chars(memory_config: Any) -> int:
    return int(
        getattr(
            getattr(memory_config, "retention", None),
            "session_summary_max_chars",
            500,
        )
    )


def session_handoff_max_summaries(memory_config: Any) -> int:
    return int(
        getattr(
            getattr(memory_config, "retrieval", None),
            "session_handoff_max_summaries",
            5,
        )
    )


def merged_ranking_config(*, memory_config: Any, retrieve_ctl: Any | None) -> Any:
    retrieve_defaults = getattr(getattr(retrieve_ctl, "config", None), "defaults", None)
    return merge_ranking_config(
        getattr(memory_config, "ranking", None),
        retrieval=getattr(memory_config, "retrieval", None),
        retrieve_defaults=retrieve_defaults,
    )


def merged_candidate_learning_config(*, memory_config: Any) -> Any:
    return merge_candidate_learning_config(
        getattr(memory_config, "candidate_learning", None),
        promotion=getattr(memory_config, "promotion", None),
    )


def normalize_runtime_memory_provider(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"", "memory_v2"}:
        return "memory_v2"
    if normalized in {"memory_v2_smoke", "memory_v2_hello_world"}:
        return "memory_v2_smoke"
    raise ConfigError(
        "Unsupported runtime.memory_provider="
        f"{value!r}. Supported providers: memory_v2, memory_v2_smoke "
        "(memory_v2_hello_world is a legacy alias)."
    )


__all__ = [
    "configure_memory_service_runtime",
    "merged_candidate_learning_config",
    "merged_ranking_config",
    "normalize_runtime_memory_provider",
    "register_memory_backend_factories",
    "resolve_runtime_env_override",
    "resolve_runtime_memory_config",
    "session_handoff_max_summaries",
    "session_summary_max_chars",
]


def resolve_runtime_memory_config(
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
        except ConfigManagerError:
            pass
    if home_root is not None and data_root is not None:
        return from_base_config(
            base_config=config,
            home_root=home_root,
            data_root=data_root,
        )
    return {
        "store": {
            "backend": "sqlite",
            "sqlite_path": str((memory_root / "memory.db").resolve(strict=False)),
            "sqlite": {
                "wal_mode": True,
                "busy_timeout_ms": 5000,
                "fts5_enabled": True,
            },
        }
    }


def resolve_runtime_env_override(
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
    env = resolve_environment_config(runtime_env=runtime_env)
    return str(env.get(key, "") or "").strip()
