"""Action-policy parsing helpers."""

from typing import Any

from openminion.base.config.action_policy import normalize_action_policy_mode_override
from openminion.base.config.base import ConfigError
from openminion.base.config.core import ActionPolicyConfig, ActionPolicyMatchConfig
from openminion.base.config.core import ActionPolicyRuleConfig, OpenMinionConfig
from openminion.base.config.parse import _as_bool

_RISKS = frozenset(
    "read,write,state_change,exec,security,financial,destructive".split(",")
)


def _normalize_default_action(raw_value: Any) -> str:
    value = str(raw_value or "").strip().lower()
    return value if value in {"allow", "require_confirm"} else "require_confirm"


def _as_token_list(raw_value: Any, fallback: list[str]) -> list[str]:
    if not isinstance(raw_value, list):
        return list(fallback)
    tokens = [str(item).strip().lower() for item in raw_value if str(item).strip()]
    return tokens or list(fallback)


def _parse_action_policy_rule(raw_rule: Any, index: int) -> ActionPolicyRuleConfig:
    path = f"action_policy.rules[{index}]"
    if not isinstance(raw_rule, dict):
        raise ConfigError(f"{path} must be an object")
    raw_match = raw_rule.get("match")
    if not isinstance(raw_match, dict):
        raise ConfigError(f"{path}.match must be an object")
    if str(raw_match.get("tool_category", "")).strip():
        raise ConfigError("action_policy rules require an exact canonical tool_name")
    tool_name = str(raw_match.get("tool_name", "")).strip()
    risk = str(raw_match.get("min_risk_class", "")).strip().lower()
    mode = str(raw_rule.get("mode", "")).strip().lower()
    if not tool_name:
        raise ConfigError(f"{path}.match.tool_name is required")
    if any(token in tool_name for token in "*?[]"):
        raise ConfigError(f"{path}.match.tool_name must be exact")
    if risk and risk not in _RISKS:
        raise ConfigError(f"{path}.match.min_risk_class is invalid")
    if mode not in {"block", "ask", "auto"}:
        raise ConfigError(f"{path}.mode is invalid")
    return ActionPolicyRuleConfig(
        match=ActionPolicyMatchConfig(tool_name=tool_name, min_risk_class=risk),
        mode=mode,
    )


def _build_action_policy_config(payload: dict[str, Any]) -> ActionPolicyConfig:
    raw_rules = payload.get("rules", [])
    if not isinstance(raw_rules, list):
        raise ConfigError("action_policy.rules must be a list")
    defaults = ActionPolicyConfig()
    get = payload.get
    parse_rule = _parse_action_policy_rule
    return ActionPolicyConfig(
        mode=normalize_action_policy_mode_override(get("mode")) or "auto",
        default_action=_normalize_default_action(get("default_action")),
        allow_read_only_without_prompt=_as_bool(
            get("allow_read_only_without_prompt"), True
        ),
        rules=[parse_rule(rule, index) for index, rule in enumerate(raw_rules)],
        affirmative_tokens=_as_token_list(
            get("affirmative_tokens"),
            defaults.affirmative_tokens,
        ),
        negative_tokens=_as_token_list(
            get("negative_tokens"),
            defaults.negative_tokens,
        ),
    )


def _action_policy_to_payload(config: OpenMinionConfig) -> dict[str, Any]:
    policy = config.action_policy
    return {
        "mode": normalize_action_policy_mode_override(policy.mode) or "auto",
        "default_action": _normalize_default_action(policy.default_action),
        "allow_read_only_without_prompt": policy.allow_read_only_without_prompt,
        "rules": [
            {
                "match": dict(
                    tool_name=rule.match.tool_name,
                    min_risk_class=rule.match.min_risk_class,
                ),
                "mode": rule.mode,
            }
            for rule in policy.rules
        ],
        "affirmative_tokens": list(policy.affirmative_tokens),
        "negative_tokens": list(policy.negative_tokens),
    }
