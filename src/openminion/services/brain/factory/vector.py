from pathlib import Path
from typing import Any

from openminion.services.bootstrap.paths import SERVICES_MEMORY_DB_FILENAME


def _resolve_vector_config(config: Any) -> tuple[bool, Any | None]:
    vector_cfg = getattr(config, "vector", None)
    if vector_cfg is not None:
        return bool(getattr(vector_cfg, "enabled", False)), vector_cfg
    extra = getattr(config, "extra", None)
    legacy_config = extra.get("vector") if isinstance(extra, dict) else None
    enabled = legacy_config.get("enabled") if isinstance(legacy_config, dict) else False
    return bool(enabled), None


def init_vector_adapter(
    *,
    config: Any,
    db_dir: Path,
    logger: Any,
) -> tuple[Any | None, Any | None]:
    enabled, vector_cfg = _resolve_vector_config(config)
    if not enabled:
        return None, None

    from openminion.modules.storage.runtime.vector_sync import VectorSyncScheduler

    vector_adapter = _build_vector_adapter(
        config=config,
        vector_cfg=vector_cfg,
        db_dir=db_dir,
        logger=logger,
    )
    vector_sync = VectorSyncScheduler(vector_adapter=vector_adapter)
    return vector_adapter, vector_sync


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
        batch_size=getattr(vector_cfg, "sync_batch_size", 32) if vector_cfg else 32,
    )


def _build_embedding_provider(
    *,
    vector_cfg: Any | None,
    dimension: int,
    logger: Any,
) -> Any:
    from openminion.modules.storage.runtime.vector_index import LocalEmbeddingProvider

    provider_type = getattr(vector_cfg, "provider", "local") if vector_cfg else "local"
    if provider_type != "local":
        raise ValueError("vector.provider must be 'local'")
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
    from openminion.modules.storage.runtime.vector_index import SQLiteVecBackend

    backend_type = getattr(vector_cfg, "backend", "sqlite") if vector_cfg else "sqlite"
    if backend_type != "sqlite":
        raise ValueError("vector.backend must be 'sqlite'")
    logger.info("Vector backend enabled: backend=sqlite, dimension=%d", dimension)
    return SQLiteVecBackend(db_path=str(db_dir / "vectors.db"), dimension=dimension)
