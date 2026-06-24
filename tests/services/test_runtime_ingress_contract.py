from __future__ import annotations

import asyncio
from types import SimpleNamespace
import warnings

import pytest

from openminion.base.config import (
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
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def run_once(self, **kwargs):
        self.calls.append(dict(kwargs))
        return SimpleNamespace(
            id="turn-1",
            channel=str(kwargs.get("channel", "")),
            target=str(kwargs.get("target", "")),
            body="gateway ok",
            metadata={"session_id": kwargs.get("session_id", ""), "run_id": "run-1"},
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
