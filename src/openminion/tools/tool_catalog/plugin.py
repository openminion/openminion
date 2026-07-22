from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from openminion.modules.tool.contracts.model_ids import (
    ALL_MODEL_TOOL_IDS_SET,
    MODEL_TOOL_LIST,
    MODEL_TOOL_SEARCH,
)
from openminion.modules.tool.registry import ToolRegistry, ToolSpec


class ToolSearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(
        default="",
        description="Optional filter — returns tools whose name or description contains this string (case-insensitive)",
    )
    max_results: int = Field(default=50, ge=1, le=200)
    library: bool = Field(
        default=False,
        description="When true, return the authored-tool library instead of the model tool catalog.",
    )
    tier: Literal["experimental", "trusted", "all"] = Field(
        default="all",
        description="Authored-tool tier filter used when library=true.",
    )
    include_removed: bool = Field(
        default=False,
        description="Include removed authored tools when library=true.",
    )


def _h_tool_search(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    validated = ToolSearchArgs.model_validate(args)
    if validated.library:
        service = getattr(ctx, "authored_tools_api", None)
        if service is None:
            return {
                "ok": False,
                "error": {
                    "code": "AUTHORED_TOOLS_UNAVAILABLE",
                    "message": "Authored tool service is not available in this runtime context.",
                },
            }
        tools = service.list_authored_tools(
            tier=validated.tier,
            include_removed=validated.include_removed,
        )
        return {
            "ok": True,
            "tools": tools,
            "count": len(tools),
        }
    query = validated.query.strip().lower()
    max_results = validated.max_results

    # Use the shared manager surface so the catalog does not depend on
    # private compiled-state internals.
    try:
        from openminion.modules.tool.dispatch import get_registry_manager

        manager = get_registry_manager()
        catalog_rows = tuple(getattr(manager, "model_tool_catalog", lambda: ())())
    except Exception:
        catalog_rows = ()

    tools: list[dict[str, Any]] = []

    if catalog_rows:
        for tool_id, description in catalog_rows:
            if tool_id not in ALL_MODEL_TOOL_IDS_SET:
                continue
            if (
                query
                and query not in tool_id.lower()
                and query not in description.lower()
            ):
                continue
            tools.append({"name": tool_id, "description": description})
            if len(tools) >= max_results:
                break
    else:
        # Fallback: enumerate ALL_MODEL_TOOL_IDS_SET directly with no descriptions.
        for tool_id in sorted(ALL_MODEL_TOOL_IDS_SET):
            if query and query not in tool_id.lower():
                continue
            tools.append({"name": tool_id, "description": ""})
            if len(tools) >= max_results:
                break

    return {"ok": True, "tools": tools, "count": len(tools)}


def register(registry: ToolRegistry) -> None:
    registry.add(
        ToolSpec(
            name=MODEL_TOOL_LIST,
            args_model=ToolSearchArgs,
            min_scope="READ_ONLY",
            handler=_h_tool_search,
            idempotent=True,
        )
    )
    # Compatibility alias — same handler; prefer tool.list going forward.
    registry.add(
        ToolSpec(
            name=MODEL_TOOL_SEARCH,
            args_model=ToolSearchArgs,
            min_scope="READ_ONLY",
            handler=_h_tool_search,
            idempotent=True,
        )
    )
