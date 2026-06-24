from __future__ import annotations

from openminion.base.config import ActionPolicyConfig, resolve_agent_config
from openminion.base.config.action_policy import (
    ACTION_POLICY_SESSION_OVERRIDE_KEY,
    normalize_action_policy_mode_override,
    overlay_action_policy_mode,
)
from openminion.modules.session import SQLiteSessionStore


def read_session_action_policy_mode_override(
    store: SQLiteSessionStore,
    session_id: str,
) -> str | None:
    latest = store.get_latest_working_state(session_id)
    if isinstance(latest, dict):
        state_inline = latest.get("state_inline")
        if isinstance(state_inline, dict):
            normalized = normalize_action_policy_mode_override(
                state_inline.get(ACTION_POLICY_SESSION_OVERRIDE_KEY)
            )
            if normalized is not None:
                return normalized
    session = store.get_session(session_id)
    if not isinstance(session, dict):
        return None
    meta = session.get("meta", {})
    if not isinstance(meta, dict):
        return None
    return normalize_action_policy_mode_override(
        meta.get(ACTION_POLICY_SESSION_OVERRIDE_KEY)
    )


def resolve_configured_action_policy(
    config,
    *,
    agent_id: str | None,
) -> tuple[ActionPolicyConfig, str]:
    resolved_agent = resolve_agent_config(config, agent_id)
    agent_action_policy = getattr(resolved_agent, "action_policy", None)
    if agent_action_policy is not None:
        return agent_action_policy, "agent-config"
    return getattr(config, "action_policy", ActionPolicyConfig()), "global"


def resolve_effective_action_policy(
    configured_action_policy: ActionPolicyConfig,
    *,
    config_source: str,
    session_mode_override: str | None,
) -> tuple[ActionPolicyConfig, str]:
    if session_mode_override is None:
        return configured_action_policy, config_source
    return (
        overlay_action_policy_mode(configured_action_policy, session_mode_override),
        "session-override",
    )


def render_action_policy_summary(
    *,
    action_policy: ActionPolicyConfig,
    source: str,
) -> str:
    return (
        "action policy: "
        f"mode={action_policy.mode} "
        f"source={source} "
        f"default_action={action_policy.default_action} "
        f"allow_read_only_without_prompt={action_policy.allow_read_only_without_prompt} "
        f"rules={len(action_policy.rules)}"
    )
