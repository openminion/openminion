"""Direct-invocation admission for the time owner's declared runtime operations.

The model-facing inventory exposes one aggregate ``time`` tool, while the CLI's ``time``
group dispatches the registered runtime specs. The time owner declares those runtime names
once; this module admits a requested declared name only while that visible parent exists and
the live registry resolves that exact name. Other runtime-only names stay refused.
"""

from __future__ import annotations

from typing import Any

from openminion.tools.time.constants import TIME_RUNTIME_TOOL_NAMES

TIME_MODEL_TOOL_NAME = "time"


def direct_time_tool_report(
    runtime: Any, tool_name: str, inventory: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Describe one admitted time operation through the registry's locked lookup."""

    if tool_name not in TIME_RUNTIME_TOOL_NAMES:
        return None
    if not any(item["name"] == TIME_MODEL_TOOL_NAME for item in inventory):
        return None
    registry = getattr(runtime, "tools", None)
    if registry is None:
        return None
    spec = registry._provider_spec_for_runtime_name(tool_name)
    if spec is None:
        # Unregistered since bootstrap: the requested name no longer resolves.
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
