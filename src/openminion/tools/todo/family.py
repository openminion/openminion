from openminion.modules.tool.contracts.model_ids import MODEL_TODO_WRITE
from openminion.modules.tool.framework import ToolDecl, ToolFamilySpec

from .plugin import (
    PlanAddArgs,
    PlanClearArgs,
    PlanCompleteArgs,
    PlanListArgs,
    PlanSetArgs,
    PlanUpdateArgs,
    TodoWriteArgs,
    _h_add,
    _h_clear,
    _h_complete,
    _h_list,
    _h_set,
    _h_todo_write,
    _h_update,
)

TODO_FAMILY = ToolFamilySpec(
    module_id="plan",
    min_scope_default="WRITE_SAFE",
    common_tags=("plugin", "plan"),
    common_capabilities=("plan",),
    tools=(
        ToolDecl(
            name="plan.set",
            args_model=PlanSetArgs,
            handler=_h_set,
            description="Initialize the agent's session plan with a list of items.",
            idempotent=False,
            capabilities=("write_safe",),
        ),
        ToolDecl(
            name="plan.add",
            args_model=PlanAddArgs,
            handler=_h_add,
            description="Append or insert a new item into the current plan.",
            idempotent=False,
            capabilities=("write_safe",),
        ),
        ToolDecl(
            name="plan.update",
            args_model=PlanUpdateArgs,
            handler=_h_update,
            description="Set the status of an existing plan item.",
            idempotent=True,
            capabilities=("write_safe",),
        ),
        ToolDecl(
            name="plan.complete",
            args_model=PlanCompleteArgs,
            handler=_h_complete,
            description="Mark a plan item as done.",
            idempotent=True,
            capabilities=("write_safe",),
        ),
        ToolDecl(
            name="plan.list",
            args_model=PlanListArgs,
            handler=_h_list,
            description="Return the current plan and its summary.",
            min_scope="READ_ONLY",
            idempotent=True,
            capabilities=("read_only",),
        ),
        ToolDecl(
            name="plan.clear",
            args_model=PlanClearArgs,
            handler=_h_clear,
            description="Drop the current plan for this session.",
            idempotent=True,
            capabilities=("write_safe",),
        ),
        ToolDecl(
            name=MODEL_TODO_WRITE,
            args_model=TodoWriteArgs,
            handler=_h_todo_write,
            description="Replace the session checklist with structured todos.",
            idempotent=False,
            capabilities=("write_safe",),
        ),
    ),
)


PLAN_FAMILY = TODO_FAMILY

__all__ = ["TODO_FAMILY", "PLAN_FAMILY"]
