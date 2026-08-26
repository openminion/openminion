"""One bounded, model-authored recovery after an orchestration child fails."""

from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, model_validator

from openminion.modules.brain.constants import (
    BRAIN_INTERNAL_MODE_EXECUTION_TARGET_DELEGATED,
)
from openminion.modules.brain.execution.child_tasks import (
    ChildTaskResult,
    SubtaskSpec,
)
from openminion.modules.brain.execution.loop_contracts import ExecutionContext
from openminion.modules.brain.loop.services import runner_from_context
from openminion.modules.brain.schemas import BudgetCounters


class ChildFailureDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    disposition: Literal["continue", "retry_once", "reassign_exact", "stop"]
    target_agent_id: str = ""

    @model_validator(mode="after")
    def validate_target(self) -> "ChildFailureDecision":
        self.target_agent_id = self.target_agent_id.strip()
        if self.disposition == "reassign_exact" and not self.target_agent_id:
            raise ValueError("reassign_exact requires target_agent_id")
        if self.disposition != "reassign_exact" and self.target_agent_id:
            raise ValueError("target_agent_id is only valid for reassign_exact")
        return self


def _failure_decision(
    ctx: ExecutionContext,
    failed: ChildTaskResult,
) -> ChildFailureDecision:
    runner = runner_from_context(ctx)
    llm_api = getattr(runner, "llm_api", None) if runner is not None else None
    if llm_api is None or not callable(getattr(llm_api, "call_structured", None)):
        raise RuntimeError("Orchestration child recovery requires an LLM service")
    profiles = getattr(getattr(runner, "profile", None), "llm_profiles", None)
    raw = llm_api.call_structured(
        model=str(getattr(profiles, "act_model", "") or "act-default"),
        purpose="act",
        context={
            "instruction": (
                "Choose exactly one disposition for the failed child. Use "
                "reassign_exact only with a known exact agent ID."
            ),
            "failed_child": failed.result.model_dump(mode="python", exclude_none=True),
            "allowed_dispositions": [
                "continue",
                "retry_once",
                "reassign_exact",
                "stop",
            ],
        },
        schema=ChildFailureDecision,
    )
    return ChildFailureDecision.model_validate(raw)


def recover_child_failure(
    ctx: ExecutionContext,
    *,
    child_results: list[ChildTaskResult],
    subtasks: list[SubtaskSpec],
    budgets: list[BudgetCounters],
    parent_task_id: str,
    run_subtask: Callable[..., ChildTaskResult],
    emit_recovery: Callable[[str], None],
) -> tuple[list[ChildTaskResult], dict[str, Any] | None]:
    failed_index = next(
        (i for i, item in enumerate(child_results) if item.result.status == "failed"),
        None,
    )
    if failed_index is None:
        return child_results, None

    failed = child_results[failed_index]
    decision = _failure_decision(ctx, failed)
    fact: dict[str, Any] = {
        "disposition": decision.disposition,
        "failed_subtask_id": failed.subtask_id,
    }
    if decision.target_agent_id:
        fact["target_agent_id"] = decision.target_agent_id
    emit_recovery(f"{decision.disposition} for {failed.subtask_id}")

    recovered = list(child_results)
    if decision.disposition in {"retry_once", "reassign_exact"}:
        original = next(
            item for item in subtasks if item.subtask_id == failed.subtask_id
        )
        retry_subtask = original
        if decision.disposition == "reassign_exact":
            retry_subtask = original.model_copy(
                update={
                    "suggested_mode": BRAIN_INTERNAL_MODE_EXECUTION_TARGET_DELEGATED,
                    "inputs": {
                        **dict(original.inputs),
                        "target_agent_id": decision.target_agent_id,
                    },
                }
            )
        index = subtasks.index(original)
        retry_result = run_subtask(
            ctx=ctx,
            subtask=retry_subtask,
            budget=budgets[index],
            index=index + 1,
            total=len(subtasks),
            parent_task_id=parent_task_id,
            completed_results=recovered,
        )
        recovered[failed_index] = retry_result
        fact["outcome"] = retry_result.result.status
        if retry_result.result.status == "failed":
            return recovered, fact
    elif decision.disposition == "stop":
        fact["outcome"] = "stopped"
        return recovered, fact

    completed_ids = {item.subtask_id for item in recovered}
    for index, (subtask, budget) in enumerate(
        zip(subtasks, budgets, strict=True), start=1
    ):
        if subtask.subtask_id not in completed_ids:
            recovered.append(
                run_subtask(
                    ctx=ctx,
                    subtask=subtask,
                    budget=budget,
                    index=index,
                    total=len(subtasks),
                    parent_task_id=parent_task_id,
                    completed_results=recovered,
                )
            )
    fact.setdefault("outcome", "continued")
    return recovered, fact


__all__ = ["ChildFailureDecision", "recover_child_failure"]
