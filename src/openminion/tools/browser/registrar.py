from __future__ import annotations

from typing import Any, TYPE_CHECKING

from openminion.modules.tool.contracts.model_ids import MODEL_BROWSER
from openminion.modules.tool.contracts.runtime_ids import RUNTIME_BROWSER

if TYPE_CHECKING:
    from openminion.modules.tool.registry import ToolRegistry
    from openminion.modules.tool.runtime.registrar import ToolRegisterContext


class BrowserRegistrar:
    """Registrar with manifest for browser."""

    module_id = "browser"
    is_provider_only = False

    def register(self, registry: ToolRegistry, ctx: ToolRegisterContext = None) -> None:
        """Register tool."""
        from .tool import register as tool_register

        tool_register(registry)

    def get_manifest(self, ctx: ToolRegisterContext) -> Any:
        """Return ToolBindingManifest for browser module."""
        from openminion.modules.tool.contracts import (
            ModelToolDef,
            RuntimeBindingDef,
            ToolBindingManifest,
        )

        return ToolBindingManifest(
            module_id="browser",
            model_tools=(
                ModelToolDef(
                    model_tool_id=MODEL_BROWSER,
                    description=(
                        "Provider-neutral browser automation for interactive or "
                        "visual web tasks. Use web.fetch for static URL content "
                        "retrieval that does not require page interaction."
                    ),
                    parameters={},
                    aliases=(),
                ),
            ),
            runtime_bindings=(
                RuntimeBindingDef(
                    runtime_binding_id=RUNTIME_BROWSER,
                    model_tool_id=MODEL_BROWSER,
                    runtime_candidates=("browser",),
                ),
            ),
        )


REGISTRAR = BrowserRegistrar()
