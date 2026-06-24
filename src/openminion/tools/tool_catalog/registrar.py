from __future__ import annotations

from typing import Any, TYPE_CHECKING

from openminion.modules.tool.contracts.model_ids import (
    MODEL_TOOL_LIST,
    MODEL_TOOL_SEARCH,
)
from openminion.modules.tool.contracts.runtime_ids import RUNTIME_TOOL_LIST

if TYPE_CHECKING:
    from openminion.modules.tool.registry import ToolRegistry
    from openminion.modules.tool.runtime.registrar import ToolRegisterContext


class ToolCatalogRegistrar:
    module_id = "tool_catalog"
    is_provider_only = False

    def register(self, registry: ToolRegistry, ctx: ToolRegisterContext = None) -> None:
        del ctx
        from .plugin import register as tool_register

        tool_register(registry)

    def get_manifest(self, ctx: ToolRegisterContext) -> Any:
        del ctx
        from openminion.modules.tool.contracts import (
            ModelToolDef,
            RuntimeBindingDef,
            ToolBindingManifest,
        )

        return ToolBindingManifest(
            module_id="tool_catalog",
            model_tools=(
                ModelToolDef(
                    model_tool_id=MODEL_TOOL_LIST,
                    description="List available model-facing tools, with optional filter",
                    parameters={},
                    aliases=(MODEL_TOOL_SEARCH,),
                ),
            ),
            runtime_bindings=(
                RuntimeBindingDef(
                    runtime_binding_id=RUNTIME_TOOL_LIST,
                    model_tool_id=MODEL_TOOL_LIST,
                    runtime_candidates=("tool.list",),
                ),
            ),
        )


REGISTRAR = ToolCatalogRegistrar()
