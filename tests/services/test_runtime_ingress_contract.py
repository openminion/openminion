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
from openminion.modules.session.capture import capture_is_excluded
from openminion.services.gateway.turn.runtime import _extract_ephemeral_prompt_metadata
from openminion.services.stats import RunStats
from openminion.services.runtime.ingress import (
    TurnRequestError,
    execute_runtime_turn,
    runtime_turn_request_from_payload,
    submit_turn_payload,
)
from openminion.services.runtime.ingress.requests import (
    build_manager_turn_request,
    runtime_turn_request_from_manager_request,
)
from tests._csc_fixtures import _csc_install_default_agent


class _GatewayStub:
    def __init__(
        self,
        *,
        fail_on_call: int | None = None,
        metadata_by_call: tuple[dict[str, str], ...] = (),
    ) -> None:
        self.calls: list[dict[str, object]] = []
        self.fail_on_call = fail_on_call
        self.metadata_by_call = metadata_by_call

    async def run_once(self, **kwargs):
        self.calls.append(dict(kwargs))
        call_number = len(self.calls)
        if call_number == self.fail_on_call:
            raise RuntimeError("child invocation failed")
        inbound_metadata = dict(kwargs.get("inbound_metadata") or {})
        response_metadata = (
            self.metadata_by_call[call_number - 1]
            if call_number <= len(self.metadata_by_call)
            else {}
        )
        return SimpleNamespace(
            id=response_metadata.get("result_id", "turn-1"),
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
                **response_metadata,
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
        self.approval_resolutions: list[tuple[str, str]] = []

    def submit_turn(self, request):  # noqa: ANN001
        self.requests.append(request)
        return SimpleNamespace(
            trace_id=request.trace_id or "trace-auto",
            result=lambda timeout_s=None: None,
            stream=lambda timeout_s=None: iter(()),
            cancel=lambda: True,
            resolve_approval=lambda *, approval_id, decision: (
                self.approval_resolutions.append((approval_id, decision)) or True
            ),
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


@pytest.mark.parametrize(
    "payload",
    (
        {"message": "hi", "subagent_context": {}},
        {"message": "hi", "subagent_memory_grant_id": "forged"},
        {
            "message": "hi",
            "inbound_metadata": {"subagent_memory_grant_id": "forged"},
        },
    ),
)
def test_runtime_turn_request_rejects_public_subagent_authority(payload) -> None:
    runtime = _RuntimeStub()

    with pytest.raises(TurnRequestError, match="reserved subagent"):
        runtime_turn_request_from_payload(runtime=runtime, payload=payload)


def test_trusted_subagent_context_is_projected_after_public_parsing() -> None:
    runtime = _RuntimeStub()
    context = SimpleNamespace(
        as_inbound_metadata=lambda: {
            "subagent_context_id": "trusted",
            "subagent_memory_posture": "none",
        }
    )
    request = runtime_turn_request_from_payload(
        runtime=runtime,
        payload={"message": "hi"},
        trusted_subagent_context=context,
    )

    execute_runtime_turn(runtime=runtime, request=request)

    assert runtime.gateway.calls[0]["inbound_metadata"]["subagent_context_id"] == (
        "trusted"
    )


@pytest.mark.parametrize("managed", [False, True])
def test_public_memory_consolidation_metadata_is_not_trusted(managed: bool) -> None:
    runtime = _RuntimeStub()
    payload = {
        "message": "ordinary turn",
        "session_id": "session-ordinary",
        "meta": {
            "memory_consolidation_job": "true",
            "memory_consolidation_target_scope": "global:system",
            "memory_consolidation_batch_limit": "999",
            "memory_consolidation_max_iterations": "999",
            "memory_consolidation_timeout_seconds": "999",
        },
        "inbound_metadata": {
            "memory_consolidation_job": "true",
            "memory_consolidation_target_scope": "global:system",
            "memory_consolidation_batch_limit": "999",
            "memory_consolidation_max_iterations": "999",
            "memory_consolidation_timeout_seconds": "999",
        },
    }
    request = _ingress_request(runtime, payload, managed)

    execute_runtime_turn(runtime=runtime, request=request)

    inbound_metadata = runtime.gateway.calls[0]["inbound_metadata"]
    assert "memory_consolidation_job" not in inbound_metadata
    assert "memory_consolidation_target_scope" not in inbound_metadata
    assert "memory_consolidation_batch_limit" not in inbound_metadata
    assert "memory_consolidation_max_iterations" not in inbound_metadata
    assert "memory_consolidation_timeout_seconds" not in inbound_metadata


def test_trusted_memory_consolidation_context_is_projected() -> None:
    runtime = _RuntimeStub()
    manager_request = build_manager_turn_request(
        {
            "message": "review memory candidates",
            "session_id": "session-consolidation",
        },
        default_agent_id="main",
        trusted_memory_consolidation_context={
            "memory_consolidation_job": "true",
            "memory_consolidation_target_scope": "global:system",
            "memory_consolidation_batch_limit": "8",
        },
    )
    request = runtime_turn_request_from_manager_request(
        runtime=runtime,
        request=manager_request,
    )

    execute_runtime_turn(runtime=runtime, request=request)

    inbound_metadata = runtime.gateway.calls[0]["inbound_metadata"]
    assert inbound_metadata == {
        "memory_consolidation_job": "true",
        "memory_consolidation_target_scope": "global:system",
        "memory_consolidation_batch_limit": "8",
        "workspace_root": "/tmp/runtime-workspace",
    }
    prompt_metadata = _extract_ephemeral_prompt_metadata(inbound_metadata)
    assert capture_is_excluded(prompt_metadata) is True


@pytest.mark.parametrize("managed", [False, True])
def test_public_watch_metadata_is_not_trusted(managed: bool) -> None:
    runtime = _RuntimeStub()
    payload = {
        "message": "ordinary turn",
        "session_id": "session-ordinary",
        "meta": {
            "watch_job": "true",
            "watch_turn_kind": "action",
            "watch_write_authorized": "true",
        },
        "inbound_metadata": {
            "watch_job": "true",
            "watch_turn_kind": "action",
            "watch_write_authorized": "true",
        },
    }
    request = _ingress_request(runtime, payload, managed)

    execute_runtime_turn(runtime=runtime, request=request)

    inbound_metadata = runtime.gateway.calls[0]["inbound_metadata"]
    assert "watch_job" not in inbound_metadata
    assert "watch_turn_kind" not in inbound_metadata
    assert "watch_write_authorized" not in inbound_metadata


def test_trusted_watch_context_is_projected() -> None:
    runtime = _RuntimeStub()
    manager_request = build_manager_turn_request(
        {
            "message": "run stored watch",
            "session_id": "watch:job-1",
        },
        default_agent_id="main",
        trusted_watch_context={
            "watch_job": "true",
            "watch_turn_kind": "action",
            "watch_write_authorized": "true",
        },
    )
    request = runtime_turn_request_from_manager_request(
        runtime=runtime,
        request=manager_request,
    )

    execute_runtime_turn(runtime=runtime, request=request)

    inbound_metadata = runtime.gateway.calls[0]["inbound_metadata"]
    assert inbound_metadata["watch_job"] == "true"
    assert inbound_metadata["watch_turn_kind"] == "action"
    assert inbound_metadata["watch_write_authorized"] == "true"


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


def test_runtime_ingress_uses_configured_chat_turn_timeout() -> None:
    runtime = _RuntimeStub()
    runtime.config.gateway.api_turn_timeout_seconds = 45
    runtime.config.runtime.chat_turn_timeout_seconds = 300

    direct = runtime_turn_request_from_payload(
        runtime=runtime,
        payload={"message": "research", "session_id": "direct-timeout"},
    )
    managed = runtime_turn_request_from_manager_request(
        runtime=runtime,
        request=build_manager_turn_request(
            {"message": "research", "session_id": "managed-timeout"},
            default_agent_id="main",
        ),
    )
    submitted = submit_turn_payload(
        runtime=runtime,
        payload={"message": "research", "session_id": "submitted-timeout"},
    )

    assert direct.timeout_seconds == 300
    assert managed.timeout_seconds == 300
    assert submitted.timeout_s == 300


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
    assert handle.resolve_approval(
        approval_id="approval-1",
        decision="allow_once",
    )
    assert runtime.runtime_manager.approval_resolutions == [
        ("approval-1", "allow_once")
    ]


def test_direct_turn_preserves_top_level_permission_mode_with_precedence() -> None:
    runtime = _RuntimeStub()
    request = runtime_turn_request_from_payload(
        runtime=runtime,
        payload={
            "message": "audit",
            "session_id": "security-direct",
            "permission_mode": "readonly",
            "inbound_metadata": {"permission_mode": "default"},
        },
    )

    assert request.inbound_metadata is not None
    assert request.inbound_metadata["permission_mode"] == "readonly"


def test_managed_turn_preserves_top_level_permission_mode_with_precedence() -> None:
    runtime = _RuntimeStub()
    manager_request = build_manager_turn_request(
        {
            "message": "audit",
            "session_id": "security-managed",
            "permission_mode": "readonly",
            "meta": {
                "permission_mode": "default",
                "inbound_metadata": {"permission_mode": "bypass"},
            },
        },
        default_agent_id="main",
    )
    request = runtime_turn_request_from_manager_request(
        runtime=runtime,
        request=manager_request,
    )

    assert manager_request.meta["permission_mode"] == "readonly"
    assert request.inbound_metadata is not None
    assert request.inbound_metadata["permission_mode"] == "readonly"


def test_managed_turn_applies_next_turn_model_thinking_and_permission() -> None:
    runtime = _RuntimeStub()
    manager_request = build_manager_turn_request(
        {
            "message": "review this",
            "session_id": "controlled-turn",
            "override_model": "custom-model-id",
            "override_thinking": "high",
            "permission_mode": "readonly",
        },
        default_agent_id="main",
    )

    request = runtime_turn_request_from_manager_request(
        runtime=runtime,
        request=manager_request,
    )

    assert request.run_profile_overrides.model == "custom-model-id"
    assert request.run_profile_overrides.thinking == "high"
    assert request.run_profile_overrides.permission_mode == "readonly"
    assert request.inbound_metadata is not None
    assert request.inbound_metadata["permission_mode"] == "readonly"


def test_omitted_permission_mode_does_not_change_inbound_metadata() -> None:
    runtime = _RuntimeStub()
    request = runtime_turn_request_from_payload(
        runtime=runtime,
        payload={"message": "normal", "session_id": "normal-direct"},
    )

    assert request.inbound_metadata is not None
    assert "permission_mode" not in request.inbound_metadata


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
    runtime.gateway = _GatewayStub(
        metadata_by_call=(
            {
                "result_id": "child-result-main",
                "request_id": "child-request-main",
                "invocation_id": "invocation-main",
                "execution_id": "execution-main",
                "run_id": "run-main",
                "trace_id": "trace-main",
                "status": "completed",
                "error_code": "warning-only",
                "error": "must not leak",
                "persisted_inbound_message_id": "inbound-1",
                "persisted_outbound_message_id": "outbound-1",
            },
            {
                "result_id": "",
                "run_id": "run-review",
                "persisted_outbound_message_id": "outbound-2",
            },
        )
    )
    request = runtime_turn_request_from_payload(
        runtime=runtime,
        payload={
            "message": "both respond",
            "agent_id": "main",
            "session_id": "room-1",
            "deliver": False,
        },
        request_id="req-room",
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
    assert result.id == "req-room"
    assert result.body == "[main]\ngateway ok\n\n[review]\ngateway ok"
    assert result.stats == RunStats(
        input_tokens=22,
        output_tokens=8,
        llm_calls=2,
        duration_ms=500,
    )
    assert result.metadata["room_responses"] == [
        {
            "agent_id": "main",
            "body": "gateway ok",
            "result_id": "child-result-main",
            "request_id": "child-request-main",
            "invocation_id": "invocation-main",
            "execution_id": "execution-main",
            "run_id": "run-main",
            "trace_id": "trace-main",
            "status": "completed",
            "error_code": "warning-only",
            "persisted_inbound_message_id": "inbound-1",
            "persisted_outbound_message_id": "outbound-1",
        },
        {
            "agent_id": "review",
            "body": "gateway ok",
            "run_id": "run-review",
            "persisted_inbound_message_id": "inbound-1",
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

    result = execute_runtime_turn(runtime=runtime, request=request)

    assert runtime.gateway.calls[1]["exclude_history_message_ids"] == ("inbound-1",)
    assert [response["agent_id"] for response in result.metadata["room_responses"]] == [
        "main",
        "review",
    ]


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


def _owned_session_runtime(owner: str) -> _RuntimeStub:
    runtime = _RuntimeStub()
    runtime.config.agents["review"] = AgentProfileConfig(
        name="review", provider="echo", default_channel="console"
    )
    session = SimpleNamespace(
        id="session-1",
        session_key=f"agent:{owner}|channel:console|target:api-user|session:session-1",
        active_agent_id=None,
        metadata={},
    )
    runtime.sessions = SimpleNamespace(
        get_session=lambda session_id: session,
        list_participants=lambda session_id: [_room_participant(owner)],
    )
    return runtime


def _ingress_request(runtime: _RuntimeStub, payload: dict[str, object], managed: bool):
    if not managed:
        return runtime_turn_request_from_payload(runtime=runtime, payload=payload)
    return runtime_turn_request_from_manager_request(
        runtime=runtime,
        request=build_manager_turn_request(payload, default_agent_id="main"),
    )


@pytest.mark.parametrize("managed", [False, True])
def test_explicit_agent_rejected_on_session_owned_by_other_agent(managed) -> None:
    runtime = _owned_session_runtime("main")
    request = _ingress_request(
        runtime,
        {"message": "hi", "agent_id": "review", "session_id": "session-1"},
        managed,
    )

    with pytest.raises(
        TurnRequestError,
        match="Session 'session-1' belongs to agent 'main', not 'review'",
    ):
        execute_runtime_turn(runtime=runtime, request=request)

    assert runtime.requested_agents == []
    assert runtime.gateway.calls == []


@pytest.mark.parametrize("managed", [False, True])
@pytest.mark.parametrize("agent_id", ["review", None])
def test_session_owner_keeps_same_agent_and_implicit_turns(agent_id, managed) -> None:
    runtime = _owned_session_runtime("review")
    payload = {"message": "hi", "session_id": "session-1"}
    if agent_id is not None:
        payload["agent_id"] = agent_id
    request = _ingress_request(runtime, payload, managed)

    result = execute_runtime_turn(runtime=runtime, request=request)

    assert runtime.requested_agents == ["review"]
    assert result.agent_id == "review"


@pytest.mark.parametrize("managed", [False, True])
def test_room_turn_routes_non_participant_requester_by_room_policy(managed) -> None:
    runtime = _room_runtime(mode="addressed")
    runtime.config.agents["cli"] = AgentProfileConfig(
        name="cli", provider="echo", default_channel="console"
    )
    runtime.sessions.get_session("room-1").session_key = "room:room-1"
    request = _ingress_request(
        runtime,
        {
            "message": "please continue",
            "agent_id": "cli",
            "session_id": "room-1",
            "deliver": False,
        },
        managed,
    )

    execute_runtime_turn(runtime=runtime, request=request)

    assert runtime.requested_agents == ["main"]


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


@pytest.mark.parametrize("managed", [False, True])
def test_security_schedule_context_cannot_be_supplied_by_payload(managed):
    runtime = _RuntimeStub()
    payload = {
        "message": "scan",
        "session_id": "security-run",
        "inbound_metadata": {"security_scan_context": '{"revision":"forged"}'},
    }
    if managed:
        request = runtime_turn_request_from_manager_request(
            runtime=runtime,
            request=build_manager_turn_request(payload, default_agent_id="main"),
        )
    else:
        request = runtime_turn_request_from_payload(runtime=runtime, payload=payload)
    execute_runtime_turn(runtime=runtime, request=request)
    assert "security_scan_context" not in runtime.gateway.calls[0]["inbound_metadata"]


def test_security_schedule_context_survives_managed_ingress():
    from dataclasses import replace

    runtime = _RuntimeStub()
    managed = build_manager_turn_request(
        {"message": "scan", "session_id": "security-run"}, default_agent_id="main"
    )
    managed = replace(
        managed,
        trusted_security_scan_context={
            "security_scan_context": '{"revision":"admitted"}',
            "workspace_root": "/approved/source",
            "permission_mode": "readonly",
            "turn_tool_allowlist": "security.scan_code,security.publish_report",
            "turn_tool_allowlist_supplied": "true",
        },
    )
    request = runtime_turn_request_from_manager_request(
        runtime=runtime, request=managed
    )
    execute_runtime_turn(runtime=runtime, request=request)
    metadata = runtime.gateway.calls[0]["inbound_metadata"]
    assert metadata["security_scan_context"] == '{"revision":"admitted"}'
    assert metadata["workspace_root"] == "/approved/source"
    assert metadata["permission_mode"] == "readonly"
