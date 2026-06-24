import hashlib
from typing import Any

from openminion.modules.brain.constants import (
    BRAIN_ACTION_STATUS_BLOCKED,
    BRAIN_ACTION_STATUS_FAILED,
    BRAIN_ACTION_STATUS_NEEDS_USER,
    BRAIN_ACTION_STATUS_RETRY,
    BRAIN_ACTION_STATUS_SUCCESS,
    BRAIN_ACTION_STATUS_TIMEOUT,
)
from openminion.modules.brain.schemas import ActionError, ActionResult, WorkingState
from openminion.modules.brain.schemas.commands import AgentCommand
from openminion.modules.brain.execution.loop_contracts import ExecutionResult
from openminion.modules.brain.execution.child_tasks import (
    ChildContext,
    SubtaskResult,
)
from openminion.modules.brain.execution.orchestrate.strategies import (
    AbortOnNewMessagePolicy,
    FailFastPolicy,
    SummaryInheritancePolicy,
)
from openminion.modules.brain.loop.services import runner_from_context
from .contracts import (
    A2AStatusMapper,
    AgentDiscoveryProvider,
    AgentResolver,
    AsyncCancellationPolicy,
    BudgetPolicy,
    ClarificationAction,
    ClarificationPolicy,
    DelegationExecution,
    DelegatePayload,
    DelegationObserver,
    DelegationStrategy,
    DelegationTaskTracker,
    IdempotencyKeyGenerator,
    ResultSynthesizer,
)


def _normalized_text(value: Any) -> str:
    return str(value or "").strip()


def _describe_registry_state(registry: Any, *, agent_id: str) -> tuple[bool, str]:
    normalized = _normalized_text(agent_id)
    if not normalized:
        return False, ""

    if isinstance(registry, dict):
        record = registry.get(normalized)
        if record is None:
            return False, ""
        if isinstance(record, dict):
            state = _normalized_text(record.get("state") or record.get("status"))
            return True, state or "available"
        return True, "available"

    getter = getattr(registry, "get", None)
    if callable(getter):
        descriptor = getter(normalized)
        if descriptor is not None:
            status_getter = getattr(registry, "get_status", None)
            if callable(status_getter):
                try:
                    status = status_getter(normalized)
                    state = _normalized_text(
                        getattr(status, "state", None)
                        or getattr(status, "status", None)
                        or status
                    )
                except Exception:  # pragma: no cover - defensive seam
                    state = ""
            else:
                state = ""
            return True, state or "available"

    list_agents = getattr(registry, "list_agents", None)
    if callable(list_agents):
        try:
            candidates = list(list_agents())
        except Exception:  # pragma: no cover - defensive seam
            candidates = []
    elif isinstance(registry, (list, tuple)):
        candidates = list(registry)
    else:
        candidates = []

    for item in candidates:
        candidate_id = _normalized_text(
            getattr(item, "agent_id", None)
            or getattr(item, "name", None)
            or (item.get("agent_id") if isinstance(item, dict) else None)
            or (item.get("name") if isinstance(item, dict) else None)
        )
        if candidate_id != normalized:
            continue
        state = _normalized_text(
            getattr(item, "state", None)
            or getattr(item, "status", None)
            or (item.get("state") if isinstance(item, dict) else None)
            or (item.get("status") if isinstance(item, dict) else None)
        )
        return True, state or "available"
    return False, ""


def _is_available_state(state: str) -> bool:
    normalized = _normalized_text(state).lower()
    if not normalized:
        return True
    return normalized in {"available", "healthy", "online", "ready", "unknown"}


def _best_delegate_message(*, summary: str, outputs: dict[str, Any] | None) -> str:
    if summary:
        return summary
    normalized_outputs = dict(outputs or {})
    for key in ("answer", "message", "summary", "result", "output"):
        value = normalized_outputs.get(key)
        text = _normalized_text(value)
        if text:
            return text
    return ""


def _normalized_error_details(raw: Any) -> dict[str, Any]:
    return dict(raw) if isinstance(raw, dict) else {}


def _delegation_context_payload(
    context: ChildContext,
    *,
    fallback: Any | None = None,
) -> dict[str, Any] | None:
    raw = getattr(context, "delegation_context", None) or fallback
    if raw is None:
        return None
    model_dump = getattr(raw, "model_dump", None)
    if callable(model_dump):
        payload = model_dump(mode="json")
    elif isinstance(raw, dict):
        payload = dict(raw)
    else:
        return None
    return payload if payload else None


def _runner_task_manager(ctx) -> Any | None:
    runner = runner_from_context(ctx)
    return getattr(runner, "task_manager", None) if runner is not None else None


def _transition_linked_task(
    manager: Any,
    *,
    task_id: str,
    state_name: str,
    failure_reason: str | None = None,
) -> None:
    if manager is None:
        return
    record = manager.get_task(task_id)
    if record is not None:
        state_type = type(record.state)
        to_state = getattr(state_type, state_name, _normalized_text(state_name).lower())
    else:
        to_state = _normalized_text(state_name).lower()
    manager.transition_task(
        task_id=task_id,
        to_state=to_state,
        failure_reason=failure_reason,
    )


class AcceptOrFailResolver(AgentResolver):
    def resolve(
        self,
        *,
        target_agent_id: str | None,
        target_capability: str | None,
        registry: Any,
    ) -> str:
        del target_capability
        normalized = _normalized_text(target_agent_id)
        if not normalized:
            raise ValueError("delegate requires target_agent_id in v1")
        exists, state = _describe_registry_state(registry, agent_id=normalized)
        if not exists:
            raise ValueError(f"Unknown delegate target agent: {normalized}")
        if not _is_available_state(state):
            raise ValueError(
                f"Delegate target agent is unavailable: {normalized} (state={state})"
            )
        return normalized


class SyncCommandStrategy(DelegationStrategy):
    def execute(
        self,
        *,
        ctx,
        payload: DelegatePayload,
        resolved_agent_id: str,
        delegation_context: ChildContext,
        idempotency_key: str,
    ) -> DelegationExecution:
        params: dict[str, Any] = {
            "goal": payload.goal,
            "summary": delegation_context.summary,
            "constraints": list(delegation_context.constraints or []),
        }
        if delegation_context.active_skill_id:
            params["active_skill_id"] = delegation_context.active_skill_id
        if payload.target_capability:
            params["target_capability"] = payload.target_capability
        parent_context = _delegation_context_payload(
            delegation_context,
            fallback=payload.delegation_context,
        )
        if parent_context is not None:
            params["delegation_context"] = parent_context

        command = AgentCommand(
            title=f"delegate to {resolved_agent_id}: {payload.goal[:60]}",
            target_agent_id=resolved_agent_id,
            method="delegate",
            params=params,
            inputs={"user_input": ctx.user_input or payload.goal},
            success_criteria={"status": "success"},
            idempotency_key=idempotency_key,
            timeout_ms=payload.timeout_ms,
            expect_async=False,
        )
        action_result, job = ctx.act_command(command=command)
        if job is not None:
            action_result = ActionResult(
                command_id=command.command_id,
                status=BRAIN_ACTION_STATUS_TIMEOUT,
                summary="Delegation did not complete synchronously.",
                error=ActionError(
                    code="DELEGATE_UNEXPECTED_ASYNC",
                    message="Delegate mode v1 requires synchronous completion.",
                    details={
                        "reason_code": "delegate_unexpected_async",
                        "task_id": getattr(job, "task_id", None),
                    },
                ),
            )
        return DelegationExecution(
            action_result=action_result,
            command=command,
            job=job,
        )


class AsyncJobStrategy(DelegationStrategy):
    def execute(
        self,
        *,
        ctx,
        payload: DelegatePayload,
        resolved_agent_id: str,
        delegation_context: ChildContext,
        idempotency_key: str,
    ) -> DelegationExecution:
        params: dict[str, Any] = {
            "goal": payload.goal,
            "summary": delegation_context.summary,
            "constraints": list(delegation_context.constraints or []),
        }
        if delegation_context.active_skill_id:
            params["active_skill_id"] = delegation_context.active_skill_id
        if payload.target_capability:
            params["target_capability"] = payload.target_capability
        parent_context = _delegation_context_payload(
            delegation_context,
            fallback=payload.delegation_context,
        )
        if parent_context is not None:
            params["delegation_context"] = parent_context

        command = AgentCommand(
            title=f"delegate to {resolved_agent_id}: {payload.goal[:60]}",
            target_agent_id=resolved_agent_id,
            method="delegate",
            params=params,
            inputs={"user_input": ctx.user_input or payload.goal},
            success_criteria={"status": "success"},
            idempotency_key=idempotency_key,
            timeout_ms=payload.timeout_ms,
            expect_async=True,
        )
        action_result, job = ctx.act_command(command=command)
        if job is None:
            action_result = ActionResult(
                command_id=command.command_id,
                status=BRAIN_ACTION_STATUS_FAILED,
                summary="Async delegation did not return a job handle.",
                error=ActionError(
                    code="DELEGATE_ASYNC_MISSING_JOB",
                    message="Async delegation did not return a job handle.",
                    details={"reason_code": "delegate_async_missing_job"},
                ),
            )
        return DelegationExecution(
            action_result=action_result,
            command=command,
            job=job,
        )


class DirectStatusMapper(A2AStatusMapper):
    def map_result(
        self,
        *,
        ctx,
        payload: DelegatePayload,
        resolved_agent_id: str,
        action_result: ActionResult,
    ) -> ExecutionResult:
        del payload
        status = _normalized_text(action_result.status).lower()
        summary = _normalized_text(action_result.summary)
        error = action_result.error
        if status == BRAIN_ACTION_STATUS_SUCCESS:
            return ExecutionResult(
                status="done",
                working_state=ctx.state,
                message=summary or f"Delegation to {resolved_agent_id} succeeded.",
                action_result=action_result,
            )
        if status == BRAIN_ACTION_STATUS_NEEDS_USER:
            return ExecutionResult(
                status="waiting_user",
                working_state=ctx.state,
                message=summary
                or f"Delegated agent {resolved_agent_id} requested clarification.",
                action_result=action_result,
            )
        if status == BRAIN_ACTION_STATUS_TIMEOUT:
            message = summary or f"Delegation to {resolved_agent_id} timed out."
        elif status == BRAIN_ACTION_STATUS_BLOCKED:
            code = _normalized_text(getattr(error, "code", None)).upper()
            if code == "BUDGET_EXCEEDED":
                message = "Delegation blocked: a2a budget exhausted."
            else:
                message = summary or f"Delegation to {resolved_agent_id} was blocked."
        elif status == BRAIN_ACTION_STATUS_RETRY:
            message = summary or f"Delegation to {resolved_agent_id} returned retry."
        else:
            message = (
                _normalized_text(getattr(error, "message", None))
                or summary
                or f"Delegation to {resolved_agent_id} failed."
            )
        return ExecutionResult(
            status="error",
            working_state=ctx.state,
            message=message,
            action_result=action_result,
        )

    def map_job_status(
        self,
        *,
        ctx,
        payload: DelegatePayload,
        resolved_agent_id: str,
        job_id: str,
        job_status: str,
        summary: str = "",
        outputs: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> ExecutionResult:
        del payload
        normalized_status = _normalized_text(job_status).lower()
        normalized_outputs = dict(outputs or {})
        normalized_error = dict(error or {})
        message = _best_delegate_message(
            summary=summary,
            outputs=normalized_outputs,
        )
        if normalized_status == "pending":
            return ExecutionResult(
                status="pending",
                working_state=ctx.state,
                message=message or f"Delegation to {resolved_agent_id} is pending.",
            )
        if normalized_status == "running":
            return ExecutionResult(
                status="pending",
                working_state=ctx.state,
                message=message or f"Delegation to {resolved_agent_id} is in progress.",
            )
        if normalized_status in {"completed", "done", "success"}:
            action_result = ActionResult(
                command_id=job_id,
                status=BRAIN_ACTION_STATUS_SUCCESS,
                summary=message or f"Delegation to {resolved_agent_id} succeeded.",
                outputs=normalized_outputs,
            )
            return ExecutionResult(
                status="done",
                working_state=ctx.state,
                message=action_result.summary,
                action_result=action_result,
            )
        if normalized_status == "cancelled":
            cancelled_message = (
                _normalized_text(normalized_error.get("message"))
                or message
                or f"Delegation to {resolved_agent_id} was cancelled."
            )
            action_result = ActionResult(
                command_id=job_id,
                status=BRAIN_ACTION_STATUS_FAILED,
                summary=cancelled_message,
                outputs=normalized_outputs,
                error=ActionError(
                    code=_normalized_text(normalized_error.get("code"))
                    or "DELEGATE_JOB_CANCELLED",
                    message=cancelled_message,
                    details=_normalized_error_details(normalized_error.get("details")),
                ),
            )
            return ExecutionResult(
                status="stopped",
                working_state=ctx.state,
                message=action_result.summary,
                action_result=action_result,
            )
        failure_message = (
            _normalized_text(normalized_error.get("message"))
            or message
            or f"Delegation to {resolved_agent_id} failed."
        )
        action_result = ActionResult(
            command_id=job_id,
            status=BRAIN_ACTION_STATUS_FAILED,
            summary=failure_message,
            outputs=normalized_outputs,
            error=ActionError(
                code=_normalized_text(normalized_error.get("code"))
                or "DELEGATE_JOB_FAILED",
                message=failure_message,
                details=_normalized_error_details(normalized_error.get("details")),
            ),
        )
        return ExecutionResult(
            status="error",
            working_state=ctx.state,
            message=action_result.summary,
            action_result=action_result,
        )


class PollingResumeStrategy:
    def __init__(self, *, status_mapper: A2AStatusMapper | None = None) -> None:
        self._status_mapper = status_mapper or DirectStatusMapper()

    def check(
        self,
        *,
        ctx,
        payload: DelegatePayload,
        resolved_agent_id: str,
        job_id: str,
    ) -> ExecutionResult:
        runner = runner_from_context(ctx)
        a2a_api = getattr(runner, "a2a_api", None) if runner is not None else None
        poll_task = getattr(a2a_api, "poll_task", None)
        if not callable(poll_task):
            return ExecutionResult(
                status="error",
                working_state=ctx.state,
                message="Async delegation polling is unavailable.",
                action_result=ActionResult(
                    command_id=job_id,
                    status=BRAIN_ACTION_STATUS_FAILED,
                    summary="Async delegation polling is unavailable.",
                    error=ActionError(
                        code="DELEGATE_POLL_UNAVAILABLE",
                        message="Async delegation polling is unavailable.",
                    ),
                ),
            )
        raw = poll_task(
            task_id=job_id,
            session_id=ctx.state.session_id,
            trace_id=str(getattr(ctx.state, "trace_id", "") or ""),
        )
        normalized = dict(raw or {}) if isinstance(raw, dict) else {}
        return self._status_mapper.map_job_status(
            ctx=ctx,
            payload=payload,
            resolved_agent_id=resolved_agent_id,
            job_id=job_id,
            job_status=_normalized_text(normalized.get("status")) or "failed",
            summary=_normalized_text(normalized.get("summary")),
            outputs=normalized.get("outputs")
            if isinstance(normalized.get("outputs"), dict)
            else {},
            error=normalized.get("error")
            if isinstance(normalized.get("error"), dict)
            else {},
        )


class FailOnClarificationPolicy(ClarificationPolicy):
    def on_clarification_needed(
        self,
        *,
        delegate_result: ActionResult,
        original_context,
    ) -> ClarificationAction:
        del delegate_result, original_context
        return ClarificationAction.FAIL


class PassThroughSynthesizer(ResultSynthesizer):
    def synthesize(
        self,
        *,
        ctx,
        results: list[SubtaskResult],
    ) -> ExecutionResult:
        if not results:
            return ExecutionResult(
                status="error",
                working_state=ctx.state,
                message="Delegate result was empty.",
            )
        result = results[0]
        status = "done" if result.status == "completed" else "error"
        message = _normalized_text(result.output) or _normalized_text(result.error)
        return ExecutionResult(
            status=status,
            working_state=ctx.state,
            message=message or "Delegate result was empty.",
        )


class HashKeyGenerator(IdempotencyKeyGenerator):
    def generate(
        self,
        *,
        session_id: str,
        trace_id: str,
        goal: str,
    ) -> str:
        digest = hashlib.sha256(
            f"{session_id}:{trace_id}:{goal}".encode("utf-8")
        ).hexdigest()
        return digest[:32]


class StatusMessageObserver(DelegationObserver):
    def emit(
        self,
        *,
        ctx,
        mode_state: str,
        label: str,
        target_agent_id: str | None = None,
    ) -> None:
        payload = {
            "target_agent_id": _normalized_text(target_agent_id) or None,
        }
        ctx.emit_status(
            source_phase="ACT",
            runtime_status="acting",
            detail_text=label,
            mode="act",
            mode_state=mode_state,
            mode_label=label,
            payload={
                "execution.target": "delegated",
                **{key: value for key, value in payload.items() if value is not None},
            },
        )


class TaskManagerTaskTracker(DelegationTaskTracker):
    def __init__(self) -> None:
        self._manager = None

    def create_linked_task(
        self,
        *,
        ctx,
        job_id: str,
        target_agent_id: str,
        goal: str,
    ) -> str:
        manager = _runner_task_manager(ctx)
        if manager is None:
            return ""
        self._manager = manager
        record = manager.create_linked_task(
            linked_job_id=job_id,
            agent_id=ctx.state.agent_id,
            metadata={
                "job_id": job_id,
                "target_agent_id": target_agent_id,
                "goal": goal,
                "kind": "delegation",
                "mode_name": "act",
                "execution_target": "delegated",
                "parent_session_id": ctx.state.session_id,
            },
        )
        return record.task_id

    def mark_done(self, *, task_id: str) -> None:
        normalized = _normalized_text(task_id)
        if not normalized:
            return
        manager = getattr(self, "_manager", None)
        if manager is None:
            return
        _transition_linked_task(manager, task_id=normalized, state_name="DONE")

    def mark_failed(self, *, task_id: str, message: str) -> None:
        normalized = _normalized_text(task_id)
        if not normalized:
            return
        manager = getattr(self, "_manager", None)
        if manager is None:
            return
        _transition_linked_task(
            manager,
            task_id=normalized,
            state_name="FAILED",
            failure_reason=_normalized_text(message) or "failed",
        )

    def mark_cancelled(self, *, task_id: str) -> None:
        normalized = _normalized_text(task_id)
        if not normalized:
            return
        manager = getattr(self, "_manager", None)
        if manager is None:
            return
        _transition_linked_task(manager, task_id=normalized, state_name="CANCELLED")

    def bind_context(self, *, ctx) -> None:
        self._manager = _runner_task_manager(ctx)


class DefaultAsyncCancellationPolicy(AsyncCancellationPolicy):
    def __init__(self) -> None:
        self._preflight = AbortOnNewMessagePolicy()

    def should_cancel(
        self,
        *,
        ctx,
        results: list[SubtaskResult],
        attempts: int,
    ) -> bool:
        return self._preflight.should_cancel(
            ctx=ctx,
            results=results,
            attempts=attempts,
        )

    def cancel_async(
        self,
        *,
        ctx,
        job_id: str,
        task_id: str | None = None,
    ) -> ExecutionResult:
        runner = runner_from_context(ctx)
        a2a_api = getattr(runner, "a2a_api", None) if runner is not None else None
        cancel_task = getattr(a2a_api, "cancel_task", None)
        if not callable(cancel_task):
            return ExecutionResult(
                status="error",
                working_state=ctx.state,
                message="Async delegation cancellation is unavailable.",
            )
        raw = cancel_task(
            task_id=job_id,
            session_id=ctx.state.session_id,
            trace_id=str(getattr(ctx.state, "trace_id", "") or ""),
        )
        normalized = dict(raw or {}) if isinstance(raw, dict) else {}
        if task_id:
            tracker = TaskManagerTaskTracker()
            tracker.bind_context(ctx=ctx)
            tracker.mark_cancelled(task_id=task_id)
        message = _normalized_text(normalized.get("summary")) or "Delegation cancelled."
        error = (
            normalized.get("error") if isinstance(normalized.get("error"), dict) else {}
        )
        return ExecutionResult(
            status="stopped",
            working_state=ctx.state,
            message=message,
            action_result=ActionResult(
                command_id=job_id,
                status=BRAIN_ACTION_STATUS_FAILED,
                summary=message,
                error=ActionError(
                    code=_normalized_text(error.get("code"))
                    or "DELEGATE_JOB_CANCELLED",
                    message=_normalized_text(error.get("message")) or message,
                ),
            ),
        )


class SimpleA2ABudgetPolicy(BudgetPolicy):
    def check_budget(self, *, state: WorkingState) -> bool:
        return (
            int(getattr(getattr(state, "budgets_remaining", None), "a2a_calls", 0) or 0)
            > 0
        )

    def deduct(self, *, state: WorkingState) -> None:
        del state


class RegistryDiscoveryProvider(AgentDiscoveryProvider):
    def get_registry(self, *, ctx) -> Any:
        runner = runner_from_context(ctx)
        if runner is None:
            return {}
        explicit_registry = getattr(runner, "agent_registry", None)
        if explicit_registry is not None:
            return explicit_registry
        a2a_api = getattr(runner, "a2a_api", None)
        if a2a_api is None:
            return {}
        runtime_getter = getattr(a2a_api, "_ensure_runtime", None)
        if callable(runtime_getter):
            try:
                runtime = runtime_getter()
            except Exception:  # pragma: no cover - defensive seam
                runtime = None
            if runtime is not None:
                registry = getattr(runtime, "registry", None)
                if registry is not None:
                    return registry
                list_agents = getattr(runtime, "list_agents", None)
                if callable(list_agents):
                    try:
                        return list_agents()
                    except Exception:  # pragma: no cover - defensive seam
                        return {}
        list_agents = getattr(a2a_api, "list_agents", None)
        if callable(list_agents):
            try:
                return list_agents()
            except Exception:  # pragma: no cover - defensive seam
                return {}
        return {}


__all__ = [
    "AbortOnNewMessagePolicy",
    "AcceptOrFailResolver",
    "AsyncJobStrategy",
    "DefaultAsyncCancellationPolicy",
    "DirectStatusMapper",
    "FailFastPolicy",
    "FailOnClarificationPolicy",
    "HashKeyGenerator",
    "PassThroughSynthesizer",
    "PollingResumeStrategy",
    "RegistryDiscoveryProvider",
    "SimpleA2ABudgetPolicy",
    "StatusMessageObserver",
    "SummaryInheritancePolicy",
    "SyncCommandStrategy",
    "TaskManagerTaskTracker",
]
