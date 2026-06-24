from __future__ import annotations

from dataclasses import replace

from openminion.base.config.core import ActionPolicyConfig

_ACTION_POLICY_MODE_DISABLED = "disabled"
_ACTION_POLICY_MODE_ENFORCE = "enforce"
_ACTION_POLICY_MODE_ENFORCE_SAFE = "enforce_safe"
_ACTION_POLICY_MODE_MAP = {
    "ask": _ACTION_POLICY_MODE_ENFORCE,
    "auto": _ACTION_POLICY_MODE_ENFORCE_SAFE,
    "bypass": _ACTION_POLICY_MODE_DISABLED,
    "disabled": _ACTION_POLICY_MODE_DISABLED,
    "off": _ACTION_POLICY_MODE_DISABLED,
}

ACTION_POLICY_SESSION_OVERRIDE_KEY = "session_action_policy_mode_override"
ACTION_POLICY_USER_MODES = frozenset({"ask", "auto", "bypass"})


def normalize_action_policy_mode_override(mode: str | None) -> str | None:
    normalized = str(mode or "").strip().lower()
    if not normalized:
        return None
    return normalized if normalized in ACTION_POLICY_USER_MODES else None


def map_action_policy_mode(mode: str) -> str:
    normalized = str(mode or "").strip().lower()
    return _ACTION_POLICY_MODE_MAP.get(normalized, _ACTION_POLICY_MODE_ENFORCE)


def overlay_action_policy_mode(
    action_policy: ActionPolicyConfig,
    mode_override: str | None,
) -> ActionPolicyConfig:
    normalized = normalize_action_policy_mode_override(mode_override)
    if normalized is None:
        return action_policy
    return replace(action_policy, mode=normalized)


__all__ = [
    "ACTION_POLICY_SESSION_OVERRIDE_KEY",
    "ACTION_POLICY_USER_MODES",
    "map_action_policy_mode",
    "normalize_action_policy_mode_override",
    "overlay_action_policy_mode",
]
