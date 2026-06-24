from __future__ import annotations

from pathlib import Path
from string import Template
from typing import Any, Mapping, MutableMapping, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field

from openminion.base.generated_paths import resolve_generated_config_path
from openminion.base.config.env import resolve_environment_config
from openminion.base.config.paths import ensure_under_data_root
from openminion.modules.config import (
    is_module_standalone_mode as _is_module_standalone_mode,
    normalize_data_root_relative_path as _normalize_module_data_root_relative_path,
    resolve_module_config_path,
    resolve_module_data_root,
    resolve_module_home_root,
)
from openminion.modules.storage.runtime.provider_selection import (
    resolve_storage_provider,
)
from .constants import (
    DEFAULT_CONFIG_FILENAME,
    DEFAULT_INTEGRATED_BLOB_ROOT,
    DEFAULT_INTEGRATED_SQLITE_PATH,
    DEFAULT_STANDALONE_ROOT,
    DEFAULT_STANDALONE_SQLITE_PATH,
    OPENMINION_DATA_ROOT_ENV,
    OPENMINION_MODULE_STANDALONE_ENV,
    RETRIEVECTL_CONFIG_ENV,
)


class ConfigError(ValueError):
    pass


class StorageConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = "sqlite"
    sqlite_path: Path
    blob_root: Path
    wal_mode: bool = True
    path_mode: str = "integrated_runtime"  # or "module_standalone", "explicit_override"
    path_source: str = "default_integrated"  # or "env_var", "config_file", "home_root"


class DefaultsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy: str = "contextual"
    contextual_enabled: bool = True
    embeddings_enabled: bool = False
    lexical_candidate_count: int = Field(default=50, ge=5, le=500)
    snippet_tokens: int = Field(default=320, ge=32, le=4000)
    chunk_target_tokens: int = Field(default=700, ge=16, le=4000)
    chunk_min_tokens: int = Field(default=300, ge=4, le=4000)
    chunk_max_tokens: int = Field(default=1200, ge=8, le=8000)
    doc_group_target_tokens: int = Field(default=4096, ge=16, le=32000)
    doc_group_min_tokens: int = Field(default=2000, ge=4, le=32000)
    doc_group_max_tokens: int = Field(default=8000, ge=8, le=32000)
    raptor_internal_k: int = Field(default=2, ge=1, le=32)
    raptor_leaf_k: int = Field(default=6, ge=1, le=64)
    raptor_inheritance_multiplier: float = Field(default=0.92, ge=0.0, le=1.0)
    title_identity_max_boost: float = Field(default=0.18, ge=0.0, le=1.0)
    verify_min_score: float = Field(default=0.15, ge=0.0, le=1.0)
    confidence_memory: float = Field(default=1.0, ge=0.0, le=1.0)
    confidence_default: float = Field(default=0.6, ge=0.0, le=1.0)
    candidate_overfetch_multiplier: int = Field(default=2, ge=1, le=10)
    recency_half_life_hours: int = Field(default=72, ge=1, le=24 * 365)
    decay_halflife_days: int = Field(default=30, ge=1, le=3650)
    recency_weight: float = Field(default=0.3, ge=0.0, le=1.0)
    k_conversational: int = Field(default=3, ge=1, le=64)
    k_knowledge: int = Field(default=3, ge=1, le=64)
    mmr_lambda: float = Field(default=0.6, ge=0.0, le=1.0)
    mmr_enabled: bool = True
    feedback_decay_halflife_days: int = Field(default=60, ge=1, le=3650)
    decay_min_feedback_score: float = Field(default=0.0, ge=0.0, le=1.0)


class RetrieveCtlConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    storage: StorageConfig
    defaults: DefaultsConfig = Field(default_factory=DefaultsConfig)


def resolve_home_root() -> Path:
    return (
        resolve_module_home_root(
            None,
            resolve_environment_config(),
            fallback_to_cwd=True,
        )
        or Path.cwd().resolve()
    )


def is_standalone_mode() -> bool:
    return _is_module_standalone_mode(resolve_environment_config())


def get_default_storage_paths(
    home_root: Optional[Path] = None,
    data_root: Optional[Path] = None,
) -> tuple[Path, Path]:
    if is_standalone_mode():
        return (
            Path.home() / DEFAULT_STANDALONE_SQLITE_PATH,
            Path.home() / DEFAULT_STANDALONE_ROOT,
        )

    root = home_root or resolve_home_root()
    resolved_data_root = resolve_module_data_root(
        home_root=root,
        env=resolve_environment_config(),
        data_root=data_root,
    )
    return (
        resolved_data_root / DEFAULT_INTEGRATED_SQLITE_PATH,
        resolved_data_root / DEFAULT_INTEGRATED_BLOB_ROOT,
    )


def resolve_default_config_path(*, home_root: Optional[Path] = None) -> Path:
    """Return the default retrievectl config path under the generated root."""
    return resolve_generated_config_path(DEFAULT_CONFIG_FILENAME, home_root=home_root)


def load_config(
    path_or_data: str | Path | dict[str, Any] | RetrieveCtlConfig | None = None,
    *,
    home_root: Optional[Path] = None,
    env: Mapping[str, str] | None = None,
) -> RetrieveCtlConfig:
    if isinstance(path_or_data, RetrieveCtlConfig):
        return path_or_data

    env_config = (
        resolve_environment_config(
            runtime_env=resolve_environment_config().snapshot(), process_env=env
        )
        if env is not None
        else resolve_environment_config()
    )
    env = env_config.snapshot()

    path_mode = "integrated_runtime"
    path_source = "default_integrated"

    resolved_home_root = home_root or resolve_module_home_root(
        None,
        env_config,
        fallback_to_cwd=True,
    )
    resolved_data_root = resolve_module_data_root(
        home_root=resolved_home_root,
        env=env_config,
        data_root=env.get(OPENMINION_DATA_ROOT_ENV),
    )

    if _is_module_standalone_mode(env_config):
        path_mode = "module_standalone"
        path_source = "env_standalone"
    elif home_root:
        path_source = "explicit_home_root"

    if path_or_data is None:
        resolved = resolve_default_config_path(home_root=home_root)
        if resolved.exists():
            payload = _load_yaml(resolved)
            path_source = "config_file"
        else:
            payload = {}
            path_source = "default_integrated"
    elif isinstance(path_or_data, dict):
        payload = dict(path_or_data)
        if path_source == "default_integrated":
            path_source = "inline_dict"
    else:
        resolved = _resolve_path(path_or_data, env=env)
        if resolved.exists():
            payload = _load_yaml(resolved)
            path_source = "config_file"
        else:
            payload = {}
            path_source = "default_integrated"

    version = int(payload.get("version", 1))
    section = payload.get("retrievectl")
    if not isinstance(section, MutableMapping):
        section = {}

    storage_raw = section.get("storage")
    if not isinstance(storage_raw, MutableMapping):
        storage_raw = {}

    default_sqlite, default_blob = get_default_storage_paths(
        resolved_home_root,
        resolved_data_root,
    )

    sqlite_path_str = storage_raw.get("sqlite_path")
    blob_root_str = storage_raw.get("blob_root")

    sqlite_path_is_absolute_override = False
    blob_root_is_absolute_override = False

    if sqlite_path_str:
        sqlite_path, sqlite_path_is_absolute_override = _resolve_storage_path(
            raw_value=sqlite_path_str,
            env=env,
            resolved_data_root=resolved_data_root,
            resolved_home_root=resolved_home_root,
        )
        path_source = "config_file"
    else:
        sqlite_path = default_sqlite

    if blob_root_str:
        blob_root, blob_root_is_absolute_override = _resolve_storage_path(
            raw_value=blob_root_str,
            env=env,
            resolved_data_root=resolved_data_root,
            resolved_home_root=resolved_home_root,
        )
        path_source = "config_file"
    else:
        blob_root = default_blob

    if not _is_module_standalone_mode(env_config) and resolved_data_root is not None:
        if not sqlite_path_is_absolute_override:
            sqlite_path = ensure_under_data_root(
                sqlite_path, resolved_data_root, label="retrieve_sqlite_path"
            )
        if not blob_root_is_absolute_override:
            blob_root = ensure_under_data_root(
                blob_root, resolved_data_root, label="retrieve_blob_root"
            )

    wal_mode = bool(storage_raw.get("wal_mode", True))
    provider = resolve_storage_provider(
        module="retrieve",
        raw_provider=storage_raw.get("provider"),
        source_label="retrievectl.storage.provider",
        path_mode=path_mode,
        error_factory=ConfigError,
    )

    defaults_raw = section.get("defaults")
    if defaults_raw is None:
        defaults_raw = {}
    if not isinstance(defaults_raw, MutableMapping):
        defaults_raw = {}

    return RetrieveCtlConfig.model_validate(
        {
            "version": version,
            "storage": {
                "provider": provider,
                "sqlite_path": sqlite_path,
                "blob_root": blob_root,
                "wal_mode": wal_mode,
                "path_mode": path_mode,
                "path_source": path_source,
            },
            "defaults": dict(defaults_raw),
        }
    )


def _resolve_path(path: str | Path, *, env: Mapping[str, str]) -> Path:
    if isinstance(path, Path):
        return path.expanduser().resolve()
    if str(path).strip():
        candidate = str(path)
    else:
        candidate = env.get(RETRIEVECTL_CONFIG_ENV, str(resolve_default_config_path()))
    expanded = _expand_env(candidate, env)
    return resolve_module_config_path(expanded)


def _resolve_storage_path(
    *,
    raw_value: Any,
    env: Mapping[str, str],
    resolved_data_root: Path | None,
    resolved_home_root: Path,
) -> tuple[Path, bool]:
    path = _normalize_module_data_root_relative_path(
        Path(_expand_env(str(raw_value), env)).expanduser()
    )
    is_absolute_override = path.is_absolute()
    if not is_absolute_override:
        root = (
            resolved_data_root if resolved_data_root is not None else resolved_home_root
        )
        path = root / path
    return path.resolve(), is_absolute_override


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        parsed = yaml.safe_load(handle) or {}
    if not isinstance(parsed, MutableMapping):
        raise ConfigError("config root must be a mapping")
    return dict(parsed)


def _expand_env(value: str, env: Mapping[str, str]) -> str:
    return Template(value).safe_substitute(env)


def from_base_config(
    *,
    base_config: Any,
    home_root: Path,
    data_root: Path,
) -> RetrieveCtlConfig:
    env = dict(getattr(getattr(base_config, "runtime", object()), "env", {}) or {})
    env.setdefault(OPENMINION_DATA_ROOT_ENV, str(data_root))
    env.pop(OPENMINION_MODULE_STANDALONE_ENV, None)
    return load_config(None, home_root=home_root, env=env)


def resolve_config_path() -> Path:
    env = resolve_environment_config().snapshot()
    return _resolve_path("", env=env)
