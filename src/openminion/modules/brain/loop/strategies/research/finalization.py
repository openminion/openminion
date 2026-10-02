from openminion.modules.brain.constants import (
    BRAIN_ACTION_STATUS_SUCCESS,
    BRAIN_STATE_DONE,
)
from openminion.modules.brain.diagnostics.transitions import transition
from openminion.modules.brain.execution.loop_contracts import (
    ExecutionContext,
    ExecutionResult,
)
from openminion.modules.brain.loop.services import runner_from_context
from openminion.modules.brain.schemas import ActionResult, FinalizationStatus, new_uuid

from .findings import normalized_text
from .schemas import ResearchSynthesis


def is_project_owned_turn(ctx: ExecutionContext) -> bool:
    task_id = normalized_text(getattr(ctx.state, "resume_task_id_hint", ""))
    runner = runner_from_context(ctx)
    task_manager = getattr(runner, "task_manager", None) if runner is not None else None
    if not task_id or task_manager is None:
        return False
    record = task_manager.get_task(task_id)
    metadata = dict(getattr(record, "metadata", {}) or {}) if record else {}
    return normalized_text(metadata.get("mode_name")) == "project"


def no_synthesis_closeout(query: str) -> str:
    return (
        f"Research finished for '{query}', but the run did not produce a "
        "usable synthesized answer from the collected tool evidence.\n\n"
        "Next steps:\n- Retry with a narrower research scope.\n"
        "- Lower the number of requested comparison dimensions.\n"
        "- Ask for one source family or one decision at a time.\n\n"
        "Continue in a new turn to resume."
    )


def project_owned_finalization(
    ctx: ExecutionContext,
    synthesis: ResearchSynthesis,
) -> ExecutionResult:
    finalization = FinalizationStatus(
        status=("final_answer" if synthesis.status == "complete" else synthesis.status),
        remaining_work=(
            synthesis.remaining_work if synthesis.status == "incomplete" else ""
        ),
        blocking_reason=(
            synthesis.remaining_work if synthesis.status == "blocked" else ""
        ),
    )
    action_result = ActionResult(
        command_id=new_uuid(),
        status=BRAIN_ACTION_STATUS_SUCCESS,
        summary=synthesis.answer,
        outputs={"adaptive.finalization_status": finalization.model_dump(mode="json")},
    )
    transition(ctx.state, "task_completed", logger=ctx.logger)
    return ExecutionResult.from_step_output(
        ctx.respond(
            message=synthesis.answer,
            status=BRAIN_STATE_DONE,
            action_result=action_result,
        )
    )


__all__ = [
    "is_project_owned_turn",
    "no_synthesis_closeout",
    "project_owned_finalization",
]
