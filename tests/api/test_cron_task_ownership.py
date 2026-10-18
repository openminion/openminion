from types import SimpleNamespace

import pytest

from openminion.api.operations import cron


class _TaskManager:
    def __init__(self, owned_job_id: str | None) -> None:
        self._owned_job_id = owned_job_id

    def get_task_by_job(self, job_id: str):
        return object() if job_id == self._owned_job_id else None

    def get_task(self, task_id: str):
        return self.get_task_by_job(task_id)


class _CronStore:
    def trigger_cron_run(self, *, job_id: str) -> str:
        return f"run:{job_id}"

    def delete_cron_job(self, *, job_id: str) -> None:
        self.deleted = job_id


def test_raw_cron_trigger_rejects_task_owned_job(monkeypatch) -> None:
    runtime = SimpleNamespace(task_manager=_TaskManager("task-job"))
    monkeypatch.setattr(cron, "resolve_cron_store", lambda _runtime: _CronStore())

    with pytest.raises(cron.TaskOwnedCronJobError):
        cron.trigger_cron_job(runtime=runtime, job_id="task-job")


def test_raw_cron_delete_rejects_task_owned_job(monkeypatch) -> None:
    runtime = SimpleNamespace(task_manager=_TaskManager("task-job"))
    monkeypatch.setattr(cron, "resolve_cron_store", lambda _runtime: _CronStore())

    with pytest.raises(cron.TaskOwnedCronJobError):
        cron.delete_cron_job(runtime=runtime, job_id="task-job")


def test_raw_cron_trigger_keeps_generic_job_compatibility(monkeypatch) -> None:
    runtime = SimpleNamespace(task_manager=_TaskManager(None))
    monkeypatch.setattr(cron, "resolve_cron_store", lambda _runtime: _CronStore())

    assert cron.trigger_cron_job(runtime=runtime, job_id="generic") == "run:generic"
