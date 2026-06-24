from typing import Any

from pydantic import Field

from openminion.modules.brain.constants import (
    BRAIN_INTERNAL_MODE_ACT_ORCHESTRATE,
)
from openminion.modules.brain.diagnostics.transitions import (
    set_status_unchecked,
    transition,
)
from openminion.modules.brain.loop.orchestration import decide as decide_phase
from openminion.modules.brain.schemas import (
    ActionError,
    ActionResult,
    ActionMetrics,
    BudgetCounters,
    RespondDecision,
    WorkingState,
    new_uuid,
    normalize_decomposed_subtasks,
)
from openminion.modules.brain.execution.loop_contracts import (
    ExecutionContext,
    ExecutionResult,
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
    CompletionRatioMonitor,
    EqualSplitAllocator,
    FailFastPolicy,
    InlineAndPromotedCollector,
    LLMSynthesizer,
    SequentialStrategy,
    SummaryInheritancePolicy,
)
from .parallel import (
    ConservativeSideEffectPolicy,
    DefaultConcurrencyPolicy,
    EvenSplitBudgetAllocator,
    ParallelExecutionStrategy,
)
from openminion.modules.brain.loop.services import runner_from_context


ORCHESTRATE_MODE = BRAIN_INTERNAL_MODE_ACT_ORCHESTRATE
_ORCHESTRATE_PUBLIC_TAG = "[act:orchestrate]"


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
    by_id = {item.subtask_id: item for item in subtasks}
    if len(by_id) != len(subtasks):
        raise ValueError("Orchestrate subtasks must have unique subtask_id values.")
    in_degree = {item.subtask_id: 0 for item in subtasks}
    outgoing: dict[str, list[str]] = {item.subtask_id: [] for item in subtasks}
    for item in subtasks:
        for dependency in item.depends_on:
            normalized = str(dependency or "").strip()
            if not normalized:
                continue
            if normalized not in by_id:
                raise ValueError(
                    f"Subtask {item.subtask_id!r} depends on unknown subtask {normalized!r}."
                )
            outgoing[normalized].append(item.subtask_id)
            in_degree[item.subtask_id] += 1
    ready = [item.subtask_id for item in subtasks if in_degree[item.subtask_id] == 0]
    ordered: list[SubtaskSpec] = []
    while ready:
        current = ready.pop(0)
        ordered.append(by_id[current])
        for successor in outgoing[current]:
            in_degree[successor] -= 1
            if in_degree[successor] == 0:
                ready.append(successor)
    if len(ordered) != len(subtasks):
        raise ValueError("Orchestrate subtasks contain a cyclic depends_on graph.")
    return ordered


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
        payload = DecomposePayload(subtasks=subtasks)
        if len(payload.subtasks) > self._max_subtasks:
            return self._reject_prepare(
                ctx,
                message=(
                    f"orchestrate supports at most {self._max_subtasks} subtasks for this "
                    f"agent profile; received {len(payload.subtasks)}."
                ),
            )
        available_routes = []
        runner = runner_from_context(ctx)
        if runner is not None:
            available_routes = ["respond", "act"]
        normalized: list[SubtaskSpec] = []
        for index, subtask in enumerate(payload.subtasks, start=1):
            updated = subtask.model_copy(
                update={
                    "subtask_id": str(subtask.subtask_id or f"subtask-{index}").strip(),
                    "suggested_mode": self._resolver.resolve(
                        subtask=subtask,
                        available_routes=available_routes or ["respond", "act"],
                    ),
                }
            )
            normalized.append(updated)
        try:
            normalized = _topologically_sort_subtasks(normalized)
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

    def _build_child_state(
        self,
        *,
        parent_state: WorkingState,
        child_budget: BudgetCounters,
        subtask: SubtaskSpec,
    ) -> WorkingState:
        child_context = self._inheritance.build_child_context(
            parent_state=parent_state,
            subtask=subtask,
        )
        child_state = parent_state.model_copy(deep=True)
        child_state.goal = child_context.goal
        child_state.active_skill_id = child_context.active_skill_id
        child_state.constraints = list(child_context.constraints or [])
        child_state.plan = None
        child_state.cursor = 0
        set_status_unchecked(child_state, "active", reason="bootstrap")
        child_state.budgets_remaining = child_budget.model_copy(deep=True)
        child_state.last_command_id = None
        child_state.last_result = None
        child_state.step_outputs = []
        child_state.adaptive_satisfied_intent_ids = []
        child_state.last_adaptive_revision_checkpoint = None
        child_state.pending_jobs = []
        child_state.memory_candidates = []
        child_state.idempotency_cache = {}
        child_state.child_tasks = {}
        child_state.child_task_order = []
        child_state.pending_clarify_items = []
        child_state.unresolved_clarify_items = []
        child_state.clarify_responses = {}
        child_state.open_questions = []
        child_state.active_mode_name = None
        child_state.llm_calls_used = 0
        child_state.decision_sub_intents = []
        child_state.decision_sub_intent_refs = []
        child_state.decision_feasibility_state = {}
        child_state.decision_feasibility_report = None
        child_state.intent_execution_states = []
        child_state.task_backed_task_id = None
        child_state.task_backed_checkpoint_id = None
        child_state.task_backed_resume_state = {}
        return child_state

    def _fallback_decision(self, subtask: SubtaskSpec):
        if str(getattr(subtask, "suggested_mode", "") or "").strip() == "respond":
            return RespondDecision(
                confidence=0.6,
                reason_code="orchestrate_subtask_fallback",
                respond_kind="answer",
                sub_intents=[subtask.goal],
                answer=subtask.goal,
            )
        from openminion.modules.brain.schemas.decisions import ActDecision

        return ActDecision(
            confidence=0.7,
            reason_code="orchestrate_subtask_fallback",
            sub_intents=[subtask.goal],
            rationale=subtask.goal,
        )

    def _decide_subtask(
        self,
        ctx: ExecutionContext,
        *,
        child_state: WorkingState,
        subtask: SubtaskSpec,
        prompt: str,
    ):
        runner = runner_from_context(ctx)
        if runner is None:
            return self._fallback_decision(subtask)
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
            return self._fallback_decision(subtask)
        return decision

    def _result_from_mode_output(
        self,
        *,
        subtask: SubtaskSpec,
        mode_name: str,
        budget: BudgetCounters,
        child_state: WorkingState,
        result: ExecutionResult,
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
        return SubtaskResult(
            subtask_id=subtask.subtask_id,
            goal=subtask.goal,
            status=status,
            mode_used=mode_name,
            output=output,
            error=_subtask_failure_error(result) if status == "failed" else None,
            tokens_used=max(0, int(budget.tokens) - tokens_remaining),
        )

    def _execute_one_subtask(
        self,
        ctx: ExecutionContext,
        *,
        subtask: SubtaskSpec,
        budget: BudgetCounters,
        index: int,
        total: int,
    ) -> ChildTaskResult:
        child_context = self._inheritance.build_child_context(
            parent_state=ctx.state,
            subtask=subtask,
        )
        child_state = self._build_child_state(
            parent_state=ctx.state,
            child_budget=budget,
            subtask=subtask,
        )
        prompt = child_context.prompt or subtask.goal
        decision = self._decide_subtask(
            ctx,
            child_state=child_state,
            subtask=subtask,
            prompt=prompt,
        )
        mode_name = (
            str(
                getattr(decision, "route", getattr(decision, "mode", "")) or "act"
            ).strip()
            or "act"
        )
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
        result = invoke_decision_direct(
            runner,
            state=child_state,
            decision=decision,
            user_input=prompt,
            logger=ctx.logger,
            depth=1,
        )
        subtask_result = self._result_from_mode_output(
            subtask=subtask,
            mode_name=mode_name,
            budget=budget,
            child_state=child_state,
            result=result,
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
    ) -> ChildTaskResult:
        child_state = self._build_child_state(
            parent_state=ctx.state,
            child_budget=budget,
            subtask=subtask,
        )
        child_state.task_backed_task_id = task_id
        child_context = self._inheritance.build_child_context(
            parent_state=ctx.state,
            subtask=subtask,
        )
        prompt = child_context.prompt or subtask.goal
        decision = self._decide_subtask(
            ctx,
            child_state=child_state,
            subtask=subtask,
            prompt=prompt,
        )
        mode_name = (
            str(
                getattr(decision, "route", getattr(decision, "mode", "")) or "act"
            ).strip()
            or "act"
        )
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
        result = invoke_decision_direct(
            runner,
            state=child_state,
            decision=decision,
            user_input=prompt,
            logger=ctx.logger,
            depth=1,
        )
        subtask_result = self._result_from_mode_output(
            subtask=subtask,
            mode_name=mode_name,
            budget=budget,
            child_state=child_state,
            result=result,
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
    ) -> ExecutionResult:
        completed = sum(1 for item in results if item.status == "completed")
        failed = any(item.status == "failed" for item in results)
        action_result = ActionResult(
            command_id=new_uuid(),
            status="failed" if failed else "success",
            summary=str(getattr(synthesized, "message", "") or "").strip(),
            outputs={
                "subtask_results": [item.model_dump(mode="python") for item in results],
                "completed_subtasks": completed,
                "total_subtasks": total,
                "child_tasks": dict(ctx.state.child_tasks),
                "child_task_order": list(ctx.state.child_task_order),
            },
            error=(
                ActionError(
                    code="orchestrate_partial_failure",
                    message="One or more subtasks failed.",
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
        transition(ctx.state, "task_completed", logger=ctx.logger)
        return ExecutionResult(
            status="done",
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
    ) -> ChildTaskResult:
        if self._promoter.should_promote(subtask):
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
            )
        ctx.state.child_tasks[subtask.subtask_id] = "inline"
        return self._execute_one_subtask(
            ctx,
            subtask=subtask,
            budget=budget,
            index=index,
            total=total,
        )

    def execute(self, ctx: ExecutionContext) -> ExecutionResult:
        preparation = self.prepare(ctx)
        if preparation.mode_result is not None:
            return preparation.mode_result
        subtasks = _normalize_subtasks(getattr(ctx.decision, "subtasks", []) or [])
        ctx.state.child_tasks = {}
        ctx.state.child_task_order = [item.subtask_id for item in subtasks]
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
            run_subtask=lambda subtask, budget, index, total: self._run_subtask(
                ctx=ctx,
                subtask=subtask,
                budget=budget,
                index=index,
                total=total,
                parent_task_id=parent_task_id,
            ),
            failure_policy=self._failure_policy,
            progress_monitor=self._monitor,
            cancellation_policy=self._cancellation,
        )
        results = self._collector.collect(child_results)
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
        )
        completed = sum(1 for item in results if item.status == "completed")
        self._emit_status(
            ctx,
            mode_state="done",
            label=(
                f"{_ORCHESTRATE_PUBLIC_TAG} done: {completed}/{len(subtasks)} "
                "subtasks completed"
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
        if not str(getattr(action_result, "summary", "") or "").strip():
            return ValidationResult(
                passed=False,
                feedback="Orchestrate synthesis produced an empty final summary.",
                should_retry=True,
                code="empty_orchestrate_summary",
            )
        return ValidationResult(passed=True)


__all__ = ["OrchestrateMode", "ORCHESTRATE_MODE"]
