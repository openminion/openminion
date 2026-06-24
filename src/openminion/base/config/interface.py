from __future__ import annotations

from pathlib import Path
from typing import Protocol, TypeVar, runtime_checkable

from openminion.base.config.core import OpenMinionConfig

T = TypeVar("T", covariant=True)


@runtime_checkable
class BaseModuleConfig(Protocol):
    """Shared metadata contract for module-derived config objects."""

    module_id: str
    version: str | int
    home_root: Path | None
    data_root: Path | None


@runtime_checkable
class ModuleConfigFactory(Protocol[T]):
    """Build a typed module config from root config and resolved roots."""

    def __call__(
        self,
        *,
        base_config: OpenMinionConfig,
        home_root: Path,
        data_root: Path,
    ) -> T: ...
