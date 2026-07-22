import warnings
from pathlib import Path
from typing import Any, Literal
from collections.abc import Mapping
import os

from openminion.base.config import OpenMinionConfig
from openminion.modules.config import (
    resolve_module_config_path,
    resolve_module_data_root,
    resolve_module_home_root,
)
from .constants import (
    DEFAULT_CONFIG_FILENAME,
    DEFAULT_IDENTITY_CTL_DB_FILENAME,
    DEFAULT_INTEGRATED_BUNDLE_SUBPATH,
    DEFAULT_INTEGRATED_STORAGE_SUBPATH,
    DEFAULT_PROFILES_SUBPATH,
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
    sqlite_path: str = f"~/.openminion/identity/{DEFAULT_IDENTITY_CTL_DB_FILENAME}"
    db_path: str = ""

    @model_validator(mode="after")
    def _sync_db_paths(self) -> "StorageConfig":
        sqlite = str(self.sqlite_path or "").strip()
        db = str(self.db_path or "").strip()
        if not db and sqlite:
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

    directory: str = "~/.openminion/identity/profiles"
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
) -> IdentityCtlConfig:
    db_raw = str(base_config.identity.db_path or "").strip()
    if db_raw:
        storage_path = Path(db_raw).expanduser()
        if not storage_path.is_absolute():
            storage_path = home_root / storage_path
    else:
        storage_path = data_root / DEFAULT_INTEGRATED_STORAGE_SUBPATH
    storage_path = storage_path.resolve(strict=False)

    bundle_raw = str(base_config.identity.bundle_root or "").strip()
    legacy_root_raw = str(base_config.identity.root or "").strip()
    if legacy_root_raw:
        warnings.warn(
            "openminion.config.identity.root is deprecated; use identity.bundle_root",
            DeprecationWarning,
            stacklevel=2,
        )
    root_raw = bundle_raw or legacy_root_raw
    if root_raw:
        root_path = Path(root_raw).expanduser()
        if not root_path.is_absolute():
            root_path = home_root / root_path
    else:
        root_path = data_root / DEFAULT_INTEGRATED_BUNDLE_SUBPATH
    root_path = root_path.resolve(strict=False)
    profiles_path = (root_path / DEFAULT_PROFILES_SUBPATH).resolve(strict=False)

    return IdentityCtlConfig(
        storage=StorageConfig(sqlite_path=str(storage_path), db_path=str(storage_path)),
        profiles=ProfilesConfig(
            directory=str(profiles_path), bundle_root=str(root_path)
        ),
    )


def load_config(
    path: str | Path = DEFAULT_CONFIG_FILENAME,
    *,
    home_root: Path | None = None,
    data_root: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> IdentityCtlConfig:
    env_map = dict(env or os.environ)
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
            )
        return IdentityCtlConfig()

    if yaml is None:
        raise RuntimeError("PyYAML is required to load identityctl config files")

    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError("identityctl config must parse to an object")

    if "identityctl" in raw and isinstance(raw["identityctl"], dict):
        return IdentityCtlConfig.model_validate(raw["identityctl"])
    return IdentityCtlConfig.model_validate(raw)


def resolve_default_render_budget(
    purpose: str, *, identity_cfg: IdentityCtlConfig
) -> int:
    from .runtime.renderer import normalize_purpose

    normalized = normalize_purpose(str(purpose))
    budgets = identity_cfg.rendering.default_budgets
    budget = budgets.get(normalized)
    if budget is not None and int(getattr(budget, "max_tokens", 0) or 0) > 0:
        return int(budget.max_tokens)
    fallback = budgets.get("act")
    if fallback is not None and int(getattr(fallback, "max_tokens", 0) or 0) > 0:
        return int(fallback.max_tokens)
    return 180


def resolve_path(raw_path: str | Path) -> Path:
    return Path(raw_path).expanduser().resolve(strict=False)


def load_yaml_file(path: Path) -> dict[str, Any]:
    if yaml is None:
        raise RuntimeError("PyYAML is required to load profile files")
    parsed = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(parsed, dict):
        raise ValueError(f"profile file must parse to object: {path}")
    return parsed
