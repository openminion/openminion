"""Memory provenance API routes."""

from __future__ import annotations

from collections.abc import Mapping
from http import HTTPStatus
from typing import Any
from urllib.parse import parse_qs

from openminion.modules.memory import (
    ListQueryOptions,
    MemoryNamespaceQueryInterface,
    SearchQueryOptions,
    default_provenance_recorder,
    resolve_namespace_filter,
)
from openminion.modules.memory.diagnostics.operability import serialize_for_json
from openminion.modules.memory.errors import (
    InvalidArgumentError,
    MemctlError,
    MemoryQueryUnavailableError,
)
from openminion.modules.memory.models import MemoryNamespace, MemoryRecord

from .contracts import (
    APIRouteContext,
    RouteResult,
    error_route_result,
    exception_route_result,
)


_PROVENANCE_PATH = "/memory/provenance"
_PROVENANCE_BY_MEMORY_PATH = "/memory/provenance/by-memory"
_RECORD_LIST_PATH = "/memory/records/list"
_RECORD_SEARCH_PATH = "/memory/records/search"
_LIST_FIELDS = frozenset(
    {
        "namespace",
        "scope",
        "types",
        "tiers",
        "include_invalidated",
        "limit",
        "offset",
    }
)
_SEARCH_FIELDS = (_LIST_FIELDS - {"offset"}) | {"query"}


def _single_query_value(query: str | None, name: str) -> str | None:
    if not query:
        return None
    parsed = parse_qs(query, keep_blank_values=False)
    values = parsed.get(name)
    return values[0] if values else None


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
        return _handle_record_query(ctx, path=path, body=body, search=False)
    if method_name == "POST" and path == _RECORD_SEARCH_PATH:
        return _handle_record_query(ctx, path=path, body=body, search=True)
    if method_name != "GET":
        return None
    if path == _PROVENANCE_PATH:
        return _handle_get_turn_trace(path=path, query=query)
    if path == _PROVENANCE_BY_MEMORY_PATH:
        return _handle_get_by_memory(path=path, query=query)
    return None


def _validate_string_list(body: Mapping[str, Any], field: str) -> list[str] | None:
    value = body.get(field)
    if value is None:
        return None
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise InvalidArgumentError(f"{field} must be a list of non-empty strings")
    return [item.strip() for item in value]


def _validate_int(
    body: Mapping[str, Any],
    field: str,
    *,
    default: int,
    minimum: int,
) -> int:
    value = body.get(field, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise InvalidArgumentError(f"{field} must be an integer >= {minimum}")
    return int(value)


def _memory_unavailable(path: str, message: str) -> RouteResult:
    return error_route_result(
        HTTPStatus.SERVICE_UNAVAILABLE,
        code="memory_unavailable",
        message=message,
        details={"path": path},
        retryable=True,
    )


def _resolved_namespace(
    body: Mapping[str, Any],
) -> tuple[MemoryNamespace, str | None]:
    namespace = body.get("namespace")
    if namespace is not None and not isinstance(namespace, Mapping):
        raise InvalidArgumentError("namespace must be an object")
    scope = body.get("scope")
    if scope is not None and (not isinstance(scope, str) or not scope.strip()):
        raise InvalidArgumentError("scope must be a non-empty string")
    return resolve_namespace_filter(scope=scope, namespace=namespace), scope


def _run_record_query(
    memory_queries: MemoryNamespaceQueryInterface,
    body: Mapping[str, Any],
    *,
    search: bool,
) -> tuple[list[MemoryRecord], MemoryNamespace, str | None]:
    namespace, scope = _resolved_namespace(body)
    types = _validate_string_list(body, "types")
    tiers = _validate_string_list(body, "tiers")
    include_invalidated = body.get("include_invalidated", False)
    if not isinstance(include_invalidated, bool):
        raise InvalidArgumentError("include_invalidated must be a boolean")
    limit = _validate_int(body, "limit", default=20 if search else 100, minimum=1)
    common = {
        "scopes": [scope.strip()] if scope else [],
        "types": types,
        "tiers": tiers,
        "include_invalidated": include_invalidated,
        "limit": limit,
        "namespaces": [namespace],
    }
    if search:
        query = body.get("query")
        if not isinstance(query, str) or not query.strip():
            raise InvalidArgumentError("query must be a non-empty string")
        records = list(
            memory_queries.search_records(
                SearchQueryOptions(query=query.strip(), **common)
            )
        )
    else:
        records = list(
            memory_queries.list_records(
                ListQueryOptions(
                    offset=_validate_int(body, "offset", default=0, minimum=0),
                    **common,
                )
            )
        )
    return records, namespace, scope


def _handle_record_query(
    ctx: APIRouteContext,
    *,
    path: str,
    body: dict[str, Any] | None,
    search: bool,
) -> RouteResult:
    if body is None:
        return error_route_result(
            HTTPStatus.BAD_REQUEST,
            code="invalid_request",
            message="JSON request body is required.",
            details={"path": path},
            retryable=False,
        )
    allowed_fields = _SEARCH_FIELDS if search else _LIST_FIELDS
    unknown_fields = sorted(set(body) - allowed_fields)
    if unknown_fields:
        return error_route_result(
            HTTPStatus.BAD_REQUEST,
            code="invalid_request",
            message=f"unknown request fields: {', '.join(unknown_fields)}",
            details={"path": path, "fields": unknown_fields},
            retryable=False,
        )
    if ctx.runtime is None:
        return _memory_unavailable(path, "memory query runtime is unavailable")
    memory_queries = getattr(ctx.runtime, "memory_queries", None)
    if memory_queries is None:
        return _memory_unavailable(path, "memory query provider is unavailable")

    try:
        records, namespace, scope = _run_record_query(
            memory_queries,
            body,
            search=search,
        )
    except InvalidArgumentError as exc:
        return error_route_result(
            HTTPStatus.BAD_REQUEST,
            code="invalid_request",
            message=str(exc),
            details={"path": path},
            retryable=False,
        )
    except MemoryQueryUnavailableError as exc:
        return error_route_result(
            HTTPStatus.SERVICE_UNAVAILABLE,
            code="memory_unavailable",
            message=str(exc),
            details={"path": path},
            retryable=True,
        )
    except MemctlError as exc:
        return exception_route_result(
            HTTPStatus.INTERNAL_SERVER_ERROR,
            code="memory_query_failed",
            exc=exc,
            details={"path": path},
            retryable=False,
        )

    return RouteResult(
        status=HTTPStatus.OK,
        payload={
            "count": len(records),
            "records": serialize_for_json(records),
            "namespace": namespace.as_dict(),
            "scope": scope.strip() if scope else None,
            "legacy_scope_only": body.get("namespace") is None,
        },
    )


def _handle_get_turn_trace(
    *,
    path: str,
    query: str | None,
) -> RouteResult:
    session_id = _single_query_value(query, "session_id")
    turn_id = _single_query_value(query, "turn_id")
    if not session_id:
        return error_route_result(
            HTTPStatus.BAD_REQUEST,
            code="invalid_request",
            message="`session_id` query parameter is required.",
            details={"path": path},
            retryable=False,
        )
    if not turn_id:
        return error_route_result(
            HTTPStatus.BAD_REQUEST,
            code="invalid_request",
            message="`turn_id` query parameter is required.",
            details={"path": path},
            retryable=False,
            session_id=session_id,
        )

    trace = default_provenance_recorder().get_turn_trace(
        session_id=session_id,
        turn_id=turn_id,
    )
    if trace is None:
        return error_route_result(
            HTTPStatus.NOT_FOUND,
            code="not_found",
            message=f"no provenance trace recorded for session={session_id} turn={turn_id}",
            details={"path": path},
            retryable=False,
            session_id=session_id,
        )

    return RouteResult(
        status=HTTPStatus.OK,
        payload=trace.to_dict(),
        session_id=session_id,
    )


def _handle_get_by_memory(
    *,
    path: str,
    query: str | None,
) -> RouteResult:
    memory_id = _single_query_value(query, "memory_id")
    if not memory_id:
        return error_route_result(
            HTTPStatus.BAD_REQUEST,
            code="invalid_request",
            message="`memory_id` query parameter is required.",
            details={"path": path},
            retryable=False,
        )

    traces = default_provenance_recorder().find_traces_citing_memory(memory_id)
    return RouteResult(
        status=HTTPStatus.OK,
        payload={
            "memory_id": memory_id,
            "trace_count": len(traces),
            "traces": [t.to_dict() for t in traces],
        },
    )
