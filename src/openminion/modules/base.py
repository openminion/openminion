from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ModuleDescriptor:
    """Module metadata."""

    name: str
    version: str = "1.0.0"
    contract_version: str = "v1"
    provider_id: str | None = None
    config: dict[str, Any] = field(default_factory=dict)


class ModuleBase:
    """Optional convenience base class for module implementations."""

    def __init__(
        self,
        *,
        descriptor: ModuleDescriptor,
        config: dict[str, Any] | None = None,
    ) -> None:
        self._descriptor = descriptor
        self._config = {} if config is None else config

    @property
    def descriptor(self) -> ModuleDescriptor:
        return self._descriptor

    @property
    def config(self) -> dict[str, Any]:
        return self._config

    def healthcheck(self) -> dict[str, Any]:
        """Return a default module health payload."""
        return {
            "status": "ok",
            "module": self.descriptor.name,
            "version": self.descriptor.version,
        }

    def close(self) -> None:
        """Release resources when subclasses need teardown."""
        pass
