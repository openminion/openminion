from types import SimpleNamespace
from threading import Lock
from typing import Any

from pydantic import Field

from openminion.modules.brain.constants import (
    BRAIN_INTERNAL_MODE_ACT_ORCHESTRATE,
    BRAIN_INTERNAL_MODE_EXECUTION_TARGET_DELEGATED,
)
from openminion.modules.brain.diagnostics.transitions import transition
from openminion.modules.brain.loop.orchestration import decide as decide_phase
from openminion.modules.brain.schemas import (
    ActionError,
    ActionResult,
    ActionMetrics,
    BudgetCounters,
    WorkingState,
    new_uuid,
    normalize_decomposed_subtasks,
)
from openminion.modules.brain.execution.loop_contracts import (
    ExecutionContext,
    ExecutionResult,
)
from openminion.modules.brain.execution.delegation_policy import (
    initialize_policy_facts,
    merge_child_policy_facts,
    record_child_policy_projection,
    record_result_aggregation,
)
from openminion.modules.brain.execution.worktree_children import (
    allocate_child_worktree,
    bind_runner_tool_workspace,
    child_verifier_evidence,
    finalize_child_worktree,
)
from openminion.modules.brain.execution.dispatch import invoke_decision_direct
from openminion.modules.brain.execution.preflight import (
    ModePreparation,
    ValidationResult,
)
from openminion.modules.brain.runtime.budget.strategy import (
    resolve_orchestrate_budget_settings,
)
from openminion.modules.brain.execution.child_tasks import (
    BudgetAllocator,
    CancellationPolicy,
    ChildResultCollector,
    ChildTaskPromoter,
    ChildTaskResult,
    ContextInheritancePolicy,
    DecomposePayload,
    ExecutionStrategy,
    FailurePolicy,
    ProgressMonitor,
    ResultSynthesizer,
    SubtaskResult,
    SubtaskSpec,
    SubtaskModeResolver,
    TaskWaitPolicy,
)
from .strategies import (
    AbortOnNewMessagePolicy,
    AcceptOrPlanResolver,
    AllInlinePromoter,
    BlockingWait,
    build_child_state,
    bounded_child_budget,
    CompletionRatioMonitor,
    decision_mode_name,
    debit_parent_budget,
    EqualSplitAllocator,
    FailFastPolicy,
    InlineAndPromotedCollector,
    LLMSynthesizer,
    merge_delegation_context,
    SequentialStrategy,
    SummaryInheritancePolicy,
    validate_dependency_context_capacity,
)
from .parallel import (
    ConservativeSideEffectPolicy,
    CyclicDependencyError,
    DefaultConcurrencyPolicy,
    EvenSplitBudgetAllocator,
    ParallelExecutionStrategy,
    TopologicalDependencyAnalyzer,
)
from .recovery import recover_child_failure
from openminion.modules.brain.loop.services import runner_from_context


ORCHESTRATE_MODE = BRAIN_INTERNAL_MODE_ACT_ORCHESTRATE
_ORCHESTRATE_PUBLIC_TAG = "[act:orchestrate]"
_DELEGATE_ASSIGNMENT_MODES = {
    BRAIN_INTERNAL_MODE_EXECUTION_TARGET_DELEGATED,
    "delegate",
    "delegated",
}


def _normalize_subtasks(raw: Any) -> list[SubtaskSpec]:
    normalized: list[SubtaskSpec] = []
    for item in list(normalize_decomposed_subtasks(raw) or []):
        if isinstance(item, SubtaskSpec):
            normalized.append(item)
        else:
            normalized.append(SubtaskSpec.model_validate(item))
    return normalized


def _subtask_failure_error(result: ExecutionResult) -> str:
    action_result = getattr(result, "action_result", None)
    action_error = getattr(action_result, "error", None)
    if action_error is not None:
        return (
            str(getattr(action_error, "message", "") or "").strip() or "Subtask failed."
        )
    return str(getattr(result, "message", "") or "").strip() or "Subtask failed."


def _topologically_sort_subtasks(subtasks: list[SubtaskSpec]) -> list[SubtaskSpec]:
    try:
        groups = TopologicalDependencyAnalyzer().analyze(subtasks)
    except CyclicDependencyError as exc:
        message = str(exc)
        if (
            message
            == "Parallel execution requires unique orchestrate subtask_id values."
        ):
            raise ValueError(
                "Orchestrate subtasks must have unique subtask_id values."
            ) from exc
        if message == "Orchestrate parallel graph contains a cycle.":
            raise ValueError(
                "Orchestrate subtasks contain a cyclic depends_on graph."
            ) from exc
        if message.startswith("Unknown dependency "):
            dependency, _, remainder = message.partition(" for subtask ")
            normalized = dependency.removeprefix("Unknown dependency ").strip()
            raise ValueError(
                f"Subtask {remainder.rstrip('.')} depends on unknown subtask {normalized}."
            ) from exc
        raise ValueError(message) from exc
    return [subtask for group in groups for subtask in group.subtasks]


def _parent_task_id_from_context(ctx: ExecutionContext) -> str:
    for candidate in (
        getattr(ctx.state, "task_backed_task_id", None),
        getattr(ctx.state, "trace_id", None),
        getattr(ctx.state, "session_id", None),
    ):
        normalized = str(candidate or "").strip()
        if normalized:
            return normalized
    return "orchestrate-parent"


def _delegate_assignment_from_subtask(
    subtask: SubtaskSpec,
    *,
    inherited_context: Any | None = None,
):
    inputs = subtask.inputs if isinstance(subtask.inputs, dict) else {}
    suggested = str(subtask.suggested_mode or "").strip().lower()
    target_agent_id = str(inputs.get("target_agent_id") or "").strip()
    if suggested not in _DELEGATE_ASSIGNMENT_MODES and not target_agent_id:
        return None
    return SimpleNamespace(
        mode=BRAIN_INTERNAL_MODE_EXECUTION_TARGET_DELEGATED,
        confidence=float(inputs.get("confidence") or 1.0),
        reason_code=str(inputs.get("reason_code") or "orchestrate_exact_delegate"),
        target_agent_id=target_agent_id,
        goal=str(inputs.get("goal") or subtask.goal).strip(),
        constraints=str(inputs.get("constraints") or subtask.constraints or ""),
        synthesize_result=bool(inputs.get("synthesize_result", False)),
        timeout_ms=inputs.get("timeout_ms"),
        delegation_context=merge_delegation_context(
            inherited_context, inputs.get("delegation_context")
        ),
        sub_intents=[subtask.goal],
        rationale=str(inputs.get("rationale") or ""),
        question=None,
        answer=None,
    )


def _normalized_subtask_budget(
    *,
    budget: BudgetCounters,
    subtask_count: int,
) -> BudgetCounters:
    if subtask_count <= 0:
        return budget.model_copy(deep=True)
    normalized = budget.model_copy(deep=True)
    # Child orchestrate runs execute inside the same outer turn, but they still
    if int(normalized.tokens or 0) > 0 and int(normalized.time_ms or 0) > 0:
        normalized.ticks = max(int(normalized.ticks or 0), int(subtask_count))
        normalized.tool_calls = max(int(normalized.tool_calls or 0), int(subtask_count))
    return normalized


class OrchestrateMode:
    mode_name = ORCHESTRATE_MODE
    mode_description = (
        "break a complex request into bounded subtasks when the work requires "
        "multiple distinct phases with different tools or approaches. Each "
        "subtask runs independently with its own mode selection. Use for "
        "multi-phase research, compare-and-contrast, or divide-and-conquer work."
    )
    mode_category = "workflow"
    has_prepare = True
    has_validate = True
    priority_hint = 60
    mode_thinking_policy = {
        "default_reasoning_profile": "detailed",
        "allowed_reasoning_profiles": ("minimal", "detailed"),
        "allow_request_override": True,
    }
    default_config = {
        "parallel_enabled": False,
        "parallel_writes_enabled": False,
        "max_parallel_workers": 3,
        "max_subtasks": 5,
        "max_decompose_depth": 1,
    }
    decision_payload_fields = {
        "subtasks": (list[SubtaskSpec], Field(..., min_length=2)),
    }

    def __init__(
        self,
        *,
        strategy: ExecutionStrategy | None = None,
        allocator: BudgetAllocator | None = None,
        promoter: ChildTaskPromoter | None = None,
        wait_policy: TaskWaitPolicy | None = None,
        collector: ChildResultCollector | None = None,
        resolver: SubtaskModeResolver | None = None,
        synthesizer: ResultSynthesizer | None = None,
        failure_policy: FailurePolicy | None = None,
        inheritance_policy: ContextInheritancePolicy | None = None,
        progress_monitor: ProgressMonitor | None = None,
        cancellation_policy: CancellationPolicy | None = None,
    ) -> None:
        self._explicit_strategy = strategy is not None
        self._explicit_allocator = allocator is not None
        self._strategy = strategy or SequentialStrategy()
        self._allocator = allocator or EqualSplitAllocator()
        self._promoter = promoter or AllInlinePromoter()
        self._wait_policy = wait_policy or BlockingWait()
        self._collector = collector or InlineAndPromotedCollector()
        self._resolver = resolver or AcceptOrPlanResolver()
        self._synthesizer = synthesizer or LLMSynthesizer()
        self._failure_policy = failure_policy or FailFastPolicy()
        self._inheritance = inheritance_policy or SummaryInheritancePolicy()
        self._monitor = progress_monitor or CompletionRatioMonitor()
        self._cancellation = cancellation_policy or AbortOnNewMessagePolicy()
        self._parallel_enabled = bool(self.default_config["parallel_enabled"])
        self._parallel_writes_enabled = bool(
            self.default_config["parallel_writes_enabled"]
        )
        self._max_parallel_workers = int(self.default_config["max_parallel_workers"])
        self._max_subtasks = int(self.default_config["max_subtasks"])
        self._max_decompose_depth = int(self.default_config["max_decompose_depth"])
        self._budget_lock = Lock()

    def apply_mode_config(self, *, config, runner, profile) -> None:
        del runner, profile
        settings = resolve_orchestrate_budget_settings(
            config=config,
            default_parallel_enabled=bool(self.default_config["parallel_enabled"]),
            default_parallel_writes_enabled=bool(
                self.default_config["parallel_writes_enabled"]
            ),
            default_max_parallel_workers=int(
                self.default_config["max_parallel_workers"]
            ),
            default_max_subtasks=int(self.default_config["max_subtasks"]),
            default_max_decompose_depth=int(self.default_config["max_decompose_depth"]),
        )
        self._parallel_enabled = settings.parallel_enabled
        self._parallel_writes_enabled = settings.parallel_writes_enabled
        self._max_parallel_workers = settings.max_parallel_workers
        self._max_subtasks = settings.max_subtasks
        self._max_decompose_depth = settings.max_decompose_depth
        if not self._explicit_strategy:
            if self._parallel_enabled:
                self._strategy = ParallelExecutionStrategy(
                    concurrency_policy=DefaultConcurrencyPolicy(
                        max_workers_config=self._max_parallel_workers,
                        enabled=True,
                    ),
                    side_effect_policy=ConservativeSideEffectPolicy(
                        parallel_writes_enabled=self._parallel_writes_enabled
                    ),
                )
            else:
                self._strategy = SequentialStrategy()
        if not self._explicit_allocator:
            self._allocator = (
                EvenSplitBudgetAllocator()
                if self._parallel_enabled
                else EqualSplitAllocator()
            )

    def _emit_status(
        self,
        ctx: ExecutionContext,
        *,
        mode_state: str,
        label: str,
        index: int | None = None,
        total: int | None = None,
    ) -> None:
        ctx.emit_status(
            source_phase="ACT",
            runtime_status="orchestrating",
            detail_text=label,
            mode="act",
            mode_state=mode_state,
            mode_label=label,
            mode_step_index=index,
            mode_step_total=total,
            payload={"act.profile": "orchestrate"},
        )

    def _reject_prepare(
        self, ctx: ExecutionContext, *, message: str
    ) -> ModePreparation:
        return ModePreparation(
            mode_result=ExecutionResult.from_step_output(
                ctx.respond(message=message, status="error")
            )
        )

    def prepare(
        self,
        ctx: ExecutionContext,
        *,
        emit_status_updates: bool = False,
    ) -> ModePreparation:
        subtasks = _normalize_subtasks(getattr(ctx.decision, "subtasks", []) or [])
        if len(subtasks) < 2:
            return self._reject_prepare(
                ctx,
                message="orchestrate requires at least two validated subtasks.",
            )
        payload = DecomposePayload(subtasks=subtasks)
        if len(payload.subtasks) > self._max_subtasks:
            return self._reject_prepare(
                ctx,
                message=(
                    f"orchestrate supports at most {self._max_subtasks} subtasks for this "
                    f"agent profile; received {len(payload.subtasks)}."
                ),
            )
        normalized: list[SubtaskSpec] = []
        for index, subtask in enumerate(payload.subtasks, start=1):
            updated = subtask.model_copy(
                update={
                    "subtask_id": str(subtask.subtask_id or f"subtask-{index}").strip(),
                    "suggested_mode": self._resolver.resolve(
                        subtask=subtask,
                        available_routes=["respond", "act"],
                    ),
                }
            )
            normalized.append(updated)
        try:
            normalized = _topologically_sort_subtasks(normalized)
            validate_dependency_context_capacity(normalized)
        except ValueError as exc:
            return self._reject_prepare(ctx, message=str(exc))
        if emit_status_updates:
            self._emit_status(
                ctx,
                mode_state="prepare_subtasks",
                label=f"{_ORCHESTRATE_PUBLIC_TAG} starting: {len(normalized)} subtasks",
                total=len(normalized),
            )
        ctx.state.child_tasks = {}
        ctx.state.child_task_order = [item.subtask_id for item in normalized]
        ctx.decision.subtasks = normalized
        return ModePreparation()

    def _decide_subtask(
        self,
        ctx: ExecutionContext,
        *,
        child_state: WorkingState,
        subtask: SubtaskSpec,
        prompt: str,
        inherited_context: Any | None = None,
    ):
        delegate_assignment = _delegate_assignment_from_subtask(
            subtask,
            inherited_context=inherited_context,
        )
        if delegate_assignment is not None:
            return delegate_assignment
        runner = runner_from_context(ctx)
        if runner is None:
            raise RuntimeError("OrchestrateMode requires runner-backed services")
        decide_override = getattr(runner, "_decide", None)
        if callable(decide_override):
            decision = decide_override(
                state=child_state,
                user_input=prompt,
                logger=ctx.logger,
            )
        else:
            decision = decide_phase(
                runner,
                state=child_state,
                user_input=prompt,
                logger=ctx.logger,
            )
        if (
            str(getattr(decision, "route", getattr(decision, "mode", "")) or "").strip()
            == ORCHESTRATE_MODE
        ):
            raise ValueError(
                "Orchestrate subtasks cannot recursively select orchestrate"
            )
        return decision

    def _result_from_mode_output(
        self,
        *,
        subtask: SubtaskSpec,
        mode_name: str,
        budget: BudgetCounters,
        child_state: WorkingState,
        result: ExecutionResult,
        child_artifact: dict[str, Any] | None = None,
    ) -> SubtaskResult:
        action_result = getattr(result, "action_result", None)
        action_status = str(getattr(action_result, "status", "") or "").strip().lower()
        status = "completed"
        if (
            result.status in {"error", "stopped"}
            or action_status
            in {
                "failed",
                "blocked",
                "timeout",
            }
            or result.status == "waiting_user"
        ):
            status = "failed"
        tokens_remaining = int(getattr(child_state.budgets_remaining, "tokens", 0) or 0)
        output = str(getattr(result, "message", "") or "").strip()
        if not output and action_result is not None:
            output = str(getattr(action_result, "summary", "") or "").strip()
        action_tokens = int(
            getattr(getattr(action_result, "metrics", None), "tokens_used", 0) or 0
        )
        return SubtaskResult(
            subtask_id=subtask.subtask_id,
            goal=subtask.goal,
            status=status,
            mode_used=mode_name,
            output=output,
            error=_subtask_failure_error(result) if status == "failed" else None,
            tokens_used=max(
                action_tokens,
                max(0, int(budget.tokens) - tokens_remaining),
            ),
            child_artifact=child_artifact,
        )

    def _invoke_child(
        self,
        ctx: ExecutionContext,
        *,
        runner: Any,
        child_state: WorkingState,
        subtask: SubtaskSpec,
        decision: Any,
        prompt: str,
    ) -> tuple[ExecutionResult, dict[str, Any] | None]:
        lease = allocate_child_worktree(subtask=subtask, child_state=child_state)
        result_status = "error"
        try:
            with bind_runner_tool_workspace(runner, lease=lease):
                result = invoke_decision_direct(
                    runner,
                    state=child_state,
                    decision=decision,
                    user_input=prompt,
                    logger=ctx.logger,
                    depth=1,
                )
            result_status = result.status
        finally:
            child_artifact = finalize_child_worktree(
                ctx,
                lease=lease,
                status=result_status,
                validation=child_verifier_evidence(result)
                if result_status != "error"
                else None,
            )
        return result, child_artifact

    def _execute_one_subtask(
        self,
        ctx: ExecutionContext,
        *,
        subtask: SubtaskSpec,
        budget: BudgetCounters,
        index: int,
        total: int,
        dependency_results: list[SubtaskResult],
    ) -> ChildTaskResult:
        child_context = self._inheritance.build_child_context(
            parent_state=ctx.state,
            subtask=subtask,
            dependency_results=dependency_results,
        )
        child_state = build_child_state(
            parent_state=ctx.state,
            child_budget=budget,
            child_context=child_context,
        )
        prompt = child_context.prompt or subtask.goal
        decision = self._decide_subtask(
            ctx,
            child_state=child_state,
            subtask=subtask,
            prompt=prompt,
            inherited_context=child_context.delegation_context,
        )
        mode_name = decision_mode_name(decision)
        self._emit_status(
            ctx,
            mode_state="execute_subtask",
            label=(
                f"{_ORCHESTRATE_PUBLIC_TAG} subtask {index}/{total}: "
                f'"{subtask.goal}" — {mode_name}'
            ),
            index=index,
            total=total,
        )
        runner = runner_from_context(ctx)
        if runner is None:
            raise RuntimeError("OrchestrateMode requires runner-backed services")
        result, child_artifact = self._invoke_child(
            ctx,
            runner=runner,
            child_state=child_state,
            subtask=subtask,
            decision=decision,
            prompt=prompt,
        )
        merge_child_policy_facts(ctx, child_state=child_state)
        subtask_result = self._result_from_mode_output(
            subtask=subtask,
            mode_name=mode_name,
            budget=budget,
            child_state=child_state,
            result=result,
            child_artifact=child_artifact,
        )
        debit_parent_budget(
            ctx,
            allocated=budget,
            child_state=child_state,
            tokens_used=subtask_result.tokens_used,
            lock=self._budget_lock,
        )
        self._emit_status(
            ctx,
            mode_state="subtask_result",
            label=(
                f"{_ORCHESTRATE_PUBLIC_TAG} subtask {index}/{total}: "
                f"{subtask_result.status} ({subtask_result.tokens_used} tokens)"
            ),
            index=index,
            total=total,
        )
        return ChildTaskResult(
            subtask_id=subtask.subtask_id,
            task_id=None,
            result=subtask_result,
            was_promoted=False,
        )

    def _persist_promoted_child_result(
        self,
        ctx: ExecutionContext,
        *,
        task_id: str,
        child_result: ChildTaskResult,
        parent_task_id: str,
    ) -> None:
        progress_payload = {
            "child_task_result": child_result.result.model_dump(mode="python"),
            "last_parent_task_id": parent_task_id,
            "message": child_result.result.output or child_result.result.error or "",
            "status": child_result.result.status,
        }
        ctx.update_task_progress(task_id=task_id, progress=progress_payload)
        record = ctx.get_task(task_id=task_id)
        current_state = str(getattr(record, "state", "") or "").strip().lower()
        if current_state in {"done", "failed", "cancelled", "paused"}:
            return
        if child_result.result.status == "completed":
            ctx.transition_task(task_id=task_id, to_state="done")
            return
        if child_result.result.status == "cancelled":
            ctx.transition_task(task_id=task_id, to_state="cancelled")
            return
        ctx.transition_task(
            task_id=task_id,
            to_state="failed",
            failure_reason=child_result.result.error,
        )

    def _execute_promoted_subtask(
        self,
        ctx: ExecutionContext,
        *,
        subtask: SubtaskSpec,
        budget: BudgetCounters,
        index: int,
        total: int,
        task_id: str,
        parent_task_id: str,
        dependency_results: list[SubtaskResult],
    ) -> ChildTaskResult:
        child_context = self._inheritance.build_child_context(
            parent_state=ctx.state,
            subtask=subtask,
            dependency_results=dependency_results,
        )
        child_state = build_child_state(
            parent_state=ctx.state,
            child_budget=budget,
            child_context=child_context,
        )
        child_state.task_backed_task_id = task_id
        prompt = child_context.prompt or subtask.goal
        decision = self._decide_subtask(
            ctx,
            child_state=child_state,
            subtask=subtask,
            prompt=prompt,
            inherited_context=child_context.delegation_context,
        )
        mode_name = decision_mode_name(decision)
        self._emit_status(
            ctx,
            mode_state="execute_subtask",
            label=(
                f'{_ORCHESTRATE_PUBLIC_TAG} subtask {index}/{total}: "{subtask.goal}" — '
                f"{mode_name} (promoted)"
            ),
            index=index,
            total=total,
        )
        runner = runner_from_context(ctx)
        if runner is None:
            raise RuntimeError("OrchestrateMode requires runner-backed services")
        result, child_artifact = self._invoke_child(
            ctx,
            runner=runner,
            child_state=child_state,
            subtask=subtask,
            decision=decision,
            prompt=prompt,
        )
        merge_child_policy_facts(ctx, child_state=child_state)
        subtask_result = self._result_from_mode_output(
            subtask=subtask,
            mode_name=mode_name,
            budget=budget,
            child_state=child_state,
            result=result,
            child_artifact=child_artifact,
        )
        debit_parent_budget(
            ctx,
            allocated=budget,
            child_state=child_state,
            tokens_used=subtask_result.tokens_used,
            lock=self._budget_lock,
        )
        child_result = ChildTaskResult(
            subtask_id=subtask.subtask_id,
            task_id=task_id,
            result=subtask_result,
            was_promoted=True,
        )
        self._persist_promoted_child_result(
            ctx,
            task_id=task_id,
            child_result=child_result,
            parent_task_id=parent_task_id,
        )
        waited = self._wait_policy.wait_for_child(
            task_id=task_id,
            task_service=ctx,
            timeout_ms=None,
        )
        self._emit_status(
            ctx,
            mode_state="subtask_result",
            label=(
                f"{_ORCHESTRATE_PUBLIC_TAG} subtask {index}/{total}: "
                f"{waited.result.status} ({waited.result.tokens_used} tokens)"
            ),
            index=index,
            total=total,
        )
        return waited

    def _finalize_result(
        self,
        ctx: ExecutionContext,
        *,
        synthesized: ExecutionResult,
        results: list[SubtaskResult],
        total: int,
        recovery: dict[str, Any] | None = None,
    ) -> ExecutionResult:
        completed = sum(1 for item in results if item.status == "completed")
        incomplete = [item for item in results if item.status != "completed"]
        failed = bool(incomplete)
        action_result = ActionResult(
            command_id=new_uuid(),
            status="failed" if failed else "success",
            summary=str(getattr(synthesized, "message", "") or "").strip(),
            outputs={
                "subtask_results": [
                    item.model_dump(mode="python", exclude_none=True)
                    for item in results
                ],
                "completed_subtasks": completed,
                "total_subtasks": total,
                "child_tasks": dict(ctx.state.child_tasks),
                "child_task_order": list(ctx.state.child_task_order),
                **({"child_recovery": recovery} if recovery else {}),
            },
            error=(
                ActionError(
                    code="orchestrate_partial_failure",
                    message="One or more required subtasks did not complete.",
                )
                if failed
                else None
            ),
            metrics=ActionMetrics(
                tokens_used=sum(item.tokens_used for item in results)
            ),
        )
        ctx.state.last_result = action_result
        ctx.state.active_mode_name = ORCHESTRATE_MODE
        transition(
            ctx.state,
            "task_failed" if failed else "task_completed",
            logger=ctx.logger,
        )
        return ExecutionResult(
            status="failed" if failed else "done",
            working_state=ctx.state,
            message=str(getattr(synthesized, "message", "") or "").strip(),
            action_result=action_result,
        )

    def _run_subtask(
        self,
        *,
        ctx: ExecutionContext,
        subtask: SubtaskSpec,
        budget: BudgetCounters,
        index: int,
        total: int,
        parent_task_id: str,
        completed_results: list[ChildTaskResult] | None = None,
    ) -> ChildTaskResult:
        budget = bounded_child_budget(budget, ctx.state.budgets_remaining)
        completed_by_id = {
            item.subtask_id: item.result for item in completed_results or []
        }
        dependency_results = [
            completed_by_id[subtask_id]
            for subtask_id in subtask.depends_on
            if subtask_id in completed_by_id
        ]
        should_promote = self._promoter.should_promote(subtask)
        record_child_policy_projection(
            ctx,
            flow="orchestrate_promoted" if should_promote else "orchestrate_inline",
            child_id=subtask.subtask_id,
            child_count=total,
            child_mode="async" if should_promote else "sync",
            parent_id=parent_task_id,
            seam_id="brain.orchestrate.child",
        )
        if should_promote:
            task_id = self._promoter.promote(
                subtask=subtask,
                parent_task_id=parent_task_id,
                task_service=ctx,
            )
            ctx.state.child_tasks[subtask.subtask_id] = task_id
            return self._execute_promoted_subtask(
                ctx,
                subtask=subtask,
                budget=budget,
                index=index,
                total=total,
                task_id=task_id,
                parent_task_id=parent_task_id,
                dependency_results=dependency_results,
            )
        ctx.state.child_tasks[subtask.subtask_id] = "inline"
        return self._execute_one_subtask(
            ctx,
            subtask=subtask,
            budget=budget,
            index=index,
            total=total,
            dependency_results=dependency_results,
        )

    def execute(self, ctx: ExecutionContext) -> ExecutionResult:
        preparation = self.prepare(ctx)
        if preparation.mode_result is not None:
            return preparation.mode_result
        subtasks = _normalize_subtasks(getattr(ctx.decision, "subtasks", []) or [])
        ctx.state.child_tasks = {}
        ctx.state.child_task_order = [item.subtask_id for item in subtasks]
        initialize_policy_facts(ctx)
        budgets = self._allocator.allocate(
            budget=_normalized_subtask_budget(
                budget=ctx.state.budgets_remaining,
                subtask_count=len(subtasks),
            ),
            subtask_count=len(subtasks),
        )
        parent_task_id = _parent_task_id_from_context(ctx)
        self._emit_status(
            ctx,
            mode_state="start",
            label=f"{_ORCHESTRATE_PUBLIC_TAG} starting: {len(subtasks)} subtasks",
            total=len(subtasks),
        )
        child_results = self._strategy.execute(
            ctx=ctx,
            subtasks=subtasks,
            budgets=budgets,
            run_subtask=lambda subtask, budget, index, total, completed: (
                self._run_subtask(
                    ctx=ctx,
                    subtask=subtask,
                    budget=budget,
                    index=index,
                    total=total,
                    parent_task_id=parent_task_id,
                    completed_results=completed,
                )
            ),
            failure_policy=self._failure_policy,
            progress_monitor=self._monitor,
            cancellation_policy=self._cancellation,
        )
        child_results, recovery = recover_child_failure(
            ctx,
            child_results=child_results,
            subtasks=subtasks,
            budgets=budgets,
            parent_task_id=parent_task_id,
            run_subtask=self._run_subtask,
            emit_recovery=lambda detail: self._emit_status(
                ctx,
                mode_state="child_recovery",
                label=f"{_ORCHESTRATE_PUBLIC_TAG} recovery: {detail}",
            ),
        )
        results = self._collector.collect(child_results)
        record_result_aggregation(
            ctx,
            flow="orchestrate_inline",
            parent_id=parent_task_id,
            seam_id="brain.orchestrate.results",
            results=results,
        )
        self._emit_status(
            ctx,
            mode_state="synthesis",
            label=f"{_ORCHESTRATE_PUBLIC_TAG} synthesis: combining {len(results)} results",
            total=len(subtasks),
        )
        synthesized = self._synthesizer.synthesize(ctx=ctx, results=results)
        final = self._finalize_result(
            ctx,
            synthesized=synthesized,
            results=results,
            total=len(subtasks),
            recovery=recovery,
        )
        completed = sum(1 for item in results if item.status == "completed")
        self._emit_status(
            ctx,
            mode_state="failed" if final.status == "failed" else "done",
            label=(
                f"{_ORCHESTRATE_PUBLIC_TAG} "
                f"{'failed' if final.status == 'failed' else 'done'}: "
                f"{completed}/{len(subtasks)} subtasks completed"
            ),
            index=completed,
            total=len(subtasks),
        )
        return final

    def validate(
        self,
        ctx: ExecutionContext,
        *,
        preparation: ModePreparation | None = None,
    ) -> ValidationResult | None:
        del preparation
        subtasks = _normalize_subtasks(getattr(ctx.decision, "subtasks", []) or [])
        action_result = getattr(ctx.state, "last_result", None)
        outputs = (
            getattr(action_result, "outputs", {}) if action_result is not None else {}
        )
        if (
            "subtask_results" not in outputs
            and str(getattr(ctx.state, "active_mode_name", "") or "").strip()
            != ORCHESTRATE_MODE
        ):
            return None
        raw_results = list(outputs.get("subtask_results", []) or [])
        results = [SubtaskResult.model_validate(item) for item in raw_results]
        if len(results) != len(subtasks):
            return ValidationResult(
                passed=False,
                feedback="Orchestrate synthesis did not preserve every subtask result.",
                should_retry=True,
                code="missing_subtask_results",
                details={
                    "expected": len(subtasks),
                    "actual": len(results),
                },
            )
        result_goals = {item.goal for item in results}
        expected_goals = {item.goal for item in subtasks}
        if result_goals != expected_goals:
            return ValidationResult(
                passed=False,
                feedback="Orchestrate result goals do not match the requested subtasks.",
                should_retry=True,
                code="subtask_goal_mismatch",
            )
        failed_results = [item for item in results if item.status != "completed"]
        if failed_results:
            return ValidationResult(
                passed=False,
                feedback="One or more required orchestrated subtasks did not complete.",
                should_retry=False,
                code="orchestrate_subtask_failed",
                details={
                    "failed_subtasks": [item.subtask_id for item in failed_results]
                },
            )
        if not str(getattr(action_result, "summary", "") or "").strip():
            return ValidationResult(
                passed=False,
                feedback="Orchestrate synthesis produced an empty final summary.",
                should_retry=True,
                code="empty_orchestrate_summary",
            )
        return ValidationResult(passed=True)


__all__ = ["OrchestrateMode", "ORCHESTRATE_MODE"]
