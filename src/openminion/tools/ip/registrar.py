from __future__ import annotations

from typing import TYPE_CHECKING, Any

from openminion.modules.tool.contracts import (
    ModelToolDef,
    RuntimeBindingDef,
    ToolBindingManifest,
)
from openminion.modules.tool.contracts.model_ids import (
    MODEL_IP_LOCAL,
    MODEL_IP_PUBLIC,
)
from openminion.modules.tool.contracts.runtime_ids import (
    RUNTIME_IP_LOCAL,
    RUNTIME_IP_PUBLIC,
)

from .interfaces import TOOL_IP_LOCAL, TOOL_IP_PUBLIC

if TYPE_CHECKING:
    from openminion.modules.tool.runtime.registrar import ToolRegisterContext
    from openminion.modules.tool.registry import ToolRegistry


class IpRegistrar:
    module_id = "ip"
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
                    model_tool_id=MODEL_IP_PUBLIC,
                    description="Get the current public IP address.",
                    parameters={},
                    aliases=(),
                ),
                ModelToolDef(
                    model_tool_id=MODEL_IP_LOCAL,
                    description="Get local interface IP addresses.",
                    parameters={},
                    aliases=(),
                ),
            ),
            runtime_bindings=(
                RuntimeBindingDef(
                    runtime_binding_id=RUNTIME_IP_PUBLIC,
                    model_tool_id=MODEL_IP_PUBLIC,
                    runtime_candidates=(TOOL_IP_PUBLIC,),
                ),
                RuntimeBindingDef(
                    runtime_binding_id=RUNTIME_IP_LOCAL,
                    model_tool_id=MODEL_IP_LOCAL,
                    runtime_candidates=(TOOL_IP_LOCAL,),
                ),
            ),
        )


REGISTRAR = IpRegistrar()
