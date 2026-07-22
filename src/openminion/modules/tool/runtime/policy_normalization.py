"""Tool module support for runtime policy normalization."""

from typing import Any, cast
from collections.abc import Sequence


def canonicalize_policy_tool_token(raw_token: str) -> str:
    token = str(raw_token or "").strip()
    return token


def dedupe(values: Sequence[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        token = str(value or "").strip()
        if not token or token in seen:
            continue
        out.append(token)
        seen.add(token)
    return out


def dedupe_normalized(values: Sequence[str]) -> list[str]:
    normalized = [
        str(item or "").strip().lower() for item in values if str(item or "").strip()
    ]
    return dedupe(normalized)


def canonical_tool_name(tool_name: str) -> str:
    """Map model/runtime aliases to a stable policy-facing tool name."""
    token = str(tool_name or "").strip()
    if not token:
        return ""

    from ..dispatch import _get_registry_manager

    mgr = _get_registry_manager()
    model_tool_id = mgr.normalize_raw_name(token)
    if model_tool_id:
        return model_tool_id

    return token


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, val in override.items():
        if key in out and isinstance(out[key], dict) and isinstance(val, dict):
            out[key] = deep_merge(
                cast(dict[str, Any], out[key]), cast(dict[str, Any], val)
            )
            continue
        out[key] = val
    return out


def normalize_policy_legacy_aliases(raw: dict[str, Any]) -> dict[str, Any]:
    """Runtime helper."""
    if not isinstance(raw, dict):
        return {}
    out = dict(raw)
    tools = out.get("tools")
    if isinstance(tools, dict):
        tools_out = dict(tools)
        allow_prefix = tools_out.get("allow_prefix")
        if isinstance(allow_prefix, list):
            tools_out["allow_prefix"] = [str(item) for item in allow_prefix]
        for key in ("deny_exact",):
            values = tools_out.get(key)
            if isinstance(values, list):
                tools_out[key] = [
                    canonicalize_policy_tool_token(str(item)) for item in values
                ]
        out["tools"] = tools_out

    confirm = out.get("confirm")
    if isinstance(confirm, dict):
        confirm_out = dict(confirm)
        required_tools = confirm_out.get("required_tools")
        if isinstance(required_tools, list):
            confirm_out["required_tools"] = [
                canonicalize_policy_tool_token(str(item)) for item in required_tools
            ]
        required_when = confirm_out.get("required_when")
        if isinstance(required_when, list):
            normalized_rules: list[Any] = []
            for rule in required_when:
                if isinstance(rule, dict):
                    rule_out = dict(rule)
                    token = str(rule_out.get("tool", ""))
                    if token:
                        rule_out["tool"] = canonicalize_policy_tool_token(token)
                    normalized_rules.append(rule_out)
                else:
                    normalized_rules.append(rule)
            confirm_out["required_when"] = normalized_rules
        out["confirm"] = confirm_out
    return out
