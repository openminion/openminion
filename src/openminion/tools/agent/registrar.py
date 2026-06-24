from typing import TYPE_CHECKING, Any

from openminion.modules.tool.contracts import (
    ModelToolDef,
    RuntimeBindingDef,
    ToolBindingManifest,
)
from openminion.modules.tool.contracts.model_ids import (
    MODEL_AGENT_GET,
    MODEL_AGENT_LIST,
    MODEL_TASK_DELEGATE,
)
from openminion.modules.tool.contracts.runtime_ids import (
    RUNTIME_AGENT_GET,
    RUNTIME_AGENT_LIST,
    RUNTIME_TASK_DELEGATE,
)

if TYPE_CHECKING:
    from openminion.modules.tool.runtime.registrar import ToolRegisterContext
    from openminion.modules.tool.registry import ToolRegistry


class AgentRegistrar:
    module_id = "agent"
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
                    model_tool_id=MODEL_AGENT_LIST,
                    description=(
                        "List registered agents visible to the current "
                        "runtime, optionally filtered by status"
                    ),
                    parameters={},
                    aliases=(),
                ),
                ModelToolDef(
                    model_tool_id=MODEL_AGENT_GET,
                    description=(
                        "Look up a single registered agent by exact agent identifier"
                    ),
                    parameters={},
                    aliases=(),
                ),
                ModelToolDef(
                    model_tool_id=MODEL_TASK_DELEGATE,
                    description=(
                        "Delegate a sub-task to a named agent. Surface "
                        "is published but A2A wiring is pending — handler "
                        "returns NOT_IMPLEMENTED until enabled."
                    ),
                    parameters={},
                    aliases=(),
                ),
            ),
            runtime_bindings=(
                RuntimeBindingDef(
                    runtime_binding_id=RUNTIME_AGENT_LIST,
                    model_tool_id=MODEL_AGENT_LIST,
                    runtime_candidates=("agent.list",),
                ),
                RuntimeBindingDef(
                    runtime_binding_id=RUNTIME_AGENT_GET,
                    model_tool_id=MODEL_AGENT_GET,
                    runtime_candidates=("agent.get",),
                ),
                RuntimeBindingDef(
                    runtime_binding_id=RUNTIME_TASK_DELEGATE,
                    model_tool_id=MODEL_TASK_DELEGATE,
                    runtime_candidates=("task.delegate",),
                ),
            ),
        )


REGISTRAR = AgentRegistrar()
