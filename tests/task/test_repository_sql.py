from __future__ import annotations

import sqlite3
import tempfile
from datetime import datetime

import pytest

from openminion.modules.storage.record_store import RecordStoreSQLite
from openminion.modules.task.storage.repository import SqlTaskRepository
from openminion.modules.task.schemas import PlanStepStatus, ResumePointer, TaskStatus


def _setup_repo(tmp_path: str) -> SqlTaskRepository:
    store = RecordStoreSQLite(tmp_path, wal=False)
    repo = SqlTaskRepository(store)

    now = datetime.utcnow()
    repo.create_task(
        task_id="task-1",
        title="Test task",
        description=None,
        status=TaskStatus.PENDING,
        due_at=None,
        scheduled_at=None,
        wait_at=None,
        created_by_mode=None,
        executing_mode=None,
        created_at=now,
        updated_at=now,
    )
    repo.create_plan(
        plan_id="plan-1",
        task_id="task-1",
        plan_name="Plan",
        root_goal_id=None,
        created_by_mode=None,
        created_at=now,
        updated_at=now,
    )
    repo.create_step(
        step_id="step-1",
        plan_id="plan-1",
        order_index=0,
        title="Step",
        instruction="Do it",
        status=PlanStepStatus.PENDING,
        note=None,
        artifact_refs=[],
        executing_mode=None,
        updated_at=now,
    )
    return repo


def test_pending_action_unique_policy_request_id_enforced() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        repo = _setup_repo(tmp.name)
        cursor = ResumePointer(
            task_id="task-1",
            plan_id="plan-1",
            step_id="step-1",
            attempt=1,
            trace_id="trace-1",
            turn_id="turn-1",
            pack_id="pack-1",
        )
        now = datetime.utcnow()
        repo.record_pending_action(
            pending_action_id="pa-1",
            policy_request_id="policy-1",
            state="NEEDS_APPROVAL",
            reason="approval required",
            cursor=cursor,
            created_at=now,
        )

        with pytest.raises(sqlite3.IntegrityError):
            repo.record_pending_action(
                pending_action_id="pa-2",
                policy_request_id="policy-1",
                state="NEEDS_APPROVAL",
                reason="duplicate",
                cursor=cursor,
                created_at=now,
            )


def test_pending_action_unique_cursor_tuple_enforced() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        repo = _setup_repo(tmp.name)
        cursor = ResumePointer(
            task_id="task-1",
            plan_id="plan-1",
            step_id="step-1",
            attempt=1,
            trace_id="trace-1",
            turn_id="turn-1",
            pack_id="pack-1",
        )
        now = datetime.utcnow()
        repo.record_pending_action(
            pending_action_id="pa-1",
            policy_request_id="policy-1",
            state="NEEDS_APPROVAL",
            reason="approval required",
            cursor=cursor,
            created_at=now,
        )

        with pytest.raises(sqlite3.IntegrityError):
            repo.record_pending_action(
                pending_action_id="pa-2",
                policy_request_id="policy-2",
                state="NEEDS_APPROVAL",
                reason="duplicate cursor",
                cursor=cursor,
                created_at=now,
            )


def test_repository_persists_mode_lineage_columns() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        store = RecordStoreSQLite(tmp.name, wal=False)
        repo = SqlTaskRepository(store)
        now = datetime.utcnow()

        repo.create_task(
            task_id="task-mode",
            title="Mode task",
            description=None,
            status=TaskStatus.PENDING,
            due_at=None,
            scheduled_at=None,
            wait_at=None,
            created_by_mode="plan",
            executing_mode=None,
            created_at=now,
            updated_at=now,
        )
        repo.create_plan(
            plan_id="plan-mode",
            task_id="task-mode",
            plan_name="Mode plan",
            root_goal_id=None,
            created_by_mode="plan",
            created_at=now,
            updated_at=now,
        )
        repo.create_step(
            step_id="step-mode",
            plan_id="plan-mode",
            order_index=1,
            title="Step",
            instruction="Do it",
            status=PlanStepStatus.PENDING,
            note=None,
            artifact_refs=[],
            executing_mode="plan",
            updated_at=now,
        )
        repo.update_task(
            task_id="task-mode",
            executing_mode="plan",
            updated_at=now,
        )

        task_row = repo.get_task("task-mode")
        plan_row = repo.get_plan("plan-mode")
        step_row = repo.get_step("step-mode")

        assert task_row is not None
        assert plan_row is not None
        assert step_row is not None
        assert task_row["created_by_mode"] == "plan"
        assert task_row["executing_mode"] == "plan"
        assert plan_row["created_by_mode"] == "plan"
        assert step_row["executing_mode"] == "plan"
