from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openminion.modules.tool.runtime.registrar import ToolRegisterContext
    from openminion.modules.tool.registry import ToolRegistry


class SearchFirecrawlRegistrar:
    module_id = "search.firecrawl"
    is_provider_only = True

    def register(self, registry: ToolRegistry, ctx: ToolRegisterContext = None) -> None:
        del ctx
        from .plugin import register

        register(registry)

    def get_manifest(self, ctx: ToolRegisterContext) -> Any:
        del ctx
        from openminion.modules.tool.contracts import ToolBindingManifest

        return ToolBindingManifest(
            module_id=self.module_id,
            model_tools=(),
            runtime_bindings=(),
        )


REGISTRAR = SearchFirecrawlRegistrar()
