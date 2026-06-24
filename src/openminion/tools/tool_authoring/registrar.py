from typing import TYPE_CHECKING, Any

from openminion.modules.tool.contracts import (
    ModelToolDef,
    RuntimeBindingDef,
    ToolBindingManifest,
)
from openminion.modules.tool.contracts.model_ids import (
    MODEL_TOOL_AUTHOR,
    MODEL_TOOL_GET,
    MODEL_TOOL_INSPECT,
    MODEL_TOOL_REGISTER,
)
from openminion.modules.tool.contracts.runtime_ids import (
    RUNTIME_TOOL_AUTHOR,
    RUNTIME_TOOL_GET,
    RUNTIME_TOOL_INSPECT,
    RUNTIME_TOOL_REGISTER,
)

if TYPE_CHECKING:
    from openminion.modules.tool.registry import ToolRegistry
    from openminion.modules.tool.runtime.registrar import ToolRegisterContext


class AuthoredToolRegistrar:
    module_id = "tool_authoring"
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
                    model_tool_id=MODEL_TOOL_AUTHOR,
                    description="Persist an authored-tool draft from explicit source, tests, and schemas.",
                    parameters={},
                    aliases=(),
                ),
                ModelToolDef(
                    model_tool_id=MODEL_TOOL_INSPECT,
                    description="Run static analysis and held-out tests against an authored-tool draft or ad-hoc source.",
                    parameters={},
                    aliases=(),
                ),
                ModelToolDef(
                    model_tool_id=MODEL_TOOL_REGISTER,
                    description="Register an inspected authored-tool draft into the runtime registry and issue a policy grant.",
                    parameters={},
                    aliases=(),
                ),
                ModelToolDef(
                    model_tool_id=MODEL_TOOL_GET,
                    description="Fetch full metadata, source, tests, and audit history for one authored tool.",
                    parameters={},
                    aliases=(),
                ),
            ),
            runtime_bindings=(
                RuntimeBindingDef(
                    runtime_binding_id=RUNTIME_TOOL_AUTHOR,
                    model_tool_id=MODEL_TOOL_AUTHOR,
                    runtime_candidates=(MODEL_TOOL_AUTHOR,),
                ),
                RuntimeBindingDef(
                    runtime_binding_id=RUNTIME_TOOL_INSPECT,
                    model_tool_id=MODEL_TOOL_INSPECT,
                    runtime_candidates=(MODEL_TOOL_INSPECT,),
                ),
                RuntimeBindingDef(
                    runtime_binding_id=RUNTIME_TOOL_REGISTER,
                    model_tool_id=MODEL_TOOL_REGISTER,
                    runtime_candidates=(MODEL_TOOL_REGISTER,),
                ),
                RuntimeBindingDef(
                    runtime_binding_id=RUNTIME_TOOL_GET,
                    model_tool_id=MODEL_TOOL_GET,
                    runtime_candidates=(MODEL_TOOL_GET,),
                ),
            ),
        )


REGISTRAR = AuthoredToolRegistrar()
