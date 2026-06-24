from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from openminion.modules.brain.loop.strategies.research import RESEARCH_MODE
from openminion.modules.brain.runner.cron_resume.linker import DefaultCronJobLinker
from openminion.modules.brain.runner.cron_resume.policies import (
    ExponentialBackoffResumePolicy,
)
from openminion.modules.session.storage.repository import create_sqlite_cron_repository
from openminion.modules.task import TaskLifecycleState, TaskManager


def _manager(tmp_path: Path) -> TaskManager:
    repo = create_sqlite_cron_repository(db_path=tmp_path / "sessions.db")
    return TaskManager.from_cron_repository(repo)


def test_exponential_backoff_policy_only_enables_resumable_paused_modes() -> None:
    policy = ExponentialBackoffResumePolicy()
    resumable_spec = type("Spec", (), {"has_resume": True})()
    non_resumable_spec = type("Spec", (), {"has_resume": False})()
    paused_record = type("Record", (), {"state": TaskLifecycleState.PAUSED})()
    active_record = type("Record", (), {"state": TaskLifecycleState.ACTIVE})()

    assert policy.should_create_cron_job(paused_record, resumable_spec) is True
    assert policy.should_create_cron_job(paused_record, non_resumable_spec) is False
    assert policy.should_create_cron_job(active_record, resumable_spec) is False


def test_exponential_backoff_policy_matches_default_sequence() -> None:
    policy = ExponentialBackoffResumePolicy()
    current = timedelta(seconds=30)
    observed = [current]
    for attempt in range(1, 8):
        current = policy.next_backoff_interval(attempt, current)
        observed.append(current)

    assert observed == [
        timedelta(seconds=30),
        timedelta(minutes=1),
        timedelta(minutes=2),
        timedelta(minutes=5),
        timedelta(minutes=10),
        timedelta(minutes=30),
        timedelta(hours=1),
        timedelta(hours=1),
    ]


def test_exponential_backoff_policy_stops_after_attempt_or_elapsed_limits() -> None:
    policy = ExponentialBackoffResumePolicy()
    paused_record = type("Record", (), {"state": TaskLifecycleState.PAUSED})()

    assert policy.should_stop_retrying(49, timedelta(hours=23), paused_record) is False
    assert policy.should_stop_retrying(50, timedelta(hours=1), paused_record) is True
    assert policy.should_stop_retrying(1, timedelta(hours=24), paused_record) is True


def test_default_cron_job_linker_creates_bidirectional_linkage(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    record = manager.create_task(
        session_id="s-unit",
        mode_name=RESEARCH_MODE,
        goal="resume the task",
        agent_id="agent-a",
    )
    job_id = manager.create_cron_job(
        name="resume-job",
        schedule={"kind": "every", "every_ms": 60_000},
        payload={"kind": "agentTurn", "message": "resume", "session_id": "s-unit"},
        agent_id="agent-a",
        session_target="isolated",
    )

    linker = DefaultCronJobLinker(task_manager=manager)
    linker.link(record.task_id, job_id)

    updated = manager.get_task(record.task_id)
    job = manager.get_scheduled_job(job_id)

    assert updated is not None
    assert updated.metadata["linked_cron_job_id"] == job_id
    assert job is not None
    assert job["payload"]["linked_task_id"] == record.task_id
    assert linker.get_linked_cron_job(record.task_id) == job_id
    assert linker.get_linked_task(job_id) == record.task_id


def test_default_cron_job_linker_unlink_is_noop_for_unlinked_task(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    record = manager.create_task(
        session_id="s-noop",
        mode_name=RESEARCH_MODE,
        goal="no linked cron",
        agent_id="agent-a",
    )

    DefaultCronJobLinker(task_manager=manager).unlink_and_delete(record.task_id)

    loaded = manager.get_task(record.task_id)
    assert loaded is not None
    assert "linked_cron_job_id" not in loaded.metadata


def test_terminal_task_transition_triggers_linked_cron_cleanup(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    record = manager.create_task(
        session_id="s-cleanup",
        mode_name=RESEARCH_MODE,
        goal="clean linked cron",
        agent_id="agent-a",
    )
    job_id = manager.create_cron_job(
        name="cleanup-job",
        schedule={"kind": "every", "every_ms": 60_000},
        payload={"kind": "agentTurn", "message": "resume", "session_id": "s-cleanup"},
        agent_id="agent-a",
        session_target="isolated",
    )
    DefaultCronJobLinker(task_manager=manager).link(record.task_id, job_id)

    completed = manager.transition_task(task_id=record.task_id, to_state="done")

    assert completed.state == TaskLifecycleState.DONE
    assert manager.get_scheduled_job(job_id) is None
    refreshed = manager.get_task(record.task_id)
    assert refreshed is not None
    assert "linked_cron_job_id" not in refreshed.metadata
