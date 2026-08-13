from __future__ import annotations

from typing import TYPE_CHECKING

from openminion.modules.tool.contracts import (
    ModelToolDef,
    RuntimeBindingDef,
    ToolBindingManifest,
)

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

_TOOL_BINDINGS = (
    (MODEL_GWS_CALL, "Call Google Workspace API", RUNTIME_GWS_CALL, "gws.call"),
    (MODEL_GWS_SCHEMA, "Get GWS API schema", RUNTIME_GWS_SCHEMA, "gws.schema"),
    (MODEL_GWS_AUTH_SETUP, "Setup GWS auth", RUNTIME_GWS_AUTH_SETUP, "gws.auth.setup"),
    (MODEL_GWS_AUTH_LOGIN, "Login to GWS", RUNTIME_GWS_AUTH_LOGIN, "gws.auth.login"),
    (
        MODEL_GWS_AUTH_EXPORT,
        "Export GWS credentials",
        RUNTIME_GWS_AUTH_EXPORT,
        "gws.auth.export",
    ),
)


class GWSRegistrar:
    module_id = "gws"
    is_provider_only = False

    def register(self, registry: ToolRegistry, ctx: ToolRegisterContext = None) -> None:
        del ctx
        from .plugin import register

        register(registry)

    def get_manifest(self, ctx: ToolRegisterContext) -> ToolBindingManifest:
        del ctx
        return ToolBindingManifest(
            module_id=self.module_id,
            model_tools=tuple(
                ModelToolDef(
                    model_tool_id=model_id, description=description, parameters={}
                )
                for model_id, description, _, _ in _TOOL_BINDINGS
            ),
            runtime_bindings=tuple(
                RuntimeBindingDef(
                    runtime_binding_id=runtime_id,
                    model_tool_id=model_id,
                    runtime_candidates=(runtime_name,),
                )
                for model_id, _, runtime_id, runtime_name in _TOOL_BINDINGS
            ),
        )


REGISTRAR = GWSRegistrar()
