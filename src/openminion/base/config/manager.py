"""Instance-scoped config manager for derived module configs."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openminion.base.config.env import EnvironmentConfig
from openminion.base.config.io import load_config, resolve_config_path
from openminion.base.config.core import OpenMinionConfig
from openminion.base.config.paths import resolve_data_root, resolve_home_root
from openminion.base.config.interface import ModuleConfigFactory


class ConfigManagerError(RuntimeError):
    pass


@dataclass
class ConfigManager:
    """Instance-scoped config provider for derived module configs."""

    base_config: OpenMinionConfig
    home_root: Path
    data_root: Path
    config_path: Path | None = None
    _factories: dict[str, ModuleConfigFactory[Any]] = field(default_factory=dict)
    _cache: dict[str, Any] = field(default_factory=dict)
    env: EnvironmentConfig = field(init=False)

    def __post_init__(self) -> None:
        runtime_env = (
            getattr(getattr(self.base_config, "runtime", None), "env", None) or {}
        )
        object.__setattr__(
            self,
            "env",
            EnvironmentConfig.from_sources(runtime_env=runtime_env),
        )

    @classmethod
    def load(
        cls,
        config_path: str | None = None,
        *,
        home_root: Path | None = None,
        data_root: Path | None = None,
    ) -> "ConfigManager":
        env_config = EnvironmentConfig.from_sources()
        raw_config_path = str(config_path or "").strip()
        normalized_config_path: str | None = None
        if raw_config_path:
            candidate = Path(raw_config_path).expanduser()
            if candidate.is_absolute():
                normalized_config_path = str(candidate.resolve(strict=False))
            else:
                normalized_config_path = raw_config_path
        resolved_home_root = home_root or resolve_home_root(
            config_path=normalized_config_path
        )
        resolved_config_path = resolve_config_path(
            normalized_config_path, home_root=resolved_home_root
        )
        if not resolved_config_path.exists():
            raise ConfigManagerError(f"config file not found: {resolved_config_path}")
        resolved_data_root = data_root or resolve_data_root(
            resolved_home_root,
            data_root=env_config.openminion_data_root or None,
        )
        base_config = load_config(
            str(resolved_config_path), home_root=resolved_home_root
        )
        return cls(
            base_config=base_config,
            home_root=resolved_home_root,
            data_root=resolved_data_root,
            config_path=resolved_config_path,
        )

    def register(self, name: str, factory: ModuleConfigFactory[Any]) -> None:
        key = str(name or "").strip()
        if not key:
            raise ConfigManagerError("module name is required")
        if key in self._factories:
            raise ConfigManagerError(f"module factory already registered: {key}")
        self._factories[key] = factory

    def is_registered(self, name: str) -> bool:
        key = str(name or "").strip()
        if not key:
            return False
        return key in self._factories

    def get(self, name: str) -> Any:
        key = str(name or "").strip()
        if not key:
            raise ConfigManagerError("module name is required")
        if key in self._cache:
            return self._cache[key]
        factory = self._factories.get(key)
        if factory is None:
            raise ConfigManagerError(f"module config factory not registered: {key}")
        cfg = factory(
            base_config=self.base_config,
            home_root=self.home_root,
            data_root=self.data_root,
        )
        self._cache[key] = cfg
        return cfg

    def reset(self, name: str | None = None) -> None:
        if name is None:
            self._cache.clear()
            return
        key = str(name or "").strip()
        if not key:
            return
        self._cache.pop(key, None)
