from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from openminion.base.time import utc_now_iso as _iso_now_utc
import traceback
from typing import Any

from pydantic import Field

from openminion.modules.brain.checkpoint import (
    CheckpointEnvelope,
    SimpleCheckpointMixin,
)
from openminion.modules.brain.constants import (
    BRAIN_INTERNAL_MODE_ACT_RESEARCH,
    BRAIN_ACT_PROFILE_GENERAL,
    BRAIN_ACT_PROFILE_RESEARCH,
    BRAIN_DECISION_ROUTE_ACT,
    BRAIN_STATE_DONE,
    BRAIN_STATE_ERROR,
    BRAIN_STATE_WAITING_USER,
    RESEARCH_PUBLIC_TAG as _RESEARCH_PUBLIC_TAG,
)
from openminion.modules.brain.config import (
    RESEARCH_CHECKPOINT_INTERVAL,
    RESEARCH_MAX_ITERATIONS,
    RESEARCH_MAX_RESUME_COUNT,
)
from openminion.modules.brain.diagnostics.transitions import (
    set_status_unchecked,
    transition,
)
from openminion.modules.brain.schemas import (
    BudgetCounters,
    Plan,
    ResearchConvergenceConfig,
    ResearchConvergenceCounters,
    ResearchConvergenceSignal,
    WorkingState,
    compose_progress_signal,
    compose_research_convergence_signal,
)
from openminion.modules.brain.execution.loop_contracts import (
    ExecutionContext,
    ExecutionResult,
)
from openminion.modules.brain.execution.dispatch import invoke_decision_direct
from openminion.modules.brain.execution.lifecycle import set_phase
from openminion.modules.brain.execution.preflight import (
    ModePreparation,
    ValidationResult,
)
from openminion.modules.brain.runtime.budget.strategy import (
    resolve_research_budget_settings,
)
from openminion.modules.brain.loop.services import runner_from_context
from openminion.modules.brain.checkpoint.contracts import TaskProgress
from openminion.modules.brain.runner.resume import (
    ExponentialBackoffResumePolicy,
    schedule_backoff_resume,
    schedule_recurring_resume,
)
from .checkpoint import (
    build_checkpoint_state as _build_checkpoint_state,
    normalize_checkpoint_state as _normalize_checkpoint_state,
)
from .findings import (
    build_pause_partial_answer as _build_pause_partial_answer,
    evidence_dates_from_action_result as _evidence_dates_from_action_result,
    evidence_dates_from_working_state as _evidence_dates_from_working_state,
    meaningful_partial_texts as _meaningful_partial_texts,
    normalized_text as _normalized_text,
    render_temporal_fact_lines as _render_temporal_fact_lines_impl,
    usable_child_action_result_text as _usable_child_action_result_text,
    usable_child_result_text as _usable_child_result_text,
    usable_child_working_state_text as _usable_child_working_state_text,
)
from .schemas import ResearchFinding
from openminion.modules.brain.constants import STATE_KEY_TASK_BACKED_RESUME
from openminion.base.constants import STATE_KEY_WORKING

RESEARCH_MODE = BRAIN_INTERNAL_MODE_ACT_RESEARCH


@contextmanager
def _non_recursive_child_profile(runner: Any):
    profile = getattr(runner, "profile", None)
    if profile is None or not hasattr(profile, "default_act_profile"):
        yield
        return
    original = getattr(profile, "default_act_profile", None)
    try:
        setattr(profile, "default_act_profile", None)
        yield
    finally:
        setattr(profile, "default_act_profile", original)


def _render_temporal_fact_lines(findings: list[dict[str, Any]]) -> list[str]:
    return _render_temporal_fact_lines_impl(findings, now_iso_fn=_iso_now_utc)


class ResearchMode(SimpleCheckpointMixin):
    CHECKPOINT_VERSION = 1
    mode_name = RESEARCH_MODE
    mode_description = (
        "iteratively search, gather, and synthesize information from multiple "
        "sources when the answer requires discovery — the number of searches "
        "depends on what is found, not a fixed plan. Persists across turns "
        "with checkpoints, resume, and progress tracking."
    )
    mode_category = "task_backed"
    has_prepare = True
    has_validate = True
    has_resume = True
    priority_hint = 55
    mode_thinking_policy = {
        "default_reasoning_profile": "detailed",
        "allowed_reasoning_profiles": ("minimal", "detailed"),
        "allow_request_override": True,
    }
    default_config = {
        "checkpoint_interval": RESEARCH_CHECKPOINT_INTERVAL,
        "max_resume_count": RESEARCH_MAX_RESUME_COUNT,
        "max_research_iterations": RESEARCH_MAX_ITERATIONS,
    }
    decision_payload_fields = {
        "research_query": (str, Field(default="")),
        "research_scope": (str, Field(default="")),
    }

    def __init__(self) -> None:
        self._checkpoint_interval = RESEARCH_CHECKPOINT_INTERVAL
        self._max_resume_count = RESEARCH_MAX_RESUME_COUNT
        self._max_research_iterations = RESEARCH_MAX_ITERATIONS
        self._resume_policy = ExponentialBackoffResumePolicy()
        self._query = ""
        self._next_iteration = 0
        self._findings: list[dict[str, Any]] = []
        self._resume_count = 0
        self._convergence_config = ResearchConvergenceConfig()

    def apply_mode_config(self, *, config, runner, profile) -> None:
        del runner, profile
        settings = resolve_research_budget_settings(
            config=config,
            default_checkpoint_interval=RESEARCH_CHECKPOINT_INTERVAL,
            default_max_resume_count=RESEARCH_MAX_RESUME_COUNT,
            default_max_research_iterations=RESEARCH_MAX_ITERATIONS,
        )
        self._checkpoint_interval = settings.checkpoint_interval
        self._max_resume_count = settings.max_resume_count
        self._max_research_iterations = settings.max_research_iterations

    def prepare(
        self,
        ctx: ExecutionContext,
        *,
        emit_status_updates: bool = False,
    ) -> ModePreparation:
        del ctx, emit_status_updates
        return ModePreparation()

    def validate(
        self,
        ctx: ExecutionContext,
        *,
        preparation: ModePreparation | None = None,
    ) -> ValidationResult | None:
        del preparation
        query = self._query_from_context(ctx)
        if query:
            return ValidationResult(passed=True)
        return ValidationResult(
            passed=False,
            feedback="Research mode requires a non-empty research_query.",
            code="missing_research_query",
        )

    def _checkpoint_task_metadata(self, ctx: ExecutionContext) -> dict[str, Any] | None:
        query = self._query or self._query_from_context(ctx)
        if not query:
            return None
        return {"query": query, "objective": query}

    def _checkpoint_cursor_from_payload(
        self,
        payload: dict[str, Any],
        *,
        fallback: int = 0,
    ) -> int:
        value = payload.get("next_iteration")
        if value is not None:
            try:
                return max(0, int(value))
            except (TypeError, ValueError):
                pass
        return super()._checkpoint_cursor_from_payload(payload, fallback=fallback)

    def snapshot_state(self) -> dict[str, Any]:
        return _build_checkpoint_state(
            query=self._query,
            next_iteration=self._next_iteration,
            findings=self._findings,
            resume_count=self._resume_count,
        )

    def restore_state(self, payload: dict[str, Any]) -> None:
        migrated = _normalize_checkpoint_state(dict(payload or {}))
        self._query = _normalized_text(migrated.get("query"))
        self._next_iteration = int(migrated.get("next_iteration", 0) or 0)
        self._findings = [
            ResearchFinding.model_validate(item).model_dump(mode="python")
            for item in list(migrated.get("findings", []) or [])
        ]
        self._resume_count = int(migrated.get("resume_count", 0) or 0)

    def checkpoint(self, ctx: ExecutionContext, state: dict[str, Any]) -> str:
        self.restore_state(state)
        return super().checkpoint(ctx, self.snapshot_state())

    def resume(self, ctx: ExecutionContext, checkpoint_id: str) -> dict[str, Any]:
        task_id = _normalized_text(getattr(ctx.state, "task_backed_task_id", "") or "")
        if not task_id:
            return {"_resume_error": "Missing task-backed task ID."}
        resumed = self._load_resume_payload(ctx, checkpoint_id)
        if resumed is None:
            latest = ctx.get_latest_checkpoint(task_id=task_id)
            if latest is None:
                return {}
            latest_checkpoint_id, latest_state = latest
            if _normalized_text(latest_checkpoint_id) != _normalized_text(
                checkpoint_id
            ):
                return {
                    "_resume_error": (
                        f"Checkpoint {checkpoint_id!r} is unavailable for task {task_id!r}."
                    )
                }
            try:
                CheckpointEnvelope.model_validate(latest_state)
                return {}
            except Exception:
                migrated = _normalize_checkpoint_state(dict(latest_state or {}))
                self.restore_state(migrated)
                migrated_checkpoint_id = self._save_checkpoint(
                    ctx,
                    cursor=self._next_iteration,
                )
                if migrated_checkpoint_id:
                    ctx.state.task_backed_checkpoint_id = migrated_checkpoint_id
                resumed = self._set_resume_state(
                    ctx,
                    payload=self.snapshot_state(),
                    cursor=self._next_iteration,
                )
        if not resumed:
            return {}
        if resumed.get("_resume_error"):
            return resumed
        record = ctx.get_task(task_id=task_id)
        metadata = getattr(record, "metadata", {}) if record is not None else {}
        resume_count = int(metadata.get("resume_count", 0) or 0) + 1
        if resume_count > self._max_resume_count:
            return {
                "_resume_error": (
                    f"Resume limit exceeded for research task {task_id!r}: "
                    f"{resume_count} > {self._max_resume_count}."
                )
            }
        migrated = {
            key: value
            for key, value in resumed.items()
            if not key.startswith("_checkpoint_")
        }
        if (
            self._refresh_schedule(record) is not None
            and int(migrated.get("next_iteration", 0) or 0)
            >= self._max_research_iterations
        ):
            query = _normalized_text(
                metadata.get("query")
                or metadata.get("objective")
                or migrated.get("query")
            )
            migrated = _build_checkpoint_state(
                query=query,
                next_iteration=0,
                findings=[],
                resume_count=resume_count,
            )
        else:
            migrated["resume_count"] = resume_count
        self.restore_state(migrated)
        progress = dict(metadata.get("progress", {}) or {})
        progress["resume_count"] = resume_count
        progress["message"] = f"Resuming research from {checkpoint_id}."
        progress["last_checkpoint_id"] = _normalized_text(
            getattr(ctx.state, "task_backed_checkpoint_id", "") or checkpoint_id
        )
        ctx.update_task_progress(task_id=task_id, progress=progress)
        return self._set_resume_state(
            ctx,
            payload=self.snapshot_state(),
            cursor=self._next_iteration,
        )

    def report_progress(self, ctx: ExecutionContext, progress: TaskProgress) -> None:
        task_id = _normalized_text(getattr(ctx.state, "task_backed_task_id", "") or "")
        if not task_id:
            return
        payload = progress.model_dump(mode="json", exclude_none=True)
        ctx.update_task_progress(task_id=task_id, progress=payload)
        ctx.emit_status(
            source_phase="ACT",
            runtime_status="active",
            detail_text=progress.message or f"Completed iteration {progress.phase}.",
            mode=BRAIN_DECISION_ROUTE_ACT,
            mode_state=progress.phase,
            mode_label=f"{_RESEARCH_PUBLIC_TAG} iteration {progress.phase}",
            payload={
                "act.profile": BRAIN_ACT_PROFILE_RESEARCH,
                "completion_pct": progress.completion_pct,
                "last_checkpoint_id": progress.last_checkpoint_id,
                "partial_results_count": len(progress.partial_results),
            },
        )

    def emit_partial_result(self, ctx: ExecutionContext, result: str) -> None:
        text = _normalized_text(result)
        if not text:
            return
        ctx.emit_status(
            source_phase="ACT",
            runtime_status="active",
            detail_text=text,
            mode=BRAIN_DECISION_ROUTE_ACT,
            mode_state="partial_result",
            mode_label=f"{_RESEARCH_PUBLIC_TAG} partial result",
            payload={
                "act.profile": BRAIN_ACT_PROFILE_RESEARCH,
                "partial_result": text,
            },
        )

    def cancel(self, ctx: ExecutionContext, reason: str) -> ExecutionResult:
        task_id = _normalized_text(getattr(ctx.state, "task_backed_task_id", "") or "")
        resume_state = dict(getattr(ctx.state, STATE_KEY_TASK_BACKED_RESUME, {}) or {})
        findings: list[dict[str, Any]] = list(resume_state.get("findings", []) or [])
        if task_id and resume_state:
            checkpoint_id = self.checkpoint(ctx, resume_state)
            ctx.state.task_backed_checkpoint_id = checkpoint_id
        if task_id:
            ctx.transition_task(task_id=task_id, to_state="cancelled")
        transition(ctx.state, "execution_stopped", logger=ctx.logger)
        message = _normalized_text(reason) or "Research cancelled."
        if findings:
            partial_texts = [
                _normalized_text(f.get("content")) for f in findings if f.get("content")
            ]
            if partial_texts:
                message = f"{message} Partial findings: {' | '.join(partial_texts)}"
        return ExecutionResult.from_step_output(
            ctx.respond(message=message, status="stopped")
        )

    def execute(self, ctx: ExecutionContext) -> ExecutionResult:
        query = self._query_from_context(ctx)
        if not query:
            return ExecutionResult.from_step_output(
                ctx.respond(
                    message="Research mode needs a concrete research_query before it can run.",
                    status=BRAIN_STATE_WAITING_USER,
                )
            )

        self._query = query
        task_id = _normalized_text(self._init_checkpoint(ctx) or "")
        checkpoint_state = dict(
            getattr(ctx.state, STATE_KEY_TASK_BACKED_RESUME, {}) or {}
        )

        if checkpoint_state.get("_resume_error"):
            transition(ctx.state, "fatal_error", logger=ctx.logger)
            if task_id:
                ctx.transition_task(
                    task_id=task_id,
                    to_state="failed",
                    failure_reason=_normalized_text(checkpoint_state["_resume_error"]),
                )
            return ExecutionResult.from_step_output(
                ctx.respond(
                    message=_normalized_text(checkpoint_state["_resume_error"]),
                    status=BRAIN_STATE_ERROR,
                )
            )

        if not checkpoint_state:
            self.restore_state(
                _build_checkpoint_state(
                    query=query,
                    next_iteration=0,
                    findings=[],
                    resume_count=0,
                )
            )
            checkpoint_state = self._set_resume_state(
                ctx,
                payload=self.snapshot_state(),
                cursor=self._next_iteration,
            )
        else:
            restored = {
                key: value
                for key, value in checkpoint_state.items()
                if not key.startswith("_checkpoint_")
            }
            self.restore_state(restored)

        findings: list[dict[str, Any]] = list(self._findings)
        resume_count = self._resume_count
        started_index = self._next_iteration
        convergence_hint = ""
        max_iterations = self._max_research_iterations

        for iteration in range(started_index, max_iterations):
            set_phase(
                ctx,
                state=ctx.state,
                phase="ACT",
                logger=ctx.logger,
                mode=BRAIN_DECISION_ROUTE_ACT,
                mode_state=f"iteration_{iteration}",
                mode_label=(
                    f"{_RESEARCH_PUBLIC_TAG} iteration {iteration + 1} of "
                    f"{max_iterations}"
                ),
                mode_step_index=iteration + 1,
                mode_step_total=max_iterations,
                payload={
                    "act.profile": BRAIN_ACT_PROFILE_RESEARCH,
                },
            )

            finding = self._execute_search_iteration(
                ctx,
                iteration=iteration,
                query=query,
                findings_so_far=findings,
                convergence_hint=convergence_hint,
            )
            findings.append(finding.model_dump(mode="python"))
            self._findings = list(findings)
            self._next_iteration = iteration + 1
            self._resume_count = resume_count
            checkpoint_state = self._set_resume_state(
                ctx,
                payload=self.snapshot_state(),
                cursor=self._next_iteration,
            )
            checkpoint_id: str | None = None
            if (iteration + 1) % max(1, self._checkpoint_interval) == 0:
                checkpoint_id = self._save_checkpoint(
                    ctx,
                    cursor=self._next_iteration,
                )

            completion_pct = float(iteration + 1) / float(max_iterations)
            partial_texts = [
                _normalized_text(f.get("content")) for f in findings if f.get("content")
            ]
            progress = TaskProgress(
                phase=f"iteration_{iteration}",
                completion_pct=completion_pct,
                partial_results=partial_texts,
                last_checkpoint_id=checkpoint_id,
                message=f"Completed research iteration {iteration + 1}.",
            )
            self.report_progress(ctx, progress)
            self.emit_partial_result(ctx, finding.content)

            if self._pause_after_phase(ctx, index=iteration):
                if checkpoint_id is None:
                    checkpoint_id = self._save_checkpoint(
                        ctx,
                        cursor=self._next_iteration,
                    )
                if task_id:
                    ctx.transition_task(task_id=task_id, to_state="paused")
                    self._schedule_pause_resume(
                        ctx=ctx,
                        task_id=task_id,
                        query=query,
                    )
                transition(ctx.state, "checkpoint_reached", logger=ctx.logger)
                pause_message = self._build_pause_response_message(
                    query=query,
                    findings=findings,
                    iteration=iteration,
                )
                return ExecutionResult.from_step_output(
                    ctx.respond(
                        message=pause_message,
                        status=BRAIN_STATE_WAITING_USER,
                    )
                )

            convergence = self._check_convergence(ctx, query=query, findings=findings)
            if convergence.converged:
                break
            convergence_hint = ""

        return self._synthesize_and_finalize(
            ctx,
            task_id=task_id,
            query=query,
            findings=findings,
        )

    def _query_from_context(self, ctx: ExecutionContext) -> str:
        return (
            _normalized_text(getattr(ctx.decision, "research_query", "") or "")
            or _normalized_text(getattr(ctx.decision, "objective", "") or "")
            or _normalized_text(getattr(ctx.state, "goal", "") or "")
            or _normalized_text(ctx.user_input or "")
        )

    def _scope_from_context(self, ctx: ExecutionContext) -> str:
        return _normalized_text(getattr(ctx.decision, "research_scope", "") or "")

    def _build_iteration_goal(
        self,
        *,
        query: str,
        scope: str,
        findings: list[dict[str, Any]],
        convergence_hint: str,
        iteration: int,
    ) -> str:
        parts = [*_render_temporal_fact_lines(findings), f"Research objective: {query}"]
        if scope:
            parts.append(f"Scope constraints: {scope}")
        if findings:
            summary_parts = []
            for f in findings[-3:]:
                content = _normalized_text(f.get("content"))
                if content:
                    summary_parts.append(content[:200])
            if summary_parts:
                parts.append(
                    f"Prior findings (latest {len(summary_parts)}): {' | '.join(summary_parts)}"
                )
        if convergence_hint:
            parts.append(f"Focus this search on: {convergence_hint}")
        parts.append(f"Iteration: {iteration + 1}")
        return "\n".join(parts)

    def _fallback_decision_for_research(self, goal: str) -> Any:
        from openminion.modules.brain.schemas import ActDecision

        return ActDecision(
            act_profile=BRAIN_ACT_PROFILE_GENERAL,
            confidence=0.7,
            reason_code="research_iteration_fallback",
            sub_intents=[goal],
            rationale=goal,
        )

    def _execute_search_iteration(
        self,
        ctx: ExecutionContext,
        *,
        iteration: int,
        query: str,
        findings_so_far: list[dict[str, Any]],
        convergence_hint: str,
    ) -> ResearchFinding:
        child_goal = self._build_iteration_goal(
            query=query,
            scope=self._scope_from_context(ctx),
            findings=findings_so_far,
            convergence_hint=convergence_hint,
            iteration=iteration,
        )

        runner = runner_from_context(ctx)
        content = ""
        mode_used = "plan"
        evidence_dates: list[str] = []

        if runner is not None:
            try:
                child_state = self._build_child_state(
                    parent_state=ctx.state,
                    child_budget=self._iteration_budget(ctx, iteration=iteration),
                    goal=child_goal,
                )
                with _non_recursive_child_profile(runner):
                    decision = self._fallback_decision_for_research(child_goal)
                    mode_used = str(
                        getattr(decision, "route", getattr(decision, "mode", ""))
                        or "act"
                    )
                    result = invoke_decision_direct(
                        runner,
                        state=child_state,
                        decision=decision,
                        user_input=child_goal,
                        logger=ctx.logger,
                        depth=1,
                    )
                    try:
                        ctx.logger.emit(
                            "brain.research.child_execution_result",
                            {
                                "iteration": int(iteration),
                                "child_mode": mode_used,
                                "status": str(getattr(result, "status", "") or ""),
                                "message": _normalized_text(
                                    getattr(result, "message", "") or ""
                                )[:1000],
                                "action_status": str(
                                    getattr(
                                        getattr(result, "action_result", None),
                                        "status",
                                        "",
                                    )
                                    or ""
                                ),
                                "action_summary": _normalized_text(
                                    getattr(
                                        getattr(result, "action_result", None),
                                        "summary",
                                        "",
                                    )
                                    or ""
                                )[:1000],
                            },
                            trace_id=str(
                                getattr(ctx.state, "trace_id", "") or ""
                            ).strip(),
                        )
                    except Exception:
                        pass
                    result_status = (
                        str(getattr(result, "status", "") or "").strip().lower()
                    )
                    if result_status in {BRAIN_STATE_DONE, BRAIN_STATE_WAITING_USER}:
                        evidence_dates = _evidence_dates_from_action_result(
                            getattr(result, "action_result", None)
                        )
                        if not evidence_dates:
                            evidence_dates = _evidence_dates_from_working_state(
                                getattr(result, STATE_KEY_WORKING, None)
                            )
                        candidate_content = _usable_child_result_text(
                            getattr(result, "message", "") or ""
                        )
                        if not candidate_content:
                            candidate_content = _usable_child_action_result_text(
                                getattr(result, "action_result", None)
                            )
                        if not candidate_content:
                            candidate_content = _usable_child_working_state_text(
                                getattr(result, STATE_KEY_WORKING, None)
                            )
                        if candidate_content:
                            content = candidate_content
            except Exception as exc:
                trace_id = str(getattr(ctx.state, "trace_id", "") or "").strip()
                try:
                    ctx.logger.emit(
                        "brain.research.child_execution_failed",
                        {
                            "iteration": int(iteration),
                            "child_mode": mode_used,
                            "error_type": type(exc).__name__,
                            "error": str(exc),
                            "traceback": traceback.format_exc(limit=8),
                        },
                        trace_id=trace_id,
                        status=BRAIN_STATE_ERROR,
                        error={
                            "code": "RESEARCH_CHILD_EXECUTION_FAILED",
                            "message": str(exc),
                        },
                    )
                except Exception:
                    pass

        if not content:
            try:
                plan = ctx.plan(user_input=child_goal)
                content = self._content_from_plan(plan, query=query)
                mode_used = "plan"
            except Exception:
                content = f"Research iteration {iteration + 1} for '{query}'."

        return ResearchFinding(
            iteration=iteration,
            source_tool=mode_used,
            source_query=child_goal,
            content=content or f"Research iteration {iteration + 1} complete.",
            evidence_dates=evidence_dates,
        )

    def _build_child_state(
        self,
        *,
        parent_state: WorkingState,
        child_budget: BudgetCounters,
        goal: str,
    ) -> WorkingState:
        child_state = parent_state.model_copy(deep=True)
        child_state.goal = goal
        child_state.plan = None
        child_state.cursor = 0
        set_status_unchecked(child_state, "active", reason="bootstrap")
        child_state.budgets_remaining = child_budget.model_copy(deep=True)
        child_state.last_command_id = None
        child_state.last_result = None
        child_state.step_outputs = []
        child_state.pending_jobs = []
        child_state.memory_candidates = []
        child_state.idempotency_cache = {}
        child_state.child_tasks = {}
        child_state.child_task_order = []
        child_state.pending_clarify_items = []
        child_state.unresolved_clarify_items = []
        child_state.clarify_responses = {}
        child_state.task_backed_task_id = None
        child_state.task_backed_checkpoint_id = None
        child_state.task_backed_resume_state = {}
        return child_state

    def _iteration_budget(
        self,
        ctx: ExecutionContext,
        *,
        iteration: int,
    ) -> BudgetCounters:
        budgets = ctx.state.budgets_remaining
        remaining_iterations = max(1, self._max_research_iterations - iteration)

        def _split(value: int, *, minimum: int = 1) -> int:
            available = max(0, int(value))
            if available <= 0:
                return 0
            return min(available, max(minimum, int(available // remaining_iterations)))

        return BudgetCounters(
            ticks=_split(int(budgets.ticks), minimum=3),
            tool_calls=_split(int(budgets.tool_calls), minimum=3),
            a2a_calls=_split(int(budgets.a2a_calls)),
            tokens=_split(int(budgets.tokens)),
            time_ms=_split(int(budgets.time_ms)),
        )

    def _content_from_plan(self, plan: Plan | None, *, query: str) -> str:
        if plan is None:
            return ""
        step_titles = [
            _normalized_text(getattr(step, "title", "") or getattr(step, "kind", ""))
            for step in getattr(plan, "steps", []) or []
            if _normalized_text(getattr(step, "title", "") or getattr(step, "kind", ""))
        ]
        if step_titles:
            return f"Found: {', '.join(step_titles[:5])}."
        objective = _normalized_text(getattr(plan, "objective", "") or "")
        if objective:
            return objective
        return f"Research iteration complete for '{query}'."

    def _check_convergence(
        self,
        ctx: ExecutionContext,
        *,
        query: str,
        findings: list[dict[str, Any]],
    ) -> ResearchConvergenceSignal:
        """ASRR-02: structural research convergence."""

        del query  # structural convergence does not consult the query

        typed_finding_count = len(findings)
        source_pairs: set[tuple[str, str]] = set()
        for entry in findings:
            source_tool = _normalized_text(entry.get("source_tool", ""))
            source_query = _normalized_text(entry.get("source_query", ""))
            if source_tool or source_query:
                source_pairs.add((source_tool, source_query))
        source_coverage = len(source_pairs)

        new_evidence_delta = 1 if findings else 0

        progress = compose_progress_signal(
            turn_index=max(0, typed_finding_count - 1),
            new_typed_record_delta=new_evidence_delta,
        )

        counters = ResearchConvergenceCounters(
            typed_finding_count=typed_finding_count,
            source_coverage=source_coverage,
            new_evidence_delta=new_evidence_delta,
            verifier_family_counts={},
        )
        config = self._convergence_config

        return compose_research_convergence_signal(
            counters=counters,
            config=config,
            progress=progress,
        )

    def _synthesize_and_finalize(
        self,
        ctx: ExecutionContext,
        *,
        task_id: str,
        query: str,
        findings: list[dict[str, Any]],
    ) -> ExecutionResult:
        synthesis = self._build_synthesis_text(
            ctx,
            query=query,
            findings=findings,
            allow_llm_synthesis=True,
        )
        transition(ctx.state, "task_completed", logger=ctx.logger)
        ctx.state.task_backed_resume_state = {}
        if task_id:
            refresh_schedule = self._refresh_schedule(ctx.get_task(task_id=task_id))
            if refresh_schedule is not None:
                ctx.transition_task(task_id=task_id, to_state="paused")
                cron_expr, timezone_name = refresh_schedule
                runner = runner_from_context(ctx)
                task_manager = (
                    getattr(runner, "task_manager", None)
                    if runner is not None
                    else None
                )
                if task_manager is not None:
                    try:
                        schedule_recurring_resume(
                            task_manager=task_manager,
                            task_id=task_id,
                            session_id=ctx.state.session_id,
                            agent_id=ctx.state.agent_id,
                            cron_expr=cron_expr,
                            timezone_name=timezone_name,
                            goal=query,
                            mode_name=RESEARCH_MODE,
                        )
                    except Exception:
                        pass
            else:
                ctx.transition_task(task_id=task_id, to_state="done")

        return ExecutionResult.from_step_output(
            ctx.respond(message=synthesis, status=BRAIN_STATE_DONE)
        )

    def _build_synthesis_text(
        self,
        ctx: ExecutionContext | None,
        *,
        query: str,
        findings: list[dict[str, Any]],
        allow_llm_synthesis: bool,
    ) -> str:
        synthesis_prompt = (
            "\n".join(_render_temporal_fact_lines(findings))
            + "\n"
            + f"Research query: {query}\n"
            f"Accumulated findings from {len(findings)} search iterations:\n"
            + "\n".join(
                f"- Iteration {f.get('iteration', '?')}: {_normalized_text(f.get('content', ''))[:400]}"
                for f in findings
            )
            + "\n\nSynthesize these findings into a comprehensive, coherent answer."
        )
        plan = None
        if allow_llm_synthesis and ctx is not None:
            try:
                plan = ctx.plan(user_input=synthesis_prompt)
            except Exception:
                plan = None

        synthesis = ""
        if plan is not None:
            synthesis = _normalized_text(getattr(plan, "objective", "") or "")
            step_titles = [
                _normalized_text(
                    getattr(step, "title", "") or getattr(step, "kind", "")
                )
                for step in getattr(plan, "steps", []) or []
                if _normalized_text(
                    getattr(step, "title", "") or getattr(step, "kind", "")
                )
            ]
            if step_titles and not synthesis:
                synthesis = " ".join(step_titles)

        if not synthesis:
            synthesis = f"Research complete for '{query}'."
            if findings:
                partial_texts = _meaningful_partial_texts(findings)
                if partial_texts:
                    synthesis = f"Research complete for '{query}': {' '.join(partial_texts[:3])}"
                else:
                    synthesis = ""
        return synthesis

    def _build_pause_response_message(
        self,
        *,
        query: str,
        findings: list[dict[str, Any]],
        iteration: int,
    ) -> str:
        del query
        partial_answer = _build_pause_partial_answer(findings)
        pause_note = (
            f"Research paused after iteration {iteration + 1}. "
            "Continue in a new turn to resume."
        )
        if not partial_answer:
            return (
                "Research paused before it produced a usable partial answer. "
                "Continue in a new turn to resume."
            )
        return f"{partial_answer}\n\n{pause_note}"

    def _refresh_schedule(self, record: Any) -> tuple[str, str] | None:
        metadata = (
            dict(getattr(record, "metadata", {}) or {}) if record is not None else {}
        )
        cron_expr = _normalized_text(metadata.get("refresh_cron_expr"))
        if not cron_expr:
            return None
        timezone_name = _normalized_text(metadata.get("refresh_timezone")) or "UTC"
        return cron_expr, timezone_name

    def _schedule_pause_resume(
        self,
        *,
        ctx: ExecutionContext,
        task_id: str,
        query: str,
    ) -> None:
        runner = runner_from_context(ctx)
        task_manager = (
            getattr(runner, "task_manager", None) if runner is not None else None
        )
        if task_manager is None:
            return
        record = task_manager.get_task(task_id)
        if record is None or not self._resume_policy.should_create_cron_job(
            record, self
        ):
            return
        initial = self._resume_policy.initial_schedule(record, self)
        interval = initial.interval
        if interval is None:
            return
        try:
            schedule_backoff_resume(
                task_manager=task_manager,
                task_id=task_id,
                session_id=ctx.state.session_id,
                agent_id=ctx.state.agent_id,
                goal=query,
                mode_name=RESEARCH_MODE,
                interval=interval,
                attempt_count=0,
                first_scheduled_at=datetime.now(timezone.utc).isoformat(),
            )
        except Exception:
            return

    def _pause_after_phase(self, ctx: ExecutionContext, *, index: int) -> bool:
        budgets = getattr(ctx.state, "budgets_remaining", None)
        if budgets is None:
            return False
        remaining_ticks = int(getattr(budgets, "ticks", 0) or 0)
        budgets.ticks = max(0, remaining_ticks - 1)
        return (
            index < self._max_research_iterations - 1
            and int(getattr(budgets, "ticks", 0) or 0) <= 0
        )
