from __future__ import annotations

from typing import TYPE_CHECKING, Any

from openminion.modules.tool.contracts import (
    ModelToolDef,
    RuntimeBindingDef,
    ToolBindingManifest,
)
from openminion.modules.tool.contracts.model_ids import MODEL_WEB_SEARCH
from openminion.modules.tool.contracts.runtime_ids import RUNTIME_WEB_SEARCH

if TYPE_CHECKING:
    from openminion.modules.tool.runtime.registrar import ToolRegisterContext
    from openminion.modules.tool.registry import ToolRegistry


class SearchRegistrar:
    module_id = "search"
    is_provider_only = False

    def register(self, registry: ToolRegistry, ctx: ToolRegisterContext = None) -> None:
        del ctx
        from .plugin import register

        register(registry)

    def get_manifest(self, ctx: ToolRegisterContext) -> Any:
        del ctx
        runtime_candidates = (
            "search.dispatch",
            "search.tavily.search",
            "search.brave.search",
            "search.serpapi.search",
            "search.firecrawl.search",
            "search.serper.search",
            "search.tinyfish.search",
        )
        return ToolBindingManifest(
            module_id=self.module_id,
            model_tools=(
                ModelToolDef(
                    model_tool_id=MODEL_WEB_SEARCH,
                    description="Search the web",
                    parameters={},
                    aliases=(),
                ),
            ),
            runtime_bindings=(
                RuntimeBindingDef(
                    runtime_binding_id=RUNTIME_WEB_SEARCH,
                    model_tool_id=MODEL_WEB_SEARCH,
                    runtime_candidates=runtime_candidates,
                ),
            ),
        )


REGISTRAR = SearchRegistrar()
