from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from openminion.cli.parser.contracts import ensure_cli_component_compatibility
from openminion.cli.tui.providers import (
    RuntimeCronProvider,
    RuntimeMemoryProvider,
    RuntimePolicyProvider,
    RuntimeSessionsProvider,
    RuntimeSystemProvider,
    RuntimeTasksProvider,
)
from openminion.modules.memory.storage.base import (
    CandidateListOptions,
    ListQueryOptions,
    SearchQueryOptions,
)
from openminion.modules.task.schemas import TaskDigest, TaskDigestTask, TaskStatus


class _FakeTaskCtl:
    def __init__(self) -> None:
        now = datetime.now(timezone.utc)
        self._digest = TaskDigest(
            agent_id="agent-a",
            session_id="sess-a",
            generated_at=now,
            tasks_ready=[
                TaskDigestTask(
                    task_id="task-002",
                    title="Second task",
                    status=TaskStatus.PENDING,
                    next_step_id="stp-2",
                    next_step_title="Wait for approval",
                    due_at=now + timedelta(days=1),
                )
            ],
            tasks_active=[
                TaskDigestTask(
                    task_id="task-001",
                    title="First task",
                    status=TaskStatus.ACTIVE,
                    next_step_id="stp-1",
                    next_step_title="Do work",
                    metadata={
                        "project_run_id": "prun_1",
                        "autonomy_run_id": "awrk_1",
                        "goal_id": "goal-1",
                        "project_phase": "execute",
                        "verification_state": "in_progress",
                        "last_checkpoint_id": "checkpoint-1",
                    },
                )
            ],
            current_task=None,
        )
        self._events = [
            {
                "type": "mission.paused",
                "task_id": "task-002",
                "payload": {
                    "policy_request_id": "dec-002",
                    "reason": "Need confirmation",
                    "tool": "file.write",
                },
            },
            {
                "type": "mission.paused",
                "task_id": "task-001",
                "payload": {
                    "policy_request_id": "dec-001",
                    "reason": "Need approval",
                    "tool": "exec",
                },
            },
            {
                "type": "mission.resumed",
                "task_id": "task-001",
                "payload": {
                    "policy_request_id": "dec-001",
                    "decision_id": "allow",
                },
            },
        ]
        self.resume_calls: list[dict[str, str]] = []

    def get_digest(
        self, *, agent_id: str, session_id: str, limit: int = 5
    ) -> TaskDigest:
        return self._digest

    def list_events(self) -> list[dict[str, Any]]:
        return list(self._events)

    def resume_pending_action(
        self,
        *,
        policy_request_id: str,
        decision_id: str,
        trace_id: str | None = None,
    ) -> dict[str, str]:
        self.resume_calls.append(
            {
                "policy_request_id": policy_request_id,
                "decision_id": decision_id,
                "trace_id": str(trace_id or ""),
            }
        )
        return {"policy_request_id": policy_request_id}


class _FakeCronRepo:
    def list_cron_jobs(
        self, *, enabled: bool | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        return [
            {
                "job_id": "daily-summary",
                "schedule": {"kind": "interval", "every_ms": 60000},
                "next_due_at": "2026-03-21T10:00:00Z",
                "enabled": True,
                "misfire_policy": {"kind": "skip"},
            }
        ]

    def list_cron_runs(
        self,
        *,
        job_id: str | None = None,
        limit: int = 100,
        states: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        if job_id != "daily-summary":
            return []
        return [
            {
                "state": "finished",
                "due_at": "2026-03-21T09:00:00Z",
                "started_at": "2026-03-21T09:00:01Z",
                "finished_at": "2026-03-21T09:00:03Z",
            }
        ]


class _FakeSessionStore:
    def __init__(self) -> None:
        now = datetime.now(timezone.utc)
        self._sessions = [
            SimpleNamespace(
                id="sess-001",
                created_at=(now - timedelta(days=2)).isoformat(),
                updated_at=(now - timedelta(hours=3)).isoformat(),
            )
        ]

    def list_sessions(self, *, limit: int = 200) -> list[Any]:
        return list(self._sessions)

    def count_messages(
        self,
        *,
        session_id: str,
        conversation_id: str | None = None,
        thread_id: str | None = None,
    ) -> int:
        return 7 if session_id == "sess-001" else 0

    def list_events(
        self,
        *,
        session_id: str,
        limit: int = 100,
        newest_first: bool = False,
        event_type_prefix: str | None = None,
    ) -> list[Any]:
        if session_id != "sess-001":
            return []
        return [
            SimpleNamespace(
                event_type="tool.request",
                payload={"tool": "search_brave"},
                created_at="2026-03-21T09:10:00Z",
            )
        ]


class _FakePolicyCtl:
    def list_decisions(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return [
            {
                "decision_id": "dec-100",
                "tool": "exec",
                "method": "default",
                "decision": "REQUIRE_CONFIRM",
                "reason_code": "SIDE_EFFECTS_CONFIRM",
                "risk_spec_json": {"risk_class": "exec"},
                "created_at": "2026-03-21T09:00:00Z",
            },
            {
                "decision_id": "dec-099",
                "tool": "file",
                "method": "read",
                "decision": "ALLOW",
                "reason_code": "READ_ONLY_ALLOW",
                "risk_spec_json": {"risk_class": "read"},
                "created_at": "2026-03-21T08:00:00Z",
            },
        ]

    def list_grants(
        self,
        *,
        subject_id: str | None = None,
        effect: str | None = None,
        tool: str | None = None,
        method: str | None = None,
        active_only: bool = False,
    ) -> list[Any]:
        return [
            SimpleNamespace(
                grant_id="grant-1",
                tool="exec",
                method="default",
                expires_at="2099-01-01T00:00:00Z",
                max_uses=5,
                uses_count=2,
            )
        ]


class _FakeMemoryService:
    def list(self, options: ListQueryOptions) -> list[Any]:
        assert options.scopes
        return [
            SimpleNamespace(
                id="mem-1",
                type="fact",
                scope=options.scopes[0],
                title="remember this",
                content="remember this",
                updated_at="2026-03-21T10:00:00Z",
            )
        ]

    def candidate_list(self, options: CandidateListOptions) -> list[Any]:
        return [
            SimpleNamespace(
                candidate_id="cand-1",
                title="candidate",
                content="candidate",
                confidence=0.9,
            )
        ]

    def search(self, options: SearchQueryOptions) -> list[Any]:
        return [
            SimpleNamespace(
                id="mem-2",
                type="summary",
                scope=options.scopes[0],
                title="query match",
                content="query match",
                updated_at="2026-03-21T11:00:00Z",
            )
        ]


class _FakeSystemSessions:
    def __init__(self) -> None:
        self._conn = sqlite3.connect(":memory:")
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("CREATE TABLE messages(created_at TEXT)")
        self._conn.execute(
            "CREATE TABLE events(id INTEGER PRIMARY KEY, created_at TEXT, event_type TEXT)"
        )
        start = datetime.now(timezone.utc)
        now = start.isoformat()
        finished = (start + timedelta(milliseconds=1200)).isoformat()
        self._conn.execute("INSERT INTO messages(created_at) VALUES (?)", (now,))
        self._conn.execute(
            "INSERT INTO events(created_at, event_type) VALUES (?, 'tool.request')",
            (now,),
        )
        self._conn.execute(
            "INSERT INTO events(created_at, event_type) VALUES (?, 'tool.completed')",
            (finished,),
        )
        self._conn.commit()

    def count_sessions(self) -> int:
        return 1


class _BrokenSystemSessions:
    @property
    def _conn(self):
        raise AttributeError("session store unavailable")

    def count_sessions(self) -> int:
        return 1


def test_runtime_tasks_provider_contract_and_mapping() -> None:
    task_ctl = _FakeTaskCtl()
    provider = RuntimeTasksProvider(task_ctl, agent_id="agent-a", session_id="sess-a")

    ensure_cli_component_compatibility(provider, component_type="tasks_provider")

    tasks = provider.list_tasks()
    assert {task["id"] for task in tasks} == {"task-001", "task-002"}
    task_2 = next(task for task in tasks if task["id"] == "task-002")
    task_1 = next(task for task in tasks if task["id"] == "task-001")
    assert task_2["pending_actions"]
    assert task_1["project"]["project_run_id"] == "prun_1"
    assert task_1["project"]["checkpoint"] == "checkpoint-1"

    pending = provider.list_pending_actions()
    assert len(pending) == 1
    assert pending[0]["decision_id"] == "dec-002"

    assert provider.resolve_action("dec-002", "allow") is True
    assert task_ctl.resume_calls


def test_runtime_cron_provider_contract_and_mapping() -> None:
    provider = RuntimeCronProvider(_FakeCronRepo())
    ensure_cli_component_compatibility(provider, component_type="cron_provider")

    jobs = provider.list_jobs()
    assert len(jobs) == 1
    assert jobs[0]["id"] == "daily-summary"
    assert jobs[0]["recent_runs"][0]["state"] == "success"

    recent = provider.list_recent_runs("daily-summary", limit=5)
    assert len(recent) == 1


def test_runtime_sessions_provider_contract_and_mapping() -> None:
    provider = RuntimeSessionsProvider(_FakeSessionStore())
    ensure_cli_component_compatibility(provider, component_type="sessions_provider")

    sessions = provider.list_all_sessions()
    assert len(sessions) == 1
    assert sessions[0]["id"] == "sess-001"
    assert sessions[0]["turn_count"] == 7

    timeline = provider.get_session_timeline("sess-001")
    assert len(timeline) == 1
    assert timeline[0]["event_type"] == "tool.request"


def test_runtime_sessions_provider_prefers_mode_status_detail() -> None:
    detail = RuntimeSessionsProvider._event_detail(
        {
            "mode": "plan",
            "mode_state": "execute_step",
            "mode_label": "Running step 2/3: search",
        }
    )

    assert detail == "Running step 2/3: search"


def test_runtime_policy_provider_contract_and_mapping() -> None:
    provider = RuntimePolicyProvider(_FakePolicyCtl())
    ensure_cli_component_compatibility(provider, component_type="policy_provider")

    pending = provider.list_pending_decisions()
    assert len(pending) == 1
    assert pending[0]["id"] == "dec-100"

    grants = provider.list_active_grants()
    assert len(grants) == 1
    assert grants[0]["id"] == "grant-1"

    history = provider.list_recent_decisions()
    assert len(history) == 2


def test_runtime_memory_provider_contract_and_mapping() -> None:
    provider = RuntimeMemoryProvider(
        _FakeMemoryService(),
        agent_id="agent-a",
        session_id="sess-a",
    )
    ensure_cli_component_compatibility(provider, component_type="memory_provider")

    records = provider.list_records()
    assert len(records) == 1
    assert records[0]["id"] == "mem-1"

    candidates = provider.list_candidates()
    assert len(candidates) == 1
    assert candidates[0]["id"] == "cand-1"

    results = provider.search("query")
    assert len(results) == 1
    assert results[0]["id"] == "mem-2"


def test_runtime_system_provider_contract_and_mapping(tmp_path: Path) -> None:
    storage_path = tmp_path / "runtime.sqlite"
    storage_path.write_text("db", encoding="utf-8")

    memory_root = tmp_path / "memory"
    memory_root.mkdir(parents=True, exist_ok=True)
    memory_db = memory_root / "memory.db"
    conn = sqlite3.connect(str(memory_db))
    conn.execute("CREATE TABLE memory_records(id TEXT, is_deleted INTEGER)")
    conn.execute("INSERT INTO memory_records(id, is_deleted) VALUES ('mem-1', 0)")
    conn.commit()
    conn.close()

    runtime = SimpleNamespace(
        runtime_manager=object(),
        storage_path=storage_path,
        memory_root=memory_root,
        sessions=_FakeSystemSessions(),
        config=SimpleNamespace(
            runtime=SimpleNamespace(ipc_host="127.0.0.1", ipc_port=18789),
            default_agent="agent-a",
            agents={
                "agent-a": SimpleNamespace(
                    name="agent-a",
                    model="gpt-5",
                    provider="openai",
                )
            },
        ),
        get_agent_runtime_info=lambda _agent_id=None: {"runtime_mode": "brain"},
        plugins=SimpleNamespace(names=lambda: ["builtin.validate"]),
    )

    provider = RuntimeSystemProvider(runtime)
    ensure_cli_component_compatibility(provider, component_type="system_provider")

    daemon = provider.get_daemon_status()
    assert daemon["mode"].startswith("in-process")

    storage = provider.get_storage_stats()
    assert storage["session_count"] == 1

    agent = provider.get_agent_info()
    assert agent["provider"] == "openai"

    telemetry = provider.get_telemetry_summary()
    assert telemetry["tool_calls"] >= 0
    assert telemetry["avg_latency"] == "1.2s"

    plugins = provider.get_plugin_status()
    assert plugins and plugins[0]["name"] == "builtin.validate"


def test_runtime_system_provider_storage_stats_handles_private_conn_lookup_failure() -> (
    None
):
    runtime = SimpleNamespace(
        storage_path="",
        memory_root=None,
        sessions=_BrokenSystemSessions(),
        config=SimpleNamespace(
            runtime=SimpleNamespace(ipc_host="", ipc_port=0),
            default_agent="agent-a",
            agents={
                "agent-a": SimpleNamespace(
                    name="agent-a",
                    model="gpt-5",
                    provider="openai",
                )
            },
        ),
        get_agent_runtime_info=lambda _agent_id=None: {"runtime_mode": "brain"},
        plugins=SimpleNamespace(names=lambda: []),
    )

    provider = RuntimeSystemProvider(runtime)

    storage = provider.get_storage_stats()
    assert storage["event_count"] == "—"


def test_runtime_system_provider_daemon_status_handles_invalid_port() -> None:
    runtime = SimpleNamespace(
        runtime_manager=object(),
        config=SimpleNamespace(
            runtime=SimpleNamespace(ipc_host="127.0.0.1", ipc_port="not-a-port")
        ),
    )

    provider = RuntimeSystemProvider(runtime)

    assert provider.get_daemon_status()["endpoint"] == "—"


def test_runtime_system_provider_sqlite_count_handles_sqlite_error() -> None:
    conn = sqlite3.connect(":memory:")
    try:
        assert RuntimeSystemProvider._sqlite_count(conn, "missing_table") == "—"
    finally:
        conn.close()


def test_runtime_system_provider_memory_count_handles_missing_table(
    tmp_path: Path,
) -> None:
    memory_root = tmp_path / "memory"
    memory_root.mkdir(parents=True, exist_ok=True)
    sqlite3.connect(str(memory_root / "memory.db")).close()

    assert RuntimeSystemProvider._memory_record_count(memory_root) == "—"
