"""Declarative graph tool family."""

from openminion.modules.tool.framework import ToolDecl, ToolFamilySpec

from .interfaces import (
    TOOL_GRAPH_NEIGHBORHOOD,
    TOOL_GRAPH_QUERY,
    TOOL_GRAPH_REFRESH,
)
from .plugin import (
    GraphNeighborhoodArgs,
    GraphQueryArgs,
    GraphRefreshArgs,
    _h_neighborhood,
    _h_query,
    _h_refresh,
)

GRAPH_FAMILY = ToolFamilySpec(
    module_id="graph",
    min_scope_default="READ_ONLY",
    common_tags=("plugin", "graph"),
    common_capabilities=("graph",),
    tools=(
        ToolDecl(
            name=TOOL_GRAPH_QUERY,
            args_model=GraphQueryArgs,
            handler=_h_query,
            description="Search one configured knowledge-graph source.",
            idempotent=True,
            capabilities=("read_only",),
        ),
        ToolDecl(
            name=TOOL_GRAPH_NEIGHBORHOOD,
            args_model=GraphNeighborhoodArgs,
            handler=_h_neighborhood,
            description="Inspect the bounded neighborhood of one graph entity.",
            idempotent=True,
            capabilities=("read_only",),
        ),
        ToolDecl(
            name=TOOL_GRAPH_REFRESH,
            args_model=GraphRefreshArgs,
            handler=_h_refresh,
            description="Refresh one configured graph source from its configured root.",
            min_scope="WRITE_SAFE",
            dangerous=True,
            idempotent=False,
            block_under_readonly=True,
            capabilities=("write_safe",),
        ),
    ),
)

__all__ = ["GRAPH_FAMILY"]
