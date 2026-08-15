import hashlib
from uuid import uuid4
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from openminion.base.logging import get_logger
from openminion.modules.a2a.interfaces import A2A_OBSERVABILITY_SCHEMA_VERSION
from openminion.modules.telemetry.events.module import emit_module_telemetry
from openminion.modules.telemetry.execution_lifecycle import build_execution_traceparent
from openminion.modules.tool.constants import TOOL_A2A_DELEGATE_DEFAULT_TIMEOUT_SECONDS


_LOG = get_logger("tool.runtime.delegation")
_DELEGATE_METHOD = "delegate"


@dataclass
class A2ADelegateResult:
    ok: bool
    status: str
    content: str = ""
    error_code: str = ""
    error_message: str = ""
    target_agent_id: str = ""
    trace_id: str = ""
    task_id: str = ""
    outputs: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class A2ADelegateApi(Protocol):
    """A2A delegation seam available to tool runtime handlers."""

    def delegate(
        self,
        *,
        agent_id: str,
        instruction: str,
        timeout_seconds: int,
        mode: str = "sync",
        permission_mode: str = "ask",
        workspace_root: str = "",
        cwd: str = "",
    ) -> A2ADelegateResult: ...

    def status(self, *, task_id: str) -> A2ADelegateResult: ...

    def resume(self, *, task_id: str) -> A2ADelegateResult: ...

    def cancel(self, *, task_id: str) -> A2ADelegateResult: ...


_SUCCESS_STATUS = "success"
_RUNNING_STATUSES = frozenset({"pending", "running"})
_TERMINAL_LIFECYCLE_STATUSES = frozenset(
    {"done", "failed", "success", "canceled", "cancelled"}
)


def is_a2a_delegate_running_status(status: Any) -> bool:
    return str(status or "").strip() in _RUNNING_STATUSES


def map_a2a_delegate_result(
    raw: Any, *, target: str, trace_id: str, async_requested: bool = False
) -> A2ADelegateResult:
    payload = raw if isinstance(raw, dict) else {}
    status = payload.get("status")
    summary = str(payload.get("summary", "") or "").strip()
    outputs = payload.get("outputs")
    normalized_outputs = dict(outputs) if isinstance(outputs, dict) else {}
    task_id = str(payload.get("task_id", "") or "").strip()

    if str(status or "").strip() == _SUCCESS_STATUS:
        return A2ADelegateResult(
            ok=True,
            status="success",
            content=summary,
            target_agent_id=target,
            trace_id=trace_id,
            task_id=task_id,
            outputs=normalized_outputs,
        )
    if is_a2a_delegate_running_status(status):
        return A2ADelegateResult(
            ok=async_requested,
            status="running",
            content=summary or "Delegated A2A job is still running.",
            error_code="" if async_requested else "A2A_DELEGATE_ASYNC_UNREQUESTED",
            error_message=""
            if async_requested
            else (
                "task.delegate was requested synchronously, but the target "
                "returned an async job. Retry with mode='async' to receive a "
                "resumable task handle."
            ),
            target_agent_id=target,
            trace_id=trace_id,
            task_id=task_id,
            outputs=normalized_outputs,
        )

    error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
    return A2ADelegateResult(
        ok=False,
        status="failed",
        content=summary,
        error_code=str(error.get("code") or "A2A_DELEGATE_FAILED"),
        error_message=str(error.get("message") or summary or "A2A delegation failed."),
        target_agent_id=target,
        trace_id=trace_id,
        task_id=task_id,
        outputs=normalized_outputs,
    )


def map_a2a_job_result(raw: Any, *, trace_id: str, task_id: str) -> A2ADelegateResult:
    payload = raw if isinstance(raw, dict) else {}
    status = str(payload.get("status") or payload.get("state") or "").strip()
    summary = str(payload.get("summary", "") or "").strip()
    error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
    outputs = payload.get("outputs")
    normalized_outputs = dict(outputs) if isinstance(outputs, dict) else {}
    if not normalized_outputs and isinstance(payload.get("result_inline"), dict):
        normalized_outputs = dict(payload.get("result_inline") or {})

    if status in _RUNNING_STATUSES or status in _TERMINAL_LIFECYCLE_STATUSES:
        lifecycle_outputs = dict(payload)
        if normalized_outputs:
            lifecycle_outputs["outputs"] = normalized_outputs
        return A2ADelegateResult(
            ok=True,
            status=status or "running",
            content=summary,
            trace_id=trace_id,
            task_id=str(payload.get("task_id") or task_id),
            outputs=lifecycle_outputs,
        )

    return A2ADelegateResult(
        ok=False,
        status=status or "failed",
        content=summary,
        error_code=str(error.get("code") or "A2A_JOB_FAILED"),
        error_message=str(error.get("message") or summary or "A2A job failed."),
        trace_id=trace_id,
        task_id=str(payload.get("task_id") or task_id),
        outputs=normalized_outputs,
    )


def run_a2a_job_lifecycle(
    *,
    caller: Any,
    operation: str,
    task_id: str,
    parent_agent_id: str,
) -> A2ADelegateResult:
    normalized_task_id = str(task_id or "").strip()
    if not normalized_task_id:
        return A2ADelegateResult(
            ok=False,
            status="failed",
            error_code="TASK_DELEGATE_INVALID_ARGS",
            error_message=f"task.delegate {operation} requires task_id.",
        )
    if not callable(caller):
        return A2ADelegateResult(
            ok=False,
            status="failed",
            error_code="A2A_DELEGATE_LIFECYCLE_UNAVAILABLE",
            error_message=(
                "The configured A2A delegate seam does not expose "
                f"{operation} lifecycle support."
            ),
            task_id=normalized_task_id,
        )

    trace_id = f"task-delegate:{operation}:{normalized_task_id}"
    session_id = f"task-delegate::{parent_agent_id or 'agent'}"
    raw = caller(task_id=normalized_task_id, session_id=session_id, trace_id=trace_id)
    return map_a2a_job_result(raw, trace_id=trace_id, task_id=normalized_task_id)


class A2aRuntimeDelegateAdapter:
    """Tool-surface delegation over any A2A ``call`` seam."""

    def __init__(
        self,
        *,
        a2a_call: Any,
        parent_agent_id: str = "",
        telemetryctl: Any | None = None,
    ) -> None:
        self._a2a_call = a2a_call
        self._parent_agent_id = str(parent_agent_id or "").strip()
        self._telemetryctl = telemetryctl
        self._observability: dict[str, str] = {}

    def bind_observability(
        self,
        *,
        session_id: str,
        turn_id: str,
        invocation_id: str,
        execution_id: str,
        traceparent: str = "",
        tracestate: str = "",
    ) -> None:
        self._observability = {
            "session_id": str(session_id),
            "turn_id": str(turn_id),
            "invocation_id": str(invocation_id),
            "execution_id": str(execution_id),
            "traceparent": str(traceparent),
            "tracestate": str(tracestate),
        }

    def _idempotency_key(
        self,
        *,
        target: str,
        instruction: str,
        workspace_root: str,
        cwd: str,
    ) -> str:
        digest = hashlib.sha256(
            (
                f"{self._parent_agent_id}|{target}|{instruction}|{workspace_root}|{cwd}"
            ).encode("utf-8")
        ).hexdigest()[:32]
        return f"task-delegate:{digest}"

    def _handoff_context(
        self, target: str
    ) -> tuple[str, str, dict[str, str], dict | None]:
        session_id = self._observability.get("session_id", "")
        turn_id = self._observability.get("turn_id", "")
        invocation_id = self._observability.get("invocation_id", "")
        execution_id = self._observability.get("execution_id", "")
        traceparent = self._observability.get("traceparent", "")
        if not traceparent and invocation_id and execution_id:
            traceparent = build_execution_traceparent(invocation_id, execution_id)
        handoff_id = str(uuid4())
        payload = {
            "handoff_id": handoff_id,
            "handoff_role": "caller",
            "target_agent": target,
        }
        if not (invocation_id and execution_id and traceparent):
            return session_id, turn_id, payload, None
        return (
            session_id,
            turn_id,
            payload,
            {
                "schema_version": A2A_OBSERVABILITY_SCHEMA_VERSION,
                "invocation_id": invocation_id,
                "execution_id": execution_id,
                "handoff_id": handoff_id,
                "traceparent": traceparent,
                "tracestate": self._observability.get("tracestate") or None,
            },
        )

    def _emit_handoff(
        self,
        session_id: str,
        turn_id: str,
        event_type: str,
        payload: dict[str, Any],
        status: str,
    ) -> None:
        emit_module_telemetry(
            self._telemetryctl,
            "emit_canonical_event",
            session_id,
            turn_id,
            event_type,
            payload,
            status=status,
            logger=_LOG,
        )

    def _call_delegate(
        self,
        *,
        target: str,
        instruction: str,
        timeout: int,
        mode: str,
        permission_mode: str,
        workspace_root: str,
        cwd: str,
        idempotency_key: str,
        observability: dict | None,
    ) -> Any:
        return self._a2a_call(
            command={
                "command_id": idempotency_key,
                "target_agent_id": target,
                "method": _DELEGATE_METHOD,
                "expect_async": mode == "async",
                "params": {
                    "goal": instruction,
                    "instruction": instruction,
                    "timeout_seconds": timeout,
                    "mode": mode,
                    "permission_mode": permission_mode,
                    "workspace_root": workspace_root,
                    "cwd": cwd,
                },
                "timeout_ms": timeout * 1000,
                "idempotency_key": idempotency_key,
                "observability": observability,
            },
            session_id=f"task-delegate::{self._parent_agent_id or 'agent'}",
            trace_id=idempotency_key,
        )

    def delegate(
        self,
        *,
        agent_id: str,
        instruction: str,
        timeout_seconds: int,
        mode: str = "sync",
        permission_mode: str = "ask",
        workspace_root: str = "",
        cwd: str = "",
    ) -> A2ADelegateResult:
        target = str(agent_id or "").strip()
        text = str(instruction or "").strip()
        normalized_mode = str(mode or "sync").strip().lower()
        try:
            timeout = int(timeout_seconds)
        except (TypeError, ValueError):
            timeout = TOOL_A2A_DELEGATE_DEFAULT_TIMEOUT_SECONDS
        if timeout <= 0:
            timeout = TOOL_A2A_DELEGATE_DEFAULT_TIMEOUT_SECONDS
        if not target or not text:
            return A2ADelegateResult(
                ok=False,
                status="failed",
                error_code="TASK_DELEGATE_INVALID_ARGS",
                error_message="task.delegate requires agent_id and instruction.",
                target_agent_id=target,
            )

        normalized_workspace_root = str(workspace_root or "").strip()
        normalized_cwd = str(cwd or workspace_root or "").strip()
        idem = self._idempotency_key(
            target=target,
            instruction=text,
            workspace_root=normalized_workspace_root,
            cwd=normalized_cwd,
        )
        trace_id = idem
        session_id, turn_id, handoff_payload, observability = self._handoff_context(
            target
        )
        self._emit_handoff(
            session_id,
            turn_id,
            "agent.handoff.started",
            handoff_payload,
            "started",
        )
        try:
            raw = self._call_delegate(
                target=target,
                instruction=text,
                timeout=timeout,
                mode=normalized_mode,
                permission_mode=str(permission_mode or "ask").strip().lower() or "ask",
                workspace_root=normalized_workspace_root,
                cwd=normalized_cwd,
                idempotency_key=idem,
                observability=observability,
            )
        except (RuntimeError, ValueError, TypeError, AttributeError, KeyError) as exc:
            self._emit_handoff(
                session_id,
                turn_id,
                "agent.handoff.failed",
                {**handoff_payload, "error": {"type": type(exc).__name__}},
                "failed",
            )
            _LOG.warning("task.delegate A2A call failed: %s", exc)
            return A2ADelegateResult(
                ok=False,
                status="failed",
                error_code="A2A_RUNTIME_ERROR",
                error_message=str(exc),
                target_agent_id=target,
                trace_id=trace_id,
            )

        result = map_a2a_delegate_result(
            raw,
            target=target,
            trace_id=trace_id,
            async_requested=normalized_mode == "async",
        )
        self._emit_handoff(
            session_id,
            turn_id,
            "agent.handoff.completed" if result.ok else "agent.handoff.failed",
            handoff_payload,
            "completed" if result.ok else "failed",
        )
        return result

    def status(self, *, task_id: str) -> A2ADelegateResult:
        return self._run_lifecycle("status", task_id, "poll_task")

    def resume(self, *, task_id: str) -> A2ADelegateResult:
        return self.status(task_id=task_id)

    def cancel(self, *, task_id: str) -> A2ADelegateResult:
        return self._run_lifecycle("cancel", task_id, "cancel_task")

    def _run_lifecycle(
        self, operation: str, task_id: str, method_name: str
    ) -> A2ADelegateResult:
        caller = getattr(self._a2a_call, method_name, None)
        if not callable(caller):
            owner = getattr(self._a2a_call, "__self__", None)
            caller = getattr(owner, method_name, None)
        try:
            return run_a2a_job_lifecycle(
                caller=caller,
                operation=operation,
                task_id=task_id,
                parent_agent_id=self._parent_agent_id,
            )
        except (RuntimeError, ValueError, TypeError, AttributeError, KeyError) as exc:
            _LOG.warning("task.delegate A2A %s failed: %s", operation, exc)
            return A2ADelegateResult(
                ok=False,
                status="failed",
                error_code="A2A_RUNTIME_ERROR",
                error_message=str(exc),
                task_id=str(task_id or "").strip(),
            )


__all__ = [
    "A2ADelegateApi",
    "A2ADelegateResult",
    "A2aRuntimeDelegateAdapter",
    "is_a2a_delegate_running_status",
    "map_a2a_delegate_result",
    "map_a2a_job_result",
    "run_a2a_job_lifecycle",
]
