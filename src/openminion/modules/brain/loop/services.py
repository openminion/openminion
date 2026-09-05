from collections.abc import Mapping
from contextlib import nullcontext
from typing import Any


def runner_from_context(ctx: Any) -> Any | None:
    return getattr(getattr(ctx, "_services", None), "runner", None)


def runtime_allows_tool(runner: Any, tool_name: str) -> bool:
    is_allowed = getattr(getattr(runner, "tool_api", None), "is_tool_allowed", None)
    return not callable(is_allowed) or is_allowed(tool_name)


def turn_tool_allowlist(
    metadata: Mapping[str, Any],
    identity_tool_filter: Mapping[str, Any] | None = None,
) -> tuple[str, ...] | None:
    if str(metadata.get("subagent_context_id", "") or "").strip():
        raw = metadata.get("subagent_tool_allowlist", "")
    elif str(metadata.get("turn_tool_allowlist_supplied", "")).lower() == "true":
        raw = metadata.get("turn_tool_allowlist", "")
    else:
        raw = None

    requested = tuple(
        item.strip() for item in str(raw or "").split(",") if item.strip()
    )
    identity_filter = identity_tool_filter or {}
    identity_items = identity_filter.get("allowed_tools")
    identity_allowed = (
        tuple(str(item).strip() for item in identity_items if str(item).strip())
        if str(identity_filter.get("tool_use", "") or "").lower()
        in {"read_only", "restricted"}
        and isinstance(identity_items, list)
        else ()
    )
    if identity_allowed:
        if raw is None:
            return identity_allowed
        return tuple(item for item in requested if item in set(identity_allowed))
    return requested if raw is not None else None


def turn_tool_scope(
    runner: Any,
    metadata: Mapping[str, Any],
    identity_filter: Mapping[str, Any] | None,
) -> Any:
    allowed = turn_tool_allowlist(metadata, identity_filter)
    if allowed is None:
        return nullcontext()
    return runner.tool_api.restrict_tools(allowed)
