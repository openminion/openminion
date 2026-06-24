"""Action-policy parsing helpers."""

from __future__ import annotations

from typing import Any

from openminion.base.config.core import (
    ActionPolicyConfig,
    ActionPolicyMatchConfig,
    ActionPolicyRuleConfig,
    OpenMinionConfig,
)
from openminion.base.config.parse import _as_bool


def _normalize_action_policy_mode(raw_value: Any) -> str:
    value = str(raw_value or "").strip().lower()
    return value if value in {"ask", "auto", "bypass"} else "auto"


def _normalize_action_policy_default_action(raw_value: Any) -> str:
    value = str(raw_value or "").strip().lower()
    return value if value in {"allow", "require_confirm"} else "require_confirm"


def _normalize_action_policy_rule_mode(raw_value: Any) -> str:
    value = str(raw_value or "").strip().lower()
    return value if value in {"ask", "auto", "block"} else "ask"


def _as_token_list(raw_value: Any, fallback: list[str]) -> list[str]:
    if not isinstance(raw_value, list):
        return list(fallback)
    tokens = [str(item).strip().lower() for item in raw_value if str(item).strip()]
    return tokens or list(fallback)


def _build_action_policy_config(
    action_policy_payload: dict[str, Any],
) -> ActionPolicyConfig:
    raw_rules = action_policy_payload.get("rules")
    action_policy_rules_payload = raw_rules if isinstance(raw_rules, list) else []
    rules: list[ActionPolicyRuleConfig] = []
    for raw_rule in action_policy_rules_payload:
        if not isinstance(raw_rule, dict):
            continue
        raw_match_value = raw_rule.get("match")
        raw_match = raw_match_value if isinstance(raw_match_value, dict) else {}
        rules.append(
            ActionPolicyRuleConfig(
                match=ActionPolicyMatchConfig(
                    tool_category=str(raw_match.get("tool_category", "")).strip(),
                    tool_name=str(raw_match.get("tool_name", "")).strip(),
                    min_risk_class=str(raw_match.get("min_risk_class", "")).strip(),
                ),
                mode=_normalize_action_policy_rule_mode(raw_rule.get("mode")),
            )
        )

    return ActionPolicyConfig(
        mode=_normalize_action_policy_mode(action_policy_payload.get("mode")),
        default_action=_normalize_action_policy_default_action(
            action_policy_payload.get("default_action")
        ),
        allow_read_only_without_prompt=_as_bool(
            action_policy_payload.get("allow_read_only_without_prompt"),
            True,
        ),
        rules=rules,
        affirmative_tokens=_as_token_list(
            action_policy_payload.get("affirmative_tokens"),
            ActionPolicyConfig().affirmative_tokens,
        ),
        negative_tokens=_as_token_list(
            action_policy_payload.get("negative_tokens"),
            ActionPolicyConfig().negative_tokens,
        ),
    )


def _action_policy_to_payload(config: OpenMinionConfig) -> dict[str, Any]:
    return {
        "mode": _normalize_action_policy_mode(config.action_policy.mode),
        "default_action": _normalize_action_policy_default_action(
            config.action_policy.default_action
        ),
        "allow_read_only_without_prompt": bool(
            config.action_policy.allow_read_only_without_prompt
        ),
        "rules": [
            {
                "match": {
                    "tool_category": rule.match.tool_category,
                    "tool_name": rule.match.tool_name,
                    "min_risk_class": rule.match.min_risk_class,
                },
                "mode": _normalize_action_policy_rule_mode(rule.mode),
            }
            for rule in config.action_policy.rules
        ],
        "affirmative_tokens": list(config.action_policy.affirmative_tokens),
        "negative_tokens": list(config.action_policy.negative_tokens),
    }
