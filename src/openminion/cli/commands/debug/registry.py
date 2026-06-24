from __future__ import annotations

from openminion.services.diagnostics.debug import DebugRegistry

from .providers.core import (
    OpenMinionDebugProvider,
    OpenMinionPluginsDebugProvider,
    OpenMinionToolsDebugProvider,
)
from .providers.modules import (
    OpenMinionCompressDebugProvider,
    OpenMinionContextDebugProvider,
    OpenMinionControlplaneDebugProvider,
    OpenMinionIdentityDebugProvider,
    OpenMinionMemoryDebugProvider,
    OpenMinionRegistryDebugProvider,
    OpenMinionRetrieveDebugProvider,
    OpenMinionSessionDebugProvider,
    OpenMinionSkillDebugProvider,
    OpenMinionTelemetryDebugProvider,
)
from .providers.tools import (
    OpenMinionReactionsDebugProvider,
    OpenMinionTavilyDebugProvider,
    OpenMinionWeatherDebugProvider,
    build_playwright_debug_provider,
)


def register_core_providers(registry: DebugRegistry) -> None:
    registry.register(OpenMinionDebugProvider())
    registry.register(OpenMinionToolsDebugProvider())
    registry.register(OpenMinionPluginsDebugProvider())

    registry.register(OpenMinionRegistryDebugProvider())
    registry.register(OpenMinionTelemetryDebugProvider())
    registry.register(OpenMinionControlplaneDebugProvider())

    try:
        from openminion.modules.controlplane.channels.telegram.debug_provider import (
            TelegramDebugProvider,
        )

        registry.register(TelegramDebugProvider())
    except ImportError:
        pass

    registry.register(OpenMinionWeatherDebugProvider())

    registry.register(OpenMinionTavilyDebugProvider())

    registry.register(OpenMinionRetrieveDebugProvider())

    registry.register(OpenMinionSessionDebugProvider())
    registry.register(OpenMinionContextDebugProvider())
    registry.register(OpenMinionMemoryDebugProvider())
    registry.register(OpenMinionCompressDebugProvider())

    registry.register(OpenMinionReactionsDebugProvider())

    registry.register(OpenMinionIdentityDebugProvider())

    registry.register(OpenMinionSkillDebugProvider())

    playwright_provider = build_playwright_debug_provider()
    if playwright_provider is not None:
        registry.register(playwright_provider)
