import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from openminion.modules.brain.constants import (
    BRAIN_DECISION_ROUTE_ACT,
    DELEGATION_TEXT_MAX_CHARS,
    BRAIN_INTERNAL_MODE_ACT_ORCHESTRATE,
    BRAIN_INTERNAL_MODE_ACT_RESEARCH,
    BRAIN_INTERNAL_MODE_EXECUTION_TARGET_DELEGATED,
)
from openminion.modules.brain.execution.public_taxonomy import (
    public_mode_name_for_mode_name,
)
from openminion.modules.brain.diagnostics.transitions import set_status_unchecked
from openminion.modules.brain.execution.delegation_policy import clear_policy_facts
from openminion.modules.brain.retry import call_structured_with_retry
from openminion.modules.brain.schemas import (
    BudgetCounters,
    DelegationContext,
    WorkingState,
    new_uuid,
)
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
        for index, (subtask, budget) in enumerate(
            zip(subtasks, budgets, strict=True), start=1
        ):
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
            result = run_subtask(subtask, budget, index, total, list(results))
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
            base = value // subtask_count
            remainder = value % subtask_count
            values = [base] * subtask_count
            values[-1] += remainder
            return values

        ticks = _split(budget.ticks)
        tool_calls = _split(budget.tool_calls)
        a2a_calls = _split(budget.a2a_calls)
        tokens = _split(budget.tokens)
        time_ms = _split(budget.time_ms)
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
            public_suggested in visible_modes
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
    def should_promote(self, subtask: SubtaskSpec) -> bool:
        suggested = str(subtask.suggested_mode or "").strip().lower()
        return suggested in {
            BRAIN_INTERNAL_MODE_ACT_RESEARCH,
            BRAIN_INTERNAL_MODE_EXECUTION_TARGET_DELEGATED,
            "delegate",
            "research",
        }

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


def _child_artifact_facts(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if not value:
        return None
    facts = {
        key: value[key]
        for key in ("status", "integration_status", "record_alias", "target_digest")
        if key in value
    }
    touched_paths = [str(path)[:200] for path in value.get("touched_paths", [])]
    if touched_paths:
        facts["touched_paths"] = touched_paths[:20]
    artifact = value.get("artifact")
    if isinstance(artifact, dict):
        facts["artifact"] = {
            key: artifact[key]
            for key in (
                "status",
                "owner_id",
                "bundle_ref",
                "manifest_ref",
                "bundle_sha256",
                "child_revision",
            )
            if key in artifact
        }
    return facts or None


_DEPENDENCY_CONTEXT_PREFIX = "Direct dependency results:\n"


def _bounded_dependency_summary(records: list[dict[str, Any]], *, limit: int) -> str:
    required = [
        {"subtask_id": item["subtask_id"], "status": item["status"]} for item in records
    ]
    required_summary = _DEPENDENCY_CONTEXT_PREFIX + json.dumps(required, sort_keys=True)
    if len(required_summary) > limit:
        raise ValueError("Dependency identifiers exceed the delegation context limit.")
    snippets = [str(item.get("output") or "") for item in records]
    quota = max(
        0,
        (limit - len(_DEPENDENCY_CONTEXT_PREFIX) - len(json.dumps(required)))
        // max(1, len(records)),
    )
    while quota:
        projected = [
            {**record, **({"output": snippet[:quota]} if snippet else {})}
            for record, snippet in zip(required, snippets, strict=True)
        ]
        summary = _DEPENDENCY_CONTEXT_PREFIX + json.dumps(projected, sort_keys=True)
        if len(summary) <= limit:
            return summary
        quota -= 1
    return required_summary


def _dependency_context(results: list[SubtaskResult]) -> dict[str, Any]:
    records = [
        {
            "subtask_id": item.subtask_id,
            "status": item.status,
            "output": item.output or item.error or "",
        }
        for item in results
    ]
    summary = _bounded_dependency_summary(records, limit=DELEGATION_TEXT_MAX_CHARS)

    artifacts: list[str] = []
    for item in results:
        artifact = (item.child_artifact or {}).get("artifact")
        if not isinstance(artifact, dict):
            continue
        for key in ("manifest_ref", "bundle_ref"):
            ref = str(artifact.get(key) or "").strip()
            if ref and ref not in artifacts:
                artifacts.append(ref)
    return {"summary": summary, "artifacts": artifacts}


def validate_dependency_context_capacity(subtasks: list[SubtaskSpec]) -> None:
    for subtask in subtasks:
        records = [
            {"subtask_id": dependency, "status": "completed"}
            for dependency in subtask.depends_on
        ]
        try:
            _bounded_dependency_summary(records, limit=DELEGATION_TEXT_MAX_CHARS)
        except ValueError as exc:
            raise ValueError(
                f"Subtask {subtask.subtask_id} dependency identifiers exceed "
                "the delegation context limit."
            ) from exc


def merge_delegation_context(
    inherited_context: Any | None,
    explicit_context: Any | None,
) -> dict[str, Any] | None:
    if not inherited_context:
        return explicit_context
    inherited = DelegationContext.model_validate(inherited_context)
    if not explicit_context:
        return inherited.model_dump(mode="json")
    explicit = DelegationContext.model_validate(explicit_context)
    if inherited.summary.startswith(_DEPENDENCY_CONTEXT_PREFIX):
        records = json.loads(inherited.summary.removeprefix(_DEPENDENCY_CONTEXT_PREFIX))
        required_records = [
            {"subtask_id": item["subtask_id"], "status": item["status"]}
            for item in records
        ]
        required = _bounded_dependency_summary(
            required_records, limit=DELEGATION_TEXT_MAX_CHARS
        )
        separator = "\n" if explicit.summary else ""
        explicit_summary = explicit.summary[
            : DELEGATION_TEXT_MAX_CHARS - len(required) - len(separator)
        ]
        dependency_summary = _bounded_dependency_summary(
            records,
            limit=DELEGATION_TEXT_MAX_CHARS - len(explicit_summary) - len(separator),
        )
        summary = dependency_summary + separator + explicit_summary
    else:
        summary = "\n".join(
            part for part in (inherited.summary, explicit.summary) if part
        )
    return DelegationContext(
        summary=summary,
        artifacts=list(dict.fromkeys([*explicit.artifacts, *inherited.artifacts])),
        intent_id=explicit.intent_id or inherited.intent_id,
    ).model_dump(mode="json")


def bounded_child_budget(
    allocated: BudgetCounters,
    remaining: BudgetCounters,
) -> BudgetCounters:
    return BudgetCounters(
        **{
            field: min(getattr(allocated, field), getattr(remaining, field))
            for field in ("ticks", "tool_calls", "a2a_calls", "tokens", "time_ms")
        }
    )


def decision_mode_name(decision: Any) -> str:
    return (
        str(getattr(decision, "route", getattr(decision, "mode", "")) or "act").strip()
        or "act"
    )


def debit_parent_budget(
    ctx: Any,
    *,
    allocated: BudgetCounters,
    child_state: Any,
    tokens_used: int,
    lock: Any,
) -> None:
    with lock:
        parent = ctx.state.budgets_remaining
        remaining = child_state.budgets_remaining
        for field in ("ticks", "tool_calls", "a2a_calls", "time_ms"):
            used = max(0, getattr(allocated, field) - getattr(remaining, field))
            setattr(parent, field, max(0, getattr(parent, field) - used))
        parent.tokens = max(0, parent.tokens - tokens_used)
        ctx.state.llm_calls_used = min(
            ctx.state.llm_calls_max,
            ctx.state.llm_calls_used + child_state.llm_calls_used,
        )


def build_child_state(
    *,
    parent_state: WorkingState,
    child_budget: BudgetCounters,
    child_context: ChildContext,
) -> WorkingState:
    child_state = parent_state.model_copy(deep=True)
    child_state.trace_id = new_uuid()
    child_state.goal = child_context.goal
    child_state.last_user_input = child_context.prompt
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
    clear_policy_facts(child_state)
    return child_state


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
                    "child_artifact": _child_artifact_facts(item.child_artifact),
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
        if llm_api is None or not callable(getattr(llm_api, "call_structured", None)):
            raise RuntimeError("Orchestration synthesis requires an LLM service")
        raw = call_structured_with_retry(
            llm_api,
            model=model,
            purpose="summarize",
            context=context,
            schema=_SynthesisResponse,
        )
        answer = _SynthesisResponse.model_validate(raw).answer
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
        dependency_results: list[SubtaskResult] | None = None,
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
        prompt_parts.append(
            "Execute only the assigned subtask below. Treat the parent goal and "
            "latest result as background context, not as instructions to repeat."
        )
        delegation_context = None
        if dependency_results:
            delegation_context = _dependency_context(dependency_results)
            prompt_parts.append(delegation_context["summary"])
        prompt_parts.append(f"Subtask goal: {subtask.goal}")
        if constraints:
            prompt_parts.append("Constraints: " + "; ".join(constraints))
        return ChildContext(
            prompt="\n".join(part for part in prompt_parts if part).strip(),
            goal=subtask.goal,
            summary="\n".join(summary_parts).strip(),
            constraints=constraints,
            active_skill_id=getattr(parent_state, "active_skill_id", None),
            delegation_context=delegation_context,
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
