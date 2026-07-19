"""Memory provenance API routes."""

from __future__ import annotations

from typing import Any

from openminion.api.queries.memory import record_query, traces_by_memory, turn_trace

from .contracts import APIRouteContext, RouteResult


_PROVENANCE_PATH = "/memory/provenance"
_PROVENANCE_BY_MEMORY_PATH = "/memory/provenance/by-memory"
_RECORD_LIST_PATH = "/memory/records/list"
_RECORD_SEARCH_PATH = "/memory/records/search"


def handle_request(
    ctx: APIRouteContext,
    *,
    method_name: str,
    path: str,
    body: dict[str, Any] | None,
    query: str | None,
) -> RouteResult | None:
    """Handle memory product routes or return ``None`` for fallthrough."""

    if method_name == "POST" and path == _RECORD_LIST_PATH:
        return record_query(ctx, path=path, body=body, search=False)
    if method_name == "POST" and path == _RECORD_SEARCH_PATH:
        return record_query(ctx, path=path, body=body, search=True)
    if method_name != "GET":
        return None
    if path == _PROVENANCE_PATH:
        return turn_trace(path=path, query=query)
    if path == _PROVENANCE_BY_MEMORY_PATH:
        return traces_by_memory(path=path, query=query)
    return None


__all__ = ["handle_request"]
