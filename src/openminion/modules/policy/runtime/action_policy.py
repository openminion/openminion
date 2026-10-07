from pathlib import Path
from typing import Any

from openminion.base.config import OpenMinionConfig
from openminion.modules.tool.plugin_api import POLICY_AUTHORIZATION_DESCRIPTORS

from ..models import RiskSpec, policy_config_from_action_policy


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
    "derive_tool_risk_spec",
    "policy_config_from_action_policy",
    "resolve_profile_action_policy",
)
