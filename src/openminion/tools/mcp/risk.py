"""MCP tool annotation and operator-override risk policy."""

import fnmatch
from typing import Any

from openminion.base.config.mcp import MCPServerConfig

from .schemas import MCPToolPosture, build_mcp_runtime_tool_name

_SCOPE_ORDER: dict[str, int] = {
    "READ_ONLY": 0,
    "WRITE_SAFE": 1,
    "POWER_USER": 2,
    "UI_AUTOMATION": 3,
}


def resolve_mcp_tool_posture(
    *,
    server: MCPServerConfig,
    remote_name: str,
    annotations: dict[str, Any],
) -> MCPToolPosture:
    """Resolve policy-visible posture from MCP annotations and overrides."""
    min_scope = "WRITE_SAFE"
    dangerous = False
    idempotent = False

    if server.trusted and _annotation_bool(annotations, "readOnlyHint"):
        min_scope = "READ_ONLY"
        dangerous = False
        idempotent = True

    idempotent_hint = _optional_annotation_bool(annotations, "idempotentHint")
    if server.trusted and idempotent_hint is not None:
        idempotent = idempotent_hint

    if _annotation_bool(annotations, "openWorldHint") and min_scope == "READ_ONLY":
        min_scope = "WRITE_SAFE"

    if _annotation_bool(annotations, "destructiveHint"):
        min_scope = _stricter_scope(min_scope, "POWER_USER")
        dangerous = True
        idempotent = False

    for override in server.tool_risk_overrides:
        if not _override_matches(
            pattern=override.pattern,
            server_name=server.name,
            remote_name=remote_name,
        ):
            continue
        override_scope = override.min_scope.strip().upper()
        if override_scope:
            min_scope = override_scope
        if override.dangerous is not None:
            dangerous = override.dangerous
        if override.idempotent is not None:
            idempotent = override.idempotent

    return MCPToolPosture(
        min_scope=min_scope,
        dangerous=dangerous,
        idempotent=idempotent,
    )


def _annotation_bool(annotations: dict[str, Any], key: str) -> bool:
    return _optional_annotation_bool(annotations, key) is True


def _optional_annotation_bool(annotations: dict[str, Any], key: str) -> bool | None:
    value = annotations.get(key)
    return value if isinstance(value, bool) else None


def _stricter_scope(left: str, right: str) -> str:
    return left if _SCOPE_ORDER[left] >= _SCOPE_ORDER[right] else right


def _override_matches(
    *,
    pattern: str,
    server_name: str,
    remote_name: str,
) -> bool:
    if not pattern:
        return False
    runtime_name = build_mcp_runtime_tool_name(
        server_name=server_name,
        remote_name=remote_name,
    )
    candidates = remote_name, runtime_name.rsplit(".", 1)[-1], runtime_name
    return any(fnmatch.fnmatch(candidate, pattern) for candidate in candidates)


__all__ = ["resolve_mcp_tool_posture"]
