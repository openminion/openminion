from __future__ import annotations

from typing import TYPE_CHECKING, Any

from openminion.modules.tool.contracts import (
    ModelToolDef,
    RuntimeBindingDef,
    ToolBindingManifest,
)
from openminion.modules.tool.contracts.model_ids import OPS_MODEL_TOOL_IDS
from openminion.modules.tool.contracts.runtime_ids import OPS_RUNTIME_BINDING_IDS

from .interfaces import ALL_OPS_TOOLS

if TYPE_CHECKING:
    from openminion.modules.tool.registry import ToolRegistry
    from openminion.modules.tool.runtime.registrar import ToolRegisterContext


class OpsRegistrar:
    module_id = "ops"
    is_provider_only = False

    def register(self, registry: ToolRegistry, ctx: ToolRegisterContext = None) -> None:
        del ctx
        from .plugin import register

        register(registry)

    def get_manifest(self, ctx: ToolRegisterContext) -> Any:
        del ctx
        return ToolBindingManifest(
            module_id=self.module_id,
            model_tools=tuple(
                ModelToolDef(
                    model_tool_id=model_id,
                    description=f"System operations capability: {model_id}.",
                    parameters={},
                    aliases=(),
                )
                for model_id in OPS_MODEL_TOOL_IDS
            ),
            runtime_bindings=tuple(
                RuntimeBindingDef(
                    runtime_binding_id=runtime_id,
                    model_tool_id=model_id,
                    runtime_candidates=(tool_name,),
                )
                for runtime_id, model_id, tool_name in zip(
                    OPS_RUNTIME_BINDING_IDS,
                    OPS_MODEL_TOOL_IDS,
                    ALL_OPS_TOOLS,
                    strict=True,
                )
            ),
        )


REGISTRAR = OpsRegistrar()
