from __future__ import annotations

from typing import Any, Mapping, TYPE_CHECKING

if TYPE_CHECKING:
    from openminion.modules.tool.registry import ToolRegistry
    from openminion.modules.tool.runtime.registrar import ToolRegisterContext


class BrowserPlaywrightRegistrar:
    """Registrar with manifest for browser_playwright."""

    module_id = "browser.playwright"
    is_provider_only = True

    def register(self, registry: ToolRegistry, ctx: ToolRegisterContext = None) -> None:
        """Register tool."""
        from .plugin import register

        runtime_env = getattr(getattr(ctx, "config", None), "env", None)
        register(
            registry, env=runtime_env if isinstance(runtime_env, Mapping) else None
        )

    def get_manifest(self, ctx: ToolRegisterContext) -> Any:
        """Return ToolBindingManifest for browser_playwright module."""
        from openminion.modules.tool.contracts import ToolBindingManifest

        return ToolBindingManifest(
            module_id=self.module_id,
            model_tools=tuple(),
            runtime_bindings=tuple(),
        )


REGISTRAR = BrowserPlaywrightRegistrar()
