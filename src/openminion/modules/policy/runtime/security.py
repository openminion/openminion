from dataclasses import dataclass, field
from typing import Any, Mapping

from openminion.modules.tool.contracts.model_ids import MODEL_BROWSER

DECISION_ALLOW = "allow"
DECISION_DENY = "deny"
DECISION_REQUIRE_APPROVAL = "require_approval"

RISK_LOW = "low"
RISK_MEDIUM = "medium"
RISK_HIGH = "high"
RISK_CRITICAL = "critical"

_RISK_ORDER = {
    RISK_LOW: 1,
    RISK_MEDIUM: 2,
    RISK_HIGH: 3,
    RISK_CRITICAL: 4,
}

_PLUGIN_CRITICAL_CAPABILITY_TOKENS = {
    "exec",
    "shell",
    "subprocess",
    MODEL_BROWSER,
    "network.egress",
    "webhook.send",
}

_PLUGIN_MUTATING_CAPABILITY_TOKENS = {
    "modify",
    "write",
    "admin",
    "execute",
    "register",
    "delete",
    "create",
}


def is_local_gateway_host(host: str | None) -> bool:
    """Return whether a gateway bind stays within the local trust boundary."""
    normalized = str(host or "").strip().lower()
    return not normalized or normalized in {"127.0.0.1", "localhost", "::1"}


@dataclass(frozen=True)
class SecurityPolicyActor:
    role: str
    scopes: set[str] = field(default_factory=set)
    agent_id: str = ""
    owner_id: str = ""


@dataclass(frozen=True)
class SecurityPolicyAction:
    resource: str
    verb: str
    risk: str = RISK_LOW
    tool_name: str = ""
    required_scopes_all: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class SecurityPolicyContext:
    channel: str = ""
    target: str = ""
    origin: str = ""
    run_id: str = ""
    session_id: str = ""


@dataclass(frozen=True)
class SecurityPolicyCheck:
    actor: SecurityPolicyActor
    action: SecurityPolicyAction
    context: SecurityPolicyContext


@dataclass(frozen=True)
class SecurityPolicyDecision:
    decision: str
    reason_code: str
    policy_version: str
    required_approval_level: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.decision == DECISION_ALLOW


@dataclass(frozen=True)
class SecurityPolicyRule:
    required_scopes_any: frozenset[str]
    max_auto_risk: str = RISK_MEDIUM
    high_risk_decision: str = DECISION_REQUIRE_APPROVAL


@dataclass(frozen=True)
class ToolBudgetPolicy:
    max_calls_per_run: int = 8
    max_calls_per_tool: int = 4
    max_budget_cost_per_run: int = 16


@dataclass
class ToolBudgetState:
    tool_calls_total: int = 0
    budget_cost_total: int = 0
    per_tool_calls: dict[str, int] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        return {
            "tool_calls_total": self.tool_calls_total,
            "budget_cost_total": self.budget_cost_total,
            "per_tool_calls": dict(sorted(self.per_tool_calls.items())),
        }


def _normalize_token(token: str | None) -> str:
    raw = str(token or "").strip().lower()
    if not raw:
        return "empty"
    normalized = "".join(c for c in raw if c.isalnum() or c == "_" or c == ".")
    return normalized if normalized else "invalid"


def _normalize_risk(risk: str) -> str:
    normalized = str(risk).strip().lower()
    if normalized in {RISK_LOW, RISK_MEDIUM, RISK_HIGH, RISK_CRITICAL}:
        return normalized
    return RISK_MEDIUM


def _default_rules() -> dict[tuple[str, str], SecurityPolicyRule]:
    return {
        ("gateway", "turn.execute"): SecurityPolicyRule(
            required_scopes_any=frozenset(
                [
                    "agent.execute",
                    "agent.admin",
                ]
            ),
            max_auto_risk=RISK_MEDIUM,
            high_risk_decision=DECISION_REQUIRE_APPROVAL,
        ),
        ("channel", "message.send"): SecurityPolicyRule(
            required_scopes_any=frozenset(
                [
                    "agent.execute",
                    "agent.admin",
                    "channel.send",
                ]
            ),
            max_auto_risk=RISK_MEDIUM,
            high_risk_decision=DECISION_REQUIRE_APPROVAL,
        ),
        ("tool", "execute"): SecurityPolicyRule(
            required_scopes_any=frozenset([]),
            max_auto_risk=RISK_MEDIUM,
            high_risk_decision=DECISION_REQUIRE_APPROVAL,
        ),
        ("plugin", "activate"): SecurityPolicyRule(
            required_scopes_any=frozenset(
                [
                    "agent.admin",
                    "plugin.activate",
                ]
            ),
            max_auto_risk=RISK_MEDIUM,
            high_risk_decision=DECISION_REQUIRE_APPROVAL,
        ),
        ("sidecar", "start"): SecurityPolicyRule(
            required_scopes_any=frozenset(
                [
                    "agent.execute",
                    "agent.admin",
                    "tool.execute",
                ]
            ),
            max_auto_risk=RISK_MEDIUM,
            high_risk_decision=DECISION_REQUIRE_APPROVAL,
        ),
        ("sidecar", "stop"): SecurityPolicyRule(
            required_scopes_any=frozenset(
                [
                    "agent.execute",
                    "agent.admin",
                    "tool.execute",
                ]
            ),
            max_auto_risk=RISK_MEDIUM,
            high_risk_decision=DECISION_REQUIRE_APPROVAL,
        ),
        ("api", "read"): SecurityPolicyRule(
            required_scopes_any=frozenset(["api.read"]),
            max_auto_risk=RISK_LOW,
            high_risk_decision=DECISION_DENY,
        ),
        ("api", "write"): SecurityPolicyRule(
            required_scopes_any=frozenset(["api.write"]),
            max_auto_risk=RISK_MEDIUM,
            high_risk_decision=DECISION_REQUIRE_APPROVAL,
        ),
    }


class SecurityPolicyEngine:
    def __init__(
        self,
        *,
        policy_version: str = "v1",
        rules: Mapping[tuple[str, str], SecurityPolicyRule] | None = None,
        identity_constraints: list[str] = [],
        tool_budget_policy: ToolBudgetPolicy | None = None,
        default_tool_required_scopes: frozenset[str] | None = None,
    ) -> None:
        self._policy_version = str(policy_version).strip() or "v1"
        self._rules = dict(rules or _default_rules())
        self._identity_constraints = identity_constraints or []
        self._tool_budget_policy = tool_budget_policy or ToolBudgetPolicy()
        normalized_default_scopes = {
            _normalize_token(scope)
            for scope in (default_tool_required_scopes or frozenset({"tool.execute"}))
            if _normalize_token(scope)
        }
        self._default_tool_required_scopes = frozenset(normalized_default_scopes)

    @property
    def policy_version(self) -> str:
        return self._policy_version

    @property
    def tool_budget_policy(self) -> ToolBudgetPolicy:
        return self._tool_budget_policy

    def evaluate_tool_budget(
        self,
        *,
        tool_name: str,
        budget_cost: int,
        state: ToolBudgetState,
    ) -> SecurityPolicyDecision:
        normalized_tool = _normalize_token(tool_name)
        call_limit = max(0, int(self._tool_budget_policy.max_calls_per_run))
        per_tool_limit = max(0, int(self._tool_budget_policy.max_calls_per_tool))
        budget_limit = max(0, int(self._tool_budget_policy.max_budget_cost_per_run))
        proposed_cost = max(0, int(budget_cost))

        if state.tool_calls_total >= call_limit:
            return SecurityPolicyDecision(
                decision=DECISION_DENY,
                reason_code="tool_budget_calls_exceeded",
                policy_version=self._policy_version,
                details={
                    "max_calls_per_run": call_limit,
                    "tool_calls_total": state.tool_calls_total,
                },
            )

        current_tool_calls = int(state.per_tool_calls.get(normalized_tool, 0))
        if current_tool_calls >= per_tool_limit:
            return SecurityPolicyDecision(
                decision=DECISION_DENY,
                reason_code="tool_budget_calls_exceeded",
                policy_version=self._policy_version,
                details={
                    "tool_name": normalized_tool,
                    "max_calls_per_tool": per_tool_limit,
                    "tool_calls": current_tool_calls,
                },
            )

        if (state.budget_cost_total + proposed_cost) > budget_limit:
            return SecurityPolicyDecision(
                decision=DECISION_DENY,
                reason_code="tool_budget_cost_exceeded",
                policy_version=self._policy_version,
                details={
                    "max_budget_cost_per_run": budget_limit,
                    "budget_cost_total": state.budget_cost_total,
                    "proposed_cost": proposed_cost,
                },
            )

        return SecurityPolicyDecision(
            decision=DECISION_ALLOW,
            reason_code="allowed",
            policy_version=self._policy_version,
        )

    def record_tool_budget_usage(
        self,
        *,
        tool_name: str,
        budget_cost: int,
        state: ToolBudgetState,
    ) -> None:
        normalized_tool = _normalize_token(tool_name)
        state.tool_calls_total += 1
        state.budget_cost_total += max(0, int(budget_cost))
        state.per_tool_calls[normalized_tool] = (
            int(state.per_tool_calls.get(normalized_tool, 0)) + 1
        )

    def evaluate(self, check: SecurityPolicyCheck) -> SecurityPolicyDecision:
        # First check identity constraints before other checks
        violation = self._check_identity_constraint_violation(check)
        if violation:
            return SecurityPolicyDecision(
                decision=DECISION_REQUIRE_APPROVAL,
                reason_code="identity_hard_constraint_restricted",
                policy_version=self._policy_version,
                details={
                    "identity_constraint_violated": violation,
                    "tool_name": check.action.tool_name,
                    "action": f"{check.action.verb} {check.action.resource}",
                },
            )

        action_key = (
            _normalize_token(check.action.resource),
            _normalize_token(check.action.verb),
        )
        rule = self._rules.get(action_key)
        if rule is None:
            return SecurityPolicyDecision(
                decision=DECISION_DENY,
                reason_code="unknown_action",
                policy_version=self._policy_version,
                details={
                    "resource": action_key[0],
                    "verb": action_key[1],
                },
            )

        normalized_scopes = {_normalize_token(scope) for scope in check.actor.scopes}
        if rule.required_scopes_any and not normalized_scopes.intersection(
            rule.required_scopes_any
        ):
            return SecurityPolicyDecision(
                decision=DECISION_DENY,
                reason_code="missing_scope",
                policy_version=self._policy_version,
                details={"required_any_scope": sorted(rule.required_scopes_any)},
            )

        required_scopes_all = {
            _normalize_token(scope)
            for scope in check.action.required_scopes_all
            if _normalize_token(scope)
        }
        if action_key == ("tool", "execute") and not required_scopes_all:
            required_scopes_all = set(self._default_tool_required_scopes)
        if required_scopes_all:
            missing_scopes = sorted(
                scope for scope in required_scopes_all if scope not in normalized_scopes
            )
            if missing_scopes:
                return SecurityPolicyDecision(
                    decision=DECISION_DENY,
                    reason_code="missing_tool_scope",
                    policy_version=self._policy_version,
                    details={
                        "required_scopes_all": sorted(required_scopes_all),
                        "missing_scopes": missing_scopes,
                    },
                )

        normalized_risk = _normalize_risk(check.action.risk)
        if _RISK_ORDER[normalized_risk] > _RISK_ORDER[rule.max_auto_risk]:
            if rule.high_risk_decision == DECISION_DENY:
                return SecurityPolicyDecision(
                    decision=DECISION_DENY,
                    reason_code="risk_exceeds_auto_threshold",
                    policy_version=self._policy_version,
                    details={
                        "risk": normalized_risk,
                        "max_auto_risk": rule.max_auto_risk,
                    },
                )
            return SecurityPolicyDecision(
                decision=DECISION_REQUIRE_APPROVAL,
                reason_code="approval_required_high_risk",
                policy_version=self._policy_version,
                required_approval_level="owner",
                details={
                    "risk": normalized_risk,
                    "max_auto_risk": rule.max_auto_risk,
                },
            )

        if action_key == ("tool", "execute"):
            tool_name = _normalize_token(check.action.tool_name)
            if not tool_name:
                return SecurityPolicyDecision(
                    decision=DECISION_DENY,
                    reason_code="tool_name_required",
                    policy_version=self._policy_version,
                )

        return SecurityPolicyDecision(
            decision=DECISION_ALLOW,
            reason_code="allowed",
            policy_version=self._policy_version,
        )

    def update_identity_constraints(self, identity_constraints: list[str]) -> None:
        self._identity_constraints = identity_constraints or []

    def _check_identity_constraint_violation(
        self, check: SecurityPolicyCheck
    ) -> str | None:
        """Check if the action violates identity hard constraints.

        Returns the constraint string if violated, None otherwise.
        """
        for constraint in self._identity_constraints:
            constraint_upper = constraint.upper().strip()
            if constraint_upper.startswith(("MUST NOT ", "NEVER ", "DO NOT ")):
                words = constraint.split(None, 3)
                if len(words) >= 3:
                    words[0].upper()  # 'MUST', 'NEVER', 'DO'
                    words[1].upper()  # 'NOT'
                    action_word = words[2].lower()  # e.g., 'delete', 'write'

                    # Match against various aspects of the action
                    matches = (
                        action_word in (check.action.tool_name or "").lower()
                        if check.action.tool_name
                        else False
                        or action_word in check.action.verb.lower()
                        or action_word in (check.action.tool_name or "").lower()
                        or action_word in (check.action.resource or "").lower()
                    )
                    if matches:
                        return constraint  # Found violation

        return None


def default_internal_actor(
    agent_id: str,
    include_admin: bool = False,
) -> SecurityPolicyActor:
    normalized_agent_id = _normalize_token(agent_id)
    scopes = {
        "agent.execute",
        "tool.execute",
        "gateway.turn.execute",
        "channel.message.send",
        f"agent.{normalized_agent_id}.execute",
    }
    if include_admin:
        scopes.add("agent.admin")
        scopes.add(f"agent.{normalized_agent_id}.admin")
        scopes.add("plugin.activate")
    return SecurityPolicyActor(
        role="internal",
        scopes=set(scopes),
        agent_id=normalized_agent_id,
    )


def default_external_actor(
    owner_id: str = "", roles: list[str] | None = None
) -> SecurityPolicyActor:
    scopes: set[str] = set()
    if roles:
        scopes.update(roles)
    if owner_id:
        scopes.add(f"user.{owner_id}.request")
        scopes.add(f"user.{owner_id}.read")
    else:
        scopes.add("user.unauthenticated.request")
    return SecurityPolicyActor(
        role="external",
        scopes=set(scopes),
        owner_id=owner_id,
    )


def derive_plugin_activation_risk(
    *,
    trust_tier: str,
    requested_capabilities: set[str],
) -> str:
    normalized_caps = {
        _normalize_token(capability).replace("__", ".")
        for capability in (requested_capabilities or set())
    }
    for capability in normalized_caps:
        if any(token in capability for token in _PLUGIN_CRITICAL_CAPABILITY_TOKENS):
            return RISK_CRITICAL
    for capability in normalized_caps:
        if any(token in capability for token in _PLUGIN_MUTATING_CAPABILITY_TOKENS):
            return RISK_HIGH

    tier = str(trust_tier or "").strip().lower().replace("_", "-")
    if tier in {"verified", "trusted"}:
        return RISK_LOW
    if tier in {"local-dev", "internal"}:
        return RISK_MEDIUM
    if tier in {"restricted", "unknown", "untrusted"}:
        return RISK_HIGH
    return RISK_MEDIUM


def evaluate_plugin_trust_policy(
    *,
    trust_tier: str,
    requested_capabilities: set[str],
    provenance_source: str,
    provenance_verified: bool,
    provenance_publisher: str,
    policy_version: str = "v1",
) -> SecurityPolicyDecision:
    tier = str(trust_tier or "").strip().lower().replace("_", "-")
    source = str(provenance_source or "").strip().lower().replace("_", "-")
    publisher = str(provenance_publisher or "").strip()
    normalized_caps = {
        _normalize_token(capability).replace("__", ".")
        for capability in (requested_capabilities or set())
    }

    if tier == "verified":
        if source == "local-path" and not provenance_verified:
            return SecurityPolicyDecision(
                decision=DECISION_DENY,
                reason_code="plugin_verified_requires_nonlocal_or_verified_provenance",
                policy_version=policy_version,
                details={
                    "trust_tier": tier,
                    "provenance_source": source,
                    "provenance_verified": bool(provenance_verified),
                },
            )
        if not provenance_verified:
            return SecurityPolicyDecision(
                decision=DECISION_DENY,
                reason_code="plugin_verified_requires_verified_provenance",
                policy_version=policy_version,
                details={
                    "trust_tier": tier,
                    "provenance_source": source,
                    "provenance_verified": bool(provenance_verified),
                },
            )
        if not publisher:
            return SecurityPolicyDecision(
                decision=DECISION_DENY,
                reason_code="plugin_verified_requires_publisher",
                policy_version=policy_version,
                details={
                    "trust_tier": tier,
                    "provenance_source": source,
                },
            )

    if tier == "restricted":
        if any(
            any(token in capability for token in _PLUGIN_MUTATING_CAPABILITY_TOKENS)
            for capability in normalized_caps
        ):
            return SecurityPolicyDecision(
                decision=DECISION_DENY,
                reason_code="plugin_restricted_capability_blocked",
                policy_version=policy_version,
                details={
                    "trust_tier": tier,
                    "capabilities": sorted(normalized_caps),
                },
            )

    return SecurityPolicyDecision(
        decision=DECISION_ALLOW,
        reason_code="allowed",
        policy_version=policy_version,
        details={
            "trust_tier": tier,
            "provenance_source": source,
        },
    )
