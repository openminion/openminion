from __future__ import annotations

from pathlib import Path

import pytest

from openminion.modules.a2a.models import JobRecord
from openminion.modules.a2a.storage import MemoryStateStore, SQLiteStateStore


def _job(
    task_id: str,
    *,
    owner: str = "parent",
    session: str = "task-delegate::session-1",
    created_at: str,
) -> JobRecord:
    return JobRecord(
        task_id=task_id,
        trace_id=f"trace-{task_id}",
        idempotency_key=f"key-{task_id}",
        idempotency_scope=f"job.start:child:delegate:{session}",
        agent_id="child",
        method="delegate",
        state="RUNNING",
        owner_agent_id=owner,
        result_inline={"secret": task_id},
        created_at=created_at,
        updated_at=created_at,
        heartbeat_at=created_at,
    )


@pytest.mark.parametrize("backend", ("memory", "sqlite"))
def test_recent_jobs_filter_exact_owner_and_session_before_limit(
    backend: str,
    tmp_path: Path,
) -> None:
    store = (
        MemoryStateStore()
        if backend == "memory"
        else SQLiteStateStore(tmp_path / "state.db")
    )
    try:
        store.create_job(_job("old", created_at="2026-09-30T00:00:00+00:00"))
        store.create_job(_job("new-a", created_at="2026-09-30T01:00:00+00:00"))
        store.create_job(_job("new-b", created_at="2026-09-30T01:00:00+00:00"))
        store.create_job(
            _job(
                "foreign-owner",
                owner="other",
                created_at="2026-09-30T02:00:00+00:00",
            )
        )
        store.create_job(
            _job(
                "foreign-session",
                session="task-delegate::session-2",
                created_at="2026-09-30T03:00:00+00:00",
            )
        )

        rows = store.list_jobs(
            {
                "owner_agent_id": "parent",
                "session_id": "task-delegate::session-1",
                "order": "recent",
                "limit": 2,
            }
        )

        assert [row.task_id for row in rows] == ["new-b", "new-a"]
    finally:
        store.close()
