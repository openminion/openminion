from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from openminion.modules.brain.adapters.tool import ToolAdapter
from openminion.modules.task.constants import (
    DEFAULT_TASK_MIN_EVERY_MS,
    TASK_REASON_RESUME_EXPIRED_ONE_SHOT,
    TASK_REASON_SCHEDULE_INTERVAL_TOO_SHORT,
)
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.runtime.policy import Policy
from openminion.modules.tool.runtime import RuntimeContext
from openminion.tools.task.plugin import (
    _h_task_cancel,
    _h_task_consolidate_memory,
    _h_task_list,
    _h_task_pause,
    _h_task_resume,
    _h_task_schedule,
    _h_task_show,
    _h_task_watch,
    _resolve_cron_store,
)


def _ctx(
    tmp_path: Path,
    *,
    agent_id: str,
    metadata: dict[str, str] | None = None,
) -> RuntimeContext:
    workspace = tmp_path / "workspace"
    run_root = tmp_path / "run"
    workspace.mkdir(parents=True, exist_ok=True)
    run_root.mkdir(parents=True, exist_ok=True)

    context_metadata = {"agent_id": agent_id}
    if metadata:
        context_metadata.update(metadata)

    policy = Policy(
        raw={
            "workspace_root": str(workspace),
            "context_metadata": context_metadata,
            "paths": {
                "read_allow": [str(workspace)],
                "write_allow": [str(workspace)],
                "deny": [],
            },
            "tools": {"allow_prefix": [""]},
        }
    )
    return RuntimeContext(
        policy=policy,
        workspace=workspace,
        run_root=run_root,
        scope="WRITE_SAFE",
        confirm=False,
    )


def test_schedule_every_cron_at_persists_agent_and_at_delete_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.delenv("OPENMINION_DATA_ROOT", raising=False)

    ctx = _ctx(tmp_path, agent_id="agent-a")
    store = _resolve_cron_store(ctx)

    every = _h_task_schedule(
        {
            "instruction": "every schedule",
            "schedule": {"kind": "every", "every_ms": 60_000},
            "name": "every-task",
        },
        ctx,
    )
    cron = _h_task_schedule(
        {
            "instruction": "cron schedule",
            "schedule": {"kind": "cron", "expr": "0 * * * *", "tz": "UTC"},
            "name": "cron-task",
        },
        ctx,
    )
    at = _h_task_schedule(
        {
            "instruction": "at schedule",
            "schedule": {"kind": "at", "at": "2030-01-01T00:00:00Z"},
            "name": "at-task",
        },
        ctx,
    )

    row_every = store.get_cron_job(every["task_id"])
    row_cron = store.get_cron_job(cron["task_id"])
    row_at = store.get_cron_job(at["task_id"])

    assert row_every is not None
    assert row_cron is not None
    assert row_at is not None
    assert row_every["agent_id"] == "agent-a"
    assert row_cron["agent_id"] == "agent-a"
    assert row_at["agent_id"] == "agent-a"
    assert row_every["delete_after_run"] is False
    assert row_cron["delete_after_run"] is False
    assert row_at["delete_after_run"] is True
    assert "scheduler_note" in every
    assert "daemon" in every["scheduler_note"].lower()
    assert "openminion daemon start" in every["scheduler_note"]


def test_schedule_every_aliases_interval_unit_and_every(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.delenv("OPENMINION_DATA_ROOT", raising=False)

    ctx = _ctx(tmp_path, agent_id="agent-a")
    store = _resolve_cron_store(ctx)

    via_interval_unit = _h_task_schedule(
        {
            "instruction": "interval-unit schedule",
            "schedule": {"kind": "every", "interval": 2, "unit": "hours"},
        },
        ctx,
    )
    via_every = _h_task_schedule(
        {
            "instruction": "every schedule",
            "schedule": {"kind": "every", "every": 7_200},
        },
        ctx,
    )
    via_cron_alias = _h_task_schedule(
        {
            "instruction": "cron schedule",
            "schedule": {
                "kind": "cron",
                "expression": "0 */2 * * *",
                "timezone": "UTC",
            },
        },
        ctx,
    )
    via_cron_expr_alias = _h_task_schedule(
        {
            "instruction": "cron expr alias",
            "schedule": {"kind": "cron", "cron_expr": "0 */2 * * *", "tz": "UTC"},
        },
        ctx,
    )
    via_hours = _h_task_schedule(
        {
            "instruction": "hours schedule",
            "schedule": {"kind": "every", "hours": 2},
        },
        ctx,
    )

    row_interval = store.get_cron_job(via_interval_unit["task_id"])
    row_every = store.get_cron_job(via_every["task_id"])
    row_cron = store.get_cron_job(via_cron_alias["task_id"])
    row_hours = store.get_cron_job(via_hours["task_id"])
    row_cron_expr = store.get_cron_job(via_cron_expr_alias["task_id"])
    assert row_interval is not None
    assert row_every is not None
    assert row_cron is not None
    assert row_hours is not None
    assert row_cron_expr is not None
    assert int((row_interval.get("schedule") or {}).get("every_ms", 0)) == 7_200_000
    assert int((row_every.get("schedule") or {}).get("every_ms", 0)) == 7_200_000
    assert (row_cron.get("schedule") or {}).get("expr") == "0 */2 * * *"
    assert (row_cron.get("schedule") or {}).get("tz") == "UTC"
    assert int((row_hours.get("schedule") or {}).get("every_ms", 0)) == 7_200_000
    assert (row_cron_expr.get("schedule") or {}).get("expr") == "0 */2 * * *"


def test_schedule_every_compound_unit_aliases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.delenv("OPENMINION_DATA_ROOT", raising=False)

    ctx = _ctx(tmp_path, agent_id="agent-a")
    store = _resolve_cron_store(ctx)

    via_interval_seconds = _h_task_schedule(
        {
            "instruction": "interval_seconds schedule",
            "schedule": {"kind": "every", "interval_seconds": 60},
        },
        ctx,
    )
    via_interval_minutes = _h_task_schedule(
        {
            "instruction": "interval_minutes schedule",
            "schedule": {"kind": "every", "interval_minutes": 2},
        },
        ctx,
    )
    via_interval_hours = _h_task_schedule(
        {
            "instruction": "interval_hours schedule",
            "schedule": {"kind": "every", "interval_hours": 1},
        },
        ctx,
    )

    row_secs = store.get_cron_job(via_interval_seconds["task_id"])
    row_mins = store.get_cron_job(via_interval_minutes["task_id"])
    row_hrs = store.get_cron_job(via_interval_hours["task_id"])

    assert row_secs is not None
    assert row_mins is not None
    assert row_hrs is not None
    assert int((row_secs.get("schedule") or {}).get("every_ms", 0)) == 60_000
    assert int((row_mins.get("schedule") or {}).get("every_ms", 0)) == 120_000
    assert int((row_hrs.get("schedule") or {}).get("every_ms", 0)) == 3_600_000


def test_schedule_rejects_every_interval_below_floor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.delenv("OPENMINION_DATA_ROOT", raising=False)

    ctx = _ctx(tmp_path, agent_id="agent-a")
    store = _resolve_cron_store(ctx)

    with pytest.raises(ToolRuntimeError) as excinfo:
        _h_task_schedule(
            {
                "instruction": "too fast",
                "schedule": {
                    "kind": "every",
                    "every_ms": DEFAULT_TASK_MIN_EVERY_MS - 1,
                },
            },
            ctx,
        )
    assert excinfo.value.code == "INVALID_ARGUMENT"
    assert (
        excinfo.value.details["reason_code"] == TASK_REASON_SCHEDULE_INTERVAL_TOO_SHORT
    )
    assert store.list_cron_jobs(limit=10) == []


def test_schedule_persists_origin_delivery_context_when_available(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.delenv("OPENMINION_DATA_ROOT", raising=False)

    ctx = _ctx(
        tmp_path,
        agent_id="agent-a",
        metadata={
            "session_id": "sess-123",
            "channel": "console",
            "target": "cli-chat",
            "conversation_id": "conv-123",
            "thread_id": "thread-123",
            "attach_id": "att-123",
        },
    )
    store = _resolve_cron_store(ctx)
    created = _h_task_schedule(
        {
            "instruction": "remember route context",
            "schedule": {"kind": "every", "every_ms": 60_000},
            "name": "origin-context",
        },
        ctx,
    )
    row = store.get_cron_job(created["task_id"])
    assert row is not None
    payload = row.get("payload") or {}
    assert isinstance(payload, dict)
    origin = payload.get("_openminion_origin")
    assert origin == {
        "session_id": "sess-123",
        "channel": "console",
        "target": "cli-chat",
        "conversation_id": "conv-123",
        "thread_id": "thread-123",
        "attach_id": "att-123",
    }


def test_task_schedule_dedupes_identical_enabled_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.delenv("OPENMINION_DATA_ROOT", raising=False)

    ctx = _ctx(tmp_path, agent_id="agent-a")
    store = _resolve_cron_store(ctx)
    args = {
        "instruction": "send a heartbeat",
        "schedule": {"kind": "every", "every_ms": 60_000},
        "name": "heartbeat",
    }

    first = _h_task_schedule(args, ctx)
    second = _h_task_schedule(args, ctx)

    assert first["task_id"] == second["task_id"]
    assert first["deduped"] is False
    assert second["deduped"] is True
    jobs = store.list_cron_jobs(limit=10)
    assert [job["job_id"] for job in jobs] == [first["task_id"]]


def test_task_list_caps_limit_at_100(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.delenv("OPENMINION_DATA_ROOT", raising=False)

    ctx = _ctx(tmp_path, agent_id="agent-a")
    for index in range(110):
        _h_task_schedule(
            {
                "instruction": f"job-{index}",
                "schedule": {"kind": "every", "every_ms": 60_000},
                "name": f"job-{index}",
            },
            ctx,
        )

    result = _h_task_list({"limit": 99_999}, ctx)
    assert result["limit"] == 100
    assert result["count"] == 100
    assert len(result["tasks"]) == 100


def test_pause_resume_and_show_preserve_runs_and_exact_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.delenv("OPENMINION_DATA_ROOT", raising=False)

    ctx = _ctx(tmp_path, agent_id="agent-a")
    store = _resolve_cron_store(ctx)
    created = _h_task_schedule(
        {
            "instruction": "watch logs",
            "schedule": {"kind": "every", "every_ms": 60_000},
            "name": "watch-logs",
        },
        ctx,
    )
    task_id = str(created["task_id"])
    run_id = store.trigger_cron_run(task_id, due_at="2026-05-19T12:00:00Z")
    store.finish_cron_run(run_id=run_id, state="failed", summary="network")

    paused = _h_task_pause({"task_id": task_id}, ctx)
    assert paused["paused"] is True
    assert paused["enabled"] is False

    shown = _h_task_show({"task_id": task_id, "runs_limit": 50}, ctx)
    task = shown["task"]
    assert shown["runs_limit"] == 20
    assert task["task_id"] == task_id
    assert task["enabled"] is False
    assert task["failure_count"] == 1
    assert len(task["runs"]) == 1
    assert task["runs"][0]["run_id"] == run_id

    resumed = _h_task_resume({"task_id": task_id}, ctx)
    assert resumed["resumed"] is True
    assert resumed["enabled"] is True
    assert resumed["next_due_at"] is not None

    with pytest.raises(ToolRuntimeError) as excinfo:
        _h_task_show({"task_id": "watch"}, ctx)
    assert excinfo.value.code == "NOT_FOUND"


def test_pause_resume_reject_cross_agent_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.delenv("OPENMINION_DATA_ROOT", raising=False)

    owner_ctx = _ctx(tmp_path, agent_id="agent-a")
    other_ctx = _ctx(tmp_path, agent_id="agent-b")
    created = _h_task_schedule(
        {
            "instruction": "owned task",
            "schedule": {"kind": "every", "every_ms": 60_000},
        },
        owner_ctx,
    )

    with pytest.raises(ToolRuntimeError) as excinfo:
        _h_task_pause({"task_id": created["task_id"]}, other_ctx)
    assert excinfo.value.code == "POLICY_DENIED"


def test_resume_rejects_expired_one_shot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.delenv("OPENMINION_DATA_ROOT", raising=False)

    ctx = _ctx(tmp_path, agent_id="agent-a")
    created = _h_task_schedule(
        {
            "instruction": "run once",
            "schedule": {"kind": "at", "at": "2030-01-01T00:00:00Z"},
        },
        ctx,
    )
    store = _resolve_cron_store(ctx)
    job = store.get_cron_job(created["task_id"])
    assert job is not None
    schedule = dict(job.get("schedule") or {})
    schedule["at"] = "2000-01-01T00:00:00Z"
    payload = dict(job.get("payload") or {})
    store.delete_cron_job(created["task_id"])
    store.add_cron_job(
        job_id=created["task_id"],
        name=str(job["name"]),
        schedule=schedule,
        payload=payload,
        description=job.get("description"),
        enabled=False,
        agent_id=job.get("agent_id"),
        session_target=job.get("session_target"),
        wake_mode=job.get("wake_mode"),
        delivery=job.get("delivery"),
        delete_after_run=True,
        misfire_policy=job.get("misfire_policy"),
        max_lateness_s=int(job.get("max_lateness_s", 600)),
        max_concurrency=int(job.get("max_concurrency", 1)),
    )
    with pytest.raises(ToolRuntimeError) as excinfo:
        _h_task_resume({"task_id": created["task_id"]}, ctx)
    assert excinfo.value.code == "INVALID_ARGUMENT"
    assert excinfo.value.details["reason_code"] == TASK_REASON_RESUME_EXPIRED_ONE_SHOT


def test_task_consolidate_memory_creates_cron_backed_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.delenv("OPENMINION_DATA_ROOT", raising=False)

    ctx = _ctx(tmp_path, agent_id="agent-a")
    store = _resolve_cron_store(ctx)

    created = _h_task_consolidate_memory(
        {
            "interval_hours": 12,
            "batch_limit": 9,
            "name": "memory consolidation",
        },
        ctx,
    )

    row = store.get_cron_job(created["task_id"])
    assert row is not None
    payload = dict(row.get("payload") or {})
    consolidation = dict(payload.get("_openminion_memory_consolidation") or {})
    assert payload["session_id"].startswith("consolidate:")
    assert created["target_scope"] == "agent:agent-a"
    assert consolidation["batch_limit"] == 9
    assert consolidation["target_scope"] == "agent:agent-a"
    assert consolidation["max_iterations"] == 2
    assert consolidation["timeout_seconds"] == 30


def test_task_list_surfaces_consolidation_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.delenv("OPENMINION_DATA_ROOT", raising=False)

    ctx = _ctx(tmp_path, agent_id="agent-a")
    _h_task_consolidate_memory({"interval_hours": 24, "batch_limit": 5}, ctx)

    listed = _h_task_list({"limit": 10}, ctx)

    assert listed["count"] == 1
    task = listed["tasks"][0]
    assert task["consolidation"] == {
        "batch_limit": 5,
        "target_scope": "agent:agent-a",
        "timeout_seconds": 30,
        "max_iterations": 2,
    }


def test_task_watch_creates_watch_payload_and_stable_session_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.delenv("OPENMINION_DATA_ROOT", raising=False)

    ctx = _ctx(
        tmp_path,
        agent_id="agent-a",
        metadata={"session_id": "sess-watch", "target": "cli-chat"},
    )
    store = _resolve_cron_store(ctx)

    created = _h_task_watch(
        {
            "description": "Watch deployment health",
            "check_instruction": "Check the deployment and report whether it is healthy.",
            "interval_minutes": 10,
            "max_checks": 3,
            "alert_condition": "deployment becomes unhealthy",
            "on_condition_action": "Run kubectl rollout restart deployment/app",
            "delivery": "announce",
        },
        ctx,
    )

    row = store.get_cron_job(created["task_id"])
    assert row is not None
    payload = row.get("payload") or {}
    watch = payload.get("_openminion_watch") or {}
    assert created["watch_created"] is True
    assert created["watch_session_id"] == f"watch:{created['task_id']}"
    assert payload.get("session_id") == f"watch:{created['task_id']}"
    assert watch["description"] == "Watch deployment health"
    assert watch["on_condition_action"] == "Run kubectl rollout restart deployment/app"
    assert watch["checks_completed"] == 0
    assert watch["max_checks"] == 3
    assert watch["created_at"] == row.get("created_at")


def test_task_watch_rejects_background_write_without_operator_authorization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.delenv("OPENMINION_DATA_ROOT", raising=False)

    ctx = _ctx(tmp_path, agent_id="agent-a")

    with pytest.raises(ToolRuntimeError) as exc:
        _h_task_watch(
            {
                "description": "Watch deployment health",
                "check_instruction": "Check deployment health.",
                "interval_minutes": 5,
                "max_checks": 3,
                "alert_condition": "deployment becomes unhealthy",
                "on_condition_action": "Run kubectl rollout restart deployment/app",
                "delivery": "announce",
                "write_authorized": True,
            },
            ctx,
        )

    assert exc.value.code == "POLICY_DENIED"
    assert exc.value.details["reason_code"] == (
        "background_write_authorization_disabled"
    )


def test_task_watch_write_authorization_uses_confirmation_rail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.delenv("OPENMINION_DATA_ROOT", raising=False)

    adapter = ToolAdapter(workspace_root=tmp_path / "workspace")
    result = adapter.execute(
        command={
            "tool_name": "task.watch",
            "args": {
                "description": "Watch deployment health",
                "check_instruction": "Check deployment health.",
                "interval_minutes": 5,
                "max_checks": 3,
                "alert_condition": "deployment becomes unhealthy",
                "on_condition_action": "Run kubectl rollout restart deployment/app",
                "delivery": "announce",
                "write_authorized": True,
            },
        },
        session_id="s1",
        trace_id="t1",
    )

    assert result["status"] == "needs_user"
    assert result["error"]["code"] == "CONFIRM_REQUIRED"
    assert (
        result["error"]["details"]["reason"]
        == "background_write_authorization_requested"
    )


def test_task_watch_persists_background_write_authorization_when_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.delenv("OPENMINION_DATA_ROOT", raising=False)

    ctx = _ctx(
        tmp_path,
        agent_id="agent-a",
        metadata={"allow_background_write_authorization": "true"},
    )
    store = _resolve_cron_store(ctx)

    created = _h_task_watch(
        {
            "description": "Watch deployment health",
            "check_instruction": "Check deployment health.",
            "interval_minutes": 5,
            "max_checks": 3,
            "alert_condition": "deployment becomes unhealthy",
            "on_condition_action": "Run kubectl rollout restart deployment/app",
            "delivery": "announce",
            "write_authorized": True,
        },
        ctx,
    )

    row = store.get_cron_job(created["task_id"])
    assert row is not None
    payload = row.get("payload") or {}
    watch = payload.get("_openminion_watch") or {}
    assert created["write_authorized"] is True
    assert watch["write_authorized"] is True
    assert watch["write_audit"] == []


def test_task_list_surfaces_watch_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.delenv("OPENMINION_DATA_ROOT", raising=False)

    ctx = _ctx(tmp_path, agent_id="agent-a")
    store = _resolve_cron_store(ctx)
    created = _h_task_watch(
        {
            "description": "Watch build",
            "check_instruction": "Check build state.",
            "interval_minutes": 5,
            "max_checks": 2,
            "alert_condition": "build fails",
            "on_condition_action": "Collect the latest build logs",
            "delivery": "none",
        },
        ctx,
    )
    row = store.get_cron_job(created["task_id"])
    assert row is not None
    payload = dict(row.get("payload") or {})
    watch = dict(payload.get("_openminion_watch") or {})
    watch["last_check_summary"] = "Build still running"
    payload["_openminion_watch"] = watch
    store.replace_cron_job_payload(created["task_id"], payload)

    listed = _h_task_list({"limit": 10}, ctx)

    assert listed["count"] == 1
    watch = listed["tasks"][0]["watch"]
    assert watch is not None
    assert watch["description"] == "Watch build"
    assert watch["on_condition_action"] == "Collect the latest build logs"
    assert watch["write_authorized"] is False
    assert watch["checks_completed"] == 0
    assert watch["last_check_result"] == "Build still running"


def test_schedule_invalid_schedule_returns_deterministic_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.delenv("OPENMINION_DATA_ROOT", raising=False)

    ctx = _ctx(tmp_path, agent_id="agent-a")
    with pytest.raises(ToolRuntimeError) as exc:
        _h_task_schedule(
            {
                "instruction": "bad schedule",
                "schedule": {"kind": "every", "every_ms": 0},
            },
            ctx,
        )
    assert exc.value.code == "INVALID_ARGUMENT"


def test_cancel_success_not_found_and_cross_agent_guard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.delenv("OPENMINION_DATA_ROOT", raising=False)

    owner_ctx = _ctx(tmp_path, agent_id="owner-a")
    other_ctx = _ctx(tmp_path, agent_id="owner-b")
    store = _resolve_cron_store(owner_ctx)

    created = _h_task_schedule(
        {
            "instruction": "cancel this",
            "schedule": {"kind": "every", "every_ms": 60_000},
            "name": "cancel-me",
        },
        owner_ctx,
    )
    task_id = created["task_id"]

    cancelled = _h_task_cancel({"task_id": task_id}, owner_ctx)
    assert cancelled["cancelled"] is True
    assert cancelled["task_cancelled"] is True
    assert store.get_cron_job(task_id) is None

    # prefix-based cancel must NOT succeed. Anti-LLM contract requires
    # exact `task_id` only; partial ids fall through to deterministic NOT_FOUND.
    by_prefix = _h_task_schedule(
        {
            "instruction": "cancel by prefix",
            "schedule": {"kind": "every", "every_ms": 60_000},
            "name": "cancel-prefix",
        },
        owner_ctx,
    )
    prefix = by_prefix["task_id"][:8]
    with pytest.raises(ToolRuntimeError) as prefix_exc:
        _h_task_cancel({"task_id": prefix}, owner_ctx)
    assert prefix_exc.value.code == "NOT_FOUND"
    assert prefix_exc.value.details.get("reason_code") == "record_not_found"
    # Original task remains scheduled — fuzzy matching must not have fired.
    assert store.get_cron_job(by_prefix["task_id"]) is not None

    # name-like cancel tokens must NOT succeed. The model-mangled
    # token `health_check_2h` against a task named `Service Health Check`
    # must fail deterministically; runtime semantic guessing is forbidden.
    by_name_hint = _h_task_schedule(
        {
            "instruction": "cancel by name hint",
            "schedule": {"kind": "every", "every_ms": 60_000},
            "name": "Service Health Check",
        },
        owner_ctx,
    )
    with pytest.raises(ToolRuntimeError) as name_exc:
        _h_task_cancel({"task_id": "health_check_2h"}, owner_ctx)
    assert name_exc.value.code == "NOT_FOUND"
    assert name_exc.value.details.get("reason_code") == "record_not_found"
    # Original task remains scheduled — name-hint matching must not have fired.
    assert store.get_cron_job(by_name_hint["task_id"]) is not None

    with pytest.raises(ToolRuntimeError) as missing_exc:
        _h_task_cancel({"task_id": "missing-task"}, owner_ctx)
    assert missing_exc.value.code == "NOT_FOUND"
    assert missing_exc.value.details.get("reason_code") == "record_not_found"

    foreign = _h_task_schedule(
        {
            "instruction": "foreign ownership",
            "schedule": {"kind": "every", "every_ms": 60_000},
            "name": "foreign-task",
        },
        owner_ctx,
    )
    with pytest.raises(ToolRuntimeError) as denied_exc:
        _h_task_cancel({"task_id": foreign["task_id"]}, other_ctx)
    assert denied_exc.value.code == "POLICY_DENIED"


def test_list_dependency_error_mapping_for_unconfigured_storage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _ctx(tmp_path, agent_id="agent-a")
    ctx.repositories.cron_db_path = None
    monkeypatch.setattr(
        "openminion.tools.task.plugin.resolve_cron_repository", lambda _ctx: None
    )

    with pytest.raises(ToolRuntimeError) as exc:
        _h_task_list({}, ctx)

    assert exc.value.code == "DEPENDENCY_MISSING"
    assert exc.value.details.get("reason_code") == "storage_unconfigured"


def test_list_dependency_error_mapping_for_unavailable_storage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _ctx(tmp_path, agent_id="agent-a")
    ctx.repositories.cron_db_path = tmp_path / "sessions.db"
    monkeypatch.setattr(
        "openminion.tools.task.plugin.resolve_cron_repository", lambda _ctx: None
    )

    with pytest.raises(ToolRuntimeError) as exc:
        _h_task_list({}, ctx)

    assert exc.value.code == "DEPENDENCY_MISSING"
    assert exc.value.details.get("reason_code") == "storage_unavailable"


def test_list_dependency_error_mapping_for_unexpected_storage_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _ctx(tmp_path, agent_id="agent-a")

    class _BrokenRepo:
        def list_cron_jobs(self, *, limit: int = 50):
            del limit
            raise RuntimeError("boom")

    monkeypatch.setattr(
        "openminion.tools.task.plugin.resolve_cron_repository",
        lambda _ctx: _BrokenRepo(),
    )

    with pytest.raises(ToolRuntimeError) as exc:
        _h_task_list({}, ctx)

    assert exc.value.code == "EXEC_ERROR"
    assert exc.value.details.get("reason_code") == "storage_exec_error"


def test_list_shape_scope_and_limit_clamp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.delenv("OPENMINION_DATA_ROOT", raising=False)

    owner_ctx = _ctx(tmp_path, agent_id="agent-owner")
    other_ctx = _ctx(tmp_path, agent_id="agent-other")
    unowned_ctx = _ctx(
        tmp_path,
        agent_id="agent-owner",
        metadata={"task_list_include_unowned": "true"},
    )
    cross_ctx = _ctx(
        tmp_path,
        agent_id="agent-owner",
        metadata={
            "task_list_include_unowned": "true",
            "task_list_allow_cross_agent": "true",
        },
    )
    store = _resolve_cron_store(owner_ctx)

    owner_task = _h_task_schedule(
        {
            "instruction": "owner task",
            "schedule": {"kind": "every", "every_ms": 60_000},
            "name": "owner-task",
        },
        owner_ctx,
    )
    _h_task_schedule(
        {
            "instruction": "other task",
            "schedule": {"kind": "every", "every_ms": 60_000},
            "name": "other-task",
        },
        other_ctx,
    )
    store.add_cron_job(
        name="unowned-task",
        schedule={"kind": "every", "every_ms": 60_000},
        payload={"kind": "agentTurn", "message": "unowned"},
        agent_id=None,
        session_target="isolated",
        misfire_policy="skip",
    )

    default_listing = _h_task_list({"limit": 20}, owner_ctx)
    assert {row["name"] for row in default_listing["tasks"]} == {"owner-task"}

    first = default_listing["tasks"][0]
    assert first["task_id"] == owner_task["task_id"]
    assert "summary" in first["schedule"]
    assert first["last_run_state"] == "pending"
    assert first["last_run_at"] is None
    assert first["pending_first_run"] is True
    assert "daemon" in str(first["last_run_note"]).lower()

    include_unowned = _h_task_list({"limit": 20}, unowned_ctx)
    unowned_names = {row["name"] for row in include_unowned["tasks"]}
    assert "owner-task" in unowned_names
    assert "unowned-task" in unowned_names
    assert "other-task" not in unowned_names

    include_cross = _h_task_list({"limit": 20}, cross_ctx)
    cross_names = {row["name"] for row in include_cross["tasks"]}
    assert {"owner-task", "other-task", "unowned-task"}.issubset(cross_names)

    for idx in range(140):
        _h_task_schedule(
            {
                "instruction": f"bulk {idx}",
                "schedule": {"kind": "every", "every_ms": 60_000},
                "name": f"bulk-{idx}",
            },
            owner_ctx,
        )
    clamped = _h_task_list({"limit": 5_000}, owner_ctx)
    assert clamped["limit"] == 100
    assert clamped["count"] <= 100


def test_task_handlers_route_through_task_manager_lifecycle_table(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.delenv("OPENMINION_DATA_ROOT", raising=False)

    owner_ctx = _ctx(tmp_path, agent_id="agent-owner")
    store = _resolve_cron_store(owner_ctx)

    created = _h_task_schedule(
        {
            "instruction": "manager-backed schedule",
            "schedule": {"kind": "every", "every_ms": 60_000},
            "name": "manager-backed",
        },
        owner_ctx,
    )

    db_path = getattr(store, "db_path", None)
    assert db_path is not None

    conn = sqlite3.connect(str(db_path))
    active_row = conn.execute(
        "SELECT state FROM scheduled_tasks WHERE task_id = ?",
        (created["task_id"],),
    ).fetchone()
    assert active_row is not None
    assert str(active_row[0]) == "active"

    _h_task_cancel({"task_id": created["task_id"]}, owner_ctx)

    cancelled_row = conn.execute(
        "SELECT state, cancelled_at FROM scheduled_tasks WHERE task_id = ?",
        (created["task_id"],),
    ).fetchone()
    conn.close()

    assert cancelled_row is not None
    assert str(cancelled_row[0]) == "cancelled"
    assert cancelled_row[1] is not None


class _GoalPolicyProfile:
    def __init__(self, policy: str) -> None:
        self.goal_execution_policy = policy


def _ctx_with_profile(
    tmp_path: Path,
    *,
    agent_id: str,
    policy: str | None,
    metadata: dict[str, str] | None = None,
) -> RuntimeContext:
    ctx = _ctx(tmp_path, agent_id=agent_id, metadata=metadata)
    if policy is not None:
        ctx.agent_profile = _GoalPolicyProfile(policy)
    return ctx


def _watch_args(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "description": "Watch deployment health",
        "check_instruction": "Check deployment health.",
        "interval_minutes": 5,
        "max_checks": 3,
        "alert_condition": "deployment becomes unhealthy",
        "delivery": "announce",
    }
    base.update(overrides)
    return base


def test_task_watch_blocks_when_policy_suggest_and_goal_origin_watch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.delenv("OPENMINION_DATA_ROOT", raising=False)

    ctx = _ctx_with_profile(
        tmp_path,
        agent_id="agent-a",
        policy="suggest",
        metadata={"goal_backed_request": "true"},
    )

    with pytest.raises(ToolRuntimeError) as excinfo:
        _h_task_watch(
            _watch_args(goal_origin_action_type="watch"),
            ctx,
        )
    assert excinfo.value.code == "POLICY_DENIED"
    details = excinfo.value.details or {}
    assert details.get("reason_code") == "policy_suggest"
    assert details.get("surface") == "watch"
    assert details.get("policy") == "suggest"
    assert details.get("action_type") == "watch"


def test_task_watch_allows_when_policy_auto_safe_and_goal_origin_watch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.delenv("OPENMINION_DATA_ROOT", raising=False)

    ctx = _ctx_with_profile(
        tmp_path,
        agent_id="agent-a",
        policy="auto_safe",
        metadata={"goal_backed_request": "true"},
    )

    result = _h_task_watch(
        _watch_args(goal_origin_action_type="watch"),
        ctx,
    )
    assert result["ok"] is True
    assert result["watch_created"] is True


def test_task_watch_allows_when_policy_auto_full_and_goal_origin_watch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.delenv("OPENMINION_DATA_ROOT", raising=False)

    ctx = _ctx_with_profile(tmp_path, agent_id="agent-a", policy="auto_full")

    result = _h_task_watch(
        _watch_args(goal_origin_action_type="watch"),
        ctx,
    )
    assert result["ok"] is True


def test_task_watch_blocks_when_policy_auto_safe_and_goal_origin_suggest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.delenv("OPENMINION_DATA_ROOT", raising=False)

    ctx = _ctx_with_profile(
        tmp_path,
        agent_id="agent-a",
        policy="auto_safe",
        metadata={"goal_backed_request": "true"},
    )

    with pytest.raises(ToolRuntimeError) as excinfo:
        _h_task_watch(
            _watch_args(goal_origin_action_type="suggest"),
            ctx,
        )
    assert excinfo.value.code == "POLICY_DENIED"
    details = excinfo.value.details or {}
    assert details.get("reason_code") == "policy_auto_safe_non_watch_task"


def test_task_watch_back_compat_when_no_goal_origin_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.delenv("OPENMINION_DATA_ROOT", raising=False)

    ctx = _ctx_with_profile(tmp_path, agent_id="agent-a", policy="suggest")

    result = _h_task_watch(_watch_args(), ctx)
    assert result["ok"] is True
    assert result["watch_created"] is True


def test_task_watch_bounded_fallback_when_profile_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.delenv("OPENMINION_DATA_ROOT", raising=False)

    ctx = _ctx_with_profile(tmp_path, agent_id="agent-a", policy=None)
    assert ctx.agent_profile is None  # pre-condition

    result = _h_task_watch(
        _watch_args(goal_origin_action_type="watch"),
        ctx,
    )
    assert result["ok"] is True


def test_task_watch_ignores_spurious_goal_origin_marker_without_goal_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.delenv("OPENMINION_DATA_ROOT", raising=False)

    ctx = _ctx_with_profile(tmp_path, agent_id="agent-a", policy="suggest")

    result = _h_task_watch(
        _watch_args(goal_origin_action_type="watch"),
        ctx,
    )
    assert result["ok"] is True


# AGFAG-03 — cron-task surface (`_h_task_schedule`) gate.


def _schedule_args(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "instruction": "summarise yesterday's traces",
        "schedule": {"kind": "every", "every_ms": 60_000},
        "name": "agf-cron",
    }
    base.update(overrides)
    return base


def test_task_schedule_blocks_when_policy_suggest_and_goal_origin_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.delenv("OPENMINION_DATA_ROOT", raising=False)

    ctx = _ctx_with_profile(
        tmp_path,
        agent_id="agent-a",
        policy="suggest",
        metadata={"goal_backed_request": "true"},
    )

    with pytest.raises(ToolRuntimeError) as excinfo:
        _h_task_schedule(
            _schedule_args(goal_origin_action_type="task"),
            ctx,
        )
    assert excinfo.value.code == "POLICY_DENIED"
    details = excinfo.value.details or {}
    assert details.get("reason_code") == "policy_suggest"
    assert details.get("surface") == "task"
    assert details.get("action_type") == "task"


def test_task_schedule_allows_when_policy_auto_safe_and_goal_origin_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.delenv("OPENMINION_DATA_ROOT", raising=False)

    ctx = _ctx_with_profile(
        tmp_path,
        agent_id="agent-a",
        policy="auto_safe",
        metadata={"goal_backed_request": "true"},
    )

    result = _h_task_schedule(
        _schedule_args(goal_origin_action_type="task"),
        ctx,
    )
    assert result["ok"] is True


def test_task_schedule_back_compat_when_no_goal_origin_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.delenv("OPENMINION_DATA_ROOT", raising=False)

    ctx = _ctx_with_profile(tmp_path, agent_id="agent-a", policy="suggest")

    result = _h_task_schedule(_schedule_args(), ctx)
    assert result["ok"] is True


def test_task_schedule_bounded_fallback_when_profile_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.delenv("OPENMINION_DATA_ROOT", raising=False)

    ctx = _ctx_with_profile(tmp_path, agent_id="agent-a", policy=None)

    result = _h_task_schedule(
        _schedule_args(goal_origin_action_type="task"),
        ctx,
    )
    assert result["ok"] is True


def test_task_schedule_ignores_spurious_goal_origin_marker_without_goal_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.delenv("OPENMINION_DATA_ROOT", raising=False)

    ctx = _ctx_with_profile(tmp_path, agent_id="agent-a", policy="suggest")

    result = _h_task_schedule(
        _schedule_args(goal_origin_action_type="task"),
        ctx,
    )
    assert result["ok"] is True
