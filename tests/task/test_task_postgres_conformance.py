from __future__ import annotations

from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path

import pytest

from openminion.modules.storage.engine import StorageEngineConfig
from openminion.modules.task.storage.repository import SqlTaskRepository
from openminion.modules.task.schemas import PlanStepStatus, ResumePointer, TaskStatus
from openminion.modules.task.storage import build_task_store
from openminion.modules.task.storage.store import PostgresTaskStore, SQLiteTaskStore
from tests.storage.postgres_test_utils import (
    build_postgres_storage_config,
    open_postgres_record_store,
)


def _backend_params():
    return [
        pytest.param("sqlite", id="sqlite"),
        pytest.param("postgres", marks=pytest.mark.postgres, id="postgres"),
    ]


@pytest.fixture(params=_backend_params())
def task_repository_case(request: pytest.FixtureRequest, tmp_path: Path):
    backend = str(request.param)
    with ExitStack() as stack:
        if backend == "sqlite":
            store = SQLiteTaskStore(tmp_path / "task.db")
        else:
            record_store, _schema_name = stack.enter_context(
                open_postgres_record_store("mpt1_task")
            )
            store = PostgresTaskStore(record_store=record_store)
        stack.callback(store.close)
        repository = SqlTaskRepository(store.record_store)
        yield backend, store, repository


def test_task_repository_round_trip(task_repository_case) -> None:
    _backend, _store, repository = task_repository_case
    now = datetime.now(timezone.utc).replace(microsecond=0)
    repository.create_task(
        task_id="task-1",
        title="Task One",
        description="desc",
        status=TaskStatus.PENDING,
        due_at=None,
        scheduled_at=None,
        wait_at=None,
        created_by_mode="plan",
        executing_mode="plan",
        created_at=now,
        updated_at=now,
    )
    repository.create_plan(
        plan_id="plan-1",
        task_id="task-1",
        plan_name="Main",
        root_goal_id=None,
        created_by_mode="plan",
        created_at=now,
        updated_at=now,
    )
    repository.attach_plan_to_task("task-1", "plan-1")
    repository.create_step(
        step_id="step-1",
        plan_id="plan-1",
        order_index=0,
        title="Do it",
        instruction="run thing",
        status=PlanStepStatus.PENDING,
        note=None,
        artifact_refs=["artifact://one"],
        executing_mode="respond",
        updated_at=now,
    )
    repository.update_task(
        "task-1",
        status=TaskStatus.ACTIVE,
        current_plan_id="plan-1",
        next_step_id="step-1",
        updated_at=now,
    )
    repository.update_plan("plan-1", now)
    repository.update_step(
        "step-1",
        status=PlanStepStatus.DONE,
        note="finished",
        artifact_refs=["artifact://two"],
        updated_at=now,
    )
    repository.record_pending_action(
        pending_action_id="pa-1",
        policy_request_id="req-1",
        state="NEEDS_APPROVAL",
        reason="approval",
        cursor=ResumePointer(
            task_id="task-1",
            plan_id="plan-1",
            step_id="step-1",
            attempt=1,
            trace_id="trace-1",
            turn_id="turn-1",
            pack_id="pack-1",
        ),
        created_at=now,
    )
    repository.update_pending_action(
        "req-1",
        resolved_at=now,
        decision_id="decision-1",
    )
    repository.record_idempotency(
        idempotency_key="idem-1",
        task_id="task-1",
        step_id="step-1",
        status=PlanStepStatus.DONE,
        note="ok",
        artifact_refs=["artifact://two"],
    )

    assert repository.get_task("task-1") is not None
    assert repository.get_plan("plan-1") is not None
    assert repository.get_step("step-1") is not None
    assert repository.get_pending_action("req-1") is not None
    assert repository.get_idempotency_record("idem-1") is not None
    assert repository.get_tasks_ready(limit=5)[0]["task_id"] == "task-1"
    assert repository.get_tasks_active(limit=5)[0]["task_id"] == "task-1"
    assert repository.get_steps_for_plan("plan-1")[0]["step_id"] == "step-1"


def test_build_task_store_returns_sqlite_store(tmp_path: Path) -> None:
    store = build_task_store(
        config=StorageEngineConfig(
            root_dir=tmp_path / "storage",
            sqlite_path=tmp_path / "task.db",
            fallback_root=tmp_path,
            record_backend="record.sqlite",
        ),
        database_path=tmp_path / "task.db",
    )
    try:
        assert isinstance(store, SQLiteTaskStore)
    finally:
        store.close()


@pytest.mark.postgres
def test_build_task_store_returns_postgres_store(tmp_path: Path) -> None:
    with open_postgres_record_store("mpt1_task_factory") as (
        _record_store,
        schema_name,
    ):
        store = build_task_store(
            config=build_postgres_storage_config(
                tmp_path=tmp_path,
                schema_name=schema_name,
                sqlite_name="task.db",
            ),
            database_path=tmp_path / "task.db",
        )
        try:
            assert isinstance(store, PostgresTaskStore)
        finally:
            store.close()
