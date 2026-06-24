"""Memory tool registration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from openminion.modules.tool.contracts import (
    ModelToolDef,
    RuntimeBindingDef,
    ToolBindingManifest,
)
from openminion.modules.tool.contracts.model_ids import (
    MODEL_MEMORY_FORGET,
    MODEL_MEMORY_SEARCH,
    MODEL_MEMORY_WRITE,
)
from openminion.modules.tool.contracts.runtime_ids import (
    RUNTIME_MEMORY_FORGET,
    RUNTIME_MEMORY_SEARCH,
    RUNTIME_MEMORY_WRITE,
)

if TYPE_CHECKING:
    from openminion.modules.tool.runtime.registrar import ToolRegisterContext
    from openminion.modules.tool.registry import ToolRegistry


class MemoryRegistrar:
    module_id = "memory"
    is_provider_only = False

    def register(
        self, registry: "ToolRegistry", ctx: "ToolRegisterContext | None" = None
    ) -> None:
        del ctx
        from .plugin import register

        register(registry)

    def get_manifest(self, ctx: "ToolRegisterContext") -> Any:
        del ctx
        return ToolBindingManifest(
            module_id=self.module_id,
            model_tools=(
                ModelToolDef(
                    model_tool_id=MODEL_MEMORY_WRITE,
                    description="Store an explicit structured memory record",
                    parameters={},
                    aliases=(),
                ),
                ModelToolDef(
                    model_tool_id=MODEL_MEMORY_SEARCH,
                    description="Search explicit memory records by typed query and scope",
                    parameters={},
                    aliases=(),
                ),
                ModelToolDef(
                    model_tool_id=MODEL_MEMORY_FORGET,
                    description="Soft-delete a memory record by explicit record id",
                    parameters={},
                    aliases=(),
                ),
            ),
            runtime_bindings=(
                RuntimeBindingDef(
                    runtime_binding_id=RUNTIME_MEMORY_WRITE,
                    model_tool_id=MODEL_MEMORY_WRITE,
                    runtime_candidates=("memory.write",),
                ),
                RuntimeBindingDef(
                    runtime_binding_id=RUNTIME_MEMORY_SEARCH,
                    model_tool_id=MODEL_MEMORY_SEARCH,
                    runtime_candidates=("memory.search",),
                ),
                RuntimeBindingDef(
                    runtime_binding_id=RUNTIME_MEMORY_FORGET,
                    model_tool_id=MODEL_MEMORY_FORGET,
                    runtime_candidates=("memory.forget",),
                ),
            ),
        )


REGISTRAR = MemoryRegistrar()
