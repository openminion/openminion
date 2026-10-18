from types import SimpleNamespace

from openminion.modules.policy.runtime.security import SecurityPolicyEngine
from openminion.services.agent.execution.policy import build_policy_adapter


def test_policy_adapter_enforces_tool_profile_required_scopes() -> None:
    profile = SimpleNamespace(
        required_scopes_all=("tool.execute", "tool.weather.read"),
        risk="medium",
        budget_cost=1,
    )
    service_port = SimpleNamespace(
        security_policy=SecurityPolicyEngine(),
        tools=SimpleNamespace(policy_for=lambda _tool_name: profile),
        identity_agent_id="agent-a",
    )
    runtime = SimpleNamespace(
        inbound=SimpleNamespace(
            channel="console",
            target="user",
            metadata={},
        )
    )

    adapter = build_policy_adapter(
        service_port,
        runtime,
        tool_budget_state=None,
        turn_boundary_adapter=None,
    )
    assert adapter is not None
    decision = adapter.evaluate(
        tool_name="weather.openmeteo.current",
        tool_spec=SimpleNamespace(
            name="weather.openmeteo.current",
            dangerous=False,
        ),
        args={"city": "Tokyo"},
    )

    assert decision.allowed is False
    assert decision.reason == "missing_tool_scope"
    assert decision.details["missing_scopes"] == ["tool.weather.read"]


def test_policy_adapter_grants_scopes_for_registered_tools() -> None:
    profile = SimpleNamespace(
        required_scopes_all=("tool.execute", "tool.weather.read"),
        risk="medium",
        budget_cost=1,
    )
    tools = SimpleNamespace(
        list=lambda: {"weather.openmeteo.current": object()},
        policy_for=lambda _tool_name: profile,
    )
    service_port = SimpleNamespace(
        security_policy=SecurityPolicyEngine(),
        tools=tools,
        identity_agent_id="agent-a",
    )
    runtime = SimpleNamespace(
        inbound=SimpleNamespace(channel="console", target="user", metadata={})
    )

    adapter = build_policy_adapter(
        service_port,
        runtime,
        tool_budget_state=None,
        turn_boundary_adapter=None,
    )
    assert adapter is not None
    decision = adapter.evaluate(
        tool_name="weather.openmeteo.current",
        tool_spec=SimpleNamespace(
            name="weather.openmeteo.current",
            dangerous=False,
        ),
        args={"city": "Tokyo"},
    )

    assert decision.allowed is True
    assert decision.reason == "policy passed"
