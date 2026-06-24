from __future__ import annotations

from openminion.modules.tool.registry import ToolRegistry
from openminion.tools.fetch.providers import provider_registry

from openminion.tools.fetch.providers.scrapling.plugin import register


def test_register_registers_scrapling_provider() -> None:
    registry = ToolRegistry()
    register(registry)
    assert "scrapling" in provider_registry().list_names()
