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
    MODEL_TASK_RUN_NOW,
    MODEL_TASK_UPDATE,
    MODEL_TASK_DELETE_HISTORY,
    MODEL_TASK_SCHEDULER_STATUS,
    MODEL_TASK_SCHEDULER_PAUSE,
    MODEL_TASK_SCHEDULER_RESUME,
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
    RUNTIME_TASK_RUN_NOW,
    RUNTIME_TASK_UPDATE,
    RUNTIME_TASK_DELETE_HISTORY,
    RUNTIME_TASK_SCHEDULER_STATUS,
    RUNTIME_TASK_SCHEDULER_PAUSE,
    RUNTIME_TASK_SCHEDULER_RESUME,
    RUNTIME_TASK_WATCH,
)

if TYPE_CHECKING:
    from openminion.modules.tool.runtime.registrar import ToolRegisterContext
    from openminion.modules.tool.registry import ToolRegistry


_WATCH_DESCRIPTION = (
    "Create a proactive monitoring watch backed by the cron scheduler. RSS, Atom, "
    "YouTube-feed, and GitHub PR requests require the matching typed routine object; "
    "do not create a plain watch for them. Use the declared schema fields rather than "
    "shorthand arguments. A continuous social feed sets description, "
    "check_instruction, alert_condition, interval_minutes, delivery='none', "
    'stop_on_condition=false, and routine={"routine_kind":"social_signal",'
    '"config":{"sources":[...],"topics":[...]}}.'
)


def _model_tools() -> tuple[ModelToolDef, ...]:
    descriptions = (
        (MODEL_TASK_SCHEDULE, "Schedule an agent instruction for later execution"),
        (
            MODEL_TASK_CONSOLIDATE_MEMORY,
            "Create a recurring memory-consolidation task backed by the cron scheduler",
        ),
        (MODEL_TASK_WATCH, _WATCH_DESCRIPTION),
        (MODEL_TASK_CANCEL, "Cancel a previously scheduled task"),
        (MODEL_TASK_LIST, "List scheduled tasks visible to the current agent scope"),
        (MODEL_TASK_PAUSE, "Pause a scheduled task by exact task identifier"),
        (MODEL_TASK_RESUME, "Resume a paused scheduled task by exact task identifier"),
        (
            MODEL_TASK_SHOW,
            "Show one scheduled task and recent run history by exact task identifier",
        ),
        (
            MODEL_TASK_RUN_NOW,
            "Queue one immediate run of an active task by exact identifier",
        ),
        (
            MODEL_TASK_UPDATE,
            "Update an idle scheduled task using its exact revision",
        ),
        (
            MODEL_TASK_DELETE_HISTORY,
            "Delete terminal run history for one exact task identifier",
        ),
        (
            MODEL_TASK_SCHEDULER_STATUS,
            "Show persisted scheduler pause state and daemon readiness",
        ),
        (
            MODEL_TASK_SCHEDULER_PAUSE,
            "Pause future scheduled task dispatch using an exact revision",
        ),
        (
            MODEL_TASK_SCHEDULER_RESUME,
            "Resume scheduled task dispatch using an exact revision",
        ),
    )
    return tuple(
        ModelToolDef(
            model_tool_id=model_tool_id,
            description=description,
            parameters={},
            aliases=(),
        )
        for model_tool_id, description in descriptions
    )


def _runtime_bindings() -> tuple[RuntimeBindingDef, ...]:
    bindings = (
        (RUNTIME_TASK_SCHEDULE, MODEL_TASK_SCHEDULE),
        (RUNTIME_TASK_CONSOLIDATE_MEMORY, MODEL_TASK_CONSOLIDATE_MEMORY),
        (RUNTIME_TASK_WATCH, MODEL_TASK_WATCH),
        (RUNTIME_TASK_CANCEL, MODEL_TASK_CANCEL),
        (RUNTIME_TASK_LIST, MODEL_TASK_LIST),
        (RUNTIME_TASK_PAUSE, MODEL_TASK_PAUSE),
        (RUNTIME_TASK_RESUME, MODEL_TASK_RESUME),
        (RUNTIME_TASK_SHOW, MODEL_TASK_SHOW),
        (RUNTIME_TASK_RUN_NOW, MODEL_TASK_RUN_NOW),
        (RUNTIME_TASK_UPDATE, MODEL_TASK_UPDATE),
        (RUNTIME_TASK_DELETE_HISTORY, MODEL_TASK_DELETE_HISTORY),
        (RUNTIME_TASK_SCHEDULER_STATUS, MODEL_TASK_SCHEDULER_STATUS),
        (RUNTIME_TASK_SCHEDULER_PAUSE, MODEL_TASK_SCHEDULER_PAUSE),
        (RUNTIME_TASK_SCHEDULER_RESUME, MODEL_TASK_SCHEDULER_RESUME),
    )
    return tuple(
        RuntimeBindingDef(
            runtime_binding_id=runtime_binding_id,
            model_tool_id=model_tool_id,
            runtime_candidates=(model_tool_id,),
        )
        for runtime_binding_id, model_tool_id in bindings
    )


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
            model_tools=_model_tools(),
            runtime_bindings=_runtime_bindings(),
        )


REGISTRAR = TaskRegistrar()
