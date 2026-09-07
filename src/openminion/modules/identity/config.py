import warnings
from pathlib import Path
from typing import Any, Literal
from collections.abc import Mapping
import os

from openminion.base.config import OpenMinionConfig
from openminion.base.config.runtime import (
    resolve_identity_db_from_env,
    resolve_identity_root_from_env,
)
from openminion.modules.config import (
    resolve_module_config_path,
    resolve_module_data_root,
    resolve_module_home_root,
)
from .constants import (
    DEFAULT_CONFIG_FILENAME,
    DEFAULT_IDENTITY_DB_FILENAME,
)
from .interfaces import IDENTITY_DEFAULT_RENDER_VERSION

from pydantic import BaseModel, ConfigDict, Field, model_validator

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover
    yaml = None  # type: ignore[assignment]


class StorageConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    backend: Literal["sqlite", "memory"] = "sqlite"
    sqlite_path: str = f"~/.openminion/identity/{DEFAULT_IDENTITY_DB_FILENAME}"
    db_path: str = ""

    @model_validator(mode="after")
    def _sync_db_paths(self) -> "StorageConfig":
        sqlite = str(self.sqlite_path or "").strip()
        db = str(self.db_path or "").strip()
        if (
            "db_path" in self.model_fields_set
            and "sqlite_path" not in self.model_fields_set
        ):
            self.sqlite_path = db
        elif (
            "sqlite_path" in self.model_fields_set
            and "db_path" not in self.model_fields_set
        ):
            self.db_path = sqlite
        elif not db and sqlite:
            self.db_path = sqlite
        elif db and not sqlite:
            self.sqlite_path = db
        return self


class PurposeBudget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_tokens: int = Field(..., ge=1)


class TemplateConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bullet_prefix: str = "- "
    section_headers: bool = False


class RenderingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    render_version: str = IDENTITY_DEFAULT_RENDER_VERSION
    default_budgets: dict[str, PurposeBudget] = Field(
        default_factory=lambda: {
            "decide": PurposeBudget(max_tokens=160),
            "plan": PurposeBudget(max_tokens=220),
            "act": PurposeBudget(max_tokens=180),
            "reflect": PurposeBudget(max_tokens=220),
            "summarize": PurposeBudget(max_tokens=160),
            "judge": PurposeBudget(max_tokens=170),
        }
    )
    templates: TemplateConfig = Field(default_factory=TemplateConfig)


class ProfilesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    directory: str = "~/.openminion/identity"
    bundle_root: str = "~/.openminion/identity"


class IdentityCtlConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    storage: StorageConfig = Field(default_factory=StorageConfig)
    rendering: RenderingConfig = Field(default_factory=RenderingConfig)
    profiles: ProfilesConfig = Field(default_factory=ProfilesConfig)


def from_base_config(
    *,
    base_config: OpenMinionConfig,
    home_root: Path,
    data_root: Path,
    env: Mapping[str, object] | None = None,
) -> IdentityCtlConfig:
    db_raw = str(base_config.identity.db_path or "").strip()
    bundle_raw = str(base_config.identity.bundle_root or "").strip()
    legacy_root_raw = str(base_config.identity.root or "").strip()
    if legacy_root_raw:
        warnings.warn(
            "openminion.config.identity.root is deprecated; use identity.bundle_root",
            DeprecationWarning,
            stacklevel=2,
        )
    legacy_db_raw = (
        legacy_root_raw
        if legacy_root_raw and Path(legacy_root_raw).suffix.lower() == ".db"
        else ""
    )
    configured_root = bundle_raw or ("" if legacy_db_raw else legacy_root_raw)
    root_path = resolve_identity_root_from_env(
        env=env,
        process_env={} if env is not None else None,
        home_root=home_root,
        data_root=data_root,
        configured_root=configured_root,
    )
    storage_path = resolve_identity_db_from_env(
        env=env,
        process_env={} if env is not None else None,
        home_root=home_root,
        data_root=data_root,
        configured_db=db_raw or legacy_db_raw,
        configured_root=configured_root,
    )

    return IdentityCtlConfig(
        storage=StorageConfig(sqlite_path=str(storage_path), db_path=str(storage_path)),
        profiles=ProfilesConfig(directory=str(root_path), bundle_root=str(root_path)),
    )


def load_config(
    path: str | Path = DEFAULT_CONFIG_FILENAME,
    *,
    home_root: Path | None = None,
    data_root: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> IdentityCtlConfig:
    env_map = dict(os.environ if env is None else env)
    resolved_home_root = resolve_module_home_root(home_root, env_map)
    resolved_data_root = resolve_module_data_root(
        home_root=resolved_home_root,
        env=env_map,
        data_root=data_root,
    )

    cfg_path = resolve_module_config_path(path, home_root=resolved_home_root)
    if not cfg_path.exists():
        if resolved_data_root is not None:
            base_root = resolved_home_root or Path.cwd().resolve(strict=False)
            return from_base_config(
                base_config=OpenMinionConfig(),
                home_root=base_root,
                data_root=resolved_data_root,
                env=env_map,
            )
        return IdentityCtlConfig()

    if yaml is None:
        raise RuntimeError("PyYAML is required to load identityctl config files")

    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError("identityctl config must parse to an object")

    payload = raw["identityctl"] if isinstance(raw.get("identityctl"), dict) else raw
    identity_cfg = IdentityCtlConfig.model_validate(payload)
    if resolved_data_root is None:
        return identity_cfg

    base_root = resolved_home_root or Path.cwd().resolve(strict=False)
    profile_fields = identity_cfg.profiles.model_fields_set
    configured_root = ""
    if "bundle_root" in profile_fields:
        configured_root = identity_cfg.profiles.bundle_root
    elif "directory" in profile_fields:
        configured_root = identity_cfg.profiles.directory
    storage_fields = identity_cfg.storage.model_fields_set
    configured_db = ""
    if "db_path" in storage_fields:
        configured_db = identity_cfg.storage.db_path
    elif "sqlite_path" in storage_fields:
        configured_db = identity_cfg.storage.sqlite_path
    identity_root = resolve_identity_root_from_env(
        env=env_map,
        process_env={},
        home_root=base_root,
        data_root=resolved_data_root,
        configured_root=configured_root,
    )
    identity_db = resolve_identity_db_from_env(
        env=env_map,
        process_env={},
        home_root=base_root,
        data_root=resolved_data_root,
        configured_db=configured_db,
        configured_root=configured_root,
    )
    identity_cfg.profiles.directory = str(identity_root)
    identity_cfg.profiles.bundle_root = str(identity_root)
    identity_cfg.storage.sqlite_path = str(identity_db)
    identity_cfg.storage.db_path = str(identity_db)
    return identity_cfg


def resolve_default_render_budget(
    purpose: str, *, identity_cfg: IdentityCtlConfig
) -> int:
    from .runtime.renderer import normalize_purpose

    normalized = normalize_purpose(purpose)
    budgets = identity_cfg.rendering.default_budgets
    budget = budgets.get(normalized) or budgets.get("act")
    return budget.max_tokens if budget is not None else 180


def resolve_path(raw_path: str | Path) -> Path:
    return Path(raw_path).expanduser().resolve(strict=False)


def load_yaml_file(path: Path) -> dict[str, Any]:
    if yaml is None:
        raise RuntimeError("PyYAML is required to load profile files")
    parsed = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(parsed, dict):
        raise ValueError(f"profile file must parse to object: {path}")
    return parsed
