"""Storage, vector, and self-improvement config parsing helpers."""

from __future__ import annotations
from typing import Any
from openminion.base.config.base import DEFAULT_STORAGE_PATH
from openminion.base.config.core import OpenMinionConfig, StorageConfig, VectorConfig
from openminion.base.config.parse import (
    _as_bool,
    _as_int,
    _normalize_self_improvement_mode,
)
from openminion.base.config.runtime import SelfImprovementConfig
from .identity import (
    _build_context_config,
    _build_identity_config,
    _identity_context_to_payload,
)


def _parse_storage_config(
    storage_payload: dict[str, Any],
    *,
    storage_backend_env: str | None,
    storage_postgres_url_env: str | None,
    storage_postgres_pool_min_env: str | None,
    storage_postgres_pool_max_env: str | None,
) -> StorageConfig:
    return StorageConfig(
        path=str(storage_payload.get("path", str(DEFAULT_STORAGE_PATH))),
        backend=storage_backend_env or str(storage_payload.get("backend", "sqlite")),
        postgres_url=storage_postgres_url_env
        or str(storage_payload.get("postgres_url", "")),
        postgres_pool_min=int(storage_postgres_pool_min_env)
        if storage_postgres_pool_min_env
        else int(storage_payload.get("postgres_pool_min", 1)),
        postgres_pool_max=int(storage_postgres_pool_max_env)
        if storage_postgres_pool_max_env
        else int(storage_payload.get("postgres_pool_max", 5)),
    )


def _build_storage_context_sections(
    *,
    storage_payload: dict[str, Any],
    vector_payload: dict[str, Any],
    self_improvement_payload: dict[str, Any],
    context_payload: dict[str, Any],
    identity_payload: dict[str, Any],
    storage_backend_env: str | None,
    storage_postgres_url_env: str | None,
    storage_postgres_pool_min_env: str | None,
    storage_postgres_pool_max_env: str | None,
) -> dict[str, Any]:
    return {
        "storage": _parse_storage_config(
            storage_payload,
            storage_backend_env=storage_backend_env,
            storage_postgres_url_env=storage_postgres_url_env,
            storage_postgres_pool_min_env=storage_postgres_pool_min_env,
            storage_postgres_pool_max_env=storage_postgres_pool_max_env,
        ),
        "vector": VectorConfig(
            enabled=_as_bool(vector_payload.get("enabled"), False),
            provider=vector_payload.get("provider", "local"),
            model=vector_payload.get("model", "all-MiniLM-L6-v2"),
            dimension=int(vector_payload.get("dimension", 384)),
            sync_batch_size=int(vector_payload.get("sync_batch_size", 32)),
            search_k=int(vector_payload.get("search_k", 10)),
        ),
        "self_improvement": SelfImprovementConfig(
            enabled=_as_bool(self_improvement_payload.get("enabled"), True),
            notes_path=str(self_improvement_payload.get("notes_path", "")),
            application_mode=_normalize_self_improvement_mode(
                self_improvement_payload.get("application_mode")
            ),
            activation_threshold=max(
                1, _as_int(self_improvement_payload.get("activation_threshold"), 2)
            ),
            max_applied_notes=max(
                1, _as_int(self_improvement_payload.get("max_applied_notes"), 3)
            ),
            min_token_overlap=max(
                1, _as_int(self_improvement_payload.get("min_token_overlap"), 1)
            ),
            auto_capture_tool_failures=_as_bool(
                self_improvement_payload.get("auto_capture_tool_failures"), True
            ),
        ),
        "context": _build_context_config(context_payload),
        "identity": _build_identity_config(identity_payload),
    }


def _storage_context_to_payload(config: OpenMinionConfig) -> dict[str, Any]:
    return {
        "storage": {
            "path": config.storage.path,
            "backend": config.storage.backend,
            "postgres_url": config.storage.postgres_url,
            "postgres_pool_min": config.storage.postgres_pool_min,
            "postgres_pool_max": config.storage.postgres_pool_max,
        },
        "self_improvement": {
            "enabled": bool(config.self_improvement.enabled),
            "notes_path": config.self_improvement.notes_path,
            "application_mode": _normalize_self_improvement_mode(
                config.self_improvement.application_mode
            ),
            "activation_threshold": config.self_improvement.activation_threshold,
            "max_applied_notes": config.self_improvement.max_applied_notes,
            "min_token_overlap": config.self_improvement.min_token_overlap,
            "auto_capture_tool_failures": bool(
                config.self_improvement.auto_capture_tool_failures
            ),
        },
        **_identity_context_to_payload(config),
    }
