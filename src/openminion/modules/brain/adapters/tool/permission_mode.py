from functools import lru_cache
from typing import Final


PERMISSION_MODE_ALIASES: Final[dict[str, str]] = {
    "default": "ask",
    "plan": "ask",
    "acceptEdits": "auto",
    "bypassPermissions": "bypass",
    "readonly": "readonly",
    "readOnly": "readonly",
    "read_only": "readonly",
}


def _matches_tool_name(normalized_tool_name: str, pattern: str) -> bool:
    return normalized_tool_name == pattern or normalized_tool_name.startswith(
        pattern + "."
    )


def canonical_permission_mode(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "ask"
    lowered = raw.lower()
    return (
        PERMISSION_MODE_ALIASES.get(raw)
        or {
            "acceptedits": "auto",
            "bypasspermissions": "bypass",
            "read-only": "readonly",
        }.get(lowered)
        or (lowered if lowered in {"ask", "auto", "bypass", "readonly"} else "ask")
    )


def canonical_permission_overrides(
    value: object,
) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    overrides: dict[str, str] = {}
    for raw_tool_name, raw_mode in value.items():
        tool_name = str(raw_tool_name or "").strip().lower()
        if not tool_name:
            continue
        mode = canonical_permission_mode(str(raw_mode or ""))
        overrides[tool_name] = mode
    return overrides


def effective_permission_mode_for_tool(
    *,
    global_mode: str,
    permission_overrides: object,
    tool_name: str,
) -> str:
    normalized_tool_name = str(tool_name or "").strip().lower()
    overrides = canonical_permission_overrides(permission_overrides)
    if normalized_tool_name:
        for override_tool_name, override_mode in overrides.items():
            if _matches_tool_name(normalized_tool_name, override_tool_name):
                return override_mode
    return canonical_permission_mode(global_mode)


@lru_cache(maxsize=1)
def readonly_blocked_tool_names() -> frozenset[str]:
    from openminion.modules.tool import build_default_tool_registry

    registry = build_default_tool_registry()
    return frozenset(
        name
        for name, spec in registry.list().items()
        if bool(getattr(spec, "block_under_readonly", False))
    )


def is_tool_blocked_by_readonly(tool_name: str) -> bool:
    normalized = str(tool_name or "").strip().lower()
    if not normalized:
        return False
    for pattern in readonly_blocked_tool_names():
        if _matches_tool_name(normalized, pattern):
            return True
    return False


__all__ = [
    "PERMISSION_MODE_ALIASES",
    "canonical_permission_overrides",
    "canonical_permission_mode",
    "effective_permission_mode_for_tool",
    "is_tool_blocked_by_readonly",
    "readonly_blocked_tool_names",
]
