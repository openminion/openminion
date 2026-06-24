from __future__ import annotations


from openminion.base.config import OpenMinionConfig, resolve_agent_config
from openminion.services.runtime.ingress import (
    build_manager_turn_request,
    runtime_turn_request_from_manager_request,
    runtime_turn_request_from_payload,
)
from tests._csc_fixtures import _csc_install_default_agent


class _RuntimeStub:
    def __init__(self) -> None:
        self.config = OpenMinionConfig()
        _csc_install_default_agent(self.config)  # type: ignore[attr-defined]
        self.config.runtime.log_level = "ERROR"
        _csc_install_default_agent(self.config, name="main", provider="echo")
        self.tool_workspace_root = "/tmp/runtime-workspace"

    def resolve_agent_profile(self, agent_id=None):  # noqa: ANN001
        return resolve_agent_config(self.config, agent_id)


def test_runtime_turn_request_from_payload_characterizes_direct_shape() -> None:
    runtime = _RuntimeStub()

    request = runtime_turn_request_from_payload(
        runtime=runtime,
        payload={
            "message": "latest news on korea",
            "agent_id": "main",
            "session_id": "direct-session",
            "channel": "console",
            "target": "api-user",
            "idempotency_key": "idem-1",
            "inbound_metadata": {"origin": "api"},
            "conversation_id": "conv-1",
            "deliver": False,
            "forced_tools": ["web.search"],
            "capability_category": "search",
            "timeout_seconds": 17,
        },
        request_id="req-direct",
    )

    assert request.agent_id == "main"
    assert request.message == "latest news on korea"
    assert request.session_id == "direct-session"
    assert request.channel == "console"
    assert request.target == "api-user"
    assert request.request_id == "req-direct"
    assert request.idempotency_key == "idem-1"
    assert request.deliver is False
    assert request.timeout_seconds == 17
    assert request.forced_tools == ("web.search",)
    assert request.capability_category == "search"
    assert request.inbound_metadata is not None
    assert request.inbound_metadata["origin"] == "api"
    assert request.inbound_metadata["conversation_id"] == "conv-1"
    assert request.inbound_metadata["workspace_root"] == "/tmp/runtime-workspace"


def test_runtime_turn_request_from_manager_request_characterizes_managed_shape() -> (
    None
):
    runtime = _RuntimeStub()
    manager_request = build_manager_turn_request(
        {
            "trace_id": "trace-managed",
            "message": "latest news on korea",
            "agent_id": "main",
            "session_id": "managed-session",
            "channel": "console",
            "user": "api-user",
            "idempotency_key": "idem-2",
            "inbound_metadata": {"origin": "api"},
            "conversation_id": "conv-2",
            "deliver": False,
            "forced_tools": ["web.search"],
            "capability_category": "search",
            "timeout_seconds": 19,
        },
        default_agent_id="main",
    )

    request = runtime_turn_request_from_manager_request(
        runtime=runtime,
        request=manager_request,
    )

    assert request.agent_id == "main"
    assert request.message == "latest news on korea"
    assert request.session_id == "managed-session"
    assert request.channel == "console"
    assert request.target == "api-user"
    assert request.request_id == "trace-managed"
    assert request.idempotency_key == "idem-2"
    assert request.deliver is False
    assert request.timeout_seconds == 19
    assert request.forced_tools == ("web.search",)
    assert request.capability_category == "search"
    assert request.inbound_metadata is not None
    assert request.inbound_metadata["origin"] == "api"
    assert request.inbound_metadata["conversation_id"] == "conv-2"
    assert request.inbound_metadata["workspace_root"] == "/tmp/runtime-workspace"
