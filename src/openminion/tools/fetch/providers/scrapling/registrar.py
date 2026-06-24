from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from openminion.modules.tool.registry import ToolRegistry
    from openminion.modules.tool.runtime.registrar import ToolRegisterContext


class FetchScraplingRegistrar:
    """Registrar with manifest for fetch_scrapling."""

    module_id = "fetch_scrapling"
    is_provider_only = True

    def register(self, registry: ToolRegistry, ctx: ToolRegisterContext = None) -> None:
        """Register tool."""
        from .plugin import register

        register(registry)

    def get_manifest(self, ctx: ToolRegisterContext) -> Any:
        """Return empty ToolBindingManifest for fetch_scrapling module (internal_only)."""
        from openminion.modules.tool.contracts import ToolBindingManifest

        return ToolBindingManifest(
            module_id="fetch_scrapling",
            model_tools=(),
            runtime_bindings=(),
        )


# Module registrar (required by bootstrap)
REGISTRAR = FetchScraplingRegistrar()
