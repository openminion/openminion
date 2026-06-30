from __future__ import annotations

from typing import TYPE_CHECKING, Any

from openminion.modules.tool.contracts import (
    ModelToolDef,
    RuntimeBindingDef,
    ToolBindingManifest,
)
from openminion.modules.tool.contracts.model_ids import MODEL_HOST_METRICS
from openminion.modules.tool.contracts.runtime_ids import RUNTIME_HOST_METRICS

from .interfaces import TOOL_HOST_METRICS

if TYPE_CHECKING:
    from openminion.modules.tool.registry import ToolRegistry
    from openminion.modules.tool.runtime.registrar import ToolRegisterContext


class HostRegistrar:
    module_id = "host"
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
                    model_tool_id=MODEL_HOST_METRICS,
                    description=(
                        "Get local host platform, disk usage, and memory metrics "
                        "without shell commands."
                    ),
                    parameters={},
                    aliases=("system.status", "host.status"),
                ),
            ),
            runtime_bindings=(
                RuntimeBindingDef(
                    runtime_binding_id=RUNTIME_HOST_METRICS,
                    model_tool_id=MODEL_HOST_METRICS,
                    runtime_candidates=(TOOL_HOST_METRICS,),
                ),
            ),
        )


REGISTRAR = HostRegistrar()
