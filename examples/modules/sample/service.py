from __future__ import annotations

from typing import Any

from openminion.modules.base import ModuleBase, ModuleDescriptor
from .interfaces import (
    SAMPLE_INTERFACE_VERSION,
    SampleService,
)


class SampleServiceImpl(ModuleBase, SampleService):
    """Sample module service implementation."""

    contract_version = SAMPLE_INTERFACE_VERSION

    def __init__(
        self,
        *,
        descriptor: ModuleDescriptor | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        if descriptor is None:
            descriptor = ModuleDescriptor(
                name="sample",
                version="1.0.0",
                contract_version=SAMPLE_INTERFACE_VERSION,
                provider_id="default",
            )

        super().__init__(descriptor=descriptor, config=config)
        self._initialized = True

    def healthcheck(self) -> dict[str, Any]:
        base_health = super().healthcheck()
        base_health.update(
            {
                "initialized": self._initialized,
                "config_keys": list(self.config) if self.config else [],
            }
        )
        return base_health

    def process(self, data: str) -> dict[str, Any]:
        if not self._initialized:
            return {
                "success": False,
                "error": "Service not initialized",
            }

        prefix = self.config.get("prefix", "")
        suffix = self.config.get("suffix", "")

        return {
            "success": True,
            "input": data,
            "output": f"{prefix}{data}{suffix}",
            "module": self.descriptor.name,
            "version": self.descriptor.version,
        }

    def close(self) -> None:
        self._initialized = False
        super().close()
