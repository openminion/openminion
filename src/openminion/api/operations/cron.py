from typing import Any

from openminion.api.queries.cron import resolve_cron_store
from openminion.modules.task.surface import resolve_task_surface_source


class TaskOwnedCronJobError(ValueError):
    code = "task_owned_cron_job"


def _reject_task_owned_job(*, runtime: Any, job_id: str) -> None:
    owner = resolve_task_surface_source(runtime)
    get_task = getattr(owner, "get_task_by_job", None)
    if callable(get_task) and get_task(job_id) is not None:
        raise TaskOwnedCronJobError(
            "task-owned cron jobs must be controlled through /v1/tasks"
        )


def create_cron_job(*, runtime, body: dict[str, Any]) -> str:
    store = resolve_cron_store(runtime)
    return store.add_cron_job(
        name=str(body.get("name", "")).strip(),
        description=str(body.get("description", "")).strip() or None,
        schedule=body.get("schedule"),
        payload=body.get("payload"),
        agent_id=str(body.get("agent_id", "")).strip() or None,
        session_target=body.get("session_target"),
        wake_mode=body.get("wake_mode"),
        delete_after_run=body.get("delete_after_run"),
        misfire_policy=body.get("misfire_policy"),
        max_concurrency=int(body["max_concurrency"])
        if "max_concurrency" in body
        else 1,
        delivery=body.get("delivery"),
        job_id=str(body.get("job_id", "")).strip() or None,
    )


def trigger_cron_job(*, runtime, job_id: str) -> str:
    _reject_task_owned_job(runtime=runtime, job_id=job_id)
    store = resolve_cron_store(runtime)
    return store.trigger_cron_run(job_id=job_id)


def delete_cron_job(*, runtime, job_id: str) -> None:
    _reject_task_owned_job(runtime=runtime, job_id=job_id)
    store = resolve_cron_store(runtime)
    store.delete_cron_job(job_id=job_id)
