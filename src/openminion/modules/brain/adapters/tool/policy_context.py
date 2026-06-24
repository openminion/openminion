"""Policy-context helpers for the brain tool adapter."""

from collections.abc import Mapping
from typing import Any

from openminion.modules.tool import Policy, canonical_tool_name
from openminion.modules.tool.contracts.model_ids import MODEL_TASK_WATCH

_REACTIONS_SET_TOOL_NAME = "reactions.set"
_REACTIONS_DEFAULT_POLICIES = frozenset({"allow", "deny", "confirm"})


def _ensure_mutable_mapping(owner: dict[str, Any], key: str) -> dict[str, Any]:
    current = owner.get(key)
    if isinstance(current, dict):
        return current
    replacement: dict[str, Any] = {}
    if isinstance(current, Mapping):
        replacement.update(dict(current))
    owner[key] = replacement
    return replacement


def _ensure_mutable_str_list(owner: dict[str, Any], key: str) -> list[str]:
    current = owner.get(key)
    if isinstance(current, list):
        out = [str(item).strip() for item in current if str(item).strip()]
        owner[key] = out
        return out
    replacement: list[str] = []
    if isinstance(current, (tuple, set, frozenset)):
        replacement = [str(item).strip() for item in current if str(item).strip()]
    owner[key] = replacement
    return replacement


def _append_unique_tool_token(tokens: list[str], tool_name: str) -> None:
    target = canonical_tool_name(tool_name)
    existing = {canonical_tool_name(str(item)) for item in tokens if str(item).strip()}
    if target in existing:
        return
    tokens.append(tool_name)


def _normalize_reactions_default_policy(runtime_config: Any | None) -> str:
    runtime_cfg = getattr(runtime_config, "runtime", runtime_config)
    if runtime_cfg is None:
        return "allow"
    raw = str(getattr(runtime_cfg, "reactions_default_policy", "allow") or "allow")
    token = raw.strip().lower()
    if token not in _REACTIONS_DEFAULT_POLICIES:
        return "allow"
    return token


def _apply_reactions_default_policy(
    *,
    policy: Policy,
    runtime_config: Any | None,
) -> None:
    mode = _normalize_reactions_default_policy(runtime_config)
    if mode == "allow":
        return
    policy_raw = getattr(policy, "raw", None)
    if not isinstance(policy_raw, dict):
        return
    tools_cfg = _ensure_mutable_mapping(policy_raw, "tools")
    deny_exact = _ensure_mutable_str_list(tools_cfg, "deny_exact")
    if mode == "deny":
        _append_unique_tool_token(deny_exact, _REACTIONS_SET_TOOL_NAME)
        return
    if canonical_tool_name(_REACTIONS_SET_TOOL_NAME) in {
        canonical_tool_name(item) for item in deny_exact if str(item).strip()
    }:
        return
    confirm_cfg = _ensure_mutable_mapping(policy_raw, "confirm")
    required_tools = _ensure_mutable_str_list(confirm_cfg, "required_tools")
    _append_unique_tool_token(required_tools, _REACTIONS_SET_TOOL_NAME)


def _runtime_env_from_policy(policy: Policy | None) -> dict[str, str]:
    raw = getattr(policy, "raw", None)
    if not isinstance(raw, Mapping):
        return {}
    context_metadata = raw.get("context_metadata")
    if isinstance(context_metadata, Mapping):
        runtime_env = context_metadata.get("runtime_env")
        if isinstance(runtime_env, Mapping):
            return {
                str(k): str(v) for k, v in runtime_env.items() if str(k or "").strip()
            }
    runtime_env = raw.get("runtime_env")
    if isinstance(runtime_env, Mapping):
        return {str(k): str(v) for k, v in runtime_env.items() if str(k or "").strip()}
    return {}


def _agent_id_from_policy(policy: Policy | None) -> str:
    raw = getattr(policy, "raw", None)
    if not isinstance(raw, Mapping):
        return "openminion"
    context_metadata = raw.get("context_metadata")
    if isinstance(context_metadata, Mapping):
        token = str(context_metadata.get("agent_id", "") or "").strip()
        if token:
            return token
    token = str(raw.get("agent_id", "") or "").strip()
    if token:
        return token
    runtime_env = _runtime_env_from_policy(policy)
    env_token = str(runtime_env.get("OPENMINION_AGENT_ID", "") or "").strip()
    if env_token:
        return env_token
    return "openminion"


def _runtime_background_write_authorization_enabled(runtime_config: Any | None) -> bool:
    runtime_cfg = getattr(runtime_config, "runtime", runtime_config)
    brain_cfg = getattr(runtime_cfg, "brain", None)
    return bool(getattr(brain_cfg, "allow_background_write_authorization", False))


def _watch_write_authorization_requested(
    *,
    tool_name: str,
    args: Mapping[str, Any],
) -> bool:
    return tool_name == MODEL_TASK_WATCH and bool(args.get("write_authorized", False))


__all__ = [
    "_agent_id_from_policy",
    "_apply_reactions_default_policy",
    "_runtime_background_write_authorization_enabled",
    "_runtime_env_from_policy",
    "_watch_write_authorization_requested",
]
