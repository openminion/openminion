from types import SimpleNamespace
from datetime import datetime, timezone
from typing import Any

from pydantic import Field

from openminion.modules.brain.constants import (
    BRAIN_INTERNAL_MODE_EXECUTION_TARGET_DELEGATED,
    BRAIN_STATE_ERROR,
    BRAIN_STATE_JOB_PENDING,
)
from openminion.modules.brain.diagnostics.transitions import (
    set_status_unchecked,
    transition,
)
from openminion.modules.brain.schemas import (
    ActionError,
    ActionResult,
    new_uuid,
)
from openminion.modules.brain.execution.child_tasks import (
    SubtaskResult,
    SubtaskSpec,
)
from openminion.modules.brain.execution.loop_contracts import (
    ExecutionContext,
    ExecutionResult,
)
from openminion.modules.brain.execution.preflight import (
    ModePreparation,
    ValidationResult,
)

from openminion.modules.brain.runner.resume import (  # noqa: E402
    DefaultCronJobLinker,
    ExponentialBackoffResumePolicy,
    next_attempt_state,
    schedule_backoff_resume,
)
from .contracts import (  # noqa: E402
    A2AStatusMapper,
    AgentDiscoveryProvider,
    AgentResolver,
    AsyncCancellationPolicy,
    BudgetPolicy,
    ClarificationAction,
    ClarificationPolicy,
    DelegatePayload,
    DelegationObserver,
    DelegationStrategy,
    DelegationTaskTracker,
    IdempotencyKeyGenerator,
    ResultSynthesizer,
)
from .strategies import (  # noqa: E402
    AcceptOrFailResolver,
    AsyncJobStrategy,
    DefaultAsyncCancellationPolicy,
    DirectStatusMapper,
    FailFastPolicy,
    FailOnClarificationPolicy,
    HashKeyGenerator,
    PassThroughSynthesizer,
    PollingResumeStrategy,
    RegistryDiscoveryProvider,
    SimpleA2ABudgetPolicy,
    StatusMessageObserver,
    SummaryInheritancePolicy,
    SyncCommandStrategy,
    TaskManagerTaskTracker,
    _runner_task_manager,
)

DELEGATE_MODE = BRAIN_INTERNAL_MODE_EXECUTION_TARGET_DELEGATED


def _delegate_subtask(payload: DelegatePayload):
    return SubtaskSpec(
        subtask_id="delegate-1",
        goal=payload.goal,
        constraints=payload.constraints,
        suggested_mode=DELEGATE_MODE,
    )


def _delegate_subtask_result(
    *,
    payload: DelegatePayload,
    mapped_result: ExecutionResult,
    action_result: ActionResult,
):
    status = "completed" if mapped_result.status == "done" else "failed"
    return SubtaskResult(
        subtask_id="delegate-1",
        goal=payload.goal,
        status=status,
        mode_used=DELEGATE_MODE,
        output=str(getattr(mapped_result, "message", "") or "").strip(),
        error=(
            str(getattr(action_result.error, "message", "") or "").strip() or None
            if status == "failed"
            else None
        ),
        tokens_used=int(
            getattr(getattr(action_result, "metrics", None), "tokens_used", 0) or 0
        ),
    )


def _empty_error_result(
    *, ctx: ExecutionContext, message: str, code: str
) -> ExecutionResult:
    action_result = ActionResult(
        command_id=new_uuid(),
        status="failed",
        summary=message,
        error=ActionError(
            code=code,
            message=message,
            details={"reason_code": code.lower()},
        ),
    )
    ctx.state.last_result = action_result
    set_status_unchecked(ctx.state, BRAIN_STATE_ERROR, reason="delegation_error")
    return ExecutionResult(
        status="error",
        working_state=ctx.state,
        message=message,
        action_result=action_result,
    )


def _delegate_result_text(action_result: ActionResult | None) -> str:
    if action_result is None:
        return ""
    summary = str(getattr(action_result, "summary", "") or "").strip()
    if summary:
        return summary
    outputs = getattr(action_result, "outputs", None)
    if not isinstance(outputs, dict):
        return ""
    for key in ("body", "message", "summary", "answer", "result", "output"):
        text = str(outputs.get(key, "") or "").strip()
        if text:
            return text
    return ""


class DelegateMode:
    mode_name = DELEGATE_MODE
    mode_description = (
        "hand off a task to another agent when the work is better handled by "
        "a specialist agent with specific capabilities. The delegating agent "
        "waits for the result and returns it to the user."
    )
    mode_category = "workflow"
    has_prepare = True
    has_validate = True
    priority_hint = 65
    mode_thinking_policy = {
        "default_reasoning_profile": "minimal",
        "allowed_reasoning_profiles": ("minimal", "detailed"),
        "allow_request_override": True,
    }
    default_config = {"max_depth": 1}
    decision_payload_fields = {
        "target_agent_id": (str, Field(..., min_length=1)),
        "target_capability": (str | None, Field(default=None)),
        "goal": (str, Field(..., min_length=1)),
        "constraints": (str, Field(default="")),
        "synthesize_result": (bool, Field(default=False)),
        "timeout_ms": (int | None, Field(default=None, ge=1)),
        "delegation_context": (dict[str, Any] | None, Field(default=None)),
    }

    def __init__(
        self,
        *,
        resolver: AgentResolver | None = None,
        strategy: DelegationStrategy | None = None,
        status_mapper: A2AStatusMapper | None = None,
        clarification_policy: ClarificationPolicy | None = None,
        synthesizer: ResultSynthesizer | None = None,
        failure_policy=None,
        inheritance_policy=None,
        cancellation_policy: AsyncCancellationPolicy | None = None,
        discovery_provider: AgentDiscoveryProvider | None = None,
        idempotency_generator: IdempotencyKeyGenerator | None = None,
        observer: DelegationObserver | None = None,
        budget_policy: BudgetPolicy | None = None,
        resume_strategy: PollingResumeStrategy | None = None,
        task_tracker: DelegationTaskTracker | None = None,
        resume_policy: ExponentialBackoffResumePolicy | None = None,
    ) -> None:
        self._resolver = resolver or AcceptOrFailResolver()
        self._explicit_strategy = strategy is not None
        self._strategy = strategy or SyncCommandStrategy()
        self._status_mapper = status_mapper or DirectStatusMapper()
        self._clarification = clarification_policy or FailOnClarificationPolicy()
        self._synthesizer = synthesizer or PassThroughSynthesizer()
        self._failure_policy = failure_policy or FailFastPolicy()
        self._inheritance = inheritance_policy or SummaryInheritancePolicy()
        self._cancellation = cancellation_policy or DefaultAsyncCancellationPolicy()
        self._discovery = discovery_provider or RegistryDiscoveryProvider()
        self._idempotency = idempotency_generator or HashKeyGenerator()
        self._observer = observer or StatusMessageObserver()
        self._budget = budget_policy or SimpleA2ABudgetPolicy()
        self._resume_strategy = resume_strategy or PollingResumeStrategy(
            status_mapper=self._status_mapper
        )
        self._task_tracker = task_tracker or TaskManagerTaskTracker()
        self._resume_policy = resume_policy or ExponentialBackoffResumePolicy()

    def _payload(self, ctx: ExecutionContext) -> DelegatePayload:
        return DelegatePayload(
            target_agent_id=str(
                getattr(ctx.decision, "target_agent_id", "")
                or getattr(ctx.state, "delegation_target_agent_id", "")
                or ""
            ),
            target_capability=getattr(ctx.decision, "target_capability", None),
            goal=str(
                getattr(ctx.decision, "goal", "")
                or getattr(ctx.state, "delegation_goal", "")
                or ""
            ),
            constraints=str(getattr(ctx.decision, "constraints", "") or ""),
            synthesize_result=bool(
                getattr(ctx.decision, "synthesize_result", False)
                or getattr(ctx.state, "delegation_synthesize_result", False)
            ),
            timeout_ms=getattr(ctx.decision, "timeout_ms", None),
            delegation_context=(
                getattr(ctx.decision, "delegation_context", None)
                or getattr(ctx.state, "delegation_context", None)
                or None
            ),
        )

    @property
    def has_resume(self) -> bool:
        return isinstance(self._strategy, AsyncJobStrategy)

    def apply_mode_config(self, *, config, runner, profile) -> None:
        del profile
        if not self._explicit_strategy and bool(
            getattr(config, "delegate_async", False)
        ):
            self._strategy = AsyncJobStrategy()
        elif not self._explicit_strategy:
            self._strategy = SyncCommandStrategy()
        bind_context = getattr(self._task_tracker, "bind_runner", None)
        if callable(bind_context):
            bind_context(runner=runner)

    def _bind_task_tracker(self, *, ctx: ExecutionContext) -> None:
        bind_context = getattr(self._task_tracker, "bind_context", None)
        if callable(bind_context):
            bind_context(ctx=ctx)

    def _has_pending_async_delegation(self, ctx: ExecutionContext) -> bool:
        return bool(
            self.has_resume
            and str(getattr(ctx.state, "delegation_job_id", "") or "").strip()
            and str(getattr(ctx.state, "status", "") or "").strip()
            == BRAIN_STATE_JOB_PENDING
        )

    def _store_async_linkage(
        self,
        *,
        ctx: ExecutionContext,
        resolved_agent_id: str,
        payload: DelegatePayload,
        job_id: str,
        task_id: str,
    ) -> None:
        ctx.state.delegation_job_id = job_id
        ctx.state.delegation_task_id = task_id or None
        ctx.state.delegation_target_agent_id = resolved_agent_id
        ctx.state.delegation_goal = payload.goal
        ctx.state.delegation_synthesize_result = payload.synthesize_result

    def _clear_async_linkage(self, ctx: ExecutionContext) -> None:
        ctx.state.delegation_job_id = None
        ctx.state.delegation_task_id = None
        ctx.state.delegation_target_agent_id = None
        ctx.state.delegation_goal = ""
        ctx.state.delegation_synthesize_result = False

    def _resolved_agent_id(
        self, *, ctx: ExecutionContext, payload: DelegatePayload
    ) -> str:
        registry = self._discovery.get_registry(ctx=ctx)
        return self._resolver.resolve(
            target_agent_id=payload.target_agent_id,
            target_capability=payload.target_capability,
            registry=registry,
        )

    def _schedule_async_resume_poll(
        self,
        *,
        ctx: ExecutionContext,
        task_id: str,
        goal: str,
        next_attempt: bool,
    ) -> bool:
        manager = _runner_task_manager(ctx)
        if manager is None:
            return True
        record = manager.get_task(task_id)
        if record is None:
            return True
        mode_spec = SimpleNamespace(has_resume=True)
        if not self._resume_policy.should_create_cron_job(record, mode_spec):
            return True
        attempt_count, current_interval, elapsed, first_scheduled_at = (
            next_attempt_state(record)
        )
        if next_attempt:
            attempt_count += 1
            if self._resume_policy.should_stop_retrying(attempt_count, elapsed, record):
                DefaultCronJobLinker(task_manager=manager).unlink_and_delete(task_id)
                return False
            interval = self._resume_policy.next_backoff_interval(
                attempt_count,
                current_interval,
            )
        else:
            initial = self._resume_policy.initial_schedule(record, mode_spec)
            interval = initial.interval or current_interval
            first_scheduled_at = (
                first_scheduled_at or datetime.now(timezone.utc).isoformat()
            )
        try:
            schedule_backoff_resume(
                task_manager=manager,
                task_id=task_id,
                session_id=ctx.state.session_id,
                agent_id=ctx.state.agent_id,
                goal=goal,
                mode_name=DELEGATE_MODE,
                interval=interval,
                attempt_count=attempt_count,
                first_scheduled_at=first_scheduled_at,
                extra_metadata={
                    "delegation_job_id": str(
                        getattr(ctx.state, "delegation_job_id", "") or ""
                    ),
                },
            )
        except Exception:
            return True
        return True

    def _idempotency_key(
        self, *, ctx: ExecutionContext, payload: DelegatePayload
    ) -> str:
        return self._idempotency.generate(
            session_id=ctx.state.session_id,
            trace_id=str(getattr(ctx.state, "trace_id", "") or ""),
            goal=payload.goal,
        )

    def prepare(
        self,
        ctx: ExecutionContext,
        *,
        emit_status_updates: bool = False,
    ) -> ModePreparation:
        if self._has_pending_async_delegation(ctx):
            return ModePreparation()
        payload = self._payload(ctx)
        if emit_status_updates:
            self._observer.emit(
                ctx=ctx,
                mode_state="resolve_target",
                label=f"[delegated] resolving agent:{payload.target_agent_id}",
                target_agent_id=payload.target_agent_id,
            )
        try:
            self._resolved_agent_id(ctx=ctx, payload=payload)
        except ValueError as exc:
            return ModePreparation(
                mode_result=_empty_error_result(
                    ctx=ctx,
                    message=str(exc),
                    code="DELEGATE_TARGET_INVALID",
                )
            )
        if not self._budget.check_budget(state=ctx.state):
            return ModePreparation(
                mode_result=_empty_error_result(
                    ctx=ctx,
                    message="Delegation blocked before execution: a2a budget exhausted.",
                    code="BUDGET_EXCEEDED",
                )
            )
        if self._cancellation.should_cancel(ctx=ctx, results=[], attempts=0):
            return ModePreparation(
                mode_result=ExecutionResult.from_step_output(
                    ctx.respond(
                        message="Delegation cancelled before execution.",
                        status="stopped",
                    )
                )
            )
        return ModePreparation()

    def execute(self, ctx: ExecutionContext) -> ExecutionResult:
        self._bind_task_tracker(ctx=ctx)
        if self._has_pending_async_delegation(ctx):
            return self.resume(ctx)
        preparation = self.prepare(ctx, emit_status_updates=True)
        if preparation.mode_result is not None:
            return preparation.mode_result
        payload = self._payload(ctx)
        resolved_agent_id = self._resolved_agent_id(ctx=ctx, payload=payload)
        child_context = self._inheritance.build_child_context(
            parent_state=ctx.state,
            subtask=_delegate_subtask(payload),
        )
        child_context.delegation_context = payload.delegation_context
        idempotency_key = self._idempotency_key(ctx=ctx, payload=payload)
        self._observer.emit(
            ctx=ctx,
            mode_state="delegating",
            label=f'[delegated] calling agent:{resolved_agent_id} with goal: "{payload.goal}"',
            target_agent_id=resolved_agent_id,
        )
        execution = self._strategy.execute(
            ctx=ctx,
            payload=payload,
            resolved_agent_id=resolved_agent_id,
            delegation_context=child_context,
            idempotency_key=idempotency_key,
        )
        action_result = execution.action_result
        command = execution.command
        if execution.job is not None:
            return self._start_async_job(
                ctx=ctx,
                resolved_agent_id=resolved_agent_id,
                payload=payload,
                command_id=command.command_id,
                job_id=execution.job.task_id,
            )
        mapped_result = self._status_mapper.map_result(
            ctx=ctx,
            payload=payload,
            resolved_agent_id=resolved_agent_id,
            action_result=action_result,
        )
        if action_result.status == "needs_user":
            action = self._clarification.on_clarification_needed(
                delegate_result=action_result,
                original_context=ctx,
            )
            if action == ClarificationAction.FAIL:
                mapped_result = _empty_error_result(
                    ctx=ctx,
                    message=(
                        f"Delegated agent {resolved_agent_id} requested clarification "
                        "but delegated execution currently fails closed on clarification."
                    ),
                    code="DELEGATE_CLARIFICATION_UNSUPPORTED",
                )
        if mapped_result.status == "error":
            self._apply_failure_policy(
                payload=payload,
                mapped_result=mapped_result,
                action_result=action_result,
            )
        if payload.synthesize_result:
            self._observer.emit(
                ctx=ctx,
                mode_state="synthesizing",
                label="[delegated] synthesizing result",
                target_agent_id=resolved_agent_id,
            )
            synthesized = self._synthesizer.synthesize(
                ctx=ctx,
                results=[
                    _delegate_subtask_result(
                        payload=payload,
                        mapped_result=mapped_result,
                        action_result=action_result,
                    )
                ],
            )
            if mapped_result.action_result is not None:
                synthesized.action_result = mapped_result.action_result
            mapped_result = synthesized
        ctx.state.last_command_id = command.command_id
        if mapped_result.action_result is not None:
            ctx.state.last_result = mapped_result.action_result
        set_status_unchecked(
            ctx.state, mapped_result.status, reason="delegation_result_mapped"
        )
        self._emit_completion_statuses(
            ctx=ctx,
            resolved_agent_id=resolved_agent_id,
            action_status=action_result.status,
        )
        return mapped_result

    def _start_async_job(
        self,
        *,
        ctx: ExecutionContext,
        resolved_agent_id: str,
        payload: DelegatePayload,
        command_id: str,
        job_id: str,
    ) -> ExecutionResult:
        self._observer.emit(
            ctx=ctx,
            mode_state="job_started",
            label=f"[delegated-async] job started (job_id={job_id})",
            target_agent_id=resolved_agent_id,
        )
        task_id = self._task_tracker.create_linked_task(
            ctx=ctx,
            job_id=job_id,
            target_agent_id=resolved_agent_id,
            goal=payload.goal,
        )
        self._store_async_linkage(
            ctx=ctx,
            resolved_agent_id=resolved_agent_id,
            payload=payload,
            job_id=job_id,
            task_id=task_id,
        )
        if task_id:
            ctx.transition_task(task_id=task_id, to_state="paused")
            self._schedule_async_resume_poll(
                ctx=ctx,
                task_id=task_id,
                goal=payload.goal,
                next_attempt=False,
            )
        ctx.state.last_command_id = command_id
        transition(ctx.state, "job_scheduled", logger=ctx.logger)
        return ExecutionResult.from_step_output(
            ctx.respond(
                message=f"Started async delegation job {job_id}.",
                status=BRAIN_STATE_JOB_PENDING,
            )
        )

    def _apply_failure_policy(
        self,
        *,
        payload: DelegatePayload,
        mapped_result: ExecutionResult,
        action_result: ActionResult,
    ) -> None:
        self._failure_policy.on_failure(
            subtask=_delegate_subtask(payload),
            result=_delegate_subtask_result(
                payload=payload,
                mapped_result=mapped_result,
                action_result=action_result,
            ),
        )

    def _emit_completion_statuses(
        self,
        *,
        ctx: ExecutionContext,
        resolved_agent_id: str,
        action_status: str,
    ) -> None:
        self._observer.emit(
            ctx=ctx,
            mode_state="delegate_result",
            label=f"[delegated] agent:{resolved_agent_id} completed ({action_status})",
            target_agent_id=resolved_agent_id,
        )
        self._observer.emit(
            ctx=ctx,
            mode_state="done",
            label="[delegated] done",
            target_agent_id=resolved_agent_id,
        )

    def resume(
        self,
        ctx: ExecutionContext,
        resume_state: dict[str, object] | None = None,
    ) -> ExecutionResult:
        del resume_state
        payload = self._payload(ctx)
        resolved_agent_id = str(
            getattr(ctx.state, "delegation_target_agent_id", "")
            or payload.target_agent_id
        ).strip()
        job_id = str(getattr(ctx.state, "delegation_job_id", "") or "").strip()
        if not job_id:
            return _empty_error_result(
                ctx=ctx,
                message="No async delegation job is available to resume.",
                code="DELEGATE_RESUME_MISSING_JOB",
            )
        self._observer.emit(
            ctx=ctx,
            mode_state="polling",
            label=f"[delegated-async] polling agent:{resolved_agent_id} (job_id={job_id})",
            target_agent_id=resolved_agent_id,
        )
        mapped_result = self._resume_strategy.check(
            ctx=ctx,
            payload=payload,
            resolved_agent_id=resolved_agent_id,
            job_id=job_id,
        )
        if mapped_result.status == "pending":
            task_id = str(getattr(ctx.state, "delegation_task_id", "") or "").strip()
            if task_id:
                ctx.transition_task(task_id=task_id, to_state="paused")
                if not self._schedule_async_resume_poll(
                    ctx=ctx,
                    task_id=task_id,
                    goal=payload.goal,
                    next_attempt=True,
                ):
                    return ExecutionResult(
                        status="pending",
                        working_state=ctx.state,
                        message=(
                            "Delegation is still pending. Cron polling stopped after "
                            "reaching retry limits."
                        ),
                    )
            transition(ctx.state, "job_still_pending", logger=ctx.logger)
            return mapped_result
        if payload.synthesize_result and mapped_result.action_result is not None:
            synthesized = self._synthesizer.synthesize(
                ctx=ctx,
                results=[
                    _delegate_subtask_result(
                        payload=payload,
                        mapped_result=mapped_result,
                        action_result=mapped_result.action_result,
                    )
                ],
            )
            synthesized.action_result = mapped_result.action_result
            mapped_result = synthesized
        task_id = str(getattr(ctx.state, "delegation_task_id", "") or "").strip()
        if mapped_result.status == "done":
            self._task_tracker.mark_done(task_id=task_id)
        elif mapped_result.status == "stopped":
            self._task_tracker.mark_cancelled(task_id=task_id)
        else:
            self._task_tracker.mark_failed(
                task_id=task_id,
                message=str(mapped_result.message or "").strip(),
            )
        self._clear_async_linkage(ctx)
        if mapped_result.action_result is not None:
            ctx.state.last_result = mapped_result.action_result
        set_status_unchecked(
            ctx.state, mapped_result.status, reason="delegation_result_mapped"
        )
        self._observer.emit(
            ctx=ctx,
            mode_state="delegate_result",
            label=(
                f"[delegated-async] agent:{resolved_agent_id} completed "
                f"({mapped_result.status})"
            ),
            target_agent_id=resolved_agent_id,
        )
        return mapped_result

    def validate(
        self,
        ctx: ExecutionContext,
        *,
        preparation: ModePreparation | None = None,
    ) -> ValidationResult | None:
        del preparation
        last_result = getattr(ctx.state, "last_result", None)
        if last_result is None:
            return ValidationResult(passed=True)
        if _delegate_result_text(last_result):
            return ValidationResult(passed=True)
        return ValidationResult(
            passed=False,
            feedback="Delegate result was empty.",
            should_retry=False,
            code="delegate_empty_result",
            details={"mode": DELEGATE_MODE},
        )


__all__ = ["DELEGATE_MODE", "DelegateMode"]
