"""Direct-invocation admission for the time module's nine registered operations.

The model-facing inventory exposes one aggregate ``time`` tool, while the CLI's ``time``
group dispatches the registered runtime specs. These nine names are admitted for direct HTTP
invocation only while that visible parent exists, and their parameters come from the live
registry spec; every other runtime-only name stays refused.
"""

from __future__ import annotations

from typing import Any

TIME_MODEL_TOOL_NAME = "time"

TIME_OPERATION_TOOL_NAMES = frozenset(
    {
        "time.now",
        "time.in_zone",
        "time.convert",
        "time.parse_iso",
        "time.diff",
        "time.format",
        "time.start_of_day",
        "time.end_of_day",
        "time.next_cron",
    }
)


def direct_time_tool_report(
    runtime: Any, tool_name: str, inventory: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Describe one admitted time operation through the registry's locked lookup."""

    if tool_name not in TIME_OPERATION_TOOL_NAMES:
        return None
    if not any(item["name"] == TIME_MODEL_TOOL_NAME for item in inventory):
        return None
    registry = getattr(runtime, "tools", None)
    if registry is None:
        return None
    spec = registry._provider_spec_for_runtime_name(tool_name)
    if spec is None:
        # Unregistered since bootstrap: the name no longer resolves.
        return None
    return {
        "name": spec.name,
        "description": spec.description,
        "parameters": dict(spec.parameters),
        "enabled": True,
        "runtime_binding_id": "",
        "runtime_tool_name": spec.name,
        "source": "runtime_direct",
    }
