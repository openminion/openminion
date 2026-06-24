from typing import TYPE_CHECKING, Any

from .runtime.turn import jobs as _jobs_runtime
from ..constants import (
    BRAIN_COMMAND_KIND_AGENT,
    BRAIN_COMMAND_KIND_TOOL,
    BRAIN_JOB_STATUS_FAILED,
)
from ..diagnostics.events import CanonicalEventLogger
from ..schemas import ActionResult, Command, JobHandle, WorkingState

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..runner import BrainRunner


def poll_async_job(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    job: JobHandle,
) -> dict[str, Any] | None:
    provider = str(getattr(job, "provider", "")).strip().lower()
    adapter = None
    if provider == BRAIN_COMMAND_KIND_TOOL:
        adapter = runner.tool_api
    elif provider in {"a2actl", "a2a"}:
        adapter = runner.a2a_api
    if adapter is None:
        return None

    poll_task = getattr(adapter, "poll_task", None)
    poll = getattr(adapter, "poll", None)
    try:
        if callable(poll_task):
            result = poll_task(
                task_id=job.task_id,
                session_id=state.session_id,
                trace_id=state.trace_id or "",
            )
        elif callable(poll):
            result = poll(
                task_id=job.task_id,
                session_id=state.session_id,
                trace_id=state.trace_id or "",
            )
        else:
            return None
    except Exception as exc:
        return {
            "status": BRAIN_JOB_STATUS_FAILED,
            "summary": f"Async job polling failed for {job.task_id}.",
            "error": {"code": "JOB_POLL_FAILED", "message": str(exc)},
        }

    return result if isinstance(result, dict) else None


def remember_idempotency(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    command: Command,
    result: ActionResult,
) -> None:
    if command.kind not in {BRAIN_COMMAND_KIND_TOOL, BRAIN_COMMAND_KIND_AGENT}:
        return
    if not runner.options.idempotency_enabled:
        return
    if not command.idempotency_key:
        return
    state.idempotency_cache[command.idempotency_key] = result
    while len(state.idempotency_cache) > runner.options.idempotency_cache_size:
        oldest_key = next(iter(state.idempotency_cache))
        del state.idempotency_cache[oldest_key]


def reconcile_pending_jobs(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    logger: CanonicalEventLogger,
) -> Any:
    return _jobs_runtime.reconcile_pending_jobs(
        runner,
        state=state,
        logger=logger,
    )
