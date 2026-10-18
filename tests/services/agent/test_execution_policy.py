from types import SimpleNamespace

from pydantic import BaseModel

from openminion.modules.policy.runtime.security import SecurityPolicyEngine
from openminion.modules.tool.registry import ToolRegistry, ToolSpec
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


class _NoArgs(BaseModel):
    pass


def _power_user_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="authoring.apply",
            args_model=_NoArgs,
            min_scope="POWER_USER",
            handler=lambda _args, _ctx: {},
            dangerous=False,
        )
    )
    return registry


def _policy_decision(*, internal_actor_scopes=()):
    service_port = SimpleNamespace(
        security_policy=SecurityPolicyEngine(
            internal_actor_scopes=frozenset(internal_actor_scopes)
        ),
        tools=_power_user_registry(),
        identity_agent_id="agent-a",
    )
    adapter = build_policy_adapter(
        service_port,
        SimpleNamespace(
            inbound=SimpleNamespace(channel="console", target="user", metadata={})
        ),
        tool_budget_state=None,
        turn_boundary_adapter=None,
    )
    assert adapter is not None
    return adapter.evaluate(
        tool_name="authoring.apply",
        tool_spec=SimpleNamespace(
            name="authoring.apply",
            dangerous=False,
        ),
        args={},
    )


def test_registered_power_user_tool_does_not_self_grant_scope() -> None:
    decision = _policy_decision()

    assert decision.allowed is False
    assert decision.reason == "missing_tool_scope"
    assert decision.details["missing_scopes"] == ["tool.execute.elevated"]


def test_internal_actor_scope_grant_authorizes_registered_power_user_tool() -> None:
    decision = _policy_decision(internal_actor_scopes=("tool.execute.elevated",))

    assert decision.allowed is True
    assert decision.reason == "policy passed"
