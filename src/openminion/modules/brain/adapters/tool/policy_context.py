from collections.abc import Mapping
from typing import Any

from openminion.modules.tool import Policy, canonical_tool_name
from openminion.modules.tool.contracts.model_ids import (
    MODEL_FILE_WRITE,
    MODEL_TASK_WATCH,
)
from openminion.tools.exec.command_parser import is_read_only_exec_command
from openminion.tools.exec.process import resolve_shell_family

_REACTIONS_SET_TOOL_NAME = "reactions.set"
_REACTIONS_DEFAULT_POLICIES = frozenset({"allow", "deny", "confirm"})


def _ensure_mutable_mapping(owner: dict[str, Any], key: str) -> dict[str, Any]:
    current = owner.get(key)
    if not isinstance(current, dict):
        current = dict(current) if isinstance(current, Mapping) else {}
        owner[key] = current
    return current


def _ensure_mutable_str_list(owner: dict[str, Any], key: str) -> list[str]:
    current = owner.get(key)
    items = current if isinstance(current, (list, tuple, set, frozenset)) else ()
    replacement = [str(item).strip() for item in items if str(item).strip()]
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


def _apply_agent_command_policy(policy: Policy, agent_profile: Any | None) -> None:
    command_policy = getattr(agent_profile, "command_policy", {})
    allowed = [
        str(item).strip()
        for item in command_policy.get("allow", ())
        if str(item).strip()
    ]
    allow_host = command_policy.get("allow_host") is True
    if not allowed and not allow_host:
        return

    policy_raw = policy.raw
    commands_cfg = _ensure_mutable_mapping(policy_raw, "commands")
    command_allowlist = _ensure_mutable_str_list(commands_cfg, "allow")
    for command in allowed:
        if command not in command_allowlist:
            command_allowlist.append(command)

    if allow_host:
        exec_cfg = _ensure_mutable_mapping(policy_raw, "exec")
        exec_cfg["host_enabled"] = True
        host_allowlist = _ensure_mutable_str_list(exec_cfg, "allowlist")
        for command in allowed:
            if command not in host_allowlist:
                host_allowlist.append(command)


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


def _background_write_authorized(inputs: Any) -> bool:
    return (
        isinstance(inputs, Mapping)
        and bool(inputs.get("background_write_authorized"))
        and str(inputs.get("background_write_authorization_source", "") or "")
        == "watch_subscription"
    )


def _resolve_auto_confirm(
    *,
    tool_name: str,
    args: Mapping[str, Any],
    permission_mode: str,
    replay_confirmed: bool,
    background_write_authorized: bool,
) -> bool:
    if permission_mode == "bypass":
        return True
    if permission_mode == "auto":
        return tool_name in {MODEL_FILE_WRITE, "file.copy", "file.move"}
    if replay_confirmed or background_write_authorized:
        return True
    if tool_name == "blockchain.send_transaction":
        return True
    if tool_name == "exec.run":
        return bool(
            is_read_only_exec_command(
                str(args.get("command", "") or ""),
                shell_family=resolve_shell_family(),
            )
        )
    return False


__all__ = [
    "_agent_id_from_policy",
    "_apply_agent_command_policy",
    "_apply_reactions_default_policy",
    "_background_write_authorized",
    "_resolve_auto_confirm",
    "_runtime_background_write_authorization_enabled",
    "_runtime_env_from_policy",
    "_watch_write_authorization_requested",
]
