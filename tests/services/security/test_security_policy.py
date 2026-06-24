import unittest

from openminion.services.security.policy import (
    DECISION_ALLOW,
    DECISION_DENY,
    DECISION_REQUIRE_APPROVAL,
    RISK_HIGH,
    RISK_LOW,
    SecurityPolicyAction,
    SecurityPolicyActor,
    SecurityPolicyCheck,
    SecurityPolicyContext,
    SecurityPolicyEngine,
    ToolBudgetPolicy,
    ToolBudgetState,
    default_internal_actor,
    derive_plugin_activation_risk,
    evaluate_plugin_trust_policy,
)


class SecurityPolicyEngineTests(unittest.TestCase):
    def test_unknown_action_is_denied(self) -> None:
        engine = SecurityPolicyEngine()
        decision = engine.evaluate(
            SecurityPolicyCheck(
                actor=default_internal_actor("openminion"),
                action=SecurityPolicyAction(
                    resource="unknown", verb="noop", risk=RISK_LOW
                ),
                context=SecurityPolicyContext(),
            )
        )
        self.assertEqual(decision.decision, DECISION_DENY)
        self.assertEqual(decision.reason_code, "unknown_action")

    def test_missing_scope_is_denied(self) -> None:
        engine = SecurityPolicyEngine()
        decision = engine.evaluate(
            SecurityPolicyCheck(
                actor=SecurityPolicyActor(
                    role="operator", scopes=set(), agent_id="openminion"
                ),
                action=SecurityPolicyAction(
                    resource="gateway", verb="turn.execute", risk=RISK_LOW
                ),
                context=SecurityPolicyContext(),
            )
        )
        self.assertEqual(decision.decision, DECISION_DENY)
        self.assertEqual(decision.reason_code, "missing_scope")

    def test_high_risk_tool_requires_approval(self) -> None:
        engine = SecurityPolicyEngine()
        decision = engine.evaluate(
            SecurityPolicyCheck(
                actor=default_internal_actor("openminion"),
                action=SecurityPolicyAction(
                    resource="tool",
                    verb="execute",
                    risk=RISK_HIGH,
                    tool_name="weather.openmeteo.current",
                ),
                context=SecurityPolicyContext(),
            )
        )
        self.assertEqual(decision.decision, DECISION_REQUIRE_APPROVAL)
        self.assertEqual(decision.reason_code, "approval_required_high_risk")

    def test_default_internal_actor_allows_gateway_turn(self) -> None:
        engine = SecurityPolicyEngine()
        decision = engine.evaluate(
            SecurityPolicyCheck(
                actor=default_internal_actor("openminion"),
                action=SecurityPolicyAction(
                    resource="gateway", verb="turn.execute", risk=RISK_LOW
                ),
                context=SecurityPolicyContext(channel="console", target="user"),
            )
        )
        self.assertEqual(decision.decision, DECISION_ALLOW)
        self.assertEqual(decision.reason_code, "allowed")

    def test_plugin_activation_risk_derives_critical_for_exec_like_capability(
        self,
    ) -> None:
        risk = derive_plugin_activation_risk(
            trust_tier="local-dev",
            requested_capabilities={"tool.exec.shell"},
        )
        self.assertEqual(risk, "critical")

    def test_missing_tool_specific_scope_is_denied(self) -> None:
        engine = SecurityPolicyEngine(
            default_tool_required_scopes=frozenset({"tool.execute"}),
        )
        decision = engine.evaluate(
            SecurityPolicyCheck(
                actor=default_internal_actor("openminion"),
                action=SecurityPolicyAction(
                    resource="tool",
                    verb="execute",
                    risk=RISK_LOW,
                    tool_name="weather.openmeteo.current",
                    required_scopes_all=frozenset(
                        {"tool.execute", "tool.weather.admin"}
                    ),
                ),
                context=SecurityPolicyContext(),
            )
        )
        self.assertEqual(decision.decision, DECISION_DENY)
        self.assertEqual(decision.reason_code, "missing_tool_scope")

    def test_tool_budget_calls_exceeded_is_denied(self) -> None:
        engine = SecurityPolicyEngine(
            tool_budget_policy=ToolBudgetPolicy(
                max_calls_per_run=1,
                max_calls_per_tool=1,
                max_budget_cost_per_run=2,
            )
        )
        state = ToolBudgetState()
        first = engine.evaluate_tool_budget(
            tool_name="weather.openmeteo.current", budget_cost=1, state=state
        )
        self.assertEqual(first.decision, DECISION_ALLOW)
        engine.record_tool_budget_usage(
            tool_name="weather.openmeteo.current", budget_cost=1, state=state
        )

        second = engine.evaluate_tool_budget(
            tool_name="weather.openmeteo.current", budget_cost=1, state=state
        )
        self.assertEqual(second.decision, DECISION_DENY)
        self.assertEqual(second.reason_code, "tool_budget_calls_exceeded")

    def test_plugin_trust_policy_denies_verified_local_unverified(self) -> None:
        decision = evaluate_plugin_trust_policy(
            trust_tier="verified",
            requested_capabilities={"message.inbound.read"},
            provenance_source="local-path",
            provenance_verified=False,
            provenance_publisher="",
        )
        self.assertEqual(decision.decision, DECISION_DENY)
        self.assertEqual(
            decision.reason_code,
            "plugin_verified_requires_nonlocal_or_verified_provenance",
        )

    def test_plugin_trust_policy_allows_verified_registry_with_publisher(self) -> None:
        decision = evaluate_plugin_trust_policy(
            trust_tier="verified",
            requested_capabilities={"message.inbound.read"},
            provenance_source="registry",
            provenance_verified=True,
            provenance_publisher="example-inc",
        )
        self.assertEqual(decision.decision, DECISION_ALLOW)
        self.assertEqual(decision.reason_code, "allowed")

    def test_plugin_trust_policy_denies_restricted_mutating_capability(self) -> None:
        decision = evaluate_plugin_trust_policy(
            trust_tier="restricted",
            requested_capabilities={"message.outbound.modify"},
            provenance_source="registry",
            provenance_verified=True,
            provenance_publisher="example-inc",
        )
        self.assertEqual(decision.decision, DECISION_DENY)
        self.assertEqual(decision.reason_code, "plugin_restricted_capability_blocked")
