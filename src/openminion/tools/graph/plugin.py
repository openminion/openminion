"""Handlers for provider-neutral graph tools."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, NoReturn

from pydantic import BaseModel, ConfigDict, Field

from openminion.modules.context.knowledge import (
    GraphNeighborhoodRequest,
    GraphQueryRequest,
    GraphRefreshRequest,
    KnowledgeGraphError,
)
from openminion.modules.tool.contracts.schemas import ErrorCode
from openminion.modules.tool import (
    RuntimeContext,
    resolve_knowledge_graph_service,
)
from openminion.modules.tool.errors import ToolRuntimeError

if TYPE_CHECKING:
    from openminion.modules.context.knowledge import KnowledgeGraphService


class _StrictArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GraphQueryArgs(_StrictArgs):
    source: str = Field(..., min_length=1)
    query: str = Field(..., min_length=1)
    limit: int = Field(default=12, ge=1, le=100)


class GraphNeighborhoodArgs(_StrictArgs):
    source: str = Field(..., min_length=1)
    entity_id: str = Field(..., min_length=1)
    depth: int = Field(default=1, ge=1, le=4)
    limit: int = Field(default=24, ge=1, le=100)


class GraphRefreshArgs(_StrictArgs):
    source: str = Field(..., min_length=1)


def _service(ctx: RuntimeContext) -> KnowledgeGraphService:
    service = resolve_knowledge_graph_service(ctx)
    if service is None:
        raise ToolRuntimeError(
            "DEPENDENCY_MISSING",
            "Knowledge-graph service is unavailable",
            {"reason_code": "graph_service_unavailable"},
        )
    return service


def _raise_tool_error(exc: KnowledgeGraphError) -> NoReturn:
    code: ErrorCode = "UPSTREAM_ERROR"
    if exc.code == "UNKNOWN_PROVIDER":
        code = "NOT_FOUND"
    elif exc.code in {
        "DISABLED_PROVIDER",
        "INVALID_CAPABILITY",
        "MISSING_REQUIRED_CAPABILITY",
        "UNSUPPORTED_CAPABILITY",
    }:
        code = "INVALID_ARGUMENT"
    elif exc.details.get("reason_code") == "package_unavailable":
        code = "DEPENDENCY_MISSING"
    raise ToolRuntimeError(
        code,
        exc.message,
        {**exc.details, "graph_error_code": exc.code},
    ) from exc


def _h_query(args: dict[str, Any], ctx: RuntimeContext) -> dict[str, Any]:
    source = str(args["source"])
    try:
        results = _service(ctx).query(
            GraphQueryRequest(
                query=str(args["query"]),
                max_results=int(args["limit"]),
            ),
            provider_names=(source,),
        )
    except KnowledgeGraphError as exc:
        _raise_tool_error(exc)
    return {
        "source": source,
        "count": sum(len(result.items) for result in results),
        "results": [result.to_dict() for result in results],
    }


def _h_neighborhood(args: dict[str, Any], ctx: RuntimeContext) -> dict[str, Any]:
    source = str(args["source"])
    try:
        results = _service(ctx).neighborhood(
            GraphNeighborhoodRequest(
                entity_id=str(args["entity_id"]),
                depth=int(args["depth"]),
                max_results=int(args["limit"]),
            ),
            provider_names=(source,),
        )
    except KnowledgeGraphError as exc:
        _raise_tool_error(exc)
    return {
        "source": source,
        "count": sum(len(result.items) for result in results),
        "results": [result.to_dict() for result in results],
    }


def _h_refresh(args: dict[str, Any], ctx: RuntimeContext) -> dict[str, Any]:
    source = str(args["source"])
    try:
        results = _service(ctx).refresh(
            GraphRefreshRequest(mode="manual", full=False),
            provider_names=(source,),
        )
    except KnowledgeGraphError as exc:
        _raise_tool_error(exc)
    return {
        "source": source,
        "results": [result.to_dict() for result in results],
    }


__all__ = [
    "GraphNeighborhoodArgs",
    "GraphQueryArgs",
    "GraphRefreshArgs",
]
