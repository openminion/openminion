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


def test_direct_and_managed_ingress_requests_match_for_equivalent_payload() -> None:
    runtime = _RuntimeStub()
    payload = {
        "trace_id": "trace-parity",
        "message": "latest news on japan",
        "agent_id": "main",
        "session_id": "session-parity",
        "channel": "console",
        "target": "api-user",
        "user": "api-user",
        "idempotency_key": "idem-parity",
        "inbound_metadata": {"origin": "api"},
        "conversation_id": "conv-parity",
        "deliver": False,
        "forced_tools": ["web.search"],
        "capability_category": "search",
        "timeout_seconds": 15,
    }

    direct = runtime_turn_request_from_payload(
        runtime=runtime,
        payload=payload,
        request_id="trace-parity",
    )
    managed = runtime_turn_request_from_manager_request(
        runtime=runtime,
        request=build_manager_turn_request(payload, default_agent_id="main"),
    )

    assert managed.agent_id == direct.agent_id
    assert managed.message == direct.message
    assert managed.session_id == direct.session_id
    assert managed.channel == direct.channel
    assert managed.target == direct.target
    assert managed.request_id == direct.request_id
    assert managed.idempotency_key == direct.idempotency_key
    assert managed.deliver == direct.deliver
    assert managed.timeout_seconds == direct.timeout_seconds
    assert managed.forced_tools == direct.forced_tools
    assert managed.capability_category == direct.capability_category
    assert dict(managed.inbound_metadata or {}) == dict(direct.inbound_metadata or {})
