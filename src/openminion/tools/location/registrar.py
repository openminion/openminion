from __future__ import annotations

from typing import Any, TYPE_CHECKING

from openminion.modules.tool.contracts.model_ids import MODEL_LOCATION
from openminion.modules.tool.contracts.runtime_ids import RUNTIME_LOCATION

if TYPE_CHECKING:
    from openminion.modules.tool.registry import ToolRegistry
    from openminion.modules.tool.runtime.registrar import ToolRegisterContext


class LocationRegistrar:
    module_id = "location"
    is_provider_only = False

    def register(self, registry: ToolRegistry, ctx: ToolRegisterContext = None) -> None:
        del ctx
        from .plugin import register

        register(registry)

    def get_manifest(self, ctx: ToolRegisterContext) -> Any:
        del ctx
        from openminion.modules.tool.contracts import (
            ModelToolDef,
            RuntimeBindingDef,
            ToolBindingManifest,
        )

        return ToolBindingManifest(
            module_id=self.module_id,
            model_tools=(
                ModelToolDef(
                    model_tool_id=MODEL_LOCATION,
                    description="Location operations (get, set, IP-based)",
                    parameters={},
                    aliases=(),
                ),
            ),
            runtime_bindings=(
                RuntimeBindingDef(
                    runtime_binding_id=RUNTIME_LOCATION,
                    model_tool_id=MODEL_LOCATION,
                    runtime_candidates=("location.get",),
                ),
            ),
        )


REGISTRAR = LocationRegistrar()
