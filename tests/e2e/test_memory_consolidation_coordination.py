from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from threading import Event, RLock
from time import sleep
from unittest.mock import MagicMock

import pytest

from openminion.modules.memory.models import MemoryCandidate
from openminion.modules.memory.runtime.consolidation.merge import (
    apply_memory_consolidation_decisions,
)
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.base import ListQueryOptions
from openminion.modules.memory.storage.memory import InMemoryMemoryStore
from openminion.modules.tool.runtime import RuntimeContext
from openminion.modules.tool.runtime.policy import Policy
from openminion.modules.session.storage.sqlite_store import SQLiteSessionStore
from openminion.modules.task.scheduling.schedule import to_iso_utc, utc_now
from openminion.services.brain.post_execution import BrainBridgeTurnMixin
from openminion.services.cron import CronScheduler
from openminion.services.runtime.cron.executor import CronTurnExecutor
from openminion.tools.task.plugin import (
    _h_task_consolidate_memory,
    _resolve_cron_store,
)


pytestmark = pytest.mark.e2e


def test_scheduled_consolidation_rejects_off_batch_then_preserves_valid_counts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.delenv("OPENMINION_DATA_ROOT", raising=False)
    workspace = tmp_path / "workspace"
    run_root = tmp_path / "run"
    workspace.mkdir(parents=True)
    run_root.mkdir(parents=True)
    policy = Policy(
        raw={
            "workspace_root": str(workspace),
            "context_metadata": {"agent_id": "agent-a"},
            "paths": {
                "read_allow": [str(workspace)],
                "write_allow": [str(workspace)],
                "deny": [],
            },
            "tools": {"allow_prefix": [""]},
        }
    )
    task_context = RuntimeContext(
        policy=policy,
        workspace=workspace,
        run_root=run_root,
        scope="WRITE_SAFE",
        confirm=False,
    )
    created = _h_task_consolidate_memory(
        {"interval_hours": 12, "batch_limit": 2},
        task_context,
    )
    cron_store = _resolve_cron_store(task_context)
    job = cron_store.get_cron_job(created["task_id"])
    assert job is not None

    memory_store = InMemoryMemoryStore()
    for candidate_id, created_at in (
        ("cand-promote", "2026-09-25T00:00:00+00:00"),
        ("cand-defer", "2026-09-25T00:00:01+00:00"),
        ("cand-off-batch", "2026-09-25T00:00:02+00:00"),
    ):
        memory_store.candidate_put(
            MemoryCandidate(
                candidate_id=candidate_id,
                session_id="source-session",
                proposed_scope="agent:agent-a",
                type="fact",
                title=candidate_id,
                content=f"{candidate_id} content",
                source="validated",
                confidence=0.8,
                created_at=created_at,
                updated_at=created_at,
            )
        )
    memory_service = MemoryService(store=memory_store)
    payload = dict(job["payload"])
    cron_executor = CronTurnExecutor(
        runtime=SimpleNamespace(),
        cron_store=cron_store,
        request_builder=MagicMock(),
        timeout_s=30,
        max_attempts=1,
    )
    request_payload = cron_executor._request_payload(
        job=job,
        run={
            "run_id": "run-consolidation",
            "due_at": "2026-09-25T01:00:00+00:00",
        },
        message=payload["message"],
        payload=payload,
    )
    session_api = MagicMock()
    session_api.get_latest_working_state.return_value = {"module_state": {}}
    runner = SimpleNamespace(
        profile=SimpleNamespace(agent_id="agent-a"),
        memory_api=memory_service,
        session_api=session_api,
    )
    BrainBridgeTurnMixin()._inject_resume_task_hints(
        runner=runner,
        session_id=request_payload["session_id"],
        inbound_metadata=request_payload["meta"],
    )
    state_inline = session_api.put_working_state.call_args.kwargs["state_inline"]
    consolidation = state_inline["module_state"]["memory_consolidation"]
    selected_ids = [str(item["candidate_id"]) for item in consolidation["candidates"]]
    assert selected_ids == ["cand-promote", "cand-defer"]

    candidates_before = {
        candidate_id: memory_store.candidate_get(candidate_id)
        for candidate_id in selected_ids + ["cand-off-batch"]
    }
    ordinary_job = {
        **job,
        "job_id": "ordinary-job",
        "payload": {"kind": "agentTurn", "message": "ordinary task"},
    }
    ordinary_request = cron_executor._request_payload(
        job=ordinary_job,
        run={"run_id": "run-ordinary", "due_at": "2026-09-25T01:00:00+00:00"},
        message="ordinary task",
        payload=ordinary_job["payload"],
    )
    assert "memory_consolidation_job" not in ordinary_request["meta"]
    ordinary_session_api = MagicMock()
    ordinary_session_api.get_latest_working_state.return_value = {"module_state": {}}
    ordinary_runner = SimpleNamespace(
        profile=SimpleNamespace(agent_id="agent-a"),
        memory_api=memory_service,
        session_api=ordinary_session_api,
    )
    BrainBridgeTurnMixin()._inject_resume_task_hints(
        runner=ordinary_runner,
        session_id=ordinary_request["session_id"],
        inbound_metadata=ordinary_request["meta"],
    )
    ordinary_state = ordinary_session_api.put_working_state.call_args.kwargs[
        "state_inline"
    ]
    assert "memory_consolidation" not in ordinary_state["module_state"]
    assert {
        candidate_id: memory_store.candidate_get(candidate_id)
        for candidate_id in candidates_before
    } == candidates_before
    assert memory_store.list(ListQueryOptions(scopes=["agent:agent-a"])) == []

    rejected = apply_memory_consolidation_decisions(
        memory_service,
        decisions=[
            {
                "candidate_id": "cand-off-batch",
                "action": "promote",
                "reasoning": "Existing but outside this selected batch.",
            }
        ],
        target_scope=consolidation["target_scope"],
        selected_candidate_ids=selected_ids,
    )

    assert rejected["applied_count"] == 0
    assert rejected["promoted_count"] == 0
    assert "not in the selected batch" in rejected["errors"][0]
    assert memory_store.candidate_get("cand-off-batch").status == "proposed"
    assert memory_store.list(ListQueryOptions(scopes=["agent:agent-a"])) == []

    accepted = apply_memory_consolidation_decisions(
        memory_service,
        decisions=[
            {
                "candidate_id": "cand-promote",
                "action": "promote",
                "reasoning": "Validated durable fact.",
            },
            {
                "candidate_id": "cand-defer",
                "action": "defer",
                "reasoning": "Needs another confirming observation.",
            },
        ],
        target_scope=consolidation["target_scope"],
        selected_candidate_ids=selected_ids,
    )

    assert accepted["applied_count"] == 2
    assert accepted["promoted_count"] == 1
    assert accepted["deferred_count"] == 1
    assert accepted["errors"] == []
    assert memory_store.candidate_get("cand-promote").status == "promoted"
    assert memory_store.candidate_get("cand-defer").status == "proposed"
    assert memory_store.candidate_get("cand-off-batch").status == "proposed"
    assert len(memory_store.list(ListQueryOptions(scopes=["agent:agent-a"]))) == 1


def test_expired_consolidation_yields_then_persists_scope_watermark(
    tmp_path: Path,
) -> None:
    store = SQLiteSessionStore(tmp_path / "session.db")
    key = "memory-consolidation:agent:agent-a"
    first_job_id = store.add_cron_job(
        name="consolidate-a-1",
        schedule={"kind": "every", "every_ms": 60_000},
        payload={"kind": "agentTurn", "message": "consolidate"},
        session_target="isolated",
        delivery={"mode": "none"},
        concurrency_key=key,
        retry_backoff_s=1,
    )
    second_job_id = store.add_cron_job(
        name="consolidate-a-2",
        schedule={"kind": "every", "every_ms": 60_000},
        payload={"kind": "agentTurn", "message": "consolidate"},
        session_target="isolated",
        delivery={"mode": "none"},
        concurrency_key=key,
        retry_backoff_s=1,
    )
    run_id = store.trigger_cron_run(first_job_id)
    store.acquire_cron_runs("dead-daemon", lease_ttl_s=1, limit=1)
    overdue = to_iso_utc(utc_now() - timedelta(seconds=5))
    store._conn.execute(
        "UPDATE cron_runs SET lease_expires_at = ? WHERE run_id = ?",
        (overdue, run_id),
    )
    store._conn.execute(
        "UPDATE cron_jobs SET next_due_at = ? WHERE job_id = ?",
        (overdue, second_job_id),
    )
    store._conn.commit()

    foreground_clear = Event()
    finished = Event()
    events: list[str] = []
    active = 0
    max_active = 0
    lock = RLock()
    watermark = {
        "target_scope": "agent:agent-a",
        "candidate_ids": ["cand-1", "cand-2"],
        "state_hash": "state-123",
        "completed_at": "2026-08-22T12:00:00+00:00",
    }

    def _execute(_job: dict, _run: dict) -> dict:
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        try:
            return {
                "summary": "consolidated",
                "output": {"coordination_watermark": watermark},
            }
        finally:
            with lock:
                active -= 1

    def _on_event(event_type: str, _payload: dict) -> None:
        events.append(event_type)
        if event_type == "cron.run.finished":
            finished.set()

    scheduler = CronScheduler(
        store=store,
        daemon_id="live-daemon",
        tick_seconds=0.02,
        lease_ttl_seconds=2,
        max_concurrent_runs=2,
        execute_agent_turn=_execute,
        can_start_background_work=foreground_clear.is_set,
        on_event=_on_event,
    )
    scheduler.start()
    try:
        sleep(0.1)
        assert "cron.run.lease_recovered" in events
        assert "cron.scheduler.foreground_deferred" in events
        assert not finished.is_set()
        foreground_clear.set()
        assert finished.wait(timeout=4.0)
    finally:
        scheduler.shutdown(grace_s=1.0)

    persisted = store.list_cron_runs(job_id=first_job_id, limit=1)[0]
    scope_state = store.get_cron_scope_state(key)
    assert persisted["run_id"] == run_id
    assert persisted["state"] == "finished"
    assert persisted["attempts"] == 2
    assert persisted["output"]["coordination_watermark"] == watermark
    assert scope_state is not None
    assert scope_state["watermark"] == watermark
    assert max_active == 1
    store.close()
