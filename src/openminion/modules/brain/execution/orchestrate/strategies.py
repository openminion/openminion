from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from openminion.modules.brain.constants import (
    BRAIN_DECISION_ROUTE_ACT,
    BRAIN_INTERNAL_MODE_ACT_ORCHESTRATE,
    BRAIN_INTERNAL_MODE_ACT_RESEARCH,
    BRAIN_INTERNAL_MODE_EXECUTION_TARGET_DELEGATED,
)
from openminion.modules.brain.execution.public_taxonomy import (
    public_mode_name_for_mode_name,
)
from openminion.modules.brain.retry import call_structured_with_retry
from openminion.modules.brain.schemas import BudgetCounters
from openminion.modules.brain.execution.loop_contracts import ExecutionResult
from openminion.modules.brain.loop.services import runner_from_context
from openminion.modules.brain.execution.child_tasks import (
    BudgetAllocator,
    CancellationPolicy,
    ChildResultCollector,
    ChildContext,
    ChildTaskPromoter,
    ChildTaskResult,
    ContextInheritancePolicy,
    ExecutionStrategy,
    FailureAction,
    FailurePolicy,
    ProgressMonitor,
    ResultSynthesizer,
    SubtaskResult,
    SubtaskSpec,
    SubtaskModeResolver,
    TaskWaitPolicy,
)


class SequentialStrategy(ExecutionStrategy):
    def execute(
        self,
        *,
        ctx,
        subtasks: list[SubtaskSpec],
        budgets: list[BudgetCounters],
        run_subtask,
        failure_policy: FailurePolicy,
        progress_monitor: ProgressMonitor,
        cancellation_policy: CancellationPolicy,
    ) -> list[ChildTaskResult]:
        results: list[ChildTaskResult] = []
        total = len(subtasks)
        if total != len(budgets):
            raise ValueError("subtasks and budgets must have the same length")
        for index, (subtask, budget) in enumerate(zip(subtasks, budgets), start=1):
            if cancellation_policy.should_cancel(
                ctx=ctx,
                results=list(results),
                attempts=index,
            ):
                results.append(
                    ChildTaskResult(
                        subtask_id=subtask.subtask_id or f"subtask-{index}",
                        task_id=None,
                        was_promoted=False,
                        result=SubtaskResult(
                            subtask_id=subtask.subtask_id or f"subtask-{index}",
                            goal=subtask.goal,
                            status="cancelled",
                            mode_used=str(subtask.suggested_mode or "act"),
                            error="Cancelled before execution.",
                        ),
                    )
                )
                break
            result = run_subtask(subtask, budget, index, total)
            results.append(result)
            if result.result.status == "failed":
                action = failure_policy.on_failure(subtask=subtask, result=result)
                if action == FailureAction.ABORT:
                    break
            if progress_monitor.is_stalled(results=list(results), attempts=index):
                break
        return results


class EqualSplitAllocator(BudgetAllocator):
    def allocate(
        self,
        *,
        budget: BudgetCounters,
        subtask_count: int,
    ) -> list[BudgetCounters]:
        if subtask_count <= 0:
            return []

        def _split(value: int) -> list[int]:
            base = int(value // subtask_count)
            remainder = int(value % subtask_count)
            values = [base for _ in range(subtask_count)]
            values[-1] += remainder
            return values

        ticks = _split(int(budget.ticks))
        tool_calls = _split(int(budget.tool_calls))
        a2a_calls = _split(int(budget.a2a_calls))
        tokens = _split(int(budget.tokens))
        time_ms = _split(int(budget.time_ms))
        return [
            BudgetCounters(
                ticks=ticks[idx],
                tool_calls=tool_calls[idx],
                a2a_calls=a2a_calls[idx],
                tokens=tokens[idx],
                time_ms=time_ms[idx],
            )
            for idx in range(subtask_count)
        ]


class AcceptOrPlanResolver(SubtaskModeResolver):
    def resolve(
        self,
        *,
        subtask: SubtaskSpec,
        available_routes: list[str],
    ) -> str:
        suggested = str(subtask.suggested_mode or "").strip()
        if not suggested:
            return "act"
        public_suggested = public_mode_name_for_mode_name(suggested) or suggested
        visible_modes = {
            public_mode_name_for_mode_name(mode_name) or str(mode_name or "").strip()
            for mode_name in available_routes
        }
        if (
            public_suggested
            and public_suggested in visible_modes
            and suggested != BRAIN_INTERNAL_MODE_ACT_ORCHESTRATE
        ):
            return suggested
        return BRAIN_DECISION_ROUTE_ACT


class AllInlinePromoter(ChildTaskPromoter):
    def should_promote(self, subtask: SubtaskSpec) -> bool:
        del subtask
        return False

    def promote(
        self,
        subtask: SubtaskSpec,
        parent_task_id: str,
        task_service: Any,
    ) -> str:
        del subtask, parent_task_id, task_service
        raise NotImplementedError("AllInlinePromoter never promotes subtasks")


class HeuristicPromoter(ChildTaskPromoter):
    def __init__(self, *, goal_length_threshold: int = 200) -> None:
        self._goal_length_threshold = int(goal_length_threshold)

    def should_promote(self, subtask: SubtaskSpec) -> bool:
        suggested = str(subtask.suggested_mode or "").strip().lower()
        if suggested in {
            BRAIN_INTERNAL_MODE_ACT_RESEARCH,
            BRAIN_INTERNAL_MODE_EXECUTION_TARGET_DELEGATED,
            "delegate",
            "research",
        }:
            return True
        return len(str(subtask.goal or "").strip()) > self._goal_length_threshold

    def promote(
        self,
        subtask: SubtaskSpec,
        parent_task_id: str,
        task_service: Any,
    ) -> str:
        state = getattr(task_service, "state", None)
        record = task_service.create_task(
            session_id=str(getattr(state, "session_id", "") or "").strip(),
            mode_name=str(subtask.suggested_mode or BRAIN_DECISION_ROUTE_ACT).strip()
            or BRAIN_DECISION_ROUTE_ACT,
            goal=subtask.goal,
            agent_id=getattr(state, "agent_id", None),
            metadata={
                "parent_task_id": str(parent_task_id or "").strip(),
                "subtask_id": subtask.subtask_id,
                "subtask_goal": subtask.goal,
                "suggested_mode": str(subtask.suggested_mode or "").strip(),
                "depends_on": list(subtask.depends_on),
            },
        )
        return str(getattr(record, "task_id", "") or "").strip()


class _SynthesisResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str = Field(..., min_length=1)


class LLMSynthesizer(ResultSynthesizer):
    def synthesize(
        self,
        *,
        ctx,
        results: list[SubtaskResult],
    ) -> ExecutionResult:
        runner = runner_from_context(ctx)
        llm_api = getattr(runner, "llm_api", None) if runner is not None else None
        model = "summarize-default"
        profile = getattr(runner, "profile", None) if runner is not None else None
        llm_profiles = getattr(profile, "llm_profiles", None)
        if llm_profiles is not None:
            model = str(getattr(llm_profiles, "summarize_model", model) or model)
        context = {
            "user_input": ctx.user_input,
            "subtasks": [
                {
                    "subtask_id": item.subtask_id,
                    "goal": item.goal,
                    "status": item.status,
                    "mode_used": item.mode_used,
                    "output": item.output,
                    "error": item.error,
                }
                for item in results
            ],
            "hints": {
                "instruction": (
                    "Synthesize the subtask results into one concise final answer. "
                    "Preserve partial failure information when present."
                )
            },
        }
        if llm_api is not None and callable(getattr(llm_api, "call_structured", None)):
            raw = call_structured_with_retry(
                llm_api,
                model=model,
                purpose="summarize",
                context=context,
                schema=_SynthesisResponse,
            )
            answer = _SynthesisResponse.model_validate(raw).answer
        else:
            lines = []
            for item in results:
                payload = item.output or item.error or item.status
                lines.append(f"{item.goal}: {payload}")
            answer = "\n".join(lines).strip() or "No subtask results were produced."
        return ExecutionResult(
            status="done",
            working_state=ctx.state,
            message=answer,
        )


class BlockingWait(TaskWaitPolicy):
    def wait_for_child(
        self,
        task_id: str,
        task_service: Any,
        timeout_ms: int | None,
    ) -> ChildTaskResult:
        del timeout_ms
        record = task_service.get_task(task_id=task_id)
        if record is None:
            subtask_id = str(task_id or "").strip() or "missing-task"
            result = SubtaskResult(
                subtask_id=subtask_id,
                goal=subtask_id,
                status="failed",
                mode_used="act",
                error=f"Promoted child task {task_id!r} was not found.",
            )
            return ChildTaskResult(
                subtask_id=subtask_id,
                task_id=task_id,
                result=result,
                was_promoted=True,
            )
        metadata = dict(getattr(record, "metadata", {}) or {})
        progress = dict(metadata.get("progress", {}) or {})
        payload = progress.get("child_task_result") or metadata.get("child_task_result")
        if isinstance(payload, dict):
            result = SubtaskResult.model_validate(payload)
        else:
            subtask_id = str(metadata.get("subtask_id") or task_id).strip() or task_id
            goal = str(
                metadata.get("subtask_goal") or metadata.get("goal") or ""
            ).strip()
            mode_used = (
                str(
                    metadata.get("suggested_mode") or metadata.get("mode_name") or "act"
                ).strip()
                or "act"
            )
            state_text = str(getattr(record, "state", "") or "").strip().lower()
            result = SubtaskResult(
                subtask_id=subtask_id,
                goal=goal or subtask_id,
                status="completed" if state_text == "done" else "failed",
                mode_used=mode_used,
                output=str(progress.get("message") or "").strip(),
                error=str(getattr(record, "failure_reason", "") or "").strip() or None,
            )
        return ChildTaskResult(
            subtask_id=result.subtask_id,
            task_id=task_id,
            result=result,
            was_promoted=True,
        )


class InlineAndPromotedCollector(ChildResultCollector):
    def collect(self, results: list[ChildTaskResult]) -> list[SubtaskResult]:
        return [item.result for item in results]


class FailFastPolicy(FailurePolicy):
    def on_failure(
        self,
        *,
        subtask: SubtaskSpec,
        result: ChildTaskResult,
    ) -> FailureAction:
        del subtask, result
        return FailureAction.ABORT


class SummaryInheritancePolicy(ContextInheritancePolicy):
    def build_child_context(
        self,
        *,
        parent_state,
        subtask: SubtaskSpec,
    ) -> ChildContext:
        summary_parts: list[str] = []
        goal = str(getattr(parent_state, "goal", "") or "").strip()
        if goal:
            summary_parts.append(f"Parent goal: {goal}")
        last_result = getattr(parent_state, "last_result", None)
        last_summary = str(getattr(last_result, "summary", "") or "").strip()
        if last_summary:
            summary_parts.append(f"Latest result: {last_summary}")
        constraints = list(getattr(parent_state, "constraints", []) or [])
        if str(subtask.constraints or "").strip():
            constraints.append(str(subtask.constraints).strip())
        prompt_parts = list(summary_parts)
        prompt_parts.append(f"Subtask goal: {subtask.goal}")
        if constraints:
            prompt_parts.append("Constraints: " + "; ".join(constraints))
        return ChildContext(
            prompt="\n".join(part for part in prompt_parts if part).strip(),
            goal=subtask.goal,
            summary="\n".join(summary_parts).strip(),
            constraints=constraints,
            active_skill_id=getattr(parent_state, "active_skill_id", None),
        )


class CompletionRatioMonitor(ProgressMonitor):
    def is_stalled(
        self,
        *,
        results: list[ChildTaskResult],
        attempts: int,
    ) -> bool:
        completed = 0
        for item in results:
            candidate = getattr(item, "result", item)
            if getattr(candidate, "status", None) == "completed":
                completed += 1
        return attempts >= 2 and completed == 0


class AbortOnNewMessagePolicy(CancellationPolicy):
    def should_cancel(
        self,
        *,
        ctx,
        results: list[ChildTaskResult],
        attempts: int,
    ) -> bool:
        del results, attempts
        marker = getattr(ctx.options, "decompose_cancel_requested", False)
        if bool(marker):
            return True
        runner = runner_from_context(ctx)
        if runner is None:
            return False
        session_api = getattr(runner, "session_api", None)
        probe = getattr(session_api, "has_pending_user_input", None)
        if callable(probe):
            try:
                return bool(
                    probe(ctx.state.session_id, getattr(ctx.state, "trace_id", None))
                )
            except TypeError:
                return bool(probe(ctx.state.session_id))
        return False


__all__ = [
    "AbortOnNewMessagePolicy",
    "AcceptOrPlanResolver",
    "AllInlinePromoter",
    "BlockingWait",
    "CompletionRatioMonitor",
    "EqualSplitAllocator",
    "FailFastPolicy",
    "HeuristicPromoter",
    "InlineAndPromotedCollector",
    "LLMSynthesizer",
    "SequentialStrategy",
    "SummaryInheritancePolicy",
]
