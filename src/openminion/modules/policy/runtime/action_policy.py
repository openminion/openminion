from pathlib import Path
from typing import Any, Mapping

from openminion.base.config import OpenMinionConfig
from openminion.modules.tool.plugin_api import POLICY_AUTHORIZATION_DESCRIPTORS

from ..models import (
    InvocationSummary,
    PolicyConfig,
    PolicyDecision,
    PolicyRule,
    RiskClass,
    RiskSpec,
    policy_config_from_action_policy,
)
from ..constants import POLICY_DECISION_DENY


def select_policy_rule(
    *,
    invocation: InvocationSummary,
    risk: RiskSpec,
    config: PolicyConfig,
    risk_order: Mapping[RiskClass, int],
) -> PolicyRule | None:
    tool_name = (
        invocation.tool
        if invocation.method == "default"
        else f"{invocation.tool}.{invocation.method}"
    )
    matches = [
        rule
        for rule in config.rules
        if rule.tool_name == tool_name
        and (
            not rule.min_risk_class
            or risk_order[risk.risk_class] >= risk_order[rule.min_risk_class]
        )
    ]
    if not matches:
        return None
    priority = {"auto": 0, "ask": 1, "block": 2}
    return max(matches, key=lambda rule: priority[rule.mode])


def blocked_rule_decision(
    matching_rule: PolicyRule | None, risk: RiskSpec
) -> PolicyDecision | None:
    if matching_rule is None or matching_rule.mode != "block":
        return None
    return PolicyDecision(
        decision=POLICY_DECISION_DENY,
        reason_code="CONFIG_RULE_BLOCK",
        reason="Denied by configured exact-tool rule",
        risk=risk,
        details={"rule_mode": "block", "tool_name": matching_rule.tool_name},
    )


def merge_policy_risk(
    registered: RiskSpec,
    command: RiskSpec | None,
    *,
    risk_order: Mapping[RiskClass, int],
) -> RiskSpec:
    if (
        command is None
        or risk_order[command.risk_class] <= risk_order[registered.risk_class]
    ):
        return registered
    return RiskSpec(
        risk_class=command.risk_class,
        side_effects=registered.side_effects,
        reversibility=registered.reversibility,
        default_confirm=registered.default_confirm,
        sensitive_targets=list(registered.sensitive_targets),
    )


def resolve_profile_action_policy(config: OpenMinionConfig, profile: Any) -> Any:
    if profile.action_policy is not None:
        return profile.action_policy
    return config.action_policy


def build_action_policy_service(
    *,
    config: OpenMinionConfig,
    tool_registry: Any,
    data_root: Path,
) -> Any:
    from .service import PolicyCtl

    policy_dir = data_root / "policy"
    policy_dir.mkdir(parents=True, exist_ok=True)
    policy_ctl = PolicyCtl.with_sqlite(
        policy_dir / "policy.db",
        config=policy_config_from_action_policy(config.action_policy),
    )
    for tool_name, tool in tool_registry.list().items():
        risk = derive_tool_risk_spec(tool_name=tool_name, tool=tool)
        policy_ctl.register_risk(tool_name, risk)
        if "." not in tool_name:
            policy_ctl.register_risk(f"{tool_name}.default", risk)
    return policy_ctl


def derive_tool_risk_spec(*, tool_name: str, tool: Any) -> RiskSpec:
    tool_id, _, method = tool_name.rpartition(".")
    descriptor = POLICY_AUTHORIZATION_DESCRIPTORS.get((tool_id, method))
    if descriptor is not None:
        return RiskSpec.from_dict(
            {
                "risk_class": descriptor.risk_class,
                "side_effects": descriptor.side_effects,
                "reversibility": descriptor.reversibility,
                "default_confirm": True,
            }
        )

    min_scope = (
        str(getattr(tool, "min_scope", "READ_ONLY") or "READ_ONLY").strip().upper()
    )
    dangerous = bool(getattr(tool, "dangerous", False))
    idempotent = bool(getattr(tool, "idempotent", True))
    policy_risk = str(getattr(getattr(tool, "policy", None), "risk", "") or "")
    if policy_risk.strip().lower() in {"high", "critical"}:
        dangerous = True

    if dangerous:
        return RiskSpec(
            risk_class="destructive",
            side_effects="local",
            reversibility="irreversible",
            default_confirm=True,
        )
    if min_scope == "READ_ONLY":
        return RiskSpec(
            risk_class="read",
            side_effects="none",
            reversibility="reversible",
            default_confirm=False,
        )
    if min_scope == "WRITE_SAFE":
        return RiskSpec(
            risk_class="write",
            side_effects="local",
            reversibility="reversible" if idempotent else "unknown",
            default_confirm=not idempotent,
        )
    if min_scope in {"POWER_USER", "UI_AUTOMATION"}:
        return RiskSpec(
            risk_class="exec",
            side_effects="local",
            reversibility="unknown",
            default_confirm=True,
        )
    return RiskSpec(
        risk_class="write",
        side_effects="local",
        reversibility="unknown",
        default_confirm=not idempotent,
    )


__all__ = (
    "build_action_policy_service",
    "blocked_rule_decision",
    "derive_tool_risk_spec",
    "policy_config_from_action_policy",
    "merge_policy_risk",
    "resolve_profile_action_policy",
    "select_policy_rule",
)
