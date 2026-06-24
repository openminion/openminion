from __future__ import annotations

from typing import TYPE_CHECKING, Any

from openminion.modules.tool.contracts import (
    ModelToolDef,
    RuntimeBindingDef,
    ToolBindingManifest,
)
from openminion.modules.tool.contracts.model_ids import MODEL_WEATHER
from openminion.modules.tool.contracts.runtime_ids import (
    RUNTIME_WEATHER_CURRENT,
)

if TYPE_CHECKING:
    from openminion.modules.tool.runtime.registrar import ToolRegisterContext
    from openminion.modules.tool.registry import ToolRegistry


class WeatherRegistrar:
    module_id = "weather"
    is_provider_only = False

    def register(self, registry: ToolRegistry, ctx: ToolRegisterContext = None) -> None:
        del ctx
        from .plugin import register

        register(registry)

    def get_manifest(self, ctx: ToolRegisterContext) -> Any:
        del ctx
        return ToolBindingManifest(
            module_id=self.module_id,
            model_tools=(
                ModelToolDef(
                    model_tool_id=MODEL_WEATHER,
                    description="Get current weather conditions",
                    parameters={},
                    aliases=(),
                ),
            ),
            runtime_bindings=(
                RuntimeBindingDef(
                    runtime_binding_id=RUNTIME_WEATHER_CURRENT,
                    model_tool_id=MODEL_WEATHER,
                    runtime_candidates=("weather",),
                ),
            ),
        )


REGISTRAR = WeatherRegistrar()
