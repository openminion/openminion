from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from openminion.modules.tool.registry import ToolRegistry
    from openminion.modules.tool.runtime.registrar import ToolRegisterContext


class WeatherOpenMeteoRegistrar:
    module_id = "weather.openmeteo"
    is_provider_only = True

    def register(self, registry: ToolRegistry, ctx: ToolRegisterContext = None) -> None:
        del ctx
        from .plugin import register

        register(registry)

    def get_manifest(self, ctx: ToolRegisterContext) -> Any:
        del ctx
        return None


REGISTRAR = WeatherOpenMeteoRegistrar()
