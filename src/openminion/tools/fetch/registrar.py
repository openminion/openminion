from __future__ import annotations

from typing import Any, TYPE_CHECKING

from openminion.modules.tool.contracts.model_ids import MODEL_WEB_FETCH
from openminion.modules.tool.contracts.runtime_ids import RUNTIME_WEB_FETCH

if TYPE_CHECKING:
    from openminion.modules.tool.registry import ToolRegistry
    from openminion.modules.tool.runtime.registrar import ToolRegisterContext


class FetchRegistrar:
    """Registrar with manifest for fetch."""

    module_id = "fetch"
    is_provider_only = False

    def register(self, registry: ToolRegistry, ctx: ToolRegisterContext = None) -> None:
        """Register tool."""
        from .plugin import register

        register(registry)

    def get_manifest(self, ctx: ToolRegisterContext) -> Any:
        """Return ToolBindingManifest for fetch module."""
        from openminion.modules.tool.contracts import (
            ModelToolDef,
            RuntimeBindingDef,
            ToolBindingManifest,
        )

        return ToolBindingManifest(
            module_id="fetch",
            model_tools=(
                ModelToolDef(
                    model_tool_id=MODEL_WEB_FETCH,
                    description=(
                        "Fetch static URL content for reading or citation. Prefer "
                        "this over browser automation when no page interaction, "
                        "screenshot, or DOM action is required."
                    ),
                    parameters={},
                    aliases=(),
                ),
            ),
            runtime_bindings=(
                RuntimeBindingDef(
                    runtime_binding_id=RUNTIME_WEB_FETCH,
                    model_tool_id=MODEL_WEB_FETCH,
                    runtime_candidates=("fetch.get", "fetch.head"),
                ),
            ),
        )


REGISTRAR = FetchRegistrar()
