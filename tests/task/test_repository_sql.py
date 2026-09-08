from __future__ import annotations

import sqlite3
import tempfile
from datetime import datetime

import pytest

from openminion.modules.storage.record_store import RecordStoreSQLite
from openminion.modules.storage.migrations.task_tables import migrate_v1_to_v2
from openminion.modules.task.storage.migrations import run_migrations
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


def _create_legacy_pending_actions_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE pending_actions (
            pending_action_id TEXT PRIMARY KEY,
            policy_request_id TEXT UNIQUE NOT NULL,
            state TEXT NOT NULL,
            reason TEXT,
            task_id TEXT NOT NULL,
            plan_id TEXT NOT NULL,
            step_id TEXT NOT NULL,
            attempt INTEGER NOT NULL DEFAULT 1,
            trace_id TEXT NOT NULL,
            turn_id TEXT,
            pack_id TEXT,
            created_at TEXT NOT NULL,
            resolved_at TEXT,
            decision_id TEXT,
            UNIQUE(task_id, plan_id, step_id, attempt)
        )
        """
    )


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
            agent_id="agent-1",
            session_id="session-1",
            cursor=cursor,
            created_at=now,
        )
        stored = repo.get_pending_action("policy-1")
        assert stored is not None
        assert stored["agent_id"] == "agent-1"
        assert stored["session_id"] == "session-1"
        assert repo.list_pending_actions(
            agent_id="agent-1",
            session_id="session-1",
        ) == [stored]
        assert (
            repo.list_pending_actions(
                agent_id="other",
                session_id="session-1",
            )
            == []
        )

        with pytest.raises(sqlite3.IntegrityError):
            repo.record_pending_action(
                pending_action_id="pa-2",
                policy_request_id="policy-1",
                state="NEEDS_APPROVAL",
                reason="duplicate",
                agent_id="agent-1",
                session_id="session-1",
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
            agent_id="agent-1",
            session_id="session-1",
            cursor=cursor,
            created_at=now,
        )

        with pytest.raises(sqlite3.IntegrityError):
            repo.record_pending_action(
                pending_action_id="pa-2",
                policy_request_id="policy-2",
                state="NEEDS_APPROVAL",
                reason="duplicate cursor",
                agent_id="agent-1",
                session_id="session-1",
                cursor=cursor,
                created_at=now,
            )


def test_pending_action_owner_columns_migrate_fail_closed(tmp_path) -> None:
    db_path = tmp_path / "legacy-task.db"
    conn = sqlite3.connect(db_path)
    _create_legacy_pending_actions_table(conn)
    conn.execute(
        """
        INSERT INTO pending_actions (
            pending_action_id, policy_request_id, state, task_id, plan_id,
            step_id, trace_id, created_at
        ) VALUES ('pa-legacy', 'policy-legacy', 'NEEDS_APPROVAL', 'task-1',
                  'plan-1', 'step-1', 'trace-1', '2026-09-07T00:00:00+00:00')
        """
    )
    conn.commit()
    conn.close()

    store = RecordStoreSQLite(db_path, wal=False)
    migrate_v1_to_v2(store)
    row = store.query_dicts(
        "SELECT agent_id, session_id FROM pending_actions "
        "WHERE policy_request_id = 'policy-legacy'"
    )[0]

    assert row == {"agent_id": "", "session_id": ""}
    store.close()


def test_pending_action_ownership_alembic_migration_upgrades_v1(tmp_path) -> None:
    db_path = tmp_path / "task-v1.db"
    with sqlite3.connect(db_path) as conn:
        _create_legacy_pending_actions_table(conn)
        conn.execute("CREATE TABLE alembic_version (version_num TEXT PRIMARY KEY)")
        conn.execute(
            "INSERT INTO alembic_version(version_num) VALUES ('0001_baseline')"
        )

    run_migrations(db_path)

    with sqlite3.connect(db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(pending_actions)")}
        revision = conn.execute("SELECT version_num FROM alembic_version").fetchone()

    assert {"agent_id", "session_id"} <= columns
    assert revision == ("0002_pending_action_ownership",)


def test_pending_action_resolution_is_compare_and_set(tmp_path) -> None:
    repo = _setup_repo(str(tmp_path / "task.db"))
    cursor = ResumePointer(
        task_id="task-1",
        plan_id="plan-1",
        step_id="step-1",
        trace_id="trace-1",
    )
    now = datetime.utcnow()
    repo.record_pending_action(
        pending_action_id="pa-1",
        policy_request_id="policy-1",
        state="NEEDS_APPROVAL",
        reason="approval required",
        agent_id="agent-1",
        session_id="session-1",
        cursor=cursor,
        created_at=now,
    )

    assert repo.update_pending_action("policy-1", now, "allow") is True
    assert repo.update_pending_action("policy-1", now, "deny") is False
    assert repo.get_pending_action("policy-1")["decision_id"] == "allow"


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
