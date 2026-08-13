from typing import Any

from ..runtime import RuntimeContext


def get_family_tool_config(ctx: Any, family_name: str) -> dict[str, Any]:
    if not isinstance(ctx, RuntimeContext):
        return {}
    policy = getattr(ctx, "policy", None)
    raw = getattr(policy, "raw", {})
    if not isinstance(raw, dict):
        return {}
    tools_cfg = raw.get("tools", {})
    if not isinstance(tools_cfg, dict):
        return {}
    family_cfg = tools_cfg.get(family_name, {})
    return dict(family_cfg) if isinstance(family_cfg, dict) else {}


def is_tool_disabled_by_policy(ctx: Any, family_name: str) -> bool:
    return not bool(get_family_tool_config(ctx, family_name).get("enabled", True))
