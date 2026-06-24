from __future__ import annotations

from typing import Any, Mapping, TYPE_CHECKING

if TYPE_CHECKING:
    from openminion.modules.tool.registry import ToolRegistry
    from openminion.modules.tool.runtime.registrar import ToolRegisterContext


class BrowserPinchTabRegistrar:
    """Registrar with manifest for browser_pinchtab."""

    module_id = "browser.pinchtab"
    is_provider_only = True

    def register(self, registry: ToolRegistry, ctx: ToolRegisterContext = None) -> None:
        """Register tool."""
        from .plugin import register

        runtime_cfg = getattr(ctx, "config", None) if ctx is not None else None
        runtime_env = getattr(runtime_cfg, "env", None)
        if not isinstance(runtime_env, Mapping):
            runtime_env = None
        register(registry, env=runtime_env)

    def get_manifest(self, ctx: ToolRegisterContext) -> Any:
        """Return ToolBindingManifest for browser_pinchtab module."""
        from openminion.modules.tool.contracts import (
            ToolBindingManifest,
        )

        return ToolBindingManifest(
            module_id="browser.pinchtab",
            model_tools=tuple(),
            runtime_bindings=tuple(),
        )


# Module registrar (required by bootstrap)
REGISTRAR = BrowserPinchTabRegistrar()
