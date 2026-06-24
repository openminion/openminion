"""Google Workspace tool registration."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from openminion.modules.tool.contracts.model_ids import (
    MODEL_GWS_AUTH_EXPORT,
    MODEL_GWS_AUTH_LOGIN,
    MODEL_GWS_AUTH_SETUP,
    MODEL_GWS_CALL,
    MODEL_GWS_SCHEMA,
)
from openminion.modules.tool.contracts.runtime_ids import (
    RUNTIME_GWS_AUTH_EXPORT,
    RUNTIME_GWS_AUTH_LOGIN,
    RUNTIME_GWS_AUTH_SETUP,
    RUNTIME_GWS_CALL,
    RUNTIME_GWS_SCHEMA,
)

if TYPE_CHECKING:
    from openminion.modules.tool.registry import ToolRegistry
    from openminion.modules.tool.runtime.registrar import ToolRegisterContext


class GWSRegistrar:
    module_id = "gws"
    is_provider_only = False

    def register(self, registry: ToolRegistry, ctx: ToolRegisterContext = None) -> None:
        del ctx
        from .plugin import register

        register(registry)

    def get_manifest(self, ctx: ToolRegisterContext) -> Any:
        del ctx
        from openminion.modules.tool.contracts import (
            ModelToolDef,
            RuntimeBindingDef,
            ToolBindingManifest,
        )

        return ToolBindingManifest(
            module_id="gws",
            model_tools=(
                ModelToolDef(
                    model_tool_id=MODEL_GWS_CALL,
                    description="Call Google Workspace API",
                    parameters={},
                ),
                ModelToolDef(
                    model_tool_id=MODEL_GWS_SCHEMA,
                    description="Get GWS API schema",
                    parameters={},
                ),
                ModelToolDef(
                    model_tool_id=MODEL_GWS_AUTH_SETUP,
                    description="Setup GWS auth",
                    parameters={},
                ),
                ModelToolDef(
                    model_tool_id=MODEL_GWS_AUTH_LOGIN,
                    description="Login to GWS",
                    parameters={},
                ),
                ModelToolDef(
                    model_tool_id=MODEL_GWS_AUTH_EXPORT,
                    description="Export GWS credentials",
                    parameters={},
                ),
            ),
            runtime_bindings=(
                RuntimeBindingDef(
                    runtime_binding_id=RUNTIME_GWS_CALL,
                    model_tool_id=MODEL_GWS_CALL,
                    runtime_candidates=("gws.call",),
                ),
                RuntimeBindingDef(
                    runtime_binding_id=RUNTIME_GWS_SCHEMA,
                    model_tool_id=MODEL_GWS_SCHEMA,
                    runtime_candidates=("gws.schema",),
                ),
                RuntimeBindingDef(
                    runtime_binding_id=RUNTIME_GWS_AUTH_SETUP,
                    model_tool_id=MODEL_GWS_AUTH_SETUP,
                    runtime_candidates=("gws.auth.setup",),
                ),
                RuntimeBindingDef(
                    runtime_binding_id=RUNTIME_GWS_AUTH_LOGIN,
                    model_tool_id=MODEL_GWS_AUTH_LOGIN,
                    runtime_candidates=("gws.auth.login",),
                ),
                RuntimeBindingDef(
                    runtime_binding_id=RUNTIME_GWS_AUTH_EXPORT,
                    model_tool_id=MODEL_GWS_AUTH_EXPORT,
                    runtime_candidates=("gws.auth.export",),
                ),
            ),
        )


REGISTRAR = GWSRegistrar()
