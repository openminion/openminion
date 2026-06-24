from typing import TYPE_CHECKING, Any

from openminion.modules.tool.contracts import (
    ModelToolDef,
    RuntimeBindingDef,
    ToolBindingManifest,
)
from openminion.modules.tool.contracts.model_ids import (
    MODEL_TASK_CONSOLIDATE_MEMORY,
    MODEL_TASK_CANCEL,
    MODEL_TASK_LIST,
    MODEL_TASK_PAUSE,
    MODEL_TASK_RESUME,
    MODEL_TASK_SCHEDULE,
    MODEL_TASK_SHOW,
    MODEL_TASK_WATCH,
)
from openminion.modules.tool.contracts.runtime_ids import (
    RUNTIME_TASK_CONSOLIDATE_MEMORY,
    RUNTIME_TASK_CANCEL,
    RUNTIME_TASK_LIST,
    RUNTIME_TASK_PAUSE,
    RUNTIME_TASK_RESUME,
    RUNTIME_TASK_SCHEDULE,
    RUNTIME_TASK_SHOW,
    RUNTIME_TASK_WATCH,
)

if TYPE_CHECKING:
    from openminion.modules.tool.runtime.registrar import ToolRegisterContext
    from openminion.modules.tool.registry import ToolRegistry


class TaskRegistrar:
    module_id = "task"
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
                    model_tool_id=MODEL_TASK_SCHEDULE,
                    description="Schedule an agent instruction for later execution",
                    parameters={},
                    aliases=(),
                ),
                ModelToolDef(
                    model_tool_id=MODEL_TASK_CONSOLIDATE_MEMORY,
                    description="Create a recurring memory-consolidation task backed by the cron scheduler",
                    parameters={},
                    aliases=(),
                ),
                ModelToolDef(
                    model_tool_id=MODEL_TASK_WATCH,
                    description="Create a proactive monitoring watch backed by the cron scheduler",
                    parameters={},
                    aliases=(),
                ),
                ModelToolDef(
                    model_tool_id=MODEL_TASK_CANCEL,
                    description="Cancel a previously scheduled task",
                    parameters={},
                    aliases=(),
                ),
                ModelToolDef(
                    model_tool_id=MODEL_TASK_LIST,
                    description="List scheduled tasks visible to the current agent scope",
                    parameters={},
                    aliases=(),
                ),
                ModelToolDef(
                    model_tool_id=MODEL_TASK_PAUSE,
                    description="Pause a scheduled task by exact task identifier",
                    parameters={},
                    aliases=(),
                ),
                ModelToolDef(
                    model_tool_id=MODEL_TASK_RESUME,
                    description="Resume a paused scheduled task by exact task identifier",
                    parameters={},
                    aliases=(),
                ),
                ModelToolDef(
                    model_tool_id=MODEL_TASK_SHOW,
                    description="Show one scheduled task and recent run history by exact task identifier",
                    parameters={},
                    aliases=(),
                ),
            ),
            runtime_bindings=(
                RuntimeBindingDef(
                    runtime_binding_id=RUNTIME_TASK_SCHEDULE,
                    model_tool_id=MODEL_TASK_SCHEDULE,
                    runtime_candidates=("task.schedule",),
                ),
                RuntimeBindingDef(
                    runtime_binding_id=RUNTIME_TASK_CONSOLIDATE_MEMORY,
                    model_tool_id=MODEL_TASK_CONSOLIDATE_MEMORY,
                    runtime_candidates=("task.consolidate_memory",),
                ),
                RuntimeBindingDef(
                    runtime_binding_id=RUNTIME_TASK_WATCH,
                    model_tool_id=MODEL_TASK_WATCH,
                    runtime_candidates=("task.watch",),
                ),
                RuntimeBindingDef(
                    runtime_binding_id=RUNTIME_TASK_CANCEL,
                    model_tool_id=MODEL_TASK_CANCEL,
                    runtime_candidates=("task.cancel",),
                ),
                RuntimeBindingDef(
                    runtime_binding_id=RUNTIME_TASK_LIST,
                    model_tool_id=MODEL_TASK_LIST,
                    runtime_candidates=("task.list",),
                ),
                RuntimeBindingDef(
                    runtime_binding_id=RUNTIME_TASK_PAUSE,
                    model_tool_id=MODEL_TASK_PAUSE,
                    runtime_candidates=("task.pause",),
                ),
                RuntimeBindingDef(
                    runtime_binding_id=RUNTIME_TASK_RESUME,
                    model_tool_id=MODEL_TASK_RESUME,
                    runtime_candidates=("task.resume",),
                ),
                RuntimeBindingDef(
                    runtime_binding_id=RUNTIME_TASK_SHOW,
                    model_tool_id=MODEL_TASK_SHOW,
                    runtime_candidates=("task.show",),
                ),
            ),
        )


REGISTRAR = TaskRegistrar()
