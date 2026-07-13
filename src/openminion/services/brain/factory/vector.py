from pathlib import Path
from typing import Any

from openminion.base.config.core import resolve_default_agent_id
from openminion.base.config.env import resolve_environment_config
from openminion.services.bootstrap.paths import SERVICES_MEMORY_DB_FILENAME


def _resolve_vector_config(config: Any) -> tuple[bool, Any | None]:
    enabled = False
    vector_cfg = None
    try:
        vector_cfg = getattr(config, "vector", None)
        if vector_cfg is not None:
            enabled = getattr(vector_cfg, "enabled", False)
        elif hasattr(config, "extra") and isinstance(config.extra, dict):
            enabled = config.extra.get("vector", {}).get("enabled", False)
    except Exception:
        enabled = False
    return bool(enabled), vector_cfg


def init_vector_adapter(
    *,
    config: Any,
    db_dir: Path,
    logger: Any,
) -> tuple[Any | None, Any | None]:
    enabled, vector_cfg = _resolve_vector_config(config)
    if not enabled:
        return None, None

    try:
        from openminion.modules.storage.runtime.vector_sync import VectorSyncScheduler

        vector_adapter = _build_vector_adapter(
            config=config,
            vector_cfg=vector_cfg,
            db_dir=db_dir,
            logger=logger,
        )
        vector_sync = VectorSyncScheduler(
            vector_adapter=vector_adapter,
            batch_size=getattr(vector_cfg, "sync_batch_size", 32) if vector_cfg else 32,
        )
        vector_sync.start()
        logger.info("Vector sync scheduler started")

        return vector_adapter, vector_sync
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Vector adapter initialization failed, continuing without vectors: %s",
            exc,
        )
        return None, None


def _build_vector_adapter(
    *,
    config: Any,
    vector_cfg: Any | None,
    db_dir: Path,
    logger: Any,
) -> Any:
    from openminion.modules.storage.runtime.vector_index import (
        create_vector_index_adapter,
    )

    dimension = getattr(vector_cfg, "dimension", 384) if vector_cfg else 384
    return create_vector_index_adapter(
        db_path=str(db_dir / SERVICES_MEMORY_DB_FILENAME),
        embedding_provider=_build_embedding_provider(
            vector_cfg=vector_cfg,
            dimension=dimension,
            logger=logger,
        ),
        vector_index=_build_vector_backend(
            config=config,
            vector_cfg=vector_cfg,
            db_dir=db_dir,
            dimension=dimension,
            logger=logger,
        ),
    )


def _build_embedding_provider(
    *,
    vector_cfg: Any | None,
    dimension: int,
    logger: Any,
) -> Any:
    from openminion.modules.storage.runtime.vector_index import (
        APIEmbeddingProvider,
        LocalEmbeddingProvider,
    )

    provider_type = getattr(vector_cfg, "provider", "local") if vector_cfg else "local"
    if provider_type == "api":
        api_key = getattr(
            vector_cfg, "api_key", None
        ) or resolve_environment_config().get("EMBEDDING_API_KEY")
        if not api_key:
            raise ValueError(
                "vector.provider='api' requires api_key in config or EMBEDDING_API_KEY environment variable"
            )
        api_model = getattr(vector_cfg, "model", "text-embedding-3-small")
        api_base_url = getattr(vector_cfg, "base_url", "https://api.openai.com/v1")
        logger.info("Vector adapter enabled: provider=api, model=%s", api_model)
        return APIEmbeddingProvider(
            api_key=api_key, model=api_model, base_url=api_base_url
        )
    model_name = (
        getattr(vector_cfg, "model", "all-MiniLM-L6-v2")
        if vector_cfg
        else "all-MiniLM-L6-v2"
    )
    logger.info(
        "Vector adapter enabled: provider=local, model=%s, dimension=%d",
        model_name,
        dimension,
    )
    return LocalEmbeddingProvider(model=model_name, dimension=dimension)


def _build_vector_backend(
    *,
    config: Any,
    vector_cfg: Any | None,
    db_dir: Path,
    dimension: int,
    logger: Any,
) -> Any:
    from openminion.modules.storage.runtime.vector_index import (
        QdrantVectorBackend,
        SQLiteVecBackend,
    )

    backend_type = getattr(vector_cfg, "backend", "sqlite") if vector_cfg else "sqlite"
    if backend_type != "qdrant":
        logger.info("Vector backend enabled: backend=sqlite, dimension=%d", dimension)
        return SQLiteVecBackend(db_path=str(db_dir / "vectors.db"), dimension=dimension)
    qdrant_url = getattr(vector_cfg, "qdrant_url", "http://localhost:6333")
    qdrant_api_key = getattr(vector_cfg, "qdrant_api_key", None)
    try:
        agent_name = resolve_default_agent_id(config)
    except Exception:
        agent_name = "default"
    logger.info("Vector backend enabled: backend=qdrant, url=%s", qdrant_url)
    return QdrantVectorBackend(
        collection_name=f"openminion_vectors_{agent_name.replace('-', '_')}",
        url=qdrant_url,
        api_key=qdrant_api_key,
        dimension=dimension,
    )
