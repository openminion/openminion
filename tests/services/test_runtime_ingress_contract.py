from __future__ import annotations

import asyncio
from threading import Event, Thread
from types import SimpleNamespace
import warnings

import pytest

from openminion.base.config import (
    AgentProfileConfig,
    OpenMinionConfig,
    RunProfileOverrides,
    UnknownProfileError,
    resolve_agent_config,
)
from openminion.services.stats import RunStats
from openminion.services.runtime.ingress import (
    TurnRequestError,
    execute_runtime_turn,
    runtime_turn_request_from_payload,
    submit_turn_payload,
)
from tests._csc_fixtures import _csc_install_default_agent


class _GatewayStub:
    def __init__(self, *, fail_on_call: int | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        self.fail_on_call = fail_on_call

    async def run_once(self, **kwargs):
        self.calls.append(dict(kwargs))
        call_number = len(self.calls)
        if call_number == self.fail_on_call:
            raise RuntimeError("child invocation failed")
        inbound_metadata = dict(kwargs.get("inbound_metadata") or {})
        return SimpleNamespace(
            id="turn-1",
            channel=str(kwargs.get("channel", "")),
            target=str(kwargs.get("target", "")),
            body="gateway ok",
            metadata={
                "session_id": kwargs.get("session_id", ""),
                "run_id": "run-1",
                "response_id": f"provider-{call_number}",
                "persisted_inbound_message_id": inbound_metadata.get(
                    "persisted_inbound_message_id", "inbound-1"
                ),
                "persisted_outbound_message_id": f"outbound-{call_number}",
            },
            stats=RunStats(
                input_tokens=11,
                output_tokens=4,
                llm_calls=1,
                duration_ms=250,
            ),
        )


class _ManagerStub:
    def __init__(self) -> None:
        self.requests = []

    def submit_turn(self, request):  # noqa: ANN001
        self.requests.append(request)
        return SimpleNamespace(
            trace_id=request.trace_id or "trace-auto",
            result=lambda timeout_s=None: None,
            stream=lambda timeout_s=None: iter(()),
            cancel=lambda: True,
        )


class _RuntimeStub:
    def __init__(self) -> None:
        self.config = OpenMinionConfig()
        self.config.runtime.log_level = "ERROR"
        _csc_install_default_agent(self.config, name="main", provider="echo")
        self.run_profile_overrides = RunProfileOverrides()
        self.tool_workspace_root = "/tmp/runtime-workspace"
        self.gateway = _GatewayStub()
        self.runtime_manager = _ManagerStub()
        self.requested_agents: list[str | None] = []
        self.sessions = SimpleNamespace(
            get_session=lambda session_id: None,
            list_participants=lambda session_id: [],
        )

    def resolve_agent_profile(self, agent_id=None):  # noqa: ANN001
        return resolve_agent_config(self.config, agent_id)

    def resolve_gateway(self, agent_id=None, overrides=None):  # noqa: ANN001
        del overrides
        self.requested_agents.append(agent_id)
        return self.gateway


def test_execute_runtime_turn_resolves_gateway_and_shapes_payload() -> None:
    runtime = _RuntimeStub()
    request = runtime_turn_request_from_payload(
        runtime=runtime,
        payload={
            "message": "hi there",
            "agent_id": "main",
            "session_id": "session-1",
            "channel": "console",
            "target": "api-user",
            "inbound_metadata": {"origin": "chat"},
            "forced_tools": ["web.search"],
            "capability_category": "search",
        },
        request_id="req-1",
    )

    result = execute_runtime_turn(runtime=runtime, request=request)

    assert runtime.requested_agents == ["main"]
    assert result.id == "turn-1"
    assert result.body == "gateway ok"
    assert result.as_payload()["run_id"] == "run-1"
    assert result.as_payload()["stats"]["input_tokens"] == 11
    assert runtime.gateway.calls[0]["session_id"] == "session-1"
    assert runtime.gateway.calls[0]["forced_tools"] == ["web.search"]
    assert runtime.gateway.calls[0]["capability_category"] == "search"
    assert (
        runtime.gateway.calls[0]["inbound_metadata"]["workspace_root"]
        == "/tmp/runtime-workspace"
    )


def test_execute_runtime_turn_inside_running_loop_avoids_unawaited_coroutine_warning() -> (
    None
):
    runtime = _RuntimeStub()
    request = runtime_turn_request_from_payload(
        runtime=runtime,
        payload={
            "message": "hi there",
            "agent_id": "main",
            "session_id": "session-async",
            "channel": "console",
            "target": "api-user",
        },
        request_id="req-async",
    )

    async def _run_inside_loop():
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = execute_runtime_turn(runtime=runtime, request=request)
        return result, caught

    result, caught = asyncio.run(_run_inside_loop())

    assert result.body == "gateway ok"
    assert not [item for item in caught if issubclass(item.category, RuntimeWarning)]


def test_execute_runtime_turn_cancels_active_gateway_coroutine() -> None:
    runtime = _RuntimeStub()
    started = Event()
    stopped = Event()

    class _SlowGateway:
        async def run_once(self, **kwargs):
            del kwargs
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

    runtime.gateway = _SlowGateway()
    request = runtime_turn_request_from_payload(
        runtime=runtime,
        payload={
            "message": "wait",
            "agent_id": "main",
            "session_id": "session-cancel",
        },
    )
    cancel_event = Event()

    def cancel() -> None:
        assert started.wait(1.0)
        cancel_event.set()

    Thread(target=cancel, daemon=True).start()

    with pytest.raises(RuntimeError, match="Turn cancelled"):
        execute_runtime_turn(
            runtime=runtime,
            request=request,
            cancel_event=cancel_event,
        )
    assert stopped.wait(1.0)


def test_runtime_turn_request_from_payload_rejects_empty_message() -> None:
    runtime = _RuntimeStub()

    with pytest.raises(TurnRequestError):
        runtime_turn_request_from_payload(
            runtime=runtime,
            payload={"message": "   ", "session_id": "s1"},
        )


def test_runtime_turn_request_rejects_unknown_profile_in_single_agent_mode() -> None:
    runtime = _RuntimeStub()
    with pytest.raises(UnknownProfileError):
        runtime_turn_request_from_payload(
            runtime=runtime,
            payload={
                "message": "hi there",
                "agent_id": "ops-agent",
                "session_id": "session-identity",
            },
            request_id="req-identity",
        )


def test_runtime_turn_request_uses_runtime_level_overrides_for_timeout_floor() -> None:
    runtime = _RuntimeStub()
    runtime.config.agents[next(iter(runtime.config.agents.keys()))].provider = "openai"
    runtime.run_profile_overrides = RunProfileOverrides(provider="cortensor")

    request = runtime_turn_request_from_payload(
        runtime=runtime,
        payload={
            "message": "hi there",
            "agent_id": "main",
            "session_id": "session-timeout",
        },
        request_id="req-timeout",
    )

    assert request.timeout_seconds == 455.0


def test_submit_turn_payload_uses_runtime_manager_and_preserves_meta() -> None:
    runtime = _RuntimeStub()

    handle = submit_turn_payload(
        runtime=runtime,
        payload={
            "trace_id": "trace-1",
            "message": "hello",
            "session_id": "session-submit",
            "agent_id": "main",
            "idempotency_key": "idem-submit",
            "forced_tools": ["web.search"],
            "capability_category": "search",
            "timeout_seconds": 21,
        },
    )

    assert handle.trace_id == "trace-1"
    assert handle.timeout_s == 21
    assert runtime.runtime_manager.requests
    request = runtime.runtime_manager.requests[0]
    assert request.meta["idempotency_key"] == "idem-submit"
    assert request.meta["forced_tools"] == ["web.search"]
    assert request.meta["capability_category"] == "search"


def _room_participant(agent_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        participant_type="agent",
        participant_id=agent_id,
        left_at=None,
    )


def _room_runtime(*, mode: str = "broadcast") -> _RuntimeStub:
    runtime = _RuntimeStub()
    runtime.config.agents["review"] = AgentProfileConfig(
        name="review", provider="echo", default_channel="console"
    )
    session = SimpleNamespace(
        active_agent_id="main",
        metadata={"room_routing_mode": mode},
    )
    runtime.sessions = SimpleNamespace(
        get_session=lambda session_id: session,
        list_participants=lambda session_id: [
            _room_participant("main"),
            _room_participant("review"),
        ],
    )
    return runtime


def test_multi_agent_turn_derives_distinct_child_execution_ids() -> None:
    runtime = _room_runtime()
    request = runtime_turn_request_from_payload(
        runtime=runtime,
        payload={
            "message": "both respond",
            "agent_id": "main",
            "session_id": "room-1",
            "deliver": False,
            "idempotency_key": "idem-room",
        },
        request_id="req-room",
    )

    execute_runtime_turn(runtime=runtime, request=request)

    assert [call["request_id"] for call in runtime.gateway.calls] == [
        "req-room::main",
        "req-room::review",
    ]
    assert [call["idempotency_key"] for call in runtime.gateway.calls] == [
        "idem-room::main",
        "idem-room::review",
    ]


def test_multi_agent_turn_isolates_broadcast_history_and_attributes_results() -> None:
    runtime = _room_runtime()
    request = runtime_turn_request_from_payload(
        runtime=runtime,
        payload={
            "message": "both respond",
            "agent_id": "main",
            "session_id": "room-1",
            "deliver": False,
        },
    )

    result = execute_runtime_turn(runtime=runtime, request=request)

    assert runtime.gateway.calls[0]["exclude_history_message_ids"] == ()
    assert runtime.gateway.calls[1]["exclude_history_message_ids"] == (
        "inbound-1",
        "outbound-1",
    )
    second_metadata = runtime.gateway.calls[1]["inbound_metadata"]
    assert {
        key: second_metadata[key]
        for key in (
            "room_router_skip_inbound_persist",
            "room_router_skip_session_compaction",
            "persisted_inbound_message_id",
        )
    } == {
        "room_router_skip_inbound_persist": "true",
        "room_router_skip_session_compaction": "true",
        "persisted_inbound_message_id": "inbound-1",
    }
    assert result.metadata["room_responses"] == [
        {
            "agent_id": "main",
            "body": "gateway ok",
            "persisted_outbound_message_id": "outbound-1",
        },
        {
            "agent_id": "review",
            "body": "gateway ok",
            "persisted_outbound_message_id": "outbound-2",
        },
    ]


def test_multi_agent_turn_keeps_prior_peer_output_for_sequential_history() -> None:
    runtime = _room_runtime(mode="sequential")
    request = runtime_turn_request_from_payload(
        runtime=runtime,
        payload={
            "message": "respond in order",
            "agent_id": "main",
            "session_id": "room-1",
            "deliver": False,
        },
    )

    execute_runtime_turn(runtime=runtime, request=request)

    assert runtime.gateway.calls[1]["exclude_history_message_ids"] == ("inbound-1",)


def test_multi_agent_turn_propagates_second_child_failure_without_retry() -> None:
    runtime = _room_runtime()
    runtime.gateway = _GatewayStub(fail_on_call=2)
    request = runtime_turn_request_from_payload(
        runtime=runtime,
        payload={
            "message": "both respond",
            "agent_id": "main",
            "session_id": "room-1",
            "deliver": False,
        },
    )

    with pytest.raises(RuntimeError, match="child invocation failed"):
        execute_runtime_turn(runtime=runtime, request=request)

    assert len(runtime.gateway.calls) == 2


def test_multi_agent_turn_generates_ids_without_inventing_idempotency() -> None:
    runtime = _room_runtime()
    request = runtime_turn_request_from_payload(
        runtime=runtime,
        payload={
            "message": "both respond",
            "agent_id": "main",
            "session_id": "room-1",
            "deliver": False,
        },
    )

    execute_runtime_turn(runtime=runtime, request=request)

    request_ids = [str(call["request_id"]) for call in runtime.gateway.calls]
    assert len(set(request_ids)) == 2
    assert all(request_ids)
    assert [call["idempotency_key"] for call in runtime.gateway.calls] == [None, None]


def test_multi_agent_turn_rejects_delivery_before_gateway_calls() -> None:
    runtime = _room_runtime()
    request = runtime_turn_request_from_payload(
        runtime=runtime,
        payload={
            "message": "both respond",
            "agent_id": "main",
            "session_id": "room-1",
            "deliver": True,
        },
    )

    with pytest.raises(TurnRequestError, match="deliver=False"):
        execute_runtime_turn(runtime=runtime, request=request)

    assert runtime.gateway.calls == []


def test_room_turn_rejects_configured_uninvited_agent_before_call() -> None:
    runtime = _room_runtime(mode="addressed")
    runtime.sessions.list_participants = lambda session_id: [_room_participant("main")]
    request = runtime_turn_request_from_payload(
        runtime=runtime,
        payload={
            "message": "@review respond",
            "agent_id": "main",
            "session_id": "room-1",
            "deliver": False,
        },
    )

    with pytest.raises(ValueError, match="not an active room participant"):
        execute_runtime_turn(runtime=runtime, request=request)

    assert runtime.gateway.calls == []


def test_room_lookup_failure_is_not_masked() -> None:
    runtime = _RuntimeStub()

    def fail_lookup(session_id: str) -> None:
        raise RuntimeError(f"lookup failed for {session_id}")

    runtime.sessions.get_session = fail_lookup
    request = runtime_turn_request_from_payload(
        runtime=runtime,
        payload={"message": "respond", "session_id": "room-1"},
    )

    with pytest.raises(RuntimeError, match="lookup failed"):
        execute_runtime_turn(runtime=runtime, request=request)

    assert runtime.gateway.calls == []
