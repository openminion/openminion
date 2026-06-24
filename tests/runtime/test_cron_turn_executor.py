from __future__ import annotations

import json
from types import SimpleNamespace
from datetime import datetime, timezone

from openminion.services.runtime.cron_delivery import CronDeliveryBridge
from openminion.services.runtime.cron_executor import CronTurnExecutor


class _FakeHandle:
    def __init__(self, responses: list[object | Exception]) -> None:
        self._responses = responses

    def result(self, timeout_s: float = 0) -> object:  # noqa: ARG002
        value = self._responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class _FakeRuntimeManager:
    def __init__(self, responses: list[object | Exception]) -> None:
        self._responses = responses
        self.submitted: list[object] = []

    def submit_turn(self, request):  # noqa: ANN001
        self.submitted.append(request)
        return _FakeHandle(self._responses)


class _FakeCronStore:
    def __init__(self) -> None:
        self.cutoffs: list[str] = []
        self.replaced_payloads: list[tuple[str, dict[str, object]]] = []

    def delete_old_cron_runs(self, cutoff: str) -> int:
        self.cutoffs.append(cutoff)
        return 3

    def replace_cron_job_payload(
        self,
        job_id: str,
        payload: dict[str, object],
    ) -> None:
        self.replaced_payloads.append((job_id, dict(payload)))


class _FakeSessions:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []
        self.events: list[dict[str, object]] = []

    def append_message(self, **kwargs):  # noqa: ANN003
        self.messages.append(dict(kwargs))
        return SimpleNamespace(id="msg-1")

    def append_event(self, **kwargs):  # noqa: ANN003
        self.events.append(dict(kwargs))
        return SimpleNamespace(id="evt-1")


class _FakeGoalRuntime:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def advance_from_cron(
        self,
        *,
        goal_id: str | None,
        mission_id: str | None,
        session_api,
        session_id: str,
    ) -> None:
        self.calls.append(
            {
                "goal_id": goal_id,
                "mission_id": mission_id,
                "session_api": session_api,
                "session_id": session_id,
            }
        )


def _runtime(
    responses: list[object | Exception],
    *,
    agent_name: str = "agent-main",
    registered_agents: list[str] | None = None,
    goal_runtime: object | None = None,
):
    runtime_manager = _FakeRuntimeManager(list(responses))
    runner = SimpleNamespace(goal_runtime=goal_runtime)
    agent_service = SimpleNamespace(_runner=runner)
    runtime = SimpleNamespace(
        config=SimpleNamespace(
            agent=SimpleNamespace(name=agent_name),
            agents={agent_name: SimpleNamespace(name=agent_name)},
            default_agent=agent_name,
        ),
        runtime_manager=runtime_manager,
        list_registered_agents=(lambda: list(registered_agents or [])),
        sessions=_FakeSessions(),
        resolve_agent_service=(lambda _agent_id: agent_service),
    )
    return runtime, runtime_manager


def _request_builder(payload: dict[str, object], agent_id: str) -> object:
    return SimpleNamespace(
        agent_id=agent_id,
        session_id=str(payload.get("session_id") or ""),
        trace_id=str(payload.get("trace_id") or ""),
        meta=dict(payload.get("meta") or {}),
        payload=dict(payload),
    )


def test_cron_turn_executor_success_injects_metadata() -> None:
    runtime, runtime_manager = _runtime(
        [SimpleNamespace(final_text="cron ok")],
        registered_agents=["agent-explicit"],
    )
    executor = CronTurnExecutor(
        runtime=runtime,
        cron_store=_FakeCronStore(),
        request_builder=_request_builder,
        timeout_s=90.0,
        max_attempts=2,
    )

    result = executor.execute(
        {
            "job_id": "job-abc",
            "agent_id": "agent-explicit",
            "payload": {"kind": "agentTurn", "message": "cron message"},
        },
        {
            "run_id": "run-def",
            "due_at": "2026-03-20T00:00:00Z",
            "isolated_session_id": "cron-run-session-1",
        },
    )

    assert result == {
        "summary": "cron ok",
        "isolated_session_id": "cron-run-session-1",
        "metadata": {},
    }
    request = runtime_manager.submitted[0]
    assert request.agent_id == "agent-explicit"
    assert request.session_id == "cron-run-session-1"
    assert request.meta == {
        "cron_job_id": "job-abc",
        "cron_run_id": "run-def",
        "scheduled_for": "2026-03-20T00:00:00Z",
    }


def test_cron_turn_executor_prefers_payload_session_and_forwards_linked_task_id() -> (
    None
):
    runtime, runtime_manager = _runtime(
        [SimpleNamespace(final_text="cron ok")],
        registered_agents=["agent-explicit"],
    )
    executor = CronTurnExecutor(
        runtime=runtime,
        cron_store=_FakeCronStore(),
        request_builder=_request_builder,
        timeout_s=90.0,
        max_attempts=1,
    )

    executor.execute(
        {
            "job_id": "job-resume",
            "agent_id": "agent-explicit",
            "payload": {
                "kind": "agentTurn",
                "message": "resume task",
                "session_id": "session-linked",
                "linked_task_id": "task-123",
            },
        },
        {
            "run_id": "run-linked",
            "due_at": "2026-03-20T00:00:00Z",
            "isolated_session_id": "cron-run-session-1",
        },
    )

    request = runtime_manager.submitted[0]
    assert request.session_id == "session-linked"
    assert request.meta["linked_task_id"] == "task-123"


def test_cron_turn_executor_preloads_goal_context_and_forwards_ids() -> None:
    goal_runtime = _FakeGoalRuntime()
    runtime, runtime_manager = _runtime(
        [SimpleNamespace(final_text="cron ok")],
        registered_agents=["agent-explicit"],
        goal_runtime=goal_runtime,
    )
    executor = CronTurnExecutor(
        runtime=runtime,
        cron_store=_FakeCronStore(),
        request_builder=_request_builder,
        timeout_s=90.0,
        max_attempts=1,
    )

    executor.execute(
        {
            "job_id": "job-goal",
            "agent_id": "agent-explicit",
            "payload": {
                "kind": "agentTurn",
                "message": "advance goal",
                "goal_id": "goal-1",
                "mission_id": "mission-1",
            },
        },
        {
            "run_id": "run-goal",
            "due_at": "2026-03-20T00:00:00Z",
            "isolated_session_id": "cron-run-session-2",
        },
    )

    request = runtime_manager.submitted[0]
    assert request.meta["goal_id"] == "goal-1"
    assert request.meta["mission_id"] == "mission-1"
    assert request.meta["goal_context_preloaded"] == "true"
    assert goal_runtime.calls == [
        {
            "goal_id": "goal-1",
            "mission_id": "mission-1",
            "session_api": None,
            "session_id": "cron-run-session-2",
        }
    ]


def test_cron_turn_executor_forwards_memory_consolidation_metadata() -> None:
    runtime, runtime_manager = _runtime(
        [SimpleNamespace(final_text="consolidated")],
        registered_agents=["agent-explicit"],
    )
    executor = CronTurnExecutor(
        runtime=runtime,
        cron_store=_FakeCronStore(),
        request_builder=_request_builder,
        timeout_s=90.0,
        max_attempts=1,
    )

    executor.execute(
        {
            "job_id": "job-consolidate",
            "agent_id": "agent-explicit",
            "payload": {
                "kind": "agentTurn",
                "message": "consolidate",
                "session_id": "consolidate:job-consolidate",
                "_openminion_memory_consolidation": {
                    "batch_limit": 8,
                    "max_iterations": 2,
                    "timeout_seconds": 30,
                    "target_scope": "agent:agent-explicit",
                },
            },
        },
        {"run_id": "run-consolidate", "due_at": "2026-03-20T00:00:00Z"},
    )

    request = runtime_manager.submitted[0]
    assert request.meta["memory_consolidation_job"] == "true"
    assert request.meta["memory_consolidation_target_scope"] == "agent:agent-explicit"
    assert request.meta["memory_consolidation_batch_limit"] == "8"
    assert request.meta["memory_consolidation_max_iterations"] == "2"
    assert request.meta["memory_consolidation_timeout_seconds"] == "30"


def test_cron_turn_executor_retries_until_success() -> None:
    runtime, runtime_manager = _runtime(
        [TimeoutError("slow"), SimpleNamespace(final_text="second try")],
        registered_agents=["agent-main"],
    )
    executor = CronTurnExecutor(
        runtime=runtime,
        cron_store=_FakeCronStore(),
        request_builder=_request_builder,
        timeout_s=10.0,
        max_attempts=2,
    )

    result = executor.execute(
        {
            "job_id": "job-retry",
            "payload": {"kind": "agentTurn", "message": "retry me"},
        },
        {"run_id": "run-retry", "due_at": "2026-03-20T00:00:00Z"},
    )

    assert result["summary"] == "second try"
    assert len(runtime_manager.submitted) == 2


def test_cron_turn_executor_returns_error_after_final_failure() -> None:
    runtime, runtime_manager = _runtime(
        [TimeoutError("slow"), RuntimeError("boom")],
        registered_agents=["agent-main"],
    )
    executor = CronTurnExecutor(
        runtime=runtime,
        cron_store=_FakeCronStore(),
        request_builder=_request_builder,
        timeout_s=10.0,
        max_attempts=2,
    )

    result = executor.execute(
        {
            "job_id": "job-fail",
            "payload": {"kind": "agentTurn", "message": "fail"},
        },
        {"run_id": "run-fail", "due_at": "2026-03-20T00:00:00Z"},
    )

    assert result["error"] is True
    assert "after 2 attempt" in result["summary"]
    assert len(runtime_manager.submitted) == 2


def test_cron_turn_executor_handles_system_cleanup_event() -> None:
    runtime, _runtime_manager = _runtime([], registered_agents=["agent-main"])
    store = _FakeCronStore()
    executor = CronTurnExecutor(
        runtime=runtime,
        cron_store=store,
        request_builder=_request_builder,
        timeout_s=10.0,
        max_attempts=1,
    )

    result = executor.execute(
        {
            "job_id": "job-cleanup",
            "payload": {
                "kind": "systemEvent",
                "event_text": "prune_cron_runs",
                "kwargs": {"days": 7},
            },
        },
        {"run_id": "run-cleanup"},
    )

    assert result["status"] == "completed"
    assert result["summary"] == "Pruned 3 old cron runs."
    assert store.cutoffs


def test_cron_turn_executor_rejects_unregistered_agent() -> None:
    runtime, runtime_manager = _runtime(
        [],
        registered_agents=["agent-main"],
    )
    executor = CronTurnExecutor(
        runtime=runtime,
        cron_store=_FakeCronStore(),
        request_builder=_request_builder,
        timeout_s=10.0,
        max_attempts=1,
    )

    result = executor.execute(
        {
            "job_id": "job-unknown",
            "agent_id": "other-agent",
            "payload": {"kind": "agentTurn", "message": "cron message"},
        },
        {"run_id": "run-unknown"},
    )

    assert result["error"] is True
    assert "not registered" in result["summary"]
    assert runtime_manager.submitted == []


def test_cron_turn_executor_watch_forwards_bounds_and_stages_progress() -> None:
    store = _FakeCronStore()
    runtime, runtime_manager = _runtime(
        [
            SimpleNamespace(
                final_text="Deployment is unhealthy.",
                metadata={
                    "watch_condition_met": "true",
                    "watch_summary": "Deployment is unhealthy.",
                },
            ),
            SimpleNamespace(
                final_text="Restarted the deployment.",
                metadata={
                    "tool_results": json.dumps(
                        [
                            {
                                "tool_name": "file.write",
                                "ok": True,
                                "call_id": "call-write",
                            }
                        ]
                    )
                },
            ),
        ],
        registered_agents=["agent-main"],
    )
    executor = CronTurnExecutor(
        runtime=runtime,
        cron_store=store,
        request_builder=_request_builder,
        timeout_s=90.0,
        max_attempts=1,
    )

    created_at = datetime.now(timezone.utc).isoformat()
    result = executor.execute(
        {
            "job_id": "job-watch",
            "payload": {
                "kind": "agentTurn",
                "message": "check deployment",
                "session_id": "watch:job-watch",
                "_openminion_watch": {
                    "description": "Watch deployment",
                    "alert_condition": "deployment becomes unhealthy",
                    "on_condition_action": "Run kubectl rollout restart deployment/app",
                    "checks_completed": 0,
                    "max_checks": 3,
                    "ttl_minutes": 60,
                    "timeout_seconds": 45,
                    "max_iterations": 3,
                    "allowed_tools": ["file.read", "web.fetch"],
                    "write_authorized": True,
                    "created_at": created_at,
                },
            },
        },
        {"run_id": "run-watch", "due_at": "2026-04-13T00:10:00Z"},
    )

    request = runtime_manager.submitted[0]
    assert request.session_id == "watch:job-watch"
    assert request.payload["timeout_seconds"] == 45
    assert request.meta["watch_job"] == "true"
    assert request.meta["watch_turn_kind"] == "check"
    assert request.meta["watch_allowed_tools"] == "file.read,web.fetch"
    action_request = runtime_manager.submitted[1]
    assert action_request.session_id == "watch:job-watch"
    assert action_request.meta["watch_job"] == "true"
    assert action_request.meta["watch_turn_kind"] == "action"
    assert action_request.meta["watch_write_authorized"] == "true"
    assert action_request.meta["watch_write_authorization_scope"] == "watch_job"
    assert "watch_allowed_tools" not in action_request.meta
    assert "Declared action: Run kubectl rollout restart deployment/app" in str(
        action_request.payload["message"]
    )
    assert "operator-authorized background write access" in str(
        action_request.payload["message"]
    )
    assert result["output"]["watch_condition_met"] is True
    assert result["output"]["watch_delivery_requested"] is True
    assert result["output"]["watch_terminal"] is True
    assert result["output"]["watch_action_executed"] is True
    assert result["output"]["watch_action_summary"] == "Restarted the deployment."
    assert result["summary"] == "Restarted the deployment."
    assert store.replaced_payloads[-1][0] == "job-watch"
    stored_watch = store.replaced_payloads[-1][1]["_openminion_watch"]
    assert stored_watch["write_audit"] == [
        {"tool_name": "file.write", "ok": True, "call_id": "call-write"}
    ]


def test_cron_turn_executor_watch_without_trailer_fails_closed() -> None:
    store = _FakeCronStore()
    runtime, _runtime_manager = _runtime(
        [SimpleNamespace(final_text="Still checking.", metadata={})],
        registered_agents=["agent-main"],
    )
    executor = CronTurnExecutor(
        runtime=runtime,
        cron_store=store,
        request_builder=_request_builder,
        timeout_s=90.0,
        max_attempts=1,
    )

    created_at = datetime.now(timezone.utc).isoformat()
    result = executor.execute(
        {
            "job_id": "job-watch-open",
            "payload": {
                "kind": "agentTurn",
                "message": "check deployment",
                "_openminion_watch": {
                    "description": "Watch deployment",
                    "alert_condition": "deployment unhealthy",
                    "checks_completed": 0,
                    "max_checks": 3,
                    "created_at": created_at,
                },
            },
        },
        {"run_id": "run-open", "due_at": "2026-04-13T00:10:00Z"},
    )

    assert result["output"]["watch_condition_met"] is False
    assert result["output"]["watch_delivery_requested"] is False
    assert result["output"]["watch_terminal"] is False


def test_cron_turn_executor_watch_does_not_fire_action_when_condition_is_false() -> (
    None
):
    store = _FakeCronStore()
    runtime, runtime_manager = _runtime(
        [
            SimpleNamespace(
                final_text="Deployment is healthy.",
                metadata={
                    "watch_condition_met": "false",
                    "watch_summary": "Deployment is healthy.",
                },
            )
        ],
        registered_agents=["agent-main"],
    )
    executor = CronTurnExecutor(
        runtime=runtime,
        cron_store=store,
        request_builder=_request_builder,
        timeout_s=90.0,
        max_attempts=1,
    )

    created_at = datetime.now(timezone.utc).isoformat()
    result = executor.execute(
        {
            "job_id": "job-watch-no-action",
            "payload": {
                "kind": "agentTurn",
                "message": "check deployment",
                "_openminion_watch": {
                    "description": "Watch deployment",
                    "alert_condition": "deployment unhealthy",
                    "on_condition_action": "Restart the deployment",
                    "checks_completed": 0,
                    "max_checks": 3,
                    "created_at": created_at,
                },
            },
        },
        {"run_id": "run-no-action", "due_at": "2026-04-13T00:10:00Z"},
    )

    assert len(runtime_manager.submitted) == 1
    assert result["output"]["watch_condition_met"] is False
    assert result["output"]["watch_action_executed"] is False
    assert result["output"]["watch_action_summary"] == ""


def test_cron_delivery_bridge_routes_announce_to_origin_session() -> None:
    runtime, _runtime_manager = _runtime([], registered_agents=["agent-main"])
    bridge = CronDeliveryBridge(runtime=runtime)

    bridge.deliver(
        "announce",
        "last",
        {
            "job_id": "job-abc",
            "payload": {
                "kind": "agentTurn",
                "message": "cron message",
                "_openminion_origin": {
                    "session_id": "sess-123",
                    "conversation_id": "conv-123",
                    "thread_id": "thread-123",
                    "attach_id": "att-123",
                },
            },
        },
        {"run_id": "run-def", "due_at": "2026-03-20T00:00:00Z"},
        {"summary": "scheduled result"},
    )

    assert runtime.sessions.messages
    message = runtime.sessions.messages[-1]
    assert message["session_id"] == "sess-123"
    assert message["conversation_id"] == "conv-123"
    assert message["thread_id"] == "thread-123"
    assert message["attach_id"] == "att-123"
    assert message["body"] == "scheduled result"

    assert runtime.sessions.events
    event = runtime.sessions.events[-1]
    assert event["session_id"] == "sess-123"
    assert event["event_type"] == "cron.announce"


def test_cron_delivery_bridge_drops_unroutable_announce() -> None:
    runtime, _runtime_manager = _runtime([], registered_agents=["agent-main"])
    bridge = CronDeliveryBridge(runtime=runtime)

    bridge.deliver(
        "announce",
        "last",
        {
            "job_id": "job-abc",
            "payload": {"kind": "agentTurn", "message": "cron message"},
        },
        {"run_id": "run-def", "due_at": "2026-03-20T00:00:00Z"},
        {"summary": "scheduled result"},
    )

    assert runtime.sessions.messages == []
    assert runtime.sessions.events == []
