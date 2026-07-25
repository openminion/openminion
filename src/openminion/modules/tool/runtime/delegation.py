from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


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


__all__ = [
    "A2ADelegateApi",
    "A2ADelegateResult",
    "is_a2a_delegate_running_status",
    "map_a2a_delegate_result",
    "map_a2a_job_result",
    "run_a2a_job_lifecycle",
]
